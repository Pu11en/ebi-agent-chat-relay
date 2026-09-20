## Purpose

Keep Discord turns and automated builds alive through temporary model saturation while making every retry, wait, and authorized fallback visible and safe.

## ADDED Requirements

### Requirement: Backend failures are classified by recovery behavior
The system SHALL classify a failed model run as relay queueing, temporary model saturation, provider rate limiting, subscription or quota exhaustion, authentication failure, or a permanent request error before deciding what happens next.

#### Scenario: Provider capacity is not confused with relay capacity
- **WHEN** a backend reports that the selected model is temporarily at capacity while the local relay has an execution slot
- **THEN** the system records temporary model saturation and does not describe the failure as local relay queueing

#### Scenario: Quota exhaustion is not retried as saturation
- **WHEN** a backend reports an exhausted subscription or usage quota
- **THEN** the system records quota exhaustion and presents the valid wait-or-switch action without automatic saturation retries

#### Scenario: Unknown permanent error remains terminal
- **WHEN** a backend error does not match a safe recoverable category
- **THEN** the system reports a permanent request error and preserves the original diagnostic without retrying automatically

### Requirement: Temporary saturation preserves the pending turn
The system SHALL keep the same logical turn pending when temporary model saturation occurs and SHALL show whether that turn is queued, waiting, retrying, or using an authorized fallback.

#### Scenario: Saturated interactive turn waits visibly
- **WHEN** an interactive Discord turn receives a temporary saturation result
- **THEN** the thread shows a waiting status and the user does not need to repeat the prompt

#### Scenario: Saturated automated task remains pending
- **WHEN** a headless or `/gowork` task receives a temporary saturation result
- **THEN** the task remains incomplete and is eligible for the same recovery policy as an interactive turn

### Requirement: Saturation retries are bounded and durable
The system SHALL retry temporary saturation with bounded exponential backoff, SHALL persist enough state to resume after restart, and SHALL stop at the configured attempt or elapsed-time limit.

#### Scenario: Retry delay grows within bounds
- **WHEN** the same pending turn receives repeated temporary saturation results
- **THEN** each scheduled retry uses the configured backoff sequence without exceeding its maximum delay or recovery window

#### Scenario: Bot restarts during a wait
- **WHEN** the bot restarts after a retry has been scheduled but before it runs
- **THEN** the system reloads the pending turn and resumes at or after the persisted next-attempt time

#### Scenario: Retry budget is exhausted
- **WHEN** the retry attempt or elapsed-time limit is reached without a successful result
- **THEN** the system stops automatic attempts, keeps the task recoverable, and explains the valid next actions

### Requirement: Fallbacks obey explicit authority boundaries
The system SHALL switch models or backends automatically only through a fallback chain explicitly authorized for that task, session, or computer configuration, and SHALL NOT silently introduce a paid provider or a materially different data boundary.

#### Scenario: Authorized fallback is available
- **WHEN** temporary saturation reaches the configured fallback threshold and the next fallback was explicitly authorized
- **THEN** the system announces the switch and continues the same logical turn on that fallback

#### Scenario: No authorized fallback is available
- **WHEN** temporary saturation persists and the configured chain has no remaining authorized target
- **THEN** the system continues within its retry budget or asks the user to choose instead of silently switching providers

#### Scenario: Fallback also becomes unavailable
- **WHEN** an authorized fallback returns a recoverable capacity error
- **THEN** the system advances only to another explicitly authorized target or returns to the visible waiting state

### Requirement: Recovery executes each logical turn at most once
The system SHALL give every recoverable turn a stable identity and SHALL ignore duplicate retry claims, restarts, and late provider completions after one result has been accepted.

#### Scenario: Late original response races a retry
- **WHEN** the original provider response completes after a retry has started
- **THEN** exactly one completion is accepted and delivered for the logical turn

#### Scenario: Two workers claim a restored retry
- **WHEN** concurrent workers attempt to resume the same persisted pending turn
- **THEN** only one worker acquires execution authority and the other performs no model call

### Requirement: Non-recoverable failures give a specific next action
The system SHALL stop automatic recovery for authentication failures, permanent request errors, and quota conditions that require user action, and SHALL explain the category and next valid action in plain language.

#### Scenario: Authentication failed
- **WHEN** credentials are missing, expired, or rejected
- **THEN** the system stops retrying and says that the affected computer or provider login must be repaired

#### Scenario: Provider quota is exhausted
- **WHEN** a known reset time is available for an exhausted quota
- **THEN** the system shows that reset time and offers only already-authorized alternatives
