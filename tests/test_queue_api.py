"""POST /api/loops with "queue": true puts the plan in the build queue."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer


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
    cog.enqueue = AsyncMock(return_value=2)
    cog.start_asking = AsyncMock()
    bot = MagicMock()
    bot.cogs = {"TaskLoopCog": cog}
    bot.get_channel.return_value = channel
    api = ApiServer(repo=repo, bot=bot, host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client, cog, channel
    await client.close()
    os.unlink(path)


async def test_queue_true_queues_instead_of_starting(client_and_cog) -> None:  # noqa: ANN001
    client, cog, channel = client_and_cog
    resp = await client.post(
        "/api/loops", json={"plan_path": "/p/PLAN.md", "report_thread_id": 5, "queue": True}
    )
    assert resp.status == 202
    assert (await resp.json()) == {"status": "queued", "place": 2}
    cog.enqueue.assert_awaited_once()
    assert cog.enqueue.await_args.args == (channel, "/p/PLAN.md")
    cog.start_asking.assert_not_called()
