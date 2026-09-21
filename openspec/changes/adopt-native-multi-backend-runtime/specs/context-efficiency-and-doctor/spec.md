## Purpose

Keep recurring model context and operational state small, explainable, and removable without running a permanent harness monitor.

## ADDED Requirements

### Requirement: Bounded dynamic context
Every turn SHALL receive only the current thread's necessary operational state, compact references to durable instructions, and bounded cross-session collision information.

#### Scenario: No conflicting sessions exist
- **WHEN** a project thread starts a turn with no relevant collision
- **THEN** the prompt omits unrelated lounge history and verbose coordination procedures

### Requirement: Context accounting
The system SHALL expose model-visible context sources and their approximate size so repeated overhead can be measured before it is changed.

#### Scenario: Operator requests context status
- **WHEN** the operator runs the on-demand diagnostic
- **THEN** it reports static instructions, dynamic harness context, selected skills, attachments, and native context usage separately

### Requirement: On-demand doctor
The system SHALL provide a one-shot diagnostic for backend readiness, Discord connectivity, queue health, repository isolation, stale runtime artifacts, and instruction duplication.

#### Scenario: Doctor finishes
- **WHEN** the operator requests a harness check
- **THEN** it produces current findings and exits without installing a watcher, scheduler, telemetry collector, or background process

### Requirement: Evidence-bound cleanup
The system SHALL delete generated runtime artifacts or legacy code only after ownership checks prove they are inactive, unreferenced, recoverable through source control where applicable, and outside active sessions.

#### Scenario: Stale worktree candidate is discovered
- **WHEN** a generated worktree directory appears stale
- **THEN** the doctor identifies its repository, branch, dirty state, active-session references, and cleanup consequence before offering exact deletion

### Requirement: No overlapping orchestrators
The default runtime SHALL have one owner for session lifecycle, one owner for parallel-build state, and one source for agent configuration.

#### Scenario: External harness is evaluated
- **WHEN** HAR or another open-source harness is considered
- **THEN** only non-overlapping capabilities with measurable benefit are adopted, rather than installing a second controller over the same sessions
