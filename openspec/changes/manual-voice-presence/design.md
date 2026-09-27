## Context

See proposal.md. The existing presence controller starts/reconnects whenever the owner is present; its five-second timer reverses a manual removal. Voice runs separately from the Python chat bot. Source foundation 50ff65f matches the live voice source and includes the tested last-sentence fix, not the shelved naming patch.

## Goals / Non-Goals

One temporary, small manual-control change. No Jester Voice inspection/integration, new voice features, model calls, transcript deletion, bot replacement, or Discord slash-command registry changes.

## Decisions

- Use exact `!voice join` / `!voice leave` text commands, scoped to configured guild/channel/owner, rejecting bots and webhooks. Add GuildMessages and MessageContent gateway intents to the companion; the same application's existing main bot already uses MessageContent.
- Keep a non-persisted join request and serialized lifecycle. Only the join method can connect; reconciliation can stop but never connect. Invalidate pending joins and disconnect immediately on leave, then serialize session cleanup.
- Require owner presence for join, not leave. Pause revokes the join request; old Resume controls only explain how to join. No new settings or background recovery system.
- Transcript channel 1546684774272340040 is not a configured no-mention/control-center channel; monitor-all is false. Plain commands without a mention do not wake the Python chat agent under the current configuration. Do not change chat-bot routing.
- Existing runtime publication continues to export captured audio after stop. Pending transcript delivery is unchanged; this is not a cancel-agent-work command.

## Risks / Trade-offs

- A failed/disconnected join needs another command → deliberate, prevents unwanted rejoining.
- Gateway permission not enabled on another installation → document Message Content requirement; no new privileged portal change expected here.
- Existing recording notices say automatic → new notices and README must describe manual control; do not delete historic messages.
- Runtime is a separate dirty deployment worktree → apply only the reviewed delta on explicit activation approval, preserving the previous seven-file voice fix and other sessions.
