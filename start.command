#!/bin/bash
# Start Claude Bot and Menu Bar
# Double-click this file to launch everything

cd ~/telegram-claude-bot

# Kill any existing instances first
pkill -f "telegram-claude-bot/bot.py" 2>/dev/null
pkill -f "telegram-claude-bot/menubar.py" 2>/dev/null
sleep 2

# Source environment
source ~/.zshrc 2>/dev/null

# Start bot in background
nohup python3 bot.py > /tmp/claudebot.err 2>&1 &
echo "Bot started (PID $!)"

# Start menubar in background
nohup python3 menubar.py > /tmp/menubar.log 2>&1 &
echo "Menubar started (PID $!)"

echo "All running! You can close this window."
