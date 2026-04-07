# Project: telegram-claude-bot

## Summary
A Telegram bot that acts as a remote coding agent for macOS. Control your Mac from your phone — write code, run commands, serve web apps, take screenshots, manage projects, and more via Claude (Anthropic's AI). Includes a native macOS menu bar app for lifecycle management.

## Status: In Progress
## Last touched: 2026-04-07 03:55:02 UTC by Piclo Bot (Telegram)

### Features
- Telegram ID whitelist — only authorized users can control the bot
- Activity logging — all actions logged to /tmp/piclobot_activity.log
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
- macOS .app bundle — "Piclo Bot.app" in /Applications, Launchpad, Spotlight, Dock
- API Keys in menu bar — Keychain-backed key management UI (set, update, view masked keys)
- Keychain-first key loading — tries macOS Keychain before falling back to .zshrc
- One-click key migration — "Migrate .zshrc Keys → Keychain" button in menu bar
- Restart Bot menu item — stop + start in one click
- User-selectable projects folder — native macOS folder picker on first launch
- Change projects folder from menu bar — updates config and restarts bot
- Config persistence — ~/Library/Application Support/Piclo Bot/config.json
- Safe process management — PID file only, no pkill (prevents killing other apps)
- py2app self-contained .app — bundles Python + all deps, no system Python needed
- pywhispercpp transcription — same Whisper accuracy, ~50MB vs 1.5GB (no PyTorch)
- Whisper fallback chain — pywhispercpp → openai-whisper → whisper CLI
- Daily standup — morning project summaries (needs STANDUP_CHAT_ID)
- Build watcher — monitors background tasks, notifies on completion
- Shared projects folder — ~/Documents/Claude Projects/ used by both Cowork and bot
- First-run project migration — auto-copies legacy ~/Projects on startup
- HANDOFF.md protocol — automatic context handoff between Cowork and Piclo Bot
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
- 2026-04-07 03:55:02 UTC (Piclo Bot) — "Yup check it out" [tools: run_command, read_file, run_command]
- 2026-04-07 03:54:05 UTC (Piclo Bot) — "Oh list all projects in my Claude projects folder" [tools: run_command]
- 2026-04-07 03:53:37 UTC (Piclo Bot) — "List all project folders in my documents folder" [tools: run_command]
- 2026-04-07 03:51:18 UTC (Piclo Bot) — "Report all projects you can access" [tools: list_projects]
- 2026-04-07 00:19:42 UTC (Piclo Bot) — "No, I think this is best so that way you can see that the message is being generated and read it ins" → Oh that's actually brilliant! 🎯 Smart flow:
- 2026-04-07 00:18:36 UTC (Piclo Bot) — "Hey, you're still getting my voice messages." → Yep, I'm still receiving them! 🎤 But I notice I'm not sending voice replies back like we set up.
- 2026-04-06 23:44:46 UTC (Piclo Bot) — "I just uploaded a photo to /Users/johnjurkoii/Desktop/BotUploads/photo_1775519082.jpg. Save this to" [tools: run_command]
- 2026-04-06 23:42:50 UTC (Piclo Bot) — "Can you take those screenshots one more time?" [tools: screenshot]
- 2026-04-06 23:40:52 UTC (Piclo Bot) — "Hey can I get a screenshot of my desktop?" [tools: screenshot]
- 2026-04-06 23:20:58 UTC (Piclo Bot) — "Tell me the top ten best writers of all time" → Ha! 🤔 That's a solid question, but it's not really my lane — I'm here to help you build stuff on your Mac, not debate literature!
- 2026-04-06 23:02:00 UTC (Piclo Bot) — "Hey, what's the weather indicator like right now?" → I don't have a weather tool available — I can't check real-time weather data. 🌤️
- 2026-04-06 22:57:39 UTC (Piclo Bot) — "How’s my Mac’s health" [tools: system_health]
- 2026-04-06 22:33:49 UTC (Piclo Bot) — "You can see my texts too right" → Yep! 🎯 I can see your texts in the `.bot_memory.json` file — it shows me the full conversation history including:
- 2026-04-06 22:32:55 UTC (Piclo Bot) — "No, I sent a voice message but maybe you're reading my transcription and considering that a text, I'" → Ah, that's a good point! 🤔 You're right — I might be treating the transcribed text from your voice message the same as regular text input.
- 2026-04-06 22:32:31 UTC (Piclo Bot) — "So why didn't you send a voice message at that time?" → Good catch! 🤔 You sent a **text message** ("Great, only send voice messages..."), so I followed the rule and responded with text only.
- 2026-04-06 22:31:48 UTC (Piclo Bot) — "It's safe." [tools: read_file, read_file, run_command, run_command]
- 2026-04-06 22:31:30 UTC (Piclo Bot) — "You should now see that you have access to the TTS so that you can send me voice messages back. Conf" → Let me check the bot files to confirm the TTS setup...
- 2026-04-06 19:57:22 UTC (Piclo Bot) — "Maybe I need to restart you first for that code to take effect?" → Oh yeah, that makes sense! 🎯
- 2026-04-06 19:55:45 UTC (Piclo Bot) — "I ran out of usage for cowork so it’s not doing anything at the moment. You can make the update and" [tools: read_file, read_file, run_command, run_command, run_command]
- 2026-04-06 19:55:43 UTC (Piclo Bot) — Changed kill code from 'killclaudenow' to 'killpiclonow'
- 2026-04-06 19:54:44 UTC (Piclo Bot) — "Can we change it to killpiclonow" → Got it! But hold up — Cowork is actively working in those bot files right now, and you said not to change anything.
- 2026-04-06 19:48:11 UTC (Piclo Bot) — "Cool and you do still have a kill code" [tools: run_command]
- 2026-04-06 19:47:51 UTC (Piclo Bot) — "Awesome, so you're set to check that anytime we start a new session where you get closed down and re" → Exactly! 🎯
- 2026-04-06 19:47:25 UTC (Piclo Bot) — "You should have some persistent memory with a file that we created, are you seeing that file?" [tools: run_command, read_file]
- 2026-04-06 19:37:30 UTC (Piclo Bot) — User tested voice interaction + handoff from Telegram. Bot didn't have TTS yet, discussed adding it.
- 2026-04-06 18:30:00 UTC (Cowork) — Full rebrand from "Claude Bot" to "Piclo Bot": renamed all identifiers across bot.py, menubar.py, config.py, setup.py, build_app.sh, start.command, README.md, TODO.md, PRODUCT_ROADMAP.md, HANDOFF.md. Generated custom 1024x1024 purple robot icon (icon_piclo.png + icon_piclo.svg). Built icon.icns for macOS app. Rebuilt py2app bundle as "Piclo Bot.app" with new icon and branding. Installed Python 3.14 via Homebrew, created .venv, resolved all dependency issues. Domain piclobot.com identified as available.
- 2026-04-06 17:45:00 UTC (Cowork) — Major app architecture overhaul: (1) Created config.py for persistent settings with ~/Library/Application Support/Piclo Bot/config.json. (2) Rewrote menubar.py: user-selectable projects folder via native macOS picker on first launch, change folder from menu bar, safe PID-only process management (removed all pkill — previous version crashed other apps). (3) Updated bot.py: PROJECTS_DIR reads from config, ALLOWED_PATHS uses BOT_OWN_DIR dynamically, whisper transcription now tries pywhispercpp first (50MB, no PyTorch) with openai-whisper and CLI fallbacks. (4) Created setup.py for py2app self-contained .app build. (5) Rewrote build_app.sh to use py2app instead of manual .app shell wrapper.
- 2026-04-06 17:10:00 UTC (Cowork) — Built macOS .app bundle (build_app.sh → /Applications/Piclo Bot.app). Added Keychain-backed API Keys menu to menubar: set/update/migrate keys, masked display, Keychain-first loading with .zshrc fallback. Added Restart Bot menu item. Updated PRODUCT_ROADMAP.md with .app status.
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
- Package self-contained .app via py2app (Phase 2 — current .app requires Python installed)
- Create landing page for product launch
- See PRODUCT_ROADMAP.md for full go-to-market plan

### Key Files
- bot.py — Main Telegram bot (~1300 lines). All tools, handlers, commands, and Claude integration.
- menubar.py — macOS menu bar app. Start/stop/restart bot, API key management, folder picker, launch at login.
- config.py — App configuration (projects folder, first-run detection). Stores in ~/Library/Application Support/Piclo Bot/.
- setup.py — py2app build configuration. Bundles everything into a self-contained .app.
- build_app.sh — Builds dist/Piclo Bot.app via py2app. Run once, then copy to /Applications.
- start.command — Double-click launcher (legacy). Starts menubar, closes Terminal window.
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
- API keys: Keychain preferred, .zshrc fallback. Use "Migrate .zshrc Keys → Keychain" in menu bar to move them.
- macOS Screen Recording permission must be granted manually for screenshots
- ngrok free tier: only one tunnel at a time, URLs change on restart
- Whisper requires: pip3 install openai-whisper (+ ffmpeg via homebrew)
- python-telegram-bot[job-queue] needed for scheduled tasks
- The .bot_memory.json format changed (now includes token_usage) — backwards compatible with old flat format
- [MIGRATED] Bot paths updated from ~/telegram-claude-bot to ~/Documents/Claude Projects/telegram-claude-bot
- [FIXED] First .app attempt used pkill with broad pattern — crashed other apps. Replaced with PID-only process management.
- [FIXED] .app couldn't access ~/Documents due to macOS sandbox. Rebuilt as self-contained py2app bundle.
- PROJECTS_DIR is now dynamic (from config.json), not hardcoded. User picks folder on first launch.
