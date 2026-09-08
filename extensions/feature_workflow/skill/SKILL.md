---
name: lockin-feature-workflow
description: Plan distinct features with OpenSpec in Lockin AI Discord conversations, or dispatch approved feature tasks to visible Ebi workers and integrate their results. Use for feature planning, approved parallel builds, and resuming a coordinated build.
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

## Hand off an approved build

Read [the coordinator guide](references/coordinator.md) for the executable
commands and manifest. Pin the reviewed plan's files and digest, committed
foundation, scope, dependencies, file owners, and one integration owner. Commit
artifacts before dispatch so workers receive the exact same foundation. Preserve
an approval reference to the user's actual instruction; never invent approval.
Only approve the features covered by it. Revised scope needs its own approval.

Use the selected skills' task descriptions: objective, owned files, interface,
acceptance criteria, and scope. Shared files have one owner. Independent tasks
can run together; real prerequisites must be integrated before dependents start.

Run the standalone coordinator with a separate state directory per run. On a
busy server, use `--queue-ready` so approved builders enter Ebi's existing queue
while current conversations finish; keep the configured execution limit intact. It
creates actual Discord threads and verifies worker results. Use the existing
Ebi API URL, discover the current server's workers channel and concurrency
setting, and keep these in local run configuration. Do not reconfigure/restart
Ebi. Its global session limit includes manager turns; after dispatching, end
an idle manager turn so workers can run. The external watcher consumes no AI
slot and queues a completion message to the integration owner.

## Integrate and finish

On a coordinator completion message, read state and worker evidence. Review
commits and merge them into the integration owner's isolated worktree in
prerequisite order. Rerun focused checks on the combined behavior, not only
individual modules; mark integrations through the coordinator. If approved
dependent tasks remain pending, restart the watcher after integration and end
the idle manager turn again. Each watcher stops at an integration frontier. Only the plan
owner updates plan task checkboxes, after preserving the approved snapshot.

Send the user worker links, the integrated outcome, and one concrete way to
try it. Preserve commits and push to an existing authorized backup remote;
creating/publishing a new repository is a separate scope. Keep their normal
conversation as the entry point, so they do not need to operate a terminal.

For stalled or interrupted work, inspect durable state and the existing thread
before continuing it. Completed results are collected again without rebuilding.
Ambiguous dispatch/handoff requires reconciliation; the coordinator deliberately
avoids blind retries because Ebi's spawn API has no idempotency key.
