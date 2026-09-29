"""Worker cleanup must not close review chats or destroy unsuccessful attempts."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.loop_store import LoopStore
from claude_code_core.work_copy import WorkCopy, create_work_copy
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.cogs.task_loop import TaskLoopCog, _Running
from claude_discord.database.models import init_db
from claude_discord.database.repository import SessionRepository
from claude_discord.database.settings_repo import SettingsRepository


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def thread_at(thread_id: int) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id, thread.name = thread_id, "worker"
    thread.jump_url = f"https://discord.com/channels/1/2/{thread_id}"
    thread.send, thread.edit, thread.delete = AsyncMock(), AsyncMock(), AsyncMock()
    return thread


@dataclass
class Build:
    cog: TaskLoopCog
    chat: SimpleNamespace
    running: _Running
    copy: WorkCopy
    sessions: SessionRepository
    settings: SettingsRepository
    threads: list[MagicMock] = field(default_factory=list)
    paths: list[Path] = field(default_factory=list)


@pytest.fixture
async def build(tmp_path: Path) -> Build:
    path = str(tmp_path / "sessions.db")
    await init_db(path)
    sessions, settings = SessionRepository(path), SettingsRepository(path)
    repo = tmp_path / "project"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "PLAN.md").write_text("- [ ] implement task\n")
    (repo / "shared.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    copy = await create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "copies")
    chat = SimpleNamespace(
        repo=sessions,
        _settings_repo=settings,
        _active_runners={},
        _backend_settings=None,
        _get_dashboard=lambda: None,
    )
    bot = MagicMock(cogs={"ClaudeChatCog": chat})
    cog = TaskLoopCog(bot, work_root=tmp_path / "copies", store=LoopStore(tmp_path / "loops.json"))
    parent = thread_at(100)
    await sessions.save(100, "parent-native", working_dir=str(copy.path))
    running = _Running(
        loop=MagicMock(),
        repo_dir=repo,
        worker_thread_id=100,
        report_channel_id=200,
        build_id="build",
        copy=copy,
        thread=parent,
        report_target=parent,
        report=AsyncMock(),
        mode="cheap",
    )
    running.loop.manifest_summary.return_value = None
    running.loop.friction_lines.return_value = []
    result = Build(cog, chat, running, copy, sessions, settings)

    async def spawn(*args: object, working_dir: str, **kwargs: object) -> MagicMock:
        thread = thread_at(101 + len(result.threads))
        result.threads.append(thread)
        result.paths.append(Path(working_dir))
        await sessions.save(thread.id, f"worker-{thread.id}", working_dir=working_dir)
        # Simulate an old worker-held name to prove closure releases it.
        await settings.set(f"voice_label:{thread.id}", "zoro")
        return thread

    chat.spawn_session = AsyncMock(side_effect=spawn)
    cog._record = AsyncMock()
    cog._ai_label = AsyncMock(return_value="fake backend")
    return result


async def test_finished_build_remains_runnable_while_waiting_for_verdict(build: Build) -> None:
    cog, running = build.cog, build.running
    cog._check_it_myself = AsyncMock(return_value=[])
    cog._missing_steps = AsyncMock(return_value=[])
    cog._lessons = AsyncMock(return_value=[])
    chat_gate = ClaudeChatCog.__new__(ClaudeChatCog)
    chat_gate.repo = build.sessions

    async def verdict(*args: object) -> tuple[str, bool]:
        record = await build.sessions.get(100)
        assert record is not None and record.is_open, "review is not workflow completion"
        assert not await chat_gate._close_requested(100), "normal questions must still enter"
        assert record.session_id == "parent-native"
        return "fix", False

    cog._wait_or_wake = AsyncMock(side_effect=verdict)
    assert await cog._wrap_up(running) == "fix"
    running.thread.delete.assert_not_awaited()


async def run_group(build: Build, outcome: str) -> list[tuple[str, bool, str]]:
    async def turn(*args: object, working_dir: str, result_sink, **kwargs: object) -> None:
        path = Path(working_dir)
        (path / "shared.txt").write_text("worker change\n")
        git(path, "commit", "-qam", "worker evidence")
        if outcome == "conflict":
            (build.copy.path / "shared.txt").write_text("integration change\n")
            git(build.copy.path, "commit", "-qam", "integration evidence")
        if outcome == "failed":
            (path / "unfinished.txt").write_text("uncommitted work must also survive\n")
        await result_sink("STUCK: incomplete" if outcome == "failed" else "DONE", None)

    build.chat.run_fresh_turn = AsyncMock(side_effect=turn)
    return await build.cog._run_group(build.running, ["implement task"])


async def test_integrated_legacy_worker_closes_without_deleting_history(build: Build) -> None:
    result = await run_group(build, "accepted")
    thread = build.threads[0]
    record = await build.sessions.get(thread.id)
    assert result[0][1] is True
    assert record is not None and record.is_closed
    assert record.session_id == f"worker-{thread.id}"
    assert await build.settings.get(f"voice_label:{thread.id}") is None
    thread.edit.assert_awaited_with(archived=True)
    thread.delete.assert_not_awaited()
    assert all("locked" not in call.kwargs for call in thread.edit.await_args_list)


@pytest.mark.parametrize("outcome", ["failed", "conflict"])
async def test_unsuccessful_legacy_worker_keeps_open_history_and_files(
    build: Build, outcome: str
) -> None:
    result = await run_group(build, outcome)
    thread, path = build.threads[0], build.paths[0]
    record = await build.sessions.get(thread.id)
    assert result[0][1] is False
    observed = {
        "session_open": record is not None and record.is_open,
        "worktree_kept": path.is_dir(),
        "thread_deletes": thread.delete.await_count,
        "thread_archives": thread.edit.await_count,
    }
    assert observed == {
        "session_open": True,
        "worktree_kept": True,
        "thread_deletes": 0,
        "thread_archives": 0,
    }
    assert (path / "shared.txt").read_text() == "worker change\n"
    if outcome == "failed":
        assert (path / "unfinished.txt").exists()
    assert "- [ ] implement task" in build.copy.plan_path.read_text()


async def test_repeated_legacy_group_does_not_recycle_an_unsuccessful_worktree(
    build: Build,
) -> None:
    await run_group(build, "failed")
    first = build.paths[0]
    await run_group(build, "failed")
    assert build.paths[1] != first, "a retry must not erase the prior attempt"
    assert (first / "unfinished.txt").exists()
    assert (build.paths[1] / "unfinished.txt").exists()


@pytest.mark.parametrize("ending", ["approved", "auto", "already-integrated", "early"])
async def test_completed_build_parent_is_closed_without_deleting_its_conversation(
    build: Build, ending: str
) -> None:
    cog, running = build.cog, build.running
    cog._keep_build = AsyncMock(return_value=(True, "integrated locally"))
    cog._wait_or_wake = AsyncMock(return_value=("looks good", False))
    state = SimpleNamespace(
        integrated_commit="integrated locally" if ending == "already-integrated" else None,
        mark_integrated=MagicMock(),
    )
    cog._build_state = MagicMock(return_value=state)
    await build.settings.set("voice_label:100", "zoro")
    if ending == "approved":
        assert await cog._wait_verdict(running, [], []) == "kept"
    elif ending == "early":
        assert await cog._finish_early(running) == "gone"
    else:
        assert await cog._auto_integrate(running, running.report_target, "") == "kept"
    record = await build.sessions.get(100)
    assert record is not None and record.is_closed
    assert record.session_id == "parent-native"
    assert await build.settings.get("voice_label:100") is None
    running.thread.delete.assert_not_awaited()
    assert all("locked" not in call.kwargs for call in running.thread.edit.await_args_list)
