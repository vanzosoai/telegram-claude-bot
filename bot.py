import os
import json
import logging
import subprocess
import time
from datetime import datetime, time as dt_time
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import anthropic

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
MEMORY_FILE = os.path.expanduser("~/telegram-claude-bot/.bot_memory.json")

def save_memory():
    """Save conversation history to disk for persistence across restarts"""
    try:
        # Convert to serializable format, keeping last 20 messages per user
        data = {}
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
            data[str(uid)] = serializable
        with open(MEMORY_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logging.error(f"Failed to save memory: {e}")

def load_memory():
    """Load conversation history from disk"""
    global conversation_history
    try:
        if os.path.exists(MEMORY_FILE):
            with open(MEMORY_FILE, 'r') as f:
                data = json.load(f)
            conversation_history = {int(k): v for k, v in data.items()}
            logging.info(f"Loaded memory for {len(conversation_history)} users")
    except Exception as e:
        logging.error(f"Failed to load memory: {e}")
        conversation_history = {}

# Change this to your secret kill code
KILL_CODE = "killclaudenow"

# Only allow operations in these folders
ALLOWED_PATHS = [
    "/Users/johnjurkoii/Projects",
    "/Users/johnjurkoii/telegram-claude-bot",
    "/Users/johnjurkoii/Desktop",
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
        "description": "Take a screenshot of the Mac screen. Returns the path to the saved screenshot image.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
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

def execute_tool(tool_name, tool_input):
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
            projects_dir = os.path.expanduser("~/Projects")
            if not os.path.exists(projects_dir):
                return "~/Projects directory not found"
            entries = []
            for name in sorted(os.listdir(projects_dir)):
                full_path = os.path.join(projects_dir, name)
                if os.path.isdir(full_path):
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
                    entries.append(f"📁 {name} — {mtime}{git_status}")
            return "\n".join(entries) if entries else "No projects found in ~/Projects"

        elif tool_name == "screenshot":
            screenshot_path = "/tmp/bot_screenshot.png"
            result = subprocess.run(
                ["screencapture", "-x", screenshot_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and os.path.exists(screenshot_path):
                return f"SCREENSHOT_SAVED:{screenshot_path}"
            return f"Failed to take screenshot: {result.stderr}"

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
        return "claude-sonnet-4-5-20250514"
    for signal in complex_signals:
        if signal in msg_lower:
            return "claude-sonnet-4-5-20250514"
    return "claude-haiku-4-5-20251001"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.username or "unknown"
    user_message = update.message.text

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

    # Emergency kill code - nuclear shutdown
    if user_message.strip().lower() == KILL_CODE:
        log_activity("kill_code", user_id, "Emergency shutdown triggered")
        await update.message.reply_text("🛑 Emergency stop activated. Shutting everything down.")
        subprocess.run("pkill -9 -f ngrok", shell=True)
        subprocess.run("lsof -ti:8080 | xargs kill -9", shell=True)
        subprocess.run("pkill -9 -f menubar.py", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.johnjurkoii.claudebot.plist", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.johnjurkoii.menubar.plist", shell=True)
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
Projects: /Users/johnjurkoii/Projects

You can ONLY work within these folders:
- /Users/johnjurkoii/Projects (for all projects)
- /Users/johnjurkoii/telegram-claude-bot (for bot files)
- /Users/johnjurkoii/Desktop
- /tmp

Never touch system files, never use sudo, never modify anything outside these folders.

To show an app:
1. lsof -ti:8080 | xargs kill -9
2. nohup python3 -m http.server 8080 & (in project folder)
3. ngrok is already running as a background service, do NOT start it again
4. curl -s http://127.0.0.1:4040/api/tunnels to get the public URL
5. Return the public_url to user

Stop server: lsof -ti:8080 | xargs kill -9

You have a list_projects tool to show all projects in ~/Projects with git status.
You have a screenshot tool to capture the screen.

IMPORTANT: Complete tasks in as few steps as possible. Do not repeat failed commands.
You are running as: {model_label} {"Haiku (fast)" if "haiku" in model else "Sonnet (powerful)"}""",
            messages=messages,
            tools=tools
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            status_message = ""

            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    label = tool_input.get('command', tool_name)
                    status_message += f"⚙️ `{label}`\n"
                    result = execute_tool(tool_name, tool_input)

                    # Handle screenshot - send as photo in Telegram
                    if result and result.startswith("SCREENSHOT_SAVED:"):
                        screenshot_path = result.split(":", 1)[1]
                        try:
                            with open(screenshot_path, 'rb') as photo:
                                await context.bot.send_photo(
                                    chat_id=update.effective_chat.id,
                                    photo=photo,
                                    caption="📸 Screenshot"
                                )
                            result = "Screenshot sent to chat successfully."
                        except Exception as e:
                            result = f"Screenshot taken but failed to send: {e}"

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
        projects_dir = os.path.expanduser("~/Projects")
        summary_parts = ["☀️ *Morning Standup*\n"]

        if os.path.exists(projects_dir):
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
    finished = []
    for pid, info in build_watchers.items():
        try:
            os.kill(pid, 0)  # Check if still running
        except OSError:
            # Process finished
            elapsed = int(time.time() - info["start"])
            msg = f"🔔 Build finished ({elapsed}s): `{info['command'][:100]}`"
            try:
                await context.bot.send_message(
                    chat_id=info["chat_id"],
                    text=msg
                )
            except:
                pass
            finished.append(pid)
            log_activity("build_finished", detail=info["command"])

    for pid in finished:
        del build_watchers[pid]


def main():
    load_memory()
    log_activity("bot_started", detail=f"whitelist={ALLOWED_USER_IDS or 'NONE - UNSECURED'}")

    if not ALLOWED_USER_IDS:
        logging.warning("⚠️  NO WHITELIST CONFIGURED! Set ALLOWED_TELEGRAM_IDS env var.")
        logging.warning("⚠️  Message the bot to see your user ID, then set the env var.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

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