# Default commands every computer bot needs

## Default set and how to read this list
- **Planning inventory only: no commands are being added, removed or renamed yet.**
- Same core controls for **drewai in Lenovo**, **I mac codex in iMac**, and **david in David’s Computer**.
- Use control-center buttons for everyday actions, with slash commands as shortcuts; use ordinary messages for coding requests, reviews, tests and explanations.
- **✅ Existing** means the behavior exists in the inspected framework or DrewAI installation; it does not prove installation or support on David/iMac.
- **⬜ Needed** means a proposed common control or an unfinished unification; suggested command names are not commands users can run yet.
- Keep one obvious entry point for each action. Older commands can remain compatible while the main menus stop showing duplicate routes.
- Show actions only when that computer/backend supports them, with a clear reason when unavailable.

## Projects, folders and starting work
- ✅ **Open controls — /launcher:** show this computer’s session buttons.
- ⬜ **New session — suggested /new:** one common entry to Favorites / Recent / Browse → select folder → Start here; the shared launcher already supports this flow, but that slash alias is not installed.
- ⬜ **Projects — suggested /projects:** browse existing folders, create a folder, clone a repository, and choose where to start.
- ✅ **Favorites:** add/remove personal folder shortcuts and use recent folders through the launcher; a common slash entry is still to be settled.
- ⬜ **Project files:** view the chosen project’s files and retrieve outputs from its menu; normal Discord uploads and agent attachments already handle sending files into a task.
- ✅ **Find past work — /search:** search thread titles and optionally conversation text.
- ✅ **List sessions — /sessions:** list known sessions; add clear active/waiting/finished filters and project labels to the common menu.
- ✅ **Resume:** the launcher opens the original thread; existing /resume creates a new thread from a previous session, so those behaviors need distinct labels before unifying the interface.
- ✅ **Existing DrewAI alternatives:** /cdnew opens a new session in a folder; /cd retargets the current thread and starts a fresh session there. Keep these as advanced shortcuts, not competing default start buttons.

## Work inside a session
- ✅ **Stop — /stop:** stop the current run while preserving the session.
- ⬜ **Retry:** retry a failed/interrupted request in the same thread with its saved folder; avoid silently duplicating a task that is still running.
- ✅ **New branch of the conversation — /fork:** create another thread from existing conversation context; this alone is not a guarantee of a separate working copy of the files.
- ⬜ **New task in this project:** start fresh conversation context while retaining the selected project; how parallel tasks share files is still an open decision.
- ✅ **Shorten context — /compact:** summarize a long conversation to free room where the backend supports it.
- ✅ **Context usage — /context:** show context usage where supported.
- ✅ **Rewind conversation — /rewind:** go back to an earlier conversation point; this preserves working files and must never be labelled as undoing code changes.
- ✅ **Reset conversation — /clear:** reset the session in this thread; this is not a project-file deletion command.
- ✅ **Completion goal — /goal:** set/check/clear a completion condition; support depends on the chosen backend.
- ⬜ **Archive finished work:** tidy a completed thread without deleting its project folder.
- Keep Stop and Open visible on active tasks; place Reset/Rewind under a clearly labelled conversation menu so they are not confused with resuming.

## Agent, model and personal settings
- ✅ **Choose agent — /backend:** show/switch among agents installed on this computer.
- ✅ **Choose model — /model show and /model set:** display the effective model and select another; /switch is an existing DrewAI shortcut that combines agent and model selection.
- ✅ **Thinking effort — /effort:** show/set supported effort levels.
- ✅ **Usage — /usage:** show available usage information; do not claim universal cost or subscription data that a backend cannot supply.
- ✅ **Skills — /skill:** run a skill available on this computer; the menu should make available skills easy to find.
- ✅ **Tools — /tools-show, /tools-set, /tools-reset:** inspect/change/reset tool access; group these together in Settings.
- ⬜ **Settings:** one place that clearly distinguishes this thread, your own shortcuts and bot-wide defaults.
- ⬜ **Notifications:** completion/questions go to the requester of the current turn; configure personal noise preferences without removing shared access.
- ⬜ **Instructions and memory:** show which local personal/project instructions apply and offer a scoped way to manage them; never replace David’s instructions with Drew’s.
- ✅ **Turn footer display — /engine-status:** configure the Codex usage footer; despite its name this is not a computer-health command.

## Background work and optional features
- ⬜ **Task list and queue:** submit several named jobs, inspect their order, cancel queued jobs, and see which are running; distinguish a saved task list from the existing concurrency limit’s temporary waiting queue.
- ✅ **Multi-step plans on DrewAI — /gowork and /stopwork:** work through a plan and stop after the current step; these are installed instance features, not a verified default across all three bots.
- ✅ **Reminders on DrewAI — /remind:** send a reminder at a chosen time; decide whether this belongs in every bot’s base installation.
- ⬜ **Scheduled agent tasks:** list/add/pause/remove recurring work through a common menu; scheduling infrastructure exists, but a shared default slash/menu interface still needs to be defined.
- ✅ **Local-model maintenance, optional — /ollama status, list, ps, show, pull, rm, use:** inspect, download, remove and select local models; /model install also installs/selects a local model.
- ✅ **External consultation, conditional — /ask:** the framework provides an anonymized external question feature when its required configuration exists; it was not in DrewAI’s 34 registered commands in this check.
- Voice recording, transcription, social posting and application-specific integrations are optional features, not required just to start coding in a folder.
- No scheduled/paid work starts merely because we approve this command inventory.

## Health, recovery and advanced maintenance
- ⬜ **Bot status:** show this computer’s agent availability, authentication state without secrets, current jobs and configuration problems.
- ⬜ **Diagnostics:** test the local bridge, filesystem access and agent installation without making a paid model call.
- ⬜ **Recent errors/logs:** show a bounded, secret-redacted diagnostic summary.
- ⬜ **Recover/restart:** wait for active work before restarting through an independent supervisor; an offline bot cannot serve its own recovery button without another reachable management path.
- ✅ **Framework update — /upgrade:** update support exists; keep it in maintenance and retain idle-aware activation.
- ✅ **Import terminal sessions — /sync-sessions:** bring local CLI sessions into Discord.
- ✅ **Import preferences — /sync-settings:** configure that session-import behavior; it is not shared Discord read/unread synchronization.
- ✅ **Terminal resume details — /resume-info:** advanced fallback for continuing outside Discord.
- ✅ **Working-copy tools — /worktree-list and /worktree-cleanup:** inspect session copies and remove clean orphaned ones; put these under maintenance rather than everyday navigation.
- ✅ **Help — /help:** list commands actually available on this bot, explain their scope, and link back to control-center.
- All actions use the bot assigned to the current category; both authorized operators retain access and their own personal preferences.
- This inventory covers all 34 current top-level DrewAI commands plus proposed gaps; it does not certify every existing command across every backend or remote computer.
