# Switch Lockin AI to feature-based planning

Status recorded September 7, 2026.

Setup has started in thread 1546685954167672912. OpenSpec and the selected
skills are installed; the standalone coordinator passes 32 focused checks.
Two separate planning threads produced validated plans. Approved build workers
are waiting for two free session slots; readiness is not yet established.
The external watcher will return to the integration owner automatically.
See [continuation notes](feature-workflow-resume.md). The original setup
handoff below is preserved for reference.

## What is already done

The Portable Planner plugin was already disabled. The shared Codex
`/mnt/c/Users/drewp/.codex/AGENTS.md` no longer directs agents to invoke it.
Its old planning files remain reference material; no project plan was deleted
or migrated. The prior shared instructions were backed up in
`setup-evidence/codex-agents-before-planner-retirement-2026-09-07.md`.

Normal Codex planning is the interim behavior. OpenSpec plus automatic Ebi
worker coordination is a selected direction, not a deployed capability.
Existing in-flight turns were not stopped or restarted.

## Drew's next action in Discord

1. In `#control-center`, use `/cdnew`, type `lockin`, and select `Lockin AI`.
   This creates a fresh conversation about the same project, leaving the
   voice-transcription conversation alone. The picker may name the thread
   `ebi-agent-chat-relay`; that is the real folder behind the Lockin AI shortcut.
2. Send this message:

   > Set up the agreed feature-based planning and parallel-build workflow for
   > Lockin AI. Read LOCKIN-AI.md and docs/feature-workflow-setup.md first.
   > Use OpenSpec and the two coordination skills we selected. Keep existing
   > projects and plans intact. Prove it with two small isolated features and
   > real Discord worker threads before calling it ready. Keep my other
   > running conversations intact. Walk me through only the parts I need to do.

You do not need to enter terminal commands or remember full folder paths.

## Setup handoff for the agent receiving that message

The detailed source review is `docs/parallel-feature-workflow-2026-09-07.md`.
When working in an isolated worktree, read these setup notes from the canonical
repository `/home/drewp/main-projects/ebi-agent-chat-relay` if the notes are not
present in the checkout; they were local untracked documentation at handoff.

1. Inspect current installed tools and active Ebi sessions. Continue from
   actual state; avoid duplicating installations or another session's work.
2. Set up OpenSpec and the narrowly selected `parallel-feature-development`
   and `task-coordination-strategies` skills through supported Codex paths.
   Keep source provenance and required references. Select only this workflow,
   not the entire Claude Agent Teams runtime. Keep development source on WSL.
3. Bind each feature conversation to its own named OpenSpec change and plan
   owner, preserving shared project constraints and confirmed old decisions.
   Replace planning state only within the trial's agreed scope.
4. Implement the Ebi-specific handoff: approved plan revision, dependency-ready
   task selection, actual Discord worker threads, isolated worktrees based on
   the agreed foundation, worker results, and one integration owner. Reuse
   Ebi's existing interfaces; preserve upstream core behavior where possible.
   Account for the actual global session limit and release idle manager turns
   so queued workers can run. Worker recovery must not duplicate finished work.
5. Prove a small disposable two-feature trial: separate planning conversations
   retain separate decisions; only approved tasks run; independent builds use
   distinct worktrees and visible Discord threads; the lead collects results
   and integrates them; focused checks and a human try-it step are available.
   Code isolation alone is not proof of correct integration. Keep existing
   feature work, live data, credentials, and running sessions outside the trial.
6. Report readiness against those observations. If a restart is needed, arrange
   it without interrupting unrelated active work. Update the cheat sheet with
   the verified workflow and show Drew the exact next Discord action.

No new repository on GitHub, remote publication, full-server restructuring, or
replacement of other projects' planning state is implied by this setup handoff.

## Everyday usage after the trial passes

- New feature: `/cdnew` → choose the project → describe the feature.
- Continue a feature: reply in that feature's existing thread.
- Planning: discuss outcomes and trade-offs; the agent maintains the feature plan.
- Build: approve the plan; the configured coordinator dispatches eligible work.
- Inspect or steer: open the relevant worker thread and use its supported controls.
- Finish: review the integrated build and try it yourself.

These last steps describe the intended verified end state, not functionality
already established by retiring Portable Planner.
