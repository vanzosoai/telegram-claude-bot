# Telegram Claude Bot - TODO

## SECURITY
- [x] 1. Add Telegram user ID whitelist so only my account can talk to the bot
- [x] 2. Add activity log saving everything the bot does to /tmp/claudebot_activity.log
- [x] 3. Add rate limiting to prevent API credit burn if something goes haywire

## EFFICIENCY
- [x] 4. Set up git author config with name and email
- [x] 5. Add persistent memory so bot remembers context between restarts
- [x] 6. Smart model routing - use Haiku for simple chat, switch to Sonnet for complex builds

## POWER
- [x] 7. Add a list_projects command that shows all projects in ~/Projects
- [x] 8. Screenshot tool so bot can take a screenshot of the Mac and send it as an image in Telegram

## FUN & USEFUL
- [x] 10. Daily standup - bot messages me every morning with summary of active projects
- [x] 11. Proactive notifications when a build finishes or something breaks

## MENU BAR
- [x] 13. Replace robot emoji with a proper native menu bar icon silhouette
