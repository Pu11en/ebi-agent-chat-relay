## Purpose

Define a recoverable session close lifecycle that is distinct from stopping a running turn and
works consistently for people, menus, and explicitly authorized agent workflows.

## ADDED Requirements

### Requirement: Stop preserves an open session
`/stop` SHALL interrupt only the active model turn and SHALL preserve the session identity, bound
folder, Discord thread, and ability to continue by sending another message.

#### Scenario: Stop an active turn
- **WHEN** an authorized user invokes `/stop` while a turn is running
- **THEN** the system interrupts that turn
- **AND** keeps the session open and resumable in the same thread

#### Scenario: Stop with no active turn
- **WHEN** an authorized user invokes `/stop` while no turn is running
- **THEN** the system reports that there is nothing to stop and changes no stored state

### Requirement: Close wraps up and archives without destroying continuity
`/close` SHALL finish an already-running turn, produce a concise wrap-up, mark the session closed,
and archive its Discord thread. Closing MUST NOT delete the stored session, delete working files,
delete the thread, or permanently lock the thread.

#### Scenario: Close an idle session
- **WHEN** an authorized user invokes `/close` while no turn is running
- **THEN** the system records a wrap-up, marks the session closed, and archives the thread
- **AND** retains its conversation identity and bound folder

#### Scenario: Close while a turn is running
- **WHEN** an authorized user invokes `/close` during an active turn
- **THEN** the system records a pending close, allows the active turn to finish, then performs the
  wrap-up and archives the thread
- **AND** does not silently kill the active turn

#### Scenario: Wrap-up cannot be generated
- **WHEN** the session cannot generate a new model-assisted wrap-up
- **THEN** the system records a deterministic status summary and completes the close without losing
  session continuity

#### Scenario: Close is repeated
- **WHEN** close is requested for an already-closed session
- **THEN** the system reports its closed state without duplicating the wrap-up or corrupting state

### Requirement: Closed sessions can be reopened
A closed session SHALL remain listed in Sessions and SHALL be reopenable. Reopening SHALL unarchive
the Discord thread, mark the session open, and continue the preserved conversation using its bound
folder unless normal cross-harness compatibility rules require a fresh harness session.

#### Scenario: Reopen from Sessions
- **WHEN** an authorized user opens a closed session from Sessions
- **THEN** the original thread is unarchived and marked open
- **AND** the user can continue from the preserved conversation

#### Scenario: Backend changed while closed
- **WHEN** a closed session is reopened under a harness that cannot resume its stored session id
- **THEN** the system applies the existing cross-harness context handoff behavior
- **AND** does not pass an incompatible session id to the new harness

### Requirement: Agent-initiated close requires inherited authority
An agent MAY invoke the same close operation only when the current human request explicitly asks to
close the session or an established workflow explicitly grants close-on-done authority. A model's
own preference to finish MUST NOT be treated as authorization.

#### Scenario: User requested close
- **WHEN** the active request explicitly tells the agent to close this session after completion
- **THEN** the agent can invoke the shared close operation after its work finishes

#### Scenario: Workflow is configured to close on done
- **WHEN** a preauthorized workflow completes successfully with close-on-done enabled
- **THEN** it can invoke the shared close operation

#### Scenario: No close authority exists
- **WHEN** neither the current user request nor a preauthorized workflow grants close authority
- **THEN** an agent cannot close or archive the session on its own
