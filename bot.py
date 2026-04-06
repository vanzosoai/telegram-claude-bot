import os
import sys
import json
import logging
import subprocess
import time
import signal
import atexit
from datetime import datetime, time as dt_time
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic

# === SINGLE INSTANCE LOCK ===
PID_FILE = "/tmp/claudebot.pid"

def check_single_instance():
    """Ensure only one bot instance runs. Kill any existing instance first."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Check if that process is actually running
            os.kill(old_pid, 0)
            # It's running — kill it so we can take over
            print(f"⚠️ Killing existing bot instance (PID {old_pid})...")
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(2)
            try:
                os.kill(old_pid, signal.SIGKILL)  # Force kill if still alive
            except OSError:
                pass
            time.sleep(1)
        except (OSError, ValueError):
            # Process not running, stale PID file
            pass

    # Write our PID
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def cleanup_pid():
    """Remove PID file on exit"""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, 'r') as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(PID_FILE)
    except:
        pass

atexit.register(cleanup_pid)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Activity logger - separate file for audit trail
activity_logger = logging.getLogger('activity')
activity_handler = logging.FileHandler('/tmp/claudebot_activity.log')
activity_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
activity_logger.addHandler(activity_handler)
activity_logger.setLevel(logging.INFO)

def log_activity(event_type, user_id=None, detail=""):
    """Log all bot activity to /tmp/claudebot_activity.log"""
    entry = {"event": event_type, "user_id": user_id, "detail": detail[:500]}
    activity_logger.info(json.dumps(entry))

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Whitelist: comma-separated Telegram user IDs in env var, e.g. "123456789,987654321"
# If not set, bot will reject all users and log their IDs so you can add them
ALLOWED_USER_IDS_RAW = os.environ.get("ALLOWED_TELEGRAM_IDS", "8687978775")
ALLOWED_USER_IDS = set()
if ALLOWED_USER_IDS_RAW.strip():
    for uid in ALLOWED_USER_IDS_RAW.split(","):
        uid = uid.strip()
        if uid.isdigit():
            ALLOWED_USER_IDS.add(int(uid))

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

conversation_history = {}

# Token usage tracking
BOT_START_TIME = time.time()
token_usage = {
    "total_input": 0,
    "total_output": 0,
    "session_messages": 0,
    "by_model": {}
}

# Approximate costs per million tokens (April 2026)
TOKEN_COSTS = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
}

def track_tokens(response, model):
    """Track token usage from an API response"""
    if hasattr(response, 'usage'):
        inp = response.usage.input_tokens
        out = response.usage.output_tokens
        token_usage["total_input"] += inp
        token_usage["total_output"] += out
        token_usage["session_messages"] += 1
        if model not in token_usage["by_model"]:
            token_usage["by_model"][model] = {"input": 0, "output": 0, "calls": 0}
        token_usage["by_model"][model]["input"] += inp
        token_usage["by_model"][model]["output"] += out
        token_usage["by_model"][model]["calls"] += 1

def estimate_cost():
    """Estimate session cost in USD"""
    total = 0.0
    for model, usage in token_usage["by_model"].items():
        costs = TOKEN_COSTS.get(model, {"input": 3.00, "output": 15.00})
        total += (usage["input"] / 1_000_000) * costs["input"]
        total += (usage["output"] / 1_000_000) * costs["output"]
    return total

# Rate limiting: track API calls per user
rate_limit_tracker = {}  # user_id -> list of timestamps
RATE_LIMIT_MAX_CALLS = 30       # max API calls per window
RATE_LIMIT_WINDOW_SECONDS = 300  # 5 minute window
RATE_LIMIT_COOLDOWN = 60         # cooldown after hitting limit

def check_rate_limit(user_id):
    """Returns (allowed, message). Prevents runaway API usage."""
    now = time.time()
    if user_id not in rate_limit_tracker:
        rate_limit_tracker[user_id] = []

    # Clean old entries
    rate_limit_tracker[user_id] = [
        t for t in rate_limit_tracker[user_id]
        if now - t < RATE_LIMIT_WINDOW_SECONDS
    ]

    if len(rate_limit_tracker[user_id]) >= RATE_LIMIT_MAX_CALLS:
        oldest = rate_limit_tracker[user_id][0]
        wait = int(RATE_LIMIT_WINDOW_SECONDS - (now - oldest))
        return False, f"⚠️ Rate limit hit ({RATE_LIMIT_MAX_CALLS} calls in {RATE_LIMIT_WINDOW_SECONDS//60}min). Try again in {wait}s."

    rate_limit_tracker[user_id].append(now)
    return True, "ok"

# Persistent memory: save/load conversation history to disk
MEMORY_FILE = os.path.expanduser("~/Documents/Claude Projects/telegram-claude-bot/.bot_memory.json")

def save_memory():
    """Save conversation history and token usage to disk for persistence across restarts"""
    try:
        # Convert to serializable format, keeping last 20 messages per user
        convos = {}
        for uid, messages in conversation_history.items():
            serializable = []
            for msg in messages[-20:]:
                if isinstance(msg.get("content"), str):
                    serializable.append(msg)
                elif isinstance(msg.get("content"), list):
                    # Tool results - skip for persistence (they contain ephemeral data)
                    continue
                else:
                    # Assistant content blocks - convert to text
                    try:
                        text = ""
                        for block in msg.get("content", []):
                            if hasattr(block, "text"):
                                text += block.text
                        if text:
                            serializable.append({"role": msg["role"], "content": text})
                    except:
                        continue
            convos[str(uid)] = serializable

        data = {
            "conversations": convos,
            "token_usage": token_usage,
        }
        with open(MEMORY_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Failed to save memory: {e}")

def load_memory():
    """Load conversation history and token usage from disk"""
    global conversation_history, token_usage
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r') as f:
                data = json.load(f)
            # Handle both old format (flat dict) and new format (nested)
            if "conversations" in data:
                conversation_history = {int(k): v for k, v in data["conversations"].items()}
                saved_usage = data.get("token_usage", {})
                if saved_usage:
                    token_usage["total_input"] = saved_usage.get("total_input", 0)
                    token_usage["total_output"] = saved_usage.get("total_output", 0)
                    token_usage["session_messages"] = saved_usage.get("session_messages", 0)
                    token_usage["by_model"] = saved_usage.get("by_model", {})
            else:
                # Old format: flat dict of conversations
                conversation_history = {int(k): v for k, v in data.items()}
            logging.info(f"Loaded memory for {len(conversation_history)} users, {token_usage['session_messages']} historical messages tracked")
    except Exception as e:
        logging.error(f"Failed to load memory: {e}")
        conversation_history = {}

# Change this to your secret kill code
KILL_CODE = "killclaudenow"

# Shared projects folder — both Cowork and Claude Bot use this
PROJECTS_DIR = os.path.expanduser("~/Documents/Claude Projects")

# Only allow operations in these folders
ALLOWED_PATHS = [
    PROJECTS_DIR,
    os.path.expanduser("~/Projects"),  # legacy path
    os.path.expanduser("~/Documents/Claude Projects/telegram-claude-bot"),
    os.path.expanduser("~/Desktop"),
    "/tmp",
]

# Commands that are never allowed regardless of path
BLOCKED_COMMANDS = [
    "rm -rf /",
    "rm -rf ~",
    "sudo rm",
    "chmod 777",
    "chown",
    "mkfs",
    "dd if=",
    "> /etc",
    "curl | sh",
    "wget | sh",
    "launchctl",
    "systemctl",
]

tools = [
    {
        "name": "run_command",
        "description": "Run a shell command on the user's Mac and return the output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file on the user's Mac.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The full path to the file to read"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "Write content to a file on the user's Mac. Creates the file if it doesn't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The full path to the file to write"},
                "content": {"type": "string", "description": "The content to write to the file"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_projects",
        "description": "List all projects in ~/Projects with their git status and last modified time.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "screenshot",
        "description": "Take a screenshot of the Mac. User has 2 displays. By default captures both. Returns the image(s) to Telegram.",
        "input_schema": {
            "type": "object",
            "properties": {
                "display": {"type": "string", "description": "Which display: 'all' (default, both displays), '1' (main), or '2' (secondary)", "default": "all"}
            },
            "required": []
        }
    },
    {
        "name": "run_background",
        "description": "Run a long-running command in the background (builds, deploys, tests). The user will be notified via Telegram when it finishes. Use this instead of run_command for anything that might take more than 30 seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run in the background"},
                "label": {"type": "string", "description": "Short description like 'npm build' or 'deploy to prod'"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "send_file",
        "description": "Send a file from the Mac to the user via Telegram. Use for any file the user requests (code, images, documents, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the file to send"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "system_health",
        "description": "Get Mac system health: CPU usage, memory, disk space, top processes, and battery status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "serve_project",
        "description": "Serve a project folder via local HTTP server and ngrok tunnel. Returns a public URL the user can open on their phone. Use this INSTEAD of manually starting servers and ngrok.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path to the folder to serve (e.g. ~/Documents/Claude Projects/todo-app)"},
                "port": {"type": "integer", "description": "Port to serve on (default 8080)"}
            },
            "required": ["path"]
        }
    }
]

def is_safe_command(command):
    """Check if a command is safe to run"""
    command_lower = command.lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in command_lower:
            return False, f"Blocked dangerous command: {blocked}"
    return True, "ok"

def is_safe_path(path):
    """Check if a path is within allowed directories"""
    real_path = os.path.realpath(os.path.expanduser(path))
    for allowed in ALLOWED_PATHS:
        real_allowed = os.path.realpath(os.path.expanduser(allowed))
        if real_path.startswith(real_allowed):
            return True
    return False

def execute_tool(tool_name, tool_input, chat_id=None):
    log_activity("tool_call", detail=f"{tool_name}: {json.dumps(tool_input)[:300]}")
    try:
        if tool_name == "run_command":
            command = tool_input["command"]
            safe, reason = is_safe_command(command)
            if not safe:
                log_activity("blocked_command", detail=command)
                return f"🚫 Command blocked for safety: {reason}"
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60
            )
            output = result.stdout or result.stderr
            result_text = output if output else "Command executed successfully with no output"
            # Truncate very long outputs to avoid blowing context
            if len(result_text) > 10000:
                result_text = result_text[:5000] + "\n\n... [truncated] ...\n\n" + result_text[-2000:]
            return result_text

        elif tool_name == "read_file":
            path = tool_input["path"]
            if not is_safe_path(path):
                return f"🚫 Access denied. Can only read from: {', '.join(ALLOWED_PATHS)}"
            with open(path, 'r') as f:
                content = f.read()
            # Truncate very long files
            if len(content) > 15000:
                content = content[:7000] + "\n\n... [truncated - file too long] ...\n\n" + content[-3000:]
            return content

        elif tool_name == "write_file":
            path = tool_input["path"]
            if not is_safe_path(path):
                return f"🚫 Access denied. Can only write to: {', '.join(ALLOWED_PATHS)}"
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, 'w') as f:
                f.write(tool_input["content"])
            return f"File written successfully to {path}"

        elif tool_name == "list_projects":
            # Check both new and legacy project folders
            dirs_to_check = [PROJECTS_DIR, os.path.expanduser("~/Projects")]
            entries = []
            for projects_dir in dirs_to_check:
                if not os.path.exists(projects_dir):
                    continue
                label = "" if projects_dir == PROJECTS_DIR else " (legacy)"
                for name in sorted(os.listdir(projects_dir)):
                    full_path = os.path.join(projects_dir, name)
                    if os.path.isdir(full_path) and not name.startswith('.'):
                        mtime = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%Y-%m-%d %H:%M")
                        git_status = ""
                        if os.path.isdir(os.path.join(full_path, ".git")):
                            try:
                                branch = subprocess.run(
                                    ["git", "-C", full_path, "branch", "--show-current"],
                                    capture_output=True, text=True, timeout=5
                                ).stdout.strip()
                                dirty = subprocess.run(
                                    ["git", "-C", full_path, "status", "--porcelain"],
                                    capture_output=True, text=True, timeout=5
                                ).stdout.strip()
                                git_status = f" [git: {branch}{'*' if dirty else ''}]"
                            except:
                                git_status = " [git]"
                        # Read handoff status if it exists
                        handoff_status = ""
                        handoff_path = os.path.join(full_path, "HANDOFF.md")
                        if os.path.exists(handoff_path):
                            try:
                                with open(handoff_path) as hf:
                                    for line in hf:
                                        if line.startswith("## Status:"):
                                            handoff_status = f" → {line.strip().replace('## Status:', '').strip()}"
                                            break
                            except:
                                pass
                        entries.append(f"📁 {name}{label} — {mtime}{git_status}{handoff_status}")
            return "\n".join(entries) if entries else "No projects found"

        elif tool_name == "screenshot":
            display = tool_input.get("display", "all")
            screenshot_raw_1 = "/tmp/bot_screenshot_1.png"
            screenshot_raw_2 = "/tmp/bot_screenshot_2.png"
            screenshot_path = "/tmp/bot_screenshot.jpg"

            if display == "all" or display == "both":
                # Capture both displays as separate files
                result = subprocess.run(
                    ["screencapture", "-x", screenshot_raw_1, screenshot_raw_2],
                    capture_output=True, text=True, timeout=10
                )
                # Use whichever has the most content (largest file = main display)
                screenshots = []
                for f in [screenshot_raw_1, screenshot_raw_2]:
                    if os.path.exists(f):
                        screenshots.append(f)
                if not screenshots:
                    return f"Failed to take screenshot: {result.stderr}"
                # Send the largest one (most likely the active display)
                screenshots.sort(key=lambda f: os.path.getsize(f), reverse=True)
                screenshot_raw = screenshots[0]
            else:
                # Single display capture
                screenshot_raw = screenshot_raw_1
                result = subprocess.run(
                    ["screencapture", "-x", screenshot_raw],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0 or not os.path.exists(screenshot_raw):
                    return f"Failed to take screenshot: {result.stderr}"

            # Resize to max 1920px wide and compress as JPEG for Telegram
            subprocess.run(
                ["sips", "--resampleWidth", "1920", "--setProperty", "format", "jpeg",
                 "--setProperty", "formatOptions", "60", screenshot_raw, "--out", screenshot_path],
                capture_output=True, text=True, timeout=10
            )
            if os.path.exists(screenshot_path):
                # If we got both displays, send both
                if display in ["all", "both"] and len(screenshots) > 1:
                    second_jpg = "/tmp/bot_screenshot_2.jpg"
                    subprocess.run(
                        ["sips", "--resampleWidth", "1920", "--setProperty", "format", "jpeg",
                         "--setProperty", "formatOptions", "60", screenshots[1], "--out", second_jpg],
                        capture_output=True, text=True, timeout=10
                    )
                    if os.path.exists(second_jpg):
                        return f"SCREENSHOT_SAVED:{screenshot_path}|{second_jpg}"
                return f"SCREENSHOT_SAVED:{screenshot_path}"
            return f"SCREENSHOT_SAVED:{screenshot_raw}"

        elif tool_name == "run_background":
            command = tool_input["command"]
            label = tool_input.get("label", command[:50])
            safe, reason = is_safe_command(command)
            if not safe:
                return f"🚫 Command blocked for safety: {reason}"
            # Launch in background, capture output to a log file
            log_file = f"/tmp/bg_{int(time.time())}.log"
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=open(log_file, 'w'),
                stderr=subprocess.STDOUT
            )
            # Register with build watcher for notifications
            if chat_id:
                build_watchers[proc.pid] = {
                    "command": label,
                    "chat_id": chat_id,
                    "start": time.time(),
                    "log_file": log_file
                }
            log_activity("background_started", detail=f"PID {proc.pid}: {label}")
            return f"⏳ Running in background (PID {proc.pid}): {label}\nYou'll get a notification when it finishes.\nLog: {log_file}"

        elif tool_name == "send_file":
            path = tool_input["path"]
            if not is_safe_path(path):
                return f"🚫 Access denied. Can only send from: {', '.join(ALLOWED_PATHS)}"
            if not os.path.exists(path):
                return f"File not found: {path}"
            file_size = os.path.getsize(path)
            if file_size > 50 * 1024 * 1024:  # 50MB Telegram limit
                return f"File too large ({file_size // (1024*1024)}MB). Telegram limit is 50MB."
            return f"SEND_FILE:{path}"

        elif tool_name == "system_health":
            parts = []
            # CPU and load
            load = subprocess.run(["sysctl", "-n", "vm.loadavg"],
                                capture_output=True, text=True, timeout=5).stdout.strip()
            parts.append(f"📊 Load: {load}")
            # Memory
            mem = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            # Parse vm_stat for useful info
            page_size = 16384  # default macOS
            free = 0
            active = 0
            for line in mem.split('\n'):
                if 'free' in line.lower():
                    try: free = int(line.split(':')[1].strip().rstrip('.')) * page_size
                    except: pass
                if 'active' in line.lower() and 'inactive' not in line.lower():
                    try: active = int(line.split(':')[1].strip().rstrip('.')) * page_size
                    except: pass
            parts.append(f"🧠 Memory: {active//(1024**3)}GB active, {free//(1024**3)}GB free")
            # Disk
            disk = subprocess.run(["df", "-h", "/"],
                                capture_output=True, text=True, timeout=5).stdout
            disk_lines = disk.strip().split('\n')
            if len(disk_lines) > 1:
                parts.append(f"💾 Disk: {disk_lines[1].split()[3]} free of {disk_lines[1].split()[1]}")
            # Battery
            batt = subprocess.run(["pmset", "-g", "batt"],
                                capture_output=True, text=True, timeout=5).stdout
            if "%" in batt:
                parts.append(f"🔋 {batt.split(chr(10))[1].strip()[:60]}")
            # Top 5 processes by CPU
            top = subprocess.run(
                ["ps", "aux", "--sort=-%cpu"],
                capture_output=True, text=True, timeout=5
            )
            if top.returncode != 0:
                # macOS ps doesn't support --sort, use different approach
                top = subprocess.run(
                    "ps aux | sort -nrk 3 | head -6",
                    shell=True, capture_output=True, text=True, timeout=5
                )
            top_lines = top.stdout.strip().split('\n')[1:6]  # skip header
            if top_lines:
                parts.append("🔥 Top processes:")
                for line in top_lines:
                    cols = line.split()
                    if len(cols) >= 11:
                        parts.append(f"  {cols[10][:25]} — CPU: {cols[2]}% MEM: {cols[3]}%")
            # Uptime
            uptime = subprocess.run(["uptime"], capture_output=True, text=True, timeout=5).stdout.strip()
            parts.append(f"⏱️ {uptime}")
            return "\n".join(parts)

        elif tool_name == "serve_project":
            path = tool_input["path"]
            port = tool_input.get("port", 8080)
            if not is_safe_path(path):
                return f"🚫 Access denied. Can only serve from: {', '.join(ALLOWED_PATHS)}"
            if not os.path.isdir(path):
                return f"🚫 Not a directory: {path}"

            import json as _json

            # Step 1: Clean slate — kill old server AND old ngrok
            subprocess.run(f"lsof -ti:{port} | xargs kill -9 2>/dev/null", shell=True, timeout=5)
            subprocess.run("pkill -9 -f ngrok 2>/dev/null", shell=True, timeout=5)
            time.sleep(1)

            # Step 2: Start HTTP server and verify it's listening
            subprocess.Popen(
                f"cd '{path}' && python3 -m http.server {port}",
                shell=True,
                stdout=open("/tmp/http_server.log", "w"),
                stderr=subprocess.STDOUT
            )
            # Verify server is up (retry up to 5 times)
            server_up = False
            for _ in range(5):
                time.sleep(0.5)
                check = subprocess.run(
                    f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/",
                    shell=True, capture_output=True, text=True, timeout=3
                )
                if check.stdout.strip().startswith(("200", "301", "404")):
                    server_up = True
                    break
            if not server_up:
                return f"⚠️ HTTP server failed to start on port {port}. Check /tmp/http_server.log"

            # Step 3: Start fresh ngrok tunnel
            subprocess.Popen(
                f"ngrok http {port} --log=stdout > /tmp/ngrok.log 2>&1",
                shell=True
            )

            # Step 4: Wait for ngrok to register tunnel (retry with backoff)
            url = None
            for attempt in range(8):
                time.sleep(1.5 if attempt < 3 else 2.5)
                try:
                    result = subprocess.run(
                        "curl -s http://127.0.0.1:4040/api/tunnels",
                        shell=True, capture_output=True, text=True, timeout=5
                    )
                    if result.returncode != 0 or not result.stdout.strip():
                        continue
                    data = _json.loads(result.stdout)
                    tunnels = data.get("tunnels", [])
                    if tunnels:
                        # Prefer https
                        url = tunnels[0]["public_url"]
                        for t in tunnels:
                            if t["public_url"].startswith("https"):
                                url = t["public_url"]
                                break
                        break
                except (_json.JSONDecodeError, KeyError, Exception):
                    continue

            if url:
                return f"🌐 App is live!\n{url}\n\nServing: {path}\nPort: {port}"
            else:
                # Check ngrok log for errors
                ngrok_err = ""
                try:
                    with open("/tmp/ngrok.log", "r") as f:
                        log_content = f.read()[-500:]
                    if "ERR" in log_content:
                        ngrok_err = f"\nNgrok log: {log_content[-200:]}"
                except:
                    pass
                return f"⚠️ Server running on localhost:{port} but ngrok tunnel failed to initialize.{ngrok_err}\nTry: ngrok config check"

    except subprocess.TimeoutExpired:
        return "⚠️ Command timed out (60s limit)"
    except Exception as e:
        log_activity("tool_error", detail=f"{tool_name}: {str(e)}")
        return f"Error executing {tool_name}: {str(e)}"

async def send_long_message(update, text):
    MAX_LENGTH = 4000
    if len(text) <= MAX_LENGTH:
        await update.message.reply_text(text)
    else:
        chunks = [text[i:i+MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
        for chunk in chunks:
            await update.message.reply_text(chunk)

def classify_complexity(message):
    """Determine if a message needs Sonnet (complex) or Haiku (simple).
    Returns model string."""
    complex_signals = [
        "build", "create", "implement", "refactor", "debug", "fix bug",
        "write a", "set up", "deploy", "migrate", "architect", "design",
        "full stack", "database", "api", "test suite", "dockerfile",
        "configure", "install", "scaffold", "restructure", "optimize",
        "project", "app", "application", "service", "server",
        "multiple files", "entire", "whole", "complete",
    ]
    msg_lower = message.lower()
    # Long messages or messages with complex signals -> Sonnet
    if len(message) > 200:
        return "claude-sonnet-4-6"
    for signal in complex_signals:
        if signal in msg_lower:
            return "claude-sonnet-4-6"
    return "claude-haiku-4-5-20251001"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text_override=None):
    user_id = update.effective_user.id
    user_name = update.effective_user.username or "unknown"
    user_message = text_override or update.message.text

    log_activity("message_received", user_id, f"@{user_name}: {user_message[:200]}")

    # Whitelist check
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        logging.warning(f"UNAUTHORIZED access attempt from user_id={user_id} username=@{user_name}")
        log_activity("unauthorized", user_id, f"@{user_name} rejected")
        await update.message.reply_text(
            f"🚫 Unauthorized. Your Telegram user ID is: {user_id}\n"
            f"Add this to ALLOWED_TELEGRAM_IDS env var to authorize."
        )
        return

    if not ALLOWED_USER_IDS:
        # No whitelist configured - log the ID prominently so owner can set it up
        logging.warning(f"⚠️ NO WHITELIST SET. User ID: {user_id} Username: @{user_name}")
        logging.warning(f"⚠️ Set ALLOWED_TELEGRAM_IDS={user_id} in your environment to secure the bot!")

    # Natural language command shortcuts (works with voice too)
    msg_lower = user_message.strip().lower()
    shortcut_map = {
        cmd_help: ["what commands", "what can you do", "what can i do", "help me", "show commands", "list commands", "show me commands"],
        cmd_status: ["bot status", "are you alive", "are you running", "you alive", "you up", "status check", "how are you"],
        cmd_cost: ["how much", "token usage", "what's the cost", "whats the cost", "how expensive", "cost so far", "what am i spending"],
        cmd_clear: ["clear history", "fresh start", "reset conversation", "forget everything", "start over", "clear context", "wipe history"],
        cmd_projects: ["list projects", "show projects", "my projects", "what projects"],
        cmd_screenshot: ["take a screenshot", "show my screen", "what's on my screen", "whats on my screen", "screen capture", "screenshot please", "grab my screen", "show me my screen"],
        cmd_health: ["system health", "how's my mac", "hows my mac", "mac health", "check my system", "system status"],
    }
    for handler, phrases in shortcut_map.items():
        if any(phrase in msg_lower for phrase in phrases):
            await handler(update, context)
            return

    # Emergency kill code - nuclear shutdown
    if msg_lower == KILL_CODE.lower():
        log_activity("kill_code", user_id, "Emergency shutdown triggered")
        await update.message.reply_text("🛑 Emergency stop activated. Shutting everything down.")
        subprocess.run("pkill -9 -f ngrok", shell=True)
        subprocess.run("lsof -ti:8080 | xargs kill -9", shell=True)
        subprocess.run("pkill -9 -f menubar.py", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.johnjurkoii.claudebot.plist 2>/dev/null", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.johnjurkoii.claudemenubar.plist 2>/dev/null", shell=True)
        os._exit(0)

    # Rate limiting
    allowed, msg = check_rate_limit(user_id)
    if not allowed:
        log_activity("rate_limited", user_id, msg)
        await update.message.reply_text(msg)
        return

    if user_id not in conversation_history:
        conversation_history[user_id] = []

    conversation_history[user_id].append({
        "role": "user",
        "content": user_message
    })

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    # Smart model routing
    model = classify_complexity(user_message)
    model_label = "⚡" if "haiku" in model else "🧠"
    logging.info(f"Model selected: {model} for message from {user_id}")
    log_activity("model_selected", user_id, f"{model} for: {user_message[:100]}")

    messages = conversation_history[user_id].copy()
    MAX_ITERATIONS = 6  # Bumped from 4 for complex tasks
    iteration = 0

    while iteration < MAX_ITERATIONS:
        iteration += 1

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=f"""You are a coding agent on the user's Mac. Be concise and efficient - we're on mobile.
You have tools to run shell commands, read and write files, list projects, and take screenshots.
Home directory: /Users/johnjurkoii
Projects: {PROJECTS_DIR}

You can ONLY work within these folders:
- {PROJECTS_DIR} (primary — all new projects go here)
- /Users/johnjurkoii/Projects (legacy projects)
- ~/Documents/Claude Projects/telegram-claude-bot (bot files)
- /Users/johnjurkoii/Desktop
- /tmp

Never touch system files, never use sudo, never modify anything outside these folders.

HANDOFF DOCS — CRITICAL WORKFLOW:
Every project in {PROJECTS_DIR} should have a HANDOFF.md in its root.
- BEFORE starting work: check the "Last touched" line in HANDOFF.md. If it says "by Claude Bot (Telegram)" then YOU were the last to edit — skip the full re-read and just resume working. If it says "by Cowork (Desktop)" then the OTHER agent made changes — do a full read to absorb new context.
- If no HANDOFF.md exists, create one before doing any other work.
- AFTER completing work: update HANDOFF.md with what you did and what's next
- ALL timestamps must be UTC 24-hour format with seconds: YYYY-MM-DD HH:MM:SS UTC
- Use Python: datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
- HANDOFF.md format:
  # Project: <name>
  ## Summary
  <2-3 sentences: what this app does, who it's for, how it works>
  ## Status: <In Progress | Complete | Blocked | Paused>
  ## Last touched: <YYYY-MM-DD HH:MM:SS UTC> by Claude Bot (Telegram)
  ### Features
  <Running list — append only, never remove>
  - <feature> — <one-line description>
  ### Known Bugs & Issues
  <Running list — mark [FIXED] when resolved, never delete entries>
  - [FIXED] <bug> — <what fixed it>
  - [OPEN] <bug> — <context>
  ### Work Log
  <Append-only, newest first. Each entry gets UTC timestamp + agent name.>
  - YYYY-MM-DD HH:MM:SS UTC (Claude Bot) — <what was done>
  ### Next Steps
  - <what should be done next>
  ### Key Files
  - <filename> — <what it does>
  ### Tech Stack
  - <languages, frameworks, dependencies>
  ### Gotchas
  - <anything the next agent needs to know>

This handoff system lets the user switch between Cowork (desktop) and this bot (mobile) seamlessly. Both agents read and write HANDOFF.md. Always keep it current. Features and Bugs sections are the project's institutional memory — never remove entries.

To show an app to the user via a public URL:
Use the serve_project tool with the project folder path. It handles everything (kills old servers, starts new one, starts ngrok if needed, returns the public URL) in one step. DO NOT manually start servers or ngrok — always use serve_project.

Stop server: lsof -ti:8080 | xargs kill -9 2>/dev/null

IMPORTANT: Complete tasks in as few steps as possible. Do not repeat failed commands.
All new projects should be created in {PROJECTS_DIR}.
You are running as: {model_label} {"Haiku (fast)" if "haiku" in model else "Sonnet (powerful)"}""",
            messages=messages,
            tools=tools
        )

        track_tokens(response, model)

        if response.stop_reason == "tool_use":
            tool_results = []
            status_message = ""

            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    label = tool_input.get('command', tool_name)
                    status_message += f"⚙️ `{label}`\n"
                    result = execute_tool(tool_name, tool_input, chat_id=update.effective_chat.id)

                    # Handle screenshot - send as photo(s) in Telegram
                    if result and result.startswith("SCREENSHOT_SAVED:"):
                        paths = result.split(":", 1)[1].split("|")
                        try:
                            for i, spath in enumerate(paths):
                                if os.path.exists(spath):
                                    caption = f"📸 Display {i+1}" if len(paths) > 1 else "📸 Screenshot"
                                    with open(spath, 'rb') as photo:
                                        await context.bot.send_photo(
                                            chat_id=update.effective_chat.id,
                                            photo=photo,
                                            caption=caption
                                        )
                            result = f"Screenshot(s) sent ({len(paths)} display{'s' if len(paths)>1 else ''})."
                        except Exception as e:
                            result = f"Screenshot taken but failed to send: {e}"

                    # Handle file send - send file to user in Telegram
                    elif result and result.startswith("SEND_FILE:"):
                        file_path = result.split(":", 1)[1]
                        try:
                            with open(file_path, 'rb') as f:
                                await context.bot.send_document(
                                    chat_id=update.effective_chat.id,
                                    document=f,
                                    filename=os.path.basename(file_path),
                                    caption=f"📤 {os.path.basename(file_path)}"
                                )
                            result = f"File sent: {os.path.basename(file_path)}"
                        except Exception as e:
                            result = f"File found but failed to send: {e}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            if status_message:
                await send_long_message(update, status_message)

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        else:
            final_response = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_response += block.text

            conversation_history[user_id] = messages
            conversation_history[user_id].append({
                "role": "assistant",
                "content": final_response
            })

            log_activity("response_sent", user_id, final_response[:200])
            save_memory()
            await send_long_message(update, final_response)
            return

    # Hit the loop limit - report back
    conversation_history[user_id] = messages
    save_memory()
    await send_long_message(update,
        f"⚠️ I hit my {MAX_ITERATIONS} step limit. Here's where I got - can you give me more guidance or break it into smaller steps?")

# === DAILY STANDUP ===
STANDUP_HOUR = int(os.environ.get("STANDUP_HOUR", "9"))  # 9 AM default, configurable
STANDUP_CHAT_ID = os.environ.get("STANDUP_CHAT_ID", "")   # Set to your chat ID

async def daily_standup(context):
    """Send a morning standup with project summaries"""
    if not STANDUP_CHAT_ID:
        return

    try:
        summary_parts = ["☀️ *Morning Standup*\n"]
        dirs_to_scan = [PROJECTS_DIR, os.path.expanduser("~/Projects")]

        for projects_dir in dirs_to_scan:
          if not os.path.exists(projects_dir):
              continue
          for name in sorted(os.listdir(projects_dir)):
                full_path = os.path.join(projects_dir, name)
                if os.path.isdir(full_path) and os.path.isdir(os.path.join(full_path, ".git")):
                    try:
                        # Get recent commits (last 24h)
                        recent = subprocess.run(
                            ["git", "-C", full_path, "log", "--oneline", "--since=24 hours ago"],
                            capture_output=True, text=True, timeout=5
                        ).stdout.strip()
                        # Get dirty status
                        dirty = subprocess.run(
                            ["git", "-C", full_path, "status", "--porcelain"],
                            capture_output=True, text=True, timeout=5
                        ).stdout.strip()
                        branch = subprocess.run(
                            ["git", "-C", full_path, "branch", "--show-current"],
                            capture_output=True, text=True, timeout=5
                        ).stdout.strip()

                        if recent or dirty:
                            summary_parts.append(f"📁 *{name}* ({branch})")
                            if recent:
                                commits = recent.split('\n')
                                summary_parts.append(f"  {len(commits)} commit(s) yesterday:")
                                for c in commits[:3]:
                                    summary_parts.append(f"  • {c}")
                            if dirty:
                                changed = len(dirty.split('\n'))
                                summary_parts.append(f"  ⚠️ {changed} uncommitted change(s)")
                            summary_parts.append("")
                    except:
                        continue

        if len(summary_parts) == 1:
            summary_parts.append("No project activity in the last 24h. Fresh start! 🚀")

        await context.bot.send_message(
            chat_id=int(STANDUP_CHAT_ID),
            text="\n".join(summary_parts)
        )
        log_activity("standup_sent", detail=f"To chat {STANDUP_CHAT_ID}")
    except Exception as e:
        logging.error(f"Standup failed: {e}")

# === PROACTIVE NOTIFICATIONS ===
# Track build processes and notify on completion/failure
build_watchers = {}  # pid -> {"command": str, "chat_id": int, "start": float}

async def check_builds(context):
    """Check if any watched build processes have finished"""
    if not build_watchers:
        return
    logging.debug(f"Checking {len(build_watchers)} background tasks")
    finished = []
    for pid, info in list(build_watchers.items()):
        try:
            os.kill(pid, 0)  # Check if still running
        except (OSError, ProcessLookupError):
            # Process finished
            elapsed = int(time.time() - info["start"])
            # Read last few lines of output
            tail = ""
            log_file = info.get("log_file")
            if log_file and os.path.exists(log_file):
                try:
                    with open(log_file, 'r') as f:
                        lines = f.readlines()
                        tail = "".join(lines[-5:]).strip()
                        if tail:
                            tail = f"\n```\n{tail[:500]}\n```"
                except Exception as e:
                    logging.error(f"Failed to read log {log_file}: {e}")
            msg = f"🔔 Done ({elapsed}s): {info['command'][:100]}{tail}"
            try:
                logging.info(f"Sending build notification to chat {info['chat_id']}: {msg[:100]}")
                await context.bot.send_message(
                    chat_id=info["chat_id"],
                    text=msg
                )
            except Exception as e:
                logging.error(f"Failed to send build notification: {e}")
            finished.append(pid)
            log_activity("build_finished", detail=info["command"])

    for pid in finished:
        del build_watchers[pid]


# === VOICE MESSAGE HANDLER ===
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transcribe voice messages and process as text commands"""
    user_id = update.effective_user.id
    user_name = update.effective_user.username or "unknown"

    log_activity("voice_received", user_id, f"@{user_name}")

    # Whitelist check
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text(f"🚫 Unauthorized. Your ID: {user_id}")
        return

    await update.message.reply_text("🎤 Transcribing...")

    try:
        # Download the voice file
        voice = update.message.voice or update.message.audio
        voice_file = await context.bot.get_file(voice.file_id)
        ogg_path = f"/tmp/voice_{user_id}_{int(time.time())}.ogg"
        wav_path = ogg_path.replace('.ogg', '.wav')
        await voice_file.download_to_drive(ogg_path)

        # Convert to wav using ffmpeg (macOS has it via homebrew or we use afconvert)
        convert_result = subprocess.run(
            ["ffmpeg", "-i", ogg_path, "-y", wav_path],
            capture_output=True, text=True, timeout=15
        )
        if convert_result.returncode != 0:
            # Try macOS native converter as fallback
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16", ogg_path, wav_path],
                capture_output=True, text=True, timeout=15
            )

        # Transcribe using macOS built-in speech recognition or whisper
        # First try whisper if installed
        transcript = None
        whisper_result = subprocess.run(
            ["which", "whisper"], capture_output=True, text=True
        )
        if whisper_result.returncode == 0:
            result = subprocess.run(
                ["whisper", wav_path, "--model", "tiny", "--output_format", "txt", "--output_dir", "/tmp"],
                capture_output=True, text=True, timeout=60
            )
            txt_path = wav_path.replace('.wav', '.txt')
            if os.path.exists(txt_path):
                with open(txt_path) as f:
                    transcript = f.read().strip()

        # Fallback: use Anthropic to describe what to do based on audio duration
        if not transcript:
            # Use macOS say -v ? to check, or just tell user we need whisper
            # Try python whisper package
            try:
                import whisper as whisper_pkg
                model = whisper_pkg.load_model("tiny")
                result = model.transcribe(wav_path)
                transcript = result["text"].strip()
            except ImportError:
                pass

        if not transcript:
            await update.message.reply_text(
                "⚠️ Couldn't transcribe. Install whisper:\n"
                "`pip3 install openai-whisper`\n"
                "Then restart the bot."
            )
            return

        await update.message.reply_text(f"🎤 Heard: \"{transcript}\"")
        log_activity("voice_transcribed", user_id, transcript)

        # Process as a normal text message using text_override
        await handle_message(update, context, text_override=transcript)

    except Exception as e:
        logging.error(f"Voice handling error: {e}")
        await update.message.reply_text(f"⚠️ Voice error: {str(e)[:200]}")
    finally:
        # Cleanup temp files
        for f in [ogg_path, wav_path]:
            try:
                os.remove(f)
            except:
                pass


# === FILE TRANSFER HANDLER ===
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive files from phone and save to Mac"""
    user_id = update.effective_user.id

    # Whitelist check
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text(f"🚫 Unauthorized. Your ID: {user_id}")
        return

    doc = update.message.document
    if not doc:
        return

    file_name = doc.file_name or f"upload_{int(time.time())}"
    log_activity("file_received", user_id, f"{file_name} ({doc.file_size} bytes)")

    # Save to ~/Desktop/BotUploads by default
    upload_dir = os.path.expanduser("~/Desktop/BotUploads")
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, file_name)

    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(save_path)
        await update.message.reply_text(f"📥 Saved to {save_path}")
        log_activity("file_saved", user_id, save_path)

        # If there's a caption, treat it as an instruction about the file
        if update.message.caption:
            caption_text = f"I just uploaded a file to {save_path}. {update.message.caption}"
            await handle_message(update, context, text_override=caption_text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Upload failed: {str(e)[:200]}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive photos from phone and save to Mac"""
    user_id = update.effective_user.id

    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text(f"🚫 Unauthorized. Your ID: {user_id}")
        return

    photo = update.message.photo[-1]  # Largest size
    file_name = f"photo_{int(time.time())}.jpg"

    upload_dir = os.path.expanduser("~/Desktop/BotUploads")
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, file_name)

    try:
        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(save_path)
        await update.message.reply_text(f"📸 Photo saved to {save_path}")
        log_activity("photo_saved", user_id, save_path)

        if update.message.caption:
            caption_text = f"I just uploaded a photo to {save_path}. {update.message.caption}"
            await handle_message(update, context, text_override=caption_text)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Photo save failed: {str(e)[:200]}")


# === SLASH COMMANDS ===

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available commands"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return
    await update.message.reply_text(
        "🤖 *Claude Bot Commands*\n\n"
        "/help — Show this list\n"
        "/status — Bot health, uptime, recent activity\n"
        "/cost — Token usage and estimated cost this session\n"
        "/clear — Reset conversation history\n"
        "/projects — List all projects with git status\n"
        "/screenshot — Capture your screen\n"
        "/health — Mac system health (CPU, RAM, disk, battery)\n\n"
        "You can also just type naturally — Claude handles the rest.\n"
        "Send voice messages, photos, or files too.\n\n"
        "Emergency kill: send `killclaudenow`",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot health check"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return

    uptime_seconds = int(time.time() - BOT_START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {secs}s"

    # Check recent activity
    recent_activity = "No log found"
    try:
        result = subprocess.run(
            "tail -5 /tmp/claudebot_activity.log",
            shell=True, capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            recent_activity = result.stdout.strip()
    except:
        pass

    # Check PID
    pid = os.getpid()

    # Memory usage
    mem_info = ""
    try:
        result = subprocess.run(
            f"ps -o rss= -p {pid}",
            shell=True, capture_output=True, text=True, timeout=5
        )
        mem_kb = int(result.stdout.strip())
        mem_info = f"Memory: {mem_kb // 1024}MB"
    except:
        mem_info = "Memory: unknown"

    msg_count = token_usage["session_messages"]
    cost = estimate_cost()
    conv_count = len(conversation_history)

    await update.message.reply_text(
        f"🟢 *Bot Status*\n\n"
        f"Uptime: {uptime_str}\n"
        f"PID: {pid}\n"
        f"{mem_info}\n"
        f"Messages this session: {msg_count}\n"
        f"Est. cost this session: ${cost:.4f}\n"
        f"Active conversations: {conv_count}\n\n"
        f"📋 *Recent Activity:*\n`{recent_activity[-500:]}`",
        parse_mode="Markdown"
    )

async def cmd_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show token usage and estimated cost"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return

    total_cost = estimate_cost()
    lines = [f"💰 *Token Usage This Session*\n"]
    lines.append(f"Total input: {token_usage['total_input']:,} tokens")
    lines.append(f"Total output: {token_usage['total_output']:,} tokens")
    lines.append(f"Messages: {token_usage['session_messages']}")
    lines.append(f"Estimated cost: ${total_cost:.4f}\n")

    if token_usage["by_model"]:
        lines.append("*By model:*")
        for model, usage in token_usage["by_model"].items():
            label = "⚡ Haiku" if "haiku" in model else "🧠 Sonnet"
            model_cost = 0.0
            costs = TOKEN_COSTS.get(model, {"input": 3.00, "output": 15.00})
            model_cost += (usage["input"] / 1_000_000) * costs["input"]
            model_cost += (usage["output"] / 1_000_000) * costs["output"]
            lines.append(f"  {label}: {usage['calls']} calls, {usage['input']:,}+{usage['output']:,} tokens (${model_cost:.4f})")

    # Rough projection
    uptime_hours = (time.time() - BOT_START_TIME) / 3600
    if uptime_hours > 0.1 and total_cost > 0:
        daily_rate = (total_cost / uptime_hours) * 24
        lines.append(f"\n📊 At this pace: ~${daily_rate:.2f}/day, ~${daily_rate * 30:.2f}/month")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset conversation history"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return

    if user_id in conversation_history:
        msg_count = len(conversation_history[user_id])
        del conversation_history[user_id]
        save_memory()
        await update.message.reply_text(f"🧹 Cleared {msg_count} messages from your conversation. Fresh start!")
    else:
        await update.message.reply_text("Already clear — no conversation history.")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message for new users"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "there"

    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text(
            f"🚫 Unauthorized. Your Telegram ID: {user_id}\n"
            f"Add this to ALLOWED_TELEGRAM_IDS to authorize."
        )
        return

    await update.message.reply_text(
        f"Hey {user_name}! 🤖\n\n"
        f"I'm your remote coding agent. I can control your Mac, "
        f"write code, run commands, serve apps, take screenshots, and more — "
        f"all from right here in Telegram.\n\n"
        f"Just type what you need in plain English, or send a voice message.\n\n"
        f"Type /help to see all commands."
    )

async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick shortcut to list projects"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return
    result = execute_tool("list_projects", {})
    await send_long_message(update, f"📁 *Projects:*\n\n{result}")

async def cmd_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick shortcut to take a screenshot"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return
    result = execute_tool("screenshot", {"display": "all"})
    if result and result.startswith("SCREENSHOT_SAVED:"):
        paths = result.split(":", 1)[1].split("|")
        for i, spath in enumerate(paths):
            if os.path.exists(spath):
                caption = f"📸 Display {i+1}" if len(paths) > 1 else "📸 Screenshot"
                with open(spath, 'rb') as photo:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=photo,
                        caption=caption
                    )
    else:
        await update.message.reply_text(f"⚠️ Screenshot failed: {result}")

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick shortcut to system health"""
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return
    result = execute_tool("system_health", {})
    await send_long_message(update, result)


def setup_projects_folder():
    """First-run setup: create shared projects folder and migrate legacy projects"""
    import shutil

    # Create the shared folder if it doesn't exist
    if not os.path.exists(PROJECTS_DIR):
        os.makedirs(PROJECTS_DIR, exist_ok=True)
        print(f"📁 Created shared projects folder: {PROJECTS_DIR}")
        log_activity("setup", detail=f"Created {PROJECTS_DIR}")

    # Check for legacy ~/Projects and offer to migrate
    legacy_dir = os.path.expanduser("~/Projects")
    if os.path.exists(legacy_dir):
        migrated = 0
        for name in os.listdir(legacy_dir):
            src = os.path.join(legacy_dir, name)
            dst = os.path.join(PROJECTS_DIR, name)
            if os.path.isdir(src) and not os.path.exists(dst):
                try:
                    # Symlink instead of move — keeps old paths working
                    shutil.copytree(src, dst, symlinks=True)
                    migrated += 1
                    print(f"  📋 Copied project: {name}")
                except Exception as e:
                    print(f"  ⚠️ Failed to copy {name}: {e}")
        if migrated:
            print(f"📦 Migrated {migrated} project(s) from ~/Projects to {PROJECTS_DIR}")
            log_activity("setup", detail=f"Migrated {migrated} projects")

    # Create/update Cowork instructions for handoff protocol (always overwrite to stay in sync)
    instructions_path = os.path.join(PROJECTS_DIR, ".claude", "instructions.md")
    os.makedirs(os.path.dirname(instructions_path), exist_ok=True)
    with open(instructions_path, 'w') as f:
        f.write("""# Claude Projects — Shared Workspace Instructions

This folder is shared between Cowork (desktop) and Claude Bot (Telegram).

## Automatic Handoff Protocol (ALWAYS ACTIVE)

These behaviors are mandatory in EVERY session, without the user asking:

### On Session Start
1. Check if the current project has a HANDOFF.md
2. If NO: create one immediately before doing any other work. Read existing project files to populate it. Tell the user you created it.
3. If YES: check the "Last touched" line. If YOU (Cowork) were the last to edit, skip the full re-read — just resume. If Claude Bot was the last to edit, do a full read to absorb new context silently.

### After Significant Work / End of Session
1. Update HANDOFF.md automatically — do not wait to be asked
2. Add new features to the Features list
3. Log bugs found or fixed in Known Bugs & Issues
4. Add a Work Log entry with UTC timestamp
5. Update Next Steps

The user should NEVER have to ask for handoff updates. They happen automatically.

## Timestamp Format
All timestamps: YYYY-MM-DD HH:MM:SS UTC (24-hour, with seconds). No exceptions.

## HANDOFF.md Format
```
# Project: <name>

## Summary
<2-3 sentences: what this app does, who it's for, how it works>

## Status: <In Progress | Complete | Blocked | Paused>
## Last touched: <YYYY-MM-DD HH:MM:SS UTC> by <Cowork (Desktop) | Claude Bot (Telegram)>

### Features
<Running list — append only, never remove>
- <feature> — <one-line description>

### Known Bugs & Issues
<Mark [FIXED] when resolved, never delete entries>
- [FIXED] <bug> — <what fixed it>
- [OPEN] <bug> — <context>

### Work Log
<Append-only, newest first>
- <YYYY-MM-DD HH:MM:SS UTC> (<agent>) — <what was done>

### Next Steps
- <what to do next, in priority order>

### Key Files
- <filename> — <what it does>

### Tech Stack
- <languages, frameworks, dependencies>

### Gotchas
- <anything the next agent needs to know>
```

Features, Bugs, and Work Log are append-only — they form the project's institutional memory. Never remove entries. Mark bugs [FIXED] when resolved.

## Folder Structure
All new projects should be created as subfolders here. Each project is independent with its own git repo, HANDOFF.md, and files.

## Why This Exists
The user controls their Mac remotely via a Telegram bot (Claude Bot) and also works locally via Cowork. This shared folder + handoff system lets both agents collaborate on the same projects without losing context. If the handoff goes stale, the other agent works blind.
""")
        print(f"📝 Created Cowork instructions at {instructions_path}")


def main():
    check_single_instance()
    load_memory()
    setup_projects_folder()
    log_activity("bot_started", detail=f"whitelist={ALLOWED_USER_IDS or 'NONE - UNSECURED'}")

    if not ALLOWED_USER_IDS:
        logging.warning("⚠️  NO WHITELIST CONFIGURED! Set ALLOWED_TELEGRAM_IDS env var.")
        logging.warning("⚠️  Message the bot to see your user ID, then set the env var.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Slash commands (registered before the catch-all text handler)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cost", cmd_cost))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("screenshot", cmd_screenshot))
    app.add_handler(CommandHandler("health", cmd_health))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Schedule jobs (requires pip install "python-telegram-bot[job-queue]")
    job_queue = app.job_queue
    if job_queue is not None:
        if STANDUP_CHAT_ID:
            job_queue.run_daily(
                daily_standup,
                time=dt_time(hour=STANDUP_HOUR, minute=0),
                name="daily_standup"
            )
            print(f"Daily standup scheduled for {STANDUP_HOUR}:00 -> chat {STANDUP_CHAT_ID}")
        job_queue.run_repeating(check_builds, interval=10, first=10, name="build_checker")
    else:
        logging.warning("JobQueue not available. Install with: pip install 'python-telegram-bot[job-queue]'")

    print("Bot is running with full computer access...")
    print(f"Whitelist: {ALLOWED_USER_IDS or 'NONE - message bot to get your ID'}")
    print(f"Memory: {len(conversation_history)} saved conversations")
    app.run_polling()

if __name__ == "__main__":
    main()