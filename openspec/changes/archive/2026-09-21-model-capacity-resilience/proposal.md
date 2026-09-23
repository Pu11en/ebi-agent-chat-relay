## Why

Discord tasks currently can end with a bare “model at capacity” response even when the relay itself
has free session slots. Temporary provider saturation must preserve the task and recover visibly
instead of forcing Drew to repeat the request.

## What Changes

- Normalize backend outcomes into temporary model saturation, provider rate limit, subscription or
  quota exhaustion, authentication failure, relay queueing, and permanent request errors.
- Preserve the pending turn and show a live waiting/retrying/fallback status for recoverable
  capacity errors.
- Retry temporary saturation with bounded exponential backoff and restart-safe persistence.
- Support an explicit configured fallback chain among already-authorized models/backends; never
  switch to a paid provider or materially different data boundary silently.
- Stop retrying and explain the next valid action for quota, authentication, or permanent errors.
- Prevent duplicate execution when a retry races with a late provider response or bot restart.

## Capabilities

### New Capabilities
- `model-capacity-recovery`: Typed backend-capacity classification, durable retry, safe fallback,
  and user-visible recovery for Discord turns and automated workers.

### Modified Capabilities

None.

## Impact

This affects backend event normalization, run orchestration, persistence, Discord status UI,
headless workers, and tests. It must preserve existing relay capacity handling and provider safety
boundaries.
