Check: `uv run pytest tests/test_capacity_recovery.py tests/test_run_helper.py tests/test_task_loop_cog.py -q`

Try: `uv run pytest tests/test_capacity_recovery.py -q`

## 1. Outcome contract

- [ ] 1.1 Add failing unit tests for structured provider saturation, rate limit, quota, authentication, relay queueing, and unknown permanent errors; verify `uv run pytest tests/test_capacity_recovery.py -q` fails for the missing classifier.
- [ ] 1.2 Implement the surface-agnostic capacity outcome and per-backend normalization in new owned capacity modules; verify the classifier tests pass without changing runner behavior.
- [ ] 1.3 Add backend phrase fixtures for the observed `model at capacity` variants and false-positive sentences; verify all supported harnesses share the same categories.

## 2. Durable pending turns

- [ ] 2.1 Add failing repository tests for create, conditional claim, schedule, accept-once, expiry, and restart reload; verify the focused repository test fails before schema work.
- [ ] 2.2 Add the zero-config SQLite schema and a dedicated recovery repository without modifying unrelated session rows; verify fresh and upgraded database tests pass.
- [ ] 2.3 Add a restart loader that claims only due records and reacquires normal relay admission; verify two simulated workers cannot run one turn twice.

## 3. Shared recovery coordinator

- [ ] 3.1 Add failing policy tests for bounded exponential backoff, retry-after clamping, jitter bounds, total recovery deadline, and ambiguous partial output; verify they fail before orchestration is added.
- [ ] 3.2 Implement one coordinator around complete backend attempts with a stable turn key and accepted-result guard; verify retry and late-result race tests pass.
- [ ] 3.3 Capture explicit fallback chains at submission and migrate the existing `/gowork` single fallback into a one-entry chain; verify no unconfigured provider is ever selected.
- [ ] 3.4 Integrate the coordinator with interactive run orchestration and automated task-loop result handling; verify identical capacity outcomes keep both kinds of task pending.

## 4. Visible status and operations

- [ ] 4.1 Add failing surface tests for distinct relay-queued, provider-waiting, retrying, fallback, exhausted, authentication, and permanent-error notices; verify message content names the right category.
- [ ] 4.2 Update the existing live status rather than posting repeated alerts, including attempt count and next retry; verify a multi-retry turn produces one current recovery status and one final answer.
- [ ] 4.3 Expose non-sensitive recovery state in session/task status APIs and logs while excluding prompts and credentials; verify API serialization and log-capture tests.

## 5. Integration and cleanup

- [ ] 5.1 Add restart, late-response race, exhausted-budget, authorized-fallback, and no-authority integration tests; verify all complete without duplicate model execution or duplicate Discord answers.
- [ ] 5.2 Remove the duplicate string-only `/gowork` capacity path after the shared coordinator covers its behavior; verify existing usage-limit and relay-admission tests remain green.
- [ ] 5.3 Run `uv run ruff check claude_discord tests`, `uv run ruff format --check claude_discord tests`, `uv run pyright claude_discord`, and the full test suite; record any unrelated pre-existing failure separately.

## How to try it

- Start a test turn with a backend stub that returns `model at capacity` once; confirm the original prompt waits, retries, and finishes without being retyped.
- Restart the bot while that retry is waiting; confirm the same turn resumes after restart and produces only one final answer.
- Configure one explicit fallback, force repeated saturation, and confirm the bot announces only that fallback; repeat with no fallback and confirm it asks instead of switching silently.
