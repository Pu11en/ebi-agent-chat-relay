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

- [ ] Task 1: Remove the stopped agents' leftovers in one go. (1) Discard the duplicate voice-recorder edits: `examples/ebibot/cogs/voice_recorder.py` and `_transcribe_recording.py` are byte-identical to `origin/main` 9d9c336, so run `git checkout -- examples/ebibot/cogs/voice_recorder.py` and delete the untracked copy. (2) Restore `scripts/health-check.sh` from `data/restart-health-check.sh.bak`, then delete `data/restart-*` and `.restart-after-turn.log`. Nothing gets committed here except the plan tick, and `git status` must show no modified tracked files.
- [ ] Task 2: Decide on the unfinished project picker. `ASK:` delete the untested `examples/ebibot/cogs/workdir_command.py` + `extensions/project_picker/` (they break 3 tests)? Yes deletes them; no moves them to branch `wip/project-picker`.
- [ ] Task 3: Bring `main` level with GitHub: `git pull --rebase origin main` (1 new commit). Record the pytest result.
- [ ] Task 4: Merge branch `feat/task-loop` into `main` and resolve any conflicts. Record the pytest result.
- [ ] Task 5: Run lint and types on the merged code: `ruff check`, `ruff format --check`, `pyright claude_discord/`. Fix what they report.
- [ ] Task 6: Fix `tests/test_deploy_recovery.py::test_runtime_hook_loads_fallback_then_returns_to_main` (it was failing before today).
- [ ] Task 7: Fix `tests/test_thread_policy.py::…every_call_site_passes_the_constant` (it was failing before today).
- [ ] Task 8: Make the start-fresh nudge work on Codex. Read `model_context_window` from Codex's `token_count` event into the `turn.completed` StreamEvent (`claude_code_core/codex_runner.py`). Write the failing test first.
- [ ] Task 9: Make the start-fresh nudge work on DSH with an estimate: prompt + reply characters ÷ 4 per session, against the model's window, labelled "estimate". Write the failing test first.
- [ ] Task 10: Security check of everything unpushed (`git log origin/main..main`), following `.agents/skills/security-audit/SKILL.md`. Fix what it finds.
- [ ] Task 11: Add a CHANGELOG entry, in plain words, for today's features.
- [ ] Task 12: Drew tries it locally before anything goes to GitHub. Post a Try-it message: the 3 things to check in Discord (a plan shows as scrollable text; `/gowork` on a 2-task practice plan; the 'Start fresh?' nudge), then `ASK:` did everything work? No → stop and describe what broke.
- [ ] Task 13: Push the unpushed commits to branch `land/2026-09-10` and open a PR with a plain-English summary. `ASK:` before pushing.
- [ ] Task 14: `ASK:` merge the PR (squash)? After the merge: `git fetch && git reset --keep origin/main`.
- [ ] Task 15: List worktrees and local branches with "merged into main?". `ASK:` remove the merged ones? Keep `wt-task-loop`.
- [ ] Task 16: `ASK:` stop the leftover Hermes gateway (PID 537)? Show the exact command and how to undo it.
- [ ] Task 17: `ASK:` remove crontab line 1 (a missing `~/.hermes` script) and fix line 2's path to `/home/drewp/main-projects/discord-control/idle-nudge.sh`? Show the exact command and how to undo it.

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
