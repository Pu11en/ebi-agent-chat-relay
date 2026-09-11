# Findings: what the stopped agents left (2026-09-10, ~20:28)

All three Discord sessions were stopped by Drew's request. Sources: four read-only search agents plus direct checks.

## DSH thread "📂 ebi-agent-chat-relay" (1547744459104583771)
- **What it built:** five commits straight on `main`, none pushed:
  - 3de8ee2: extra authorized users
  - 03b79bb: auto-join operators
  - 27377b8: DSH thread-id binding fix
  - 7b19d85: retry failed member adds
  - 24d945d: mute a member's reply-needed ping
- **`.env`:** sets `CCDB_ALLOWED_USER_IDS` and `CCDB_THREAD_MUTE_USER_IDS` to the second operator.
- **Forced restart:** it armed `data/restart_after_turn.sh`, hooked into `scripts/health-check.sh`, to force a restart within 240 s. This session disarmed it as `data/restart_after_turn.sh.disarmed`, so the mute feature is not live yet.

## Thread "📂 audio-content" (1547448027776811038)
- **What it built:** auto-transcribe every voice recording with speaker labels (faster-whisper + ffmpeg, both present).
- **Already on GitHub:** merged as PR #17, which is `origin/main` 9d9c336.
- **The local edits are duplicates:** the uncommitted copies in the main tree are byte-identical to 9d9c336, so discarding them is safe.
- **Not live yet:** it hasn't run on a new recording.

## Thread "📂 realpage" (1547708946704506920): out of scope, Drew owns it
- **QA results:** 148 findings, mostly table overflow at tablet and phone widths, plus two dead links.
- **Uncommitted fixes in `/home/drewp/main-projects/realpage`:** URL fixes, rebuilt JSON, and a small CSS change.

## Repo state
- **`main` vs GitHub:** 9 commits ahead of `origin/main`, 1 behind.
- **Branches:** `feat/plans-as-markdown` ⊂ `feat/task-loop` (the worktree at `/home/drewp/main-projects/wt-task-loop`) holds:
  - 7bb740f: plans as markdown
  - b3e4d9e, 68e8b93: task loop, `/gowork`
  - 2f818dc: start-fresh nudge
- **Untracked from an earlier session (04:47):** `examples/ebibot/cogs/workdir_command.py` and `extensions/project_picker/`. They break `test_example_cogs_load`.
- **Worktrees:** 8 in total, several of them stale (see Task 9).

## Context usage per harness (for the nudge)
- **Claude:** `modelUsage.contextWindow` on the result event. Works today.
- **Codex:** the `token_count` event carries `model_context_window` (e.g. 258400), but `parse_codex_line` ignores it (`codex_runner.py:109-117`).
- **DSH:** the SDK exposes no usage fields at all (`dsh_backend.py:756-806`), so usage must be estimated.

## Outside the repo
- **Hermes gateway:** PID 537 is still running.
- **crontab line 1:** runs `~/.hermes/profiles/asset/cron/audio-cache-cleaner.sh`, which is missing.
- **crontab line 2:** runs `/home/drewp/discord-control/idle-nudge.sh`, which is the wrong path.

## /gowork design decisions (Drew, one at a time)
- **Q1, who picks the worker's harness and model:** buttons when `/gowork` is typed. Pick the harness first, then the model from that harness's list. The worker thread keeps that choice for every round.
- **Q2, pings:** ping only for a yes/no question, when the loop is stuck, and once at the end. Finished tasks post quietly, and the bot's 'reply needed' ping is silenced in worker threads.
- **Q3, how a build starts:** once a plan is ready, the planner asks 'Start the build?' with a Yes button. Yes brings up the harness and model buttons, then the loop starts. Typing `/gowork` also still works.
- **Q4, which plan runs:** the planner's 'Start the build?' button runs the plan it just wrote. A typed `/gowork` shows buttons for every plan in the project that still has unticked tasks.
- **Q5, bot restart mid-build:** it resumes by itself. Each running loop (plan, worker thread, report channel, harness, model) is saved in the bot's database. On startup it posts '🔁 Resuming: Task N of M' and continues. A question left unanswered when the restart happened is asked again.
- **(Re-asked after a mix-up)** Drew confirmed the flow: each task gets a fresh session in ONE worker thread, the worker tests its own work, the bot verifies, the session is cleared, and the next task starts in the same thread.
- **Tests:** the bot runs the plan's test command itself after every task. A failure means the task isn't done: it retries once, then stops.
- **At the end:** the worker thread is deleted completely, and the summary/output is posted in the main (planner) thread. The recaps are kept in the plan's progress log first, so nothing is lost. Drew's message was cut off at "and then", so this is still being planned.
- **GitHub is always last:** everything stays local (local git commits only) until Drew has tried it on localhost and said it's good. Then one yes/no, "Put it on GitHub?". The exception: Drew explicitly says to skip testing and push. This is now a global rule in /home/drewp/AGENTS.md for every harness.
- **The try-it step:** when results post, a local copy is already running. The message has the localhost link, 3 quick checks, and 'Looks good' / 'Something's off' buttons. 'Something's off' creates a new fix task.
- **Re-confirmed:** Drew kept the five earlier answers (buttons for harness and model, pings only when needed, the Start button, the plan picker, auto-resume).
- **Hole 1, where the worker works:** in its own copy (a worktree on its own branch). 'Looks good' adds the work to the project, then the copy and its branch are deleted to free the space. 'Something's off' keeps the copy for the fix task.
- **Hole 2, how a task is checked:** every plan has a plain-words 'how to check it' line (a test command, or something like 'localhost:8765 loads'). The bot runs it after every task, and a failure means the task isn't done.
- **Hole 3, a missing DONE line:** strict. No status line means the task is retried even if the work looks fine, then the loop stops as stuck after the retry limit. (Current behavior; keep it.)
- **Hole 4, a stuck build:** the thread and the copy stay. The main thread shows '🛑 Stuck on Task N: <reason>' with three buttons: Try again, Skip this task, Throw it all away. Only a finished build cleans up by itself.
- **Hole 5, the preview's lifetime:** it keeps running through 'Something's off' (the fix updates it and a fresh try-it card is posted). It shuts down after 'Looks good', or after 24 hours with no tap.
- **Hole 6, the cost cap:** 40 tries per build. At the limit it stops with '🛑 Used 40 tries, X of Y tasks done' and a 'Keep going' button. All /gowork design questions are now answered.
