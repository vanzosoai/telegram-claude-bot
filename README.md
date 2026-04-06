# Claude Bot — Remote Coding Agent for Telegram

Control your Mac's coding environment from your phone via Telegram. Claude (Anthropic's AI) acts as your hands — writing code, running commands, serving apps, taking screenshots, and more.

## Architecture

```
Phone (Telegram) → Bot (bot.py) → Claude API → Your Mac
                   Menu Bar (menubar.py) — Start/Stop/Launch at Login
```

- **bot.py** — Main Telegram bot, polls for messages, routes to Claude with tools
- **menubar.py** — macOS menu bar app, manages bot lifecycle
- **start.command** — Double-click launcher, closes Terminal after starting

## Setup

1. Set environment variables in `~/.zshrc`:
   ```
   export TELEGRAM_TOKEN="your-telegram-bot-token"
   export ANTHROPIC_API_KEY="your-anthropic-key"
   ```
2. Install dependencies:
   ```
   pip3 install python-telegram-bot anthropic rumps openai-whisper
   pip3 install python-telegram-bot[job-queue]  # for scheduled tasks
   ```
3. Install ngrok and authenticate: `ngrok config add-authtoken YOUR_TOKEN`
4. Double-click `start.command` — menu bar icon appears, bot auto-starts
5. Toggle "Launch at Login" from the menu bar icon for auto-start on boot

---

## Commands

Type these in Telegram or say them over voice (no slash needed for voice):

| Command | Voice alternative | What it does |
|---------|-------------------|-------------|
| `/start` | — | Welcome message, explains the bot |
| `/help` | "what commands can I use" | Lists all commands |
| `/status` | "are you alive" | Uptime, PID, memory, recent activity |
| `/cost` | "how much am I spending" | Token usage by model, estimated cost, monthly projection |
| `/clear` | "start over" | Reset conversation history |
| `/projects` | "list my projects" | Shows ~/Projects with git status |
| `/screenshot` | "show me my screen" | Captures screen, sends as photo |
| `/health` | "how's my Mac" | CPU, RAM, disk, battery, top processes |

Or just talk naturally — anything that doesn't match a command goes to Claude.

---

## Feature Tracker

### Security (7)
| Feature | Status | Description |
|---------|--------|-------------|
| Telegram ID whitelist | ✅ Done | `ALLOWED_TELEGRAM_IDS` env var, defaults to owner |
| Activity logging | ✅ Done | All actions logged to `/tmp/claudebot_activity.log` |
| Rate limiting | ✅ Done | 30 calls per 5 minutes per user |
| Emergency kill code | ✅ Done | Send `killclaudenow` to nuke everything |
| Path restrictions | ✅ Done | Bot can only access ~/Projects, ~/Desktop, ~/telegram-claude-bot, /tmp |
| Command blocklist | ✅ Done | Blocks rm -rf /, sudo, format, mkfs, etc. |
| PID file lock | ✅ Done | Prevents multiple bot instances (409 conflict fix) |

### Intelligence (5)
| Feature | Status | Description |
|---------|--------|-------------|
| Smart model routing | ✅ Done | Haiku for quick tasks, Sonnet for complex ones |
| Persistent memory | ✅ Done | `.bot_memory.json` — conversations + token stats survive restarts |
| Multi-step tool use | ✅ Done | Up to 6 iterations per message for complex tasks |
| Conversation history | ✅ Done | Per-user context, last 20 messages persisted |
| Token usage tracking | ✅ Done | Per-model input/output tokens, cost estimation, monthly projection |

### Tools — Claude can use these (9)
| Tool | Status | Description |
|------|--------|-------------|
| run_command | ✅ Done | Execute shell commands (with safety checks) |
| read_file | ✅ Done | Read files from allowed paths |
| write_file | ✅ Done | Create/edit files in allowed paths |
| list_projects | ✅ Done | Show ~/Projects with git branch & dirty status |
| screenshot | ✅ Done | Capture screen (dual display, JPEG compressed) |
| run_background | ✅ Done | Start long tasks with completion notifications |
| send_file | ✅ Done | Send any file from Mac to Telegram |
| system_health | ✅ Done | CPU, memory, disk, battery, top processes |
| serve_project | ✅ Done | One-step local server + ngrok tunnel for public URLs |

### Input Handlers (4)
| Feature | Status | Description |
|---------|--------|-------------|
| Text messages | ✅ Done | Standard chat with Claude |
| Voice messages | ✅ Done | Whisper transcription → Claude processing |
| Photo uploads | ✅ Done | Save to ~/Desktop with optional caption as command |
| File/document uploads | ✅ Done | Save to ~/Desktop with optional caption as command |
| Natural language shortcuts | ✅ Done | Voice phrases route to commands without hitting the API |

### Commands (8)
| Command | Status | Description |
|---------|--------|-------------|
| /start | ✅ Done | Welcome message and onboarding |
| /help | ✅ Done | List all commands with descriptions |
| /status | ✅ Done | Bot health: uptime, PID, memory, recent activity |
| /cost | ✅ Done | Token usage breakdown, cost estimate, monthly projection |
| /clear | ✅ Done | Reset conversation history |
| /projects | ✅ Done | Quick shortcut to list_projects tool |
| /screenshot | ✅ Done | Quick shortcut to screenshot tool |
| /health | ✅ Done | Quick shortcut to system_health tool |

### Menu Bar App (6)
| Feature | Status | Description |
|---------|--------|-------------|
| Custom icon | ✅ Done | Native template icon (adapts to dark/light mode) |
| Start/Stop bot | ✅ Done | PID-based process management |
| Auto-start bot on launch | ✅ Done | Menubar starts bot automatically |
| Launch at Login | ✅ Done | LaunchAgent for menubar (manages bot lifecycle) |
| Status indicator | ✅ Done | 🟢 Running / 🔴 Stopped, polls every 10s |
| Terminal-free operation | ✅ Done | start.command closes Terminal, runs in background |

### Scheduled Tasks (2)
| Feature | Status | Description |
|---------|--------|-------------|
| Daily standup | ✅ Done | Morning project summaries (needs STANDUP_CHAT_ID) |
| Build watcher | ✅ Done | Monitors background tasks, notifies on completion |

**Total: 41 features implemented**

---

## Future Ideas
| Feature | Status | Description |
|---------|--------|-------------|
| Git auto-commit watcher | 💡 Planned | Watch for changes, suggest commits |
| Multi-user support | 💡 Planned | Separate contexts per authorized user |
| Web dashboard | 💡 Planned | Browser UI for logs, memory, status |
| Plugin system | 💡 Planned | Extensible tool registration |
| Packaged .app | 💡 Planned | One-click install for distribution |
| Encrypted memory | 💡 Planned | Protect .bot_memory.json at rest |
| Inline code preview | 💡 Planned | Syntax-highlighted code blocks in Telegram |
| Live progress streaming | 💡 Planned | Edit message in-place as Claude works |
| Inline keyboards | 💡 Planned | Confirmation buttons for dangerous commands |
| setup.sh installer | 💡 Planned | Interactive setup script for new users |

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_TOKEN` | Yes | — | Telegram Bot API token |
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `ALLOWED_TELEGRAM_IDS` | No | `8687978775` | Comma-separated authorized user IDs |
| `STANDUP_CHAT_ID` | No | — | Chat ID for daily standup messages |
| `STANDUP_HOUR` | No | `9` | Hour (24h) for daily standup |

## Files

```
telegram-claude-bot/
├── bot.py              # Main bot (~1200 lines)
├── menubar.py          # macOS menu bar app
├── start.command       # Double-click launcher
├── .bot_memory.json    # Persistent memory (gitignored)
├── .gitignore
├── TODO.md             # Original feature checklist
├── PRODUCT_ROADMAP.md  # Go-to-market plan & competitive analysis
└── README.md           # This file
```
