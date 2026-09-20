## Context

The relay already has a process-wide semaphore that exposes local queueing, and `/gowork` separately recognizes a narrow set of usage-limit messages. Other backend errors reach `EventProcessor` as terminal text, so provider saturation can end a turn even though local capacity is available. A retry can also cross a bot restart or race a late backend result. The design must work through the existing backend abstraction and preserve the rule that provider and data-boundary changes require explicit authority.

## Goals / Non-Goals

**Goals:**

- Give every runner a shared, typed interpretation of capacity-related outcomes.
- Reuse one durable recovery state machine for interactive turns and automated workers.
- Preserve existing local semaphore behavior as a distinct admission state.
- Make recovery state visible without duplicating the user's prompt or answer.

**Non-Goals:**

- Changing subscription limits or bypassing a provider's controls.
- Treating model quality, reasoning effort, or a generic model error as capacity.
- Creating an implicit paid-provider fallback.
- Retrying tool side effects after a model has already begun producing an accepted result.

## Decisions

### Normalize raw errors at the backend boundary

Add a surface-agnostic capacity outcome type in the core/backend layer. Each backend adapter maps structured exit information first, then a small tested set of provider phrases as a compatibility fallback. The classifier returns a category, retryability, safe user detail, optional retry-after time, and raw diagnostic for logs.

This is preferred to putting string checks in each Cog because interactive chat and `/gowork` would otherwise disagree. Unrecognized errors remain permanent instead of being optimistically retried.

### Put retry policy around a complete logical turn

The orchestration layer owns a `turn_key` and wraps a fresh backend attempt. It retries only when the classifier says the attempt ended before an accepted completion. Status changes are emitted through the existing surface and result-sink boundaries, so Discord and future frontends do not implement policy themselves.

This is preferred to retrying inside stream parsing because the parser cannot safely decide whether partial model output or tool effects may already have escaped.

### Persist a small recovery record in SQLite

Add a repository-managed record containing the stable turn key, frontend/thread identity, prompt or secure prompt reference, backend/model, authorized fallback chain, attempt count, state, next-attempt time, and accepted-result marker. Claiming an attempt and accepting a result use conditional updates so only one worker wins.

The repository follows the project's zero-config schema pattern and makes restart recovery possible without introducing another service. Prompt storage follows the same local trust boundary as existing session records; logs and user notices never echo secrets.

### Use bounded exponential backoff with provider hints

Provider `retry-after` information wins when present, clamped to configured minimum and maximum delays. Otherwise recovery uses exponential delays plus small jitter. Both attempt count and total elapsed recovery time are bounded. Defaults are supplied by ccdb and may be overridden without consumer code changes.

This avoids synchronized retry storms while guaranteeing the system eventually asks for a decision instead of waiting forever.

### Treat fallback authority as data captured at submission

The pending record receives the fallback chain that was explicitly configured for the task, session, or computer when the turn began. Recovery never consults a newly discovered provider and never infers consent from model availability. Every switch is posted before the next attempt.

This preserves predictable cost and privacy boundaries. The existing single `/gowork` fallback is migrated into a one-entry chain.

### Separate relay admission from provider recovery

The local semaphore continues to control how many processes may run and remains labeled `queued — waiting for capacity`. Provider saturation begins only after an admitted attempt returns that classified outcome and is labeled with the affected model plus retry timing. Metrics and API status expose these as separate fields.

## Risks / Trade-offs

- [Provider text changes and defeats phrase matching] → Prefer structured codes, keep phrase fixtures per backend, and classify unknowns as terminal.
- [A retry repeats external tool effects] → Retry only attempts with no accepted completion; stable turn keys and conditional acceptance suppress duplicates, while ambiguous partial execution pauses for the user.
- [Persisted prompts contain sensitive data] → Keep records in the existing local database boundary, exclude prompt content from logs/API responses, and delete recovery payloads after terminal completion.
- [Long recovery looks stuck] → Update one live status with category, next attempt, attempt count, and fallback changes.
- [Retry storms after restart] → Honor persisted deadlines, add jitter, and reacquire the existing relay semaphore for every attempt.

## Migration Plan

1. Add the outcome classifier and fixtures without changing behavior.
2. Add the recovery repository and restart loader behind a disabled internal integration path.
3. Route interactive and automated results through the shared recovery coordinator while preserving the old usage-limit user choice as the terminal fallback.
4. Enable default bounded retry for temporary saturation and expose recovery status.
5. Remove the duplicate `/gowork` string-only branch after parity tests pass.

Rollback disables new recovery scheduling while leaving records readable. Pending records remain visible and can be resumed manually; no session or task data is deleted.
