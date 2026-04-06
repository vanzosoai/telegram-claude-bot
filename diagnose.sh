#!/bin/bash
# Piclo Bot diagnostic + clean restart script
echo "=== STEP 1: Kill ALL old bot processes ==="
# Find any python running bot.py
PIDS=$(ps aux | grep "[b]ot.py" | awk '{print $2}')
if [ -n "$PIDS" ]; then
    echo "Found bot processes: $PIDS"
    echo "$PIDS" | xargs kill -9 2>/dev/null
    echo "Killed them all."
else
    echo "No bot.py processes found."
fi

# Also kill any menubar.py processes not from .app
MENU_PIDS=$(ps aux | grep "[m]enubar.py" | grep -v ".app" | awk '{print $2}')
if [ -n "$MENU_PIDS" ]; then
    echo "Found stale menubar processes: $MENU_PIDS"
    echo "$MENU_PIDS" | xargs kill -9 2>/dev/null
    echo "Killed them."
fi

echo ""
echo "=== STEP 2: Clean up PID file ==="
rm -f /tmp/piclobot.pid
echo "Removed /tmp/piclobot.pid"

echo ""
echo "=== STEP 3: Check error log from last attempt ==="
if [ -f /tmp/piclobot.err ]; then
    echo "--- /tmp/piclobot.err ---"
    cat /tmp/piclobot.err
    echo "--- end ---"
else
    echo "No error log found at /tmp/piclobot.err"
fi

echo ""
echo "=== STEP 4: Check stdout log ==="
if [ -f /tmp/piclobot.log ]; then
    echo "--- /tmp/piclobot.log (last 20 lines) ---"
    tail -20 /tmp/piclobot.log
    echo "--- end ---"
else
    echo "No log found at /tmp/piclobot.log"
fi

echo ""
echo "=== STEP 5: Test bot.py can actually start ==="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python3"
if [ -f "$VENV_PYTHON" ]; then
    echo "Venv python: $VENV_PYTHON"
    echo "Testing import..."
    "$VENV_PYTHON" -c "import anthropic; import telegram; from config import get_projects_dir; print('Projects dir:', get_projects_dir()); print('All imports OK')" 2>&1
else
    echo "ERROR: No venv python at $VENV_PYTHON"
    echo "Available pythons:"
    which -a python3
fi

echo ""
echo "=== STEP 6: Verify app source dir resolution ==="
echo "Script dir: $SCRIPT_DIR"
echo "Bot.py exists: $([ -f "$SCRIPT_DIR/bot.py" ] && echo YES || echo NO)"
echo "Venv exists: $([ -d "$SCRIPT_DIR/.venv" ] && echo YES || echo NO)"

echo ""
echo "=== DONE ==="
echo "Copy all this output and paste it back to Claude."
