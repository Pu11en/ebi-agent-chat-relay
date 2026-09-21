## Why

DrewAI, iMac, and David currently cannot delegate work to one another because bot-authored messages
are ignored and each control API is local. Trusted Discord-mediated handoffs are required for
cross-computer tasks and for eventually owning shared slash commands in one app without removing
remote computers' ability to work.

## What Changes

- Add bidirectional handoff jobs between every trusted computer agent.
- Use one shared `agent-handoffs` channel with one visible task thread per job.
- Define a compact task envelope containing an id, explicit sender/recipient, validated project
  owner and folder, goal, permissions, relevant findings, expected result, and reply location.
- Authenticate the Discord guild, bot identities, and authorized human origin; deduplicate task
  ids and prevent result messages from starting reply loops.
- Show accepted, queued, running, blocked, completed, and failed states; queue offline destinations
  and resume automatically when they reconnect.
- Keep detailed activity in the handoff thread while sending acknowledgement, blockers, and the
  final result back to the origin.
- Preserve the original task's authority: read-only work is automatic, authorized edits remain
  authorized, and destructive/external/paid/unclear actions do not gain permission through a
  handoff.

## Capabilities

### New Capabilities
- `trusted-agent-handoffs`: Authenticated, idempotent, visible cross-computer task delivery and
  result return over Discord.

### Modified Capabilities

None.

## Impact

This affects the relay/control plane, Discord message handling, persistence, queueing, instance
configuration, security validation, and integration tests across Linux/macOS/Windows consumers.
No direct SSH/Tailscale desktop control is introduced.
