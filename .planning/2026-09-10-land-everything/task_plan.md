# Task Plan: Land everything, clean

## Goal
All of today's bot work is on GitHub `main` through one reviewed PR, tests are green, and no stray
edits, armed scripts, worktrees or dead harness leftovers remain. The work behaves the same on
Claude Code, Codex and DSH.

## How this plan runs
- **Who runs it:** `/gowork` in the bot's own Discord channel, with this file as the plan. One task per fresh session.
- **Where:** the worker works in `/home/drewp/main-projects/ebi-agent-chat-relay` (the main tree). The live bot runs from `/home/drewp/main-projects/wt-task-loop`, so edits here cannot break it mid-run.
- **Out of scope:** realpage. Drew owns it in his own thread.
- **Rules:** tests before code (the repo's TDD rule). Never push, delete branches or worktrees, or touch anything outside the repo without `ASK:`. Never restart the bot, because that would kill the worker itself. Restarting is Drew's call, after this plan is done.

## Tasks
Each task is one small outcome, about 15–30 minutes in one fresh session.

- [x] Task 1: Remove the stopped agents' leftovers in one go. (1) Discard the duplicate voice-recorder edits: `examples/ebibot/cogs/voice_recorder.py` and `_transcribe_recording.py` are byte-identical to `origin/main` 9d9c336, so run `git checkout -- examples/ebibot/cogs/voice_recorder.py` and delete the untracked copy. (2) Restore `scripts/health-check.sh` from `data/restart-health-check.sh.bak`, then delete `data/restart-*` and `.restart-after-turn.log`. Nothing gets committed here except the plan tick, and `git status` must show no modified tracked files.
  - v4.1.0 (2026-09-21): Obsolete: `handoff/v4.1.0-base` committed every leftover (`ba5b3cf`, `5eeada3`); nothing to discard on David's clone.
- [x] Task 2: Decide on the unfinished project picker. `ASK:` delete the untested `examples/ebibot/cogs/workdir_command.py` + `extensions/project_picker/` (they break 3 tests)? Yes deletes them; no moves them to branch `wip/project-picker`.
  - v4.1.0 (2026-09-21): Done in v4.1.0 box B4 (`515828d`): the picker extension was deleted, `/launcher` covers it.
- [x] Task 3: Bring `main` level with GitHub: `git pull --rebase origin main` (1 new commit). Record the pytest result.
  - v4.1.0 (2026-09-21): Obsolete: v4.1.0 starts from `handoff/v4.1.0-base`, which is ahead of GitHub `main` (HANDOFF §2).
- [x] Task 4: Merge branch `feat/task-loop` into `main` and resolve any conflicts. Record the pytest result.
  - v4.1.0 (2026-09-21): Done: `feat/task-loop` differs from the base only by `1d9d71d` (a status note, dropped in B5).
- [x] Task 5: Run lint and types on the merged code: `ruff check`, `ruff format --check`, `pyright claude_discord/`. Fix what they report.
  - v4.1.0 (2026-09-21): Done in A1: ruff check / format --check / pyright all clean on `release/v4.1.0`.
- [x] Task 6: Fix `tests/test_deploy_recovery.py::test_runtime_hook_loads_fallback_then_returns_to_main` (it was failing before today).
  - v4.1.0 (2026-09-21): Done: `tests/test_deploy_recovery.py` passes (A1 full run, 4523 passed).
- [x] Task 7: Fix `tests/test_thread_policy.py::…every_call_site_passes_the_constant` (it was failing before today).
  - v4.1.0 (2026-09-21): Done: `tests/test_thread_policy.py` passes (A1 full run).
- [ ] Task 8: Make the start-fresh nudge work on Codex. Read `model_context_window` from Codex's `token_count` event into the `turn.completed` StreamEvent (`claude_code_core/codex_runner.py`). Write the failing test first.
  - v4.1.0 (2026-09-21): Still open → v4.1.0 box D10a.
- [ ] Task 9: Make the start-fresh nudge work on DSH with an estimate: prompt + reply characters ÷ 4 per session, against the model's window, labelled "estimate". Write the failing test first.
  - v4.1.0 (2026-09-21): Still open → v4.1.0 box D10b.
- [x] Task 10: Security check of everything unpushed (`git log origin/main..main`), following `.agents/skills/security-audit/SKILL.md`. Fix what it finds.
  - v4.1.0 (2026-09-21): Superseded by v4.1.0 box E2 (security audit over the whole release diff).
- [x] Task 11: Add a CHANGELOG entry, in plain words, for today's features.
  - v4.1.0 (2026-09-21): Superseded by v4.1.0 box F1 (CHANGELOG for 4.1.0).
- [x] Task 12: Drew tries it locally before anything goes to GitHub. Post a Try-it message: the 3 things to check in Discord (a plan shows as scrollable text; `/gowork` on a 2-task practice plan; the 'Start fresh?' nudge), then `ASK:` did everything work? No → stop and describe what broke.
  - v4.1.0 (2026-09-21): Superseded by HANDOFF §8: Drew tries `release/v4.1.0` on the Lenovo before merging.
- [x] Task 13: Push the unpushed commits to branch `land/2026-09-10` and open a PR with a plain-English summary. `ASK:` before pushing.
  - v4.1.0 (2026-09-21): Superseded by v4.1.0 box F3 (`release/v4.1.0` + PR).
- [x] Task 14: `ASK:` merge the PR (squash)? After the merge: `git fetch && git reset --keep origin/main`.
  - v4.1.0 (2026-09-21): Drew's call after the PR (HANDOFF §5 rule 9); not part of the build.
- [x] Task 15: List worktrees and local branches with "merged into main?". `ASK:` remove the merged ones? Keep `wt-task-loop`.
  - v4.1.0 (2026-09-21): Lenovo housekeeping; out of scope on David's computer (HANDOFF §5 rule 7).
- [x] Task 16: `ASK:` stop the leftover Hermes gateway (PID 537)? Show the exact command and how to undo it.
  - v4.1.0 (2026-09-21): Lenovo housekeeping (Hermes gateway PID); out of scope here.
- [x] Task 17: `ASK:` remove crontab line 1 (a missing `~/.hermes` script) and fix line 2's path to `/home/drewp/main-projects/discord-control/idle-nudge.sh`? Show the exact command and how to undo it.
  - v4.1.0 (2026-09-21): Lenovo housekeeping (crontab); out of scope here.

## Done when
- Every box above is ticked.
- `git status` is clean and `git log origin/main..main` is empty.
- The full test suite passes.
- Drew has a short plain-English summary of what is on GitHub and what the go-live restart will switch on.

## After the loop (Drew decides when)
`rm -f ~/.ccdb-dev-worktree && systemctl --user restart ebi-agent-chat-relay` puts the bot back on the
updated `main`. The undo is `echo /home/drewp/main-projects/wt-task-loop > ~/.ccdb-dev-worktree` plus the same restart.

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
