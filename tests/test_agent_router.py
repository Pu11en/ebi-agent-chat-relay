"""Tests for friendly agent-name routing over the relay API."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.agent_router import parse_agent_routes
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer


def test_parse_agent_routes_accepts_aliases_and_normalized_names() -> None:
    directory = parse_agent_routes("drewai=1550757693784989707|drew,drew ai; imac:222; david=333")

    assert directory.resolve("DrewAI").thread_id == 1550757693784989707
    assert directory.resolve("drew ai").thread_id == 1550757693784989707
    assert directory.resolve("drew").thread_id == 1550757693784989707
    assert directory.resolve("iMac").thread_id == 222
    assert directory.resolve("DAVID").thread_id == 333


def test_parse_agent_routes_accepts_remote_ccdb_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DREWAI_RELAY_SECRET", "secret-value")
    directory = parse_agent_routes(
        """
        {
          "drewai": {
            "url": "https://drew.example/api/agents/drewai/message",
            "secret_env": "DREWAI_RELAY_SECRET",
            "aliases": ["drew", "drew ai"]
          }
        }
        """
    )

    route = directory.resolve("Drew AI")

    assert route.thread_id is None
    assert route.remote_url == "https://drew.example/api/agents/drewai/message"
    assert route.bearer_token == "secret-value"
    public = route.to_public_dict()
    assert public["target"] == "remote"
    assert "secret-value" not in str(public)


def test_parse_agent_routes_rejects_missing_remote_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DREWAI_RELAY_SECRET", raising=False)
    with pytest.raises(ValueError, match="DREWAI_RELAY_SECRET"):
        parse_agent_routes(
            '{"drewai": {"url": "https://drew.example/api/agents/drewai/message",'
            ' "secret_env": "DREWAI_RELAY_SECRET"}}'
        )


def test_parse_agent_routes_rejects_duplicate_aliases() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_agent_routes("drewai=111|main;david=222|main")


class FakeThread:
    def __init__(self, thread_id: int, name: str = "Target thread") -> None:
        self.id = thread_id
        self.name = name


@pytest.fixture
async def repo(tmp_path) -> NotificationRepository:
    repo = NotificationRepository(str(tmp_path / "notifications.db"))
    await repo.init_db()
    return repo


@pytest.mark.asyncio
async def test_agent_message_endpoint_delivers_to_named_route(
    repo: NotificationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    import discord

    monkeypatch.setenv("CCDB_AGENT_ROUTES", "drewai=777|drew,drew ai")
    monkeypatch.setattr(discord, "Thread", FakeThread)

    target_thread = FakeThread(777, "Overall Discord bot changes")
    cog = MagicMock()
    cog.deliver_relayed_message = AsyncMock()
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": cog}
    bot.get_channel.return_value = target_thread

    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/agents/Drew%20AI/message",
            json={"text": "Search Drew's main projects for Pinterest tools.", "from_thread": 111},
        )
        assert resp.status == 202
        body = await resp.json()
        assert body["status"] == "delivered"
        assert body["agent_id"] == "drewai"
        assert body["thread_id"] == 777

        await asyncio.sleep(0)
        cog.deliver_relayed_message.assert_awaited_once()
        delivered_thread, prompt = cog.deliver_relayed_message.await_args.args
        assert delivered_thread is target_thread
        assert "Search Drew's main projects for Pinterest tools." in prompt
        assert "thread 111" in prompt
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_agent_message_endpoint_forwards_to_remote_route(
    repo: NotificationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def remote_handler(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.json_response(
            {"status": "delivered", "agent_id": "drewai", "thread_id": 999},
            status=202,
        )

    remote_app = web.Application()
    remote_app.router.add_post("/api/agents/drewai/message", remote_handler)
    remote_server = TestServer(remote_app)
    remote_client = TestClient(remote_server)
    await remote_client.start_server()

    try:
        monkeypatch.setenv("DREWAI_RELAY_SECRET", "secret-value")
        monkeypatch.setenv(
            "CCDB_AGENT_ROUTES",
            (
                '{"drewai": {"url": "'
                f"{remote_client.make_url('/api/agents/drewai/message')}"
                '", "secret_env": "DREWAI_RELAY_SECRET", "aliases": ["drew"]}}'
            ),
        )
        bot = MagicMock()
        bot.cogs = {}
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post(
                "/api/agents/drew/message",
                json={"text": "find realpage", "from_thread": 111, "hop": 1},
            )
            assert resp.status == 202
            body = await resp.json()
            assert body["status"] == "delivered"
            assert body["agent_id"] == "drewai"
            assert seen["auth"] == "Bearer secret-value"
            assert seen["body"] == {"text": "find realpage", "from_thread": 111, "hop": 1}
        finally:
            await client.close()
    finally:
        await remote_client.close()


@pytest.mark.asyncio
async def test_agent_message_endpoint_returns_404_for_unknown_agent(
    repo: NotificationRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CCDB_AGENT_ROUTES", "drewai=777")
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": MagicMock()}

    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/agents/david/message",
            json={"text": "hello", "from_thread": 111},
        )
        assert resp.status == 404
        body = await resp.json()
        assert "Known agents: drewai" in body["error"]
    finally:
        await client.close()
