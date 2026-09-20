## Context

There are two partially overlapping implementations today. The core `TaskLoop` asks a quick AI to
group plain checkbox tasks, truncates a group to `MAX_PARALLEL = 3`, runs each in a side copy, then
merges results back into one build copy. Separately, `extensions/feature_workflow/coordinator.py`
already validates explicit dependencies and owned paths, records dispatch intent, creates visible
workers through the localhost API, verifies Git evidence, and waits for an integration owner—but
its per-run `max_running` defaults to three. Neither system currently creates durable linked child
planning threads. See the two capability specs for observable requirements.

The extension coordinator is the stronger safety foundation. The implementation should extract its
task-graph and durable-run concepts into reusable extension modules, then make `/gowork` consume
that deterministic scheduler instead of relying on AI-only grouping. ccdb remains the generic
Discord/session transport; personal automatic planning policy stays in an extension.

## Goals / Non-Goals

**Goals:**

- Make dependency and ownership metadata—not an AI guess—the authority for parallel safety.
- Submit every safe ready task while leaving actual execution backpressure to the relay/providers.
- Keep each worker isolated, fresh, visible, recoverable, and independently verifiable.
- Keep planning splits compact and linked, with one parent coordinator and one plan owner per child.
- Preserve ordinary sequential `/gowork` plans during migration.

**Non-Goals:**

- Bypassing the relay's global semaphore, provider quotas, operating-system limits, or rate limits.
- Allowing two agents to write the same worktree or the same file concurrently.
- Letting workers merge their own branches, spawn more workers, or broaden authorization.
- Treating a planning child as a second copy of the parent's full conversation.
- Auto-pushing, deploying, or consuming a new paid provider because more tasks are ready.

## Decisions

### Make a versioned task graph the scheduling contract

Add a parser/model for task ID, checkbox text, dependencies, owned POSIX paths, focused check, and
feature/plan revision. Validate unknown nodes, cycles, protected paths, and overlap before dispatch.
Tasks without metadata remain a one-node-at-a-time legacy graph. The quick AI can suggest metadata
during planning, but its response is never scheduling authority.

Alternative considered: improve the current group prompt. Rejected because model output cannot
prove file non-overlap or dependency closure and it changes between runs.

### Use one ready-set scheduler without a product worker cap

On each tick, compute every pending task whose dependency commits are recorded in the current
integration foundation and whose ownership does not overlap an active task. Persist spawn intent,
then submit all members of that ready set to Ebi. Remove both the core group slice and the
coordinator's normal per-run admission window. Existing relay/provider capacity remains observable
backpressure and queues turns after submission.

Alternative considered: raise the fixed cap. Rejected because every chosen number recreates the
same artificial bottleneck and confuses product policy with infrastructure safety.

### Reuse durable coordinator states and tighten foundations

Keep the existing progression—pending, spawning, ambiguous/dispatched, verified, integrated,
blocked—with an append-only event record or atomically replaced state plus immutable approval
digest. A task's foundation is the exact integration commit containing all dependencies at dispatch
time. A verified result never respawns; uncertain submission requires reconciliation.

Alternative considered: infer state from Discord threads. Rejected because Discord visibility and
HTTP responses are not an exactly-once job ledger.

### Give every task a normal session worktree and branch

Use the repository's supported `.worktrees/wt-<thread-id>` and `session/<thread-id>` convention for
every concurrent task, not temporary side directories hidden under one worker. The compact brief
names the approved plan revision, foundation, ownership, checks, and result record. Cleanup may
remove a clean checkout only after durable branch/result evidence exists.

Alternative considered: continue merging ad hoc side copies inside the task loop. Rejected because
their lifecycle, ownership, and Discord identity are less explicit than ordinary session worktrees.

### Serialize integration through one owner

Workers produce commits and evidence only. The parent/integration owner verifies identity, clean
state, ancestry, changed paths, and checks, then merges verified commits in dependency order and
records the new foundation. Each merge is followed by the plan-level check before dependents are
released. Parallel implementation can therefore be broad while integration remains deterministic.

Alternative considered: let workers merge when done. Rejected because completion races would make
foundations nondeterministic and let a worker modify shared state.

### Store planning links separately from chat transcripts

Add a small durable planning registry keyed by parent thread and split identity. A child record
stores its build goal, project/computer, compact handoff, state, dependencies, plan owner, and
thread ID. The splitter proposes a structured candidate; deterministic checks auto-split when the
candidate names a distinct deliverable and non-shared writable scope, ask when those fields are
ambiguous, and honor an explicit separation request.

Alternative considered: rely on pinned messages or thread names. Rejected because neither gives
idempotent recovery, dependency state, or a stable parent dashboard.

### Keep policy in an extension and transport in ccdb

The automatic split classifier, parent coordinator, and build scheduler live under
`extensions/feature_workflow/`. They use existing `spawn_session`, message relay, session status,
and frontend repositories through narrow interfaces. Generic additions to the localhost API are
limited to fields needed by any caller, such as correlation/parent identifiers if existing thread
metadata cannot carry them.

Alternative considered: put Drew's entire workflow in the reusable Cogs. Rejected because the
framework/instance boundary requires personal policy to remain an extension.

### Assign one file owner per implementation stream

The graph/schema, durable state, worker workspace, planning registry, Discord adapter, and final
integration each have disjoint primary modules and test files. Shared exports/setup files are owned
only by the final integrator. Interface modules land before dependent streams begin.

Alternative considered: let every worker patch `task_loop.py` and its monolithic test file.
Rejected because the build process would violate the same overlap rule it is introducing.

## Risks / Trade-offs

- **[Submitting all ready work floods a constrained host]** → Keep Ebi's global semaphore and
  provider queues authoritative, expose queued status, and allow an operator safety stop without
  redefining a `/gowork` worker limit.
- **[Declared ownership is incomplete]** → Verify actual changed paths before accepting results;
  reject the result rather than merging accidental scope.
- **[Two non-overlapping commits still conflict semantically]** → Run the combined plan check after
  each integration wave and retain every worker branch for correction.
- **[Legacy plans cannot parallelize]** → Preserve sequential behavior and offer a planning-time
  conversion; never guess ownership at execution time.
- **[Automatic planning split is too eager]** → Require a distinct deliverable plus safe scope,
  ask when either is uncertain, and preserve a parent-visible undo/relink action.
- **[Parent and child decisions drift]** → Version compact handoffs and send bounded decision
  updates with acknowledgement rather than replaying transcripts.
- **[Crash occurs between external side effect and local state]** → Record intent first and use
  stable correlation IDs; ambiguous operations reconcile instead of retrying.

## Migration Plan

1. Extract and test the task graph, state transitions, ownership verifier, and compact handoff
   contracts without changing current `/gowork` behavior.
2. Add the planning registry and explicit “make this separate” path; run it in report-only mode for
   automatic candidates until duplicate/recovery tests pass.
3. Route metadata-rich plans through the new ready-set scheduler while legacy plans stay on the
   sequential path. Keep current parallel grouping disabled for those new runs.
4. Remove the per-run admission cap and submit ready tasks through the existing relay queue; keep
   global infrastructure capacity and stop controls unchanged.
5. Turn on automatic child creation for clear candidates, retaining ask-on-uncertain behavior.
6. After restart, overlap, failure, and combined-check tests pass, remove the obsolete AI grouping
   and side-copy path. Rollback routes all plans to the preserved sequential executor and pauses
   automatic splitting without deleting branches, state, or child links.
