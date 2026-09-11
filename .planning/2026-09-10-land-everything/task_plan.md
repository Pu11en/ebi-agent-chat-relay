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
- [ ] Task 1: Clean the working tree.
  - `examples/ebibot/cogs/voice_recorder.py` and `_transcribe_recording.py` are byte-identical to `origin/main` 9d9c336, so discard the local copies.
  - Restore `scripts/health-check.sh` from `data/restart-health-check.sh.bak`, then delete `data/restart-*` and `.restart-after-turn.log`.
  - `ASK:` delete the untested `examples/ebibot/cogs/workdir_command.py` + `extensions/project_picker/` (they break 3 tests), or move them to branch `wip/project-picker`?
- [ ] Task 2: Bring `main` level with GitHub: `git pull --rebase origin main` (1 new commit). Run `uv run pytest tests/ -q`, and record the result.
- [ ] Task 3: Merge branch `feat/task-loop` into `main`. It carries plans-as-markdown, `/gowork` + `/stopwork`, and the start-fresh nudge. Resolve conflicts, then run `ruff check`, `ruff format --check`, `pyright` and the full pytest.
- [ ] Task 4: Make the start-fresh nudge work on Codex. In `claude_code_core/codex_runner.py`, read `model_context_window` from Codex's `token_count` event and attach it to the `turn.completed` StreamEvent (`context_window`). Write the failing test first.
- [ ] Task 5: Make the start-fresh nudge work on DSH. The DSH SDK reports no token usage, so estimate it:
  - count prompt + reply characters per session ÷ 4, against the model's window;
  - label it an estimate in code and docs.
  - Write the failing test first.
- [ ] Task 6: Get the full suite green. Fix or explain `tests/test_deploy_recovery.py::test_runtime_hook_loads_fallback_then_returns_to_main` and `tests/test_thread_policy.py::…every_call_site_passes_the_constant` (they were failing before today's work).
- [ ] Task 7: Final review of everything unpushed (`git log origin/main..main`):
  - follow `.agents/skills/security-audit/SKILL.md` and `.agents/skills/verify/SKILL.md`;
  - fix what they find;
  - add a CHANGELOG entry for today's features.
- [ ] Task 8: Ship through a PR.
  - Push `main`'s unpushed commits to branch `land/2026-09-10`, then `gh pr create` with a plain-English summary.
  - `ASK:` merge the PR now (squash)?
  - Once it's merged: `git fetch && git reset --keep origin/main`.
- [ ] Task 9: Tidy worktrees and branches.
  - List every `git worktree` and local branch with "merged into main?".
  - `ASK:` remove the merged ones? Keep `wt-task-loop` until Drew moves the bot back to main.
- [ ] Task 10: Clear leftovers of the retired harness (outside the repo, so every step is an `ASK:` showing its exact command and its undo):
  - Hermes gateway PID 537 is still running from its archived files;
  - crontab line 1 points at a missing `~/.hermes` script;
  - crontab line 2 points at `/home/drewp/discord-control/idle-nudge.sh`, but the real path is `/home/drewp/main-projects/discord-control/`.

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
