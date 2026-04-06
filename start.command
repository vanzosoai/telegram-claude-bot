#!/bin/bash
# Start Claude Bot Menu Bar App
# Double-click this file to launch — Terminal window will close automatically

cd ~/telegram-claude-bot

# Kill any existing instances first
pkill -f "telegram-claude-bot/bot.py" 2>/dev/null
pkill -f "telegram-claude-bot/menubar.py" 2>/dev/null
sleep 2

# Source environment
source ~/.zshrc 2>/dev/null

# Start menubar app (it auto-starts the bot)
nohup python3 menubar.py > /tmp/menubar.log 2>&1 &

echo "Menu bar app launched! Closing this window..."
sleep 1

# Close this Terminal window
osascript -e 'tell application "Terminal" to close front window' &
exit 0
