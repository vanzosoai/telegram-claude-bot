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
import asyncio
import anthropic

# === SINGLE INSTANCE LOCK ===
PID_FILE = "/tmp/piclobot.pid"

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
activity_handler = logging.FileHandler('/tmp/piclobot_activity.log')
activity_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
activity_logger.addHandler(activity_handler)
activity_logger.setLevel(logging.INFO)

def log_activity(event_type, user_id=None, detail=""):
    """Log all bot activity to /tmp/piclobot_activity.log"""
    entry = {"event": event_type, "user_id": user_id, "detail": detail[:500]}
    activity_logger.info(json.dumps(entry))


def log_handoff_session(user_message, bot_response, tools_used=None, source="Telegram"):
    """Append a session entry to the bot's HANDOFF.md so Cowork knows what happened.

    Only logs meaningful interactions (tool use, code changes, project work).
    Skips casual chitchat to keep the work log clean and useful.
    """
    try:
        from datetime import datetime, timezone

        # Skip logging for trivial interactions (short Q&A, greetings, etc.)
        if not tools_used and len(bot_response) < 200:
            return
        if any(skip in user_message.lower() for skip in ["hello", "hi ", "hey ", "thanks", "thank you", "ok", "cool"]):
            if not tools_used:
                return

        handoff_path = os.path.join(BOT_OWN_DIR, "HANDOFF.md")
        if not os.path.exists(handoff_path):
            return

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Build a concise summary focused on what was DONE, not the full response
        user_short = user_message[:100].replace('\n', ' ').strip()
        if tools_used:
            tools_summary = ", ".join(tools_used[:5])
            entry = f"- {timestamp} (Piclo Bot) — \"{user_short}\" [tools: {tools_summary}]\n"
        else:
            # For non-tool responses, summarize the first sentence of the response
            first_line = bot_response.split('\n')[0][:150].strip()
            entry = f"- {timestamp} (Piclo Bot) — \"{user_short}\" → {first_line}\n"

        with open(handoff_path, 'r') as f:
            content = f.read()

        marker = "### Work Log\n"
        if marker in content:
            pos = content.index(marker) + len(marker)
            content = content[:pos] + entry + content[pos:]

            import re
            content = re.sub(
                r'## Last touched:.*',
                f'## Last touched: {timestamp} by Piclo Bot (Telegram)',
                content
            )

            with open(handoff_path, 'w') as f:
                f.write(content)
    except Exception as e:
        logging.warning(f"Handoff log failed (non-critical): {e}")

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

# Persistent memory: save/load conversation history + session context to disk
MEMORY_FILE = os.path.expanduser("~/Documents/Claude Projects/telegram-claude-bot/.bot_memory.json")

# Session context — persists across restarts so the bot never loses track of what's happening
session_context = {}  # user_id -> {"active_project": str, "active_task": str, "last_tools": list, "summary": str}

def save_memory():
    """Save conversation history, token usage, and session context to disk"""
    try:
        # Convert to serializable format, keeping last 40 messages per user
        # IMPORTANT: Only save simple user/assistant text messages.
        # Tool use/result blocks have ephemeral IDs that cause 400 errors
        # if loaded into a new session. The session_context captures what
        # tools did without the fragile ID references.
        convos = {}
        for uid, messages in conversation_history.items():
            serializable = []
            for msg in messages[-40:]:
                if isinstance(msg.get("content"), str):
                    serializable.append(msg)
                elif isinstance(msg.get("content"), list):
                    # Tool results/tool use blocks — skip entirely
                    # (their tool_use_ids become orphaned across restarts)
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

        # Serialize session context
        ctx = {}
        for uid, context in session_context.items():
            ctx[str(uid)] = context

        data = {
            "conversations": convos,
            "token_usage": token_usage,
            "session_context": ctx,
            "saved_at": time.time(),
        }
        with open(MEMORY_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save memory: {e}")

def load_memory():
    """Load conversation history, token usage, and session context from disk"""
    global conversation_history, token_usage, session_context
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r') as f:
                data = json.load(f)
            if "conversations" in data:
                raw_convos = {int(k): v for k, v in data["conversations"].items()}
                # Sanitize: strip any tool_result messages that would cause
                # orphaned tool_use_id errors with the API
                for uid, msgs in raw_convos.items():
                    clean = []
                    for msg in msgs:
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            # This is a tool_result or tool_use block — skip it
                            continue
                        if isinstance(content, str):
                            clean.append(msg)
                    raw_convos[uid] = clean
                conversation_history = raw_convos
                saved_usage = data.get("token_usage", {})
                if saved_usage:
                    token_usage["total_input"] = saved_usage.get("total_input", 0)
                    token_usage["total_output"] = saved_usage.get("total_output", 0)
                    token_usage["session_messages"] = saved_usage.get("session_messages", 0)
                    token_usage["by_model"] = saved_usage.get("by_model", {})
                # Load session context
                saved_ctx = data.get("session_context", {})
                session_context = {int(k): v for k, v in saved_ctx.items()}

                saved_time = data.get("saved_at", 0)
                age_min = (time.time() - saved_time) / 60 if saved_time else 0
                logging.info(f"Loaded memory: {len(conversation_history)} users, "
                           f"{len(session_context)} active contexts, "
                           f"saved {age_min:.0f}min ago")
            else:
                conversation_history = {int(k): v for k, v in data.items()}
    except Exception as e:
        logging.error(f"Failed to load memory: {e}")
        conversation_history = {}
        session_context = {}


def update_session_context(user_id, user_message, bot_response, tools_used=None):
    """Update the persistent session context for a user.

    This is the bot's 'working memory' — what project are we in, what task
    is in progress, what tools were just used. Survives restarts.
    """
    if user_id not in session_context:
        session_context[user_id] = {
            "active_project": None,
            "active_task": None,
            "last_tools": [],
            "summary": "",
            "last_interaction": 0,
        }

    ctx = session_context[user_id]
    ctx["last_interaction"] = time.time()
    ctx["last_tools"] = (tools_used or [])[-10:]  # Last 10 tools used

    # Try to detect active project from tool usage (file paths, cwd references)
    if tools_used:
        for tool in tools_used:
            if tool in ["read_file", "write_file", "run_command", "serve_project"]:
                # The project context gets set by the conversation naturally
                pass

    # Build a running summary of the session (last 3 exchanges)
    user_short = user_message[:100].strip()
    response_short = bot_response.split('\n')[0][:150].strip()
    summary_entry = f"User: {user_short} → Bot: {response_short}"

    # Keep a rolling window of recent context
    existing = ctx.get("summary", "")
    lines = existing.split(" | ") if existing else []
    lines.append(summary_entry)
    ctx["summary"] = " | ".join(lines[-5:])  # Keep last 5 exchanges

# Change this to your secret kill code
KILL_CODE = "killpiclonow"

# Shared projects folder — read from config (user picks on first launch)
from config import get_projects_dir
_configured_dir = get_projects_dir()
PROJECTS_DIR = _configured_dir if _configured_dir else os.path.expanduser("~/Documents/Claude Projects")

# Bot's own directory (for self-referencing)
BOT_OWN_DIR = os.path.dirname(os.path.abspath(__file__))

# Only allow operations in these folders
ALLOWED_PATHS = [
    PROJECTS_DIR,
    BOT_OWN_DIR,
    os.path.expanduser("~/Projects"),  # legacy path
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
        "description": "Take a screenshot of the Mac. Can capture all displays, a single display, or a specific app window. When the user says 'show me the app' or 'what does X look like', use window mode with the app name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "display": {"type": "string", "description": "Which display: 'all' (default, both displays), '1' (main), or '2' (secondary)", "default": "all"},
                "window": {"type": "string", "description": "App name to capture a specific window (e.g. 'Safari', 'Chrome', 'Terminal', 'Finder'). If set, captures just that app's frontmost window instead of the full screen."}
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
            window_app = tool_input.get("window", "")
            screenshot_raw_1 = "/tmp/bot_screenshot_1.png"
            screenshot_raw_2 = "/tmp/bot_screenshot_2.png"
            screenshot_path = "/tmp/bot_screenshot.jpg"

            if window_app:
                # Targeted window capture — get a specific app's window
                screenshot_raw = "/tmp/bot_screenshot_window.png"
                try:
                    # Use AppleScript to get the window ID of the target app
                    window_id_script = f'''
                    tell application "System Events"
                        set appProc to first process whose name contains "{window_app}"
                        set winID to id of first window of appProc
                        return winID
                    end tell
                    '''
                    id_result = subprocess.run(
                        ["osascript", "-e", window_id_script],
                        capture_output=True, text=True, timeout=5
                    )
                    if id_result.returncode == 0 and id_result.stdout.strip():
                        win_id = id_result.stdout.strip()
                        result = subprocess.run(
                            ["screencapture", "-x", "-l", win_id, screenshot_raw],
                            capture_output=True, text=True, timeout=10
                        )
                    else:
                        # Fallback: bring app to front and capture the main screen
                        subprocess.run(
                            ["osascript", "-e", f'tell application "{window_app}" to activate'],
                            capture_output=True, timeout=5
                        )
                        time.sleep(0.5)
                        result = subprocess.run(
                            ["screencapture", "-x", screenshot_raw],
                            capture_output=True, text=True, timeout=10
                        )
                except Exception:
                    # Final fallback: just capture the screen
                    result = subprocess.run(
                        ["screencapture", "-x", screenshot_raw],
                        capture_output=True, text=True, timeout=10
                    )

                if not os.path.exists(screenshot_raw):
                    return f"Failed to capture {window_app} window"

            elif display == "all" or display == "both":
                # Capture both displays as separate files
                result = subprocess.run(
                    ["screencapture", "-x", screenshot_raw_1, screenshot_raw_2],
                    capture_output=True, text=True, timeout=10
                )
                screenshots = []
                for f in [screenshot_raw_1, screenshot_raw_2]:
                    if os.path.exists(f):
                        screenshots.append(f)
                if not screenshots:
                    return f"Failed to take screenshot: {result.stderr}"
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
                if not window_app and display in ["all", "both"] and len(screenshots) > 1:
                    second_jpg = "/tmp/bot_screenshot_2.jpg"
                    subprocess.run(
                        ["sips", "--resampleWidth", "1920", "--setProperty", "format", "jpeg",
                         "--setProperty", "formatOptions", "60", screenshots[1], "--out", second_jpg],
                        capture_output=True, text=True, timeout=10
                    )
                    if os.path.exists(second_jpg):
                        return f"SCREENSHOT_SAVED:{screenshot_path}|{second_jpg}"
                caption = f" of {window_app}" if window_app else ""
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

def sanitize_messages(messages):
    """Remove orphaned tool_result blocks that would cause API 400 errors.

    The Anthropic API requires every tool_result to reference a tool_use_id
    that exists earlier in the conversation. If conversation history gets
    truncated or corrupted, tool_results can become orphaned. This strips them.
    """
    # Collect all tool_use IDs present in assistant messages
    valid_tool_ids = set()
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for block in content:
                # Handle both dict and object-style blocks
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    valid_tool_ids.add(block.get("id", ""))
                elif hasattr(block, "type") and block.type == "tool_use":
                    valid_tool_ids.add(block.id)

    # Filter out tool_result messages with orphaned IDs
    clean = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            # Check if this is a tool_result message
            has_tool_results = any(
                (isinstance(b, dict) and b.get("type") == "tool_result")
                for b in content
            )
            if has_tool_results:
                # Keep only tool_results whose IDs exist in the conversation
                filtered = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        if block.get("tool_use_id", "") in valid_tool_ids:
                            filtered.append(block)
                    else:
                        filtered.append(block)
                if filtered:
                    clean.append({"role": msg["role"], "content": filtered})
                # If all blocks were orphaned, drop the entire message
                continue
        clean.append(msg)
    return clean


async def send_long_message(update, text, voice_reply=False):
    """Send a text response, optionally with a voice note version."""
    MAX_LENGTH = 4000
    if len(text) <= MAX_LENGTH:
        await update.message.reply_text(text)
    else:
        chunks = [text[i:i+MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
        for chunk in chunks:
            await update.message.reply_text(chunk)

    # If the user sent a voice message, send a voice summary back
    if voice_reply:
        await send_voice_reply(update, text)


async def stream_response_to_telegram(update, context, model, system_prompt, messages):
    """Stream Claude's response with live message editing in Telegram.
    Returns (full_response_obj, final_text) for tool_use or (response_obj, final_text) for text.
    For tool_use responses, falls back to non-streaming since we need the full response."""

    # First, try streaming. Collect text and watch for tool_use.
    collected_text = ""
    collected_blocks = []
    sent_message = None
    last_edit_time = 0
    EDIT_INTERVAL = 0.8  # Telegram rate limits edits; don't hammer it
    input_tokens = 0
    output_tokens = 0
    stop_reason = None

    try:
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=tools
        ) as stream:
            for event in stream:
                if hasattr(event, 'type'):
                    if event.type == 'content_block_start':
                        if hasattr(event.content_block, 'type') and event.content_block.type == 'tool_use':
                            # Tool use detected — we need to collect the full response
                            pass
                    elif event.type == 'content_block_delta':
                        if hasattr(event.delta, 'text'):
                            collected_text += event.delta.text
                            now = time.time()
                            # Send or edit the message periodically
                            if now - last_edit_time >= EDIT_INTERVAL and len(collected_text) > 0:
                                try:
                                    if sent_message is None:
                                        sent_message = await update.message.reply_text(collected_text[:4000])
                                    else:
                                        display = collected_text[:4000]
                                        if display != (sent_message.text or ""):
                                            await sent_message.edit_text(display)
                                    last_edit_time = now
                                except Exception as e:
                                    logging.debug(f"Stream edit skipped: {e}")

            # Get the final message object from the stream
            final_message = stream.get_final_message()
            stop_reason = final_message.stop_reason
            input_tokens = final_message.usage.input_tokens if hasattr(final_message, 'usage') else 0
            output_tokens = final_message.usage.output_tokens if hasattr(final_message, 'usage') else 0

        # Final edit to make sure the complete text is shown
        if collected_text and sent_message:
            try:
                if len(collected_text) <= 4000:
                    if collected_text != (sent_message.text or ""):
                        await sent_message.edit_text(collected_text)
                else:
                    # Text exceeded one message — send remaining chunks
                    chunks = [collected_text[i:i+4000] for i in range(4000, len(collected_text), 4000)]
                    for chunk in chunks:
                        await update.message.reply_text(chunk)
            except Exception as e:
                logging.debug(f"Final stream edit skipped: {e}")
        elif collected_text and not sent_message:
            # Never managed to send — send now
            await send_long_message(update, collected_text)

        return final_message, collected_text, sent_message

    except Exception as e:
        logging.error(f"Streaming failed, falling back to blocking: {e}")
        # Fallback to non-streaming
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=tools
        )
        final_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                final_text += block.text
        return response, final_text, None


async def send_voice_reply(update, text):
    """Generate and send a TTS voice note summarizing the response."""
    import uuid
    tts_path = f"/tmp/piclobot_tts_{uuid.uuid4().hex[:8]}.aiff"
    ogg_path = tts_path.replace('.aiff', '.ogg')
    try:
        # Strip markdown, code blocks, emojis, and clutter for clean speech
        import re
        clean = re.sub(r'```[\s\S]*?```', 'See code in the text response.', text)
        clean = re.sub(r'`[^`]+`', '', clean)
        clean = re.sub(r'[*_#>~\[\]()]', '', clean)
        clean = re.sub(r'https?://\S+', 'link in the text response', clean)
        # Strip all emojis and unicode symbols
        clean = re.sub(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251\U00002600-\U000026FF\U00002700-\U000027BF\U0000FE00-\U0000FE0F\U0000200D\U00002B50\U00002B55\U000023CF\U000023E9-\U000023F3\U000023F8-\U000023FA\U0000231A\U0000231B\U000025AA-\U000025FE\U00002934\U00002935\U00002328\U000023CF]+', '', clean)
        # Clean up any leftover double spaces
        clean = re.sub(r'  +', ' ', clean)
        clean = clean.strip()

        # Truncate for TTS — keep it brief
        if len(clean) > 500:
            sentences = re.split(r'(?<=[.!?])\s+', clean)
            clean = ' '.join(sentences[:3]) + ' Check the text response for full details.'

        if not clean or len(clean) < 10:
            logging.warning(f"TTS skipped: cleaned text too short ({len(clean)} chars)")
            return

        # Use macOS say for TTS
        # Use a more natural macOS voice if available
        # Samantha (premium) > Daniel > default. Falls back gracefully.
        logging.info(f"TTS: generating speech ({len(clean)} chars)...")
        say_cmd = ["say", "-o", tts_path]
        # Try premium voices first, fall back to default
        for voice in ["Samantha (Enhanced)", "Samantha", "Daniel"]:
            test = subprocess.run(["say", "-v", voice, ""], capture_output=True)
            if test.returncode == 0:
                say_cmd.extend(["-v", voice])
                break
        say_cmd.append(clean)
        result = subprocess.run(
            say_cmd,
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            err = result.stderr.decode() if result.stderr else "unknown error"
            logging.error(f"TTS say failed (rc={result.returncode}): {err}")
            await update.message.reply_text(f"🔇 Voice generation failed: {err[:200]}")
            return

        # Convert to ogg/opus for Telegram voice notes
        logging.info("TTS: converting to opus...")
        conv = subprocess.run(
            ["ffmpeg", "-i", tts_path, "-c:a", "libopus", "-b:a", "64k", "-y", ogg_path],
            capture_output=True, timeout=15
        )
        if conv.returncode != 0:
            err = conv.stderr.decode() if conv.stderr else "unknown error"
            logging.error(f"TTS ffmpeg failed (rc={conv.returncode}): {err}")
            await update.message.reply_text(f"🔇 Voice conversion failed: {err[:200]}")
            return

        # Verify file exists and has content
        if not os.path.exists(ogg_path) or os.path.getsize(ogg_path) < 100:
            logging.error(f"TTS ogg file missing or too small: {ogg_path}")
            await update.message.reply_text("🔇 Voice file was empty — TTS may not be working.")
            return

        # Get duration so Telegram renders the proper voice player with speed controls
        duration = None
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", ogg_path],
                capture_output=True, text=True, timeout=5
            )
            if probe.returncode == 0 and probe.stdout.strip():
                duration = int(float(probe.stdout.strip()))
        except Exception:
            pass  # Duration is optional, voice still sends without it

        # Send as voice note
        logging.info(f"TTS: sending voice note ({os.path.getsize(ogg_path)} bytes, {duration}s)...")
        with open(ogg_path, 'rb') as voice:
            await update.message.reply_voice(voice=voice, duration=duration)
        logging.info("TTS: voice note sent successfully")

    except subprocess.TimeoutExpired:
        logging.error("TTS timed out")
        await update.message.reply_text("🔇 Voice generation timed out.")
    except Exception as e:
        logging.error(f"TTS failed: {e}")
        await update.message.reply_text(f"🔇 Voice failed: {str(e)[:200]}")
    finally:
        for path in [tts_path, ogg_path]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text_override=None, from_voice=False):
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
            # Send voice reply for shortcut commands too
            if from_voice:
                # Grab last bot message for TTS (the command handler already sent text)
                # We can't easily get the text back, so just confirm vocally
                await send_voice_reply(update, f"Done. I ran the {handler.__name__.replace('cmd_', '')} command. Check the text response for details.")
            return

    # Emergency kill code - nuclear shutdown
    if msg_lower == KILL_CODE.lower():
        log_activity("kill_code", user_id, "Emergency shutdown triggered")
        await update.message.reply_text("🛑 Emergency stop activated. Shutting everything down.")
        subprocess.run("pkill -9 -f ngrok", shell=True)
        subprocess.run("lsof -ti:8080 | xargs kill -9", shell=True)
        subprocess.run("pkill -9 -f menubar.py", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.piclobot.legacy.plist 2>/dev/null", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.piclobot.menubar.plist 2>/dev/null", shell=True)
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

    # Fire-and-forget typing indicator — don't block on it
    asyncio.create_task(context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    ))

    # Smart model routing + sanitization (these are fast CPU ops)
    model = classify_complexity(user_message)
    model_label = "⚡" if "haiku" in model else "🧠"
    logging.info(f"Model selected: {model} for message from {user_id}")
    log_activity("model_selected", user_id, f"{model} for: {user_message[:100]}")

    messages = sanitize_messages(conversation_history[user_id].copy())
    MAX_ITERATIONS = 6
    iteration = 0
    tools_used_this_session = []

    # Build session context hint for the system prompt
    ctx = session_context.get(user_id, {})
    context_hint = ""
    if ctx.get("summary"):
        context_hint = f"\n\nSESSION CONTEXT (recent activity, survives restarts):\n{ctx['summary']}"
        if ctx.get("last_tools"):
            context_hint += f"\nRecent tools used: {', '.join(ctx['last_tools'][-5:])}"
        age = (time.time() - ctx.get("last_interaction", 0)) / 60
        if age > 60:
            context_hint += f"\n(Last interaction was {age:.0f} minutes ago — user may have been away)"

    # Build system prompt once (reused across iterations)
    system_prompt = f"""You are Piclo Bot, a coding agent on the user's Mac. Be concise and efficient - we're on mobile.
You have tools to run shell commands, read and write files, list projects, and take screenshots.
When the user sends a voice message, you will automatically receive a text transcription of it. Your text response will automatically be converted to a voice note and sent back alongside the text. You DO have voice/audio capability — don't tell the user otherwise.
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
- BEFORE starting work: check the "Last touched" line in HANDOFF.md. If it says "by Piclo Bot (Telegram)" then YOU were the last to edit — skip the full re-read and just resume working. If it says "by Cowork (Desktop)" then the OTHER agent made changes — do a full read to absorb new context.
- If no HANDOFF.md exists, create one before doing any other work.
- AFTER completing work: update HANDOFF.md with what you did and what's next
- ALL timestamps must be UTC 24-hour format with seconds: YYYY-MM-DD HH:MM:SS UTC
- Use Python: datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
- HANDOFF.md format:
  # Project: <name>
  ## Summary
  <2-3 sentences: what this app does, who it's for, how it works>
  ## Status: <In Progress | Complete | Blocked | Paused>
  ## Last touched: <YYYY-MM-DD HH:MM:SS UTC> by Piclo Bot (Telegram)
  ### Features
  <Running list — append only, never remove>
  - <feature> — <one-line description>
  ### Known Bugs & Issues
  <Running list — mark [FIXED] when resolved, never delete entries>
  - [FIXED] <bug> — <what fixed it>
  - [OPEN] <bug> — <context>
  ### Work Log
  <Append-only, newest first. Each entry gets UTC timestamp + agent name.>
  - YYYY-MM-DD HH:MM:SS UTC (Piclo Bot) — <what was done>
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
You are running as: {model_label} {"Haiku (fast)" if "haiku" in model else "Sonnet (powerful)"}{context_hint}"""

    while iteration < MAX_ITERATIONS:
        iteration += 1

        # On the first iteration with no prior tool use, try streaming for faster perceived response
        use_streaming = (iteration == 1 and len(tools_used_this_session) == 0)

        if use_streaming:
            try:
                response, streamed_text, sent_msg = await stream_response_to_telegram(
                    update, context, model, system_prompt, messages
                )
                track_tokens(response, model)

                if response.stop_reason == "tool_use":
                    # Streaming showed partial text, but Claude wants tools.
                    # Delete the streamed message if it was just thinking text
                    if sent_msg and streamed_text:
                        try:
                            await sent_msg.delete()
                        except Exception:
                            pass
                    # Fall through to tool handling below
                else:
                    # Pure text response — already displayed via streaming
                    final_response = streamed_text or ""
                    if not final_response:
                        for block in response.content:
                            if hasattr(block, "text"):
                                final_response += block.text

                    conversation_history[user_id] = messages
                    conversation_history[user_id].append({
                        "role": "assistant",
                        "content": final_response
                    })

                    log_activity("response_sent", user_id, final_response[:200])
                    update_session_context(user_id, user_message, final_response, tools_used_this_session)
                    save_memory()
                    log_handoff_session(user_message, final_response, tools_used=tools_used_this_session)

                    # Voice reply (TTS) — run in parallel, text is already delivered
                    if from_voice:
                        await send_voice_reply(update, final_response)
                    return

            except Exception as e:
                logging.error(f"Streaming path failed, falling back: {e}")
                use_streaming = False

        if not use_streaming or (use_streaming and response.stop_reason == "tool_use"):
            # Non-streaming path: tool iterations or fallback
            if not (use_streaming and response.stop_reason == "tool_use"):
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
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
                        tools_used_this_session.append(tool_name)
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
                update_session_context(user_id, user_message, final_response, tools_used_this_session)
                save_memory()
                log_handoff_session(user_message, final_response, tools_used=tools_used_this_session)
                await send_long_message(update, final_response, voice_reply=from_voice)
                return

    # Hit the loop limit - report back
    conversation_history[user_id] = messages
    save_memory()
    await send_long_message(update,
        f"⚠️ I hit my {MAX_ITERATIONS} step limit. Here's where I got - can you give me more guidance or break it into smaller steps?",
        voice_reply=from_voice)

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

        # Convert to 16kHz mono WAV (required by whisper models)
        convert_result = subprocess.run(
            ["ffmpeg", "-i", ogg_path, "-ar", "16000", "-ac", "1", "-y", wav_path],
            capture_output=True, text=True, timeout=15
        )
        if convert_result.returncode != 0:
            # Try macOS native converter as fallback
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", ogg_path, wav_path],
                capture_output=True, text=True, timeout=15
            )

        # Transcribe using whisper (pywhispercpp preferred, openai-whisper fallback)
        transcript = None

        # Try pywhispercpp first (lightweight, no PyTorch dependency)
        transcribe_errors = []
        if not transcript:
            try:
                from pywhispercpp.model import Model as WhisperModel
                model = WhisperModel("tiny")
                segments = model.transcribe(wav_path)
                text = " ".join([seg.text for seg in segments]).strip()
                if text:
                    transcript = text
                else:
                    transcribe_errors.append("pywhispercpp: empty result")
            except Exception as e:
                transcribe_errors.append(f"pywhispercpp: {e}")
                logging.warning(f"pywhispercpp failed: {e}", exc_info=True)

        # Fallback: try openai-whisper (heavier, requires PyTorch)
        if not transcript:
            try:
                import whisper as whisper_pkg
                model = whisper_pkg.load_model("tiny")
                result = model.transcribe(wav_path)
                transcript = result["text"].strip()
            except ImportError:
                transcribe_errors.append("openai-whisper: not installed")
            except Exception as e:
                transcribe_errors.append(f"openai-whisper: {e}")
                logging.warning(f"openai-whisper failed: {e}")

        # Fallback: whisper CLI
        if not transcript:
            try:
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
                else:
                    transcribe_errors.append("whisper CLI: not found")
            except Exception as e:
                transcribe_errors.append(f"whisper CLI: {e}")
                logging.warning(f"whisper CLI failed: {e}")

        if not transcript:
            error_detail = "; ".join(transcribe_errors) if transcribe_errors else "Unknown"
            logging.error(f"All transcription failed: {error_detail}")
            await update.message.reply_text(
                f"⚠️ Transcription failed:\n{error_detail}",
                parse_mode=None
            )
            return

        await update.message.reply_text(f"🎤 Heard: \"{transcript}\"")
        log_activity("voice_transcribed", user_id, transcript)

        # Process as a normal text message using text_override, with voice response
        await handle_message(update, context, text_override=transcript, from_voice=True)

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
        "🤖 *Piclo Bot Commands*\n\n"
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
            "tail -5 /tmp/piclobot_activity.log",
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

This folder is shared between Cowork (desktop) and Piclo Bot (Telegram).

## Automatic Handoff Protocol (ALWAYS ACTIVE)

These behaviors are mandatory in EVERY session, without the user asking:

### On Session Start
1. Check if the current project has a HANDOFF.md
2. If NO: create one immediately before doing any other work. Read existing project files to populate it. Tell the user you created it.
3. If YES: check the "Last touched" line. If YOU (Cowork) were the last to edit, skip the full re-read — just resume. If Piclo Bot was the last to edit, do a full read to absorb new context silently.

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
## Last touched: <YYYY-MM-DD HH:MM:SS UTC> by <Cowork (Desktop) | Piclo Bot (Telegram)>

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
The user controls their Mac remotely via a Telegram bot (Piclo Bot) and also works locally via Cowork. This shared folder + handoff system lets both agents collaborate on the same projects without losing context. If the handoff goes stale, the other agent works blind.
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