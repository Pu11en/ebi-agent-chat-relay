# What real projects do about many AI threads in one folder

## 🔍 What we looked at
- We downloaded and read the actual code of **about 20 open-source projects**. We didn't just read their websites.
- **Tools that give each thread its own copy:** Claude Squad, Vibe Kanban, Nimbalyst, ccmanager, uzi, worktrunk, git-worktree-runner, gwq, parallel-code, workmux, overstory, baton, container-use.
- **Tools that help threads share one folder safely:** GitButler, MCP Agent Mail, opencode, Codex's old "ghost snapshots", clash, claude-presence, mcp-coordinator.
- **Licenses:** almost all let us copy their ideas and rewrite them in our language (Python).
  - ⚠️ We can use Claude Squad's ideas but **not** its code.
  - ⚠️ We can use Agent Mail's ideas but **not** its code, because its license specifically excludes Anthropic and OpenAI.

## 🧩 The best piece for each problem
- **Making the copy safely:** Vibe Kanban. Before every run it checks that the copy is real and healthy. If it isn't, it rebuilds it and keeps the work. A lock stops two threads from building copies at the same time.
- **Getting `.env` and settings into the copy:** ccmanager. It copies only the private files you list in one small file, `.worktreeinclude`.
- **Installed packages:** parallel-code links the already-installed website packages instead of re-downloading them. For Python, re-running the installer is fast and cheap.
- **Ports (so two websites don't fight):** worktrunk gives each copy its own fixed port number from 10000 to 19999, and uzi checks that the port is actually free.
- **Bringing work home:** worktrunk's "merge" is the gold standard.
  - It saves any loose work first, catches the copy up with main, and runs the tests.
  - It only moves main forward if nothing changed in the meantime. If anything fails, it undoes everything.
- **Cleaning up:** worktrunk again. It refuses to delete a copy that still has unsaved work, and only deletes a branch once its work is already in main.
- **Keeping the AI inside its copy:** every tool starts the AI **inside** its copy instead of asking it to move there. overstory also adds a hook that blocks the AI from writing outside its copy.

## 👀 Seeing what the other thread is doing
- **Best discovery: opencode's "snapshot" trick.**
  - At the start and end of every turn, it takes a quick hidden snapshot of the folder, stored away from your real project.
  - Comparing the two tells you **every file that turn changed, no matter how it was changed**: normal edits, Codex edits, even shell commands.
  - We tested it here and it works. Your real project is untouched.
- **Real clash or just the same file?** git has a built-in dry-run merge that tells "both touched this file but it merges fine" apart from "these two changes truly conflict". We tested it and it works.
- **Warning both threads:** when two threads change the same file, both get told, plus whether it's a real clash.
- **Handing off:** claude-presence keeps a short waiting line. When one thread is done with something, the next thread's AI is told on its next message.

## ⚠️ Gaps and problems we found
- **None of the ~20 projects checks for low disk space or memory.** We'd have to build that ourselves. It matters because your C: drive is nearly full.
- **Codex once filled a user's disk** with its snapshot feature, by snapshotting their whole home folder. Our version must only snapshot real projects, with size limits.
- **Possible bug in our bot:** it seems to look for Codex's file edits under the wrong name, so edits made by Codex may never reach the "who's editing what" warning. It needs one short, real Codex run to confirm.
- Some details were only read in the code and never actually run, like whether worktrunk's port numbers stay the same forever. We'll confirm them while building.

## 🧭 Suggested blend for our bot
- ⬜ **Step 1: snapshot tracking.** Know exactly what each thread changed, and warn when two threads touch the same file. It works even with no copies, so it helps right away.
- ⬜ **Step 2: the bot makes the copy itself** and starts the AI inside it, the first time a thread edits. Health check, lock, and the `.env` copy come along with it.
- ⬜ **Step 3: ports and packages** set up per copy.
- ⬜ **Step 4: "bring it home"** merge (worktrunk-style), local only. Nothing goes to GitHub until you've tried it.
- ⬜ **Step 5: safe cleanup, plus a disk-space guard** that stops making new copies when space is low.
