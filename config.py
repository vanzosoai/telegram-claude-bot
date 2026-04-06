"""
Piclo Bot — App Configuration
Manages settings stored in ~/Library/Application Support/Piclo Bot/config.json
Handles first-run detection, folder picker, and persistent preferences.
"""

import os
import json
import subprocess

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/Piclo Bot")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG = {
    "projects_dir": "",  # Empty = first run, needs folder picker
    "first_run_complete": False,
}


def load_config():
    """Load config from disk, or return defaults if not found."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                saved = json.load(f)
            # Merge with defaults so new keys get added on upgrades
            config = DEFAULT_CONFIG.copy()
            config.update(saved)
            return config
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Write config to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def get_projects_dir():
    """Get the configured projects directory. Returns empty string if not set."""
    config = load_config()
    return config.get("projects_dir", "")


def set_projects_dir(path):
    """Set the projects directory and mark first run as complete."""
    config = load_config()
    config["projects_dir"] = path
    config["first_run_complete"] = True
    save_config(config)


def is_first_run():
    """Check if this is the first time the app is running."""
    config = load_config()
    return not config.get("first_run_complete", False)


def pick_folder(prompt="Choose your projects folder:"):
    """Show a native macOS folder picker dialog. Returns path or None if cancelled."""
    script = f'''
    set chosenFolder to choose folder with prompt "{prompt}" default location (path to documents folder)
    return POSIX path of chosenFolder
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and result.stdout.strip():
            path = result.stdout.strip().rstrip("/")
            return path
    except (subprocess.TimeoutExpired, Exception):
        pass
    return None


def pick_folder_and_save(prompt="Choose your projects folder:"):
    """Show folder picker and save the result. Returns path or None."""
    path = pick_folder(prompt)
    if path:
        set_projects_dir(path)
    return path


def ensure_projects_dir():
    """Ensure a projects directory is configured. Shows picker if not.
    Returns the path, or None if user cancelled."""
    projects_dir = get_projects_dir()
    if projects_dir and os.path.isdir(projects_dir):
        return projects_dir

    # Need to pick a folder
    return pick_folder_and_save(
        "Welcome to Piclo Bot! Choose a folder for your projects:"
    )
