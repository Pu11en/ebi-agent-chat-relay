# Task loop: the plan runs itself, one task at a time

Status: design, not built. Based on the realpage sessions of 2026-09-10
(planner thread 1547651896439738409, executors 1547705028960190565 and others).

## What went wrong in realpage

- **Drew was the messenger.** He carried 9 prompts out of the planner and 9 reports back in, and the planner could never see the executor ("its messages come through blank").
- **Drew got lost.** He said "I'm so confused where we are", "what is the sessions goal", and "I need a visualization".
- **The questions were too hard.** "I don't know how to answer your questions anymore."
- **Reports were technical.** "Talk to me like I'm dumb."
- **Workers said done when they weren't.** Task 2 was reported finished, but its Dockerfile was never committed.
- **The planner got too big.** "This session is probably getting too big."

## The design (Ralph loop, same for DSH, Codex and Claude Code)

There are two sessions, and the files are the only contract between them.

| | Planner (Drew's channel) | Worker (one thread next to it) |
|---|---|---|
| Job | Talk with Drew, write the plan | Do the next task, then forget |
| Memory | `PLAN.md` + `PROGRESS.md` in the project | none; a fresh session every task |
| Talks to Drew | Only yes/no questions | Only a plain-English recap, or a yes/no question when blocked |

### The files
- **`PLAN.md`**: the goal, a numbered task list with `- [ ]` checkboxes, and a "never do" list. This is the same shape as `PLAN-v1.md`.
- **`PROGRESS.md`**: one short entry appended per task: what was done, the commit hash, how it was checked, and anything left open.

### The loop (the bot runs it, no AI needed to drive it)
1. Drew says yes to "Start the build?". The bot opens the worker thread.
2. Each round, the bot starts a **fresh** session in the worker thread, on whichever harness the thread uses, with the same prompt. The prompt says: read `PLAN.md` and `PROGRESS.md`, do the first unchecked task only, verify it, commit, tick the box, append to `PROGRESS.md`, and end with a status line.
3. The worker's last line is one of:
   - `DONE`: the task is verified. The bot starts the next round.
   - `ASK: <yes/no question>`: the loop pauses, the question goes to Drew, and his yes or no resumes it.
   - `STUCK`: it failed twice. The loop stops and tells Drew why in plain words.
   - `COMPLETE`: every box is ticked.
4. The loop never pushes, deploys or spends money without an `ASK`.

### The check (so "done" means done)
- **The bot checks before continuing:** the checkbox actually changed, a new commit exists, and the working tree is clean.
- **If not, the claim doesn't count.** The round is retried once, then marked `STUCK`.
- **Where tests exist, the worker runs them and the result goes in `PROGRESS.md`.**

### What Drew sees
After each task, the worker thread posts a recap written for a non-technical reader:

> **Task 3 of 10: Contact info — done ✅**
> What happened: found phone or email for 71 of 204 buildings.
> What changed for you: leads now show a phone number where one exists.
> Anything you need to do: nothing.

The planner channel keeps one pinned progress card, "Task 3 of 10 ✅ · next: Railway deploy", so Drew never has to ask where things stand.

Every question to Drew is yes/no, with the recommended answer marked and one sentence on what each answer does.

## What gets built

| Piece | Where | Effort |
|---|---|---|
| Loop runner (fresh session per round, status-line parsing, git check, pause/resume) | new cog `claude_discord/cogs/task_loop.py` + REST `POST /api/loops` | ~1 day |
| `task-loop` global skill: how to write `PLAN.md`, the worker prompt, the recap format, yes/no questions | `~/.agents/skills/task-loop` (visible to every harness) | ~2 h |
| Progress card in the planner channel | reuse the thread dashboard embed | ~2 h |
| Test on a two-task toy plan, then realpage Task 10 | live | ~1 h |

## Not doing
- **Parallel workers.** That already failed once.
- **A planner that reads every report.** The files are the memory, so the planner never bloats.
- **Harness-specific features** (Claude plan mode, Codex `update_plan`).
