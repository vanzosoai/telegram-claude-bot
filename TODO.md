# Piclo Bot - TODO

## SECURITY
- [x] 1. Add Telegram user ID whitelist so only my account can talk to the bot
- [x] 2. Add activity log saving everything the bot does to /tmp/piclobot_activity.log
- [x] 3. Add rate limiting to prevent API credit burn if something goes haywire

## EFFICIENCY
- [x] 4. Set up git author config with name and email
- [x] 5. Add persistent memory so bot remembers context between restarts
- [x] 6. Smart model routing - use Haiku for simple chat, switch to Sonnet for complex builds

## POWER
- [x] 7. Add a list_projects command that shows all projects in ~/Projects
- [x] 8. Screenshot tool so bot can take a screenshot of the Mac and send it as an image in Telegram
- [x] 9. Background task runner with proactive notifications when builds finish
- [x] 10. Voice messages - transcribe voice and execute as commands
- [x] 11. File transfer - send files both ways (phone <-> Mac)
- [x] 12. System health - CPU, memory, disk, battery, top processes

## FUN & USEFUL
- [x] 13. Daily standup - bot messages me every morning with summary of active projects
- [x] 14. Proactive notifications when a build finishes or something breaks

## MENU BAR
- [x] 15. Replace robot emoji with a proper native menu bar icon silhouette
- [x] 16. Fix stop button (decouple from launch-at-login)

## SETUP NEEDED
- Voice messages require: `pip3 install openai-whisper`
- Daily standup requires: `pip3 install "python-telegram-bot[job-queue]"`
- Daily standup requires env vars: STANDUP_CHAT_ID=8687978775, STANDUP_HOUR=9

## FUTURE IDEAS
- One-line installer script for new users
- Email integration
- Dropbox/Google Drive integration
- Auto-commit watcher (nudge when uncommitted changes sit too long)
- Clipboard bridge (sync clipboard between phone and Mac)
