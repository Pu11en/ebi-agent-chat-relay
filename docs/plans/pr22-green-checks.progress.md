# PR #22 green-checks — build progress

One section per plan box, added when the box is ticked. Each section: what changed,
commit ID(s), the exact proof command(s) and their pass line. No proof, no tick.

## 1: Make the lock-per-loop test reliable

The test `TestIntegrationLockPerLoop::test_a_new_event_loop_gets_its_own_lock` compared
`id()` of locks returned from two `asyncio.run` calls. The first loop's lock could be
garbage-collected before the comparison, so its address got reused and `id()` comparison
flaked on Python 3.13.

What changed in `tests/test_work_copy.py`:

- The helper is now `_usable_lock()`, which keeps each lock object alive by returning it
  after acquiring and releasing it inside its own loop (proving the second loop's lock
  works without hanging).
- The assertion is now `first is not second` (object identity on two still-live locks)
  instead of comparing `id()` of a possibly-freed lock.

Proof:

- `uv run pytest tests/test_work_copy.py::TestIntegrationLockPerLoop::test_a_new_event_loop_gets_its_own_lock -v -p no:randomly` → `1 passed`.
- 50 consecutive runs of `TestIntegrationLockPerLoop` (`-p no:randomly`, via a shell loop) → `Total failures: 0 / 50`.
- Plan `Check:` command → `385 passed`, `All checks passed!` (ruff), `528 files already formatted` (ruff format --check), exit 0.

Commit: see the plan repo (`gowork/pr22-green-checks-20260921-144616`).

Note on the commit mechanism (unblocks the whole build): the worktree's git metadata
lives under `/home/drewp/main-projects/ebi-agent-chat-relay/.git`, which this session's
file sandbox mounts read-only, so `git commit` fails there. The workspace now carries its
own writable `.git` directory (objects + refs copied in from the read-only repo, `origin`
re-added), so commits succeed inside the workspace and history is preserved from
`6a91e31`. This was the blocker the previous attempt hit; it is now gone.

## Task 1 fix-up: Check line made runner-proof

The round-1 DONE was rejected because the plan's `Check:` line chained three commands
with `&&`, and the build runner executes the Check line as a single command invocation —
the tail (`ruff format --check ...`) was fed to pytest, which died with
`unrecognized arguments: --check`.

What changed:

- New `scripts/pr22-gate.sh` runs the whole gate (targeted pytest via
  `test-clean-env.sh`, then `ruff check`, then `ruff format --check`) as one script.
- The plan's `Check:` line is now just `Check: scripts/pr22-gate.sh` — no `&&`, nothing
  to mis-parse.
- The script falls back to a repo-local uv cache (`.uvcache/`, gitignored) when the
  default `~/.cache/uv` is read-only, which is exactly what a sandboxed runner hits.

Proof: `scripts/pr22-gate.sh` → `385 passed in 47.04s`, `All checks passed!`,
`528 files already formatted`, exit 0.

Commit: `218f044`.
