Check: `uv run pytest tests/test_command_surface.py tests/test_session_lifecycle.py tests/test_project_launcher.py tests/test_backend_command.py tests/test_claude_chat.py tests/test_session_manage.py -q`

Try: `uv run python -m claude_discord.main`

## 1. Shared surface and lifecycle foundations

- [x] 1.1 Add failing location-classification and final-command-manifest tests in `tests/test_command_surface.py`, then add the pure coordinator types in `claude_discord/command_surface.py`; owner: these two files only; depends on nothing; verify with `uv run pytest tests/test_command_surface.py -q`.
- [x] 1.2 Add failing session lifecycle migration/repository tests in `tests/test_session_lifecycle.py`, then add backward-compatible lifecycle, pending-close, and wrap-up fields in `claude_code_core/session_repo.py` and `claude_discord/database/models.py`; owner: those three files; depends on 1.1 only for shared state names; verify with `uv run pytest tests/test_session_lifecycle.py tests/test_repository.py -q`.
- [x] 1.3 Add a tested close/reopen service in `claude_discord/session_lifecycle.py` that preserves records, waits for active completion, archives without locking, and enforces typed human/workflow authority; owner: the service and `tests/test_session_lifecycle.py`; depends on 1.2; verify with `uv run pytest tests/test_session_lifecycle.py -q`.

## 2. Control-center experience

- [ ] 2.1 Replace the launcher persistent/bottom views with the status plus New session, Sessions, and Settings control row, including debounced replace-and-delete repair behavior; owner: `claude_discord/cogs/project_launcher.py` and `tests/test_project_launcher.py`; depends on 1.1; verify with `uv run pytest tests/test_project_launcher.py -q`.
- [ ] 2.2 Extend the New session flow with separate Favorites, Recent, and Browse choices while keeping thread creation idle on the default model; owner: launcher view code and its tests only; depends on 2.1; verify tests assert no runner/model turn starts before the first task.
- [ ] 2.3 Add safe Create and Clone destination flows under approved roots, using argument-vector subprocess execution and attempt-owned failure cleanup; owner: a new `claude_discord/project_creation.py` plus `tests/test_project_creation.py`, with only thin launcher calls; depends on 2.2; verify traversal, collision, failed clone, and success tests pass.
- [ ] 2.4 Build the newest-first searchable Sessions view with Open, New in same folder, and Close backed by accessible durable records; owner: a new `claude_discord/discord_ui/session_browser.py`, its tests, and minimal repository query additions; depends on 1.3; verify closed/archived, inaccessible, search, and ordering tests pass.
- [ ] 2.5 Add the Settings entry view and supported-feature filtering contract without implementing the separate My AI Setup inventory; owner: a new `claude_discord/discord_ui/settings_home.py` and tests; depends on 1.1; verify unsupported configured features are absent and opening it starts no model turn.

## 3. Session commands

- [ ] 3.1 Refactor stop, fork, rewind, compact, clear, context, and goal into callable services while retaining existing command behavior; owner: `claude_discord/cogs/claude_chat.py`, `claude_discord/cogs/session_manage.py`, and their existing tests; depends on 1.1; verify existing focused suites remain green before adding `/session`.
- [ ] 3.2 Add `/session` and its ephemeral Fork, Rewind, Compact, Clear, Context, and Goal view with confirmations where state is discarded; owner: a new `claude_discord/discord_ui/session_actions.py`, `tests/test_session_actions.py`, and thin command registration; depends on 3.1; verify every button calls exactly one shared service.
- [x] 3.3 Normalize discovered models into model-plus-harness choices and change `/switch` to direct selection with current marker, recency ordering, availability filtering, and typed search; owner: `claude_discord/model_catalog.py`, `claude_discord/cogs/backend_command.py`, and focused tests; depends on 1.1; verify `uv run pytest tests/test_model_catalog.py tests/test_backend_command.py -q`.
- [ ] 3.4 Wire `/close` and Sessions Open/Close to the lifecycle service, including persisted pending-close completion and restart reconciliation; owner: thin adapters in the chat cog/setup plus lifecycle tests; depends on 1.3 and 2.4; verify active, idle, duplicate, restart, and reopen scenarios pass.

## 4. Registration, migration, and verification

- [ ] 4.1 Register `/new`, `/sessions`, `/settings`, `/session`, and `/close`, make `/help` location-aware, and fail closed for commands used in the wrong place while leaving old registrations intact; owner: setup/command adapters and `tests/test_help_sync.py`; depends on sections 2 and 3; verify new and legacy commands coexist in the test command tree.
- [ ] 4.2 Run the full command-surface check plus `uv run ruff check claude_discord/ tests/` and `uv run pyright claude_discord/`, repair only in-scope failures, and verify the security checklist for Cog/subprocess changes; owner: integration fixes only; depends on 4.1.
- [ ] 4.3 Enable the local dev worktree and complete the three Discord checks below without retiring old commands; owner: no source expansion; depends on 4.2; verify Drew records acceptance or concrete fixes.
- [ ] 4.4 After recorded acceptance only, remove superseded command registrations and old launcher buttons without deleting service code or stored state, then rerun Check; owner: command registration/help tests; depends on 4.3; verify the final registered union is exactly eight commands.

## How to try it

- In the control center, send a message and confirm one fresh status/control row appears at the bottom with New session, Sessions, and Settings while the old row disappears.
- Start `/new`, verify Favorites/Recent/Browse/Create/Clone are offered, create an idle thread, and confirm no AI runs until you type a task.
- In a session, try `/switch`, `/session`, `/stop`, and `/close`, then reopen the closed session from Sessions and confirm its folder and conversation continue.
