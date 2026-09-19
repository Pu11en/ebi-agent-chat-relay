# David’s own Claude, operated from Discord

## ✅ What stays David’s

- His own Claude subscription, login, projects, model preferences, skills, and memory.
- His own `AGENTS.md`, `CLAUDE.md`, and existing personal settings.
- No copying Drew’s home folders, personal instructions, Linux paths, or credentials.
- The requested change is how David starts and uses his agent: Discord threads with background startup and recovery.
- Permission bypass is explicitly requested for David’s Discord bot; unrelated personal settings remain his.

## ⏳ One-time setup on his computer

Paste the following into **David’s local Claude Code session** that set up the bot; if his Discord agent is already responding and can run commands, it can receive this there instead.

```text
Finish configuring my existing Windows Discord Claude bot so I can do normal work entirely through Discord threads. Preserve MY instructions and settings; do not copy Drew’s AGENTS.md, CLAUDE.md, skills, memory, credentials, paths, or personal prompts. This computer and Claude subscription are mine (David’s). Startup, recovery, and default permission bypass are authorized.

Inspect the existing installation first. Determine native Windows versus WSL, actual bridge executable, configuration file, account, and process. Back up only the instance files you change locally without exposing secrets. Keep my existing projects and session history. Do not reinstall over the working instance or upgrade to an arbitrary revision as part of this setup.

Separate the bridge installation from the project folder used for Claude sessions. Reuse my existing configured project directory if appropriate. Preserve my personal and project instructions; do not overwrite or regenerate them. Check any APPEND_SYSTEM_PROMPT, launcher environment, and bot-generated instructions for copied Drew-specific content. Remove only copied instance-specific content; preserve generic bridge functionality and use commands appropriate to my shell. Do not use the framework source checkout as my default project merely because the bot is installed there.

Apply these settings to MY bridge and resolve conflicting launcher environment values:
CCDB_BACKEND=claude
CCDB_PERMISSION_MODE=bypassPermissions
CLAUDE_PERMISSION_MODE=bypassPermissions
CCDB_DANGEROUSLY_SKIP_PERMISSIONS=true
CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true
DISCORD_OWNER_ID=718234548139196476
CCDB_ALLOWED_USER_IDS=488763953397235712,718234548139196476
CCDB_THREAD_MEMBER_IDS=488763953397235712,718234548139196476
DISCORD_CHANNEL_ID=1550644904617582662
CCDB_CHANNEL_IDS=1550644904617582662,1550644907100741783
CCDB_MONITOR_ALL_CHANNELS=false
CCDB_MENTION_ANYWHERE=false
MAX_CONCURRENT_SESSIONS=3

Use my existing private bot token, Claude login, and dedicated data directory. Bot/application ID is 1550644558176460961 and guild ID is 1546639912848199742. Do not print tokens or passwords. Verify effective launch arguments include --permission-mode bypassPermissions and --dangerously-skip-permissions; resolve conflicting saved modes for ordinary coding sessions while retaining intentional future plan-mode requests. Preserve my model preference.

Configure automatic background startup using the environment where my bridge and Claude already run. Target startup after reboot, without an open terminal, under MY user account and profile so my Claude login and personal instructions remain available. On native Windows use a scheduled task or equivalent suitable background manager; if WSL, use its actual distribution and Linux user and arrange Windows startup accordingly. Do not run Claude as SYSTEM or copy credentials into another account. If Windows needs an account credential or elevation, collect that locally through the appropriate Windows UI, never Discord. If pre-sign-in operation cannot yet be configured, set up sign-in startup and clearly identify that remaining limitation.

Add bounded automatic crash recovery, logs with rotation, and a separate health checker that can recover an unresponsive bridge without relying on that bridge to receive a Discord command. Do not restart healthy long-running Claude tasks; distinguish a busy agent from an unhealthy bridge and avoid repeated restart loops or duplicate bot instances. Check other active sessions before stopping only my bridge. Preserve all session history; do not automatically replay interrupted write commands. Provide a desktop recovery shortcut as a fallback. Keep the computer awake while plugged in for bot availability, allowing its screen to turn off and preserving battery behavior.

Verify Discord access for both David and Drew, shared public session threads, independent sessions in separate threads, and existing model/project/session controls. Agent notifications should target the person who sent the message starting that run, while both people retain thread access. Identify any missing notification feature in this installed revision rather than claiming that changing Discord roles implements it; do not block basic bot recovery on optional feature work.

Check configuration, process health, credential availability, and launcher behavior without paid model calls. Tell us in Discord when ready and give us the exact short message to send for an end-to-end test. Verify the old terminal can close without stopping the background bot. Schedule any reboot test with the person using this computer rather than rebooting unexpectedly. Report what was actually tested, what still needs a reboot or message test, and any remaining local login step. Never claim setup succeeded merely because a startup task was registered.
```

## ⬜ What proves it works

- Close the original terminal: David’s bot remains connected and answers a new Discord message.
- Start work in two threads: each keeps its own conversation and project context, within the configured limit.
- Ask the agent which project and instruction files it uses: it reports David’s intended folder and instructions, without dumping their private contents.
- After a coordinated restart or reboot: the bot reconnects without manually opening Claude Code; verify whether Windows sign-in is still required.
- A controlled bridge failure recovers, while healthy active sessions are left alone.
- Only the requester gets the bot’s completion ping; personal Discord notification overrides may still need adjustment.

## ⚠️ Current limits

- These instructions are ready; this session has no remote access to David’s Windows computer and has not applied or verified them there.
- Everyday work can happen in Discord, but a sleeping/offline computer cannot run the bot, and expired account login or machine failure may still require local action.
- Permission bypass addresses command approvals; the separate Discord interaction timeout must be checked again after recovery.
- Windows provides [startup triggers](https://learn.microsoft.com/en-us/windows/win32/taskschd/boot-trigger-example--scripting-) and [restart settings](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-restartcount); the task’s [user account](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks) determines which profile it uses.
