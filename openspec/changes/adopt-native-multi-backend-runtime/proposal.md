## Why

Lockin AI already provides the Discord experience Drew wants, but its Codex adapter launches and parses a new `codex exec` process for every turn, while safe parallel editing is not guaranteed for projects without local Git. The runtime should become more native, efficient, and backend-neutral without making Discord harder to use or weakening any working behavior.

## What Changes

- Introduce a capability-based backend contract so Codex App Server, Claude Code, and future coding harnesses can coexist behind the same Discord experience.
- Add a native Codex App Server adapter for persistent threads, structured events, steering, interruption, token/context reporting, and native session lifecycle operations.
- Preserve the existing Claude Code adapter and make unsupported backend capabilities degrade explicitly rather than silently.
- Establish a recorded parity suite and canary cutover: the new Codex adapter cannot become the default until it matches the current Discord workflow and can roll back without losing conversations.
- Make local repository readiness a prerequisite for parallel editing, automatically creating a local Git baseline when safe and otherwise keeping work single-session with a clear explanation. GitHub remains optional.
- Reduce repeated model context by replacing broad per-turn operational prose with compact, state-derived instructions and references.
- Add an on-demand harness doctor for configuration, backend readiness, stale runtime artifacts, and context overhead. It performs no continuous monitoring.
- Remove legacy runner code and stale artifacts only after replacement ownership and rollback evidence prove they are unused.
- Borrow deterministic worktree and verification concepts from HAR, but do not install HAR, Intense-Visions, Harnessworks, or another competing orchestrator into the live runtime.

## Capabilities

### New Capabilities

- `multi-backend-session-runtime`: Backend-neutral session lifecycle with native Codex App Server support, retained Claude Code support, and explicit capability discovery.
- `discord-runtime-parity`: Observable compatibility, canary, rollback, and recovery requirements that prevent the internal migration from degrading the current Discord experience.
- `parallel-repository-readiness`: Safe local-Git readiness and isolated-worktree requirements before parallel editors are dispatched.
- `context-efficiency-and-doctor`: Bounded dynamic context and an on-demand diagnostic/cleanup surface with no persistent review monitor.

### Modified Capabilities

None. This repository has no existing behavioral capability specifications.

## Impact

- Affects `claude_code_core` backend contracts and Codex/Claude adapters, Discord session orchestration, status rendering, session persistence, feature-worker dispatch, worktree management, and configuration.
- Adds an official Codex App Server protocol integration behind the existing relay interface.
- Preserves current Discord channels, `/cdnew`, project folders, thread history, queues, Stop controls, attachments, skills, voice integration boundaries, and the feature workflow.
- Requires protocol fixtures, fake-server tests, parity scenarios, migration state, and an immediate rollback path before live cutover.
- Does not require GitHub, a second dashboard, a second orchestrator, continuous harness monitoring, telemetry, or user-facing orchestration vocabulary.
