## Purpose

Enable trusted computer agents to delegate bounded work to one another through visible, durable,
and authority-preserving Discord handoff jobs without creating bot-to-bot loops.

## ADDED Requirements

### Requirement: Trusted computer agents can hand work in either direction
The system SHALL support bidirectional task handoffs among configured trusted agents, including
DrewAI, iMac, and David, without assuming one permanent controller. Only a configured sender bot in
the configured guild and handoff channel MAY create a task for a configured recipient.

#### Scenario: DrewAI delegates to David
- **WHEN** DrewAI posts a valid task addressed to David in the configured handoff channel
- **THEN** David accepts or queues the task and reports its acknowledgement in the task thread

#### Scenario: David delegates to DrewAI
- **WHEN** David posts a valid task addressed to DrewAI in the configured handoff channel
- **THEN** DrewAI accepts or queues the task under the same rules

#### Scenario: iMac delegates to another trusted computer
- **WHEN** iMac posts a valid task addressed to any other configured trusted recipient
- **THEN** the addressed recipient processes it without requiring iMac to be a central controller

#### Scenario: Sender is not trusted
- **WHEN** a bot or webhook not mapped to a configured agent identity posts a task-shaped message
- **THEN** no handoff task is created or executed

### Requirement: Every handoff has one visible Discord job thread
Each accepted handoff SHALL use the shared `agent-handoffs` channel and exactly one Discord thread
identified by its task id. The task thread SHALL contain the human-readable request, acknowledgement,
state changes, blockers, and terminal result; handoff execution MUST NOT become a hidden backchannel.

#### Scenario: New handoff is created
- **WHEN** a trusted sender submits a new valid task id
- **THEN** one task thread is created in `agent-handoffs`
- **AND** all subsequent events for that task are posted to that thread

#### Scenario: Duplicate delivery is received
- **WHEN** the same task id and recipient are observed again
- **THEN** the existing task thread and stored job are reused
- **AND** no second execution or task thread is created

### Requirement: Handoffs use a compact versioned task packet
Every task SHALL carry a bounded, versioned packet with a unique task id, explicit sender and
recipient agent ids, original human origin, explicit project owner and folder locator, goal,
inherited permission scope, compact relevant findings, expected result, and reply location. The
packet MUST NOT contain an unbounded conversation transcript.

#### Scenario: Complete packet is received
- **WHEN** a recipient receives a supported packet containing all required fields within the size
  limit
- **THEN** the packet is validated and stored before acknowledgement

#### Scenario: Packet is incomplete, oversized, or unsupported
- **WHEN** a required field is absent, the packet exceeds its bound, or its version is unsupported
- **THEN** the recipient does not execute it
- **AND** posts a clear rejected or blocked result to the job thread and origin

#### Scenario: Context is needed beyond the packet
- **WHEN** the recipient needs more information to perform the task safely
- **THEN** it requests that specific information through the task thread
- **AND** does not copy the full originating conversation by default

### Requirement: Project ownership and location are explicit
The sender SHALL resolve conversational wording into an explicit project owner and folder locator
before sending a handoff. The recipient SHALL resolve that locator only within the named owner's
approved project roots on the recipient computer. It MUST reject ambiguous pronouns, nonexistent
folders, remote absolute paths, and path escapes.

#### Scenario: David is asked to use Drew's projects
- **WHEN** a task on David explicitly says “Drew's projects” and names a folder
- **THEN** the packet identifies Drew as project owner and carries the folder locator
- **AND** the recipient resolves it under Drew's approved project roots

#### Scenario: Ownership is ambiguous
- **WHEN** a request uses “my projects” or another pronoun whose owner cannot be proven from the
  original human origin
- **THEN** the handoff is blocked for clarification rather than guessing an owner

#### Scenario: Sender supplies its local absolute path
- **WHEN** a task packet contains an absolute path from another computer instead of an approved
  owner/folder locator
- **THEN** the recipient rejects the location and does not read or edit that path

### Requirement: Authority is preserved and never broadened
The recipient SHALL inherit no more authority than the original human task granted. Read-only work
MAY proceed automatically. Edits MAY proceed only when the original task authorized edits.
Destructive operations, deployments, paid-provider use, external communications, permission changes,
and materially unclear actions MUST stop for new authority unless they were explicitly authorized
by the original human request and permitted by recipient policy.

#### Scenario: Read-only folder question
- **WHEN** the original request asks an agent to inspect a folder and report information
- **THEN** a trusted recipient can read within the resolved folder and return findings without a
  second approval

#### Scenario: Authorized edit is delegated
- **WHEN** the original request explicitly authorizes edits within the resolved project scope
- **THEN** the recipient can make in-scope edits under its normal safety and worktree rules

#### Scenario: Handoff asks for broader authority
- **WHEN** a packet requests an action beyond the original permission scope
- **THEN** the recipient blocks that action and returns the needed authority to the origin

#### Scenario: Recipient has a stricter local policy
- **WHEN** inherited authority permits an action that the recipient computer's policy forbids
- **THEN** the stricter recipient policy wins and the task reports a blocker

### Requirement: Job state is durable and visible
Each recipient SHALL persist the handoff before execution and expose the states **accepted**,
**queued**, **running**, **blocked**, **completed**, and **failed**. If a destination is offline, the
Discord job SHALL remain pending; after reconnect the destination SHALL discover and resume eligible
work automatically without requiring a new task message.

#### Scenario: Destination is online
- **WHEN** a valid task arrives and local execution capacity is available
- **THEN** the recipient posts accepted and running states and starts the task

#### Scenario: Destination is offline
- **WHEN** the source creates a task while the addressed recipient is offline
- **THEN** the job remains visible without being marked completed
- **AND** the recipient discovers, stores, acknowledges, and queues it after reconnecting

#### Scenario: Recipient restarts during execution
- **WHEN** a recipient restarts with a nonterminal stored handoff
- **THEN** startup reconciliation resumes or safely requeues it according to its recorded execution
  state
- **AND** never creates a second logical task

#### Scenario: Work is blocked
- **WHEN** required context, authority, folder availability, or local capability is missing
- **THEN** the recipient records blocked state and sends the blocker to the job thread and origin

### Requirement: Results return to the origin and remain attributable
The detailed execution record SHALL stay in the handoff task thread. The originating conversation
SHALL receive the initial acknowledgement, blockers that need its human, and the final success or
failure result with the task id, recipient, summary, and relevant artifact references. The
originating agent SHALL normally integrate the returned result into its own task.

#### Scenario: Task completes
- **WHEN** the recipient completes the expected result
- **THEN** it posts the detailed result in the task thread
- **AND** routes a compact final result to the recorded origin conversation

#### Scenario: Task fails
- **WHEN** recipient execution reaches a terminal failure
- **THEN** it posts a bounded failure explanation and safe retry guidance in both locations

#### Scenario: Origin is temporarily unavailable
- **WHEN** a terminal result cannot immediately be delivered to the origin conversation
- **THEN** it remains durably pending and is retried without rerunning the recipient task

### Requirement: Delivery is idempotent and loop safe
The system SHALL distinguish task, acknowledgement, status, clarification, and terminal-result
events. Only a new valid task event MAY start execution. Acknowledgements, results, retries, or bot
responses MUST NOT create another task. Event ids SHALL be deduplicated and automated exchanges
SHALL have bounded message, retry, and hop limits.

#### Scenario: Result is observed by all bots
- **WHEN** a terminal result message appears in the shared handoff channel
- **THEN** only the recorded origin handles it as a result
- **AND** no agent starts a new task from it

#### Scenario: Discord redelivers an event
- **WHEN** a recipient observes an event id already stored for that task
- **THEN** it acknowledges the existing state without applying the event twice

#### Scenario: Retry limit is reached
- **WHEN** delivery or execution retries reach the configured bound
- **THEN** the job becomes blocked or failed with a visible explanation instead of retrying forever

### Requirement: Handoff jobs are independent of ordinary chat sessions
A handoff SHALL be tracked as a job even when its execution does not need a long-lived chat session.
The recipient MAY use a fresh agent turn, an existing authorized session, or a non-model operation
as appropriate, but all choices SHALL preserve the same task id, authority, visibility, and result
contract.

#### Scenario: Read-only lookup needs one agent turn
- **WHEN** a recipient can answer a folder question with one bounded agent task
- **THEN** it completes within the handoff job thread without requiring a separate user session

#### Scenario: Existing session is explicitly requested
- **WHEN** the original request identifies an existing recipient session and grants permission to
  use it
- **THEN** the handoff may target that session while preserving the job audit trail
