# Go Work upgrade: verification record and local activation notes (T32)

This page is the deployment/rollback record for the Go Work upgrade built by
`docs/plans/gowork-upgrade-execution-plan.md` (T01–T32). It says what exists, how it
was checked, and what an operator does to switch it on — and it says plainly what has
**not** happened: the upgrade is **not deployed**, no bot was restarted, no instruction
file outside temporary test homes was touched, and nothing was pushed. Every command
below was run on the build machine (Windows 11, Python 3.12, the worktree of branch
`release/v4.1.0`); the results are copied, not summarised.

## What is implemented, what is checked, what is not yet live

### Implemented and checked offline (this branch)

- Plan trees with stable ids, complete task assignments, validation of dependencies,
  ownership and coverage; multi-build storage; a per-build task ledger with attempts,
  evidence, one automatic repair, versioned rework and thread bookkeeping (T01–T06, T18,
  T19).
- Measured host capacity: resource snapshots, one admission controller with fair turns,
  an adaptive policy, and real process starts that reserve slots (T07–T10). `setup_bridge()`
  already wires this whenever `MAX_CONCURRENT_SESSIONS` is unset.
- Manifest dispatch through the existing coordinator: per-task side copies and worker
  threads, persisted handoffs, per-worker save/release, archive-not-delete, serialized
  combination with combined checks, real review evidence, restart reconciliation,
  durable blocker questions answered only by direct replies, plain-language reports,
  automatic local integration with no looks-good gate, friction records (T11–T25).
- Planner side: refined prompts, validated plan export with legacy-runner handling, full
  task context for grouping, packaged guidance with an idempotent staging installer
  (T26–T29); saved contract cases and the offline practice command (T30–T31).

### Not yet live

- The running bot (EbiBot, `/home/ebi/claude-code-discord-bridge/`, systemd
  `discord-bot`) runs the code in its own checkout of `main`. Nothing on this branch
  reaches it until the branch is merged and the bot restarts through its own path
  (`scripts/pre-start.sh` pulls `main` on restart; `AutoUpgradeCog` can do it on a
  webhook). Neither was triggered by this build.
- The planner guidance (T29) is staged into temporary homes by the tests only. The real
  `~/AGENTS.md`, `~/.agents/skills`, `~/.claude`, `$CODEX_HOME` and DSH home are unchanged.
- Live model behaviour is not measured anywhere in this record: the prompt examples
  (T26, T30) show what the planner is given, not how a model answers.
- The real host's readings (memory, swap, load) have been read by `HostProbe` in one
  test on the build machine; no capacity decision has yet been taken on the live host.
- The route from a planning thread's edited plan into a *manifest* build's copy carries
  checkbox changes only; a mid-build manifest edit is applied to the build's copy of the
  plan (the T31 demo says so in its limitations).

## Saved verification commands and results

Run from the repository root of the worktree, on 2026-09-21. `scripts/test-clean-env.sh`
strips this machine's live `DISCORD_*`/`CCDB_*` variables first; plain `pytest` inherits
them and fails.

| Command | Result |
|---|---|
| `uv run python scripts/check_gowork_upgrade.py` (the plan's Check) | 512 passed, 4 warnings in ~110 s (498 before T30) |
| `bash scripts/test-clean-env.sh tests/ -q -p no:cacheprovider` (full suite) | 5606 passed, 37 skipped in ~3 min 45 s (5592 before T30) |
| `uv run ruff check claude_discord/ claude_code_core/ extensions/ scripts/` | All checks passed |
| `uv run ruff format --check claude_discord/ claude_code_core/ extensions/ scripts/ tests/gowork_upgrade/` | 278 files already formatted |
| `uv run pyright claude_discord/ claude_code_core/` | 0 errors, 6 warnings (pre-existing) |
| `uv run python -m claude_discord.gowork_demo` (the plan's Try) | 11 × `✓`, "All behaviours observed in 7 s", exit 0 |
| `uv run python -m claude_code_core.gowork_contracts tests/gowork_upgrade/fixtures/contracts` | 8 case(s), 0 failed; every scenario covered; exit 0 |

Regressions found by this final pass and fixed on this branch (not deferred): an earlier
attempt's worker thread was forgotten after a repair, rework or retry and never
archived; the end-of-build archive retry was documented but never called (both T31).
No other failure was seen. Nothing was skipped to make a number green: the 37 skips are
the suite's pre-existing platform/optional-dependency skips.

## Requirement → test map

Every ticked task in the plan maps to tests that exist on this branch. "Implemented,
checked offline" means: the behaviour exists in the package, its tests pass in the Check
and the full suite, and it has **not** run on the live bot. `tests/gowork_upgrade/test_activation_notes.py`
reads this table and fails if a row is missing, a test is misnamed, or a status claims
more than that.

| Task | Promise | Tests | Status |
|---|---|---|---|
| T01 | stable master/child plan identities, legacy plans still load | `tests/gowork_upgrade/test_plan_identity.py` | implemented, checked offline |
| T02 | complete worker task assignments | `tests/gowork_upgrade/test_task_assignments.py` | implemented, checked offline |
| T03 | dependency/ownership/coverage validation | `tests/gowork_upgrade/test_plan_validation.py` | implemented, checked offline |
| T04 | multiple builds stored without overwriting runs | `tests/gowork_upgrade/test_loop_store_identity.py`, `tests/test_loop_store.py` | implemented, checked offline |
| T05 | durable task attempts and acceptance evidence | `tests/gowork_upgrade/test_task_state.py` | implemented, checked offline |
| T06 | ready tasks across child plans | `tests/gowork_upgrade/test_ready_tasks.py` | implemented, checked offline |
| T07 | host/worker resource pressure | `tests/gowork_upgrade/test_resources.py` | implemented, checked offline |
| T08 | fair shared admission | `tests/gowork_upgrade/test_admission.py` | implemented, checked offline |
| T09 | adaptive capacity policy | `tests/gowork_upgrade/test_capacity_policy.py` | implemented, checked offline |
| T10 | adaptive capacity on real process starts | `tests/gowork_upgrade/test_adaptive_starts.py`, `tests/test_run_helper.py`, `tests/test_setup.py` | implemented, checked offline; not yet live |
| T11 | dispatch through the existing coordinator (T11a–c) | `tests/gowork_upgrade/test_running_identity.py`, `tests/gowork_upgrade/test_manifest_dispatch.py`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_manifest_build_runs_projects_together_and_accepts_every_task`, `tests/gowork_upgrade/test_start_adapters.py` | implemented, checked offline; not yet live |
| T11a | running identity by build | `tests/gowork_upgrade/test_running_identity.py` | implemented, checked offline |
| T11b | manifest dispatch from the ledger | `tests/gowork_upgrade/test_manifest_dispatch.py`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_manifest_build_runs_projects_together_and_accepts_every_task` | implemented, checked offline |
| T11c | `/gowork` and `POST /api/loops` answer with the running build | `tests/gowork_upgrade/test_start_adapters.py`, `tests/test_api_server.py` | implemented, checked offline; not yet live |
| T12 | compact persisted worker handoffs | `tests/gowork_upgrade/test_worker_handoff.py` | implemented, checked offline |
| T13 | workers saved and released individually | `tests/gowork_upgrade/test_rolling_workers.py::test_a_fast_worker_releases_its_dependent_while_a_sibling_runs`, `tests/gowork_upgrade/test_rolling_workers.py::test_cancelling_the_build_keeps_in_flight_attempts_and_saved_results` | implemented, checked offline |
| T14 | archive worker threads, never delete, retry safely | `tests/gowork_upgrade/test_manifest_build_cog.py::test_worker_threads_are_archived_after_the_save_never_deleted_and_retried`, `tests/gowork_upgrade/test_task_state.py::test_an_earlier_attempts_thread_is_still_archived_after_a_repair_rework_or_retry` | implemented, checked offline |
| T15 | serialized combination with combined checks | `tests/test_work_copy.py::test_a_clash_can_keep_the_side_copy_for_repair`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_combined_check_failure_blocks_the_task_and_its_dependents`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_clash_keeps_the_workers_branch` | implemented, checked offline |
| T16 | real check and review evidence | `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_hard_task_cannot_be_accepted_without_a_reviewer`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_reviewer_verdict_decides_and_a_broken_review_blocks`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_task_without_a_runnable_check_is_not_accepted` | implemented, checked offline |
| T17 | restart reconciliation without duplication | `tests/gowork_upgrade/test_rolling_workers.py::test_attempts_left_running_by_a_crash_are_reconciled_not_reassigned`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_restart_keeps_finished_work_and_never_reruns_or_reassigns` | implemented, checked offline |
| T18 | exactly one automatic repair | `tests/gowork_upgrade/test_rolling_workers.py::test_a_failure_gets_exactly_one_automatic_repair`, `tests/gowork_upgrade/test_rolling_workers.py::test_the_repair_budget_survives_a_restart`, `tests/gowork_upgrade/test_task_state.py::test_the_repair_budget_follows_the_task_across_attempts` | implemented, checked offline |
| T19 | versioned mid-build requirement changes | `tests/gowork_upgrade/test_task_state.py::test_a_plan_edit_is_saved_at_once_and_stale_acceptances_become_rework`, `tests/gowork_upgrade/test_rolling_workers.py::test_a_mid_build_plan_change_reworks_only_the_affected_tasks` | implemented, checked offline |
| T20 | durable blocker questions | `tests/gowork_upgrade/test_blockers.py`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_stuck_task_becomes_one_durable_question` | implemented, checked offline |
| T21 | direct Discord replies route to their blocker | `tests/gowork_upgrade/test_manifest_build_cog.py::test_replies_resolve_only_their_own_blocker` | implemented, checked offline; not yet live |
| T22 | concise context-rich outcomes | `tests/gowork_upgrade/test_report.py` | implemented, checked offline |
| T23 | automatic local integration under a per-project lock | `tests/test_work_copy.py::test_two_builds_for_one_project_integrate_in_turn`, `tests/test_work_copy.py::test_a_conflict_blocks_and_keeps_both_sides`, `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_failing_project_check_keeps_the_build_for_repair` | implemented, checked offline |
| T24 | no looks-good gate on success | `tests/gowork_upgrade/test_manifest_build_cog.py::test_a_verified_build_integrates_itself_locally` | implemented, checked offline |
| T25 | reproducible workflow friction | `tests/gowork_upgrade/test_friction.py`, `tests/gowork_upgrade/test_rolling_workers.py::test_friction_is_recorded_with_identity_and_reported_from_records_only` | implemented, checked offline |
| T26 | refined planning prompts (static examples) | `tests/gowork_upgrade/test_planning_prompts.py` | implemented, checked offline |
| T27 | validated plan export, legacy-runner handling | `tests/gowork_upgrade/test_plan_export.py` | implemented, checked offline |
| T28 | full task context for grouping and workers | `tests/gowork_upgrade/test_task_context.py` | implemented, checked offline |
| T29 | packaged planner guidance, idempotent staging | `tests/gowork_upgrade/test_planner_guidance.py` | implemented, checked offline; not yet live |
| T30 | offline contract evaluation from saved examples | `tests/gowork_upgrade/test_contract_evaluation.py` | implemented, checked offline |
| T31 | repeatable offline practice command | `tests/gowork_upgrade/test_practice_demo.py` | implemented, checked offline |
| T32 | final regression and these notes | `tests/gowork_upgrade/test_activation_notes.py` | implemented, checked offline |

## Backup and migration of the Go Work state

Go Work keeps everything under one state directory and one work root:

| What | Where (default) | Override |
|---|---|---|
| Running builds (`LoopStore`) | `~/.local/state/ccdb/gowork-loops.json` | `CCDB_GOWORK_STATE` (the file path; siblings derive from it) |
| Per-build task ledgers (T05) and handoffs (T12) | `~/.local/state/ccdb/builds/<build_id>.json`, `builds/<build_id>/handoffs/` | beside `CCDB_GOWORK_STATE` |
| Admission fairness (T08) | `~/.local/state/ccdb/gowork-admission.json` | beside `CCDB_GOWORK_STATE` |
| Blocker questions (T20) | `~/.local/state/ccdb/gowork-blockers.json` | beside `CCDB_GOWORK_STATE` |
| Friction records (T25) | `~/.local/state/ccdb/gowork-friction.jsonl` | beside `CCDB_GOWORK_STATE` |
| Build copies, side copies, `step-records.jsonl`, `queue.json` | `~/.local/state/ccdb/gowork/` | `CCDB_GOWORK_ROOT` |

Before activation, while the bot is stopped or idle (no `/gowork` build running):

```bash
STATE=~/.local/state/ccdb
STAMP=$(date +%Y%m%d-%H%M%S)
tar -C "$STATE" -czf ~/ccdb-gowork-state-$STAMP.tgz \
    gowork-loops.json builds gowork-admission.json gowork-blockers.json gowork-friction.jsonl 2>/dev/null
# the work root holds git worktrees of your projects; back it up only if a build is parked
tar -C "$STATE" -czf ~/ccdb-gowork-root-$STAMP.tgz gowork 2>/dev/null
```

Migration is automatic and repeatable: on the first `resume_all()` after the restart,
`LoopStore.migrate()` writes a derived `build_id` (`thread-<worker_thread_id>`) into each
record that lacks one (T04; a second run changes nothing). Old ledger files that predate
`earlier_threads` load with an empty list (T31). Old checkbox plans and their stored runs
keep working unchanged; the manifest path is taken only by plans that carry a
`gowork-plan` fence. No SQLite schema changes are part of this upgrade.

## Shared-host coordination

- One `AdmissionController` per bot process, shared by every build in it (T08). It counts
  only this process's runs; other people's sessions on the same host are seen through the
  measured readings (`HostProbe`: memory, swap, load, cgroup limits), so heavy neighbours
  reduce what the policy admits (T09). The policy keeps 4096 MB of headroom by default and
  never grows past what observed worker peaks say will fit.
- To keep a hard ceiling for the benefit of other sessions, set `MAX_CONCURRENT_SESSIONS`
  (a fixed semaphore on every run; the adaptive path is then off) — that is today's
  behaviour and needs no other change. Leave it unset to get measured capacity.
- Two bot processes on one host must use different state directories (`CCDB_GOWORK_STATE`
  and `CCDB_GOWORK_ROOT`), otherwise they overwrite each other's `gowork-loops.json` and
  `gowork-admission.json`. They do not share admission; each measures the host and backs
  off on pressure independently.
- A restart while builds run is safe by design (T17: attempts left `running` are salvaged
  from their side copies or blocked with the restart reason, never rerun blindly), but
  the drain check used by `AutoUpgradeCog` counts running sessions, not parked builds:
  wait until `/gowork` builds are idle (no `⚡` worker threads open) for the least
  disruptive restart.

## Instruction install

The planner guidance (T29) is staged, inspected and only then installed — always into a
home you name; the tool refuses to run without `--home`.

```bash
# 1. See what would change in the real home (writes nothing)
uv run python -m claude_code_core.gowork_guidance plan --home ~
# 2. Stage it: one skill under ~/.agents/skills/gowork-planning/, one owned routing block
#    in ~/AGENTS.md (CLAUDE.md left alone when it already imports ~/AGENTS.md), links for
#    Codex ($CODEX_HOME) and DSH ($DSH_HOME); every edited file gets a .ccdb-backup first
uv run python -m claude_code_core.gowork_guidance stage --home ~
# 3. Confirm every harness resolves the same file
uv run python -m claude_code_core.gowork_guidance check --home ~
```

Running `stage` twice changes nothing (every item reports `unchanged`). This was done in
temporary homes only; the operator runs it against `~` when ready. It does not touch the
running bot and does not need a restart — harness sessions read the files when they start.

## Live activation

Nothing here was executed. In order:

1. Merge this branch into `main` through a PR after review (no direct push to `main`).
2. Take the backup above.
3. Restart through the bot's own path at a quiet moment: `sudo systemctl restart discord-bot`
   on the host (`scripts/pre-start.sh` pulls `main` and syncs dependencies), or let
   `AutoUpgradeCog` do it on its webhook with drain and approval. To try the branch before
   merging, `make dev-on` from a checkout of it on the host loads `claude_discord` from that
   worktree on the next restart (`make dev-off` reverts) — an operator action on the host.
4. Confirm in the log: `Session capacity: adaptive (starts at 2, measured)` (or
   `Max concurrent sessions: N (fixed)` when `MAX_CONCURRENT_SESSIONS` is set).
5. Start one small manifest plan with `/gowork` in a test channel (the T27 template:
   `uv run python -m claude_code_core.gowork_export template`) and watch for: one worker
   thread per task, `⚡ Started …` lines, archived worker threads, a `🏁` card, and
   `✅ Kept: added to your project` with no reply typed.
6. Stage the guidance (previous section) once the build path is trusted.

## Rollback

- Code: `git revert` the merge commit on `main` (or check out the previous release) and
  restart through the same path. The old code reads the same `gowork-loops.json` (build
  ids are ignored by it, not removed); ledgers, blockers and friction files are extra
  files it never opens. A build that was mid-flight on the new path is not resumable by
  the old code: stop it first (`/stopwork` or "close" in its thread) or accept that its
  side copies stay on their branches under `CCDB_GOWORK_ROOT` for manual `git merge`.
- State: restore the backup archives over `~/.local/state/ccdb/` while the bot is stopped.
- Capacity: set `MAX_CONCURRENT_SESSIONS=10` to return to the fixed ten without a code
  change (the adaptive controller is then not constructed).
- Guidance: `uv run python -m claude_code_core.gowork_guidance rollback --home ~` removes
  the links, the routing block and the skill, restores each `.ccdb-backup` whose file is
  otherwise unchanged (a file edited since keeps the edits and its backup), and removes
  only folders the install created and only when empty. A second rollback changes nothing.
