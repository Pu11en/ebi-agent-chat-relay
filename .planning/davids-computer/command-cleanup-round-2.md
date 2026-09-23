# Command cleanup: the full plan, round 2

## What you asked for
- **Delete** the commands you don't use and **add** the ones you would use.
- **Control-center** gets its own commands; **session threads** get different ones.
- In each computer's category, **only that computer's bot shows up**. In the Lenovo category, only DrewAI.
- **/switch replaces /backend and /model.**
- The same setup for **David's computer** and the **iMac**.
- ✅ Nothing is built or deleted yet. This is only the plan.

## ⚠️ The Discord catch
- Discord can limit commands per channel: control-center gets one set, and the workers channel gets another. Threads automatically follow their parent channel, so every session thread gets the workers set.
- **But Discord always shows every command from every bot to the server owner and to Administrators.** Hiding commands can't clean up your own list, because you own the server.
- Hiding **would** clean up David's list, as long as he isn't an Administrator.
- So cleaning up **your** list means fewer bots registering commands, not hiding them.

## Ways to show only one bot
- **A: One bot identity for every computer (recommended, needs a 10-minute test first).** All three computers log in as DrewAI, each one only answers in its own category, and only one of them registers the commands. You'd see one clean list everywhere. ⚠️ Downsides: David's computer would hold DrewAI's login key, and every reply shows as "DrewAI". Thread names can still say which computer did the work.
- **B: DrewAI owns the commands and hands work to the other computers.** This uses the computer-to-computer connection we already want but haven't built. It's the cleanest long term and the biggest build.
- **C: A separate Discord server per computer.** No code needed, but you'd switch servers instead of categories.
- **D: Keep three bots and shrink each to about 7 commands.** Small and safe, but you'd still see three copies of each command.

## Proposed commands
### Control-center
- **/new**: pick, create or clone a project, then get a fresh empty session thread. (Replaces /launcher and /cdnew.)
- **/sessions**: your sessions, newest first, with search and Open / Close / New in same folder. (Replaces /search and /resume.)
- **/settings**: one menu with Default model, Skills, Shared instructions, Connections, Bot status and Updates. (Takes over /usage, /upgrade, the tools, sync, worktree and Ollama commands, and /engine-status.)
- **/help**: short and specific to where you are.
### Inside a session thread
- **/switch**: the model list opens instantly and the AI is picked automatically. It gets an optional thinking-effort box too. (Replaces /backend, /model and /effort.)
- **/stop**: stop the current run and keep the conversation.
- **/help**
- ⬜ **Still to decide:** whether /compact, /rewind, /clear, /goal, /fork and /context stay as commands or become plain requests like "compact this" or "fork this".
- Plans and reminders (/gowork, /stopwork, /remind) already work when you just ask in words. ⬜ Should their commands go?

## Commands that would disappear
- /launcher, /cdnew, /cd, /search, /resume, /resume-info
- /backend, /model, /effort, /usage, /engine-status
- /tools-show, /tools-set, /tools-reset
- /sync-sessions, /sync-settings, /worktree-list, /worktree-cleanup, /ollama, /upgrade
- **Rule we agreed:** each one is removed only after its replacement is built and you've tried it.
- That takes you from **34 commands to about 7**.

## How it works on David's computer and the iMac
- **The same commands everywhere:** /new, /sessions, /settings and /help in control-center, and /switch, /stop and /help in threads.
- Each computer still does its own work, uses its own folders and keeps its own AI logins and personal settings.
- The model list in /switch shows only the AIs installed on **that** computer.
- David's current /new keeps working until its upgraded version is ready.
- ⚠️ I haven't checked which version the iMac runs yet.

## Questions we'll go through, one at a time
- ⏳ **1.** How to show only one bot (A, B, C or D)
- ⬜ **2.** Is the control-center list right?
- ⬜ **3.** Which thread extras stay as commands
- ⬜ **4.** What each /settings section does
- ⬜ **5.** What order to build it in
