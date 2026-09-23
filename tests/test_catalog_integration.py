"""One identity, one answer (OpenSpec shared-project-catalog task 5.1, offline stand-in for 5.2).

New session (the launcher), the control plane (what Claude, Codex and DSH
call) and the service itself resolve the same catalog identity to the same
canonical path, and an owner-qualified remote request becomes a handoff
packet that carries no local path at all.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.handoffs import protocol as p
from claude_discord.catalog_config import CatalogConfig
from claude_discord.catalog_handoff import build_catalog_lookup_task
from claude_discord.catalog_service import ProjectCatalogService
from claude_discord.cogs.project_launcher import ProjectLauncherCog
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.project_catalog_repo import ProjectCatalogRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.project_catalog import ProjectIdentity, ResolutionKind


@pytest.fixture
async def world(tmp_path: Path):
    main = tmp_path / "main"
    work = tmp_path / "work"
    for name in ("alpha", "beta"):
        (main / name).mkdir(parents=True)
    (work / "alpha").mkdir(parents=True)
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    config = CatalogConfig.from_env(
        {
            "CCDB_PROJECT_ROOTS": f"{main},{work}",
            "CCDB_CATALOG_OWNER": "david",
            "CCDB_CATALOG_COMPUTER": "davidpc",
            "CCDB_CATALOG_COMPUTERS": "drew/drewai",
        }
    )
    catalog = ProjectCatalogService(
        config, ProjectCatalogRepository(db), settings=SettingsRepository(db), cache_ttl=0
    )
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999, display_name="David's computer")
    bot.cogs = {}
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    bot.guilds = []
    chat = SimpleNamespace(
        _allowed_user_ids={42},
        _ensure_thread_members=AsyncMock(),
        spawn_session=AsyncMock(),
        _run_claude=AsyncMock(),
    )
    repo = SimpleNamespace(
        save=AsyncMock(), list_all=AsyncMock(return_value=[]), get=AsyncMock(return_value=None)
    )
    launcher = ProjectLauncherCog(
        bot,
        repo,
        SettingsRepository(db),
        chat,
        channel_id=100,
        channel_ids={100},
        working_dir=str(tmp_path),
        catalog=catalog,
    )
    notif = NotificationRepository(db)
    await notif.init_db()
    api = ApiServer(repo=notif, bot=bot, default_channel_id=1, host="127.0.0.1", port=0)
    api.project_catalog = catalog
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        yield SimpleNamespace(catalog=catalog, launcher=launcher, client=client, root=tmp_path)
    finally:
        await client.close()


def _interaction() -> MagicMock:
    item = MagicMock(spec=discord.Interaction)
    item.user = MagicMock(spec=discord.Member)
    item.user.id = 42
    item.guild_id = 10
    item.channel_id = 100
    item.channel = MagicMock(spec=discord.TextChannel)
    item.channel.id = 100
    item.response = MagicMock()
    item.response.defer = AsyncMock()
    item.response.send_message = AsyncMock()
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    item.edit_original_response = AsyncMock()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 333
    thread.mention = "<#333>"
    thread.add_user = AsyncMock()
    thread.send = AsyncMock()
    item.channel.create_thread = AsyncMock(return_value=thread)
    return item


async def test_new_session_and_every_harness_bind_the_same_canonical_path(world) -> None:
    key = ProjectIdentity("david", "davidpc", "work", "alpha").key
    expected = str(world.root / "work" / "alpha")

    # The launcher (New session → Projects → pick).
    event = _interaction()
    await world.launcher.start_catalog_session(event, key)
    world.launcher.repo.save.assert_awaited_once_with(333, "", working_dir=expected)
    world.launcher.chat.spawn_session.assert_not_awaited()

    # The control plane, which is what Claude, Codex and DSH call.
    found = await (await world.client.get(f"/api/projects/{key}")).json()
    assert found["working_directory"] == expected
    resolved = await (
        await world.client.post("/api/projects/resolve", json={"text": "David's alpha"})
    ).json()
    assert resolved["kind"] == "ambiguous_owner"  # two local alphas: ask which, never guess
    resolved = await (
        await world.client.post("/api/projects/resolve", json={"text": "beta"})
    ).json()
    assert resolved["kind"] == "local_available"
    assert resolved["path"] == str(world.root / "main" / "beta")

    # The service the two above share.
    project = await world.catalog.find(key)
    assert project is not None and str(project.working_directory) == expected


async def test_an_owner_qualified_remote_request_enters_handoff_without_a_local_path(
    world,
) -> None:
    result = await world.catalog.resolve("Drew's alpha")
    assert result.kind is ResolutionKind.REMOTE_TARGET
    assert result.working_directory is None
    response = await world.client.post("/api/projects/resolve", json={"text": "Drew's alpha"})
    listed = await response.json()
    assert listed["kind"] == "remote_target" and "path" not in listed
    event = build_catalog_lookup_task(
        result,
        origin=p.ConversationCoordinate(guild_id=10, channel_id=100, thread_id=333),
        origin_human_id="42",
        sender_agent_id="davidpc",
    )
    assert event.task is not None
    assert event.recipient == "drewai"
    assert event.task.project.owner == "drew"
    assert str(world.root) not in p.encode_event(event)
    # The local same-named folder was never chosen for a remote owner.
    assert "work" not in event.task.project.folder
