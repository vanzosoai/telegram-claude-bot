# Project: telegram-claude-bot

## Summary
A Telegram bot that acts as a remote coding agent for macOS. Control your Mac from your phone — write code, run commands, serve web apps, take screenshots, manage projects, and more via Claude (Anthropic's AI). Includes a native macOS menu bar app for lifecycle management.

## Status: In Progress
## Last touched: 2026-04-06 16:57:33 UTC by Cowork (Desktop)

### Features
- Telegram ID whitelist — only authorized users can control the bot
- Activity logging — all actions logged to /tmp/claudebot_activity.log
- Rate limiting — 30 calls per 5 minutes per user
- Emergency kill code — send "killclaudenow" to nuke everything
- Path restrictions — bot can only access ~/Projects, ~/Desktop, ~/Documents/Claude Projects, /tmp
- Command blocklist — blocks rm -rf /, sudo, format, mkfs, etc.
- PID file lock — prevents multiple bot instances (409 conflict fix)
- Smart model routing — Haiku for quick tasks, Sonnet for complex ones
- Persistent memory — .bot_memory.json survives restarts (conversations + token stats)
- Multi-step tool use — up to 6 iterations per message for complex tasks
- Conversation history — per-user context, last 20 messages persisted
- Token usage tracking — per-model input/output tokens, cost estimation, monthly projection
- run_command tool — execute shell commands with safety checks
- read_file tool — read files from allowed paths
- write_file tool — create/edit files in allowed paths
- list_projects tool — show projects with git branch, dirty status, and handoff status
- screenshot tool — capture screen (dual display, JPEG compressed for Telegram)
- run_background tool — start long tasks with completion notifications
- send_file tool — send any file from Mac to Telegram
- system_health tool — CPU, memory, disk, battery, top processes
- serve_project tool — one-step local server + ngrok tunnel with retry for public URLs
- Text message handling — standard chat with Claude
- Voice message handling — Whisper transcription → Claude processing
- Photo upload handling — save to ~/Desktop with optional caption as command
- File/document upload handling — save to ~/Desktop with optional caption as command
- Natural language command shortcuts — voice phrases route to commands without API calls
- /start command — welcome message and onboarding
- /help command — list all commands with descriptions
- /status command — bot health: uptime, PID, memory, recent activity
- /cost command — token usage breakdown, cost estimate, monthly projection
- /clear command — reset conversation history
- /projects command — quick shortcut to list_projects tool
- /screenshot command — quick shortcut to screenshot tool
- /health command — quick shortcut to system_health tool
- Custom menu bar icon — native template icon (adapts to dark/light mode)
- Start/Stop bot from menu bar — PID-based process management
- Auto-start bot on launch — menubar starts bot automatically
- Launch at Login — LaunchAgent for menubar (manages bot lifecycle)
- Status indicator — polls every 10s, shows Running/Stopped
- Terminal-free operation — start.command closes Terminal, runs in background
- Daily standup — morning project summaries (needs STANDUP_CHAT_ID)
- Build watcher — monitors background tasks, notifies on completion
- Shared projects folder — ~/Documents/Claude Projects/ used by both Cowork and bot
- First-run project migration — auto-copies legacy ~/Projects on startup
- HANDOFF.md protocol — automatic context handoff between Cowork and Claude Bot
- Skip-if-same-agent — don't re-read HANDOFF.md if you were the last to edit

### Known Bugs & Issues
- [FIXED] 409 Conflict errors — multiple bot instances fighting over Telegram polling. Fixed with PID file lock.
- [FIXED] Voice message "Message.text can't be set" — Telegram Message objects are read-only. Fixed with text_override parameter.
- [FIXED] Whisper too slow — "base" model hanging. Switched to "tiny" model.
- [FIXED] Model not found (claude-sonnet-4-5-20250514) — deprecated model name. Updated to claude-sonnet-4-6.
- [FIXED] ERR_NGROK_3004 — ngrok tunnel not running. Fixed with serve_project tool that auto-starts ngrok with retry.
- [FIXED] KeepAlive=true causing respawn on quit — menubar LaunchAgent. Changed to KeepAlive=false.
- [FIXED] Screenshots showing empty desktop — macOS Screen Recording permission not granted. User must grant in System Settings.
- [FIXED] Screenshot too large for Telegram — retina display produces huge PNGs. Fixed with sips resize + JPEG compression.
- [FIXED] Background task notifications not sending — silent except:pass swallowing errors. Added proper error logging.
- [FIXED] Menu bar icon not updating — OS-level icon cache. Required Mac restart to clear.
- [FIXED] Menubar stop_bot broken pipe — subprocess passing pipe as literal arg. Fixed with shell=True.
- [FIXED] JobQueue not available — missing python-telegram-bot[job-queue]. Added check with warning.

### Work Log
- 2026-04-06 16:57:33 UTC (Cowork) — Migration prep: updated all hardcoded paths in bot.py, menubar.py, and start.command from ~/telegram-claude-bot to ~/Documents/Claude Projects/telegram-claude-bot. Ready for Finder move.
- 2026-04-06 14:00:47 UTC (Cowork) — Created HANDOFF.md. Added handoff skill with auto-create/update behavior, UTC timestamps (HH:MM:SS), skip-if-same-agent optimization. Updated bot system prompt, Cowork instructions, and skill to all use identical handoff protocol.
- 2026-04-06 ~13:00 UTC (Cowork) — Added /start, /help, /status, /cost, /clear, /projects, /screenshot, /health commands. Added token usage tracking with per-model cost estimation. Added natural language shortcuts for voice commands. Persistent token stats across restarts.
- 2026-04-06 ~12:00 UTC (Cowork) — Created shared projects folder system (~/Documents/Claude Projects/). Added first-run migration from ~/Projects. Created .claude/instructions.md for Cowork integration. Added HANDOFF.md protocol to bot system prompt.
- 2026-04-06 ~11:00 UTC (Cowork) — Added serve_project tool (one-step server + ngrok with retry). Updated model from deprecated claude-sonnet-4-5-20250514 to claude-sonnet-4-6. Menubar now auto-starts bot. start.command auto-closes Terminal. LaunchAgent manages menubar app.
- 2026-04-06 ~10:00 UTC (Cowork) — Major feature buildout: security (whitelist, logging, rate limiting, kill code, path restrictions, command blocklist, PID lock), intelligence (smart routing, persistent memory, multi-step tools), tools (all 9), input handlers (voice, photo, file), menu bar app, scheduled tasks.

### Next Steps
- Dogfood the bot daily for 1 week, keep a friction log
- Build interactive setup.sh installer script
- Add auto-recovery watchdog (restart bot if it crashes)
- Add inline keyboards for confirmations (Yes/No buttons)
- Add live progress streaming (edit message in-place)
- Package as .app bundle via py2app or PyInstaller
- Create landing page for product launch
- See PRODUCT_ROADMAP.md for full go-to-market plan

### Key Files
- bot.py — Main Telegram bot (~1300 lines). All tools, handlers, commands, and Claude integration.
- menubar.py — macOS menu bar app. Start/stop bot, launch at login, status indicator.
- start.command — Double-click launcher. Starts menubar, closes Terminal window.
- .bot_memory.json — Persistent memory: conversations + token usage (gitignored)
- skills/handoff/SKILL.md — Cowork skill for project migration and handoff docs
- README.md — Feature tracker with all 41+ implemented features
- PRODUCT_ROADMAP.md — Competitive analysis, pricing research, go-to-market plan
- TODO.md — Original feature checklist

### Tech Stack
- Python 3 (python-telegram-bot, anthropic SDK, rumps)
- Anthropic Claude API (Haiku 4.5 + Sonnet 4.6)
- OpenAI Whisper (tiny model) for voice transcription
- ngrok for public URL tunneling
- macOS native: screencapture, sips, LaunchAgents, rumps menu bar
- ffmpeg for audio conversion

### Gotchas
- Requires TELEGRAM_TOKEN and ANTHROPIC_API_KEY in ~/.zshrc
- macOS Screen Recording permission must be granted manually for screenshots
- ngrok free tier: only one tunnel at a time, URLs change on restart
- Whisper requires: pip3 install openai-whisper (+ ffmpeg via homebrew)
- python-telegram-bot[job-queue] needed for scheduled tasks
- The .bot_memory.json format changed (now includes token_usage) — backwards compatible with old flat format
- [MIGRATED] Bot paths updated from ~/telegram-claude-bot to ~/Documents/Claude Projects/telegram-claude-bot
- After moving the folder, toggle Launch at Login off/on in the menu bar to regenerate the LaunchAgent plist
