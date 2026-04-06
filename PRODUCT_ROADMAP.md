# Claude Bot — Product Roadmap & Go-to-Market Plan

**Last updated:** April 6, 2026

---

## Competitive Landscape (Reality Check)

Before investing more, you need to know what you're up against. Two major players entered this exact space recently:

### Claude Code Channels (Anthropic — March 2026)
Anthropic shipped an official Telegram plugin for Claude Code. Setup is 6 steps: create bot via BotFather → install plugin → configure token → relaunch → pair → lock down. It exposes tools for reply, react, and edit_message. It supports photo uploads, typing indicators, and per-project session persistence with directory sandboxing. It's free with any Claude Code subscription (~$20/mo for Claude Pro).

- **Source:** https://code.claude.com/docs/en/channels-reference
- **Threat level:** HIGH — this is literally Anthropic shipping what you built, backed by their full engineering team

### OpenClaw (Open Source — 68K GitHub stars)
Created by PSPDFKit founder Peter Steinberger. A personal AI agent that connects to WhatsApp, Telegram, Discord, Slack, and 50+ platforms. It sends emails, manages calendars, monitors websites, runs overnight tasks, and remembers everything. 100% local data. Cloud version: $29 first month, then $59/mo.

- **Source:** https://github.com/openclaw/openclaw
- **Threat level:** HIGH — massively adopted, well-funded, multi-platform

### What This Means
You are NOT going to win by being another "AI assistant on Telegram." Both Claude Code Channels and OpenClaw do that, and they're either free or backed by serious resources. **You need a differentiated angle.**

---

## Your Actual Advantages (What They Don't Have)

| Advantage | Why It Matters |
|-----------|---------------|
| **macOS-native menu bar app** | Neither competitor has a native Mac presence with start/stop/status |
| **One-step app serving** | serve_project is genuinely unique — build → serve → public URL in seconds |
| **Dual-display screenshots** | Remote visual debugging from your phone |
| **Purpose-built for Mac dev workflow** | Not a general assistant — it's a remote dev environment controller |
| **Zero-config smart routing** | Haiku for quick stuff, Sonnet for complex — saves money automatically |
| **System health monitoring** | CPU, memory, disk, battery, top processes from your phone |
| **Simple enough to fork** | ~1050 lines of Python vs OpenClaw's massive codebase |

---

## Strategic Positioning Options

### Option A: "Mac Dev Remote" (Niche Product)
Position as the remote control for Mac-based developers. Not a general AI assistant — a specialized tool for checking on builds, previewing apps, debugging, and managing projects from your phone.

- **Target:** Solo developers, indie hackers, freelancers with Macs
- **Price:** $29 one-time or $9/mo
- **Differentiator:** Native Mac integration, app serving, screenshot debugging
- **Risk:** Small market, Claude Code Channels is free

### Option B: "Non-Dev Mac Remote" (Broader Market)
Strip out the coding-specific features, add file management, app launching, system admin tools. Position for power users who want to control their Mac from anywhere.

- **Target:** Remote workers, sysadmins, power users
- **Price:** $15/mo
- **Differentiator:** No terminal knowledge needed, natural language Mac control
- **Risk:** Requires significant new features, competing with TeamViewer/etc

### Option C: Open Source + Hosted Service (OpenClaw Model)
Open source the core, sell a hosted/managed version with one-click setup, auto-updates, and a dashboard.

- **Target:** Technical users who want to self-host, plus those who'll pay for convenience
- **Price:** Free (self-host) / $19/mo (managed)
- **Differentiator:** Simpler than OpenClaw, Mac-focused, lower cost
- **Risk:** Maintaining two versions, support burden

### Option D: Template/Starter Kit (Developer Product)
Sell this as a starting point for developers who want to build their own Telegram-controlled AI agent. Include docs, examples, and the architecture as a product.

- **Target:** Developers building custom agents
- **Price:** $49 one-time
- **Differentiator:** Battle-tested code, Mac-specific tools, clear architecture
- **Risk:** Low recurring revenue, easy to clone

**Recommendation:** Option A with elements of D. Nail the Mac dev niche first, then expand.

---

## Feature Roadmap

### Phase 1: Dogfood & Polish (Weeks 1-2)
*Use it daily, find every rough edge*

| Task | Status | Priority | Notes |
|------|--------|----------|-------|
| Use bot daily for real work | ⬜ Not started | P0 | Keep a friction log |
| Fix bugs found during dogfooding | ⬜ Not started | P0 | |
| Token/cost tracking per message | ⬜ Not started | P0 | Show input/output tokens + estimated cost |
| Daily/weekly cost summary command | ⬜ Not started | P0 | `/cost` command |
| Auto-recovery watchdog | ⬜ Not started | P1 | Restart bot if it crashes |
| Error messages that actually help | ⬜ Not started | P1 | No stack traces to Telegram |
| Conversation context management | ⬜ Not started | P1 | `/clear` to reset, `/context` to see length |

### Phase 2: Setup & Onboarding (Weeks 3-4)
*Make it installable by someone who isn't you*

| Task | Status | Priority | Notes |
|------|--------|----------|-------|
| Interactive setup.sh script | ⬜ Not started | P0 | Walk through API keys, deps, permissions |
| `/start` welcome message | ⬜ Not started | P0 | Explain commands, show examples |
| `/help` command with all features | ⬜ Not started | P0 | |
| Dependency auto-installer | ⬜ Not started | P1 | Check and install pip packages, ngrok, ffmpeg |
| First-run permission checker | ⬜ Not started | P1 | Screen Recording, file access, etc |
| Guided setup via Telegram | ⬜ Not started | P2 | Bot walks you through config via chat |

### Phase 3: Delight Features (Weeks 5-6)
*Things that make people say "holy shit" and share it*

| Task | Status | Priority | Notes |
|------|--------|----------|-------|
| Live progress streaming | ⬜ Not started | P1 | Edit message in-place as Claude works |
| Inline keyboards for confirmations | ⬜ Not started | P1 | "Run this command? [Yes] [No]" buttons |
| Quick action buttons after responses | ⬜ Not started | P1 | [Screenshot] [Serve App] [System Health] |
| Git status + quick commit from phone | ⬜ Not started | P1 | "3 files changed" → [Commit] [Diff] [Stash] |
| Smart notifications | ⬜ Not started | P2 | "Build failed at 3am" → wake-up alert with context |
| Response time indicator | ⬜ Not started | P2 | Show which model is thinking + elapsed time |
| Pinned project context | ⬜ Not started | P2 | `/project myapp` — all commands scoped to that project |
| Dark mode app preview | ⬜ Not started | P2 | Screenshot with dark mode forced |
| Diff viewer in Telegram | ⬜ Not started | P3 | Syntax-highlighted code diffs as images |
| Audio responses | ⬜ Not started | P3 | TTS for hands-free coding updates |

### Phase 4: Productize (Weeks 7-8)
*Package for sale*

| Task | Status | Priority | Notes |
|------|--------|----------|-------|
| .app bundle via py2app or PyInstaller | ⬜ Not started | P0 | Drag-to-Applications installer |
| .dmg with background image | ⬜ Not started | P1 | Professional installer feel |
| Auto-update mechanism | ⬜ Not started | P1 | Check GitHub releases, prompt to update |
| Landing page | ⬜ Not started | P1 | Video demo, feature list, buy button |
| Stripe/Gumroad payment integration | ⬜ Not started | P1 | |
| License key validation | ⬜ Not started | P2 | Simple key check on startup |
| Usage analytics (opt-in) | ⬜ Not started | P2 | What features people actually use |
| Privacy policy / ToS | ⬜ Not started | P2 | Required for any product touching user data |

---

## Delight Playbook (What Makes People Share)

### The "Magic Moments"
These are the interactions that make someone grab their friend's phone and say "look at this":

1. **Voice → Running Code in 10 seconds.** You mumble "build me a calculator app" into your phone while walking. By the time you look down, there's a live URL you can tap.

2. **Screenshot debugging from the couch.** "What's on my screen right now?" → instant screenshot of your dual displays with Claude explaining what it sees.

3. **Morning standup from bed.** Wake up, check Telegram: "Good morning. 3 projects have uncommitted changes. Your todo-app build passed. Disk is 78% full."

4. **One-tap app preview.** Build an app → get a link → tap it → it works on your phone. No deploy, no Vercel, no waiting.

5. **"Fix it" from anywhere.** See a bug on your phone → voice message "the header color is wrong, change it to blue" → bot fixes it → sends you the new screenshot.

### Micro-Delights (Small Things That Add Up)
- Typing indicator while Claude thinks (already working)
- Model badge on responses (⚡ fast / 🧠 deep)
- Emoji status in responses (✅ done, ⏳ working, ❌ failed)
- Sound/vibration on long task completion
- "Good morning" / "Good night" personality touches
- Remember the user's name and preferences

### Anti-Delights (Things That Kill the Magic)
- Slow responses (>10s feels broken) — smart routing helps
- Errors with stack traces — must be human-readable
- Silent failures — always confirm what happened
- Needing to restart the bot — auto-recovery is essential
- Running out of context with no warning — need a `/clear` command

---

## Pricing Research (April 2026 Market)

| Product | Price | Model |
|---------|-------|-------|
| Claude Pro | $20/mo | Subscription (includes Claude Code Channels) |
| OpenClaw Cloud | $29 first month, $59/mo | Subscription |
| Cursor | $20/mo | Subscription + usage |
| Replit Core | $25/mo | Subscription + credits |
| GitHub Copilot | $10-19/mo | Subscription |

**Your sweet spot:** $9-15/mo or $49-79 one-time purchase. You're simpler and more focused than OpenClaw, and you offer Mac-native features that Claude Code Channels doesn't have. Price below the big players, above "just another bot."

**API cost pass-through:** At typical usage (50-100 messages/day), Anthropic API costs run $5-15/mo. If you bundle the API key, you need to price above this. If BYOK, make the setup painless and add a cost tracker so users aren't surprised.

---

## Success Metrics

| Metric | Target | How to Measure |
|--------|--------|---------------|
| Daily active use | 7 days/week | Personal friction log |
| Response time | <8s average | Bot-side logging |
| Crash rate | <1/week | Activity log monitoring |
| Setup time (new user) | <10 minutes | Time the setup.sh flow |
| "Magic moment" in first session | Yes | Does it wow in the first 5 messages? |

---

## Immediate Next Steps (This Week)

1. **Dogfood it.** Use the bot for all your coding work for 7 days straight. Keep a friction log.
2. **Add token tracking.** This is the single most important missing feature for both you and future users.
3. **Add `/start`, `/help`, `/cost`, `/clear` commands.** Basic bot hygiene.
4. **Write the setup.sh script.** If you can't hand your laptop to a friend and have them set it up in 10 minutes, it's not ready.

---

## Key Sources
- [Claude Code Channels Reference](https://code.claude.com/docs/en/channels-reference)
- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [AI Agent Pricing 2026](https://www.chargebee.com/blog/pricing-ai-agents-playbook/)
- [AI Coding Tools Pricing](https://awesomeagents.ai/pricing/ai-coding-tools-pricing/)
