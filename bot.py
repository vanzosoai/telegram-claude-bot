import os
import logging
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import anthropic

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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
    try:
        if tool_name == "run_command":
            command = tool_input["command"]
            safe, reason = is_safe_command(command)
            if not safe:
                return f"🚫 Command blocked for safety: {reason}"
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            output = result.stdout or result.stderr
            return output if output else "Command executed successfully with no output"

        elif tool_name == "read_file":
            path = tool_input["path"]
            if not is_safe_path(path):
                return f"🚫 Access denied. Can only read from: {', '.join(ALLOWED_PATHS)}"
            with open(path, 'r') as f:
                return f.read()

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

    except Exception as e:
        return f"Error executing {tool_name}: {str(e)}"

async def send_long_message(update, text):
    MAX_LENGTH = 4000
    if len(text) <= MAX_LENGTH:
        await update.message.reply_text(text)
    else:
        chunks = [text[i:i+MAX_LENGTH] for i in range(0, len(text), MAX_LENGTH)]
        for chunk in chunks:
            await update.message.reply_text(chunk)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text

    # Emergency kill code - nuclear shutdown
    if user_message.strip().lower() == KILL_CODE:
        await update.message.reply_text("🛑 Emergency stop activated. Shutting everything down.")
        subprocess.run("pkill -9 -f ngrok", shell=True)
        subprocess.run("lsof -ti:8080 | xargs kill -9", shell=True)
        subprocess.run("pkill -9 -f menubar.py", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.johnjurkoii.claudebot.plist", shell=True)
        subprocess.run("launchctl unload ~/Library/LaunchAgents/com.johnjurkoii.menubar.plist", shell=True)
        os._exit(0)

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

    messages = conversation_history[user_id].copy()
    MAX_ITERATIONS = 4
    iteration = 0

    while iteration < MAX_ITERATIONS:
        iteration += 1

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system="""You are a coding agent on the user's Mac. Be concise and efficient - we're on mobile.
You have tools to run shell commands, read and write files.
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

IMPORTANT: Complete tasks in as few steps as possible. Do not repeat failed commands.""",
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
                    status_message += f"⚙️ `{tool_input.get('command', tool_name)}`\n"
                    result = execute_tool(tool_name, tool_input)
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

            await send_long_message(update, final_response)
            return

    # Hit the loop limit - report back and ask for help
    conversation_history[user_id] = messages
    await send_long_message(update,
        "⚠️ I hit my 4 step limit and couldn't complete the task. Here's where I got stuck - can you give me more guidance or break it into smaller steps?")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running with full computer access...")
    app.run_polling()

if __name__ == "__main__":
    main()