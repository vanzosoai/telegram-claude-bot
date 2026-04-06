---
name: handoff
description: "Migrate any project into the shared Claude Projects folder and create a HANDOFF.md for seamless context handoff between Cowork and Piclo Bot (Telegram). Use this skill whenever the user says 'migrate', 'move to Claude Projects', 'handoff', 'set up handoff', 'connect to Piclo Bot', or wants to make a project accessible from their phone via the Telegram bot. Also use when the user says 'update handoff' or 'check handoff' to refresh an existing HANDOFF.md after doing work."
---

# Project Handoff — Migrate & Sync Between Cowork and Piclo Bot

This skill does two things:
1. **Migrate** a project into `~/Documents/Claude Projects/` so both Cowork (desktop) and Piclo Bot (Telegram/mobile) can access it
2. **Create or update** a `HANDOFF.md` file that gives either agent full context to pick up where the other left off

The user has a Telegram bot called **Piclo Bot** (previously "Claude Bot") that controls their Mac remotely. Both Cowork and Piclo Bot read/write HANDOFF.md files to stay in sync. This folder and format is the shared protocol between them.

**Piclo Bot now auto-logs to HANDOFF.md** — every meaningful interaction (tool use, code changes) gets appended to the Work Log automatically. Entries from the bot look like:
`- 2026-04-06 19:45:00 UTC (Piclo Bot) — "deploy staging" [tools: run_command, serve_project]`

When you open Cowork, the Work Log already has a record of everything the user did from their phone. Read it to get caught up.

All timestamps use **UTC in 24-hour format with seconds** (e.g. `2026-04-06 19:34:12 UTC`).

---

## HANDOFF.md Format

```markdown
# Project: <project-name>

## Summary
<2-3 sentences: what this app/project does, who it's for, and how it works>

## Status: <In Progress | Complete | Blocked | Paused>
## Last touched: <YYYY-MM-DD HH:MM:SS UTC> by Cowork (Desktop)

### Features
<Running list — append only, never remove>
- <feature> — <one-line description>

### Known Bugs & Issues
<Running list — mark [FIXED] when resolved, never delete>
- [FIXED] <bug> — <what fixed it>
- [OPEN] <bug> — <context>

### Work Log
<Append-only, newest first. UTC timestamp + agent name.>
- YYYY-MM-DD HH:MM:SS UTC (Cowork) — <what was done>
- YYYY-MM-DD HH:MM:SS UTC (Piclo Bot) — "user request" [tools: tool1, tool2]

### Next Steps
- <what should be done next>

### Key Files
- <filename> — <what it does>

### Tech Stack
- <languages, frameworks, dependencies>

### Gotchas
- <anything non-obvious>
```

---

## Automatic Handoff Behavior (Always Active)

### On Session Start
1. Check if the current project has a HANDOFF.md
2. If NO → create one before doing any other work. Read the project's existing files to populate it. Tell the user: "I created a HANDOFF.md for this project so it stays in sync with Piclo Bot."
3. If YES → check the "Last touched" line:
   - If it says "by Cowork (Desktop)" → YOU were the last to edit. Skip the full re-read, just resume.
   - If it says "by Piclo Bot (Telegram)" or "by Claude Bot (Telegram)" → the OTHER agent made changes. Read the **Work Log entries since your last session** to see what was done from the phone. Absorb context silently — do not summarize back unless asked.

### On Session End / After Significant Work
1. Update HANDOFF.md with what was done
2. Do this automatically — if the user says "that's it" or "thanks", update before wrapping up
3. Add new features to Features list, bugs to Known Bugs, work to Work Log
4. Add a Work Log entry with UTC timestamp

### Update Steps
1. Read the current HANDOFF.md
2. Update: Status, Last touched (UTC, by "Cowork (Desktop)"), Features, Known Bugs, Work Log, Next Steps, Key Files, Gotchas
3. Write the updated HANDOFF.md

Features and Known Bugs are **append-only**. Mark bugs [FIXED] when resolved but never delete.
The Work Log is append-only with newest first. Each entry identifies which agent (Cowork or Piclo Bot).

---

## Ongoing Behavior

1. **Start of session:** Read HANDOFF.md (or create if missing). Do this before any other work.
2. **End of session:** Update HANDOFF.md. Do this without being asked.
3. **After migrations:** Create HANDOFF.md as part of the flow.

Never skip these steps. The user switches between Cowork (desktop) and Piclo Bot (phone) constantly. If the handoff goes stale, the other agent works blind.
