"""Tests for cogs.task_loop — Discord wiring of the sequential task loop."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

    async def fresh_turn(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
        if "checking finished work" in prompt:  # the bot's own end check
            await result_sink("PASS: the goal — it works\nDONE", None)
            return
        if "agree the goal" in prompt:  # the goal interview: no goal agreed here
            await result_sink("No questions.", None)
            return
        # The worker ticks its task and commits, like a real round would.
        plan = Path(working_dir) / "PLAN.md"
        plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
        _git(Path(working_dir), "commit", "-qam", "tick")
        await result_sink("recap\nDONE", None)

    chat.run_fresh_turn = AsyncMock(side_effect=fresh_turn)
    bot.cogs = {"ClaudeChatCog": chat}
    tmp = Path(tempfile.mkdtemp(prefix="gowork-test-"))
    cog = TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))
    cog._quick_ai = AsyncMock(return_value=None)  # never call a real AI in tests
    cog._interview_ai = AsyncMock(return_value=None)  # the goal interview's AI, too
    cog.smart_unstick = False  # the TestSmartUnsticking tests turn it on
    cog.smart_review = False  # the TestSecondAiReview tests turn it on
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
        posted = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
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
        await _type_when_asked(cog, 1, "throw it away")  # a stuck build waits
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
        await _type_when_asked(cog, 1, "throw it away")  # a stuck build waits
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
        in_thread = [str(c.args[0]) for c in thread.send.call_args_list if c.args]
        in_channel = [str(c.args[0]) for c in channel.send.call_args_list if c.args]
        done_line = next(t for t in in_thread if "Task 1 of 1 done" in t)
        all_done = next(t for t in in_thread if "All 1 tasks are done" in t)
        end_line = next(t for t in in_channel if "is finished" in t)
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
        await _type_when_asked(cog, 1, "throw it away")  # a stuck build waits
        await asyncio.wait_for(cog.running[0].task, 10)

    async def test_close_in_the_starting_channel_ends_that_session(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        chat.close_session = AsyncMock()
        blocker = asyncio.Event()

        async def slow_turn(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            await blocker.wait()
            await result_sink("x\nSTUCK: test", None)

        chat.run_fresh_turn = AsyncMock(side_effect=slow_turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        assert cog.take_message(self._message(1, "hello")) is False  # normal chat
        msg = self._message(1, " Close ")
        assert cog.take_message(msg) is True
        await asyncio.sleep(0)
        chat.close_session.assert_awaited_once_with(msg.channel)
        assert cog.running[0].loop._notes == []  # the build is untouched
        blocker.set()
        await _type_when_asked(cog, 1, "throw it away")
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

    async def test_a_legacy_record_without_build_id_is_migrated_and_resumed(
        self, repo: Path
    ) -> None:
        """T04: records saved before build ids exist still resume, and are upgraded on disk."""
        import json

        cog, chat, thread = _cog_with_chat()
        copy = await create_work_copy(repo, repo / "PLAN.md", root=cog._work_root)
        legacy = {
            "repo_dir": str(repo),
            "plan_path": str(repo / "PLAN.md"),
            "copy_path": str(copy.path),
            "copy_plan": str(copy.plan_path),
            "branch": copy.branch,
            "worker_thread_id": thread.id,
            "report_channel_id": 1,
        }
        cog._store.path.parent.mkdir(parents=True, exist_ok=True)
        cog._store.path.write_text(json.dumps([legacy]), encoding="utf-8")
        report_channel = MagicMock()
        report_channel.id = 1
        report_channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(
            side_effect=lambda cid: thread if cid == thread.id else report_channel
        )

        assert await cog.resume_all() == 1
        on_disk = json.loads(cog._store.path.read_text(encoding="utf-8"))
        assert on_disk[0]["build_id"] == f"thread-{thread.id}"
        assert cog.running[0].build_id == f"thread-{thread.id}"
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        assert cog._store.all() == []  # forgotten by build id, not by repo

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
        posted = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
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
    """Act like Drew: wait for the bot's question in *channel_id*, then type.

    The budget is wall-clock, not a count of polls: before the question a build may
    make several git commits, which on a loaded machine (full suite, WSL disk) took
    longer than the old 500 x 10 ms and failed the test intermittently.
    """
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        waiter = cog._waiters.get(channel_id)
        if waiter is not None and not waiter.done():
            msg = MagicMock()
            msg.channel.id = channel_id
            msg.content = text
            if cog.take_message(msg):  # an older question may still be on the way out
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

        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "is finished" in posted
        assert "- [x]" in (repo / "PLAN.md").read_text()  # the work is in the project now
        thread.delete.assert_awaited()
        assert cog._store.all() == []

    async def test_finished_worker_archives_before_waiting_for_verdict(self, repo: Path) -> None:
        cog, _chat, thread = _cog_with_chat()
        thread.edit = AsyncMock()
        thread.delete = AsyncMock()
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        for _ in range(500):
            if cog.running and cog.running[0].finished:
                break
            await asyncio.sleep(0.01)

        assert cog.running and cog.running[0].finished
        thread.edit.assert_awaited_with(archived=True, reason="go-work finished")
        thread.delete.assert_not_awaited()

        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

    async def test_after_the_build_the_thread_is_a_normal_chat(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):  # wait for the finished card
            if cog._waiters.get(555) is not None:
                break
            await asyncio.sleep(0.01)

        question = MagicMock()
        question.channel.id = 555
        question.content = "can i ask you questions here?"
        assert cog.take_message(question) is False  # the normal chat answers it
        # ...and a change made in that chat is part of the build
        work_dir = Path(chat.spawn_session.await_args.kwargs["working_dir"])
        (work_dir / "chat-edit.txt").write_text("made while chatting")

        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert chat.run_fresh_turn.await_count == 1  # no fix step was run
        assert "Fix:" not in (repo / "PLAN.md").read_text()
        assert (repo / "chat-edit.txt").read_text() == "made while chatting"
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

    async def turn(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
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

        end = _embeds(channel)[-1].description or ""  # the card is in the build's thread
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


class TestStuckBuildWaits:
    def _stuck_then_fine(self, chat: MagicMock) -> None:
        """First round says STUCK; later rounds work normally."""
        real = chat.run_fresh_turn.side_effect
        calls = {"n": 0}

        async def turn(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                await result_sink("x\nSTUCK: the tests need a database", None)
                return
            await real(seed, thread, prompt, working_dir=working_dir, result_sink=result_sink)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)

    def _channel(self) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return channel

    async def test_keep_going_picks_up_where_it_stopped(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        self._stuck_then_fine(chat)
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 555, "keep going, use sqlite")
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert chat.run_fresh_turn.await_count == 2  # no restart from scratch
        second_prompt = chat.run_fresh_turn.await_args_list[1].args[2]
        assert "keep going, use sqlite" in second_prompt
        assert "- [x]" in (repo / "PLAN.md").read_text()
        posted = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "own words" in posted and "close" in posted and "throw it away" in posted

    async def test_skip_moves_past_the_step(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        self._stuck_then_fine(chat)
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 555, "skip")
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert chat.run_fresh_turn.await_count == 1
        assert "(skipped)" in (repo / "PLAN.md").read_text()

    async def test_throw_it_away_cleans_up_and_leaves_the_project_alone(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        self._stuck_then_fine(chat)
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        work_dir = Path(chat.spawn_session.await_args.kwargs["working_dir"])
        await _type_when_asked(cog, 1, "throw it away")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert not work_dir.exists()
        assert "- [ ]" in (repo / "PLAN.md").read_text()
        thread.delete.assert_awaited()
        assert cog._store.all() == []


class TestAiPickerByLetter:
    async def test_lists_every_ai_and_model_and_a_letter_picks_one(self) -> None:
        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [
                ("claude", "sonnet", "balanced"),
                ("claude", "opus", "strongest"),
                ("codex", "gpt-6", "newest"),
                ("dsh", "deepseek-pro", "cheap"),
            ]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "claude"))
        await _type_when_asked(cog, 9, "E")  # A per step, B same, then the list
        assert await asyncio.wait_for(picker, 5) == ("codex", "gpt-6")
        listing = " ".join(str(c.args[0]) for c in channel.send.call_args_list)
        assert "A)" in listing and "each step" in listing and "same" in listing.lower()
        assert "deepseek-pro" in listing and "F)" in listing

    async def test_a_means_the_bot_picks_per_step(self) -> None:
        from claude_discord.cogs.task_loop import PER_STEP

        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", "")]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "dsh"))
        await _type_when_asked(cog, 9, "a")
        assert await asyncio.wait_for(picker, 5) == (PER_STEP, None)

    async def test_b_means_keep_the_threads_ai(self) -> None:
        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", "")]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "dsh"))
        await _type_when_asked(cog, 9, "b")
        assert await asyncio.wait_for(picker, 5) == ("dsh", None)

    async def test_typing_the_name_still_works(self) -> None:
        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", "")]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "dsh"))
        await _type_when_asked(cog, 9, "codex")
        assert await asyncio.wait_for(picker, 5) == ("codex", None)


class TestStartedByWords:
    async def test_same_means_the_threads_ai_without_asking(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        settings.current_backend = AsyncMock(return_value="codex")
        chat._backend_settings = settings
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()

        got = await asyncio.wait_for(
            cog.start_asking(channel, str(repo / "PLAN.md"), harness="same"), 10
        )
        assert got is thread
        settings.set_backend.assert_awaited_once_with("codex", thread_id=thread.id)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

    async def test_the_only_allowed_user_is_pinged_when_nobody_was_named(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        cog._allowed_user_ids = {42}
        thread.delete = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))  # no notify_user_id
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "<@42>" in posted


class TestSwitchingPlans:
    def _stuck(self, chat: MagicMock) -> None:
        async def turn(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            await result_sink("x\nSTUCK: needs Drew at the computer", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)

    def _channel(self) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return channel

    async def test_other_talk_is_left_for_the_chat_while_stopped(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        self._stuck(chat)
        await cog.start_loop(self._channel(), str(repo / "PLAN.md"))
        for _ in range(500):
            if cog.running and cog.running[0].in_review and 1 in cog._waiters:
                break
            await asyncio.sleep(0.01)
        msg = MagicMock()
        msg.channel.id = 1
        msg.content = "ok can we do go work now on the plan 6"
        assert cog.take_message(msg) is False  # the chat answers it
        await asyncio.sleep(0.05)
        assert cog.running and cog.running[0].in_review  # still waiting, not resumed
        await _type_when_asked(cog, 1, "throw it away")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

    async def test_wrap_up_keeps_finished_steps_and_frees_the_project(self, repo: Path) -> None:
        (repo / "PLAN.md").write_text("- [ ] Task 1: a\n- [ ] Task 2: b\n")
        _git(repo, "commit", "-qam", "two")
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        real = chat.run_fresh_turn.side_effect
        calls = {"n": 0}

        async def turn(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] == 1:
                await real(seed, thread, prompt, working_dir=working_dir, result_sink=result_sink)
            else:
                await result_sink("x\nSTUCK: needs Drew", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        await cog.start_loop(self._channel(), str(repo / "PLAN.md"))
        await _type_when_asked(cog, 555, "wrap up")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        text = (repo / "PLAN.md").read_text()
        assert "- [x] Task 1: a" in text and "- [ ] Task 2: b" in text  # step 1 kept
        assert cog.running == [] and cog._store.all() == []
        thread.delete.assert_awaited()

    async def test_starting_a_new_plan_waits_for_the_stopped_one(self, repo: Path) -> None:
        (repo / "PLAN-v6.md").write_text("- [ ] Task 1: new\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "v6")
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        self._stuck(chat)
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):
            if cog.running and cog.running[0].in_review:
                break
            await asyncio.sleep(0.01)
        chat._backend_settings = None
        # Nobody types anything: starting plan 6 closes the stopped build itself.
        got = await asyncio.wait_for(
            cog.start_asking(channel, str(repo / "PLAN-v6.md"), harness="claude"), 10
        )
        assert got is thread
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "Switching" in posted
        assert cog.running and cog.running[0].copy.plan_path.name == "PLAN-v6.md"
        cog.stop_for(thread.id)
        await _type_when_asked(cog, 1, "throw it away")
        for r in list(cog.running):
            await asyncio.wait_for(r.task, 10)


class TestCogUnloadStopsBuilds:
    async def test_unload_cancels_and_awaits_a_build_waiting_on_a_question(
        self, repo: Path
    ) -> None:
        cog, chat, thread = _cog_with_chat()
        chat._backend_settings = None

        async def asks(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            await result_sink("x\nASK: which colour?", None)

        chat.run_fresh_turn = AsyncMock(side_effect=asks)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):
            if thread.id in cog._waiters:
                break
            await asyncio.sleep(0.01)
        assert cog.running, "the build should still be waiting for an answer"
        tasks = {r.task for r in cog.running if r.task is not None}

        await cog.cog_unload()

        assert tasks, "the build's task should have been running"
        done, pending = await asyncio.wait(tasks, timeout=5)
        assert not pending, "unload must not leave a build task running"


class TestSwitchWhileWaiting:
    def _channel(self) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return channel

    async def _v6(self, repo: Path) -> None:
        (repo / "PLAN-v6.md").write_text("- [ ] Task 1: new\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "v6")

    async def test_switch_while_the_old_build_waits_on_a_question(self, repo: Path) -> None:
        await self._v6(repo)
        cog, chat, thread = _cog_with_chat()
        chat._backend_settings = None
        thread.delete = AsyncMock()

        async def asks(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            await result_sink("x\nASK: which colour?", None)

        chat.run_fresh_turn = AsyncMock(side_effect=asks)
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):
            if thread.id in cog._waiters:
                break
            await asyncio.sleep(0.01)
        got = await asyncio.wait_for(
            cog.start_asking(channel, str(repo / "PLAN-v6.md"), harness="claude"), 10
        )
        assert got is thread
        assert cog.running[0].copy.plan_path.name == "PLAN-v6.md"

    async def test_switch_during_the_final_review_keeps_the_finished_build(
        self, repo: Path
    ) -> None:
        await self._v6(repo)
        cog, chat, thread = _cog_with_chat()
        chat._backend_settings = None
        thread.delete = AsyncMock()
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):
            if cog.running and cog.running[0].in_review:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(
            cog.start_asking(channel, str(repo / "PLAN-v6.md"), harness="claude"), 10
        )
        assert "- [x] Task 1: a" in (repo / "PLAN.md").read_text()  # old work kept


class TestWorkerThreadDeleted:
    async def test_the_build_ends_cleanly_and_keeps_its_work(self, repo: Path) -> None:
        (repo / "PLAN.md").write_text("- [ ] Task 1: a\n- [ ] Task 2: b\n")
        _git(repo, "commit", "-qam", "two")
        cog, chat, thread = _cog_with_chat()
        real = chat.run_fresh_turn.side_effect
        calls = {"n": 0}

        async def turn(seed, th, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            calls["n"] += 1
            await real(seed, th, prompt, working_dir=working_dir, result_sink=result_sink)
            if calls["n"] == 1:
                gone = discord.NotFound(MagicMock(status=404), "Unknown Channel")
                thread.send = AsyncMock(side_effect=gone)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = self._channel()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert "- [x] Task 1: a" in (repo / "PLAN.md").read_text()
        assert cog.running == [] and cog._store.all() == []

    def _channel(self) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return channel


class TestStopButtonStopsTheBuild:
    async def test_stop_in_the_worker_thread_stops_the_build(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        blocker = asyncio.Event()

        async def slow(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            await blocker.wait()
            await result_sink("x\nSTUCK: interrupted", None)

        chat.run_fresh_turn = AsyncMock(side_effect=slow)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await cog.on_session_stopped(thread.id)
        assert cog.running[0].loop._stop is True  # stops, does not retry the step
        await cog.on_session_stopped(1)  # a stop in the planning thread is just a chat stop
        blocker.set()
        await _type_when_asked(cog, 1, "throw it away")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

    async def test_other_threads_are_ignored(self) -> None:
        cog, _, _ = _cog_with_chat()
        await cog.on_session_stopped(12345)  # no build: nothing happens


class TestCloseSession:
    async def test_close_kills_forgets_and_archives(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        chat = MagicMock()
        runner = MagicMock()
        runner.kill = AsyncMock()
        chat._active_runners = {7: runner}
        chat.repo.delete = AsyncMock()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 7
        thread.send = AsyncMock()
        thread.edit = AsyncMock()

        await ClaudeChatCog.close_session(chat, thread)

        runner.kill.assert_awaited_once()
        assert 7 not in chat._active_runners
        chat.repo.delete.assert_awaited_once_with(7)
        thread.edit.assert_awaited_once_with(archived=True)


async def test_build_rounds_do_not_hear_the_lounge() -> None:
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    chat = MagicMock()
    chat._run_claude = AsyncMock()
    await ClaudeChatCog.run_fresh_turn(
        chat, MagicMock(), MagicMock(), "task", working_dir="/x", result_sink=AsyncMock()
    )
    assert chat._run_claude.await_args.kwargs["lounge"] is False


class TestBuildTalksInItsOwnThread:
    async def test_stuck_question_is_asked_and_answered_in_the_worker_thread(
        self, repo: Path
    ) -> None:
        cog, chat, thread = _cog_with_chat()
        calls = 0

        async def turn(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            nonlocal calls
            calls += 1
            await result_sink("x\nSTUCK: no idea", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        await _type_when_asked(cog, 1, "throw it away")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        in_thread = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "I'm stuck" in in_thread and "own words" in in_thread
        in_channel = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "Thrown away" in in_channel  # the thread is deleted, so the result goes here
        assert 1 not in cog._waiters

    async def test_a_hint_typed_in_a_stuck_build_keeps_it_going(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        outcomes = iter(["x\nSTUCK: no idea", None])

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            nxt = next(outcomes)
            if nxt is None:
                plan = Path(working_dir) / "PLAN.md"
                plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
                _git(Path(working_dir), "commit", "-qam", "tick")
                nxt = "done\nDONE"
            await result_sink(nxt, None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        await _type_when_asked(cog, thread.id, "use the other file")
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert "use the other file" in chat.run_fresh_turn.await_args_list[1].args[2]


class TestUsageLimitInThread:
    def _limited_then_fine(self, chat: MagicMock) -> None:
        calls = 0

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls == 1:
                await result_sink(None, "You've hit your session limit · resets 7pm")
                return
            plan = Path(working_dir) / "PLAN.md"
            plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
            _git(Path(working_dir), "commit", "-qam", "tick")
            await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)

    async def _start(self, cog: TaskLoopCog, repo: Path) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), harness="claude", model="sonnet")
        return channel

    async def test_typing_another_ai_switches_and_retries_the_step(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(  # type: ignore[method-assign]
            return_value=[("claude", "sonnet", ""), ("dsh", "glm-5.3", "")]
        )
        self._limited_then_fine(chat)
        channel = await self._start(cog, repo)

        await _type_when_asked(cog, thread.id, "dsh glm-5.3")
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        settings.set_backend.assert_awaited_with("dsh", thread_id=thread.id)
        settings.set_model.assert_awaited_with("dsh", "glm-5.3", thread_id=thread.id)
        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "usage limit" in said and "wait" in said
        assert "Stuck" not in " ".join(str(c.args[0]) for c in channel.send.call_args_list)
        assert chat.run_fresh_turn.await_count == 2

    async def test_wait_tries_the_same_ai_again_later(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import claude_discord.cogs.task_loop as mod

        monkeypatch.setattr(mod, "LIMIT_WAIT_SECONDS", 0)
        cog, chat, thread = _cog_with_chat()
        chat._backend_settings = None
        self._limited_then_fine(chat)
        await self._start(cog, repo)

        await _type_when_asked(cog, thread.id, "wait")
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert chat.run_fresh_turn.await_count == 2


class TestCloseFromTheBuildThread:
    @pytest.mark.parametrize(
        "text",
        ["close", "close.", "Stop", "wrap up", "ok close", "please stop"],
    )
    def test_close_words(self, text: str) -> None:
        from claude_discord.cogs.task_loop import wants_close

        assert wants_close(text)

    @pytest.mark.parametrize(
        "text",
        [
            "make it blue",
            "don't close the modal",
            "A",
            "yes",
            "keep the header and then close the menu",
        ],
    )
    def test_not_close_words(self, text: str) -> None:
        from claude_discord.cogs.task_loop import wants_close

        assert not wants_close(text)

    async def test_close_in_the_thread_keeps_finished_steps_and_ends(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()

        async def asks(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
            await result_sink("x\nASK: run the paid check?", None)

        chat.run_fresh_turn = AsyncMock(side_effect=asks)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        await _type_when_asked(cog, thread.id, "close.")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert cog.running == []
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "Wrapped up" in posted
        thread.delete.assert_awaited()


class TestPausedBuildListens:
    async def test_pause_waits_and_the_reply_reaches_the_ai(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        calls = 0

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls == 1:
                await result_sink("saved\nPAUSE: Drew is planning the bot first", None)
                return
            plan = Path(working_dir) / "PLAN.md"
            plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
            _git(Path(working_dir), "commit", "-qam", "tick")
            await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        await _type_when_asked(cog, thread.id, "planning is done, the bot should remember facts")
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "Paused" in said and "planning the bot first" in said
        assert "remember facts" in chat.run_fresh_turn.await_args_list[1].args[2]


class TestPlannerChangesReachTheBuild:
    async def test_a_step_added_to_the_real_plan_is_built_and_kept(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        calls = 0

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls == 1:  # meanwhile the planning session adds a step (not committed)
                real = repo / "PLAN.md"
                real.write_text(real.read_text() + "- [ ] Task 2: from the planner\n")
            plan = Path(working_dir) / "PLAN.md"
            plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
            _git(Path(working_dir), "commit", "-qam", "tick")
            await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))

        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert calls == 2
        final = (repo / "PLAN.md").read_text()
        assert "- [x] Task 1: a" in final and "- [x] Task 2: from the planner" in final
        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "planning session" in said


class TestLimitFallback:
    """A build started with a fallback AI switches to it by itself on a usage limit."""

    @pytest.mark.asyncio
    async def test_switches_without_asking(self) -> None:
        from claude_discord.cogs.task_loop import _Running

        cog, chat, thread = _cog_with_chat()
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="claude")
        settings.current_model = AsyncMock(return_value="sonnet")
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        chat._backend_settings = settings
        running = _Running(
            MagicMock(), Path("/x"), 555, 1, thread=thread, fallback=("dsh", "glm-5.3")
        )

        assert await cog._limit_hit(running, "limit reached") is True
        settings.set_backend.assert_awaited_once_with("dsh", thread_id=555)
        settings.set_model.assert_awaited_once_with("dsh", "glm-5.3", thread_id=555)
        assert "switched to dsh · glm-5.3" in thread.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_fallback_itself_limited_asks(self) -> None:
        from claude_discord.cogs.task_loop import _Running

        cog, chat, thread = _cog_with_chat()
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="dsh")
        settings.current_model = AsyncMock(return_value="glm-5.3")
        settings.set_backend = AsyncMock()
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(return_value=[])
        cog._wait_or_wake = AsyncMock(return_value=(None, False))
        running = _Running(
            MagicMock(), Path("/x"), 555, 1, thread=thread, fallback=("dsh", "glm-5.3")
        )

        assert await cog._limit_hit(running, "limit reached") is False
        settings.set_backend.assert_not_awaited()


class TestGoalInterview:
    async def test_the_goal_is_agreed_in_the_planning_thread_first(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        prompts: list[str] = []

        async def interview(prompt: str, cwd: Path) -> str:
            prompts.append(prompt)
            if "hear it on my phone" not in prompt:
                return "My guesses:\nASK: What is this build for? A) ... E) other"
            return "Goal: Drew hears it on his phone.\nDone when: it plays.\nDONE"

        cog._interview_ai = AsyncMock(side_effect=interview)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        starting = asyncio.create_task(
            cog.start_loop(channel, str(repo / "PLAN.md"), ask_goal=True)
        )
        await _type_when_asked(cog, 1, "B, i want to hear it on my phone")  # planning thread
        await asyncio.wait_for(starting, 10)
        # The build's thread opened only after the goal was agreed.
        assert chat.spawn_session.await_count == 1
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        asked = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "What is this build for?" in asked and "Goal:" in asked
        first_step = chat.run_fresh_turn.await_args_list[0].args[2]
        assert "Drew hears it on his phone" in first_step
        assert "Goal: Drew hears it on his phone." in (repo / "PLAN.md").read_text()

    async def test_a_plan_with_a_goal_skips_the_interview(self, repo: Path) -> None:
        (repo / "PLAN.md").write_text("Goal: g\nDone when: d\n- [ ] Task 1: a\n")
        _git(repo, "commit", "-qam", "goal")
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), ask_goal=True)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        cog._interview_ai.assert_not_called()


class TestGoalNotMet:
    def _setup(self, repo: Path, passes_on: int):  # noqa: ANN202
        (repo / "PLAN.md").write_text("Goal: g\nDone when: it plays\n- [ ] Task 1: a\n")
        _git(repo, "commit", "-qam", "goal")
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        state = {"checks": 0, "added": 0}

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            plan = Path(working_dir) / "PLAN.md"
            if "checking finished work" in prompt:
                state["checks"] += 1
                verdict = "PASS" if state["checks"] >= passes_on else "FAIL"
                await result_sink(f"{verdict}: The goal is met: it plays — no sound\nDONE", None)
                return
            if "isn't met yet" in prompt:
                state["added"] += 1
                await result_sink(f"- [ ] Extra {state['added']}: fix the sound\nDONE", None)
                return
            plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
            _git(Path(working_dir), "commit", "-qam", "tick")
            await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return cog, thread, channel

    async def test_missing_steps_are_added_and_built_by_themselves(self, repo: Path) -> None:
        cog, thread, channel = self._setup(repo, passes_on=2)
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")  # the only reply needed
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert "- [x] Extra 1: fix the sound" in (repo / "PLAN.md").read_text()
        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "round 1 of 3" in said

    async def test_after_three_rounds_it_stops_and_asks(self, repo: Path) -> None:
        cog, thread, channel = self._setup(repo, passes_on=99)
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        plan = (repo / "PLAN.md").read_text()
        assert "- [x] Extra 3: fix the sound" in plan and "Extra 5" not in plan
        card = _embeds(channel)[-1].description or ""
        assert "isn't met" in card and "add them" in card


class TestRightAiPerStep:
    async def test_each_step_gets_the_ai_the_picker_chose(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(  # type: ignore[method-assign]
            return_value=[("claude", "haiku", "fastest"), ("codex", "gpt-5.5", "strong")]
        )
        cog._quick_ai = AsyncMock(return_value="B — it's a tricky change")
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), per_step_ai=True)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        settings.set_backend.assert_any_await("codex", thread_id=thread.id)
        settings.set_model.assert_any_await("codex", "gpt-5.5", thread_id=thread.id)
        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "codex · gpt-5.5" in said and "tricky change" in said
        assert "Task 1: a" in cog._quick_ai.await_args.args[0]

    async def test_when_the_picker_fails_the_build_keeps_its_ai(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        chat._backend_settings = None
        cog._quick_ai = AsyncMock(return_value=None)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), per_step_ai=True)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        assert chat.run_fresh_turn.await_count >= 1

    def test_start_list_offers_per_step_first(self) -> None:
        from claude_discord.cogs.task_loop import PER_STEP

        assert PER_STEP == "per-step"


class TestParallelSteps:
    async def test_independent_steps_run_side_by_side_and_all_land(self, repo: Path) -> None:
        (repo / "PLAN.md").write_text("- [ ] Task 1: a\n- [ ] Task 2: b\n")
        _git(repo, "commit", "-qam", "two")
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        thread.parent = MagicMock()
        side_threads: list[MagicMock] = []

        async def spawn(channel, text, *, thread_name, auto_start, working_dir):  # noqa: ANN001
            if not side_threads and "Task loop" in thread_name:
                side_threads.append(thread)
                return thread
            t = MagicMock(spec=discord.Thread)
            t.id = 600 + len(side_threads)
            t.send = AsyncMock(return_value=MagicMock())
            t.delete = AsyncMock()
            side_threads.append(t)
            return t

        chat.spawn_session = AsyncMock(side_effect=spawn)
        running_now = 0
        most_at_once = 0

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            nonlocal running_now, most_at_once
            if "checking finished work" in prompt:
                await result_sink("PASS: ok — ok\nDONE", None)
                return
            assert "Do exactly this one step" in prompt  # both ran in the group
            running_now += 1
            most_at_once = max(most_at_once, running_now)
            await asyncio.sleep(0.05)
            name = "a.txt" if "Task 1: a" in prompt else "b.txt"
            (Path(working_dir) / name).write_text("made\n")
            _git(Path(working_dir), "add", ".")
            _git(Path(working_dir), "commit", "-qm", name)
            running_now -= 1
            await result_sink(f"Made {name}.\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        cog._quick_ai = AsyncMock(return_value="1,2")
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert most_at_once == 2
        assert (repo / "a.txt").exists() and (repo / "b.txt").exists()
        plan = (repo / "PLAN.md").read_text()
        assert "- [x] Task 1: a" in plan and "- [x] Task 2: b" in plan
        for side in side_threads[1:]:
            side.delete.assert_awaited()
        card = _embeds(channel)[0].description or ""
        assert "at the same time" in card.lower()


class TestSmartUnsticking:
    def _setup(self, repo: Path, script: list[str]):  # noqa: ANN202
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        settings.current_backend = AsyncMock(return_value="claude")
        settings.current_model = AsyncMock(return_value="sonnet")
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                ("claude", "sonnet", "balanced"),
                ("claude", "opus", "most capable"),
                ("codex", "gpt-6", "most capable"),
            ]
        )
        prompts: list[str] = []

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            prompts.append(prompt)
            plan = Path(working_dir) / "PLAN.md"
            if "checking finished work" in prompt:
                await result_sink("PASS: ok — ok\nDONE", None)
                return
            action = script.pop(0) if script else "done"
            if action == "stuck":
                await result_sink("tried\nSTUCK: the tests keep failing", None)
            elif action == "split":
                plan.write_text(
                    plan.read_text().replace(
                        "- [ ] Task 1: a", "- [ ] Task 1a: first half\n- [ ] Task 1b: second half"
                    )
                )
                _git(Path(working_dir), "commit", "-qam", "split")
                await result_sink("split it\nPLAN: split into two", None)
            else:
                plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
                _git(Path(working_dir), "commit", "-qam", "tick")
                await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        cog.smart_unstick = True
        return cog, chat, thread, settings, prompts

    async def _run(self, cog: TaskLoopCog, thread: MagicMock, repo: Path) -> MagicMock:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        return channel

    async def test_a_stuck_step_is_tried_again_on_the_strongest_ai(self, repo: Path) -> None:
        cog, chat, thread, settings, prompts = self._setup(repo, ["stuck", "done"])
        await self._run(cog, thread, repo)
        settings.set_model.assert_any_await("claude", "opus", thread_id=thread.id)
        assert "keep failing" in prompts[1]
        settings.set_model.assert_any_await("claude", "sonnet", thread_id=thread.id)  # back after
        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "I'm stuck" not in said

    async def test_stuck_twice_splits_the_step(self, repo: Path) -> None:
        cog, chat, thread, settings, prompts = self._setup(
            repo, ["stuck", "stuck", "split", "done", "done"]
        )
        await self._run(cog, thread, repo)
        assert "smaller steps" in prompts[2]
        plan = (repo / "PLAN.md").read_text()
        assert "- [x] Task 1a: first half" in plan and "- [x] Task 1b: second half" in plan

    async def test_stuck_after_everything_asks_the_person(self, repo: Path) -> None:
        cog, chat, thread, settings, prompts = self._setup(repo, ["stuck", "stuck", "stuck"])
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "throw it away")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        said = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "I'm stuck" in said and "stronger AI" in said


class TestLearning:
    async def test_steps_are_recorded_and_the_card_says_what_to_do_next_time(
        self, repo: Path
    ) -> None:
        from claude_code_core.gowork_records import read_records

        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()

        async def quick(prompt: str) -> str | None:
            if "differently next time" in prompt:
                return "- Next time, split the big step in two.\n- Opus did well."
            return None

        cog._quick_ai = AsyncMock(side_effect=quick)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        records = read_records(cog._records_path)
        assert [(r["step"], r["result"]) for r in records] == [("Task 1: a", "done")]
        assert records[0]["repo"] == repo.name and "seconds" in records[0]
        card = _embeds(channel)[-1].description or ""  # the finished card, in the channel
        assert "Next time" in card and "split the big step" in card
        assert "split the big step" in (repo / "PLAN.progress.md").read_text()

    async def test_the_picker_sees_each_ais_track_record(self, repo: Path) -> None:
        from claude_code_core.gowork_records import append_record

        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        chat._backend_settings = None
        append_record(
            cog._records_path,
            {"ai": "codex · gpt-5.5", "result": "stuck", "seconds": 600, "step": "x"},
        )
        cog._ai_choices = AsyncMock(return_value=[("codex", "gpt-5.5", "")])  # type: ignore[method-assign]
        prompts: list[str] = []

        async def quick(prompt: str) -> str | None:
            prompts.append(prompt)
            return None

        cog._quick_ai = AsyncMock(side_effect=quick)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), per_step_ai=True)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        pick = next(p for p in prompts if "Pick the AI" in p)
        assert "codex · gpt-5.5: 1 steps (1 stuck)" in pick


def _second_repo(tmp_path: Path, name: str) -> Path:
    r = tmp_path / name
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "PLAN.md").write_text("- [ ] Task 1: a\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


class TestBuildQueue:
    def _cog(self):  # noqa: ANN202
        cog, chat, first = _cog_with_chat()
        threads: list[MagicMock] = []

        async def spawn(channel, text, *, thread_name, auto_start, working_dir):  # noqa: ANN001
            t = MagicMock(spec=discord.Thread)
            t.id = 700 + len(threads)
            t.mention = f"<#{t.id}>"
            t.send = AsyncMock(return_value=MagicMock())
            t.delete = AsyncMock()
            threads.append(t)
            return t

        chat.spawn_session = AsyncMock(side_effect=spawn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        return cog, chat, threads, channel

    async def test_queued_builds_run_one_after_another(self, tmp_path: Path) -> None:
        a, b = _second_repo(tmp_path, "alpha"), _second_repo(tmp_path, "beta")
        cog, chat, threads, channel = self._cog()

        await cog.enqueue(channel, str(a / "PLAN.md"))
        await cog.enqueue(channel, str(b / "PLAN.md"))
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "in line" in posted
        assert len(threads) == 1  # only the first build started

        # The first reaches its finished card (waiting for Drew): the line moves on.
        for _ in range(500):
            if len(threads) == 2:
                break
            await asyncio.sleep(0.01)
        assert len(threads) == 2
        await _type_when_asked(cog, threads[0].id, "looks good")
        await _type_when_asked(cog, threads[1].id, "looks good")
        for running in list(cog.running):
            await asyncio.wait_for(running.task, 10)
        assert "- [x]" in (a / "PLAN.md").read_text() and "- [x]" in (b / "PLAN.md").read_text()
        states = [e["state"] for e in cog._queue.state.history]
        assert states == ["kept in your project ✅", "kept in your project ✅"]

    async def test_the_morning_summary_posts_once_after_eight(self, tmp_path: Path) -> None:
        import datetime as dt

        cog, chat, threads, channel = self._cog()
        cog.bot.get_channel = MagicMock(return_value=channel)
        from claude_code_core.build_queue import QueueItem

        item = QueueItem(plan_path="/x/PLAN-a.md", report_id=1)
        cog._queue.started(item, "alpha", 700)
        cog._queue.note(700, "stuck: needs a key")
        # Model an overnight build independently of the date pytest is run.
        cog._queue.state.history[-1]["started"] = "2026-09-15T23:30:00"

        await cog._maybe_morning_summary(dt.datetime(2026, 9, 16, 7, 30))
        assert not channel.send.await_count
        await cog._maybe_morning_summary(dt.datetime(2026, 9, 16, 8, 5))
        await cog._maybe_morning_summary(dt.datetime(2026, 9, 16, 9, 0))
        assert channel.send.await_count == 1
        text = channel.send.await_args.args[0]
        assert "PLAN-a.md" in text and "needs a key" in text


class TestSecondAiReview:
    async def test_a_different_ai_reviews_and_can_send_a_step_back(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        cog.smart_review = True
        current = {"backend": "claude", "model": "sonnet"}
        settings = MagicMock()

        async def set_backend(h: str, *, thread_id: int) -> None:
            current["backend"] = h

        async def set_model(h: str, m: str, *, thread_id: int) -> None:
            current["model"] = m

        settings.set_backend = AsyncMock(side_effect=set_backend)
        settings.set_model = AsyncMock(side_effect=set_model)
        settings.current_backend = AsyncMock(side_effect=lambda tid=None: current["backend"])
        settings.current_model = AsyncMock(side_effect=lambda h, tid=None: current["model"])
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                ("claude", "sonnet", "balanced"),
                ("claude", "opus", ""),
                ("claude", "haiku", ""),
                ("codex", "gpt-5.5", ""),  # another family: never used (Drew's pick)
            ]
        )
        cog._quick_ai = AsyncMock(return_value="HARD — it changes the login flow")
        reviews: list[str] = []
        builder_prompts: list[str] = []

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            plan = Path(working_dir) / "PLAN.md"
            if "checking finished work" in prompt:
                await result_sink("PASS: ok — ok\nDONE", None)
                return
            if "[gowork review" in prompt:
                reviews.append(f"{current['backend']} · {current['model']}")
                verdict = "CHANGES: the test is missing" if len(reviews) == 1 else "APPROVE"
                await result_sink(f"looked\n{verdict}", None)
                return
            builder_prompts.append(prompt)
            plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
            _git(Path(working_dir), "commit", "-qam", "tick")
            await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        # A different model of the same family, never a weaker one than needed.
        assert reviews == ["claude · opus", "claude · opus"]
        assert current["backend"] == "claude" and current["model"] == "sonnet"  # back after
        assert len(builder_prompts) == 2 and "the test is missing" in builder_prompts[1]

    async def test_an_easy_step_is_not_reviewed(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        cog.smart_review = True
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        settings.current_backend = AsyncMock(return_value="claude")
        settings.current_model = AsyncMock(return_value="sonnet")
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(return_value=[("codex", "gpt-5.5", "")])  # type: ignore[method-assign]
        cog._quick_ai = AsyncMock(return_value="EASY — a one-line text change")
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        prompts = [c.args[2] for c in chat.run_fresh_turn.await_args_list]
        assert not any("[gowork review" in p for p in prompts)


class TestModes:
    def test_mode_words(self) -> None:
        from claude_discord.cogs.task_loop import parse_mode

        assert parse_mode("go work, cheap") == "cheap"
        assert parse_mode("careful please") == "careful"
        assert parse_mode("go work") is None
        assert parse_mode(None) is None

    def _settings(self, chat: MagicMock) -> None:
        settings = MagicMock()
        settings.set_backend = AsyncMock()
        settings.set_model = AsyncMock()
        settings.current_backend = AsyncMock(return_value="claude")
        settings.current_model = AsyncMock(return_value="sonnet")
        chat._backend_settings = settings

    async def _run(self, repo: Path, mode: str) -> list[str]:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        cog.smart_review = True
        self._settings(chat)
        cog._ai_choices = AsyncMock(  # type: ignore[method-assign]
            return_value=[("claude", "opus", ""), ("claude", "sonnet", "")]
        )
        cog._quick_ai = AsyncMock(return_value="EASY — small")
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), mode=mode)
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        card = _embeds(channel)[0].description or ""
        assert mode in card.lower()
        return [c.args[2] for c in chat.run_fresh_turn.await_args_list]

    async def test_careful_reviews_even_easy_steps(self, repo: Path) -> None:
        prompts = await self._run(repo, "careful")
        assert any("[gowork review" in p for p in prompts)

    async def test_cheap_never_reviews(self, repo: Path) -> None:
        prompts = await self._run(repo, "cheap")
        assert not any("[gowork review" in p for p in prompts)


class TestReviewFixesInTheCog:
    def test_pause_is_not_a_close_word(self) -> None:
        from claude_discord.cogs.task_loop import wants_close

        assert not wants_close("pause")
        assert wants_close("close")

    async def test_stop_at_the_finished_card_is_just_chat(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):
            if cog._waiters.get(555) is not None:
                break
            await asyncio.sleep(0.01)
        msg = MagicMock()
        msg.channel.id = 555
        msg.content = "stop"
        assert cog.take_message(msg) is False  # the normal chat handles it
        assert cog.running and not cog.running[0].auto_finish
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

    async def test_the_backup_ai_is_used_once_then_the_person_is_asked(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        now = {"backend": "claude"}
        settings = MagicMock()

        async def set_backend(h: str, *, thread_id: int) -> None:
            now["backend"] = h

        settings.set_backend = AsyncMock(side_effect=set_backend)
        settings.set_model = AsyncMock()
        settings.current_backend = AsyncMock(side_effect=lambda tid=None: now["backend"])
        settings.current_model = AsyncMock(return_value="gpt-5.4")  # codex's own default
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(return_value=[("dsh", "glm-5.3", "")])  # type: ignore[method-assign]
        calls = 0

        async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
            nonlocal calls
            calls += 1
            if calls <= 2:
                await result_sink(None, "You've hit your session limit")
                return
            plan = Path(working_dir) / "PLAN.md"
            plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
            _git(Path(working_dir), "commit", "-qam", "tick")
            await result_sink("done\nDONE", None)

        chat.run_fresh_turn = AsyncMock(side_effect=turn)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"), fallback_harness="codex")
        await _type_when_asked(cog, thread.id, "B")  # the second limit asks the person
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
        assert calls == 3

    async def test_a_discord_hiccup_keeps_the_plan_in_line(self, tmp_path: Path) -> None:
        repo = _second_repo(tmp_path, "gamma")
        cog, chat, thread = _cog_with_chat()
        chat.spawn_session = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "down"))
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.enqueue(channel, str(repo / "PLAN.md"))
        assert [i.plan_path for i in cog._queue.state.waiting] == [str(repo / "PLAN.md")]


async def test_a_build_whose_thread_is_gone_says_where_its_work_is(repo: Path) -> None:
    from claude_code_core.work_copy import create_work_copy as make_copy

    cog, chat, thread = _cog_with_chat()
    copy = await make_copy(repo, repo / "PLAN.md", root=cog._work_root)
    report = MagicMock()
    report.send = AsyncMock()
    cog.bot.get_channel = MagicMock(side_effect=lambda cid: report if cid == 1 else None)
    cog.bot.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    from claude_code_core.loop_store import LoopRecord

    cog._store.save(
        LoopRecord(
            repo_dir=str(repo),
            plan_path=str(repo / "PLAN.md"),
            copy_path=str(copy.path),
            copy_plan=str(copy.plan_path),
            branch=copy.branch,
            worker_thread_id=999,
            report_channel_id=1,
        )
    )
    assert await cog.resume_all() == 0
    text = report.send.await_args.args[0]
    assert copy.branch in text and "nothing was added" in text


async def test_the_goal_interview_reminds_an_ai_that_forgot_to_ask(repo: Path) -> None:
    cog, chat, thread = _cog_with_chat()
    thread.delete = AsyncMock()
    prompts: list[str] = []

    async def interview(prompt: str, cwd: Path) -> str:
        prompts.append(prompt)
        if 'They answered: "A"' in prompt:
            return "Goal: g\nDone when: d\nDONE"
        if "didn't end with an ASK line" not in prompt:
            return "This plan adds a feature."  # forgot to ask
        return "Guesses:\nASK: What's it for? A) ... E) other"

    cog._interview_ai = AsyncMock(side_effect=interview)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    starting = asyncio.create_task(cog.start_loop(channel, str(repo / "PLAN.md"), ask_goal=True))
    await _type_when_asked(cog, 1, "A")
    await asyncio.wait_for(starting, 10)
    await _type_when_asked(cog, 1, "looks good")
    await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None
    assert len(prompts) == 3


async def test_the_quick_helper_always_calls_the_claude_cli(monkeypatch) -> None:  # noqa: ANN001
    import claude_discord.cogs.task_loop as mod

    cog, chat, _ = _cog_with_chat()
    del cog._quick_ai  # use the real one
    chat.runner = MagicMock(command="codex")  # the bot's default backend is Codex
    seen: list[tuple] = []

    async def fake_exec(*args, **kwargs):  # noqa: ANN002, ANN003
        seen.append(args)
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"B - hard\n", b""))
        proc.returncode = 0
        return proc

    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)
    assert await cog._quick_ai("pick") == "B - hard"
    assert seen[0][0] == "/usr/bin/claude" and "--" in seen[0]


def test_model_tiers_from_names() -> None:
    from claude_discord.cogs.task_loop import model_tier

    assert model_tier("haiku") == 0 and model_tier("deepseek-v4-flash") == 0
    assert model_tier("sonnet") == 1 and model_tier("gpt-5.5") == 1
    assert model_tier("claude-opus-5") == 2 and model_tier("deepseek-v4-pro") == 2


class TestQueueFixesFromPracticeRun:
    async def test_the_queue_waits_for_a_build_still_being_set_up(self, repo: Path) -> None:
        cog, chat, thread = _cog_with_chat()
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="claude")
        chat._backend_settings = settings
        cog._ai_choices = AsyncMock(return_value=[("claude", "sonnet", "")])  # type: ignore[method-assign]
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        # Build 1 is asking which AI to use (not started yet)...
        asking = asyncio.create_task(cog.start_asking(channel, str(repo / "PLAN.md")))
        for _ in range(200):
            if cog._waiters.get(1) is not None:
                break
            await asyncio.sleep(0.01)
        # ...when build 2 for the same project is queued: it must wait.
        await cog.enqueue(channel, str(repo / "PLAN.md"))
        assert len(cog._queue.state.waiting) == 1
        assert chat.spawn_session.await_count == 0
        asking.cancel()

    async def test_the_summary_is_only_for_overnight_builds(self) -> None:
        import datetime as dt

        cog, chat, _ = _cog_with_chat()
        channel = MagicMock()
        channel.send = AsyncMock()
        cog.bot.get_channel = MagicMock(return_value=channel)
        from claude_code_core.build_queue import QueueItem

        cog._queue.started(QueueItem(plan_path="/x/P.md", report_id=1), "alpha", 700)
        cog._queue.state.history[-1]["started"] = "2026-09-15T16:35:31"
        await cog._maybe_morning_summary(dt.datetime(2026, 9, 15, 16, 40))
        assert not channel.send.await_count  # an afternoon build waits for tomorrow
        await cog._maybe_morning_summary(dt.datetime(2026, 9, 16, 8, 5))
        assert channel.send.await_count == 1


async def test_a_build_that_cannot_combine_stops_instead_of_repeating(repo: Path) -> None:
    """A plan switch while keeping fails must not retry for ever (it spammed Discord)."""
    import claude_discord.cogs.task_loop as mod

    cog, chat, thread = _cog_with_chat()
    thread.delete = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    await cog.start_loop(channel, str(repo / "PLAN.md"))
    for _ in range(500):
        if cog._waiters.get(1) is not None:
            break
        await asyncio.sleep(0.01)
    running = cog.running[0]
    failing = AsyncMock(return_value=(False, "the work didn't combine cleanly"))
    with patch.object(mod, "keep_work", failing):
        await _type_when_asked(cog, 1, "looks good")  # first try fails
        running.auto_finish = True  # a new plan closes this build
        running.wake.set()
        await asyncio.wait_for(running.task, 10)

    posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
    assert posted.count("couldn't keep it yet") == 1
    assert "still doesn't combine" in posted and running.copy.branch in posted


class TestAuditFixesInTheCog:
    async def test_a_model_that_isnt_on_the_list_is_never_used(self) -> None:
        from claude_discord.cogs.task_loop import PER_STEP

        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", ""), ("claude", "opus", "")]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "claude"))
        # Typing the option's words, not its letter (this produced "claude · each").
        await _type_when_asked(cog, 9, "let the bot pick the best claude model for each step")
        assert await asyncio.wait_for(picker, 5) == (PER_STEP, None)

    async def test_a_typed_name_keeps_only_a_real_model(self) -> None:
        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", "")]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "claude"))
        await _type_when_asked(cog, 9, "claude banana")
        assert await asyncio.wait_for(picker, 5) == ("claude", None)  # not "banana"

    async def test_a_build_whose_copy_vanished_ends_cleanly(self, repo: Path) -> None:
        import shutil as sh

        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        for _ in range(500):
            if cog._waiters.get(1) is not None:
                break
            await asyncio.sleep(0.01)
        running = cog.running[0]
        sh.rmtree(running.copy.path)  # someone deleted the build's copy
        await _type_when_asked(cog, 1, "looks good")
        await asyncio.wait_for(running.task, 10)

        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "copy" in posted and "gone" in posted
        assert cog.running == []

    async def test_keeping_a_clash_can_take_the_builds_version(self, repo: Path) -> None:
        import claude_discord.cogs.task_loop as mod

        cog, chat, thread = _cog_with_chat()
        thread.delete = AsyncMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.send = AsyncMock()
        await cog.start_loop(channel, str(repo / "PLAN.md"))
        calls: list[bool] = []

        async def keep(copy, *, prefer_build: bool = False):  # noqa: ANN001, ANN202
            calls.append(prefer_build)
            if not prefer_build:
                return False, "the work didn't combine cleanly"
            return True, "added to your project"

        with patch.object(mod, "keep_work", AsyncMock(side_effect=keep)):
            await _type_when_asked(cog, 1, "looks good")
            await _type_when_asked(cog, 1, "use the build's version")
            await asyncio.wait_for(cog.running[0].task, 10) if cog.running else None

        assert calls == [False, True]
        posted = " ".join(str(c.args[0]) for c in channel.send.call_args_list if c.args)
        assert "use the build's version" in posted  # it offered the way out


class TestCodexPerStep:
    """Drew: /gowork on Codex should use every Codex model, picked per step."""

    _CODEX = [
        ("codex", "gpt-6-astra", "Our most capable model for complex, demanding work."),
        ("codex", "gpt-5.6-sol", "Reliable agentic workhorse for everyday tasks."),
        ("codex", "gpt-5.6-terra", "Balanced agentic coding model for everyday work."),
        ("codex", "gpt-5.6-luna", "Fast and affordable agentic coding model."),
        ("codex", "gpt-5.5", "Proven previous-generation model for coding and general work."),
    ]

    def test_codex_tiers_read_from_the_live_notes(self) -> None:
        from claude_discord.cogs.task_loop import model_tier

        tiers = {m: model_tier(m, n) for _h, m, n in self._CODEX}
        assert tiers["gpt-6-astra"] == 2
        assert tiers["gpt-5.6-luna"] == 0  # "fast and affordable"
        assert tiers["gpt-5.5"] == 1  # "Proven" is not "pro"
        assert model_tier("deepseek-v4-pro") == 2 and model_tier("x", "Pro tier") == 2

    def test_auto_or_per_step_means_the_best_model_of_that_ai_each_step(self) -> None:
        from claude_discord.cogs.task_loop import PER_STEP, split_per_step

        assert split_per_step("codex", "auto") == ("codex", None, True)
        assert split_per_step(PER_STEP, "codex") == ("codex", None, True)
        assert split_per_step(PER_STEP, None) == (None, None, True)
        assert split_per_step("codex", "gpt-5.5") == ("codex", "gpt-5.5", False)

    async def test_typing_codex_each_step_picks_the_codex_family(self) -> None:
        from claude_discord.cogs.task_loop import PER_STEP

        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", ""), *self._CODEX]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        channel = MagicMock()
        channel.id = 9
        channel.send = AsyncMock()
        picker = asyncio.create_task(cog._ask_harness(channel, "claude"))
        await _type_when_asked(cog, 9, "codex each step")
        assert await asyncio.wait_for(picker, 5) == (PER_STEP, "codex")
        listing = " ".join(str(c.args[0]) for c in channel.send.call_args_list)
        assert "codex each step" in listing

    async def test_every_codex_model_competes_for_each_step(self) -> None:
        from types import SimpleNamespace

        cog, _, _ = _cog_with_chat()

        async def catalog() -> list[tuple[str, str, str]]:
            return [("claude", "sonnet", ""), *self._CODEX]

        cog._ai_choices = catalog  # type: ignore[method-assign]
        running = SimpleNamespace(family="codex", limited=set(), worker_thread_id=1)
        options = await cog._family_options(running)  # type: ignore[arg-type]
        assert [m for _h, m, _n in options][0] == "gpt-5.6-luna"  # cheapest first
        assert {m for _h, m, _n in options} == {m for _h, m, _n in self._CODEX}
