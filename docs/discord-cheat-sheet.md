# Discord Build Cheat Sheet

Live guide: https://discord.com/channels/1546639912848199742/1546670029422985226/1546670031314751508

Verified against the installed Ebi configuration on 2026-09-07. Human reference only; these are examples, not instructions to execute automatically.

The main #control-center channel starts new conversations. Existing project threads continue their own conversations. The reference channel is not an AI chat channel.

## 1 · What happens when I type?

**In <#1546658182989086720> (#control-center):**
A normal message starts a **new thread and a new AI conversation**. Keep talking inside the thread it creates. Posting another normal message in the main channel starts another conversation.

This is **not one permanent manager chat**, and it does not automatically select Meme Explorer.

**Inside <#1546664272094953562> (Meme Explorer):**
A normal message continues that project's existing conversation. This is where you ask questions, plan, approve work, and report problems.

**In <#1546658184091934740> (#workers):**
Worker threads and coordination updates appear here. Open a linked worker thread to inspect that task. A normal message in the main #workers channel also starts a new conversation.

**In this cheat-sheet channel:**
Read the guide. The AI does not listen here.

Example: to change Meme Explorer's navigation, open its thread and say “Change the navigation.” Do not post that in the main #control-center channel.

---

## 2 · Open, continue, or create a project

**Continue Meme Explorer**
Open <#1546664272094953562> and type normally. Tomorrow, return to that same thread. You do not need a new session every day.

**Start a fresh conversation for another EXISTING folder**
1. Go to <#1546658182989086720>.
2. Type `/cdnew` and select the command.
3. Select its `path` field, type a few letters of the project name, and click the suggested folder.
4. Submit the command. Open the thread it links and send your first message.

The menu fills in the full path for you. `/cdnew` opens a conversation; it does not create a folder or a GitHub repository.

**Start a BRAND-NEW project with no folder**
In #control-center, send:
> Create a new folder called Task Garden inside my main WSL projects folder. Just create the folder for now; don't build or publish anything.

Continue any setup questions in the thread it creates. Once the folder exists, use `/cdnew` to select Task Garden and begin the project conversation there.

**Can't find an old project conversation?**
In #control-center use `/search`, with `query` set to the project name, then open a matching thread link. A thread hidden by Discord's archive feature is not necessarily deleted.

---

## 3 · Your everyday plan → build → try flow

Stay in your **project thread** for these steps.

**A. Plan**
> Help me plan [what I want to build]. Ask me the questions that actually change the result. Save the plan in the project. Don't build yet.

**B. Build when you're ready**
> Build the plan. Use separate Ebi worker threads in #workers for tasks that can be done independently, and link them here. Combine the results into one working build. Run focused checks, then tell me how to try it, with a preview link if available.

**C. Try the result yourself**
Open the preview or follow the instructions it gives you. Then report what happened in the same project thread:
> I tried [action]. I expected [result], but [what happened]. Here is a screenshot.

**D. Pick up later**
> Summarize where we are, what's finished, and the next useful step.

“Manager” just means **the project conversation you ask to coordinate the work**. It is not another bot you need to install. Worker threads are created when work is delegated; they do not automatically appear after every plan.

A worker saying “done” is not the same as the whole app being ready. Keep the project conversation responsible for combining and handing off the result.

---

## 4 · Workers, stopping, and changing direction

**Where do I talk most of the time?**
Your project thread. You can mostly leave #workers alone.

**Want to inspect one task?**
Open the worker link your project conversation gives you. Read its messages and ask about that task there.

**Want to change something while it is running?**
Click that thread's red **Stop** button, wait for it to stop, then send your new instruction.

Use the button: `/stop` and simply typing a new message can fail to interrupt Codex immediately in this Ebi version.

**Want every worker to know about a change?**
Tell your project conversation to pass the change to the affected workers. Separate threads are separate conversations; they do not automatically share everything you say.

**Want to stop the entire build?**
Stopping the project conversation does **not** stop its workers. Use Stop in each running worker thread too.

**What do the status labels mean?**
- “Auto-processing” / “Session running”: the AI is working.
- “Waiting for input”: that turn ended; read its response and reply when ready.
- “No activity”: not automatically a failure; the model may still be thinking.

This setup allows up to **3 active sessions total**, including a coordinating session. Additional work waits for a slot.

---

## 5 · WSL, GitHub, and a project with no repo

**WSL folder = your actual project files.**
**Local Git = change history and separate work branches on your machine.**
**GitHub = an optional online repository connected to local Git.**

Opening a project or planning does not require GitHub. Ebi's isolated coding worktrees do need local Git; the agent can set that up as part of an authorized build. A folder is not automatically a GitHub repository.

**Find the existing GitHub connection**
In your project thread, ask:
> Is this project connected to GitHub? If so, send me the repository link. Don't change anything.

**Publish a project when YOU want to**
> Create a private GitHub repository for this project, connect it, and push the project code. Keep secrets, local data, and generated files out.

That is a request to publish, not something /cdnew does by itself.

Your files stay in WSL. Discord is the interface. Your **PC and WSL must be running** for the bot to work.

The optional Japanese weekly-usage/credits footer has been turned off. Old test messages may still contain it; it was a usage display, not a project error.

---

## 6 · The only commands to learn first

**`/cdnew` — open a NEW project conversation**
Use in #control-center. Select an existing project folder from the suggestions.

**`/search` — find an existing conversation**
Use in #control-center. Put a project name or keyword in `query`, then follow a matching thread link.

**`/help` — see Ebi's other commands**
Optional. You do not need to learn the whole list to build something.

**Continue a project — no command**
Open its existing thread and type normally.

**Stop a running task — no command**
Use the red Stop button in that thread.

**Don't use these as “continue” buttons**
- `/cd` changes the current thread's folder and starts fresh AI context.
- `/clear` clears the session association; it is not a refresh button.
- `/cdnew` creates another conversation; it does not reopen the old one.

**If you feel lost, do just this**
Open <#1546664272094953562> and say:
> I'm learning this setup. Explain the current project status simply and help me choose the next thing to do. Don't start a build yet.
