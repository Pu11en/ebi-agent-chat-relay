# Jester + EBI stabilization — resume here

Checkpoint: September 29, 2026 (Claude Code continuation). Local work in progress,
NOT deployed or complete. Start with the "Claude Code continuation" section below.
Keep this briefing current after each checked batch. Credit balance is unavailable;
do not claim this checkpoint means credits have run out.

## Activation record — September 29, 08:31–08:35 CDT (read this first)

Drew approved activating the EBI candidate and keeping `17c65de`'s resume rule.
Jester was NOT redeployed (candidate `1348470` still pending its own activation).

- Idle check: no claims, 100/100 snapshot sessions history, no agent CLI running,
  last bot turn 00:08. Lounge notices posted before (id 2677) and after.
- Backup (bot stopped): `~/.local/state/ccdb/activation-backup-20260929-083125/`
  — `sessions.db` (integrity ok), `gowork-loops.json`, `gowork-blockers.json`,
  `builds/`, `SHA256SUMS`, `previous-main-commit` (`8e021d2`).
- Deploy: local merge of `fix/harness-round-2-20260928` (`62c66d6`) into `main`
  as `1656b6d` (not pushed; main is ahead of origin). Restarted
  `ebi-agent-chat-relay.service`; pre-start "All checks passed. Starting bot (1656b6d)".
- Verified: `/api/health` runtime commit `1656b6d`, `running_matches_disk: true`,
  clean tree; schema migrated (363 rows, 0 pending archives); no errors and no
  agent launches after start. Loop decisions as predicted: closed build
  `thread-1554146845415055445` not resumed (record removed, copy kept); finished
  `thread-1554145503506333736` restored as a verdict wait (checks not rerun).
- Live check (jester profile): 10 pass, 1 fail — closed sessions
  `1553779983158349925` (zoro) and `1553899450227757156` (nami) still hold tags
  until the next tag allocation releases them. Recheck after the next new session.
- Rollback if needed: follow `docs/activation-checklist-2026-09-29.md`
  (restore the backup DB or drop the two new columns; restore the loop store;
  reset main to `8e021d2`).

Open: Jester activation (separate approval), microphone trial, OpenSpec 7.3/7.4
(post-activation sample review after real use).

## Claude Code continuation — September 29 (read this first)

Authorized local work is complete up to the approval boundary. Nothing was pushed,
deployed, restarted or repaired live. Both worktrees and all history preserved.
OpenSpec `stabilize-session-reliability`: **25 of 29 tasks** now ticked with an
evidence map in `openspec/changes/stabilize-session-reliability/evidence-2026-09-29.md`;
only section 7 (approval, activation, live checks, microphone trial) remains.

**Urgent for Drew:** do not restart the live bot on current main. Its `resume_all`
would resume build `thread-1554146845415055445` (closed by Drew; manifest plan with
21 open tasks) and dispatch paid workers. Also: a code-only rollback of the
candidate breaks every session read. Both are handled in
`docs/activation-checklist-2026-09-29.md` (EBI candidate), which also lists the
decisions to make before activation (incl. `17c65de`'s resume-prompt policy).

- EBI candidate `fix/harness-round-2-20260928` HEAD **`62c66d6`** (14 commits
  on `8021f53` today). Last gate `make verify`: **6,233 passed, five known
  warnings, zero errors**. `scripts/scenario-matrix.sh`: 9 rows, all pass.
  Evidence: `docs/boundary-audit-2026-09-29.md` (start here),
  `docs/identity-and-recovery-2026-09-29.md`, `docs/scenario-matrix-2026-09-29.md`,
  `docs/activation-checklist-2026-09-29.md`, plus the earlier lifecycle/tag docs.
- Jester candidate `fix/jester-stabilization-20260928` HEAD **`1348470`**
  (JV-01…JV-10). Checks: **156 Node, 7 Python, 16/16 simulations**. Ledger
  cross-checked against the full V1 scope; unbuilt V1 features are GAP rows
  (overlap priority, readback, blockers list, first-turn start check, crash
  transcript marker) — feature work for Drew to schedule, not done here.
- Live read-only findings (not repaired): two closed sessions hold tags
  (`1553779983158349925` zoro, `1553899450227757156` nami); pool exhausted with 16
  open untagged. REL-01 window narrowed to 18:54:36–19:07:19; writer unknown.
- Recorded hazards, not changed: scheduler/API turns outside the chat lock,
  per-process archive ordering lock, no message-ID dedupe, import rows without a
  backend, Codex `OPENAI_API_KEY` fallback question. Two load-only test flakes and
  the parallel-only `Loop ... pid ... is closed` diagnostic remain unattributed.

Next session prompt: "Read the Claude Code continuation section of
handoffs/2026-09-28-jester-ebi-stabilization.md and the EBI candidate's
docs/activation-checklist-2026-09-29.md. Drew decides activation; do not restart,
deploy or change live state without his explicit approval of that checklist."

## Drew's request and authority

Drew wants the whole Jester voice experience and EBI integration reviewed against
real session failures, repaired in checked local batches, and handed to Claude Code
if Codex cannot continue. He explicitly approved continuing the work in a loop.
Do not make him invent prompts or manage individual routine engineering steps.

Local investigation, regression tests, narrow repairs and checked local commits are
authorized. No deployment, shared restart, live session/data repair, paid model or
agent tests, new dependencies/plugins, publishing, or autonomous background repair
is authorized by this loop request. Do not use /gowork, Swarm, or new agents.
Preserve all history and unrelated edits; use test-first fixes and the project skills.
Check lounge/claims/live activity before shared writes. Never print secrets.

## Locations and exact baseline

- Main EBI: `/home/drewp/main-projects/ebi-agent-chat-relay`, HEAD `48f67d5`;
  live service last reported reloaded September 28 at 19:08 CDT. Main is two commits
  ahead of origin. Existing untracked status cards, restart log, reset planning and
  older handoff are unrelated; leave them alone.
- Isolated EBI candidate (current work is here):
  `/home/drewp/.local/state/ccdb/session-wt/harness-round-2-20260928`, branch
  `fix/harness-round-2-20260928`, HEAD
  `8021f537b4df52e4a00999dc8a5eaf53fa860b40` (after `c4248b4`, `8406035`,
  `bd518f5`, `582ab45`, and prior `17c65de`). Candidate worktree is clean and is
  NOT the live checkout.
  Commit 17c65de covers stall-warning noise,
  authority-preserving recovery prompts and test fixtures.
  Checked local commit `582ab45` adds the async failure gate and fixture repairs;
  evidence is in `docs/async-verification-2026-09-28.md`. Commit `bd518f5` preserves
  native identity on failed results with six SQLite regressions. These commits
  are separate from later lifecycle/recovery work, not pushed.
  Checked local commit `8406035` repairs tag integrity, adopts shared creation/
  allocation locking and explicit worker eligibility registration, and adds 16
  regression cases. Evidence: `docs/voice-tag-integrity-2026-09-28.md`. Go Work
  caller adoption and lifecycle repairs remain separate until those contracts
  have end-to-end evidence; the local fixes do not make this a release.
  September 29 archive eligibility is committed as `c4248b4`: `gowork_state.py`,
  its tests, activation-map/demo compatibility and
  `docs/worker-lifecycle-review-2026-09-28.md`.
  `8021f53` preserves successful build-parent history and adds retryable
  session-archive state with reopen/version race protection. Regression cases
  cover those contracts and were included in full `make verify` (**6,200 passed,
  five known warnings, zero errors in 557.98s**). Exact limits and uncompleted
  end-to-end work are in `docs/session-close-recovery-2026-09-29.md`.
- Jester main: `/home/drewp/main-projects/jester-voice`, clean at
  `f804d995b6dfed88d9695b9d6f15d10506839b2f`.
  Functional commits requiring review: `5653e5e`, `e53aa95`, `5f16221`.
  EBI handoff commits requiring review: `726ecaf`, `48f67d5`.
- New isolated Jester candidate:
  `/home/drewp/.local/state/ccdb/session-wt/jester-stabilization-20260928`, branch
  `fix/jester-stabilization-20260928`, HEAD `bc10c47fe05c48b9aa088aa4ea7ad04d5c2b7045`,
  based on f804d995. Contains five
  local regression fixes described below. `node_modules` is an untracked symlink
  to main's installed dependencies; never stage it. No dependency installation.
  Initial coverage ledger and detailed review: `STABILIZATION-REVIEW-2026-09-28.md`
  in that worktree. Full original-commit review and ledger expansion remain open.
  Checked local fix commits: `8185665` (JV-01 presence), `ff252bb` (JV-02 safe
  simulations), `0892200` (JV-03 follow-on validation), `7c5769a` (JV-04 log drain).
  These are not on main and not pushed. Local commit `bc10c47` adds the action-journal
  failure/recovery repair. The only untracked item should remain the `node_modules`
  symlink; do not stage it.
- Main EBI plan: `openspec/changes/stabilize-session-reliability/` (untracked).
  Proposal, design, three specs and tasks exist. Combined Jester owner-V1 coverage
  added and strict validation passed; 29 tasks, five complete (1.1, 1.2, 2.1, 3.3, 5.4).

## What has actually been checked

- Fresh read-only check: both `jester-voice.service` and
  `ebi-agent-chat-relay.service` active; no claims; 100 returned session rows all
  history/idle. This is a limited snapshot, not a complete Discord inventory.
- API base `http://127.0.0.1:9876`; `/api/lounge`, `/api/claims`, and
  `/api/jester/sessions?limit=100` are the diagnostic endpoints used. In this shell
  CCDB_API_SECRET is absent and local GET requests succeeded without it. Never
  print credentials if configured. Avoid `/api/sessions` for read-only diagnosis:
  the current implementation can allocate voice labels while listing.
- Earlier candidate `make verify`: exit 0, 6,145 passed, 19 warnings, approximately
  98 seconds. Ruff/type checks passed BUT an unhandled CollisionWatch background
  exception printed (`await bot.wait_until_ready()` on a MagicMock). This is NOT
  a clean release gate. At that checkpoint coroutine warnings were unresolved;
  their later attribution and repair are recorded below.
- Focused resume/fixture tests: nine passed. These results predate further edits.
- Earlier Jester handoff reports 133 Node + 7 Python tests, 16 simulations,
  a 20-minute speech soak, and one disposable live task. These are inherited
  results, not newly rerun here and not proof of a real microphone round trip.
- No service restarted, no live records changed, no paid agent/model invoked.
- New identity slice: `tests/test_event_session_integrity.py` first reproduced
  four failures (two success controls passed) in 0.74s. `_on_complete` now ignores
  error-echo IDs when persisting/in-memory updating native identity. Focused tests:
  `uv run pytest -q tests/test_event_session_integrity.py tests/test_event_processor.py
  tests/test_event_processor_surface.py tests/test_cross_backend_handoff.py
  tests/test_backend_settings.py tests/test_run_helper.py`: **196 passed in 8.49s**.
  These two files are now locally committed as `bd518f5`. This fixes a proven error
  path, NOT the unproven original overwrite or every stale-success race. Full
  scenario task 2.2/2.3 stays open; no live binding was repaired.
- Runtime observation: EBI PID139188 started 19:07:58 CDT; Jester PID89557 started
  18:33:57 CDT. Current health does not itself expose immutable boot commit identity.
- Pre-gate EBI candidate run including identity slice: `make verify` exited 0,
  **6,151 passed, 19 warnings in 120.90s**, formatting/lint/pyright passed. Two
  CollisionWatch unhandled exceptions and unawaited-coroutine warnings remain.
  That was NOT a clean release gate, so no EBI commit was made at that checkpoint.
- New Jester main baseline: 133 Node tests + 7 Python tests passed. Candidate with
  JV-01/02/03: **138 Node, 7 Python (9.08s), 16/16 isolated simulations passed**.
  Final candidate including JV-04: **139 Node tests passed (6.12s), 7 Python tests
  passed (6.73s), 16/16 isolated simulations passed**. No turn-log ENOENT after
  the drain fix; expected injected outage messages and MockTimers experimental
  warning remain visible. `git diff --check` passed. Jester's shared Lefthook hook
  has no project config and reports that; verification was run explicitly.
  Main has no saved human sim/results/review.json feedback file.
- Scoped EBI security check of event_processor reports seven S101 assertions;
  identical findings were reproduced on HEAD before the patch. No new subprocess,
  environment or credential handling in the identity patch. This is NOT a completed
  security audit of the broader candidate. The broader dirty changes still need review.
- Async-gate reproduction: five child-pytest probes reproduced false success
  for dropped/retained task crashes, callback crashes, unawaited coroutines and
  async fixture-teardown crashes (**5 failed, 2 controls passed in 7.66s**).
  Test-only task/handler instrumentation plus targeted fatal warning categories
  made all seven probes pass (**7 passed in 8.40s**). Awaited errors and cancellation
  remain allowed. Two extra cancellation-shutdown/retrieved-error probes were added.
- The stricter focused run exposed **1 failure and 32 teardown errors** in 178 tests:
  23 CollisionWatch errors from a synchronous `wait_until_ready` bot double, six
  upgrade tests dropping `communicate()` coroutines through fake `wait_for`, three
  runner timeout tests dropping `wait()` coroutines, and one async `stdin.write`
  double. Narrow fixture fixes preserve the production behavior. The next focused
  gate/suites run passed **185 tests in 22.71s** with no warnings.
- First strict full `make verify`: formatting/lint/types passed; **6,160 passed,
  5 warnings, 6 teardown errors in 449.39s**, exit 2. Five slash-upgrade fixtures
  also discarded `communicate()` via fake `wait_for`; the question-timeout fixture
  discarded `Queue.get()` the same way. Candidate removes the former mock and uses
  a real zero-deadline timeout for the latter. No application fix was needed for
  these attributable fixture faults. The full suite now costs about 7.5 minutes
  because per-test garbage collection exposes otherwise delayed coroutine errors.
  All nine meta-probes passed. This failed run was superseded by the clean run below.
- Final strict `make verify` passed: formatting, lint and pyright clean;
  **6,160 passed, 5 warnings, zero errors in 507.62s**, exit 0, with no unexplained
  async failures. The five warnings are an intentional duplicate ZIP member and
  four discord.py positional-argument deprecations. The gate costs 7.5–8.5 minutes
  in the two observed full runs; focused checks remain preferable between edits.
  Seven-file focused recheck: **229 passed in 30.41s**, no warnings. Identity-only
  check immediately before its commit: **6 passed in 1.72s**. Both commits contain
  code covered by this full candidate run; the other dirty candidate code was also
  present, so do not call clean HEAD alone a verified release integration.
- Task 5.4 is complete; OpenSpec strict validation passed afterward. Scoped
  security review found only benign test assertions/trusted argv execution in the
  gate and the same seven pre-existing internal-state assertions in event_processor.
  Neither the security scan findings nor the five warnings were hidden. The shared
  Lefthook hook reported no config; explicit checks ran before both local commits.
- Last read-only check: both services active, claims empty, lounge unchanged at
  the 19:39 handoff, and 100 returned snapshots all history. No restart/live repair.
- Tag-integrity RED: **14 failed in 3.56s**, including the real API with `limit=1`,
  storage write/delete/read failures and archived/closed holder behavior. Candidate
  now uses explicit release authority plus shared single/bulk allocation, consults
  all stored holders' lifecycle evidence, and never reports an unpersisted name.
  Focused checks: **259 passed in 46.28s**, one intentional ZIP warning; 100%
  statement coverage of both tag modules. Additional observability/workflow/lounge
  checks: **102 passed in 50.60s**. Full `make verify` passed: formatting/lint/types
  clean, **6,176 passed, five explained warnings, zero errors in 525.29s**, exit 0.
  Public imports and diff checks passed. Local commit `8406035` contains the batch;
  the hook again reported no config, with explicit verification already complete.
  Task 3.3 is complete; 3.2 stays open for remaining worker adoption/failure paths.
  The run included the remaining dirty candidate, not clean HEAD alone. Details
  and interleaving limits are in the report. No live tags were changed.

## Newly reproduced Jester defects

- JV-01: dismissal saved by `leave` survives restart with owner absent, suppressing
  autojoin on the next visit. RED observed true instead of false. Candidate clears
  ended-visit dismissal during absent startup; same-visit behavior is preserved.
- JV-02: main's simulator uses Presence defaults and its fake leave writes the
  live operational presence file. RED intercepted the boundary without writing,
  proving the unsafe target path. Candidate uses per-run/per-case mkdtemp state.
  **Do not run main's unpatched simulator.** Historical live impact is unknown.
- JV-03: single-source dependencies dispatch on invalid completion timestamps,
  block on invalid failure dates, or permanently block on temporary failures.
  Three independent RED cases; candidate requires finite newer times and terminal
  failure. Focused dependencies/watcher/simulator tests: 18 passed.
- JV-04: Conversation.close returns before queued evidence writes finish, producing
  observed ENOENT after test cleanup and risking lost shutdown evidence. Controlled
  queue-gate regression failed; candidate awaits its log queue before returning.
  Final full checks passed and the fix is locally committed; no live activation.

## Confirmed incidents and uncertainty

1. REL-01: Zoro's row pairs backend `claude` with old conversation ID
   `01a0e5ff-6bfe-7263-9680-4af9f23adffd`; Claude rejects it. Thread
   `1553779983158349925`, folder `/home/drewp/main-projects/drews audit`.
   Journal September 28 CDT: backend set 18:44:33; Codex-to-Claude handoff
   recognized 18:54:33; bounded transcript injected 18:54:34; new Claude ID
   `70615534-f45b-420b-b2f7-4ec8e56fa9d5` at 18:54:36; errors on subsequent
   launches at 19:07:19, 19:31:44, 19:32:04, 19:32:43 and 19:47:44.
   The new Claude transcript exists under `.claude/projects/-home-drewp-main-projects-drews-audit/`
   and has the correct ID throughout; last timestamp 00:07:13Z September 29.
   The user explicitly closed this session at 19:52:46. Do not reopen or repair it
   without approval. First incorrect overwrite remains UNKNOWN: investigate the
   interval after new init and before the first failed resume, not just the last error.
   Live `EventProcessor._on_complete` persists an echoed ID even for an error;
   this mechanism is reproduced and repaired locally in `bd518f5`, but it is not
   proof of the original writer and has not been deployed.
2. REL-02: worker archive paths do not close EBI rows. Earlier DB inventory found
   23 open Go Work records and eight worker-held voice tags. Only 15 worker archive
   states were individually verified; do not label all 23 safe cleanup targets.
   Discord visibility/auto-archive is not logical closure; user closed their threads.
3. REL-03: a saved explicitly closed task loop resumed at restart and spawned work.
   Saved loops lack durable waiting checkpoints in live code. Do not restart yet.
   Loop store: `/home/drewp/.local/state/ccdb/gowork-loops.json`.
   Closed build parent `1554146845415055445`; finished waiting parent
   `1554145503506333736`. Preserve copies and ledgers.
4. REL-04/05: verification missed asynchronous crashes; old live-check assumes the
   previous voice integration and tags for all open sessions; health does not prove
   immutable loaded revision. Fresh Python imports resolve main EBI but that alone
   does not prove what the running service imported.
5. REL-06: Jester real-room trial and five-commit review incomplete. One DAVE audio
   decrypt warning at 19:29:40 has no established cause. Earlier scope notes describe
   older failures that may have been fixed; replay before calling them current bugs.

## September 29 follow-up — current checked candidate state

The Jester candidate has a new local commit `bc10c47` (`Serialize durable owner
action journal updates`). Four new temp-file/fake-EBI regressions first reproduced
concurrent prepare returning before the ID hit disk, retrying after a failed save,
failed finish exposing an unsaved `posted` state, and a real spoken POST occurring
without durable action identity. The repair serializes journal decisions/writes,
swaps in-memory state only after the atomic rename succeeds, and lets later writes
recover while the failed caller still receives its error. Candidate checks passed:
**143 Node tests, seven worker Python tests, and 16/16 isolated simulations**.
The action trace/receipt checks remain offline evidence, not a live room trial.
Jester candidate HEAD is `bc10c47fe05c48b9aa088aa4ea7ad04d5c2b7045`; only its
`node_modules` symlink should be untracked.

The EBI service lifecycle got a new real-SQLite test-first slice. Eight initial
tests failed (four successful build-parent endings leave the record open/delete
the conversation; archive failure is not retried after reconstructing the service;
an old wrap-up closes a reopened/newly-closing row). Another test separately proved
independent services can unarchive then be followed by a stale archive. Candidate
adds `lifecycle_version` compare-and-set, durable `archive_pending`, bounded
startup retry, and a shared per-database/thread lock ordering surface effects. It
also changes successful parent endings to close while preserving their Discord
conversation. Service/lifecycle focus passed **109 tests**, then lifecycle/storage
recheck passed **81**, the broader parallel subset passed **607** before one fixture
teardown error, and the corrected fixture plus archive tests passed **13**.
Explicit core/service pyright and focused security-rule scan were clean. Full
the dirty candidate `make verify` passed **6,200 tests, five known warnings, zero
errors in 557.98s**; Ruff format/lint and pyright passed. A focused Ruff security
scan found 19 task-loop internal S101 assertions vs 18 at candidate HEAD (one new
`_wait_verdict` invariant); existing S110/S112 counts were unchanged. Public
imports and `git diff --check` passed. Candidate local commit `8021f53` contains
the full checked slice; it remains unpushed and undeployed.

Evidence is `docs/session-close-recovery-2026-09-29.md` in the EBI candidate and
`STABILIZATION-REVIEW-2026-09-28.md` in the Jester candidate. Important limit:
task-loop `_close_worker_thread` still edits Discord outside the new durable
surface retry. The whole-build → process reconstruction/reconciliation test is
not written yet. Legacy combined-plan acceptance evidence and retained-work
recovery also remain open. These are not complete OpenSpec items; the plan stays
at five of 29 complete. No runtime service, database row, live thread, paid model,
external agent, or remote repository was touched in this follow-up.

## Existing candidate — review before adopting

Worker creation now passes explicit voice ineligibility under a shared tag lock;
worker completion uses SessionLifecycleService; tag release checks stored holders
outside the current API page. Waiting/verdict checkpoints are persisted and restored
before a model call, and explicitly closed parents are skipped during recovery.

September 29 reproduced the premature parent close with real temporary SQLite:
the build was closed before the verdict wait, so normal questions would be refused.
That candidate-only regression is repaired locally: review archival does not close
the parent. Four more real-Git/SQLite regressions proved legacy subthread deletion,
failed/conflicted worktree loss and retry-name reuse. Candidate now retains failed
work/open threads, uses unique side-copy names and stops deleting successful
legacy subthreads. These five regressions failed first; **133 related tests passed**.

The manifest ledger also offered blocked/unaccepted/unknown earlier workers for
archive. Six RED cases now pass; accepted-history authority is carried separately
across attempts. **48 ledger/lifecycle/manifest tests passed**. Broad Go Work check
had **385 passed / two stale-expectation failures**, repaired in activation-map/demo
checks. Their rerun passed six tests but printed one child-process closed-loop
diagnostic; a three-test demo repeat did not. Exact cause remains unconfirmed.
Full `make verify` then passed **6,188 tests, five known warnings, zero errors in
535.35s**, formatting/lint/types clean. No child-process diagnostic recurred in
that run. Ledger-only pyright and public imports passed. Optional security scans
found only the documented baseline/internal-state findings, not new command or
credential boundary issues. The ledger/demo slice is local commit `c4248b4`;
broader task-loop safeguards remain uncommitted. The gate included those dirty
changes, so it is not proof of a clean release integration. The shared hook
reported no Lefthook config; verification was explicit. See
`docs/worker-lifecycle-review-2026-09-28.md` for exact evidence and limits.

Still incomplete: connect the shared archive outbox to Go Work without locking
workers or releasing a tag before storage succeeds; test a completed whole build
through actual cog reconstruction after `_drive` removes its loop record; make
legacy successful-group closure wait for durable acceptance evidence; and keep
ambiguous/invalid checkpoints paused safely. Inspect the `retry_archives`
task-vs-thread count mismatch and attribute the intermittent subprocess shutdown
diagnostic. No whole lifecycle task is complete and no blind close-by-directory
cleanup is authorized.

Confirmed additional REL-02 mechanism, now repaired in the local candidate
(synthetic reproduction only, no live writes):
`assign_labels([999], {1: first_label, ..., 10: last_label})` with ten explicitly
open synthetic holders returns `released={1}` and mints the first holder's label
for 999. The old bulk path passed only the requested views to this allocator,
and `ApiServer.list_sessions` builds those views from `list_all(limit)`.
A paginated listing could therefore reclaim an open off-page holder, despite
48f67d5's single-spawn fix. The tag-integrity batch adds the real-SQLite/API
regression, deliberately replaces the old reclamation-policy test, and covers
ordinary closed, archived/open, reopened and unknown off-page holders. Release
requires successful deletion before reuse. Worker exclusion and remaining
interleavings still need their separate complete task evidence.

A second injected-failure probe used only fake settings/Discord objects:
`VoiceTagger.tag_thread` suppressed an `OSError` from `settings.set`, returned
`luffy`, and attempted one rename despite never storing the assignment. This is
confirmed synthetic behavior, not an observed live storage outage. The same batch
adds single/bulk write-failure and retry regressions; a promised label is now
durably assigned before it is returned/shown. Failures are logged, not hidden.

## Jester is part of the product, not an optional afterthought

Read in Jester: `HANDOFF.md`, `SCOPE-jester-owner-v1-before-trial.md`,
`REPLACES_OLD_VOICE.md`, `RESUME-HERE.md`, last four paragraphs of `sim/STATUS.md`,
`LIVE-CHECK-jester-owner.md`, `sim/README.md`. Recent owner decisions control.
Current V1 is owner conversation/control; guests may be transcribed and pause
recording but guest conversation/control/grants are explicitly deferred.

Coverage must include presence/intentional leave; dormant/engaged attention and
side conversations; natural follow-ups; barge-in/actually played words; faithful
single dispatch and corrected targets; stable session identity and receipts;
create/backend/model selection; just-listen/mute/recording privacy; transcripts,
retention and downstream allwork format; persisted once-only watches; restart,
dependency failures, DAVE and bounded audio resources; latency and real-room feel.
Map each requirement to source, implementation, exact test and evidence state.
Text simulations use fake Discord/Luna/EBI/audio boundaries and cannot prove audio.

Jester `scripts/check.sh` can install dependencies if absent. Inspect installed
dependencies and invoke local `node --test` / existing Python environment directly
instead; no implicit install. `node sim/run.mjs` writes result files but calls no
paid backend; use an isolated Jester worktree and preserve human review feedback.
Do not change downstream allwork or resurrect the old voice service.

## Next checked batches

1. Continue from the validated combined plan and initial Jester coverage ledger.
   User approved local execution; OpenSpec's EBI-local context does not authorize
   unrelated projects or Ebi workers. Five of 29 whole tasks complete; do not tick
   broad tasks merely because a candidate slice passes.
2. Finish EBI identity sequence/stale-operation coverage around the now-reproduced
   error-echo repair. First historical overwrite remains unknown. The async gate
   and attributable fixture failures are fixed and committed; continue from the
   clean strict result, not the older false-positive pass.
3. Continue from the checked tag-integrity repair, not the old allocator. Review
   worker exclusion-write failures and other tag-writer interleavings. Review
   existing lifecycle/tag/restart patches. Successful-parent history and shared
   close/archive retry state are now locally committed in `8021f53`; finish worker
   adoption, whole-build process-reconstruction evidence and legacy recovery.
   Premature review closure and failed legacy-copy loss were reproduced and
   repaired; verify from their report instead of repeating them. No live cleanup
   or restart.
4. Finish Jester's three-functional-commit review and per-requirement coverage;
   five local repairs and an initial evidence ledger now exist. Continue from the
   residual review leads, not from scratch. Do not run main's unsafe old simulator.
5. Strengthen read-only diagnosis and runtime identity;
   adversarial review + security-audit + parallel `make verify` before local commits.
6. Prepare an exact reversible repair manifest and rollout/rollback checklist.
   Request bounded approval for deployment/live repair; microphone trial needs Drew.

Borrowed methods (no plugins installed): architecture/ranked evidence from
`github.com/ksimback/tech-debt-skill`; fault/sequence checks from
`github.com/shenli/distributed-system-testing`; reproduce/trace/test/repair from
`github.com/obra/superpowers/tree/main/skills/systematic-debugging`.

## Short prompt for Claude Code

Continue Jester + EBI stabilization. Read this handoff and both candidate reports
before coding. You are authorized to keep making local fixes and checked local
commits without asking Drew for each routine choice. Continue through the
OpenSpec change and Jester owner-V1 ledger in small test-first batches; save a
short evidence update after every batch and keep going until all authorized local
work is complete. Do not stop after one green suite or five of 29 tasks. If a hard
context, tool or credit limit prevents continuing, save the exact state and next
command here so another Claude Code session can resume. Do not claim a process is
running overnight unless you actually launched and verified it.

Preserve both candidate worktrees and unrelated changes. Do not restart services,
change live data, deploy, publish, install dependencies, make live Discord/API
writes, dispatch EBI/Gowork workers, or run paid runtime-model tests; those actions
still need approval. Local coding work, simulations, tests and small local commits
are approved. Five of 29 OpenSpec tasks are complete; EBI archive recovery and
Jester JV-01..05 are in the reports. Continue the whole-build/new-cog archive test,
wire worker closure into durable retry safely, finish legacy recovery and review
the five unreviewed commits. Keep a full V1 evidence ledger and report live
microphone/performance gaps honestly. Do not deploy candidate code.
