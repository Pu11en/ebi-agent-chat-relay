## Purpose

Runs independent `/gowork` tasks concurrently in isolated worktrees while preserving dependency
order, deterministic recovery, focused verification, and one controlled integration path.

## ADDED Requirements

### Requirement: Plans declare dependencies and owned paths
The task loop SHALL accept explicit task dependencies and owned repository paths, SHALL reject
unknown dependencies, cycles, invalid paths, and overlapping ownership among tasks that could run
at the same time, and SHALL preserve sequential compatibility for plans without parallel metadata.

#### Scenario: Valid independent tasks
- **WHEN** open tasks declare satisfied dependencies and non-overlapping owned paths
- **THEN** the loop identifies each task as independently dispatchable

#### Scenario: Dependency cycle
- **WHEN** task dependencies contain a cycle
- **THEN** the build is rejected before any worker is dispatched and the cycle is named

#### Scenario: Independent ownership overlaps
- **WHEN** two tasks that could run together own the same path or overlapping directory scopes
- **THEN** the build is rejected before either task is dispatched and the conflicting ownership is reported

#### Scenario: Legacy plan lacks parallel metadata
- **WHEN** a valid existing plan has ordinary checkboxes but no explicit dependencies or owned paths
- **THEN** the loop continues to process it safely one task at a time

### Requirement: Dispatch every safe ready task
The loop SHALL submit every open task whose dependencies are integrated and whose owned paths do
not conflict with an active task. It MUST NOT impose a fixed product-level `/gowork` worker count;
relay, provider, operating-system, or machine capacity MAY queue submitted turns without changing
which tasks are logically ready.

#### Scenario: Four tasks are ready
- **WHEN** four open tasks have satisfied dependencies and pairwise non-overlapping ownership
- **THEN** all four are submitted without waiting for an arbitrary `/gowork` worker slot

#### Scenario: Relay is busy
- **WHEN** a ready task is submitted while the relay or provider is at capacity
- **THEN** the task remains durably queued or waiting and the user-visible status distinguishes infrastructure backpressure from a `/gowork` dependency

#### Scenario: Dependency is not integrated
- **WHEN** a task's worker finished but its required dependency has not been verified and integrated
- **THEN** the dependent task remains pending and is not submitted

### Requirement: Isolate every simultaneous task
Every simultaneously active task SHALL run from its own branch and Git worktree rooted at the
verified foundation that contains all integrated dependencies. A worker MUST be restricted to its
declared owned paths and MUST NOT edit the shared plan, runtime state, integration checkout, or
another worker's worktree.

#### Scenario: Two tasks start together
- **WHEN** two independent tasks are dispatched
- **THEN** each receives a distinct branch, worktree, task identity, foundation commit, and owned-path boundary

#### Scenario: Worker changes an unowned path
- **WHEN** a worker result includes a change outside that task's owned paths
- **THEN** verification rejects the result and it is not integrated

### Requirement: Keep one integration owner
Exactly one integration owner SHALL combine verified task results into the integration checkout in
dependency order. A task SHALL count as satisfying dependencies only after its result ancestry,
identity, approved plan revision, owned paths, clean worktree, commit, and check evidence are
verified and its commit is present in the integration history.

#### Scenario: Verified result is ready
- **WHEN** a worker provides a clean committed result with valid identity, ancestry, ownership, and check evidence
- **THEN** the result is handed to the integration owner once and remains unintegrated until that owner records the integration commit

#### Scenario: Merge conflicts
- **WHEN** a verified worker result cannot be integrated cleanly
- **THEN** the task remains visible as needing integration resolution and is not falsely marked complete or automatically rebuilt

### Requirement: Preserve fresh focused worker sessions
Each task SHALL run in a fresh AI session with a compact briefing containing the whole-build goal,
the exact task, relevant approved decisions, dependency foundation, owned paths, restrictions,
required checks, and result destination. The worker SHALL execute only its task and SHALL not
launch additional workers.

#### Scenario: Worker thread is created
- **WHEN** a ready task is submitted
- **THEN** its visible Discord thread contains the task identity and receives a compact self-contained briefing without the parent thread's full transcript

### Requirement: Verify focused and integrated behavior
A worker SHALL report actual focused checks before its result can be accepted. After integration,
the integration owner SHALL run the plan-level check against the combined state; a failure SHALL
identify affected results and prevent dependent dispatch until resolved.

#### Scenario: Worker omits check evidence
- **WHEN** a result contains no focused check command and outcome
- **THEN** result verification rejects it

#### Scenario: Combined check fails
- **WHEN** individually verified commits cause the plan-level check to fail after integration
- **THEN** the build pauses dependent dispatch and reports the combined failure without discarding worker branches

### Requirement: Recover idempotently after interruption
The loop SHALL persist approval, dispatch intent, worker identity, foundation, results,
verification, integration, and user-visible state durably. Restarting after a crash or ambiguous
network response MUST NOT dispatch a duplicate worker or lose a completed result.

#### Scenario: Restart after dispatch intent
- **WHEN** the process restarts after recording spawn intent but before learning the thread ID
- **THEN** the task is marked ambiguous for reconciliation and is not submitted again automatically

#### Scenario: Restart after verified result
- **WHEN** the process restarts after a worker result was verified
- **THEN** the same result remains available for integration and the task is not rebuilt

#### Scenario: Worktree was cleaned after completion
- **WHEN** a verified result's clean worktree no longer exists but its exact session branch and commit remain
- **THEN** recovery validates the durable Git evidence without treating the task as lost

### Requirement: Keep status understandable in the parent thread
The parent thread SHALL show each task's dependency state, queue or capacity state, worker link,
verification state, integration state, and blocker, and SHALL summarize overall progress without
requiring the user to inspect every worker thread.

#### Scenario: Mixed task states
- **WHEN** some tasks are running, one is queued by infrastructure, and another waits for a dependency
- **THEN** the parent status distinguishes all three causes and links each dispatched worker
