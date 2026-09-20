Check: `uv run pytest tests/test_feature_task_graph.py tests/test_feature_run_state.py tests/test_feature_worker_workspace.py tests/test_planning_split*.py tests/test_feature_workflow.py tests/test_task_loop.py tests/test_task_loop_cog.py -q`

Try: `uv run python -m extensions.feature_workflow.coordinator --help`

## 1. Shared scheduling contracts

- [x] 1.1 **Depends on: none; owns: `extensions/feature_workflow/task_graph.py`, `tests/test_feature_task_graph.py`.** Parse task IDs, dependencies, owned paths, checks, and legacy checkbox plans; verify unknown nodes, cycles, protected/invalid paths, and independent ownership overlap are rejected while legacy plans become sequential graphs.
- [ ] 1.2 **Depends on: 1.1; owns: `extensions/feature_workflow/scheduler.py`, `tests/test_feature_scheduler.py`.** Compute the complete dependency-ready, non-conflicting set with no product worker cap; verify four safe tasks are all ready and unresolved/integration dependencies remain pending.
- [ ] 1.3 **Depends on: 1.1; owns: `extensions/feature_workflow/run_state.py`, `tests/test_feature_run_state.py`.** Define atomic durable state transitions and stable correlation IDs for pending, spawning, ambiguous, dispatched, verified, integrated, queued, and blocked tasks; verify restart never converts uncertainty into duplicate dispatch.

## 2. Isolated worker evidence

- [ ] 2.1 **Depends on: 1.1, 1.3; owns: `extensions/feature_workflow/worker_workspace.py`, `tests/test_feature_worker_workspace.py`.** Create/recover one approved-foundation branch and `.worktrees/wt-<thread-id>` checkout per task; verify branch ancestry, repository identity, clean-state protection, and durable-branch recovery after checkout cleanup.
- [ ] 2.2 **Depends on: 1.1, 1.3; owns: `extensions/feature_workflow/result_verifier.py`, `tests/test_feature_result_verifier.py`.** Validate task identity, approval revision, foundation ancestry, owned changed paths, clean commit, and nonempty focused check evidence; verify each invalid evidence class is rejected before integration.
- [ ] 2.3 **Depends on: 1.1; owns: `extensions/feature_workflow/briefing.py`, `tests/test_feature_briefing.py`.** Build the compact fresh-session task brief with goal, decision subset, dependencies, ownership, restrictions, checks, and result destination; verify it excludes the parent transcript and forbids merging or worker spawning.

## 3. Ready-set dispatch and integration

- [ ] 3.1 **Depends on: 1.2, 1.3, 2.2, 2.3; owns: `extensions/feature_workflow/coordinator.py`, `tests/test_feature_workflow.py`.** Replace the normal per-run admission window with intent-first submission of every ready task through the relay queue while preserving approval and reconciliation; verify infrastructure capacity changes status but does not redefine graph readiness.
- [ ] 3.2 **Depends on: 2.2, 3.1; owns: `extensions/feature_workflow/integration.py`, `tests/test_feature_integration.py`.** Serialize verified commits through one integration owner, require dependency-order ancestry and combined plan checks, and preserve conflicted branches; verify dependents start only from a recorded integrated foundation.
- [ ] 3.3 **Depends on: 1.2, 1.3, 3.1, 3.2; owns: `claude_code_core/task_loop.py`, `tests/test_task_loop.py`.** Route metadata-rich plans to the deterministic ready-set lifecycle and retain sequential behavior for legacy plans; verify the fixed group slice and AI-only grouping are not used for structured plans.
- [ ] 3.4 **Depends on: 3.3; owns: `claude_discord/cogs/task_loop.py`, `tests/test_task_loop_cog.py`.** Connect visible worker threads, queue/capacity notices, status links, stop behavior, and restart recovery to the structured scheduler; verify mixed running/queued/dependency states remain distinguishable in the parent.

## 4. Linked child planning

- [ ] 4.1 **Depends on: none; owns: `extensions/feature_workflow/planning_models.py`, `tests/test_planning_models.py`.** Define compact child handoff, split identity, parent/child status, acknowledgement, dependency, and ownership records; verify bounded serialization contains every required field and no transcript.
- [ ] 4.2 **Depends on: 4.1; owns: `extensions/feature_workflow/planning_registry.py`, `tests/test_planning_registry.py`.** Persist parent-child links and intent-first creation state atomically; verify restart restores links and never creates a duplicate child for the same split identity.
- [ ] 4.3 **Depends on: 4.1; owns: `extensions/feature_workflow/split_policy.py`, `tests/test_planning_split_policy.py`.** Implement explicit-separation handling and clear/uncertain/keep-together decisions from distinct goals, acceptance criteria, dependencies, and writable scope; verify uncertain fixtures require one question and overlapping scope produces ordering/ownership guidance.
- [ ] 4.4 **Depends on: 4.2, 4.3; owns: `extensions/feature_workflow/planning_discord.py`, `tests/test_planning_split_discord.py`.** Create linked Discord child planning threads with compact context, bounded decision updates, acknowledgements, and parent status summaries; verify two children stay isolated and completion reports exactly once.

## 5. Final wiring and migration

- [ ] 5.1 **Depends on: 3.4, 4.4; integration owner only; owns: `claude_discord/setup.py`, `claude_discord/ext/api_server.py`, `claude_discord/__init__.py`, `tests/test_setup.py`, `tests/test_api_server.py`.** Wire only the generic correlation/parent metadata and extension hooks needed by both flows; verify existing consumers work with defaults and the control plane remains localhost-only.
- [ ] 5.2 **Depends on: 5.1; integration owner only; owns: `extensions/feature_workflow/skill/SKILL.md`, `extensions/feature_workflow/skill/references/coordinator.md`.** Update operator guidance for structured plan metadata, automatic splits, no product cap, infrastructure queueing, integration ownership, and rollback; verify documented commands match CLI help.
- [ ] 5.3 **Depends on: 5.2; integration owner only; owns: no source files.** Run the Check command, `uv run ruff check claude_code_core claude_discord extensions/feature_workflow`, `uv run pyright claude_code_core claude_discord extensions/feature_workflow`, and the security checklist; record all passing evidence before enabling structured parallel runs.

## How to try it

1. Start a disposable plan with four independent owned paths and confirm four linked workers are submitted while relay-limited ones say queued rather than blocked by `/gowork`.
2. Add a dependency and an overlapping path, then confirm the dependency waits for verified integration and the overlap is rejected before dispatch.
3. During planning say “make this separate,” confirm one compact child thread appears, restart the bot, and confirm the parent restores the same link without a duplicate child.
