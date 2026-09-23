# Draft: everyday flows for all three bots

## One familiar control-center
- **This is a proposed flow map, not a claim that every button already exists.**
- **drewai → Lenovo**, **I mac codex → iMac**, **david → David’s Computer**.
- Each category keeps **control-center** as its home; work conversations live under **workers**.
- The category chooses the computer automatically. Both authorized people can use it; that computer keeps its own folders, login and personal instructions.
- The main actions are **New session**, **Resume**, **Projects**, **Active sessions**, and **Settings**; favorite folders stay immediately available inside New session and through the existing favorite shortcut.
- The pinned controls stay in place, with a quiet bottom shortcut so you do not need to scroll back through messages.

## Start a task in an existing folder
- **Control-center → New session → Favorites / Recent / Browse → select folder → Start here**.
- The new workers thread shows the chosen folder and the bot/model it will use.
- The new thread starts idle: no copied task or automatic model call. Send the first task there when ready, including any screenshots or files it needs.
- Ordinary starts use the saved model; changing it is optional, not another required screen.
- Keep replying in that thread for follow-up work on the same task.
- Navigation needs no path typing; describing the task still requires your message.

## Start a new project
- Proposed: **Projects → New project → Create folder / Clone repository**.
- Create folder: browse to its parent, enter the new project name, then create it.
- Clone repository: supply its URL or choose a known repository, then choose the parent folder.
- Show the resulting folder with **Start session** and **Save favorite**.
- Missing GitHub login gets a clear sign-in step only when the repository needs it; do not invent a completed login or require it for an ordinary local folder.
- Creating folders, repository selection and login support are proposed additions, not already-verified launcher features.

## Continue existing work
- **Control-center → Resume → choose an existing conversation → open its original thread**.
- Show the project, thread title and recent activity so similar tasks are distinguishable.
- Reply to continue; retain that thread’s conversation and chosen folder.
- Offer a separate **New task in this project** action when the work needs a fresh conversation.
- Never silently erase context, create a duplicate conversation or switch the folder of a running thread.
- The current Resume implementation opens original accessible threads; richer labels and the New task shortcut are proposed.

## Parallel tasks and active work
- Proposed: **New task in this project → choose how it shares files → new workers thread**.
- Open decision: separate working copies, shared files, a queue, or letting the agent choose the method from the task.
- Separate copies keep simultaneous edits apart but require bringing completed changes together; shared files avoid that step but need coordination.
- Proposed: **Active sessions → select task → Open / Stop / Continue / Archive**.
- Stop ends the current run while keeping its conversation; Continue sends a follow-up rather than starting an unexplained duplicate.
- Archive should tidy completed conversations without deleting project files; active work should be stopped or finished first.
- Show each task’s requester, project and state; prevent duplicate starts from repeated button clicks.

## Settings, alerts and recovery
- Proposed: **Settings → Model / Favorite folders / Notifications / Bot status**.
- Be explicit about whether a setting changes this thread, your shortcuts, or the whole bot; personal favorites must not overwrite the other person’s.
- Keep a model change optional and show its scope before applying it.
- A completion or question should notify the person who requested that turn; the other operator still has access.
- Bot status should show online/offline, queued/running work and setup issues, with recovery available through the same bot’s controls when reachable.
- An offline computer needs an independent management connection or local startup; a button served only by the offline bot cannot revive itself.
- David and iMac still need the shared launcher upgrade verified; direct management links are a separate setup task.
