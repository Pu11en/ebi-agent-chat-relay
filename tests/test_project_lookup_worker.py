"""Tests for the DrewAI project lookup worker endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.project_lookup_worker import build_project_lookup_prompt


def test_project_lookup_prompt_is_read_only_and_path_first() -> None:
    prompt = build_project_lookup_prompt(
        query="find the Realpage folder and anything called CraneSignal",
        project_root="/home/drewp/main-projects",
        from_agent="david",
        from_thread=1551328742977314886,
    )

    assert "read-only" in prompt.lower()
    assert "/home/drewp/main-projects" in prompt
    assert "find the Realpage folder" in prompt
    assert "exact paths" in prompt
    assert "Do not edit" in prompt
    assert "Do not ask follow-up or multiple-choice questions" in prompt
    assert "make the best reasonable search" in prompt
    assert "david" in prompt
    assert "1551328742977314886" in prompt


@pytest.fixture
async def repo(tmp_path) -> NotificationRepository:
    repo = NotificationRepository(str(tmp_path / "notifications.db"))
    await repo.init_db()
    return repo


@pytest.mark.asyncio
async def test_project_lookup_endpoint_spawns_worker_in_projects_root(
    repo: NotificationRepository, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import discord

    monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(tmp_path))

    thread = MagicMock()
    thread.id = 444
    thread.name = "🔎 Project lookup"
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=thread)

    channel = MagicMock(spec=discord.TextChannel)
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": cog}
    bot.get_channel.return_value = channel

    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/project-lookup",
            json={
                "text": "search Drew's projects for Pinterest keyword image search",
                "from_agent": "david",
                "from_thread": 111,
            },
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["status"] == "spawned"
        assert body["thread_id"] == "444"
        assert body["working_dir"] == str(tmp_path)

        cog.spawn_session.assert_awaited_once()
        called_channel, prompt = cog.spawn_session.await_args.args
        assert called_channel is channel
        assert "Pinterest keyword image search" in prompt
        assert "david" in prompt
        assert cog.spawn_session.await_args.kwargs["working_dir"] == str(tmp_path)
        thread_name = cog.spawn_session.await_args.kwargs["thread_name"]
        assert thread_name.startswith("🔎 Project lookup")
        assert "Pinterest keyword" in thread_name
        assert cog.spawn_session.await_args.kwargs["backend"] == "claude"
        assert cog.spawn_session.await_args.kwargs["model"] == "haiku"
        assert cog.spawn_session.await_args.kwargs["read_only"] is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_lookup_endpoint_never_echoes_local_paths_or_discord_errors(
    repo: NotificationRepository, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API caller is remote: a missing root or a Discord failure is a fixed string."""
    missing_root = tmp_path / "nowhere"
    monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(missing_root))
    cog = MagicMock()
    cog.spawn_session = AsyncMock()
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": cog}
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("Forbidden: /srv/secret token=abc"))

    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/api/project-lookup", json={"text": "anything"})
        assert resp.status == 503
        body = await resp.json()
        assert str(tmp_path) not in body["error"]
        assert "nowhere" not in body["error"]

        monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(tmp_path))
        resp = await client.post("/api/project-lookup", json={"text": "anything"})
        assert resp.status == 500
        body = await resp.json()
        assert "token=abc" not in body["error"] and "/srv/secret" not in body["error"]

        resp = await client.post("/api/spawn", json={"prompt": "hi"})
        assert resp.status == 500
        body = await resp.json()
        assert "token=abc" not in body["error"] and "/srv/secret" not in body["error"]
        cog.spawn_session.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_lookup_endpoint_returns_worker_result_to_requesting_thread(
    repo: NotificationRepository, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import discord

    monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(tmp_path))

    worker_thread = MagicMock()
    worker_thread.id = 444
    worker_thread.name = "🔎 Project lookup"
    worker_thread.edit = AsyncMock()
    origin_thread = MagicMock()
    origin_thread.id = 111
    origin_thread.send = AsyncMock()
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=worker_thread)

    channel = MagicMock(spec=discord.TextChannel)
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": cog}
    bot.get_channel.side_effect = lambda channel_id: {
        12345: channel,
        111: origin_thread,
    }.get(channel_id)

    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post(
            "/api/project-lookup",
            json={
                "text": "find handoff_triggers.py",
                "from_agent": "david",
                "from_thread": 111,
            },
        )
        assert resp.status == 201
        sink = cog.spawn_session.await_args.kwargs["result_sink"]
        assert sink is not None

        await sink("Found `/home/drewp/main-projects/.../handoff_triggers.py`.", None)

        origin_thread.send.assert_awaited_once()
        sent = origin_thread.send.await_args.args[0]
        assert "DrewAI project lookup result" in sent
        assert "handoff_triggers.py" in sent
        worker_thread.edit.assert_awaited_once_with(
            archived=True,
            reason="project lookup completed",
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_project_lookup_endpoint_rejects_missing_query(repo: NotificationRepository) -> None:
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": MagicMock()}
    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/api/project-lookup", json={})
        assert resp.status == 400
        body = await resp.json()
        assert "text" in body["error"]
    finally:
        await client.close()


def test_project_lookup_model_defaults_to_cheap_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_discord.project_lookup_worker import project_lookup_harness

    monkeypatch.delenv("CCDB_PROJECT_LOOKUP_MODEL", raising=False)
    assert project_lookup_harness() == ("claude", "haiku")
    monkeypatch.setenv("CCDB_PROJECT_LOOKUP_MODEL", "sonnet")
    assert project_lookup_harness() == ("claude", "sonnet")


@pytest.mark.asyncio
async def test_project_lookup_endpoint_resolves_root_like_the_discord_path(
    repo: NotificationRepository, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured lookup root wins over the runner's working directory (review fix b)."""
    import discord

    from claude_discord.handoff_projects import lookup_root

    configured = tmp_path / "configured"
    configured.mkdir()
    runner_dir = tmp_path / "runner"
    runner_dir.mkdir()
    monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(configured))

    thread = MagicMock()
    thread.id = 444
    thread.name = "🔎 Project lookup"
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=thread)
    channel = MagicMock(spec=discord.TextChannel)
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": cog}
    bot.get_channel.return_value = channel

    api = ApiServer(repo=repo, bot=bot, default_channel_id=12345, working_dir=str(runner_dir))
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.post("/api/project-lookup", json={"text": "find it"})
        assert resp.status == 201
        body = await resp.json()
        assert body["working_dir"] == str(configured)
        assert body["working_dir"] == lookup_root(fallback=str(runner_dir))
        assert cog.spawn_session.await_args.kwargs["working_dir"] == str(configured)
    finally:
        await client.close()
