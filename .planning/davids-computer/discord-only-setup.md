# David’s Discord setup — complete corrected handoff

This version replaces the earlier setup handoff: it includes the missing feature code as well as configuration and verification instructions. Give this entire Markdown file to David’s local Claude; no earlier chat is required.

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

First recover the stalled bridge, then install the missing shared-access and requester-notification features using the verified patch contained in this file, then configure background operation, then post a setup receipt in Discord.

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

Verify Discord access for both David and Drew, shared public session threads, independent sessions in separate threads, and existing model/project/session controls. Agent notifications should target the person who sent the message starting that run, while both people retain thread access. The original published baseline does not implement the newer multi-user configuration or requester-only completion pings. These are required setup features, not optional: apply or adapt the bundled patch and run its tests before reporting them as working. Do not fetch unpublished commit IDs from GitHub; the needed code is embedded below.

Check configuration, process health, credential availability, and launcher behavior without paid model calls. Post the setup receipt described below directly in David’s Discord control-center when ready, including the exact short message for our end-to-end test. Verify the old terminal can close without stopping the background bot. Schedule any reboot test with the person using this computer rather than rebooting unexpectedly. Report what was actually tested, what still needs a reboot or message test, and any remaining local login step. Never claim setup succeeded merely because a startup task was registered.
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

## 🔧 Included feature code and application instructions

The original suggested baseline is `9d9c3369bbc54bcd91c60d3aa891b682bbe513ef` from `https://github.com/Pu11en/ebi-agent-chat-relay.git`; David’s actual installed revision must be inspected locally, not assumed.
The patch below adds extra authorized users, shared thread membership with retries, and requester-only completion pings, including regression tests.
It contains no personal instructions, credentials, bot IDs, or startup configuration.
The code originated in local commits `3de8ee2`, `03b79bb`, `7b19d85`, `24d945d`, with the requester change from `be1754c` adapted to this published baseline.

Sender-side verification against the published baseline (Linux, not David’s Windows machine):
- The embedded patch decodes correctly, its SHA-256 matches, and it applies cleanly to the baseline; resulting source matches the tested snapshot after normalizing line endings.
- 220 focused configuration, membership, notification, and integration tests passed; requester tests failed before the requester change and passed afterward.
- 3,073 other tests passed in the broader run; its one deployment-hook failure and one deselected ingest test both passed separately in a clean interpreter, covering all 3,075 collected tests across runs.
- Ruff lint, format checks, and Pyright passed. Security review found only existing assertion warnings; the remaining security rules passed.
- A combined full-suite run stalled at an ingest test, so this is **not** a claim of a clean uninterrupted full-suite pass; the test passes alone, and the unmodified baseline’s API suite also stalls in this environment. The first broad run’s deployment-hook failure was caused by using the running bot’s import-hook environment and passed under the clean interpreter.
- David’s actual installed revision, Windows startup task, reboot behavior, and live Discord responses still require verification on his computer.

Local agent procedure:
1. Read the actual package import path, executable, revision, and launcher configuration; make a backup before changes.
2. Decode the single `base64` block in this document with Python `base64.b64decode`, then `gzip.decompress`; check the SHA-256 below and write the bytes to `david-features.patch` in a temporary local directory.
3. Inspect the decoded diff. From the target SOURCE checkout, run `git apply --check <absolute-patch-path>` before `git apply <absolute-patch-path>`. If a reverse check succeeds, it is already applied; verify tests instead. If neither check succeeds, inspect existing differences and port only the missing behavior with regression tests, preserving local edits; do not force application or reset the checkout.
4. If this is a package installation rather than a source checkout, prepare a separate source directory at its matching revision and keep the existing instance configuration/data outside it. Ensure the background launcher imports the patched package rather than the old installation.
5. In the patched source environment run `python -m pytest -q tests/test_main_config.py tests/test_thread_members.py tests/test_thread_dashboard.py tests/test_claude_chat.py tests/test_setup.py`, then the repository’s lint/type/full-test checks. Do not launch paid model tests automatically.
6. Apply the instance settings earlier in this file and restart only David’s bridge after checking active work. Confirm the running process imports this code.

Patch SHA-256: `ab490fc3d4b9eb32c11ba90394c84a04b786ef5a2e9746370ff684a65bb0b7b1`

```base64
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
```

## 📬 Required setup receipt in Discord

Configure the existing local bridge API on loopback only (`API_HOST=127.0.0.1`, `API_PORT` set to its existing available port or an available dedicated port). Preserve its API secret, or generate one locally if needed; never print it. Use the actual configured URL, not Drew’s host address.
After recovery, use `POST /api/notify` with `Authorization: Bearer <local CCDB_API_SECRET>` and a JSON object containing numeric `channel_id: 1550644904617582662`, `format: "text"`, and the report as `message`. This sends a report without starting a model session. Check that delivery succeeded; if the API is unavailable, report the exact startup error locally rather than claiming Discord delivery.

The report must contain:
- **Computer:** actual native Windows or WSL environment, user profile, project directory, and running package revision/import path; redact private details not needed for verification.
- **Personal configuration:** David’s own instruction sources preserved, no Drew-specific personal prompt copied.
- **Permissions:** effective Claude launch uses bypass mode and the bypass flag.
- **Operators:** David `718234548139196476` and Drew `488763953397235712` authorized, both added to shared threads.
- **Routing:** only control-center `1550644904617582662` and workers `1550644907100741783`; three concurrent sessions configured.
- **Feature code:** patch applied, already present, or adapted, with actual regression test results; requester-only notification behavior verified in tests.
- **Background operation:** task/service name, account, startup trigger, terminal-close check, health recovery result; distinguish boot startup from sign-in startup.
- **Remaining checks:** explicitly mark reboot and live-message tests NOT YET TESTED unless performed; give one concrete next test.

First live test for Drew or David: send **“Reply with your computer name and current project folder, without changing files”** in David’s control-center. Confirm its reply and requester-only ping, continue that thread to check session continuity, then use a second thread for parallel work. A controlled restart/reboot is a separate coordinated check; do not mark it passed from a configuration inspection alone.
