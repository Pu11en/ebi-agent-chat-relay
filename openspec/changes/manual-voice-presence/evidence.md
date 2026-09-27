# Local verification — 2026-09-27

## Scope and result

Owner-only `!voice join`, `!voice leave`, and `!voice` help in the exact configured
transcript channel. No automatic join on presence, restart, health check or old
Resume interaction. Participant Pause remains available. Leave also interrupts a
queued/in-progress join, while keeping already captured audio available to process.

Built from 50ff65f, the last-sentence repair Drew already tested. No unactivated
Jester/Goku naming changes were included. Separate Jester Voice was not inspected.
No bot restart, runtime switch, live Discord command test, external push or paid
model invocation occurred. This is local implementation, not live verification.

## Test-first evidence

- New command/presence tests failed before implementation: missing command module
  and join/leave methods, owner presence still started/reconnected automatically,
  and a recovered recording reconnected instead of stopping.
- A separate transport regression failed because `entersState` received only a
  20-second timeout. Inspection of the installed voice library showed destroying
  the connection does not itself cancel that wait; the implementation now supplies
  an abortable signal, preserving the timeout and allowing immediate cancellation.
- Focused five-file checks passed: 33 tests. They cover owner/guild/channel/bot/
  webhook guards, ordinary text, absence, startup, duplicate/concurrent joins,
  leave during disclosure/connection, cancellation, pause/resume, failed join,
  delayed transcript publication and flushing captured speech.
- Full voice suite passed before and after formatting: 165 tests, zero failures.
  Final run: `node --test --test-reporter=spec test/*.test.mjs`, from the extension
  directory. No network, Discord login or local speech-model inference was needed.
- `make verify` passed: ruff format/check, pyright (zero errors/warnings), and
  6,067 Python tests on 3.13.12 (143.59 seconds). Pytest reported 19 warnings and
  asynchronous CollisionWatch MagicMock teardown errors in unchanged Python code;
  the command exited zero. Those diagnostics were not repaired in this voice task.
- `node --check` passed for all changed source/provisioning modules.
- `git diff --check` and strict OpenSpec validation passed.

## Focused security review

- Exact anchored command allowlist; only the configured owner, guild and transcript
  channel pass. Bots and webhooks are ignored; no arbitrary room/path/action.
- No shell/subprocess/model invocation was introduced; no slash command registry
  overwrite, framework cog, schema, dependency, service or credential change.
- Message Content/Guild Messages intents support typed commands; main ccdb already
  requests message content. Its launcher configuration does not treat this transcript
  channel as a no-mention chat launcher; unmentioned commands do not start an agent.
- Disclosure still must succeed before recording; owner presence is checked before
  and after asynchronous joining. Leave invalidates pending joins immediately.
- Replies suppress mentions and expose no raw exception details. Existing worker
  environment credential-stripping regression passed.
- Updated the provisioning helper's disclosure text only; did not run provisioning,
  create channels, modify historical messages, or change Discord permissions.

## Next boundary

Activation requires Drew's approval. Recheck current sessions/lounge and voice
activity; preserve the existing last-sentence patch in the permanent runtime;
switch only the voice companion, not the main bot. Then use the three short
Discord checks in tasks.md. The next larger topic is the Discord-bot audit, and
its normal-session versus /gowork decision must be asked before writing its plan.
