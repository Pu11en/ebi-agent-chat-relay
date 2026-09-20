## Context

See `proposal.md` for motivation. Ordinary `ClaudeChatCog.on_message` intentionally ignores every
bot-authored message, preventing reply loops. The framework already provides same-process
thread-to-thread relay through authenticated local REST calls, with visible thread posts, queue vs
interrupt behavior, hop/cooldown limits, and tests. That relay addresses coordination between live
sessions attached to one bot; it is not a durable cross-computer job protocol and its in-memory
guard deliberately resets on restart.

All three computers share a private Discord guild but run independent bot identities, local REST
control planes, filesystems, subscriptions, and session databases. Discord is the common transport
and durable human-visible audit surface. The shared project catalog is a separate build; this change
defines the owner/folder resolver boundary and must not duplicate catalog storage.

## Goals / Non-Goals

**Goals:**

- Make every trusted computer capable of both sending and receiving work.
- Survive an offline recipient, bot restart, Discord redelivery, and late result without duplicating
  the logical task.
- Preserve original human authority across machines and apply stricter recipient policy when needed.
- Keep task status and agent-to-agent communication visible to Drew in one Discord thread.
- Reuse backend/session execution abstractions without requiring every handoff to become a normal
  user chat session.

**Non-Goals:**

- General acceptance of arbitrary Discord bots, webhooks, guilds, or channels.
- Remote desktop, SSH, Tailscale control, or direct access to another computer's filesystem.
- Synchronizing project contents or assuming absolute paths match across computers.
- Silently purchasing API usage, deploying, deleting, messaging outsiders, or elevating
  permissions.
- Replacing the small same-process thread relay used for live session coordination.

## Decisions

### Discord carries a versioned job protocol, not ordinary bot chat

Add a dedicated handoff listener that runs only in the configured `agent-handoffs` channel and only
for allowlisted bot ids. Ordinary chat keeps its unconditional bot-author guard. The origin posts a
bounded `task/v1` envelope and creates one public thread named with a short task id; all later
structured events and human-readable summaries stay in that thread.

Removing the global bot-author guard was rejected because any bot response could recursively start
another model. Direct cross-computer REST was rejected because local control planes are not a shared
authenticated network service and Discord already supplies identity, storage, and visibility.

### Discord identity plus explicit configuration authenticates senders

Per-instance configuration maps stable agent ids to Discord application user ids, the one guild id,
and the one handoff channel id. A received packet is valid only when the Discord author id matches
the packet sender, the local agent id matches the packet recipient, and guild/channel/thread linkage
matches configuration. Webhooks and humans cannot create protocol events. The source bot acts as the
attestor for its recorded human origin and authority, which is linked to an origin guild/channel/
message coordinate for audit.

A shared secret embedded in messages was rejected because Discord author identity already prevents
another application from posting as an allowlisted bot and distributing/rotating another secret on
three computers adds failure modes. A future non-Discord transport would require its own
authentication adapter.

### The envelope is compact, typed, and semantically explicit

Define a surface-neutral `HandoffTask` with protocol version, UUID task id, sender, recipient,
origin coordinate and human id, `ProjectLocator(owner, folder)`, goal, `AuthorityScope`, compact
findings, expected result, reply coordinate, and creation/expiry timestamps. Enforce field and total
size limits before storage. Protocol messages distinguish task, ack, state, question, answer, and
result events; every event has an id and sequence.

Forwarding conversation transcripts was rejected because it wastes tokens, can leak irrelevant
context, and obscures what authority actually transferred. Free-form “my projects” was rejected
because speaker perspective changes across computers.

### Project locators resolve through an injected approved-root boundary

The handoff package owns only the `ProjectLocator` value and resolver protocol. A local adapter maps
owner plus folder to a path and verifies the resolved path remains under an approved root. The
shared-project-catalog build can later implement the same protocol; until then an instance can use
configured owner roots. Absolute remote paths are never accepted as locators.

Putting global project inventory into this change was rejected because it overlaps the separate
catalog build and would create two sources of truth.

### Authority is a typed intersection

The source derives an `AuthorityScope` from the original task: read scope, allowed edit scope, and
explicit exceptional capabilities. The recipient computes `effective = inherited ∩ local_policy`
before execution and again before a sensitive action. Destructive, deployment, paid-provider,
external-message, permission, and unclear actions require an explicit capability; otherwise the job
transitions to blocked and asks the origin human. The packet never interprets “full permissions” as
authority beyond the original task.

Trusting the recipient model to infer permissions from prose was rejected because handoff summaries
can omit or distort approval. Treating trusted bots as administrators was rejected because identity
trust is not action authority.

### Each instance keeps an idempotent local job ledger

Add durable handoff task and event records keyed by `(task_id, recipient_agent_id)` with unique event
ids and explicit state transitions. A transaction stores a valid task before ack. Only the transition
from accepted/queued to running may schedule execution. Duplicate packets return current state. At
startup the recipient scans recent handoff starters addressed to it, reconciles them with the local
ledger, and requeues recoverable nonterminal jobs. Running jobs without a verifiable active
execution become queued with a restart note.

An in-memory queue was rejected because offline/restart behavior is a primary requirement. Relying
only on Discord reaction state was rejected because it cannot atomically prevent duplicate local
execution.

### A state machine drives visible status and execution

The legal states are accepted, queued, running, blocked, completed, and failed, with terminal states
immutable except for explicit safe retry creating a new attempt under the same logical task. A
handoff coordinator chooses an execution adapter: a fresh backend turn by default, an explicitly
named existing session when authorized, or a deterministic non-model operation. Capacity waits stay
queued rather than failing. Every transition writes the ledger before a best-effort Discord status
post.

Always creating a normal ccdb session was rejected because a simple lookup does not need durable
chat identity and would clutter session lists. Hidden headless execution was rejected because the
job thread must show what is happening.

### Results use an outbox and cannot start work

Terminal execution writes a result plus an origin-delivery outbox row in the same transaction. The
recipient posts detail in the job thread and emits a bounded result event. The origin bot accepts it
only for a task it created, posts acknowledgement/blocker/final summary to the recorded origin
thread, and normally lets that origin agent integrate the result. Failed origin delivery retries
from the outbox without rerunning the task.

Treating result text as a new chat message was rejected because it can create a second task or be
mistaken for human authority.

### Loop and load bounds are protocol rules

Only `task/v1` can create work; ack/state/question/answer/result events cannot. Unique event ids,
monotonic sequences, one recipient, bounded clarification rounds, bounded exponential delivery
retry, packet size limits, and an expiry time prevent infinite chatter. The existing live-session
relay retains its own hop/cooldown rules and is not used as the durable job queue.

## Risks / Trade-offs

- [A trusted source bot could overstate the original authority] → Link every packet to an origin
  message, keep exceptional capabilities explicit, and let stricter recipient policy block.
- [Discord events can arrive late or twice] → Use unique database constraints and idempotent state
  transitions before scheduling execution.
- [The recipient was offline long enough for a thread to archive] → Scan recent starter messages
  and archived task threads at startup within a configured retention window, then unarchive when
  acknowledging.
- [Two instances both think they own a recipient id] → Require a one-to-one configured Discord bot
  id mapping and reject mismatches visibly.
- [Result text or artifacts exceed Discord limits] → Store bounded summaries in protocol events and
  use existing attachment/artifact delivery for larger outputs.
- [Project catalog ships later] → Keep the resolver protocol stable and use configured approved roots
  as the temporary adapter; never weaken path validation.
- [Fresh task execution consumes subscription capacity] → Use the recipient's configured harness and
  capacity policy, expose queued state, and never silently select a paid fallback.

## Migration Plan

1. Add the handoff value objects, state machine, schema, repositories, and configuration with the
   receiver disabled unless all trust-boundary ids are present.
2. Add protocol rendering/parsing and the dedicated listener while retaining the ordinary bot-message
   guard and existing same-process relay unchanged.
3. Enable one pair in read-only test mode, verify duplicate/offline/restart/result routing, then add
   the third identity and bidirectional mappings.
4. Enable authorized edit scopes only after read-only behavior passes the local Discord try-out.
5. Roll back by disabling the handoff listener and executor. Stored ledgers and Discord task threads
   remain audit history; no project or session data is deleted.
