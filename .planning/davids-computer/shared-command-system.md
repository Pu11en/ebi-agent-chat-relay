# Shared command system for the three computer categories

## What we have agreed
- **This is the command design we are discussing, not an installed replacement command set.**
- One common default system for Lenovo/drewai, iMac/I mac codex and David/david.
- Each computer keeps its **control-center** and **workers** channels; each workers thread is a session.
- The category determines the computer and its bot. Both operators keep their existing access.
- Type a command by itself and let its buttons/pickers guide the rest; no required extra command or typed argument after it.
- **/new creates a clean idle session.** Choose the folder, create the thread, then type the first task there when ready.
- Creating the thread must not start a model, invent a greeting task, copy another conversation or carry its pending goal into the new one.
- This corrects the earlier suggestion to collect a task before opening the thread.

## Proposed control-center commands
- **/new** — Favorites / Recent / Browse → choose a folder → create an idle workers thread → return its link.
- **/sessions** — show sessions started by the requester, including older and archived threads, newest activity first, strictly within this category; offer an Everyone filter within that same category. Show project and idle/running/archived state; select one to open or manage it.
- **/settings in control-center** — clickable/searchable settings for this category’s future-session defaults, including agent/model. Existing sessions keep their settings; do not choose and mutate another thread from this default-settings flow.
- **/help** — show only the relevant control-center actions and short examples.
- **Default model picker chosen:** one unified Favorites + Recent view with clickable choices and typed search; do not group by harness or ask for a separate harness choice. The selected model automatically determines its matching configured harness on this category’s computer. Selecting a default affects future sessions in this category only.
- **Model favorites and recents are personal:** each user sees their own shortcuts and selection history. This does not change the agreed category-wide scope of future-session model defaults.
- **Additional settings areas selected:** folder-browser starting location and concurrent-session limit; discuss and decide these individually.
- **Notification controls excluded:** keep the agreed requester-only notification behavior without adding notification customization to this settings menu.
- The starting folder is only where browsing initially opens, not an automatic working-folder choice or a restriction on available folders.
- No additional command is required to finish one of these flows.
- Advanced maintenance stays inside appropriate menus instead of adding every feature as another top-level command.
- Do not remove old commands until the agreed replacements have been built and checked.

## Proposed commands inside a session thread
- **/stop** — stop this thread’s current run while keeping its conversation and files.
- **/switch** — quick model selection for this session; resolve the matching harness automatically from the chosen model, with no separate harness selection.
- **Thread settings** — separate options still to be defined; model changes are excluded and belong only to /switch. Do not copy control-center settings into threads.
- **/help** — show thread-specific actions, including natural-language session management examples.
- Normal task requests remain ordinary messages, and follow-ups stay in this thread.
- More detailed context/tools/usage controls can sit inside the session’s settings/help controls rather than crowding the initial slash list.
- This describes which commands execute in each place; it does not promise Discord will hide all other entries from the native slash picker.

## Natural-language session management
- **“Open another session in this folder”** — create a new idle thread using the current project folder; return a link without starting the new agent.
- **“Open a session in [folder]”** — resolve the folder on this computer, or present a picker if it is ambiguous, then create an idle thread.
- **“Show the other sessions”** — list accessible sessions in this category with clear names and current state.
- **“Close this session”** — apply the close behavior we choose below; keep the project files.
- **“Close the session called [name]”** — resolve an unambiguous session in this category; if names collide, present the matching choices.
- **“Close the other sessions”** — scope “other” to this category and exclude the current thread; stop selected running work immediately and archive those threads, preserving history and project files.
- Session management must use explicit, validated control operations, not merely claim success in a model reply.
- Validate requester access and destination category when performing every action; names and quoted text are not permission to operate on another computer.
- Keep each operator’s personal preferences; notify the person requesting the action.
- Close does not mean delete project files or erase the conversation history.

## The real Discord limitation and proposed solution
- Discord says administrators can use all application commands, and channel command permissions carry into child threads: https://docs.discord.com/developers/interactions/application-commands#permissions .
- Therefore three bots each registering /new can still give Drew and David three slash entries; category permission changes alone do not deliver the requested clean list.
- **Recommended design to remove these duplicates:** register this shared command set once, and have its command handler route each request by category to the assigned computer bot.
- The slash entry would belong to the same command-owning app across categories; task execution and session replies still belong to the assigned computer. This is not dynamically swapping the app shown beside the slash command.
- This is an architectural proposal, not something already installed. It needs verified communication with each instance, online/offline reporting and category checks on both ends.
- Other instances would stop registering duplicate versions of these commands only after routing is working; their normal task-message handling remains local.
- Until that exists, separate bots can enforce where commands execute, but we cannot honestly promise category-only slash visibility for administrators.

## Close behavior chosen
- **A — Stop and archive:** stop the current run immediately, archive the thread, and retain conversation history and project files.
- This is the selected behavior for the Close action and natural-language requests to close a session.
- The choice is a design decision, not an instruction to close the current planning thread.
- **Multi-select chosen:** select one or several sessions, then press Close; only the explicitly selected sessions are stopped and archived.
- **Actions chosen:** Open, Close and New in same folder; Rename is not part of this command.
- New in same folder uses one selected session’s folder and creates a clean idle thread, with no copied conversation, task or pending goal; the first user task message starts it.
- The core /sessions design is agreed; implementation has not begun.
- Parallel working-copy policy and optional background features remain unanswered and deferred while we establish the basic commands.
