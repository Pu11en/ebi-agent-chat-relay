"""Tests for cogs.task_loop — Discord wiring of the sequential task loop."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.task_loop import TaskLoopCog, resolve_repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "PLAN.md").write_text("- [ ] Task 1: a\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


class TestResolveRepo:
    async def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            await resolve_repo(Path("PLAN.md"))

    async def test_missing_or_non_markdown_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            await resolve_repo(tmp_path / "nope.md")
        (tmp_path / "plan.txt").write_text("x")
        with pytest.raises(ValueError, match="not found"):
            await resolve_repo(tmp_path / "plan.txt")

    async def test_outside_git_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text("- [ ] a")
        with pytest.raises(ValueError, match="git"):
            await resolve_repo(tmp_path / "PLAN.md")

    async def test_returns_repo_root(self, repo: Path) -> None:
        assert (await resolve_repo(repo / "PLAN.md")).resolve() == repo.resolve()


def _cog_with_chat() -> tuple[TaskLoopCog, MagicMock, MagicMock]:
    bot = MagicMock()
    chat = MagicMock()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 555
    thread.mention = "<#555>"
    thread.send = AsyncMock(return_value=MagicMock())
    chat.spawn_session = AsyncMock(return_value=thread)

    async def fresh_turn(seed, thread, prompt, *, working_dir, result_sink):  # noqa: ANN001
        # The worker ticks its task and commits, like a real round would.
        plan = Path(working_dir) / "PLAN.md"
        plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
        _git(Path(working_dir), "commit", "-qam", "tick")
        await result_sink("recap\nDONE", None)

    chat.run_fresh_turn = AsyncMock(side_effect=fresh_turn)
    bot.cogs = {"ClaudeChatCog": chat}
    cog = TaskLoopCog(bot, work_root=Path(tempfile.mkdtemp(prefix="gowork-test-")))
    return cog, chat, thread


class TestStartLoop:
    async def test_runs_the_plan_in_a_new_thread_with_fresh_sessions(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()

        got = await cog.start_loop(channel, str(repo / "PLAN.md"))
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert got is thread
        assert chat.spawn_session.await_args.kwargs["auto_start"] is False
        # The work happened in the build's own copy, not in the real project.
        work_dir = Path(chat.spawn_session.await_args.kwargs["working_dir"])
        assert work_dir != repo
        assert "- [ ]" in (repo / "PLAN.md").read_text()
        assert "- [x]" in (work_dir / "PLAN.md").read_text()
        assert chat.run_fresh_turn.await_count == 1
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list)
        assert "Task 1 of 1 done" in posted
        assert "All 1 tasks are done" in posted
        assert cog.running == []

    async def test_second_loop_in_same_repo_refused(self, repo: Path) -> None:
        cog, chat, _ = _cog_with_chat()
        blocker = asyncio.Event()

        async def slow_turn(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            await blocker.wait()
            await result_sink("x\nSTUCK: test", None)

        chat.run_fresh_turn = AsyncMock(side_effect=slow_turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        with pytest.raises(ValueError, match="already running"):
            await cog.start_loop(channel, str(repo / "PLAN.md"))
        blocker.set()
        await asyncio.wait_for(cog.running[0].task, 10)

    async def test_plan_without_checkboxes_refused(self, repo: Path) -> None:
        (repo / "PLAN.md").write_text("# no tasks\n")
        cog, _, _ = _cog_with_chat()
        channel = MagicMock(spec=discord.TextChannel)
        with pytest.raises(ValueError, match="no `- \\[ \\]` tasks"):
            await cog.start_loop(channel, str(repo / "PLAN.md"))

    async def test_stop_for_matches_worker_or_report_channel(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        blocker = asyncio.Event()

        async def slow_turn(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            await blocker.wait()
            await result_sink("x\nSTUCK: test", None)

        chat.run_fresh_turn = AsyncMock(side_effect=slow_turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        assert cog.stop_for(999) is None
        assert cog.stop_for(thread.id) is not None
        blocker.set()
        await asyncio.wait_for(cog.running[0].task, 10)


class TestPings:
    async def test_worker_thread_is_quiet_and_only_the_end_pings(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        chat._get_dashboard = MagicMock(return_value=MagicMock(quiet_thread_ids=set()))
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()

        await cog.start_loop(channel, str(repo / "PLAN.md"), notify_user_id=42)
        if cog.running:
            await asyncio.wait_for(cog.running[0].task, 10)

        assert thread.id in chat._get_dashboard.return_value.quiet_thread_ids
        texts = [str(c.args[0]) for c in channel.send.call_args_list]
        done_line = next(t for t in texts if "Task 1 of 1 done" in t)
        end_line = next(t for t in texts if "All 1 tasks are done" in t)
        assert "<@42>" not in done_line  # progress is quiet
        assert "<@42>" in end_line  # the end pings


class TestTypedPickers:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("claude sonnet", ("claude", "sonnet")),
            ("DSH deepseek-pro", ("dsh", "deepseek-pro")),
            ("codex", ("codex", None)),
            ("same", ("dsh", None)),
            ("use claude with opus please", ("claude", "opus")),
            ("banana", None),
        ],
    )
    def test_harness_reply(self, text: str, expected: tuple[str, str | None] | None) -> None:
        from claude_discord.cogs.task_loop import parse_harness_reply

        assert parse_harness_reply(text, current="dsh") == expected

    def test_plan_reply_by_number_or_words(self, tmp_path: Path) -> None:
        from claude_discord.cogs.task_loop import parse_plan_reply

        a, b = tmp_path / "PLAN-v1.md", tmp_path / "fix-plan.md"
        assert parse_plan_reply("2", [a, b]) == b
        assert parse_plan_reply("the fix one", [a, b]) == b
        assert parse_plan_reply("9", [a, b]) is None

    async def test_chosen_harness_and_model_stick_to_the_worker_thread(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        chat._backend_settings = settings
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()

        await cog.start_loop(channel, str(repo / "PLAN.md"), harness="dsh", model="deepseek-pro")
        if cog.running:
            await asyncio.wait_for(cog.running[0].task, 10)

        settings.set_backend.assert_awaited_once_with("dsh", thread_id=thread.id)
        settings.set_model.assert_awaited_once_with("dsh", "deepseek-pro", thread_id=thread.id)


class TestTypedReplyRouting:
    def _message(self, channel_id: int, text: str) -> MagicMock:
        m = MagicMock()
        m.channel.id = channel_id
        m.content = text
        return m

    async def test_a_waiting_question_takes_the_next_typed_message(self) -> None:
        cog, _, _ = _cog_with_chat()
        waiter = asyncio.create_task(cog.wait_for_reply(50, timeout=5))
        await asyncio.sleep(0)
        assert cog.take_message(self._message(50, "yes")) is True
        assert await waiter == "yes"

    async def test_messages_elsewhere_are_left_alone(self) -> None:
        cog, _, _ = _cog_with_chat()
        assert cog.take_message(self._message(51, "hello")) is False

    async def test_typing_in_a_busy_worker_thread_becomes_a_note(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        blocker = asyncio.Event()

        async def slow_turn(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            await blocker.wait()
            await result_sink("x\nSTUCK: test", None)

        chat.run_fresh_turn = AsyncMock(side_effect=slow_turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        assert cog.take_message(self._message(thread.id, "make it blue")) is True
        assert cog.running[0].loop._notes == ["make it blue"]
        blocker.set()
        await asyncio.wait_for(cog.running[0].task, 10)


class TestChatCogHandsRepliesToGowork:
    def test_claimed_messages_are_not_treated_as_chat(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        chat = MagicMock()
        loop_cog = MagicMock()
        loop_cog.take_message.return_value = True
        chat.bot.cogs = {"TaskLoopCog": loop_cog}
        msg = MagicMock()
        assert ClaudeChatCog._claimed_by_task_loop(chat, msg) is True
        loop_cog.take_message.return_value = False
        assert ClaudeChatCog._claimed_by_task_loop(chat, msg) is False
        chat.bot.cogs = {}
        assert ClaudeChatCog._claimed_by_task_loop(chat, msg) is False
