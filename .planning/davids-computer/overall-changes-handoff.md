# Overall Discord bot changes — continuation

## Purpose and current split
- Drew now reports David’s setup is good, plans to clear the original session, and wants to move to overall Discord server changes. Preserve the working David setup while discussing the next server-wide change.
- Original thread: 1550639669589442661; project: /home/drewp/main-projects/ebi-agent-chat-relay.
- Continue from current local git. Do not discard unrelated changes or push anything merely because Drew said “ship” the David installation.
- Work locally and let Drew try changes in Discord before publishing to GitHub. Server-specific behavior belongs in instance/custom Cogs; shared framework code changes only when actually required. This project is the central place to maintain those changes.
- Keep the conversation short and discuss one command or menu detail at a time. Finish control-center design before thread commands.
- Prioritize a few discoverable commands with clickable/searchable menus, plus equivalent natural-language actions inside sessions.
- Do not reopen settled questions, claim designs are live, or make Drew repeatedly forward agent tasks once a trusted connection exists.
- This new thread is deliberately idle until Drew sends his first message; do not infer permission for a broad paid build from receiving the handoff.

## Agreed command and settings design
- Category determines the computer; no computer picker. Keep each existing control-center and workers channel; do not introduce start-here channels.
- `/new` alone chooses a folder and opens a clean idle workers session bound to it; no model starts before the first human task. The broader design retains Favorites, Recent and browsing accessible folders.
- `/sessions` alone shows the requester’s sessions including archived, newest activity first, only this category; optional Everyone filter remains in category.
- Sessions actions: Open, Close, New in same folder. Close stops immediately and archives, retaining files/history; explicit multi-select is supported. No Rename. New in same folder uses one source folder but copies no conversation/task/goal.
- `/settings` is one control-center menu: Default model, Skills, Shared instructions, Connections, Bot status, Updates. Most section details remain open.
- Default model affects future sessions in the category only. One unified model list selects the compatible harness automatically; no separate harness step. Favorites and recents are personal to each user.
- Existing thread model changes belong to `/switch`, not thread settings; other thread commands/settings remain deferred.
- Notification customization and browser starting-folder customization were explicitly rejected. Completion notifications target only the requester of that turn.
- Capacity should automatically use what each computer/provider can sustain and queue extra work. No concurrency setting in the normal UI. This adaptive behavior is not implemented; last verified local fixed limit was 10, David’s earlier reported limit was 3.
- Skills should be inside `/settings`, not a new `/skills` command; show real per-harness discovery, shared/project scope and missing prerequisites rather than assuming all harnesses have the same capabilities.
- Memory and Backups & recovery are only proposed candidates, not accepted yet.
- All supported settings/session actions must also work via natural-language requests, using the same validated operations and reporting actual outcomes. Distinguish current-session changes from future-session defaults and preserve user ownership.

## Agent-to-agent control is the next enabling capability
- Drew explicitly wants this agent to dispatch tasks to David’s already-local agent and get results here. He rejected direct desktop/Tailscale/SSH as the intended workflow.
- David should automatically accept explicit authorized tasks from the trusted DrewAI bot; no repeated manual forwarding or same-sender approvals.
- Trust the actual Discord sender identity, recipient, guild and explicit task envelope. Deduplicate request IDs; distinguish acknowledgement/running/completed states; do not let ordinary bot replies or completion messages start endless agent loops.
- This connection is NOT implemented or installed yet. David’s baseline `on_message` ignores bot-authored messages.
- Current local `/api/threads/{id}/message` delivers through this process’s own ClaudeChatCog; pointing it at a David thread does NOT execute on David’s Windows computer.
- Posting a Discord task or attachment is not execution. Do not claim work started without receiver acknowledgement or runtime evidence.
- No current direct Windows connection is verified and none is needed for the intended Discord-mediated design. Do not resume asking about Tailscale machine names.
- Public command visibility: administrators can still see app commands despite channel restrictions. Runtime category guards do not guarantee a clean native picker.
- One command-owning app routing by category was proposed for removing duplicates; that architecture is not built or fully settled. Do not remove remote command registrations before routing exists.

## David installed — avoid duplicate work
- Implemented a standalone custom Cog in `examples/project_creation/project_creation.py`, tests in `examples/project_creation/tests`, usage in its README; feature commit 2214551.
- `/new` -> Recent / All projects / Create project -> Empty folder or Clone GitHub repo. Cloning currently takes a pasted GitHub HTTPS link or owner/repo and optional folder name; no account repo-browser in this minimal release.
- Every created/cloned project gets a new child folder under C:/Users/david/projects; the existing-folder clone action remains natural-language work within a thread.
- The original delivered source uses gh for cloning; it includes staged cloning, no overwrite, Windows-name validation, scoped menus, personal persistent recents, idle spawn and same-category workers validation. Do not assume this local source includes David’s subsequent Windows fixes.
- 37 focused tests pass; lint, format, type and security checks pass. Full clean suite with the first 36 focused tests: 3150 passed, one known aggregate-hanging framework test passed separately. Final additional category test passed with all 37 focused tests.
- Self-contained tested source/test ZIP is embedded in `.planning/davids-computer/install-new-project-picker.md` with SHA-256 and exact Windows instance instructions.
- Package already delivered to David’s existing setup thread: https://discord.com/channels/1546639912848199742/1550681218717192263/1550754808259018772 . Delivery receipt is in project-picker-delivery.json.
- David installed the picker in instance commit 106acaf. Activation report 1550766026851885069 confirmed ProjectCreationCog loaded and /new registered; 59 instance tests passed after a Windows-specific test correction.
- The subsequent GitHub repair installed gh 2.101.0 and fixed the supervisor PATH in instance commit e08e425; 62 instance tests passed. Activation report 1550773844292407308 confirmed gh visible to the running bot but unauthenticated. A device-login attempt was started and its code expired; never reuse that old code.
- Drew clarified the desired repository is PUBLIC. Latest requested fix: use git clone for public HTTPS repositories without requiring GitHub CLI login, retaining authenticated private-clone support. Drew then said “now its good”; treat the public flow as user-reported working, not an independently verified new commit or test receipt. Retrieve David’s latest receipt only if needed for further code work.
- Do not block public cloning on GitHub login or reopen completed installation work. Private GitHub authentication remains unverified and is separate from this public-repository workflow.
- Trusted bot-to-bot execution remains absent: tasks can be delivered as Discord messages/files, but a human message currently starts the receiving agent. The original missing-attachment confusion was resolved by verified readback and posting repair instructions inline.
- Do not reinstall the bridge, overwrite the older auth/notification fixes, change David’s personal configuration, remove old commands, or restart another bot as part of this installation.
- Earlier belief that a GitHub login already existed was contradicted by David’s runtime audit. Preserve David’s Claude subscription/settings and any credentials subsequently established; do not claim GitHub authentication is complete without checking.

## Machines, channels and evidence
- Guild 1546639912848199742; Drew user 488763953397235712; David user 718234548139196476; both retain access.
- DrewAI bot 1546642963709427832; Lenovo category 1546658133311754390; control-center 1546658182989086720; workers 1546658184091934740.
- iMac bot 1546757772052537385; category 1546805993441206282; control-center 1546805995685290054; workers 1546805996997967892.
- David bot 1550644558176460961; category 1550644903384580167; control-center 1550644904617582662; workers 1550644907100741783.
- David native Windows: bridge C:/Users/david/ccdb/bridge; instance C:/Users/david/ccdb/instance; runtime venv C:/Users/david/ccdb/.venv; dev venv .venv-dev; own model login under his user profile.
- Last verified David active bridge revision e72a851; custom FolderLauncherCog, CompletionPingCog, ProjectCreationCog and ThreadMembersCog. /new is activated alongside the older launcher.
- David has existing supervisor, health checker, recover.ps1 and independent idle-aware restart task. Activate only when tasks finish; distinguish scheduled activation from verified running code.
- Current local runtime was last identified as a separate checkout `/home/drewp/main-projects/wt-task-loop`, revision a87b265; recheck before changing runtime. The new custom Cog is verified activated on David, not verified activated on Lenovo/iMac.
- Preserve unrelated dirty voice-recorder, health-check, older planning and project-picker extension work. Check current git status and live sessions before editing.
- Existing design details: `.planning/davids-computer/shared-command-system.md`, settings-command.json, sessions-command.json and david-minimal-project-flow.json. New explicit decisions supersede older broad inventories.
- Current local control API is available through CCDB_API_URL; use the environment, not copied credentials. Never post bot tokens, API secrets or GitHub credentials in messages or artifacts.

## Start after clearing this session
- Copy-paste prompt: “Continue overall Discord server work. Read .planning/davids-computer/overall-changes-handoff.md first. David’s public project flow is reported working; preserve it. Keep changes local. Start with one decision about the shared control-center commands and category routing, while retaining the requirement for automatic agent-to-agent tasks and results.”
- Existing overall changes thread: https://discord.com/channels/1546639912848199742/1550757693784989707 . Reuse it rather than creating duplicate sessions. Clearing the original conversation does not implement or automatically start the broader server redesign.
