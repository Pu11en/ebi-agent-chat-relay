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
