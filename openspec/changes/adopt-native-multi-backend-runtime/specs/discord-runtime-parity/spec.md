## Purpose

Protect Drew's working Discord control center from regressions while the internal coding-agent runtime is replaced incrementally.

## ADDED Requirements

### Requirement: User-visible parity gate
The new runtime SHALL NOT become the default until automated and live canary evidence confirms parity for project selection, normal chat, streaming, attachments, questions, Stop, queueing, resume, worker dispatch, integration handoff, and completion.

#### Scenario: A parity case fails
- **WHEN** any required parity scenario fails or lacks evidence
- **THEN** the existing runtime remains the default and the failure is recorded for repair

### Requirement: Per-thread canary selection
The system SHALL support selecting the new adapter for isolated canary threads while existing threads continue on their stored backend and adapter generation.

#### Scenario: Canary is enabled
- **WHEN** the operator enables the new Codex adapter for one test thread
- **THEN** other Discord threads and active sessions continue on the existing adapter unchanged

### Requirement: Lossless rollback
The system SHALL provide a documented, tested rollback that restores the previous adapter without deleting Discord history, native session identifiers, build state, or project work.

#### Scenario: Canary rollback
- **WHEN** the new adapter fails its canary or causes worse behavior
- **THEN** the operator can restore the prior adapter and continue affected work through an explicit compatible recovery path

### Requirement: No parallel runtime ownership
Only one adapter generation SHALL own a given active native session, and migration SHALL NOT create a shadow turn in another runtime.

#### Scenario: Stored session belongs to legacy adapter
- **WHEN** a message targets a legacy session during migration
- **THEN** the legacy adapter resumes it or performs an explicit transcript handoff; the new adapter does not independently continue the same native session

### Requirement: Natural Discord operation
The migration SHALL NOT require users to mention workers, worktrees, manifests, App Server, or adapter names during ordinary planning and building.

#### Scenario: User approves naturally
- **WHEN** the conversation clearly approves an agreed build using ordinary language
- **THEN** the manager chooses direct or coordinated execution without requiring a magic phrase
