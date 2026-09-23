"""Read-only handoff authority is enforced in the worker's argv, not in its prompt.

A peer that hands over ``authority={"read": true, "edit": false}`` used to get
a worker with the instance's normal tools and permission mode plus a prompt
that asked it not to edit. These tests pin the enforcement: the runner the
worker is spawned with names a read-only tool set on the command line, a
backend that cannot restrict tools refuses to start, and the executor asks
for it on every read-only task.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.handoffs import protocol as p
from claude_code_core.runner import ClaudeRunner
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.handoff_authority import (
    READ_ONLY_TOOLS,
    RecipientPolicy,
    restrict_to_read_only,
)
from claude_discord.handoff_executor import HandoffExecutor, build_handoff_prompt
from claude_discord.handoff_projects import ApprovedRootResolver

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"


def _argv(runner: ClaudeRunner) -> list[str]:
    return runner._build_args("do the thing", None)


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---------------------------------------------------------------------------
# The runner: --tools is the enforcement
# ---------------------------------------------------------------------------


class TestClaudeRunnerToolRestriction:
    def test_no_restriction_by_default(self) -> None:
        argv = _argv(ClaudeRunner(command="claude"))
        assert "--tools" not in argv
        assert "--strict-mcp-config" not in argv

    def test_tools_and_strict_mcp_reach_the_argv(self) -> None:
        runner = ClaudeRunner(command="claude", tools=["Read", "Grep"], strict_mcp_config=True)
        argv = _argv(runner)
        assert _flag_value(argv, "--tools") == "Read,Grep"
        assert "--strict-mcp-config" in argv

    def test_clone_keeps_the_restriction(self) -> None:
        runner = ClaudeRunner(command="claude", tools=["Read"], strict_mcp_config=True)
        clone = runner.clone(thread_id=1, append_system_prompt="x")
        argv = _argv(clone)
        assert _flag_value(argv, "--tools") == "Read"
        assert "--strict-mcp-config" in argv


class TestRestrictToReadOnly:
    def test_sets_the_read_only_tool_set_on_a_claude_runner(self) -> None:
        runner = ClaudeRunner(command="claude", allowed_tools=["Bash(git:*)", "Edit"])
        restrict_to_read_only(runner)
        argv = _argv(runner)
        assert _flag_value(argv, "--tools") == ",".join(READ_ONLY_TOOLS)
        assert "--strict-mcp-config" in argv
        # Nothing outside the read-only set stays pre-approved either.
        assert _flag_value(argv, "--allowedTools") == ",".join(READ_ONLY_TOOLS)

    def test_the_read_only_set_has_no_mutating_tool(self) -> None:
        assert not {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "WebFetch"} & set(
            READ_ONLY_TOOLS
        )

    def test_a_backend_that_cannot_restrict_tools_is_refused(self) -> None:
        codex_like = SimpleNamespace(allowed_tools=None, permission_mode="auto")
        with pytest.raises(RuntimeError, match="read-only"):
            restrict_to_read_only(codex_like)


# ---------------------------------------------------------------------------
# The chat cog: the worker is spawned with the restricted runner
# ---------------------------------------------------------------------------


class _StubStatus:
    _stall_hard = 300

    async def set_queued(self) -> None:
        return None


def _cog_with_real_runner(monkeypatch: pytest.MonkeyPatch, captured: list[object]) -> ClaudeChatCog:
    import claude_discord.cogs.claude_chat as chat_mod

    bot = MagicMock()
    bot.channel_id = 999
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.save = AsyncMock()
    runner = ClaudeRunner(command="claude", allowed_tools=["Bash(git:*)"])
    cog = ClaudeChatCog(bot=bot, repo=repo, runner=runner)
    cog._get_dashboard = lambda: None  # type: ignore[method-assign]
    cog._prepare_cross_backend_handoff = AsyncMock(return_value=(None, "work"))  # type: ignore[method-assign]
    cog._get_current_model = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cog._get_allowed_tools = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cog._get_current_effort = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cog._complete_pending_close = AsyncMock()  # type: ignore[method-assign]

    async def fake_run(config: object) -> None:
        captured.append(config)

    monkeypatch.setattr(chat_mod, "run_claude_with_config", fake_run)
    monkeypatch.setattr(chat_mod, "StatusManager", lambda *a, **k: _StubStatus())
    return cog


@pytest.mark.asyncio
async def test_run_claude_read_only_restricts_the_runner_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    cog = _cog_with_real_runner(monkeypatch, captured)
    thread = MagicMock(spec=discord.Thread)
    thread.id = 123
    thread.send = AsyncMock()
    message = MagicMock(spec=discord.Message)
    message.author = SimpleNamespace(id=1, bot=True)

    await cog._run_claude(message, thread, "work", None, chat_only=True, read_only=True)

    assert len(captured) == 1
    config: object = captured[0]
    argv = _argv(config.runner)  # type: ignore[attr-defined]
    assert _flag_value(argv, "--tools") == ",".join(READ_ONLY_TOOLS)
    assert "--strict-mcp-config" in argv
    assert "Edit" not in _flag_value(argv, "--tools")


@pytest.mark.asyncio
async def test_run_claude_without_read_only_keeps_the_normal_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    cog = _cog_with_real_runner(monkeypatch, captured)
    thread = MagicMock(spec=discord.Thread)
    thread.id = 124
    thread.send = AsyncMock()
    message = MagicMock(spec=discord.Message)
    message.author = SimpleNamespace(id=1, bot=True)

    await cog._run_claude(message, thread, "work", None, chat_only=True)

    argv = _argv(captured[0].runner)  # type: ignore[attr-defined]
    assert "--tools" not in argv


def _bind_read_only_check(cog: SimpleNamespace) -> None:
    cog._require_read_only_capable_backend = MethodType(
        ClaudeChatCog._require_read_only_capable_backend, cog
    )


class TestHandoffEntryPointsCarryReadOnly:
    @pytest.mark.asyncio
    async def test_run_handoff_turn_passes_read_only_through(self) -> None:
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append(kw)

        thread = SimpleNamespace(id=4242, send=AsyncMock())
        settings = SimpleNamespace(set_backend=AsyncMock(), set_model=AsyncMock())
        cog = SimpleNamespace(
            repo=SimpleNamespace(ensure_working_dir=AsyncMock(), get=AsyncMock(return_value=None)),
            _backend_settings=settings,
            _run_claude=_run_claude,
            _session_id_for_current_backend=AsyncMock(),
            runner=ClaudeRunner(command="claude"),
        )
        _bind_read_only_check(cog)

        await ClaudeChatCog.run_handoff_turn(
            cog,  # type: ignore[arg-type]
            thread,
            "look, do not touch",
            working_dir="/srv/proj",
            result_sink=AsyncMock(),
            backend="claude",
            model="haiku",
            read_only=True,
        )
        await asyncio.sleep(0)

        assert run_calls and run_calls[0]["read_only"] is True

    @pytest.mark.asyncio
    async def test_run_handoff_turn_refuses_read_only_on_a_backend_without_tool_control(
        self,
    ) -> None:
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append(kw)

        thread = SimpleNamespace(id=4242, send=AsyncMock())
        settings = SimpleNamespace(
            set_backend=AsyncMock(),
            set_model=AsyncMock(),
            current_backend=AsyncMock(return_value="codex"),
        )
        cog = SimpleNamespace(
            repo=SimpleNamespace(ensure_working_dir=AsyncMock(), get=AsyncMock(return_value=None)),
            _backend_settings=settings,
            _run_claude=_run_claude,
            _session_id_for_current_backend=AsyncMock(),
            runner=ClaudeRunner(command="claude"),
        )
        _bind_read_only_check(cog)

        with pytest.raises(RuntimeError, match="read-only"):
            await ClaudeChatCog.run_handoff_turn(
                cog,  # type: ignore[arg-type]
                thread,
                "look, do not touch",
                working_dir="/srv/proj",
                result_sink=AsyncMock(),
                backend="codex",
                read_only=True,
            )
        await asyncio.sleep(0)

        assert run_calls == []
        thread.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_session_passes_read_only_through(self) -> None:
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append(kw)

        thread = MagicMock(spec=discord.Thread)
        thread.id = 555
        thread.send = AsyncMock(return_value=MagicMock())
        thread.add_user = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.create_thread = AsyncMock(return_value=thread)
        settings = SimpleNamespace(set_backend=AsyncMock(), set_model=AsyncMock())
        cog = SimpleNamespace(
            runner=ClaudeRunner(command="claude"),
            repo=SimpleNamespace(ensure_working_dir=AsyncMock()),
            _backend_settings=settings,
            _ensure_thread_members=AsyncMock(),
            _run_claude=_run_claude,
        )
        _bind_read_only_check(cog)

        await ClaudeChatCog.spawn_session(
            cog,  # type: ignore[arg-type]
            channel,
            "look only",
            working_dir="/srv/proj",
            backend="claude",
            model="haiku",
            read_only=True,
        )
        await asyncio.sleep(0)

        assert run_calls and run_calls[0]["read_only"] is True


# ---------------------------------------------------------------------------
# The executor: every read-only task asks for it
# ---------------------------------------------------------------------------


def _task(authority: p.AuthorityScope, *, folder: str) -> p.HandoffTask:
    origin = p.ConversationCoordinate(guild_id=111, channel_id=222, thread_id=333, message_id=444)
    return p.HandoffTask(
        task_id=TASK_ID,
        sender="david",
        recipient="drewai",
        origin=origin,
        origin_human_id="555",
        project=p.ProjectLocator(owner="drew", folder=folder),
        goal="Summarise the README",
        authority=authority,
        expected_result="A short report.",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=6),
    )


class _Chat:
    def __init__(self) -> None:
        self.bot = MagicMock()
        self.spawn_session = AsyncMock(return_value=SimpleNamespace(id=999, name="w"))

    def handoff_capacity_available(self) -> bool:
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authority", "policy", "expected"),
    [
        (p.AuthorityScope(read=True), RecipientPolicy(), True),
        (p.AuthorityScope(read=True, edit=True), RecipientPolicy(allow_edit=True), False),
    ],
)
async def test_executor_spawns_read_only_when_the_effective_scope_is(
    tmp_path, authority: p.AuthorityScope, policy: RecipientPolicy, expected: bool
) -> None:
    from claude_discord.database.handoff_repo import HandoffRepository
    from claude_discord.database.models import init_db

    (tmp_path / "proj").mkdir()
    db = tmp_path / "h.db"
    await init_db(str(db))
    repo = HandoffRepository(str(db))
    task = _task(authority, folder="proj")
    await repo.record_task(task, now=NOW)
    chat = _Chat()
    executor = HandoffExecutor(
        repo=repo,
        local_agent_id="drewai",
        resolver=ApprovedRootResolver(roots={"drew": (tmp_path,)}),
        policy=policy,
    )

    started = await executor.run_ready(chat=chat, parent_channel=SimpleNamespace(id=1), now=NOW)

    assert len(started) == 1
    assert chat.spawn_session.await_args.kwargs["read_only"] is expected


def test_edit_paths_are_declared_unenforced_in_the_prompt() -> None:
    scope = p.AuthorityScope(read=True, edit=True, edit_paths=("docs",))
    task = _task(scope, folder="proj")
    prompt = build_handoff_prompt(task, project_path="/srv/proj", effective=scope)
    assert "docs" in prompt
    assert "not enforced" in prompt.lower()
