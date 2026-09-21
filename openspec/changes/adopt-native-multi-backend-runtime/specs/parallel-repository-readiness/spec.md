## Purpose

Ensure parallel coding sessions edit isolated local repositories safely while keeping GitHub optional for new and private projects.

## ADDED Requirements

### Requirement: Parallel readiness preflight
Before dispatching two or more editing workers, the system SHALL verify a local Git repository, a committed foundation, a clean integration owner, and creatable isolated worktrees.

#### Scenario: Repository is ready
- **WHEN** a planned parallel build passes every readiness check
- **THEN** workers receive distinct worktrees and branches from the same recorded foundation commit

### Requirement: Safe local Git bootstrap
For a normal project folder without Git, the system SHALL offer or perform a bounded local-only initialization when the approved build requires parallel editing, while excluding secrets and generated dependencies from the baseline.

#### Scenario: Plain project folder needs parallel work
- **WHEN** the project is not a Git repository and a safe baseline can be established without publishing anything
- **THEN** the system creates a local repository and baseline commit before dispatch and does not create a GitHub remote

### Requirement: Safe fallback
The system SHALL NOT dispatch parallel editors when repository readiness is ambiguous or unsafe.

#### Scenario: Baseline contains uncertain secrets
- **WHEN** the readiness preflight cannot confidently exclude credentials or sensitive generated data
- **THEN** the system keeps implementation single-session and tells the user what blocks safe parallelization

### Requirement: Deterministic worker proof
Each worker result SHALL identify its foundation, owned files, resulting commit, verification result, and any unresolved integration risk.

#### Scenario: Worker completes
- **WHEN** a worker reports completion
- **THEN** the integration owner can verify the exact commit and acceptance evidence before merging it
