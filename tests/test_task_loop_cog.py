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


class TestHarnessAndModel:
    def test_harness_prompt_lists_every_harness_and_marks_current(self) -> None:
        from claude_discord.cogs.task_loop import harness_prompt

        prompt = harness_prompt(current="dsh")
        values = [c.value for c in prompt.choices]
        assert {"claude", "codex", "dsh"} <= set(values)
        assert any("current" in c.label for c in prompt.choices if c.value == "dsh")

    def test_model_prompt_offers_list_plus_typing_your_own(self) -> None:
        from claude_discord.cogs.task_loop import TYPE_OWN, model_prompt

        prompt = model_prompt("claude", [("sonnet", "fast"), ("opus", "strong")])
        values = [c.value for c in prompt.choices]
        assert values[:2] == ["sonnet", "opus"]
        assert values[-1] == TYPE_OWN  # opens a text box: GLM, DeepSeek Pro, anything

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


class TestPlanPicker:
    def test_one_button_per_open_plan_with_tasks_left(self, tmp_path: Path) -> None:
        from claude_discord.cogs.task_loop import plan_prompt

        a = tmp_path / "PLAN-v1.md"
        a.write_text("- [x] one\n- [ ] two\n- [ ] three\n")
        (tmp_path / "docs").mkdir()
        b = tmp_path / "docs" / "fix-plan.md"
        b.write_text("- [ ] x\n")
        prompt = plan_prompt(tmp_path, [a, b])
        labels = [c.label for c in prompt.choices]
        assert labels[0].startswith("PLAN-v1.md") and "2 of 3 left" in labels[0]
        assert labels[1].startswith("docs/fix-plan.md")
        assert [c.value for c in prompt.choices] == ["0", "1"]
