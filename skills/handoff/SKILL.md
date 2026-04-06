---
name: handoff
description: "Migrate any project into the shared Claude Projects folder and create a HANDOFF.md for seamless context handoff between Cowork and Claude Bot (Telegram). Use this skill whenever the user says 'migrate', 'move to Claude Projects', 'handoff', 'set up handoff', 'connect to Claude Bot', or wants to make a project accessible from their phone via the Telegram bot. Also use when the user says 'update handoff' or 'check handoff' to refresh an existing HANDOFF.md after doing work."
---

# Project Handoff — Migrate & Sync Between Cowork and Claude Bot

This skill does two things:
1. **Migrate** a project into `~/Documents/Claude Projects/` so both Cowork (desktop) and Claude Bot (Telegram/mobile) can access it
2. **Create or update** a `HANDOFF.md` file that gives either agent full context to pick up where the other left off

The user has a Telegram bot (Claude Bot) that controls their Mac remotely. Both Cowork and Claude Bot read/write HANDOFF.md files to stay in sync. This folder and format is the shared protocol between them.

All timestamps use **UTC in 24-hour format with seconds** (e.g. `2026-04-06 19:34:12 UTC`). This avoids ambiguity across timezones and when multiple handoffs happen the same day.

---

## When to Migrate a Project

The user will say something like:
- "Migrate this to Claude Projects"
- "Move this project so my bot can see it"
- "Set up handoff for this project"
- "Connect this to Claude Bot"

## Migration Steps

### 1. Identify the project

Figure out which project to migrate. It's usually the currently mounted workspace folder. Confirm with the user:
- Project name (folder name)
- Current location (the mounted path)

### 2. Check for breakage risks

Before moving anything, scan for things that would break if the folder path changes. Run these checks and report findings to the user:

```bash
# Hardcoded absolute paths in source files
grep -r "$(pwd)" --include="*.py" --include="*.js" --include="*.ts" --include="*.json" --include="*.yaml" --include="*.yml" --include="*.toml" --include="*.cfg" --include="*.ini" --include="*.env" --include="*.sh" --include="Makefile" --include="*.html" . 2>/dev/null | grep -v node_modules | grep -v .git

# Check for symlinks pointing outside the project
find . -type l -exec ls -la {} \; 2>/dev/null

# Check for absolute paths in git config
cat .git/config 2>/dev/null | grep -i "worktree\|path"

# Check package manager lock files for absolute paths
grep -l "$(pwd)" package-lock.json yarn.lock Pipfile.lock poetry.lock 2>/dev/null

# Check for virtualenvs with hardcoded paths
find . -name "pyvenv.cfg" -exec grep "home" {} \; 2>/dev/null

# Check for .env files with path references
find . -name ".env*" -exec grep -l "/" {} \; 2>/dev/null
```

Report what you find to the user in plain language:
- **Safe to move:** "No hardcoded paths found. This project is portable."
- **Needs attention:** "Found hardcoded paths in these files: [list]. These will need updating after the move. Want me to fix them?"
- **Has a virtualenv:** "There's a Python virtual environment that will break. I'll recreate it after the move."
- **Has symlinks:** "Found symlinks pointing to [locations]. These will still work after the move."

### 3. Move the project

Once the user confirms, move the project:

```bash
# Create the destination if needed
mkdir -p ~/Documents/Claude\ Projects/

# Use rsync to copy (preserves permissions, safer than mv across volumes)
rsync -av --progress <source>/ ~/Documents/Claude\ Projects/<project-name>/

# Verify the copy
diff -rq <source> ~/Documents/Claude\ Projects/<project-name> --exclude=.git --exclude=node_modules --exclude=__pycache__ --exclude=.venv
```

After successful copy and verification, tell the user:
- "Project copied to ~/Documents/Claude Projects/<name>"
- "The original is still at <old-path> — you can delete it when you're confident everything works"
- Do NOT delete the original automatically

### 4. Fix any hardcoded paths

If step 2 found hardcoded paths, update them to the new location. Show the user each change before making it.

### 5. Create HANDOFF.md

Create `HANDOFF.md` in the project root. Read the project's existing README, package.json/requirements.txt, main source files, and any TODO files first. The goal is that an agent reading only HANDOFF.md gets enough context to start working immediately.

Use this exact format:

```markdown
# Project: <project-name>

## Summary
<2-3 sentences: what this app/project does, who it's for, and how it works at a high level>

## Status: <In Progress | Complete | Blocked | Paused>
## Last touched: <YYYY-MM-DD HH:MM:SS UTC> by Cowork (Desktop)

### Features
<Running list of implemented features. Add new ones as they're built. Never remove entries.>
- <feature> — <one-line description>

### Known Bugs & Issues
<Running list of bugs encountered and their resolution status. This helps agents avoid repeating the same mistakes.>
- [FIXED] <description> — <what fixed it>
- [OPEN] <description> — <any context on the cause>

### Work Log
<Append-only log of work sessions. Newest entries at the top. Each entry gets a UTC timestamp and agent identifier.>
- <YYYY-MM-DD HH:MM:SS UTC> (Cowork) — <what was done>

### Next Steps
- <what should be done next, in priority order>

### Key Files
- <filename> — <what it does>

### Tech Stack
- <languages, frameworks, key dependencies>

### Gotchas
- <anything non-obvious: environment requirements, API keys, permissions, quirks>
```

---

## Automatic Handoff Behavior (Always Active)

These behaviors should happen automatically in EVERY session, without the user asking:

### On Session Start
1. Check if the current project has a HANDOFF.md
2. If NO → create one before doing any other work. Read the project's existing files (README, package.json, source code, etc.) to populate the Summary, Features, Key Files, and Tech Stack sections. Tell the user: "I created a HANDOFF.md for this project so it stays in sync with Claude Bot."
3. If YES → check the "Last touched" line:
   - If it says "by Cowork (Desktop)" → YOU were the last to edit. Skip the full re-read, just resume working.
   - If it says "by Claude Bot (Telegram)" → the OTHER agent made changes. Do a full read to absorb new context silently. Do not summarize back unless asked.

### On Session End / After Significant Work
1. Update the HANDOFF.md with what was done this session
2. Do this automatically — do not wait to be asked. If the user says "that's it" or "thanks" or moves on, update the handoff before wrapping up.
3. If new features were built, add them to the Features list
4. If bugs were found or fixed, update Known Bugs & Issues
5. Add a Work Log entry with UTC timestamp

### When the User Explicitly Asks
The user might also say "update handoff", "check handoff", or "what's the handoff status" — handle these as direct requests to read or update the file.

### Update Steps

1. Read the current HANDOFF.md
2. Update these fields:
   - **Status** — if it changed
   - **Last touched** — current UTC timestamp (`YYYY-MM-DD HH:MM:SS UTC`), by "Cowork (Desktop)"
   - **Features** — add any new features built this session (append, never remove)
   - **Known Bugs & Issues** — add any bugs found, mark previously open bugs as [FIXED] if resolved
   - **Work Log** — add a new entry at the top with UTC timestamp describing this session's work
   - **Next Steps** — revise based on what's left
   - **Key Files** — add any new important files
   - **Gotchas** — add anything discovered during this session
3. Write the updated HANDOFF.md

The Features and Known Bugs sections are append-only — never remove entries. Mark bugs as [FIXED] when resolved but keep them in the list so agents can see the history of what went wrong and how it was solved. This is the institutional memory of the project.

The Work Log is also append-only with newest entries at the top. Each entry uses a full UTC timestamp and identifies which agent did the work (Cowork or Claude Bot). This creates a chronological record both agents can scan to understand the project's trajectory.

---

## Ongoing Behavior

The handoff system is not optional — it runs every session, automatically:

1. **Start of session:** Read HANDOFF.md (or create it if missing). Do this before any other work.
2. **End of session:** Update HANDOFF.md with what was done. Do this without being asked.
3. **After migrations:** Create HANDOFF.md as part of the migration flow.

Never skip these steps. The user relies on this system to switch seamlessly between their desktop (Cowork) and their phone (Claude Bot). If the handoff doc goes stale, the other agent works blind.
