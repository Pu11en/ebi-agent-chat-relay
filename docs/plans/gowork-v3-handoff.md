# gowork v3 — handoff (2026-09-15)

## Where things are
- **Code:** branch `feat/task-loop`, worktree `/home/drewp/main-projects/wt-task-loop`. The live bot
  loads it through dev mode (`~/.ccdb-dev-worktree` points at the worktree).
- **Live:** everything below is running since the 17:58 restart on 2026-09-15.
- **Not on GitHub.** Drew's rule: push only after he has tried it and said it's good, then ask
  "Put it on GitHub?". Another session also commits to this branch (e.g. dae386b, the backup AI),
  so check `git log` before assuming a clean history.
- **Tests:** 3,395 pass (`uv run pytest tests/ -q -p no:randomly`); ruff and pyright are clean.
- **Plan and design notes:** `docs/plans/gowork-v3.md`. Research: `.planning/harness-research/`
  (`cards-agentic-workflow.md` for the ideas, `goal-elicitation.md` for the goal interview).

## What gowork does now (all live)
- **Talking to a build:**
  - Everything happens in the build's own thread. The starting channel gets only the start card
    and the final result.
  - Drew's words come first in every step, and the AI can end a step with `PAUSE`, `SKIP` or `PLAN`
    (it rewrote the plan).
  - Only a bare "close" or "stop" ends a build. "Pause" goes to the AI instead.
  - Other sessions can't relay into a build thread unless `user_asked: true`, and build steps don't
    read the lounge.
- **Goals:**
  - A plan's `Goal:` and `Done when:` lines reach every step and the end check.
  - A plan with no goal starts with a short goal interview (guesses first, at most 5 lettered
    questions, then approval). It happens in the planning thread, before the build's thread
    opens (Drew: everything before the build belongs in the thread it was planned in); a
    one-shot Claude Sonnet reads the copy and the bot writes the approved lines into the plan.
    Questions during the build stay in the build's thread. **Committed locally, not on the PR,
    not live until the next restart.**
  - If the goal isn't met, the build adds the missing steps itself, up to 3 rounds, then asks.
- **Choosing AIs:** option "A" on the start list picks a model per step with Haiku. It stays in the
  build's AI family (Drew wants Claude only for now) and ranks models by name (`model_tier`).
- **Side-by-side steps:** up to 3 independent steps run in their own copies and threads. The plan's
  check and reviews still apply, and a step that doesn't combine runs again on its own.
- **Stuck steps:** a stuck step tries the strongest model of the family, then gets split into
  smaller steps, then asks.
- **Reviews:** only hard steps get a mid-level reviewer from the same family. "careful" mode reviews
  every step with the strongest.
- **Modes:** `cheap`, `balanced` (default) and `careful`, via `/gowork mode:`, `"mode"` in
  `POST /api/loops`, or words (the `~/AGENTS.md` rule is updated).
- **Queue:** "queue it" sends `"queue": true`. Builds run back to back and the line moves on when
  one waits for Drew. An 8 am summary posts to the thread of the last queued build.
- **Learning:** records go to `~/.local/state/ccdb/gowork/step-records.jsonl`. The picker sees each
  AI's track record, and the finished card gets "Next time" bullets.
- **Usage limits** pause and ask, or switch once to a backup AI if one was set.
- **After the last step:** the finished card and the "looks good" question go to the planning
  thread (Drew: a build starts and ends where it was planned); the build's own thread stays a
  normal chat on its copy. Any build question can be answered in either thread — in the planning
  thread only a real choice counts, so ordinary chat there is never swallowed. "Looks good" keeps
  everything, including chat edits.
- **Plan changes from the planner:** only what changed is applied, so the build's own plan edits
  survive.

## Proven in real runs (gowork-practice, PLAN-v2 and PLAN-v3)
- Worked: the build thread, step messages, "checking the work", side-by-side steps, "next time"
  notes, the finished card, the normal chat after it, and keep.
- Fixed from those runs:
  - The quick helper ran the Codex binary (the bot's default is Codex). It now always calls
    `shutil.which("claude")`.
  - An `ASK:` line with its choices underneath wasn't seen as a question.
  - The AI question gave up after 10 minutes; it now waits 12 hours.
- **Later runs (PLAN-v4 careful, PLAN-v5 queued cheap) proved:**
  - the goal interview through to a saved goal
  - the family-only picker (Haiku, then Sonnet)
  - a careful-mode review that approved
  - the strongest-model retry (Fable)
  - the goal check
  - a queued cheap build
- **Fixed from those runs:**
  - the queue started a build on a project still being set up, overwriting its record
  - the 8 am summary posted the same afternoon
  - the bot's own test run left tracked files changed, which made the next step look unsaved
  - the practice project no longer tracks its caches or history log
- **Not yet seen for real:**
  - a review sending a step back
  - the queue waiting behind a running build, now that it's fixed
  - the 8 am summary on a real morning

## Open items
- **Quick helper is Claude-only** (`_quick_ai`, Haiku). If Drew drops Claude, the picker, grouping,
  hard-step check and notes switch off quietly. Make it use the family's cheapest model.
- **Allwork's night runs are off:** Drew had the 27 "allwork-night-queue" threads deleted, along
  with scheduler task 9. Bring them back only if he asks, and into the one Allwork thread.
- **Shipcheck:**
  - The build's finished S1–S7 work is unmerged on branch `gowork/task-plan-20260915-101509` in the
    drew's eval repo. Drew is starting a thread to make shipcheck a public repo.
- **Known small gaps:**
  - Typing while the goal-interview AI is still writing goes to the build as a note.
  - A restart in the middle of side-by-side steps can leave their short-lived threads behind.
  - "Next time" notes only cover steps run since the last restart.
- **When Drew OKs it:** push `feat/task-loop` and open the PR (run `make dev-off` first if moving
  back to main).
