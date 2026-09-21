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
