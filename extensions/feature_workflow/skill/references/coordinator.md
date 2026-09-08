# Ebi coordinator operations

The installed `lockin-workflow` command is an agent-operated CLI. Use `--help`
and subcommand help for exact arguments. Its source remains in a verified WSL
worktree; read the installed command's target if you need to inspect it.

## Run contract

Commit a manifest in the integration worktree. Give each run a unique run_id
and its own state directory under `~/.local/state/lockin-ai/feature-workflow/`.
The feature plan is ordinary OpenSpec artifacts; the manifest is the executable
handoff, not a competing global plan. Example shape (replace placeholders):

```json
{
  "schema": 1,
  "run_id": "feature-batch-name",
  "foundation_sha": "FULL_COMMITTED_SHA",
  "integration_owner": "DISCORD_MANAGER_THREAD_ID",
  "backup_remote": "EXISTING_AUTHORIZED_REMOTE",
  "features": [{
    "id": "feature-name",
    "change": "feature-name",
    "plan_owner": "DISCORD_PLANNING_THREAD_ID",
    "approved_revision": "PLAN_DIGEST",
    "plan_files": ["openspec/changes/feature-name/proposal.md",
                   "openspec/changes/feature-name/design.md",
                   "openspec/changes/feature-name/tasks.md",
                   "openspec/changes/feature-name/specs/capability/spec.md",
                   "openspec/changes/feature-name/owner.json"]
  }],
  "tasks": [{
    "id": "feature-build",
    "feature": "feature-name",
    "depends_on": [],
    "owned_paths": ["src/feature/", "tests/test_feature.py"],
    "prompt": "Objective, exact interfaces, scope, checks, and completion evidence."
  }]
}
```

Compute plan digests using the coordinator's `plan-digest` command (or exported
`plan_digest` function) after plan files are finalized. The foundation must
contain those exact plan files and shared contracts. Commit the manifest after
inserting the digest. Do not alter approved files while that run is active;
create a reviewed revision/new run when scope changes.

## Dispatch

Invoke the CLI with absolute `--repo`, `--state-dir`, and `--api-url`, plus
relative `--manifest`. Set `--channel-id` to this server's existing workers
channel and `--max-running` to the actual Ebi global session limit.

1. `approve --authorization-ref "<user message permalink or scoped request>"
   --feature <id>` records approval for just that feature. Repeat for other
   approved features. `tick` before approval must create no worker.
2. Run `tick` to dispatch ready work or `watch --interval 15` to continue
   outside the manager turn. Use a separate transient systemd user unit with
   an absolute executable, working directory, and log location so it survives
   the manager ending its turn. This starts a new coordinator unit, not Ebi.
3. Link workers from `status`, then end an idle manager turn. The watcher sends
   a queue-mode handoff to the integration owner when results need integration.

The coordinator counts all Ebi sessions reported running, which includes some
queued turns. Capacity is conservative; Ebi's semaphore is authoritative and
races with other new sessions can queue a worker. Existing sessions are never
stopped to make room.

## Results and integration

The worker brief names its absolute worktree, committed foundation, approved
plan, ownership, and result JSON path. Workers commit and push their assigned
branch and write results only after focused checks. `collect` verifies result
identity, revision, ancestry, session branch, and owned-file changes.
Ebi removes clean session worktrees at turn end, so missing worker checkouts
are recovered from their durable Git branch; existing ones must still be clean. Test evidence is
reported by the worker; the integration owner must independently rerun checks.

Merge verified worker commits (preserving ancestry, not cherry-picking) into
the lead's isolated worktree, then use
`integrate --task <id> --commit <integrated-sha>`. Dependents start only from a
foundation that includes their actual prerequisites. `watch` exits after
handoff, so restart it after integration when approved dependent tasks remain
pending, then end the idle manager turn. Finish with a combined
behavior check and user try-it instructions. Keep the immutable approved plan
snapshot even when the plan owner later updates completion checkboxes.

## Recovery

Re-run `status` and `collect`; completed work is not dispatched again. A spawn
with an uncertain HTTP outcome stays ambiguous. Read the existing Discord
threads and use `reconcile --task <id> --thread-id <known-thread>` only after
matching the exact run/task to that thread. Never turn uncertainty into a new
spawn. For an interrupted worker, read its existing worktree and result first,
then resume that same thread within its original scope.

Only the coordinator's own transient unit is safe to stop for a pause. Pausing
it stops future dispatch; existing workers remain independent. Ebi's red Stop
button stops the chosen worker. The manager should tell affected workers about
scope changes and revise the approval before any replacement work begins.
