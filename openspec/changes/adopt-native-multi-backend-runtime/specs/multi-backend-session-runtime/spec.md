## Purpose

Provide one stable Discord-facing session lifecycle while allowing Codex, Claude Code, and future coding harnesses to use their strongest native capabilities.

## ADDED Requirements

### Requirement: Backend-neutral lifecycle
The system SHALL expose a common lifecycle for starting, resuming, steering, interrupting, and completing sessions without requiring Discord users to learn backend-specific commands.

#### Scenario: User continues a thread
- **WHEN** a user sends a normal message in an existing project thread
- **THEN** the configured backend resumes the correct native session and returns progress and results in that same Discord thread

### Requirement: Explicit backend capabilities
Each backend SHALL report which lifecycle operations it supports, and the system SHALL provide an explicit bounded fallback or a clear explanation for unsupported operations.

#### Scenario: Backend lacks live steering
- **WHEN** a user messages a running session whose backend cannot accept live steering
- **THEN** the system queues or interrupts according to the user's intent and does not silently discard the message

### Requirement: Native Codex integration
The Codex backend SHALL use the supported local App Server transport for structured threads, turns, items, interruption, and usage events rather than depending on terminal-text interpretation.

#### Scenario: Codex turn reports progress
- **WHEN** Codex emits structured command, edit, message, usage, or completion events
- **THEN** the Discord thread displays their normalized equivalents without parsing human-oriented terminal output

### Requirement: Claude Code remains supported
The migration SHALL preserve Claude Code as an independently authenticated peer backend whose sessions never receive Codex-native identifiers or operations.

#### Scenario: Friend selects Claude Code
- **WHEN** an authorized Discord user selects a configured Claude Code backend
- **THEN** new work uses that user's Claude Code runtime while existing Codex sessions remain resumable by Codex

### Requirement: Future adapter boundary
A new coding harness SHALL be addable through the common lifecycle and capability contract without changing Discord project/thread behavior.

#### Scenario: New adapter is registered
- **WHEN** a future adapter satisfies the required lifecycle contract and passes conformance tests
- **THEN** it can be selected without adding a competing Discord bot or orchestration interface
