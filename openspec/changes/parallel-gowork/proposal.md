## Why

The live `/gowork` loop intentionally runs one task at a time even when several tasks are
independent, and planning discussions can accumulate unrelated builds in one thread. Drew needs
safe automatic parallelism without an arbitrary product-level worker cap or shared writable files.

## What Changes

- Parse task dependencies and explicit owned paths, rejecting cycles and overlapping ownership.
- Dispatch every dependency-ready, non-conflicting task, leaving real relay/provider/machine
  backpressure to queue execution rather than imposing a fixed `/gowork` worker number.
- Create one branch and worktree per simultaneous task and integrate verified results through one
  owner in dependency order.
- Preserve a fresh session per small task, deterministic saved state, focused checks, and restart
  recovery.
- Detect clearly independent builds during planning and automatically create linked child planning
  threads; ask only when the boundary is uncertain and support explicit “make this separate.”
- Give child planners compact structured context: goal, project, decisions, dependencies,
  restrictions, and parent link.
- Keep the parent planning thread as the status/integration coordinator across child builds.
- After a worker result passes its combined checks and is recorded as integrated, post its final
  outcome and automatically archive that worker thread. Failed, blocked, conflicted, or merely
  finished-but-unverified threads remain open; archival never deletes or locks the thread.

## Capabilities

### New Capabilities
- `parallel-task-loop`: Dependency-aware, file-safe parallel `/gowork` execution and verified
  integration without a fixed product worker cap.
- `planning-thread-split`: Automatic linked child planning threads for independent builds with
  compact context handoff and parent status tracking.

### Modified Capabilities

None.

## Impact

This affects the task loop, work-copy manager, loop store/API, Discord planning/session creation,
watchers, queueing, integration, thread archival, and recovery. Infrastructure capacity remains a
separate safety mechanism and must not be described as a `/gowork` worker policy.
