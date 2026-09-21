# Go Work Planning Evidence

## Local Evidence

- Base branch: feat/task-loop; starting commit 309a85b.
- Prior work: 8736a4e raised the parallel cap; d35c3b7 saved OSS research; 309a85b aligned default session capacity.
- Research input: local-plans/gowork-oss-patterns.md; reuse its primary-source findings, not a new framework selection.
- claude_code_core/task_loop.py: MAX_PARALLEL is 10; _check_group runs combined checks and reviews; _reviewed catches review exceptions and returns no objection.
- claude_discord/cogs/task_loop.py: _run_group awaits asyncio.gather before processing results, then deletes worker threads; exception results bypass normal worker cleanup.
- docs/plans/gowork-v3.md has stale text claiming parallel steps lack reviews. The code is stronger evidence.
- Live /api/sessions reports capacity.limit=10. Shared capacity includes ordinary sessions; ten concurrent builders plus chat has not been demonstrated.
- Earlier audit summary reported repeated review catches, model-choice errors, status retries and owner-action pauses. Do not invent aggregate counts or claim every session was audited.
- Another thread implemented handoff parsing in the main repo's worktree. Confirm its contract during implementation before reusing it; this branch has no claude_code_core/handoff* match.

## Research to Proposed Work

- A2A identity/result messages and typed filtered handoffs: T04, T07.
- LangGraph delegation and aggregation: T05, T08-T10.
- CrewAI and AutoGen graph/state concepts: T01-T05, T11, T13.
- Prefect task states and SDK tracing concepts: T04, T14, T16.
- LangGraph Swarm persisted responsibility: T04, T11.
- AutoGen Studio reusable-role visualization: possible later view, not a first-release builder UI.
- Prefer existing stores, REST control plane, CLI-backed workers and readable plans; no new framework dependency chosen.

## Boundaries

- User asked for a draft plan followed by grilling, not implementation or a new worker launch.
- Ten-way behavior's exact scope, retention, failure policy and launch method remain decisions.
- Prior untracked .gowork-catchup.md is unrelated and untouched.
- No skill hooks installed and no shared active-plan pointer changed.
