"""T11c — the start adapters answer with the running build, never a second one."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.loop_store import LoopStore
from claude_discord.cogs.task_loop import BuildAlreadyRunningError, TaskLoopCog
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
async def client_and_cog():  # noqa: ANN201
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    repo = NotificationRepository(path)
    await repo.init_db()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 5
    cog = MagicMock()
    cog.start_asking = AsyncMock()
    cog.find_running = AsyncMock(return_value=None)
    bot = MagicMock()
    bot.cogs = {"TaskLoopCog": cog}
    bot.get_channel.return_value = channel
    api = ApiServer(repo=repo, bot=bot, host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client, cog, channel
    await client.close()
    os.unlink(path)


async def test_repeated_post_answers_with_the_running_build(client_and_cog) -> None:  # noqa: ANN001
    client, cog, _channel = client_and_cog
    running = MagicMock()
    running.worker_thread_id = 777
    running.build_id = "thread-777"
    cog.find_running.return_value = running

    resp = await client.post("/api/loops", json={"plan_path": "/p/PLAN.md", "report_thread_id": 5})

    assert resp.status == 200
    assert (await resp.json()) == {
        "status": "running",
        "thread_id": 777,
        "build_id": "thread-777",
    }
    cog.start_asking.assert_not_called()


async def test_a_new_plan_still_starts(client_and_cog) -> None:  # noqa: ANN001
    client, cog, _channel = client_and_cog
    resp = await client.post("/api/loops", json={"plan_path": "/p/PLAN.md", "report_thread_id": 5})
    assert resp.status == 202 and (await resp.json()) == {"status": "starting"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


async def test_find_running_and_the_command_path_report_the_existing_thread(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "PLAN.md").write_text("- [ ] Task 1: a\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")

    bot = MagicMock()
    chat = MagicMock()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 555
    thread.mention = "<#555>"
    thread.send = AsyncMock(return_value=MagicMock())
    chat.spawn_session = AsyncMock(return_value=thread)
    gate: list = []

    async def slow(*_a, result_sink, **_k):  # noqa: ANN001, ANN002, ANN003
        gate.append(result_sink)  # never answers: the build stays running

    chat.run_fresh_turn = AsyncMock(side_effect=slow)
    chat._backend_settings = None
    bot.cogs = {"ClaudeChatCog": chat}
    tmp = Path(tempfile.mkdtemp(prefix="gowork-ad-"))
    cog = TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()

    assert await cog.find_running(str(tmp_path / "PLAN.md")) is None
    await cog.start_loop(channel, str(tmp_path / "PLAN.md"))
    found = await cog.find_running(str(tmp_path / "PLAN.md"))
    assert found is not None and found.worker_thread_id == 555

    # The command path (start_asking) answers with the thread instead of "Could not start".
    report = MagicMock()
    report.send = AsyncMock()
    result = await cog.start_asking(channel, str(tmp_path / "PLAN.md"), harness="claude")
    assert result is thread
    posted = " ".join(
        str(c.args[0]) for c in report.send.call_args_list + channel.send.call_args_list if c.args
    )
    assert "already running" in posted and "<#555>" in posted
    assert len(cog.running) == 1
    with pytest.raises(BuildAlreadyRunningError):
        await cog.start_loop(channel, str(tmp_path / "PLAN.md"))
