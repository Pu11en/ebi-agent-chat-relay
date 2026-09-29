# Worker lifecycle review — September 28, 2026

Local candidate only, continued into September 29. Part of
`stabilize-session-reliability` tasks 1.4/3.1;
neither whole task is complete. No live closure, cleanup, restart, deployment,
paid worker/model call, dependency installation or push.

## Reproduced failures

`tests/test_worker_lifecycle_integrity.py` uses real temporary SQLite repositories
and temporary Git worktrees, with fake Discord/backend boundaries. All five new
cases failed before their repair (**5 failed in 5.01s**):

- The candidate's finish helper closed the build's parent session before waiting
  for a verdict. The real chat close gate would therefore reject normal questions
  despite the finished card inviting them. This is a candidate regression, not
  proof of the original live closed-thread incident.
- The legacy group path deleted successfully integrated worker conversations.
- Failed and conflicted legacy workers were closed, archived and deleted, and
  their worktrees were removed. The conflict test created a real Git conflict;
  the failed-worker test also left uncommitted work to preserve.
- Repeating a group reused the same p1 worktree name, erasing the previous attempt.

The candidate now separates review archival from logical worker completion,
retains unsuccessful attempts and their open threads, keeps merge-conflict copies,
uses unique per-attempt names, and does not delete successful legacy subthreads.
Failed work is linked from the parent with its retained path. Existing lifecycle
closure remains in the successful subtask path; see the remaining verification
and durability gaps below before treating that path as release-ready.

`tests/gowork_upgrade/test_worker_archive_eligibility.py` then reproduced six
archive-authority failures (**6 failed, 6 passed in 6.55s**, including the five
repaired tests above and an accepted-history control): blocked, unaccepted finished,
failed earlier, unknown legacy earlier, and accepted records missing commit/check
evidence were all offered for automatic archival.

The ledger now offers current workers only when accepted with a commit and checks.
An additive `earlier_accepted_threads` field carries existing acceptance authority
across rework/retry; `earlier_threads` retains attention-needed history. Old records
without the new field do not invent authority to close an unknown earlier thread.
Accepted earlier threads still archive after reopening the ledger; completed
archival clears both lists' corresponding entry. No stored live ledger was migrated.

## Checks

- First lifecycle-focused check: **133 passed in 73.49s**.
- Ledger/lifecycle/manifest compatibility: **48 passed in 65.75s**.
- Broader parallel Go Work check: **385 passed, two failed in 68.88s**. The
  failures were an activation-note reference to the renamed policy test and the
  demo's old assertion that even failed workers must archive. Updated the map and
  demo to require accepted workers archived, attention-needed workers open, and
  no worker deletion. Coverage was **94% ledger / 84% task-loop statements**.
- Map/demo rerun: **6 passed in 9.54s**, but printed one `Loop ... handles pid ...
  is closed` diagnostic. A demo-only repeat passed **3 tests in 9.23s** without
  it. Local Python's watcher emits this after `waitpid` finds a closed loop;
  it is not proof of an unreaped zombie. Exact originating subprocess/cancellation
  path is unconfirmed. Keep the warning visible as a follow-up, not suppressed.
- Full `make verify` passed: formatting/lint/types clean; **6,188 passed, five
  known warnings, zero errors in 535.35s**, exit 0. No child-process closed-loop
  diagnostic recurred in this run; the earlier occurrence remains unresolved.
  The warnings are the intentional duplicate ZIP member and four discord.py
  positional-argument deprecations. Targeted ledger pyright and public imports
  passed. This full run included remaining dirty lifecycle/recovery changes.
- Focused Ruff formatting/lint and `git diff --check` passed.
- Optional Ruff security scan: the ledger is clean; task_loop has 19 internal
  assertions, one pre-existing suppressed cleanup error and one pre-existing
  recovery continue. HEAD has 18 assertions and the same two error-handling
  findings; the extra assertion belongs to the already-dirty wait helper, not
  these new repairs. These are not command/secret boundary vulnerabilities.
  Manual diff review found no new shell execution, credentials, paid execution,
  or user-derived close authority. Failed close/archive operations now log.
  The demo retains exactly its three HEAD security-rule findings: trusted
  argv-based Git subprocess execution (S603/S607) and one internal assertion.

The independently saved commit covers ledger archive eligibility, its regressions,
the demo's corrected contract and the activation map. The broader task-loop
changes and `test_worker_lifecycle_integrity.py` / `test_worker_sessions.py` remain
uncommitted candidate work. Preserve them; do not treat this commit or the full
candidate run as a verified release integration.

## September 29 continuation

The new session/archive slice is documented in
`docs/session-close-recovery-2026-09-29.md`. Its eight baseline failures and the
additional surface-ordering failure are covered, and full `make verify` passed
with **6,200 tests, five known warnings, zero errors in 557.98s**. This was run
on the dirty candidate. The Go Work closure caller is still not wired through the
durable archive outbox, and whole-build completion followed by actual cog
reconstruction remains untested. Do not mark tasks 3.1/3.4 complete or treat the
passing suite as activation approval.

## Still required

- Legacy group closure still follows successful merge without a durable per-worker
  archive retry ledger or proof that the final combined plan check has passed.
  Preserve the new history safeguards while completing that contract.
- Final build-parent keep/auto-integrate/early-finish paths still contain deletes;
  distinguish explicit throw-away requests from normal successful completion and
  remove unauthorized history deletion with regressions.
- Exercise real ledger + session closure across active turns, storage/Discord
  failure, process reconstruction, and repeated retries before marking 3.1/3.4 done.
  Specific read-only review lead: `_drive` removes the active loop record on
  completion, while `resume_all` retries archives only for saved active loops with
  an extant copy plan. `_build_state` can fall back to the integrated source plan,
  but startup may never reach it for a completed build. Reproduce an archive fault
  through whole-build completion and actual cog reconstruction, not only calling
  `retry_archives` manually on the original `_Running` object.
- `retry_archives` counts task IDs before retry but thread pairs afterward: inspect
  and reproduce its multi-attempt count behavior before relying on that summary.
- Worker exclusion-write failures, other tag writers, unknown legacy loop recovery,
  and explicitly closed workflow parents still need the broader review.
- Attribute the intermittent demo child-process shutdown warning. `_git` awaits
  `communicate()` without cancellation cleanup; this is a lead, not a proven cause.
- Jester's full requirement ledger/review and real microphone trial remain separate
  required work. These fake-boundary tests are not live voice evidence.

## Legacy group acceptance (September 29, continued)

Contract (parallel-task-loop spec): a worker is archived only after its commit is
integrated *and* the combined check passes; verified-but-not-integrated workers
stay open. The legacy `_run_group` path closed a worker as soon as its side copy
merged into the build copy, before any whole-build check or person/auto
acceptance. The rewritten regression
`test_integrated_legacy_worker_closes_only_when_the_build_is_kept` failed against
that behavior by construction (the old test asserted immediate closure).

Repair: a landed legacy worker is recorded durably in the loop record's additive
`landed_workers` list and stays open. When the build is kept — "looks good",
auto-integration, already-integrated recovery or early wrap-up — `_close_build_threads`
closes those workers and then the build's own thread through the durable outbox
(no lock, no delete, tag released after the stored close). The test reloads the
loop store from disk before the verdict to prove the obligation survives a new
store instance. A thrown-away build leaves its landed workers open
(`test_thrown_away_build_leaves_landed_legacy_workers_open`); throw-away keeps its
existing, explicitly requested deletion of the build thread and copy.

Checks: integrity module **11 passed**; related parallel suites **423 passed in
69.18s** plus task-loop/group suites **128 passed in 14.09s**.

Limits: a restart that loses the loop record before the keep (e.g. an orphaned
build) leaves landed workers open; that is visible, not destructive. Workers
landed before this change in live builds are not migrated. Rollback: back up
`~/.local/state/ccdb/gowork-loops.json` before activation — older code treats
records carrying `waiting_*`/`landed_workers` as malformed and drops them on its
next save.
