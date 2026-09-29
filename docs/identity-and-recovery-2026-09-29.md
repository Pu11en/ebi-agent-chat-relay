# Identity, legacy recovery and spawn checks — September 29, 2026

Local candidate only (`fix/harness-round-2-20260928`). No deployment, restart,
live-row change, paid model call or worker dispatch. Each defect below was first
reproduced by a failing offline test (temporary SQLite/Git, fake Discord/backend).

## Stale result cannot undo a newer binding (task 2.3)

RED: `test_late_result_from_an_evicted_run_cannot_replace_a_newer_binding`. A
Codex run resumed `old-codex-id`; a newer Claude handoff bound `new-claude-id`;
the old run's late *successful* result then wrote `(old-codex-id, codex)` over
the newer binding. `save()` replaced identity unconditionally.

Repair: `SessionRepository.save_if_current(..., expected=...)` updates only while
the stored ID is still the one this run resumed or bound through its SYSTEM
event. `EventProcessor` tracks that ID; a result that loses the compare-and-set
is logged and ignored (the in-memory ID is not advanced either). A run with no
resumed/bound ID keeps the old unconditional save. Control
`test_resumed_run_may_advance_its_own_binding` proves a legitimate fork still
persists. Identity/backend/run-helper/repository focus: **214 passed**, then the
module **8 passed**.

REL-01 relevance (not a proof of the original writer): before `bd518f5`, a
rejected resume's error echo was saved with the *current* runner's backend, so a
Claude runner resuming the old Codex ID would write exactly the incident pair
`(claude, 01a0e5ff…)`. Also, `save()` keeps the stored backend when a writer omits
it (`COALESCE`), so any backend-less writer after a switch pairs a new backend
with an old ID. What first launched Claude with the old ID between 18:54:36 and
19:07:19 is still UNKNOWN; candidates are a turn whose session ID was captured
before the switch, and restart/resume paths. Both known writer mechanisms are now
guarded (`bd518f5`, this compare-and-set).

## Legacy saved loops wait instead of spending (task 4.3)

RED: `TestLegacyLoopRecovery` (2 failed). A loop record saved before wait
checkpoints existed, for a finished plan, re-entered `loop.run()` → `_wrap_up` on
restart, re-running the checker session and the lessons AI call; an unfinished
legacy build immediately ran another model turn. Neither had fresh authorization.

Repair: records created by current code carry `checkpoints=True`. On restart, a
record without the marker and without a saved wait is ambiguous:

- finished plan → restored as a verdict wait, with a message that its checks were
  not run again (zero model/check calls until the person replies);
- unfinished plain plan → parked with a visible "keep going" question;
- unfinished manifest build → resumes, because its durable ledger is the
  evidence T17 reconciliation already uses (existing restart test unchanged).

Two older tests changed deliberately: a current-code active record now declares
`checkpoints=True`, and the migrated legacy record must hear "keep going" first.

## Empty automatic spawn is rejected before creation

RED: `test_automatic_spawn_without_prompt_creates_no_thread`. From review of
`726ecaf`: `spawn_session("", auto_start=True)` created the Discord thread and its
working-directory row, then raised. The check now runs before anything is created.

## Other review notes

- `48f67d5` (single-thread spawn tagging) is accepted and superseded by `8406035`;
  its response reports the pre-tag thread name (minor).
- The intermittent `Loop ... handles pid ... is closed` diagnostic appeared in
  parallel (`-n 8`) runs of the Go Work suites but not in three serial passes of
  the same ~400 tests. Still unattributed; not suppressed.
- Rollback hazard: older code rejects `LoopRecord` rows with new fields
  (`waiting_*`, `landed_workers`, `checkpoints`) and drops them on its next save.
  Back up the loop store before activation. Making the loader ignore unknown
  fields was rejected: an older loader ignoring a saved wait would resume and spend.

## Running identity in health (task 5.2)

`claude_discord/runtime_identity.py` captures pid, start time, Git commit and
dirty flag once at import during startup (argv `git`, 2-second timeout, no shell).
`/api/health` now adds `runtime` (that fixed identity), `disk_commit` (read on each
request) and `running_matches_disk` (`null` when either side is unknown). RED:
`test_checkout_change_after_start_does_not_change_running_identity` failed with
`KeyError: 'runtime'`; it now proves a later checkout changes only `disk_commit`.
Unavailable Git is reported as `"unknown"`, never guessed. Gate: `make verify`
**6,213 passed, five known warnings, zero errors in 565.66s**.

Limits: identity is taken when `api_server` is first imported, which is during
startup; modules imported lazily later could come from a newer checkout, so a
mismatch proves drift but a match does not prove every module is from one commit.
`/api/health` bypasses API auth, so the commit hash, pid and start time are visible
to anyone who can reach the (normally loopback-only) API port.

## Read-only consistency diagnostic and live-check profiles (tasks 5.1, 5.3)

`claude_discord/consistency_check.py` enumerates every session row and every
tag/eligibility setting through a read-only SQLite URI: `immutable=1` when no WAL
exists (a plain `mode=ro` connection created `-wal`/`-shm` files in the first RED
run), `mode=ro` when a live WAL exists (only SQLite's shared reader index is
touched). It starts no process and calls no model. Tests prove byte-identical
database/WAL files (including a live writer), a monkeypatched `subprocess` that
fails if called, a missing database reported unavailable without being created,
legitimate pool exhaustion not reported as a problem, untagged workers not
failing, and an older schema without `archive_pending` still inspected.

`scripts/live-check.py` no longer calls `/api/sessions` (listing there can
allocate tags) or the `sqlite3` CLI. Units, log and runtime paths come from the
environment, with profiles `none` (default), `jester` and `legacy-voice`; nothing
personal is built in. Unconfigured or unreadable checks print SKIP, never PASS.
Its evaluation is separated from observation and covered offline by
`tests/test_live_check.py` (7 tests, including a proof that only
`/api/health` and `/api/jester/sessions` are read).

Live read-only run, September 29 ~01:40 CDT, profile `jester`, database opened
read-only: bot and Jester services active, API and Jester snapshot answer; running
revision SKIP (the deployed health has no runtime identity yet); **363 session
rows**, no duplicate tags, 0 free words with 16 open untagged sessions, and
**FAIL: two closed sessions still hold tags** — `1553779983158349925` (the REL-01
Zoro thread) and `1553899450227757156`. Nothing was repaired. Coverage limit:
legacy workers created before explicit ineligibility have no marker, so they are
counted as user sessions; the check cannot tell them apart from the database alone.
