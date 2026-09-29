# Voice tag integrity — September 28, 2026

Local candidate only, following `stabilize-session-reliability` tasks 3.2/3.3.
No live labels/records changed, restart, deployment, paid agent, or dependency
installation. This is an allocation repair, not the whole lifecycle/recovery fix.

## Confirmed failures

Fourteen regression cases failed before implementation (3.56s):

- A real temporary-SQLite API page with `limit=1` took an open off-page holder's
  name. With a different holder closed, it still took the open holder's name.
- An off-page closed holder retained its name without a new allocation; an
  ordinary archived-but-open holder lost its name when the pool was full. (Under
  the September 29 owner rule below an archived thread is closed, so it now gives
  its word back — through its lifecycle close, never through the pool filling.)
- Single/bulk assignment reported names despite injected storage-write failures.
- Failed deletion could give a closed holder's still-stored name to a new holder.
- A failed settings read removed a valid existing title prefix.
- Unavailable lifecycle evidence, stale creation events and concurrent listing
  could allocate incorrectly. Single creation also failed to reuse a genuinely
  closed holder's name because it never consulted closure evidence.

These reproduce mechanisms with fake Discord and real temporary repositories,
not historical live storage outages or an explanation of every incident.

## Repair

`assign_labels` no longer treats absence/age as permission to reclaim a name.
Its optional `released_ids` argument names releases already completed in storage;
unused names precede newly released ones. Its three-part return shape remains.

Single and bulk allocation now use the same path in `VoiceTagger`. It reads all
stored tag holders, plus requested targets, against the supplied session repository
(or the bot's actual repository). It does not enumerate every historical session.
Explicit worker exclusions and confirmed closure allow release. Unknown holders
retain their names; a Discord-archived thread releases its name once its session
is closed (see the September 29 update). Current repository state takes
precedence over a stale view after reopening.

Deletion must succeed before a word is reusable; assignment must succeed before
the word is returned or displayed. Failures are logged and remain retryable. A
failed snapshot does not retitle threads. API/listener paths supply the repository;
the API docstring now admits that ordinary listing maintains tags/titles rather
than claiming to be read-only.

This slice adopts the candidate's shared bot lock and explicit optional
`voice_addressable=False` creation registration. A regression races both a gateway
event and a roster allocation before thread creation returns, proving normal
worker registration happens before either observer assigns. User-session defaults
remain unchanged. The actual Go Work call-site changes are a separate pending slice.

## Verification

- Initial focused compatibility check: 86 passed, 4 failed. The four listener
  fixtures bypassed `__init__` and lacked the now-read session repository; two
  helpers now provide a normal awaitable lookup. No production workaround added.
- Next focused check: 228 passed, one intentional duplicate-ZIP warning (27.46s).
- Added successful retries after injected set/delete failures, reopened/unknown
  holder controls and the worker/event/list race. Focused result: **259 passed,
  one intentional ZIP warning, 46.28s**; both tag modules reached **100% statement
  coverage**. This is not exhaustive interleaving or live-voice coverage.
- Session-observability, workflow and lounge compatibility: **102 passed, 50.60s**.
- Full `make verify` passed: formatting, lint and pyright clean; **6,176 passed,
  five warnings, zero errors in 525.29s**, exit 0. The warnings are one intentional
  duplicate ZIP member and four discord.py positional-argument deprecations.
  Public imports and `git diff --check` passed. This run included the remaining
  dirty lifecycle/recovery candidate; it is not a clean-release integration claim.

## Security and limits

Tagging adds no command/model invocation, schema/dependency, credential handling,
or new close authority. It reads lifecycle evidence before releasing a stored name;
since September 29 a Discord archive becomes that evidence through a
`discord_archived` close, not through the allocator reading Discord directly. The two tag modules pass Ruff's optional security rules.
The API also passes the optional security scan; the chat cog has the same one
pre-existing internal-state assertion (S101) as HEAD. Manual diff review found no
new command, authorization or secret boundary. The strict full gate passed without
unexplained asynchronous failures.

The shared lock covers allocation/registration on one bot; the observed relay is
one service process (PID139188), and setup supplies its session repository to the
API. This does not claim safety for independent processes writing the same settings
or prove all lifecycle/continuation transfer interleavings. Pending review includes
worker exclusion-write failures, context-nudge's separate tag-transfer writer,
closure/retitle races, premature build-parent closure, legacy worker deletion and
legacy loop recovery. Keep the broader plan tasks open until their full contracts
have evidence; do not deploy the remaining dirty candidate.

## Worker exclusion-write failure (September 29, task 3.2)

RED: `test_failed_worker_exclusion_cannot_leave_a_tagged_orphan`. When the settings
store refused the `voice_addressable:<id> = false` write during worker creation,
`spawn_session` raised after the Discord thread existed and released the tag lock;
the queued thread-create event then gave that orphan internal thread the user tag
`luffy`. `VoiceTagger.exclude_thread` now records the worker in a process-wide
ineligible set before writing, and every allocation honors it; the caller still
receives the storage error. Limit: after a restart, an orphan whose exclusion was
never stored is unknown to the allocator again (it has no session row and no
marker); the live check reports it only as an untagged/tagged thread.
Focused checks: worker sessions **8 passed**; voice/tag/worker/spawn selection
**399 passed**.

## Update: open means visible in Discord (September 29)

The owner replaced the "Discord archive is not closure" rule
(`docs/usable-product-decisions-2026-09-29.md`). A thread archived in Discord (by
hand, by EBI, or by Discord's 7-day auto-archive) or deleted is a closed session:
`ThreadFollowCog` listeners plus a 5-minute sweep stop any running turn, close it
with the `discord_archived` authority, and release its word. Un-archiving, or a
new typed or spoken message, reopens it with its stored native session and
backend, and it gets a tag again. The owner's own threads get words first;
workflow helper threads only get leftovers and give theirs up when the owner opens
a thread on a full pool. Following is on by default;
`CCDB_FOLLOW_DISCORD_THREADS=false` opts an instance out. `assign_labels` is
unchanged: it still releases only holders the caller has already closed.
