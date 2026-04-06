import rumps
import subprocess
import os
import time
import tempfile
import base64

MENUBAR_PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.johnjurkoii.claudemenubar.plist")
BOT_PATH = "/Users/johnjurkoii/telegram-claude-bot/bot.py"
BOT_DIR = "/Users/johnjurkoii/telegram-claude-bot"
MENUBAR_PATH = "/Users/johnjurkoii/telegram-claude-bot/menubar.py"

# Base64-encoded 44x44 PNG template icon (terminal prompt silhouette)
ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACwAAAAsCAYAAAAehFoBAAABIElEQVR4nO2Y3Q7DIAiFZdn7v7K7qZ2zQ46AWlPPxTJTfr4xwKQU+ihm38kz8Msz2KEonE3yBubg3KB7VLirHg/MDZjb4PWocAl3+y3RVRu4t1r6y/UCYCTyoBUeAQvlQYBHwUL5JODRsGLeGvAs2Gr+5bbEcsDlGpndBjVRCL8VvjNsCAff2xwlfn8nEb/3UTtJuWdThXOAS9AMCLUDReYKU5Y1VuhQO0mqLZHyUVGidE7PUbsWLbfWngGc/uGyF9M5PUftWuSx1qBGtAxaLvNNN3AP0/nB5dBEddaFb7mhc72aUVmuZlOFtXNkmT81sHXotf7L9bAa2NKHFv8asBhRmxT0+2uEeM7YxywX0hKur0ut+dAeHgU9ujhbW1uSPlK9YkSi1oxJAAAAAElFTkSuQmCC"

def get_icon_path():
    """Create a temporary icon file from embedded base64 data"""
    icon_path = os.path.join(tempfile.gettempdir(), "claudebot_icon.png")
    if not os.path.exists(icon_path):
        with open(icon_path, 'wb') as f:
            f.write(base64.b64decode(ICON_B64))
    return icon_path

# LaunchAgent plist for the MENUBAR app (which auto-starts the bot)
MENUBAR_PLIST_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.johnjurkoii.claudemenubar</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>{menubar_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TELEGRAM_TOKEN</key>
        <string>{telegram_token}</string>
        <key>ANTHROPIC_API_KEY</key>
        <string>{anthropic_key}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>"""


class ClaudeBotApp(rumps.App):
    def __init__(self):
        icon_path = get_icon_path()
        super(ClaudeBotApp, self).__init__("", icon=icon_path, template=True)
        self.menu = [
            rumps.MenuItem("Status: Checking...", callback=None),
            None,
            rumps.MenuItem("Start Bot", callback=self.start_bot),
            rumps.MenuItem("Stop Bot", callback=self.stop_bot),
            None,
            rumps.MenuItem("Launch at Login: Checking...", callback=self.toggle_launch_at_login),
            None,
        ]
        # Auto-start bot if not already running
        if not self.is_bot_running():
            self.start_bot(None)
        self.update_status()

    def is_bot_running(self):
        pid_file = "/tmp/claudebot.pid"
        if os.path.exists(pid_file):
            try:
                with open(pid_file, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)  # Check if process exists
                return True
            except (OSError, ValueError):
                pass
        return False

    def is_launch_at_login_enabled(self):
        return os.path.exists(MENUBAR_PLIST_PATH)

    def update_status(self):
        running = self.is_bot_running()
        status = "🟢 Running" if running else "🔴 Stopped"
        self.menu["Status: Checking..."].title = f"Status: {status}"
        launch = self.is_launch_at_login_enabled()
        self.menu["Launch at Login: Checking..."].title = f"Launch at Login: {'✅ On' if launch else '❌ Off'}"

    def load_env(self):
        result = subprocess.run(
            'source ~/.zshrc && echo "TELEGRAM_TOKEN=$TELEGRAM_TOKEN" && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"',
            shell=True,
            capture_output=True,
            text=True,
            executable='/bin/zsh'
        )
        env = os.environ.copy()
        for line in result.stdout.strip().split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                env[key] = value
        return env

    def start_bot(self, _):
        if not self.is_bot_running():
            env = self.load_env()
            subprocess.Popen(
                ["python3", BOT_PATH],
                env=env,
                cwd=BOT_DIR,
                stdout=open("/tmp/claudebot.log", "w"),
                stderr=open("/tmp/claudebot.err", "w")
            )
            rumps.notification("Claude Bot", "Started", "🤖 Bot is running!")
        self.update_status()

    def stop_bot(self, _):
        # Use PID file for precise kill
        pid_file = "/tmp/claudebot.pid"
        if os.path.exists(pid_file):
            try:
                with open(pid_file, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, 15)  # SIGTERM - graceful
                time.sleep(2)
                try:
                    os.kill(pid, 9)  # SIGKILL if still alive
                except OSError:
                    pass
                os.remove(pid_file)
            except (OSError, ValueError):
                pass
        # Fallback: pkill
        subprocess.run(["pkill", "-f", "telegram-claude-bot/bot.py"], capture_output=True)
        # Kill any dev servers the bot started
        subprocess.run("lsof -ti:8080 | xargs kill -9 2>/dev/null", shell=True, capture_output=True)
        rumps.notification("Claude Bot", "Stopped", "🤖 Bot stopped.")
        time.sleep(1)
        self.update_status()

    def toggle_launch_at_login(self, _):
        if self.is_launch_at_login_enabled():
            # Unload and remove the menubar LaunchAgent
            subprocess.run(["launchctl", "unload", MENUBAR_PLIST_PATH], capture_output=True)
            try:
                os.remove(MENUBAR_PLIST_PATH)
            except:
                pass
            # Also clean up old bot-only plist if it exists
            old_plist = os.path.expanduser("~/Library/LaunchAgents/com.johnjurkoii.claudebot.plist")
            if os.path.exists(old_plist):
                subprocess.run(["launchctl", "unload", old_plist], capture_output=True)
                try:
                    os.remove(old_plist)
                except:
                    pass
            rumps.notification("Claude Bot", "Launch at Login Disabled",
                             "Menu bar app won't auto-start on login.")
        else:
            env = self.load_env()
            telegram_token = env.get("TELEGRAM_TOKEN", "")
            anthropic_key = env.get("ANTHROPIC_API_KEY", "")
            plist = MENUBAR_PLIST_CONTENT.format(
                menubar_path=MENUBAR_PATH,
                telegram_token=telegram_token,
                anthropic_key=anthropic_key
            )
            os.makedirs(os.path.dirname(MENUBAR_PLIST_PATH), exist_ok=True)
            with open(MENUBAR_PLIST_PATH, 'w') as f:
                f.write(plist)
            subprocess.run(["launchctl", "load", MENUBAR_PLIST_PATH], capture_output=True)
            # Clean up old bot-only plist if it exists
            old_plist = os.path.expanduser("~/Library/LaunchAgents/com.johnjurkoii.claudebot.plist")
            if os.path.exists(old_plist):
                subprocess.run(["launchctl", "unload", old_plist], capture_output=True)
                try:
                    os.remove(old_plist)
                except:
                    pass
            rumps.notification("Claude Bot", "Launch at Login Enabled",
                             "Menu bar app + bot will start automatically on login.")
        self.update_status()

    @rumps.timer(10)
    def check_status(self, _):
        self.update_status()

if __name__ == "__main__":
    ClaudeBotApp().run()
