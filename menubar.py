"""
Piclo Bot — macOS Menu Bar App
Manages the Telegram bot lifecycle, API keys (via Keychain), and projects folder.
"""

import rumps
import subprocess
import os
import signal
import time
import tempfile
import base64
import threading

from config import (
    load_config, save_config, get_projects_dir, set_projects_dir,
    is_first_run, pick_folder_and_save, ensure_projects_dir
)

# === CONSTANTS ===
PID_FILE = "/tmp/piclobot.pid"
KEYCHAIN_SERVICE = "Piclo Bot"
MENUBAR_PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.piclobot.plist")

# Resolve paths relative to this script's location (works inside .app bundle too)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_PATH = os.path.join(SCRIPT_DIR, "bot.py")

# Base64-encoded 44x44 PNG template icon (terminal prompt silhouette)
ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAACwAAAAsCAYAAAAehFoBAAABIElEQVR4nO2Y3Q7DIAiFZdn7v7K7qZ2zQ46AWlPPxTJTfr4xwKQU+ihm38kz8Msz2KEonE3yBubg3KB7VLirHg/MDZjb4PWocAl3+y3RVRu4t1r6y/UCYCTyoBUeAQvlQYBHwUL5JODRsGLeGvAs2Gr+5bbEcsDlGpndBjVRCL8VvjNsCAff2xwlfn8nEb/3UTtJuWdThXOAS9AMCLUDReYKU5Y1VuhQO0mqLZHyUVGidE7PUbsWLbfWngGc/uGyF9M5PUftWuSx1qBGtAxaLvNNN3AP0/nB5dBEddaFb7mhc72aUVmuZlOFtXNkmT81sHXotf7L9bAa2NKHFv8asBhRmxT0+2uEeM7YxywX0hKur0ut+dAeHgU9ujhbW1uSPlK9YkSi1oxJAAAAAElFTkSuQmCC"


# === KEYCHAIN HELPERS ===
def get_keychain_key(key_name):
    """Read a key from macOS Keychain. Returns None if not found."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key_name, "-w"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def set_keychain_key(key_name, value):
    """Store a key in macOS Keychain. Updates if it already exists."""
    subprocess.run(
        ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key_name],
        capture_output=True
    )
    result = subprocess.run(
        ["security", "add-generic-password", "-s", KEYCHAIN_SERVICE, "-a", key_name, "-w", value],
        capture_output=True, text=True
    )
    return result.returncode == 0


def mask_key(key):
    """Show first 8 and last 4 chars of a key for display."""
    if not key or len(key) < 16:
        return "****"
    return f"{key[:8]}...{key[-4:]}"


def get_icon_path():
    """Create a temporary icon file from embedded base64 data."""
    icon_path = os.path.join(tempfile.gettempdir(), "piclobot_icon.png")
    if not os.path.exists(icon_path):
        with open(icon_path, 'wb') as f:
            f.write(base64.b64decode(ICON_B64))
    return icon_path


# === SAFE PROCESS MANAGEMENT ===
# Only uses PID file — never pkill. This prevents accidentally killing other apps.

def read_pid():
    """Read the bot's PID from the PID file. Returns None if not found/stale."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # Check if process is alive (signal 0 = no-op)
            return pid
        except (OSError, ValueError):
            # Process not running or bad PID file — clean up
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
    return None


def stop_bot_process():
    """Gracefully stop the bot using its PID file. Never uses pkill."""
    pid = read_pid()
    if pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)  # Ask nicely
        # Wait up to 5 seconds for graceful shutdown
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break  # Process is gone
        else:
            # Still alive after 5s — force kill just this PID
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

        # Clean up PID file
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return True
    except OSError:
        return False


# === LAUNCH AGENT PLISTS ===
# When running as a .app bundle, just use `open` to launch it — no env vars needed
# (the app loads keys from Keychain at startup)
APP_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.piclobot.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>{app_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""

# When running as a raw script (dev mode), launch python3 + menubar.py
SCRIPT_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.piclobot.app</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{menubar_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>WorkingDirectory</key>
    <string>{working_dir}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>TELEGRAM_TOKEN</key>
        <string>{telegram_token}</string>
        <key>ANTHROPIC_API_KEY</key>
        <string>{anthropic_key}</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>"""


class PicloBotApp(rumps.App):
    def __init__(self):
        icon_path = get_icon_path()
        super(PicloBotApp, self).__init__("", icon=icon_path, template=True)

        # Store direct references to menu items that change titles
        # (rumps looks up items by title, so once title changes the old key breaks)
        self.status_item = rumps.MenuItem("Status: Checking...", callback=None)
        self.launch_login_item = rumps.MenuItem("Launch at Login: Checking...", callback=self.toggle_launch_at_login)

        # Build API Keys submenu
        self.telegram_key_item = rumps.MenuItem("Telegram Token: Checking...", callback=self.set_telegram_token)
        self.anthropic_key_item = rumps.MenuItem("Anthropic Key: Checking...", callback=self.set_anthropic_key)
        self.key_source_item = rumps.MenuItem("Source: Checking...", callback=None)
        self.migrate_keys_item = rumps.MenuItem("Migrate .zshrc Keys → Keychain", callback=self.migrate_keys_to_keychain)
        api_keys_menu = rumps.MenuItem("API Keys")
        api_keys_menu.update([
            self.telegram_key_item,
            self.anthropic_key_item,
            None,
            self.key_source_item,
            self.migrate_keys_item,
        ])

        # Projects folder item
        self.projects_folder_item = rumps.MenuItem("Projects Folder: ...", callback=self.change_projects_folder)

        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Start Bot", callback=self.start_bot),
            rumps.MenuItem("Stop Bot", callback=self.stop_bot),
            rumps.MenuItem("Restart Bot", callback=self.restart_bot),
            None,
            api_keys_menu,
            self.projects_folder_item,
            self.launch_login_item,
            None,
        ]

        # First-run: show folder picker before starting anything
        projects_dir = ensure_projects_dir()
        if not projects_dir:
            rumps.notification("Piclo Bot", "No Folder Selected",
                             "Please select a projects folder to use Piclo Bot.")

        # Track whether bot was running so we can detect unexpected crashes
        self._bot_was_running = False
        self._user_stopped = False  # True when user clicked Stop

        # Auto-start bot if not already running
        if not self.is_bot_running() and self._has_required_keys():
            self.start_bot(None)
        self.update_status()
        self.update_key_display()
        self.update_folder_display()

    def _has_required_keys(self):
        """Check if both API keys are available."""
        env = self.load_env()
        return bool(env.get("TELEGRAM_TOKEN")) and bool(env.get("ANTHROPIC_API_KEY"))

    def _find_source_dir(self):
        """Find the original source directory (where bot.py + .venv live).

        When running as a raw script, it's SCRIPT_DIR.
        When running inside a .app bundle, we need to find the actual project
        folder because py2app bundles don't include the venv.
        """
        # Dev mode — source is right here
        if os.path.exists(os.path.join(SCRIPT_DIR, "bot.py")) and os.path.exists(os.path.join(SCRIPT_DIR, ".venv")):
            return SCRIPT_DIR

        # Inside .app bundle — search for the source project
        projects_dir = get_projects_dir()
        if projects_dir:
            for folder_name in ["telegram-claude-bot", "piclobot", "piclo-bot"]:
                candidate = os.path.join(projects_dir, folder_name)
                if os.path.exists(os.path.join(candidate, "bot.py")):
                    return candidate

        # Last resort: check common locations
        home = os.path.expanduser("~")
        for base in [os.path.join(home, "Documents", "Claude Projects"),
                     os.path.join(home, "Documents"),
                     home]:
            for folder_name in ["telegram-claude-bot", "piclobot", "piclo-bot"]:
                candidate = os.path.join(base, folder_name)
                if os.path.exists(os.path.join(candidate, "bot.py")):
                    return candidate

        return SCRIPT_DIR  # fallback

    def _find_python3(self):
        """Find the best python3 — venv in source dir first, then Homebrew, then system."""
        source_dir = self._find_source_dir()
        # Check for venv in the source project directory
        venv_python = os.path.join(source_dir, ".venv", "bin", "python3")
        if os.path.exists(venv_python):
            return venv_python
        # Homebrew
        for p in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]:
            if os.path.exists(p):
                return p
        # Fallback
        return "python3"

    def is_bot_running(self):
        return read_pid() is not None

    def is_launch_at_login_enabled(self):
        return os.path.exists(MENUBAR_PLIST_PATH)

    def update_status(self):
        running = self.is_bot_running()
        status = "🟢 Running" if running else "🔴 Stopped"
        self.status_item.title = f"Status: {status}"
        launch = self.is_launch_at_login_enabled()
        self.launch_login_item.title = f"Launch at Login: {'✅ On' if launch else '❌ Off'}"

        # Watchdog: detect unexpected crashes
        if self._bot_was_running and not running and not self._user_stopped:
            rumps.notification("Piclo Bot", "Bot Crashed",
                             "⚠️ The bot stopped unexpectedly. Click Start Bot to restart.")
        self._bot_was_running = running
        self._user_stopped = False

    def update_key_display(self):
        """Update the API Keys submenu with current key status."""
        telegram = get_keychain_key("TELEGRAM_TOKEN")
        anthropic = get_keychain_key("ANTHROPIC_API_KEY")
        has_keychain = bool(telegram or anthropic)

        if telegram:
            self.telegram_key_item.title = f"Telegram Token: {mask_key(telegram)}"
        else:
            self.telegram_key_item.title = "Telegram Token: ⚠️ Not Set — Click to Add"

        if anthropic:
            self.anthropic_key_item.title = f"Anthropic Key: {mask_key(anthropic)}"
        else:
            self.anthropic_key_item.title = "Anthropic Key: ⚠️ Not Set — Click to Add"

        if has_keychain:
            self.key_source_item.title = "Source: 🔒 macOS Keychain"
            self.migrate_keys_item.title = "✅ Keys in Keychain"
        else:
            self.key_source_item.title = "Source: ~/.zshrc (less secure)"
            self.migrate_keys_item.title = "Migrate .zshrc Keys → Keychain"

    def update_folder_display(self):
        """Update the projects folder menu item."""
        folder = get_projects_dir()
        if folder:
            # Show just the last two path components for readability
            short = os.path.join("~", os.path.relpath(folder, os.path.expanduser("~")))
            self.projects_folder_item.title = f"Projects: {short} (click to change)"
        else:
            self.projects_folder_item.title = "Projects Folder: ⚠️ Not Set — Click to Choose"

    # === API KEY MANAGEMENT ===

    def set_telegram_token(self, _):
        current = get_keychain_key("TELEGRAM_TOKEN") or ""
        window = rumps.Window(
            message="Enter your Telegram Bot Token (from @BotFather):",
            title="Telegram Token",
            default_text=current,
            ok="Save to Keychain",
            cancel="Cancel",
            dimensions=(380, 24)
        )
        response = window.run()
        if response.clicked and response.text.strip():
            if set_keychain_key("TELEGRAM_TOKEN", response.text.strip()):
                rumps.notification("Piclo Bot", "Token Saved", "🔒 Telegram token stored in Keychain.")
                self.update_key_display()
            else:
                rumps.notification("Piclo Bot", "Error", "Failed to save token to Keychain.")

    def set_anthropic_key(self, _):
        current = get_keychain_key("ANTHROPIC_API_KEY") or ""
        window = rumps.Window(
            message="Enter your Anthropic API Key (from console.anthropic.com):",
            title="Anthropic API Key",
            default_text=current,
            ok="Save to Keychain",
            cancel="Cancel",
            dimensions=(380, 24)
        )
        response = window.run()
        if response.clicked and response.text.strip():
            if set_keychain_key("ANTHROPIC_API_KEY", response.text.strip()):
                rumps.notification("Piclo Bot", "Key Saved", "🔒 Anthropic key stored in Keychain.")
                self.update_key_display()
            else:
                rumps.notification("Piclo Bot", "Error", "Failed to save key to Keychain.")

    def migrate_keys_to_keychain(self, _):
        env = self._load_env_from_zshrc()
        telegram = env.get("TELEGRAM_TOKEN", "")
        anthropic = env.get("ANTHROPIC_API_KEY", "")
        migrated = 0
        if telegram and not get_keychain_key("TELEGRAM_TOKEN"):
            set_keychain_key("TELEGRAM_TOKEN", telegram)
            migrated += 1
        if anthropic and not get_keychain_key("ANTHROPIC_API_KEY"):
            set_keychain_key("ANTHROPIC_API_KEY", anthropic)
            migrated += 1
        if migrated:
            rumps.notification("Piclo Bot", "Keys Migrated",
                             f"🔒 {migrated} key(s) moved to Keychain. You can remove them from ~/.zshrc.")
        else:
            rumps.notification("Piclo Bot", "Nothing to Migrate",
                             "Keys already in Keychain, or none found in .zshrc.")
        self.update_key_display()

    # === PROJECTS FOLDER ===

    def change_projects_folder(self, _):
        """Let user pick a new projects folder."""
        path = pick_folder_and_save("Choose your projects folder:")
        if path:
            rumps.notification("Piclo Bot", "Folder Updated", f"Projects folder: {path}")
            self.update_folder_display()
            # Restart bot so it picks up the new folder
            if self.is_bot_running():
                self.restart_bot(None)

    # === BOT LIFECYCLE (safe — PID only, no pkill) ===

    def start_bot(self, _):
        if self.is_bot_running():
            self.update_status()
            return

        if not self._has_required_keys():
            rumps.notification("Piclo Bot", "Missing API Keys",
                             "Set your Telegram Token and Anthropic Key in the API Keys menu.")
            return

        env = self.load_env()
        python3 = self._find_python3()
        source_dir = self._find_source_dir()
        bot_path = os.path.join(source_dir, "bot.py")

        if not os.path.exists(bot_path):
            rumps.notification("Piclo Bot", "Error",
                             f"bot.py not found in {source_dir}")
            return

        def _start():
            try:
                subprocess.Popen(
                    [python3, bot_path],
                    env=env,
                    cwd=source_dir,
                    stdout=open("/tmp/piclobot.log", "w"),
                    stderr=open("/tmp/piclobot.err", "w")
                )
                time.sleep(2)
                if read_pid():
                    os.system('afplay /System/Library/Sounds/Glass.aiff &')
                    rumps.notification("Piclo Bot", "Started", "🤖 Bot is running!")
                else:
                    rumps.notification("Piclo Bot", "Warning",
                                     "Bot may have failed. Check /tmp/piclobot.err")
            except Exception as e:
                rumps.notification("Piclo Bot", "Failed to Start", str(e)[:100])
            self.update_status()

        threading.Thread(target=_start, daemon=True).start()

    def stop_bot(self, _):
        self._user_stopped = True

        def _stop():
            stop_bot_process()
            time.sleep(1)
            os.system('afplay /System/Library/Sounds/Basso.aiff &')
            rumps.notification("Piclo Bot", "Stopped", "🤖 Bot has been stopped.")
            self.update_status()

        threading.Thread(target=_stop, daemon=True).start()

    def restart_bot(self, _):
        def _restart():
            stop_bot_process()
            time.sleep(2)
            self.update_status()
            self.start_bot(None)

        self._user_stopped = True  # suppress crash alert during restart
        threading.Thread(target=_restart, daemon=True).start()

    # === ENV LOADING ===

    def _load_env_from_zshrc(self):
        """Load env vars from .zshrc (legacy fallback)."""
        try:
            result = subprocess.run(
                'source ~/.zshrc && echo "TELEGRAM_TOKEN=$TELEGRAM_TOKEN" && echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"',
                shell=True, capture_output=True, text=True, executable='/bin/zsh', timeout=10
            )
            parsed = {}
            for line in result.stdout.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    if value:
                        parsed[key] = value
            return parsed
        except Exception:
            return {}

    def load_env(self):
        """Load API keys — Keychain first, .zshrc fallback.

        IMPORTANT: When running inside a py2app .app bundle, the environment
        contains PYTHONPATH/PYTHONHOME/RESOURCEPATH etc. that point into the
        bundle. If we pass those to the bot subprocess, it loads broken bundled
        libs instead of the venv's packages. So we strip all py2app pollution.
        """
        env = os.environ.copy()

        # Strip py2app environment variables that would corrupt the subprocess
        for key in ["PYTHONPATH", "PYTHONHOME", "RESOURCEPATH",
                    "EXECUTABLEPATH", "ARGVZERO", "__PYVENV_LAUNCHER__"]:
            env.pop(key, None)

        # Set clean PATH with Homebrew
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

        telegram = get_keychain_key("TELEGRAM_TOKEN")
        anthropic = get_keychain_key("ANTHROPIC_API_KEY")

        if not telegram or not anthropic:
            zshrc_env = self._load_env_from_zshrc()
            if not telegram:
                telegram = zshrc_env.get("TELEGRAM_TOKEN", "")
            if not anthropic:
                anthropic = zshrc_env.get("ANTHROPIC_API_KEY", "")

        env["TELEGRAM_TOKEN"] = telegram or ""
        env["ANTHROPIC_API_KEY"] = anthropic or ""
        return env

    # === LAUNCH AT LOGIN ===

    def _get_app_path(self):
        """Find the .app bundle path if running inside one, else None."""
        # py2app bundles: __file__ is inside Foo.app/Contents/Resources/
        path = os.path.abspath(__file__)
        while path != '/':
            if path.endswith('.app'):
                return path
            path = os.path.dirname(path)
        return None

    def toggle_launch_at_login(self, _):
        if self.is_launch_at_login_enabled():
            subprocess.run(["launchctl", "unload", MENUBAR_PLIST_PATH], capture_output=True)
            try:
                os.remove(MENUBAR_PLIST_PATH)
            except OSError:
                pass
            rumps.notification("Piclo Bot", "Launch at Login Disabled",
                             "Menu bar app won't auto-start on login.")
        else:
            app_path = self._get_app_path()

            if app_path:
                # Running inside .app bundle — launch the .app itself via open
                plist = APP_PLIST_TEMPLATE.format(app_path=app_path)
            else:
                # Running as raw script — launch python3 + menubar.py
                env = self.load_env()
                python_path = self._find_python3()
                plist = SCRIPT_PLIST_TEMPLATE.format(
                    python_path=python_path,
                    menubar_path=os.path.abspath(__file__),
                    working_dir=SCRIPT_DIR,
                    telegram_token=env.get("TELEGRAM_TOKEN", ""),
                    anthropic_key=env.get("ANTHROPIC_API_KEY", ""),
                )

            os.makedirs(os.path.dirname(MENUBAR_PLIST_PATH), exist_ok=True)
            with open(MENUBAR_PLIST_PATH, 'w') as f:
                f.write(plist)
            subprocess.run(["launchctl", "load", MENUBAR_PLIST_PATH], capture_output=True)
            rumps.notification("Piclo Bot", "Launch at Login Enabled",
                             "Piclo Bot will start automatically on login.")
        self.update_status()

    @rumps.timer(10)
    def check_status(self, _):
        self.update_status()


if __name__ == "__main__":
    PicloBotApp().run()
