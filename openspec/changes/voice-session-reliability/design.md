## Context

See proposal.md and evidence.md. Drew approved only the local missing-sentence repair by selecting A (fix the bug locally, offline tests, no restart). This is the first implementation slice of the wider requirements, not approval to implement the rest.

## Goals / Non-Goals

The approved slice prevents a normal-running request from flushing while its owner has relevant captured speech awaiting transcription. It also respects genuine ten-second capture gaps rather than merging separate requests whose transcripts arrive late.

No new recovery, delivery receipts, UI controls, dependency, database schema, model call, runtime switch or restart. Existing explicit shutdown flushing remains unchanged. Later UI requirements stay in PLAN.md; their design is not declared ready by this slice.

## Decisions

- Reuse the existing transcription-jobs store for unfinished capture metadata and expose active capture metadata from the existing transport. The controller receives a synchronous, injectable snapshot getter scoped by voice session and speaker. This avoids a second job registry, lifecycle event bus or new persistence scheme.
- At the silence deadline, check whether unfinished capture overlaps or continues the current request within ten seconds. Wait and recheck at a bounded interval only while necessary. Future speech separated by a true ten-second pause, other speakers and other recording sessions must not block this request.
- Compare completed utterances' capture times before appending, so delayed delivery cannot merge separate addressed requests. Preserve existing owner authorization, tag locking and recognition-noise handling.
- Keep the current request reference stable while awaiting its listening acknowledgment; schedule normal automatic delivery after acknowledgment completes. This is a prerequisite for safely exercising overdue deadlines, not a separate UI redesign.
- Use deterministic fake clocks and real temporary SQLite/queue objects for regression checks. No live microphone or provider is needed.

## Risks / Trade-offs

- Capture-derived timestamps approximate speech timing → retain the existing metadata convention; test the reported timestamps and active capture boundaries.
- A pending recognition retry must not be mistaken for completed words → include future-backoff jobs in the snapshot; do not change recognition retry policy or add automatic instruction retry.
- Polling incurs small local reads while speech is unfinished → only poll an open request at its deadline, with a bounded delay, never a zero-delay spin.
- Queue/recognizer failures beyond the reported successful-but-delayed case are not a promise of recovery → keep existing transcript/audio retention and report limitations; no restart subsystem.

## Migration Plan

No schema or config migration. Build and verify only in this isolated worktree. A later approved try-out must update the separate voice runtime deliberately; the Python bot's import hook does not update it. Leave both live services untouched now.

## Later slices

The previously selected live draft, queue receipts, editing, manual retry and destination chooser are not part of the approved repair. Before any such slice, finalize its design against the recorded requirements and obtain implementation approval; do not treat the existence of this artifact as approval or full planning completion for those features.
