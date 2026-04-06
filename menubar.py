import rumps
import subprocess
import os
import time
import tempfile
import base64

PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.johnjurkoii.claudebot.plist")
BOT_PATH = "/Users/johnjurkoii/telegram-claude-bot/bot.py"
NGROK_PATH = "/opt/homebrew/bin/ngrok"

# Base64-encoded 44x44 PNG template icon (terminal prompt silhouette)
ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACwAAAAsCAYAAAAehFoBAAABIElEQVR4nO2Y3Q7DIAiFZdn7v7K7qZ2zQ46AWlPPxTJTfr4xwKQU+ihm38kz8Msz2KEonE3yBubg3KB7VLirHg/MDZjb4PWocAl3+y3RVRu4t1r6y/UCYCTyoBUeAQvlQYBHwUL5JODRsGLeGvAs2Gr+5bbEcsDlGpndBjVRCL8VvjNsCAff2xwlfn8nEb/3UTtJuWdThXOAS9AMCLUDReYKU5Y1VuhQO0mqLZHyUVGidE7PUbsWLbfWngGc/uGyF9M5PUftWuSx1qBGtAxaLvNNN3AP0/nB5dBEddaFb7mhc72aUVmuZlOFtXNkmT81sHXotf7L9bAa2NKHFv8asBhRmxT0+2uEeM7YxywX0hKur0ut+dAeHgU9ujhbW1uSPlK9YkSi1oxJAAAAAElFTkSuQmCC"

def get_icon_path():
    """Create a temporary icon file from embedded base64 data"""
    icon_path = os.path.join(tempfile.gettempdir(), "claudebot_icon.png")
    if not os.path.exists(icon_path):
        with open(icon_path, 'wb') as f:
            f.write(base64.b64decode(ICON_B64))
    return icon_path

PLIST_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.johnjurkoii.claudebot</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>{bot_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TELEGRAM_TOKEN</key>
        <string>{telegram_token}</string>
        <key>ANTHROPIC_API_KEY</key>
        <string>{anthropic_key}</string>
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
        self.update_status()

    def is_bot_running(self):
        result = subprocess.run(
            ["pgrep", "-f", "bot.py"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0

    def is_ngrok_running(self):
        result = subprocess.run(
            ["pgrep", "-f", "ngrok"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0

    def is_launch_at_login_enabled(self):
        return os.path.exists(PLIST_PATH)

    def update_status(self):
        running = self.is_bot_running()
        ngrok = self.is_ngrok_running()
        status = "🟢 Running" if running else "🔴 Stopped"
        if running and ngrok:
            status = "🟢 Running + ngrok"
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
        env = self.load_env()

        if not self.is_bot_running():
            subprocess.Popen(
                ["python3", BOT_PATH],
                env=env,
                cwd="/Users/johnjurkoii/telegram-claude-bot",
                stdout=open("/tmp/claudebot.log", "w"),
                stderr=open("/tmp/claudebot.err", "w")
            )

        if not self.is_ngrok_running():
            subprocess.Popen(
                [NGROK_PATH, "http", "8080"],
                stdout=open("/tmp/ngrok.log", "w"),
                stderr=open("/tmp/ngrok.err", "w")
            )

        rumps.notification("Claude Bot", "Started", "🤖 Bot + ngrok are running!")
        self.update_status()

    def stop_bot(self, _):
        subprocess.run(["pkill", "-f", "bot.py"])
        subprocess.run(["pkill", "-f", "ngrok"])
        subprocess.run(["lsof", "-ti:8080", "|", "xargs", "kill", "-9"],
                      shell=False, capture_output=True)
        rumps.notification("Claude Bot", "Stopped", "🤖 Bot + ngrok stopped.")
        self.update_status()

    def toggle_launch_at_login(self, _):
        if self.is_launch_at_login_enabled():
            subprocess.run(["launchctl", "unload", PLIST_PATH])
            os.remove(PLIST_PATH)
            rumps.notification("Claude Bot", "Launch at Login Disabled", "Bot will not start automatically.")
        else:
            env = self.load_env()
            telegram_token = env.get("TELEGRAM_TOKEN", "")
            anthropic_key = env.get("ANTHROPIC_API_KEY", "")
            plist = PLIST_CONTENT.format(
                bot_path=BOT_PATH,
                telegram_token=telegram_token,
                anthropic_key=anthropic_key
            )
            os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
            with open(PLIST_PATH, 'w') as f:
                f.write(plist)
            subprocess.run(["launchctl", "load", PLIST_PATH])
            rumps.notification("Claude Bot", "Launch at Login Enabled", "Bot will start automatically on login.")
        self.update_status()

    @rumps.timer(10)
    def check_status(self, _):
        self.update_status()

if __name__ == "__main__":
    ClaudeBotApp().run()