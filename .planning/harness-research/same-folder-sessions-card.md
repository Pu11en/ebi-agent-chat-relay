# Two threads, one folder: what happens now and how to fix it

## ⚠️ What happens today
- When you start a thread in a folder, the bot only **asks** the AI (in its instructions) to make its own private copy of the folder if another thread is **busy at that exact moment**.
- Nothing in the bot actually makes the copy or forces the AI to use it. It is a polite request, not a rule.
- A thread that is just sitting idle between your messages **doesn't count**. Right now, this thread and your other thread in this same folder were both told "you're alone, work in the main folder."
- **Nothing ever brings finished work back.** Copies get left behind. Example found today: a voice-recorder change from 8 days ago sat in a leftover copy, and someone had to copy it into the main folder by hand (those changes are sitting unsaved in the main folder right now).
- If thread A has half-finished edits in the main folder and thread B starts, B's copy **doesn't include A's edits**. Later A can get told to move into a copy too and leave its own edits behind.
- The "who's editing which file" warning can't see edits made by Codex at all, and can't see edits made with shell commands by either AI.
- Shared things like website ports, databases and `.env` settings are never separated. A fresh copy doesn't even have the `.env` file or installed packages.

## 🔍 How other tools solve it
- **Almost everyone gives each session its own copy** (a git "worktree": a second checkout of the same project, on its own branch). This includes Conductor, Claude Squad, Cursor, the Codex app, and Claude Code's own `--worktree` option.
- **Some give each session its own sealed box** (a container or cloud machine): Codex cloud, GitHub Copilot's agent, Devin, Jules, container-use.
- **A few share one folder but label every change with the session that made it** (GitButler: one branch per session, one save per chat turn). This avoids setup cost, but its own makers admit two sessions editing the same file collide.
- **Some share one folder and make sessions reserve files** before editing them (MCP Agent Mail's time-limited file "leases"). Old human tools like Perforce do the same with hard file locks.
- **Everyone brings work back the same way**: as a branch you review and merge. **Nobody auto-merges into main.** Teams add a merge queue so two changes that each work alone get tested together before landing.

## 💡 Lessons that apply to us
- **Make the copy in code, don't ask the AI.** The tools that work point the session at its copy themselves, so it physically can't wander into the main folder.
- **Keep read-only threads cheap.** Claude Code only makes a copy the first time a session actually edits something, so a "just answer a question" thread costs nothing.
- **Shared stuff breaks before files do.** Give each thread its own block of ports (Conductor gives each workspace 10), copy `.env` into each copy automatically, and run a setup step per copy.
- **Copies use disk.** One company measured about 10 GB for two copies of a 2 GB project. Tools cap it (Cursor keeps 25, Codex keeps 15) and reuse a thread's copy when you come back to it.
- **Cleanup must never lose work.** Only delete copies that have nothing unsaved and nothing unmerged.
- **Related work should talk, not share a folder.** When two threads need the same change, the safe way is: one lands it in main, the other pulls it in. Or the two threads get merged into one.
- **"Someone else is editing this file right now" warnings are new ground.** Nobody has solved it well. We already have the pieces (the list of running sessions) to try it.

## 🧭 What "always smart" could look like for us
- ⬜ **Every thread that edits gets its own copy**, made by the bot on the first edit, named after the thread. The main folder stays a clean "home base".
- ⬜ **Each copy gets its own ports and a copy of `.env`**, set up automatically.
- ⬜ **Each thread sees what the other threads in the same folder are doing**: their goal, which files they've touched, and a warning if they overlap.
- ⬜ **"Bring it home" step**: when a thread is done, the bot updates it with main, runs the tests, and merges it locally. It never pushes to GitHub until you've tried it. If two finish at once, they wait in line.
- ⬜ **"Catch up" step**: a thread can pull in what another thread already landed, so related work builds on each other instead of clashing.
- ⬜ **Safe cleanup**: old copies with nothing unsaved get removed. Copies still holding work get reported to you instead of deleted.

## ⏳ Where this stands
- ✅ Research done, with sources, saved in the project's research notes.
- ✅ Confirmed exactly how the bot behaves today, down to the lines of code.
- ⬜ Nothing built yet. First we pick the basic approach, then plan small build steps.
