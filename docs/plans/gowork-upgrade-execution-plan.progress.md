# Go Work Upgrade Progress

## T01 — Stable master and child plan identities

- Added a versioned plan-tree manifest with stable IDs, parent links and canonical project paths.
- Preserved legacy checkbox plans through a deterministic single-plan compatibility path.
- Added round-trip fixtures plus clear failures for duplicate IDs, unknown parents and parent cycles.
- Implementation commit: `181dc76`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (289 passed), focused lint,
  formatting, pyright, import and security checks, and the full project suite (3462 passed after
  removing three live Discord routing variables from the test environment).
- Open: nothing for T01; T02 can build complete task assignments on this identity model.

## T02 — Complete worker task assignments

- Added a complete task contract with stable task and plan/version identities, dependencies,
  file and resource ownership, required inputs, expected output, acceptance check and source
  requirement.
- Added manifest parsing/rendering, task lookup helpers and clear rejection of incomplete or
  duplicate task definitions and assignments tied to the wrong plan version.
- Preserved old checkbox plans by deriving deterministic task records and serializing them on
  one shared legacy resource; the old format is never treated as safely parallel.
- Implementation commit: `bbf51be`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (304 passed), focused lint,
  formatting, pyright, import and security checks, and the full project suite (3477 passed after
  removing the three live Discord routing variables from the test environment).
- Open: the repository-wide Ruff security selection still reports 62 pre-existing findings;
  the touched parser passes its focused security check. T03 can now validate dependency and
  ownership safety without changing this assignment format.

## T03 — Validate dependencies and ownership

- Finished the validation started in `b9773d3` (David's computer, 2026-09-21): unknown, self
  and duplicate dependencies, dependency cycles (the cycle is named), unsafe owned paths
  (absolute, drive, `~`, `..`, `.git`, control characters, backslashes), cross-project
  dependencies that name no required input, duplicate/unknown/uncovered agreed outcomes.
- Overlapping ownership is a pairwise `OwnershipConflict` (`PlanTree.ownership_conflicts()`,
  `can_run_together()`), so a conflict blocks only that pair's simultaneous dispatch, never
  the whole plan; owned paths are compared per project and patterns compare by literal prefix.
- Legacy checkbox plans stay valid without declared outcomes.
- Tests: `tests/gowork_upgrade/test_plan_validation.py` (24 cases; the parametrised invalid
  fixtures each assert their specific message), fixture `validated-plan.md`.
- Implementation commits: `b9773d3` (code + tests, on the base) — no further code change was
  needed; this entry records the verification the interrupted step never wrote.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (329 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_plan.py` (0 errors).
- Open: nothing for T03. T04 can key LoopStore by stable build identity.

## T04 — Store multiple builds without overwriting runs

- `LoopRecord.build_id`: stable build identity, derived as `thread-<worker_thread_id>` when a
  record has none (legacy files therefore load with the same id on every read, no write).
- `LoopStore.save`/`remove` key by build id; `remove()` still accepts a repo path for callers
  that predate ids; `get()`, `for_repo()` added; `migrate()` writes derived ids to disk and is
  repeatable (returns how many records lacked one; a second run returns 0 and changes nothing).
  Writes stay atomic (tmp + replace; a failed replace leaves the old file intact — tested).
- Cog: `_Running.build_id`, every "forget this build" call uses the id, `resume_all()`
  migrates first, step records now carry `"build"`.
- Tests: `tests/gowork_upgrade/test_loop_store_identity.py` (7), `tests/test_task_loop_cog.py::TestResume::test_a_legacy_record_without_build_id_is_migrated_and_resumed`;
  `tests/test_loop_store.py` fixture corrected (two projects never share one worker thread).
- Implementation commit: `0c0cd59`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (337 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/loop_store.py claude_discord/cogs/task_loop.py` (0 errors).
- Open: the cog's in-memory `_running` map is still keyed by repo path (one running build per
  project); T11 replaces that running identity when dispatch moves to the coordinator.

## T05 — Persist task attempts and acceptance evidence

- New `claude_code_core/gowork_state.py`: `open_build_state(path, tree, build_id=…)` creates or
  reopens one ledger per build (refuses another build's file). Each task has one current
  `TaskAttempt` — attempt number + stable `attempt_id` (`<build>:<task>:<n>`), plan id and
  version, owned files/resources, status (pending → running → finished → accepted, or blocked),
  result commit, checks, review lines, repair count, `accepted` flag, reason.
- Transitions are explicit and refuse anything stale: `submit_result` is idempotent for the same
  result, refuses a different result for a finished attempt and refuses any non-current attempt;
  `accept` needs a finished current attempt whose plan version still matches
  (`note_plan_version` records a mid-build change; `retry` mints the next attempt at the new
  version); an accepted task cannot be blocked. Writes are whole-file temp+rename; a stray temp
  file from an interrupted write is ignored on reopen.
- Design note: this is the core (shipped) counterpart of the Lockin extension's
  `run_state.py`; `claude_code_core` cannot import `extensions/` (not in the wheel), so the
  ledger lives in core and follows the same discipline rather than importing it.
- Tests: `tests/gowork_upgrade/test_task_state.py` (11).
- Implementation commit: `9764ca2`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (348 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_state.py` (0 errors).
- Open: nothing wires the ledger into the running loop yet — that is T11's dispatch step;
  T06 can select ready tasks from `BuildState.accepted_tasks()` plus the plan's dependencies.

## T06 — Select ready tasks across child plans

- New `claude_code_core/gowork_schedule.py`: `ready_tasks(state, running=…, limit=…)` returns
  `ReadyTask(task_id, plan_id, project_path)` tuples in plan order. Pure and deterministic.
- A task is ready only when pending, every dependency is *accepted* at the plan version that is
  current now (`BuildState.note_plan_version` makes an earlier acceptance stale), and none of its
  owned files/resources overlap a running task or one picked earlier in the same answer
  (`PlanTree.can_run_together`). Cross-repository dependencies use the same test.
- Tests: `tests/gowork_upgrade/test_ready_tasks.py` (9): website waits for product while
  marketing proceeds; finished-not-accepted, blocked and stale dependencies release nothing;
  overlaps exclude the later task; running/selected tasks are never offered; bounded by `limit`.
- Implementation commit: `496b3b0`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (357 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_schedule.py` (0 errors).
- Open: T07 measures resource pressure; the `limit` argument is where T08–T10 plug adaptive
  capacity in.

## T07 — Sample host and worker resource pressure

- New `claude_code_core/gowork_resources.py` (standard library only — no psutil in the
  dependency set, and a justified small adapter beats a new runtime dependency):
  `ResourceSnapshot` (memory total/available/used, swap used, cpu count/load, disk free,
  cgroup v2 memory/cpu limits) with `missing` naming every unreadable field and
  `effective_*` properties that respect container limits; `pressure_of()` → healthy /
  constrained / critical / **unknown** (any missing reading is unknown, never healthy);
  `workers_that_fit()` → `None` when memory is unknown (callers must not read that as room);
  `WorkerPeaks` tracks each worker's observed peak including descendants and sizes new
  workers for the biggest recent one (default 1500 MB before any observation);
  `HostProbe` reads `/proc/meminfo`, `/proc/loadavg`, `/sys/fs/cgroup/{memory,cpu}.max`,
  `shutil.disk_usage`, `GlobalMemoryStatusEx` on Windows, and sums VmRSS over a process
  tree via `/proc` (`None` on Windows — reported, not guessed).
- Tests: `tests/gowork_upgrade/test_resources.py` (9): injectable healthy / constrained /
  critical / unavailable / partial snapshots, container caps, descendant peaks, and the real
  probe returning a snapshot whose unreadable fields are named.
- Measured on David's machine: 31 371 MB total, 7 727 MB available, swap and load unknown →
  pressure `unknown`, which is the conservative answer T09 must handle.
- Implementation commit: `822ac82`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (366 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_resources.py` (0 errors).
- Open: T08 builds the admission controller on `workers_that_fit` + `WorkerPeaks`.

## T08 — Arbitrate shared capacity with fair admission

- New `claude_code_core/gowork_admission.py`: `AdmissionController(path, capacity=…,
  review_reserve=…)`, one per host, shared by every build. `reserve(kind, build_id,
  unblocks=…)` is an async context manager for `task`, `review` and `chat` slots.
- Atomic on the event loop: racing requests never exceed capacity; a queued waiter that is
  cancelled leaves the line; a holder that is cancelled releases its slot (even when admitted
  and cancelled in the same tick). Reviews may use every slot, tasks only
  `capacity − review_reserve`, so a review never queues behind the workers it judges. Chat is
  counted in `held` (it shrinks what tasks may take) but is admitted immediately, always.
- Fairness: among waiters, reviews first, then the task that unblocks the most other work,
  then arrival order — unless a build has been passed over `AGE_LIMIT` (3) times, in which case
  its request goes next. `admitted` / `passed_over` / `last_admitted` per build are written to the
  JSON file on every change and reloaded on construction, so fairness survives reopen.
  `set_capacity()` (for T09) never evicts a holder; it only changes what new requests see.
- Tests: `tests/gowork_upgrade/test_admission.py` (7): racing, cancellation of held and queued
  reservations, review reserve, chat accounting, ageing, fairness after reopen, capacity change.
- Implementation commit: `7f89cdb`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (373 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_admission.py` (0 errors).
- Open: OS/chat/coordination headroom is expressed as the capacity T09 computes from T07
  snapshots and hands to `set_capacity()`; T10 makes real process starts reserve here.

## T09 — Adapt admissions to measured resources

- New `claude_code_core/gowork_capacity.py`: `CapacityPolicy(start=2, ceiling=32,
  growth_step=2, headroom_mb=4096, cooldown_ticks=3, operator_limit=None)`.
  `decide(snapshot, peaks, held=…)` → `CapacityDecision(capacity, measured_capacity,
  external_limit, pressure, pause_starts, cancel_up_to, reason)`.
- Healthy: grow by at most `growth_step` per tick, never past `held + workers_that_fit(…)`
  at the observed typical worker size, never past the ceiling. Constrained: pause new starts,
  capacity = what is running. Critical: pause and `cancel_up_to=1` per tick (never the last
  worker), capacity shrinks by one. Unknown (missing readings): hold at
  `max(start, min(current, held))`, no growth, no pause. After any pressure, a cooldown of
  healthy ticks passes before growth resumes, so the capacity is monotone through recovery.
  `set_external_limit()` keeps an operator/provider constraint apart from the measurement and
  applies it last; both numbers are reported.
- Tests: `tests/gowork_upgrade/test_capacity_policy.py` (8): growth past ten on a healthy
  host, peak-bounded fit, constrained pause, critical shedding to one, cooldown without
  oscillation, unknown-data hold, external limit, ceiling.
- Implementation commit: `06dd84a`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (381 passed), `ruff check`,
  `ruff format --check`, `pyright claude_code_core/gowork_capacity.py` (0 errors).
- Open: T10 wires HostProbe → CapacityPolicy → AdmissionController.set_capacity into the
  real process starts (`_run_helper.py`, setup defaults, the task-loop cap).

## T10 — Connect adaptive capacity to actual process starts

- `RunConfig.slot_kind` (`chat` | `task` | `review`), `slot_build_id`, `slot_unblocks`.
  `ClaudeChatCog.run_fresh_turn(..., slot_kind=, slot_build_id=)` passes them through;
  the Go Work cog marks worker rounds and side-thread workers `task` and the build's own
  check/review sessions `review`, with the build id for fair admission.
- `_run_helper.py`: `configure_adaptive_limit(controller=, policy=, probe=)`,
  `tick_capacity()` (sample → `CapacityPolicy.decide(held=…)` → `set_capacity`),
  `run_capacity_ticks()`, `session_limit()` now reports the live adaptive capacity, and
  `parallel_limit()` replaces the loop's fixed ten. `run_claude_with_config` checks the explicit
  semaphore first (unchanged behaviour when `MAX_CONCURRENT_SESSIONS` / `max_concurrent` is
  set), otherwise reserves one admission slot per run and releases it in `finally`
  (cancellation while queued leaves nothing behind; the same run never reserves twice — the
  compact rerun reserves again only after its first reservation was released).
- `claude_code_core/task_loop.py`: `TaskLoop(max_parallel=callable)`; default still ten.
- `setup.py`: an explicit number → fixed semaphore (logged as "fixed"); otherwise
  `CapacityPolicy()` + `AdmissionController(<gowork state dir>/gowork-admission.json,
  review_reserve=1)` + `HostProbe()`; `TaskLoopCog.cog_load` starts the 15 s tick loop and
  `cog_unload` cancels it.
- Fixed a same-tick race in the controller: a waiter cancelled in the tick a slot frees up
  still sat in the line with a cancelled future; `_wake` now prunes done futures first.
- Tests: `tests/gowork_upgrade/test_adaptive_starts.py` (6): fake process integration
  passes ten workers only after healthy ticks admitted it; critical pressure pauses new
  starts and recovery admits them; a cancelled queued worker clears the registry and the
  line; chat starts while workers wait; reviews use the reserved slot; an explicit limit keeps
  the fixed semaphore. 16 `run_fresh_turn` fakes in `tests/test_task_loop_cog.py` accept the
  slot keywords. `tests/test_feature_scheduler.py` read the live parallel-gowork plan, whose
  C1 ticks changed its ready set — expectation updated.
- Implementation commit: `9e29f16`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (387 passed), full suite
  `scripts/test-clean-env.sh` (4588 passed, 35 skipped after the scheduler fix), `ruff check`,
  `ruff format --check`, `pyright` on every touched file (0 errors). Security audit of the
  touched run path: no new subprocess or shell use; `slot_kind` is normalised to the three
  known values; the admission file path derives from `CCDB_GOWORK_STATE` like the loop store.
- Open: `_worker_peaks` is not yet fed from real worker RSS (T13/T22 can observe peaks when
  workers finish); until then sizing uses the 1500 MB default.

## T11a — Running identity by build

- `TaskLoopCog._running` is keyed by `build_id` (was the project path); `_starting` holds
  (project, plan name) pairs. `_running_in(repo)`, `_running_plan(repo, plan)`, `_busy()`.
- `start_loop`: the same plan already running → `BuildAlreadyRunningError` carrying
  `.thread`, `.thread_id`, `.build_id` (message: "`PLAN.md` is already running in <#…>");
  a manifest plan (`has_manifest()` in `gowork_plan.py`) may start beside any other build in
  the project; a checkbox plan keeps the one-per-project rule. A manifest plan needs no
  `- [ ]` lines to start. `_close_for_switch` never closes anything for a manifest plan and
  only ever closes the project's checkbox build. Queue, resume and the `finally` cleanup use
  the build id.
- Tests: `tests/gowork_upgrade/test_running_identity.py` (5): two manifest plans in one
  project at once (two threads, two store records); a repeated start names the existing
  build and opens nothing; ending one build leaves the other running and stored; checkbox
  plans keep one build per project; a manifest plan starts beside a checkbox build.
- Implementation commit: `4db3f27`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (392 passed),
  `tests/test_api_server.py` (102 passed), `ruff check`, `ruff format --check`,
  `pyright claude_discord/cogs/task_loop.py` (0 errors).
- Open: T11b makes a manifest build dispatch its tasks from the ledger; until then a
  manifest plan runs its checkbox lines like a legacy plan.

## T11b — Manifest dispatch

- Core (`claude_code_core/task_loop.py`): `TaskLoop(manifest_dispatch=, state_path=,
  build_id=)`; `run()` takes the manifest path when the plan has a `gowork-plan` fence.
  `_run_manifest()`: load tree → open ledger → `ready_tasks(limit=max_parallel())` → `begin`
  each → dispatch → `submit_result` + `accept` (result commit + checks) or `block` (reason) →
  repeat; COMPLETE when all accepted, STUCK naming the blocked tasks, invalid manifests stop
  with the validation message. `ManifestResult` carries task id, ok, detail, commit, checks.
  `manifest_worker_prompt()` gives a worker its assignment (outcome, owned files/resources,
  inputs, expected output, acceptance check, source requirement; T12 turns it into a
  persisted handoff).
- Cog (`claude_discord/cogs/task_loop.py`): `_dispatch_manifest()` — per task: the project's
  work copy (`_project_copy()`: the build's own copy when the project sits in it, else a
  `create_project_copy()` of that repository, made once under a lock), a side copy branched
  from it, a worker thread, `run_fresh_turn(slot_kind="task", slot_unblocks=<dependents>)`,
  then under the build's git lock: side has new work → merge → `head_commit()` is the result
  commit; otherwise the side copy is removed and the task reports why. Ledger lives at
  `<gowork state dir>/builds/<build_id>.json`.
- `claude_code_core/work_copy.py`: `create_project_copy()`, `head_commit()`.
- Tests: `tests/gowork_upgrade/test_manifest_dispatch.py` (6, core with a fake dispatcher:
  two projects progress while the dependent waits; a failed task blocks only its dependents;
  reopening carries on from the ledger; stop; parallel limit; invalid manifest),
  `tests/gowork_upgrade/test_manifest_build_cog.py` (1, real git repo + fake workers: all four
  tasks accepted with result commits, each task's work landed in its project folder inside the
  build's copy, the project itself untouched, three tasks ran at once and the dependent ran
  only after product was accepted), `tests/test_work_copy.py` (+1). T11a's tests were updated
  for the project folders and worker threads the manifest path now creates.
- Implementation commit: `71f90d2`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (400 passed), `ruff check`,
  `ruff format --check`, `pyright` on the three touched modules (0 errors).
- Open: results are accepted on merge (T15/T16 add combined checks and evidence gates);
  merges are serialized in memory per build (T15 makes integration durable); a failed task is
  blocked at once (T18 adds the single repair attempt).

## T11c — Adapters

- `TaskLoopCog.find_running(plan_path)`; `POST /api/loops` answers `200 {"status": "running",
  "thread_id", "build_id"}` for a plan already being built and dispatches nothing;
  `start_asking()` (used by the API's background start and the queue) returns the existing
  thread and posts "ℹ️ … is already running in <#thread> — nothing new was started.";
  the `/gowork` slash command posts the same. Stopping by typed "close"/"stop" already matched
  one build by its worker thread (`take_message`), so it never touches another build.
- Tests: `tests/gowork_upgrade/test_start_adapters.py` (3): repeated POST returns the running
  thread with no start; a new plan still starts (202); `find_running` + the command path report
  the existing thread while the ledger/store hold one build.
- Implementation commit: `1260bc1`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (403 passed),
  `tests/test_queue_api.py` + `tests/test_api_server.py`, `ruff check`, `ruff format --check`,
  `pyright` on both touched modules (0 errors).

T11 is complete (T11a–T11c).

## T12 — Compact persisted worker handoffs

- Handoff contract inspection (read-only, this repo): the bot-to-bot contract is
  `claude_code_core/handoffs/protocol.py` (`HandoffTask`: sender/recipient agent ids, origin and
  reply `ConversationCoordinate`s, `ProjectLocator`, `AuthorityScope`, goal, expected result,
  bounded text, UUID task ids) carried through Discord as a `CCDB_HANDOFF_V1` JSON envelope
  (`claude_discord/handoff_messages.py`) and executed by `handoff_executor.py` for the project
  lookup use case. It moves a job *between two agents*; a Go Work worker is the same bot, one
  build, one attempt, on this host. Re-using the packet would require inventing a sender /
  recipient pair and posting envelopes into worker threads — a second use of the format, not a
  fit. So no envelope is posted and no competing message format exists: the worker handoff is a
  local file that follows the packet's rules (bounded fields, stable identity, no transcript).
- New `claude_code_core/gowork_handoff.py`: `WorkerHandoff` (attempt id, task/plan ids and
  version, project dir, goal, outcome, owned files/resources, required inputs, **input
  evidence** = each dependency's accepted commit and check output from the ledger, expected
  output, acceptance check, source requirement, saved decisions, created_at);
  `plan_decisions()` reads the plan's `## Decisions` list; `build_handoff()` needs a running
  attempt; `persist_handoff()` writes `<builds>/<build_id>/handoffs/<attempt>.json` once and
  returns the existing file on a second delivery; `render_worker_prompt()` is the worker's
  entire briefing (no chat history) and names the saved file.
- Cog: `_dispatch_manifest()` builds and persists the handoff *before* spawning the worker and
  renders the prompt from it; the interim T11b prompt helper is gone.
- Tests: `tests/gowork_upgrade/test_worker_handoff.py` (5: decisions parsing; assignment +
  goal + decisions + evidence; duplicate delivery reuses the file and a retry gets a new one;
  the prompt comes from the handoff alone; a pending attempt is refused);
  `test_manifest_build_cog.py` now checks one handoff file per attempt and that the website
  page's prompt carried product's evidence.
- Implementation commit: `1d164d0`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (408 passed), `ruff check`,
  `ruff format --check`, `pyright` on the touched modules (0 errors).

## T13 — Save and release workers individually

- Core: `TaskLoop(manifest_worker=<one task → ManifestResult>)` replaces the batch dispatcher.
  `_run_manifest()` keeps a map of in-flight worker tasks, tops it up from `ready_tasks()`
  whenever there is room, waits with `FIRST_COMPLETED`, and `_record_manifest_result()`
  persists each result the moment it is known (accept, or block with the reason — a raised
  exception blocks that task with its message; a cancelled worker is recorded as such).
  Cancelling the build cancels the in-flight workers, waits for them, and re-raises; their
  attempts stay `running` in the ledger (T17 reconciles). Termination happens only when
  nothing is in flight and nothing is ready.
- Cog: `_run_manifest_task(running, task)` is the per-task worker; its admission slot is
  released by `run_claude_with_config`'s `finally` as soon as that worker ends; a worker that
  did not finish keeps its side copy (path named in the reason) — uncombined work is retained;
  only a DONE with no new commits removes the empty side copy.
- Tests: `tests/gowork_upgrade/test_rolling_workers.py` (4): a fast worker's dependent starts
  while a slow sibling runs; a raising worker blocks only its task; cancellation keeps saved
  results and running attempts; a saved result survives a crash right after it.
  `test_manifest_dispatch.py` adapted to per-task workers (rounds grouped by start time).
- Implementation commit: `f23a090`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (412 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T14 — Archive completed worker threads without deleting history

- Ledger: `TaskAttempt.thread_id` / `archived`; `BuildState.note_thread()`, `mark_archived()`
  (idempotent), `unarchived_threads()` (settled tasks whose thread is still open). Found and
  fixed while doing this: two `BuildState` handles on one file (the loop's and the cog's) each
  kept their own document and the last writer won; every accessor and mutation now re-reads the
  file first (all on one event-loop thread), with a two-handle test.
- Core: `ManifestResult.thread_id`; `_record_manifest_result` notes the thread *before* the
  result is saved; `TaskLoop(after_manifest_result=…)` runs strictly after the save and can
  never fail the build.
- Cog: the worker thread id travels in the result; `_archive_worker_thread()` archives with
  `thread.edit(archived=True)` (never `delete`), skips the build's own thread, and on failure
  leaves the ledger unarchived; `retry_archives(running)` archives every pending thread and is
  called on `resume_all()` for manifest builds. The pre-existing finish flow still archives the
  build's own thread with its reason once the result card is posted.
- Tests: `test_task_state.py` (+2: thread bookkeeping; two handles never lose writes),
  `test_rolling_workers.py` (+1: the after-save hook sees the saved status and its failure
  does not break the build), `test_manifest_build_cog.py` (+1: every archive saw "accepted" on
  disk, one archive failed and stayed pending, `retry_archives` finished it, no `delete`
  anywhere, no worker rerun).
- Implementation commit: `1ac9121`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (416 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).
- Open: a worker that crashes before reporting leaves its thread unarchived and its attempt
  running; T17 reconciles interrupted work.

## T15 — Serialize task-result combination in review copies

- `work_copy.py`: `merge_side_copy(..., keep_on_clash=True)` aborts a clashing merge, leaves
  the build's copy exactly as it was and keeps the worker's branch + worktree for repair;
  `side_is_merged()` (`git merge-base --is-ancestor`) recognises a merge that landed before
  a crash so it is reconciled, never repeated.
- Cog `_run_manifest_task`: under the build's git lock — reconcile-or-merge, then
  `_combined_checks()` runs the task's acceptance check (argv via shlex, no shell, in the
  task's project folder) and the plan's `Check:` line (in the copy) on the *combined* copy;
  the check's own file changes are discarded. A clash names the kept branch; a failed combined
  check names the failing command and its last line.
- Core `_record_manifest_result`: a result with a commit is always `submit_result`-ed (the
  evidence is kept), and accepted only when `ok`; otherwise blocked — dependents stay held.
- Tests: `tests/test_work_copy.py` (+2: clash keeps both versions; already-merged side is
  recognised and not merged twice), `test_rolling_workers.py` (+1: failed combined check is
  saved but not accepted), `test_manifest_build_cog.py` (+2: one task's acceptance check fails
  after merging → blocked with its commit, dependent pending, siblings accepted; two workers
  edit the same unowned file → the later one clashes, its branch survives, the copy is clean).
  The cog fixtures now use acceptance checks that pass on any machine (`python -c pass`)
  because the checks really run now.
- Implementation commit: `09a9709`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (421 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T16 — Require real check and review evidence

- Core: `parse_review_verdict(text)` → ("approve" | "changes" | "none", detail); the legacy
  `parse_review` (silence = approve) stays for checkbox builds.
- Cog: `_combined_checks` returns "no runnable check" (not accepted) when neither the task's
  acceptance check nor a plan `Check:` line can run. `_review_manifest_task` keeps the modes:
  cheap → no review; balanced → review only when `_is_hard` says so; careful → every task.
  A required review with no reviewer AI, a run failure, or no verdict is "none"; APPROVE
  accepts; CHANGES blocks with the reviewer's reason. The verdict is appended to the task's
  check evidence in the ledger. The reviewer runs in the build thread on a "review" slot
  (T08 reserve) and the thread's harness/model are restored afterwards.
- Tests: `test_manifest_build_cog.py` (+4): ordinary balanced task accepted with no review
  call; a hard task with no reviewer configured is blocked with its commit kept; in careful
  mode CHANGES blocks, APPROVE accepts and a crashed review blocks; a task whose acceptance
  check is "none" is blocked with "no runnable check".
- Implementation commit: `176d6ab`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (425 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T17 — Reconcile interrupted work without duplication

- Core: `TaskLoop(reconcile=…)`; `_reconcile_interrupted()` runs once when the manifest loop
  starts: the adapter salvages what it can, then every attempt still `running` is blocked with
  "interrupted by a restart before its work was saved (attempt N) … say so to run it again",
  and a report line says what was kept and what waits. Uncertain ownership is never
  reassigned automatically.
- Cog `_reconcile_interrupted`: for each running attempt, finds its side copy
  (`work_copy.side_copy_for()`, deterministic names) — merged before the crash → saved
  (checks on the combined copy → accept/block); committed but unmerged → merged now under the
  build's git lock, then the same; nothing saved → left for the loop to block. Completed work
  is never rerun.
- Ledger: `TaskAttempt.base_commit` (recorded with the thread at start) and
  `side_is_merged(..., base=…)` so an untouched side copy — whose tip equals its base and is
  trivially an ancestor of HEAD — is not mistaken for merged work.
- Abandoned reservations: admission slots live only in memory (T08); a restart starts with
  none held, so nothing needs recovering there — noted, not coded.
- Tests: `test_rolling_workers.py` (+1: salvaged attempt accepted and not rerun, the other
  blocked with the restart reason and not reassigned, the rest carried on),
  `test_manifest_build_cog.py` (+1: restart after merge / after a worker's commit / mid-work
  through `resume_all`: two salvaged, one blocked, only the dependent page ran).
- Implementation commit: `b0d6490`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (427 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T18 — Enforce exactly one automatic repair attempt

- Ledger: `MAX_AUTO_REPAIRS = 1`; `TaskAttempt.lineage_repairs` (carried into every later
  attempt) and `previous_failure`; `repairs_left()`, `repair()` (a fresh attempt that knows
  why; refused when the budget is spent or nothing failed), `retry()` (a person's request,
  never refused, keeps the lineage count).
- Core `_record_manifest_result`: a failed result blocks the task, then — when the reason is
  a real failure (`_is_repairable`: not a cancellation, a restart interruption or an
  unavailable required review) and a repair is left — calls `repair()` and reports "🔧 …
  trying once more with the reason". A second failure stays blocked: a blocker for a person.
  Independent tasks keep going.
- Handoff: `WorkerHandoff.previous_failure` / `attempt`; the prompt tells the repair worker
  what was sent back and that this is the last automatic try.
- Cog: side copies are named `<task>-a<attempt>` (slug limit raised to 48 chars) so the
  repair's fresh side copy never wipes the clashing branch T15 kept.
- Every failure entry point on the manifest path is that one function; the legacy checkbox
  build keeps its own unstick ladder (`_unstick`, tries bounded by its existing constants) and
  never reaches the manifest ledger — recorded, not merged, because the two paths do not share
  state (see `docs/plans/v4.1.0-decisions.md`).
- Tests: `test_task_state.py` (+1 budget across attempts and reopen), `test_rolling_workers.py`
  (+4: fail→repair→fail = blocker with exactly two attempts and siblings accepted; a repair
  that succeeds saw the reason; an interrupted repair earns no third attempt after a restart;
  cancellations do not spend the repair), `test_manifest_dispatch.py` expectations updated,
  `test_manifest_build_cog.py` clash test now proves the clashing branch survives the repair.
- Implementation commit: `deb0e7b`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (432 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T19 — Version mid-build requirement changes

- Ledger: `sync_tree(tree)` (called by `open_build_state` on every reopen, i.e. every loop
  iteration) saves changed plan versions immediately, adds records for new tasks, moves
  pending attempts to the new version, and opens a **rework** attempt for tasks accepted or
  finished at an older version; returns a `SyncReport` (`last_sync`). `rework(task, reason)`:
  a fresh attempt with `rework_reason` and `previous_commit`, `lineage_repairs` unchanged,
  `previous_failure` cleared — distinct from `repair`. `records` lists only tasks in the
  current tree (removed tasks are kept in the file, not counted).
- Core: the loop reports each plan change ("📝 Plan changed (product → v3): 1 task will be
  reworked …; their dependents wait"); a finished result whose acceptance is refused as stale
  is kept and reworked, never accepted.
- Handoff: `rework_reason` / `previous_commit`; the prompt says it is a rework and where the
  earlier work is, so the worker adjusts instead of starting over.
- Tests: `test_task_state.py` (+3: edit saved at once, stale acceptance → rework with the
  repair budget intact and unrelated plans untouched, reopen keeps it; finished-at-old-version
  can't be accepted but is kept; new tasks appear pending), `test_rolling_workers.py` (+1: the
  product plan is edited while its task runs → attempt 1 kept, attempt 2 is a rework at v3 with
  the previous commit, the page waited for it, the other tasks ran once).
- Implementation commit: `b834a9f`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (436 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors). The Check's own timeout was raised to 300 s
  (real git worktrees in the cog tests take ~80 s on Windows).

## T20 — Persist blocker messages and question identity

- New `claude_code_core/gowork_blockers.py`: `BlockerLedger(<state dir>/gowork-blockers.json)`
  — one file for every build, atomic writes, reloaded on every call. `Blocker` carries
  build id, task id, attempt id (its own identity: `blocker:<attempt>`), plan id/version,
  channel, question, created_at, posted message id/channel, resolved flag, answer, who
  answered, the reply's message id and when. `open()` is idempotent per attempt; `by_message()`
  maps a Discord message to exactly one blocker; `resolve()` is once-only; `unresolved()` /
  `unposted()` filter by build; a broken file reads as empty.
- Cog: the after-save hook now also asks: a task blocked with no automatic repair left gets
  one question ("❓ **project** — task `x` is stuck: … Reply to this message with retry, skip,
  or what to change") posted to the build's report channel; the message id is recorded.
  Restart-interrupted tasks are not asked here (the restart line already said so).
  `repost_blockers(running)` runs on resume and posts only unposted questions.
- Tests: `tests/gowork_upgrade/test_blockers.py` (5), `test_manifest_build_cog.py` (+1: a task
  whose check always fails uses its repair, then exactly one question is posted, recorded with
  its message id and attempt, and recovery posts nothing twice).
- Implementation commit: `76611e9`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (442 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T21 — Route actual Discord Replies to their blocker

- Cog `take_message` → `_take_blocker_reply`: `message.reference.message_id` →
  `BlockerLedger.by_message`. Not a blocker reference → the normal rules. Unauthorized
  author or a different channel → `False` (ordinary conversation). Already answered, build
  gone, or the blocker's attempt no longer current → claimed with a short note, nothing
  dispatched. Otherwise `resolve()` (once-only) and `_apply_blocker_answer`: **skip** →
  the task stays blocked as skipped (dependents held); **retry** → `state.retry`; anything
  else → `state.rework(task, "you said: …")` so the instruction reaches the next handoff;
  then a parked build (`_Running.parked`) is woken through its own waiter — a running build
  simply picks the pending task up under the normal ready-set, checks and capacity.
- `_wait_parked`: while the build has unresolved questions, only close/finish/throw words
  count channel-wide; retry/skip must be replies to one specific question.
- Tests: `test_manifest_build_cog.py` (+1: two failing tasks → two questions; a non-reply,
  an unknown reference and an unauthorized reply dispatch nothing; "skip" and "retry" replies
  move only their tasks (attempt 3 for the retried one, "skipped" for the other, the accepted
  sibling untouched); a duplicate reply to an answered question does nothing; the retried
  task's next failure asks again).
- Implementation commit: `78693fb`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (443 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T22 — Report concise context-rich outcomes

- New `claude_code_core/gowork_report.py`: `render_progress` (project — goal; N of M done;
  ✅ done / ⏳ being built / 🛑 stuck with why / ⬜ waiting, each by its outcome text),
  `render_completion` (🏁 "All N tasks are done and checked", one bullet per task with what it
  delivers and how it was checked — a review reads as "a second AI approved it" — plus a
  "Worth knowing" list of repairs and reworks; with stuck tasks it lists issues, done-so-far
  and what waits, never success), `render_blocker_question` (one decision: what is stuck,
  why, and reply retry / skip / what to change). No commit hashes, no bare task ids.
- Core: the loop reports progress after every saved result, names started tasks by outcome,
  and a stuck outcome carries the outcome-worded reasons; `TaskLoop.manifest_summary()` and
  `project_name`.
- Cog: the blocker question uses the renderer; a manifest build's finished message carries
  the ledger summary (the legacy card keeps the bot's own check results).
- Tests: `tests/gowork_upgrade/test_report.py` (4 fixtures readable without chat history:
  progress, completion with checks/limitations, all-blocked lists issues, blocker question);
  earlier tests updated where they looked for task ids in messages.
- Implementation commit: `0f007c7`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (447 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T23 — Automatically integrate each checked local build

- `work_copy.integrate_build(copy, check=…)` → `IntegrationResult(ok, message, commit,
  check_output)`: under `integration_lock(repo)` (one asyncio lock per project) — scratch
  detached worktree at the project's HEAD → `git merge` the build branch there (a clash aborts
  and names the files; both sides kept) → the plan's check runs in the scratch checkout, never
  in the person's working tree → if the project's HEAD is unchanged, `git merge --ff-only`
  moves the project forward; git refuses to overwrite an unsaved edit or an untracked file,
  which is reported with the file names. Success removes the build's copy; every failure keeps
  it for repair. There is no push, PR or deploy anywhere in the path.
- Cog: `_keep_build()` — "looks good" and "wrap up" on a manifest build integrate this way with
  the plan's `Check:` line ("use the build's version" still takes the legacy path on request);
  checkbox builds keep `keep_work`. A refusal posts "I couldn't keep it yet: …" with the check's
  last lines and leaves the build parked — the bounded repair is the person's reply.
- Tests: `tests/test_work_copy.py` (+5: two builds for one project land in turn; unrelated
  unsaved and untracked edits survive uncommitted; an unsaved edit the build also changes
  blocks instead of overwriting; a conflict blocks and keeps both sides with a clean project;
  a failing combined check blocks before the project changes and cleans the scratch),
  `test_manifest_build_cog.py` (+2: "looks good" lands the build locally with the person's
  notes intact and no remote; a project check that fails on the combined result keeps the
  build and tells the person).
- Implementation commit: `5c8f4fe`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (454 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T24 — Remove success approval waiters and reminders

- Cog `_wrap_up`: for a manifest build whose own checks passed with nothing proposed,
  `_auto_integrate()` runs right after the finished card — `_keep_build()` (T23 integration
  with the plan's check), then "✅ Kept: added to your project (on this computer only …)",
  queue note, worker-thread cleanup, build forgotten. No verdict wait, no reminders.
  A refusal posts "⚠️ I couldn't add … yet: <reason>" and falls back to the parked wait (the
  build stays on its branch; the person's reply is the bounded repair).
- Ledger: `BuildState.integrated_commit` / `mark_integrated()`; a build already integrated
  returns "kept" at once — duplicate finish or a restart cannot integrate twice, repost the
  completion or reopen workers. Terminal blocked builds keep their ledger, side copies and
  open questions (T20) and hold no worker slot. `request_stop` is untouched: stop means stop.
- `_build_state` reads the plan from the project once the copy is gone.
- Tests: `test_manifest_build_cog.py` — the main build test now proves the build lands in the
  project on its own (integrated recorded, copy removed, no remote); the T23 tests became
  "a verified build integrates itself" and "a project check that fails on the combined result
  keeps the build waiting"; `test_running_identity.py`'s stop test parks both builds on failing
  checks and closes one. `_run_until_settled` treats "ended" as settled.
- Implementation commit: `bad8fc3`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (454 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T25 — Record reproducible workflow friction

- New `claude_code_core/gowork_friction.py`: `FrictionEvent(kind, build_id, task_id,
  attempt_id, plan_id, plan_version, detail, at)` with kinds queue_wait / dependency_wait /
  repair / review / question / capacity / rework; `append_friction` (JSONL, never raises),
  `read_friction` (skips a half-written line), `friction_summary` (repeatable counts plus
  derived `review_changes` and `question_repeat`; no tokens, no cost), `friction_report`
  (plain sentences about one build).
- Written by: the loop (`TaskLoop(friction_path=…)`: queue waits when ready tasks find no
  room, review verdicts from the saved checks, repairs, reworks from failed acceptance and
  from plan edits), the cog (a "question" when a blocker is posted), and
  `_run_helper.tick_capacity` (a host-level "capacity" line when starts are paused;
  `configure_adaptive_limit(friction_path=…)` from `setup.py`). File:
  `<gowork state dir>/gowork-friction.jsonl` beside the step records.
- At the end of a build the report is appended to the plan's `.progress.md` under
  "What slowed this build down" — the only place it is written; planning instructions
  (CLAUDE.md, skills, the planner prompts) are never touched.
- Tests: `tests/gowork_upgrade/test_friction.py` (4: round trip with identity; repeatable
  counts and no cost/token keys; the report is plain sentences about one build only; broken
  lines skipped and appends never raise), `test_rolling_workers.py` (+1: a repair and a review
  recorded with build/task/attempt/version through the loop, and reported from the records).
- Implementation commit: `ebc05c8`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (459 passed),
  `tests/test_setup.py` + `tests/test_run_helper.py` (94 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T26 — Refine existing planning prompts without replacing the flow

- New `claude_code_core/gowork_prompts.py`: `PLANNER_RULES` and `COMMUNICATION_RULES` — the
  two instruction blocks from `.planning/gowork-flow/prompt-refinement.md`, as repo-owned text
  (not installed anywhere; the test checks the user's global instruction files do not carry
  them). `planning_prompt(plan_text, plan_path=, known_answers=, project_facts=, unclear=)`
  continues the existing conversation: the plan stays on disk and is not pasted back, facts
  already gathered and settled answers are listed as "never ask again", one question with
  lettered choices and a recommendation-with-consequence, and — only when the person found the
  last explanation unclear — one concrete example from the project, labelled hypothetical.
- `goal_interview_prompt` (the existing One Question flow) now ends with the same
  communication rules; its look-first / one-question / five-question / approval structure is
  unchanged.
- Static examples (`tests/gowork_upgrade/test_planning_prompts.py`, 5): the report plan
  keeps its six-task chain and its dependency wording with no promise of parallelism; the
  multi-project business plan keeps product/website/marketing scope and asks one question with
  the settled answer preserved; a resumed answer (capacity is automatic) is not asked again and
  the unclear case asks for one labelled example; the goal interview carries the rules; the
  rules are repo text only. These examples show what the prompt preserves — they do not
  measure live model behaviour.
- Implementation commit: `60fd630`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (464 passed), `ruff check`,
  `ruff format --check`, `pyright` (0 errors).

## T27 — Export validated plans supported by the installed runner

- New `claude_code_core/gowork_export.py`: `export_plan(tree, PlanHeader, runtime=)` renders
  one plan document — `Goal:`/`Done when:`/`Check:`/`Try:` lines, `## Decisions` (read back
  by T12's `plan_decisions`), `## Agreed outcomes`, the `gowork-plan` manifest
  (`render_plan_manifest`) and `## Tasks` checkbox lines in `dependency_order()` with
  indented `Task:` / `Depends on:` / `Inputs:` / `Files:` / `Resources:` / `Result:` /
  `Verify:` / `Outcome:` lines — and parses it back through `parse_plan_tree` before
  returning; a mismatch is an `ExportError` and nothing is written. `write_plan` is atomic
  and refuses to overwrite without `overwrite=True`.
- Compatibility: `RuntimeSupport(manifest_dispatch)` — `installed_runtime()` inspects
  `TaskLoop.__init__` for `manifest_worker` (T11b) rather than assuming; `RuntimeSupport.LEGACY`
  is a checkbox-only runner. For it, a single-project tree exports as a sequential checklist
  without the fence (note: "tasks run one at a time … no parallel safety claimed"); a
  multi-project tree is refused naming every project and suggesting an upgrade or one plan per
  project. `check_plan(text, source_path=, runtime=)` judges a hand-written plan the same way:
  format, parser problems (a cycle, an unsafe path), the legacy-runner problems (manifest
  ignored, several projects, checkbox lines out of dependency order), tasks ready now.
- `plan_tree_from_manifest(raw, base_dir=)` validates manifest data through the same parser;
  `plan_template()` is the planner-facing example and validates as written (the T29 guidance
  points at it). CLI: `python -m claude_code_core.gowork_export check PLAN.md
  [--legacy-runner]` / `template`.
- Tests: `tests/gowork_upgrade/test_plan_export.py` (10): the multi-project fixture exports,
  loads back equal, schedules `[API, STYLES, POST]` through `ready_tasks` and reads its
  decisions; the old checkbox fixture checks clean on both runtimes and is never rewritten; a
  single-project tree becomes a sequential checklist for the legacy runner with the task
  details on each line; the multi-project tree is refused for it; a cycle and an unsafe path
  are refused before anything is written; a hand-written cyclic manifest is reported by
  `check_plan` and the loop stops STUCK with no worker started and no ledger created; the
  legacy-runner check names the unsupported behaviour; the template validates; overwrite
  protection; the installed runtime supports manifests.
- Implementation commit: `cac8c6a`.
- Checked with `uv run python scripts/check_gowork_upgrade.py` (483 passed), `ruff check`,
  `ruff format`, `pyright claude_discord/ claude_code_core/` (0 errors).
