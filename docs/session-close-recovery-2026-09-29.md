# Session close/reopen recovery — September 29, 2026

Local candidate only. This is a checked slice of OpenSpec tasks 3.1/3.4, not
completion of the lifecycle contract and not a deployment or live-record repair.

## Reproduced failures

Real temporary SQLite and fake conversation surfaces proved:

- A false return or exception from archive is forgotten after reconstructing both
  the repository and lifecycle service. The row is closed, so the old startup
  query never sees it again.
- A slow wrap-up from an old close overwrites a reopen, including when another
  close was requested during the same second. Timestamps are not unique requests.
- Two service instances can finish external effects in the wrong order: unarchive
  followed by the old archive, leaving the database open and the surface closed.

The first four cases failed in the combined baseline run (**8 failed, one control
passed in 2.79s**); that run also reproduced four successful-build parent paths
that delete history and leave their session rows open. The in-flight surface race
was separately RED (**one failed in 0.34s**).

## Shared-boundary repair

- Additive `lifecycle_version` and `archive_pending` columns default to zero.
  Existing rows are not retroactively queued for archive by migration.
- Request/reopen advances the version; completing a stored request uses a
  compare-and-set on that exact still-closing version. An old summary cannot
  cancel newer intent.
- Logical closure and its pending archive are one database update. Reconciliation
  can retry the effect with a reconstructed service without writing another
  summary or invoking a model. Failed archive/acknowledgement remains pending.
- Reopen cancels pending archive. A generation-bound acknowledgement cannot clear
  a newer close's work. Schema reinitialization preserves pending work.
- A weak, per-database/per-thread lock orders archive and reopen effects across
  service instances in the bot process. Slow summary generation stays outside the
  lock so a user can cancel it. Unrelated sessions use different locks.
- A service deliberately constructed without a surface does not schedule external
  archive work. Existing direct repository call signatures remain compatible.

## Evidence and limits

`tests/test_archive_recovery.py` covers failures, reconstruction, reopen, stale
summary, in-flight effect ordering, schema replay, stale acknowledgements and the
no-surface control. First focused compatibility run: **109 passed in 20.33s**.
After the two additional controls, lifecycle/storage checks: **81 passed in
12.91s**. Explicit core/service pyright and Ruff security scan were clean.

The broader parallel run passed **607 tests**, with one intentional ZIP warning
and one asynchronous teardown error: a Go Work fixture's repository `get` was a
synchronous MagicMock. That boundary now uses AsyncMock, matching production;
its focused rerun plus archive cases passed **13 tests in 12.99s**. A preceding
serial subset was deliberately interrupted after exposing the parent-thread
fixture mismatch; it is not a successful gate. After correcting these fixtures,
full `make verify` passed: formatting, lint and pyright clean; **6,200 tests
passed, five expected warnings, zero errors in 557.98s**. Warnings are the
intentional duplicate ZIP member and four discord.py positional-argument
deprecations. This is the dirty candidate integration, not a clean-HEAD or release
integration test.

The broader task-loop Ruff S scan reports 19 S101 internal assertions versus 18
at candidate HEAD; the extra is the new `_wait_verdict` copy-state invariant.
S110 and S112 match HEAD. Manual diff review found no new shell/credential boundary.
Public import check and `git diff --check` passed.

Limits that must remain visible:

- This is service/repository reconstruction, not yet the required whole-build,
  new-cog startup reproduction after the active loop record and worktree vanish.
- Go Work's current dirty close helper still constructs a service without a
  surface and then edits Discord itself. Its failed archive is NOT covered by the
  new outbox until that caller is wired in, with durable no-lock/tag-release policy.
- Reconciliation is bounded to 50 pending closes and 50 pending archives per call
  by default. It is not a background sweep of all historical state.
- A crash after Discord accepts an archive but before acknowledgement can repeat
  the surface call. Discord's archive flag is idempotent; the existing closing
  note is not proven exactly-once. Do not claim notification deduplication.
- The lock orders service calls within one process, not separate bot processes or
  direct repository writes. Raw repository callers still need the boundary audit.
- Reopen/unarchive failure recovery, legacy workers' final combined-check evidence,
  and old live inconsistencies remain separate work. No historical row was changed.

The parent preservation and earlier legacy-worker changes remain in the broader
dirty task-loop candidate. Preserve those tests/edits, but do not present this
shared-boundary commit or a full dirty-tree test run as a release integration.

## Go Work adoption of the durable archive (September 29, continued)

RED first: `test_whole_build_archive_failure_converges_after_process_reconstruction`
drives a real `_drive` build (temporary SQLite, real Git copy, fake Discord) to
"looks good" and a successful keep while Discord rejects the archive. Before the
fix the build was reported as `💥 The build crashed` (the raise escaped
`_close_worker_thread`), the loop record was removed, and nothing durable owed the
archive. `retry_archives` also counted tasks before and threads after; a new
regression reported 1 archive where 2 threads were archived.

Repair:

- `_close_worker_thread` uses the chat cog's shared lifecycle service (or builds
  the same one), so a failed archive is stored as `archive_pending` and is retried
  by the chat cog's startup/reconnect reconciliation after the loop record is gone.
  It returns whether close and archive both finished and never raises into the
  build: the keep/auto-integrate/early-finish result already happened.
- The spoken tag is released (`exclude_thread`) only after the stored close.
  A failed exclusion write keeps the ledger entry owed for retry.
- `DiscordThreadSurface.archive` never locks and posts no closing note for a
  session closed by workflow authority (`workflow_close_on_done`), whichever
  process retries it. User closes still archive+lock with the wrap-up note.
- A worker thread without any session row keeps the old archive-only behavior.
- `retry_archives` counts (task, thread) pairs consistently.

The whole-build test then reconstructs a new process: new repositories, new chat
stand-in, new `TaskLoopCog` over the same loop store (resumes 0 builds) and the
setup-wired `build_lifecycle_service(...).reconcile_pending_closes()` (the call
`ClaudeChatCog.on_ready` makes). The archive converges unlocked, the session
keeps its native ID, the tag stays released, nothing is deleted, and neither
`spawn_session` nor `run_fresh_turn` is called.

Checks: worker/lifecycle/wiring focus **24 + 15 passed**; related parallel suite
(lifecycle wiring, worker sessions/integrity, archive recovery, task-loop cog,
all Go Work upgrade tests) **419 passed in 61.05s**; changed-file pyright clean;
Ruff S counts unchanged from the documented baseline (19 S101, 1 S110, 1 S112);
full `make verify` exit 0: **6,204 passed, five known warnings, zero errors in
495.74s**. The 419-test run printed the known `Loop ... handles pid ... is
closed` diagnostic once more; it is still unattributed and was not suppressed.

Still open: legacy group workers are closed right after merging into the build
copy, before the build is accepted (next batch); the chat cog's reconciliation
is bounded to 50 rows per pass and runs on ready/reconnect, not periodically;
cross-process ordering and old live rows are unchanged. Deployment rollback
note: `LoopRecord` rows written by this candidate carry `waiting_*` fields that
older code rejects as malformed; see the rollback checklist before activation.
