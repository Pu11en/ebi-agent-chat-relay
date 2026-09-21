---
name: lockin-feature-workflow
description: Plan distinct features with OpenSpec in Lockin AI Discord conversations, split clearly independent builds into linked child planning threads, or dispatch every approved dependency-ready task to visible Ebi workers and integrate their results through one owner. Use for feature planning, approved parallel builds, and resuming a coordinated build.
---

# Lockin feature workflow

Use OpenSpec for plans, `parallel-feature-development` for ownership and
interfaces, and `task-coordination-strategies` for task dependencies. Their
Claude TaskCreate/TaskUpdate examples map to the Ebi coordinator manifest;
Discord workers are created through Ebi, not native subagents.

## Plan a feature

First check for this thread's durable binding at
`~/.local/state/lockin-ai/feature-workflow/*/threads/$DISCORD_THREAD_ID.json`.
Use its absolute repo/worktree and change on resumed worker/planning turns;
Ebi may remove a clean session worktree after a turn. Recreate a missing one
from its existing `session/<thread-id>` branch before continuing; inspect any
existing result first so finished work is not rebuilt.
Ebi's spawn API does not change the default cwd. Otherwise identify the feature
from the current conversation and inspect the project and existing constraints. Each feature has one named `openspec/changes/<change>/`
and one planning owner. Record `owner.json` in that directory with `change`,
`thread_id`, and decisions with reasons. Resume the change bound to this
Discord thread. Save a per-thread JSON binding (repo, worktree, change,
plan_owner and role) in the local run's `threads/` directory as well, so the
next fresh CLI process can recover the feature from its Discord thread ID.
A new conversation about a different feature gets a different
change; ambiguity about feature identity requires clarification, not selection
of a global current plan. Preserve old plans and confirmed decisions as reference.

Use a clean isolated git worktree. Initialize OpenSpec with `openspec init
--tools codex` only when this project has no OpenSpec setup. Use the generated
OpenSpec proposal/exploration instructions to inspect and save the requested
artifacts. Keep discussion conversational; ask only for decisions that matter
and cannot be discovered in the project. The user's existing authorization
covers the requested work: saving an authorized plan does not require a second
permission ceremony. A planning-only request does not authorize a build.

### Write structured plan metadata

Parallel safety comes from the plan's metadata, never from a model's reading
of the prose. Every task in `tasks.md` that may run beside another carries a
bold metadata prefix the coordinator parses:

```
- [ ] 2.1 **Depends on: 1.1, 1.3; owns: `pkg/worker.py`, `tests/test_worker.py`.** What to build.
- [ ] 5.1 **Depends on: 3.4; integration owner only; owns: `pkg/setup.py`.** Wiring.
```

`Depends on` names task IDs (or `none`); `owns` lists relative POSIX files or
directories (with a trailing `/`) this task alone may write; `check:` names a
focused command when the plan-level `Check:` line is not enough; `integration
owner only` marks work only the integrator may do. Owned paths may never include
the plan, `.git/`, `.worktrees/`, or `openspec/`. Two tasks that could run at the
same time may not own the same path or a directory containing it — write an
ordering dependency or give the shared file one owner. Tasks without metadata
still work: the plan runs one task at a time, exactly as before, and never
claims parallel safety it cannot show. See
[the coordinator guide](references/coordinator.md) for the full syntax.

### Split independent builds into child planning threads

When the conversation starts describing a second build, propose it as a
structured candidate — goal, acceptance criteria, writable paths, whether it
changes the active build's acceptance criteria, and any dependency on the
active build. The split policy decides, not the model:

- a distinct goal with its own acceptance criteria, no change to the active
  build's criteria and no shared writable files → a linked child planning
  thread is created automatically, without interrupting the active plan;
- any of those unknown or borderline → ask the user **one** split-or-keep
  question, then act on the answer;
- work that changes the active build's acceptance criteria or has a hard
  technical dependency on it → keep it together and say why.

"Make this separate" (or "its own thread", "split this out") is honored as a
request to create the child, unless a hard technical dependency makes separate
planning unsafe — then explain the dependency in the parent instead. The child
receives a compact structured handoff (goal, project and computer, only the
locked decisions that bind it, dependencies, ownership, restrictions,
authority, expected output, parent link), never the parent transcript. Each
child owns exactly one build plan; the parent thread stays the coordinator and
shows every child's link, goal, state, dependencies and blocker. Overlapping
writable paths between children are recorded in the parent as an ordering
dependency or a single owner before either build is dispatched.

## Hand off an approved build

Read [the coordinator guide](references/coordinator.md) for the executable
commands and manifest. Pin the reviewed plan's files and digest, committed
foundation, scope, dependencies, file owners, and one integration owner. Commit
artifacts before dispatch so workers receive the exact same foundation. Preserve
an approval reference to the user's actual instruction; never invent approval.
Only approve the features covered by it. Revised scope needs its own approval.

Use the selected skills' task descriptions: objective, owned files, interface,
acceptance criteria, and scope. Shared files have one owner. Independent tasks
run together; real prerequisites must be integrated before dependents start.

There is **no fixed `/gowork` worker number**. Every task whose dependencies are
integrated and whose owned paths do not overlap an active task is ready, and
every ready task is submitted. Ebi's global semaphore, the provider, and the
machine remain the only backpressure: a submitted turn that has to wait shows as
*queued* (infrastructure), which is a different state from *pending* (waiting on
a `/gowork` dependency) — keep the two distinguishable in the parent thread.
`--max-running` is a compatibility bound on this run's outstanding submissions,
not a worker policy; do not tune it to shape parallelism.

Run the standalone coordinator with a separate state directory per run. It
creates actual Discord threads and verifies worker results. Use the existing
Ebi API URL, discover the current server's workers channel and concurrency
setting, and keep these in local run configuration. Do not reconfigure/restart
Ebi. Its global session limit includes manager turns; after dispatching, end
an idle manager turn so workers can run. The external watcher consumes no AI
slot and queues a completion message to the integration owner.

Every simultaneous task gets its own `session/<thread-id>` branch and
`.worktrees/wt-<thread-id>` checkout rooted at the recorded foundation. A worker
edits only its owned paths there — never the plan, runtime state, the
integration checkout, or another worker's checkout — and never merges or spawns
workers.

## Integrate and finish

On a coordinator completion message, read state and worker evidence. Exactly
**one integration owner** combines verified results: review commits and merge
them into the integration owner's isolated worktree in prerequisite order,
rerun the plan-level check on the combined behavior after each wave, and mark
each integration through the coordinator. A dependent starts only from a
recorded integration commit that contains its prerequisites. If approved
dependent tasks remain pending, restart the watcher after integration and end
the idle manager turn again. Each watcher stops at an integration frontier. Only
the plan owner updates plan task checkboxes, after preserving the approved
snapshot.

A worker thread is archived only after its commit is recorded as integrated
and the combined check passes: post the final outcome in the worker and parent
threads, then archive without locking or deleting, keeping the parent's
deep-link. Failed, blocked, conflicted, ambiguous, and verified-but-unintegrated
workers stay open. A crash between integration and archival is recovered by
retrying the same archive request; already archived counts as success.

Send the user worker links, the integrated outcome, and one concrete way to
try it. Preserve commits and push to an existing authorized backup remote;
creating/publishing a new repository is a separate scope. Keep their normal
conversation as the entry point, so they do not need to operate a terminal.

For stalled or interrupted work, inspect durable state and the existing thread
before continuing it. Completed results are collected again without rebuilding.
Ambiguous dispatch/handoff requires reconciliation: a spawn that carried a
`correlation_id` can be looked up instead of guessed at; one that did not stays
ambiguous until a person matches the thread. Never turn uncertainty into a new
spawn.

### Rollback

Rollback never deletes anything. To fall back: stop the coordinator's own
transient unit (existing workers keep running independently), route new plans
through the preserved sequential path by leaving out the parallel metadata or
using a legacy plan, and pause automatic child creation by answering the
split question with "keep together". Worker branches, run state, results, and
child links stay where they are for later integration or inspection.
