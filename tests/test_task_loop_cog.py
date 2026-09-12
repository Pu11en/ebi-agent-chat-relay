"""Tests for cogs.task_loop — Discord wiring of the sequential task loop."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.loop_store import LoopStore
from claude_code_core.work_copy import create_work_copy
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
    tmp = Path(tempfile.mkdtemp(prefix="gowork-test-"))
    cog = TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))
    return cog, chat, thread


class TestStartLoop:
    async def test_runs_the_plan_in_a_new_thread_with_fresh_sessions(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()

        got = await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert got is thread
        assert chat.spawn_session.await_args.kwargs["auto_start"] is False
        # The work happened in the build's own copy; "looks good" then added it to
        # the real project and removed the copy.
        work_dir = Path(chat.spawn_session.await_args.kwargs["working_dir"])
        assert work_dir != repo
        assert "- [x]" in (repo / "PLAN.md").read_text()
        assert not work_dir.exists()
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
        await _type_when_asked(cog, 1, "looks good")
        if cog.running:
            await asyncio.wait_for(cog.running[0].task, 10)

        assert thread.id in chat._get_dashboard.return_value.quiet_thread_ids
        texts = [str(c.args[0]) for c in channel.send.call_args_list]
        done_line = next(t for t in texts if "Task 1 of 1 done" in t)
        all_done = next(t for t in texts if "All 1 tasks are done" in t)
        end_line = next(t for t in texts if "is finished" in t)
        assert "<@42>" not in done_line  # progress is quiet
        assert "<@42>" not in all_done  # no double ping before the card
        assert "<@42>" in end_line  # the finished card pings


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

    def test_plan_reply_by_letter(self, tmp_path: Path) -> None:
        from claude_discord.cogs.task_loop import parse_plan_reply

        a, b = tmp_path / "PLAN-v1.md", tmp_path / "fix-plan.md"
        assert parse_plan_reply("B", [a, b]) == b
        assert parse_plan_reply("a)", [a, b]) == a
        assert parse_plan_reply("option b", [a, b]) == b
        assert parse_plan_reply("Z", [a, b]) is None
        assert parse_plan_reply("the fix one", [a, b]) is None

    def test_letters_keep_going_past_z(self) -> None:
        from claude_discord.cogs.task_loop import choice_letter

        assert [choice_letter(i) for i in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]

    def test_long_lists_are_split_into_messages(self) -> None:
        from claude_discord.cogs.task_loop import _chunks

        lines = [f"**{i})** plan-{i}.md · 3 of 5 left" for i in range(300)]
        chunks = _chunks(lines)
        assert len(chunks) > 1 and all(len(c) <= 1900 for c in chunks)
        assert sum(c.count("\n") + 1 for c in chunks) == 300

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
        await _type_when_asked(cog, 1, "looks good")
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


class TestResume:
    async def test_finished_build_is_forgotten(self, repo: Path) -> None:
        cog, _, _ = _cog_with_chat()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        if cog.running:
            await asyncio.wait_for(cog.running[0].task, 10)
        assert cog._store.all() == []

    async def test_a_saved_build_carries_on_in_its_own_thread(self, repo: Path) -> None:
        from claude_code_core.loop_store import LoopRecord

        cog, chat, thread = _cog_with_chat()
        copy = await create_work_copy(repo, repo / "PLAN.md", root=cog._work_root)
        cog._store.save(
            LoopRecord(
                repo_dir=str(repo),
                plan_path=str(repo / "PLAN.md"),
                copy_path=str(copy.path),
                copy_plan=str(copy.plan_path),
                branch=copy.branch,
                worker_thread_id=thread.id,
                report_channel_id=1,
            )
        )
        report_channel = MagicMock()
        report_channel.id = 1
        report_channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(
            side_effect=lambda cid: thread if cid == thread.id else report_channel
        )

        assert await cog.resume_all() == 1
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        chat.spawn_session.assert_not_called()  # same thread, no new one
        assert chat.run_fresh_turn.await_count == 1
        posted = " ".join(str(c.args[0]) for c in report_channel.send.call_args_list)
        assert "Resuming after a restart: Task 1 of 1" in posted

    async def test_gone_copy_is_dropped_not_resumed(self, repo: Path) -> None:
        from claude_code_core.loop_store import LoopRecord

        cog, chat, thread = _cog_with_chat()
        cog._store.save(
            LoopRecord(
                repo_dir=str(repo),
                plan_path=str(repo / "PLAN.md"),
                copy_path=str(repo / "missing"),
                copy_plan=str(repo / "missing" / "PLAN.md"),
                branch="gowork/x",
                worker_thread_id=thread.id,
                report_channel_id=1,
            )
        )
        cog.bot.get_channel = MagicMock(return_value=thread)
        assert await cog.resume_all() == 0
        assert cog._store.all() == []


async def _type_when_asked(cog: TaskLoopCog, channel_id: int, text: str) -> None:
    """Act like Drew: wait for the bot's question in *channel_id*, then type."""
    for _ in range(500):
        if channel_id in cog._waiters:
            msg = MagicMock()
            msg.channel.id = channel_id
            msg.content = text
            assert cog.take_message(msg)
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the bot never asked")


class TestEnding:
    def _channel(self) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return channel

    async def test_looks_good_keeps_the_work_and_cleans_up(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list)
        assert "is finished" in posted
        assert "- [x]" in (repo / "PLAN.md").read_text()  # the work is in the project now
        thread.delete.assert_awaited()
        assert cog._store.all() == []

    async def test_anything_else_becomes_a_fix_task_and_it_goes_again(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "the output is ugly")
        # the worker fixes it (the fake ticks the new Fix task), then asks again
        for _ in range(500):
            if chat.run_fresh_turn.await_count >= 2:
                break
            await asyncio.sleep(0.01)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert chat.run_fresh_turn.await_count == 2
        assert "Fix: the output is ugly" in (repo / "PLAN.md").read_text()
        thread.delete.assert_awaited()


class TestStartAsking:
    async def test_asks_for_harness_by_typing_then_starts(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        settings.current_backend = AsyncMock(return_value="codex")
        chat._backend_settings = settings
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()

        starter = asyncio.create_task(cog.start_asking(channel, str(repo / "PLAN.md")))
        await _type_when_asked(cog, 1, "claude sonnet")
        await asyncio.wait_for(starter, 10)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        settings.set_backend.assert_awaited_once_with("claude", thread_id=thread.id)
        settings.set_model.assert_awaited_once_with("claude", "sonnet", thread_id=thread.id)


class TestPickPlanAfterClearedSession:
    def _setup(self, tmp_path: Path) -> tuple[TaskLoopCog, MagicMock, Path]:
        cog, chat, _ = _cog_with_chat()
        chat.repo.get = AsyncMock(return_value=None)  # the session was cleared
        chat.runner.working_dir = str(tmp_path)
        proj = tmp_path / "realpage"
        (proj / ".worktrees" / "wt-77").mkdir(parents=True)
        (proj / "PLAN-v5.md").write_text("- [ ] make the chat look nice\n")
        channel = MagicMock()
        channel.id = 77
        channel.send = AsyncMock()
        return cog, channel, proj

    async def test_finds_the_project_from_the_threads_session_copy(self, tmp_path: Path) -> None:
        cog, channel, proj = self._setup(tmp_path)
        assert await cog._pick_plan(channel) == str(proj / "PLAN-v5.md")

    async def test_unknown_project_lists_plans_from_every_project(self, tmp_path: Path) -> None:
        cog, channel, proj = self._setup(tmp_path)
        (tmp_path / "gomer").mkdir()
        (tmp_path / "gomer" / "plan.md").write_text("- [ ] y\n")
        channel.id = 78  # no session copy for this thread
        picker = asyncio.create_task(cog._pick_plan(channel))
        await _type_when_asked(cog, 78, "b")
        picked = await asyncio.wait_for(picker, 5)
        listing = " ".join(str(c.args[0]) for c in channel.send.call_args_list)
        assert "A)" in listing and "B)" in listing
        assert "realpage" in listing and "gomer" in listing
        assert picked in (str(proj / "PLAN-v5.md"), str(tmp_path / "gomer" / "plan.md"))


class TestPickPlanFreeText:
    """Typing something other than a letter goes to the normal chat instead."""

    def _setup(self, tmp_path: Path) -> tuple[TaskLoopCog, MagicMock]:
        cog, chat, _ = _cog_with_chat()
        chat.repo.get = AsyncMock(return_value=None)
        chat.runner.working_dir = str(tmp_path)
        proj = tmp_path / "realpage"
        (proj / ".worktrees" / "wt-77").mkdir(parents=True)
        (proj / "PLAN-v5.md").write_text("- [ ] a\n")
        (proj / "PLAN-v4.md").write_text("- [ ] b\n")
        channel = MagicMock()
        channel.id = 77
        channel.send = AsyncMock()
        return cog, channel

    async def test_a_question_is_released_to_the_chat(self, tmp_path: Path) -> None:
        from claude_discord.cogs.task_loop import PickDeclinedError

        cog, channel = self._setup(tmp_path)
        picker = asyncio.create_task(cog._pick_plan(channel))
        for _ in range(100):
            if 77 in cog._waiters:
                break
            await asyncio.sleep(0.01)
        message = MagicMock()
        message.channel.id = 77
        message.content = "which of these can I delete?"
        assert cog.take_message(message) is False  # the chat answers it
        with pytest.raises(PickDeclinedError):
            await asyncio.wait_for(picker, 5)
        assert 77 not in cog._waiters

    async def test_a_letter_is_still_taken(self, tmp_path: Path) -> None:
        cog, channel = self._setup(tmp_path)
        picker = asyncio.create_task(cog._pick_plan(channel))
        await _type_when_asked(cog, 77, "A")
        assert (await asyncio.wait_for(picker, 5)).endswith(".md")


PLAN_WITH_CHECKS = """# Plan
## How to try it
- Open the page and see the calculator
- Type 3 + 4 and get 7

## Tasks
- [ ] Task 1: build it
- [ ] Task 2: Put it live on Railway
"""


def _checking_chat(chat: MagicMock, verdict: str) -> None:
    """Make the fake worker also answer the bot's check round."""
    real = chat.run_fresh_turn.side_effect

    async def turn(seed, thread, prompt, *, working_dir, result_sink):  # noqa: ANN001
        if "You are checking finished work" in prompt:
            await result_sink(verdict, None)
            return
        await real(seed, thread, prompt, working_dir=working_dir, result_sink=result_sink)

    chat.run_fresh_turn = AsyncMock(side_effect=turn)


def _embeds(channel: MagicMock) -> list[discord.Embed]:
    return [c.kwargs["embed"] for c in channel.send.call_args_list if "embed" in c.kwargs]


class TestCards:
    @pytest.fixture
    def checks_repo(self, repo: Path) -> Path:
        (repo / "PLAN.md").write_text(PLAN_WITH_CHECKS)
        _git(repo, "commit", "-qam", "plan")
        return repo

    def _channel(self) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return channel

    async def test_start_card_says_which_steps_need_you(self, checks_repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        _checking_chat(chat, "PASS: Open the page — ok\nPASS: Type 3 + 4 — 7\nDONE")
        channel = self._channel()
        await cog.start_loop(channel, str(checks_repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        start = _embeds(channel)[0].description or ""
        assert "🟢 **build it**" in start
        assert "🟡 **Put it live on Railway**" in start

    async def test_finished_card_shows_the_bots_own_checks(self, checks_repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        _checking_chat(chat, "PASS: Open the page — it loads\nFAIL: Type 3 + 4 — shows 8\nDONE")
        channel = self._channel()
        await cog.start_loop(channel, str(checks_repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        end = _embeds(channel)[-1].description or ""
        assert "✅ Open the page — it loads" in end
        assert "❌ Type 3 + 4 — shows 8" in end
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "is finished" in posted
        assert "localhost" not in posted

    async def test_typing_fix_turns_the_failed_checks_into_a_fix_step(
        self, checks_repo: Path
    ) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        _checking_chat(chat, "FAIL: Type 3 + 4 — shows 8\nDONE")
        channel = self._channel()
        await cog.start_loop(channel, str(checks_repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "fix")
        await asyncio.sleep(0.3)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert (
            "Fix: make these checks pass: Type 3 + 4 — shows 8"
            in (checks_repo / "PLAN.md").read_text()
        )

    async def test_verdict_typed_in_the_worker_thread_counts(self, checks_repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        _checking_chat(chat, "PASS: Open the page — ok\nDONE")
        channel = self._channel()
        await cog.start_loop(channel, str(checks_repo / "PLAN.md"))
        for _ in range(500):
            if 1 in cog._waiters and cog.running and cog.running[0].in_review:
                break
            await asyncio.sleep(0.01)
        msg = MagicMock()
        msg.channel.id = thread.id
        msg.content = "looks good"
        assert cog.take_message(msg) is True
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        thread.delete.assert_awaited()
