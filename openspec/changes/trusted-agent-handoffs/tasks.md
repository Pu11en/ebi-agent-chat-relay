Check: `uv run pytest tests/test_handoff_protocol.py tests/test_handoff_repository.py tests/test_handoff_cog.py tests/test_handoff_integration.py -q`

Try: `uv run python -m claude_discord.main`

## 1. Protocol and durable state

- [x] 1.1 Add failing packet/event validation tests, then implement bounded versioned handoff values, event kinds, UUID/sequence checks, `ProjectLocator`, and `AuthorityScope` in `claude_code_core/handoffs/protocol.py`; owner: that module and `tests/test_handoff_protocol.py`; depends on nothing; verify malformed, oversized, ambiguous-owner, absolute-path, and round-trip cases pass.
- [x] 1.2 Add failing transition tests, then implement the accepted/queued/running/blocked/completed/failed state machine and legal retry rules in `claude_code_core/handoffs/state.py`; owner: that module and protocol tests; depends on 1.1; verify terminal events cannot create or restart work.
- [x] 1.3 Add handoff task, event, attempt, and result-outbox migrations plus repository methods with unique task-recipient and event-id constraints; owner: `claude_discord/database/models.py`, a new `claude_discord/database/handoff_repo.py`, and `tests/test_handoff_repository.py`; depends on 1.2; verify duplicate concurrent inserts schedule one logical task.

## 2. Trust and Discord transport

- [x] 2.1 Add strict per-instance handoff configuration for local agent id, guild, channel, and one-to-one trusted bot mappings, disabled unless complete; owner: a new `claude_discord/handoff_config.py` and tests; depends on 1.1; verify wrong guild/channel/author/recipient and webhook events fail closed.
- [x] 2.2 Implement human-readable task starter and typed event rendering/parsing within Discord bounds; owner: a new `claude_discord/handoff_discord.py` and `tests/test_handoff_cog.py`; depends on 1.1 and 2.1; verify one starter/thread per task and no unbounded transcript is emitted.
- [x] 2.3 Add a dedicated handoff Cog listener that accepts only configured protocol events in `agent-handoffs` while leaving the ordinary bot-message guard unchanged; owner: `claude_discord/cogs/agent_handoff.py` and focused tests; depends on 1.3 and 2.2; verify ordinary bot messages still start zero chat turns.
- [x] 2.4 Implement reconnect scanning of recent active/archived handoff starters and idempotent reconciliation into the local ledger; owner: the handoff Cog/repository tests only; depends on 2.3; verify an offline-addressed task is discovered once after startup.

## 3. Authority, project resolution, and execution

- [x] 3.1 Implement the approved-root `ProjectLocator` resolver adapter and its injectable protocol without owning shared-project-catalog data; owner: a new `claude_discord/handoff_projects.py` and tests; depends on 1.1; verify Drew-owned folder lookup, nonexistent folder, symlink/path escape, and remote absolute path cases.
- [x] 3.2 Implement inherited-authority intersection with recipient policy and blockers for destructive, deployment, paid, external-message, permission, and unclear actions; owner: a new `claude_discord/handoff_authority.py` and tests; depends on 1.1; verify read-only and authorized edits pass while broadened authority blocks.
- [x] 3.3 Add an execution coordinator that writes state before scheduling and supports a fresh backend turn by default, an explicitly authorized existing session, or deterministic work; owner: a new `claude_discord/handoff_executor.py` and focused tests; depends on 1.2, 1.3, 3.1, and 3.2; verify capacity waits remain queued and restart reconciliation never duplicates execution.
- [x] 3.4 Connect executor progress to bounded visible ack/queued/running/blocked/completed/failed posts in the one job thread; owner: handoff executor/Cog adapters and tests; depends on 2.3 and 3.3; verify every durable transition can be reconstructed from Discord plus the ledger.

## 4. Result return and integration

- [x] 4.1 Implement transactional terminal-result plus outbox writes and retry delivery to the recorded origin conversation without rerunning work; owner: handoff repository/executor and tests; depends on 1.3 and 3.3; verify a temporarily unavailable origin receives one later result.
- [x] 4.2 Add origin-side ack, blocker, and final-result handling that accepts events only for locally created tasks and never turns a result into a task; owner: the handoff Cog and tests; depends on 4.1; verify duplicate/late/cross-origin results are idempotent and attributable.
- [x] 4.3 Wire repositories, configuration, Cog, and backend adapters through `claude_discord/setup.py` with backward-compatible disabled defaults and public exports where needed; owner: setup/export files and wiring tests; depends on sections 1–4.2; verify ordinary consumers with no handoff config start unchanged.
- [ ] 4.4 Add a three-identity integration harness covering DrewAI→David, David→DrewAI, iMac→DrewAI, offline recovery, duplicate delivery, blocked authority, restart, and final return; owner: `tests/test_handoff_integration.py`; depends on 4.3; verify Check passes without live model calls.
- [ ] 4.5 Run Check, `uv run ruff check claude_discord/ claude_code_core/ tests/`, `uv run pyright claude_discord/ claude_code_core/`, the full test suite, and the security audit; owner: in-scope fixes only; depends on 4.4.

## How to try it

- Ask David to inspect a named folder in **Drew's projects** and confirm one `agent-handoffs` job thread shows acknowledgement, progress, and the returned answer in the original thread.
- Take iMac offline, send it a read-only handoff, bring it back, and confirm the existing job resumes once without sending the task again.
- Delegate an edit without edit authority, confirm it blocks visibly, then send a separately authorized edit and confirm the recipient uses its own safe worktree and returns the result.
