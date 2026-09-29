"""Worker completion and tag allocation must agree on session availability."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.cogs.task_loop import TaskLoopCog
from claude_discord.database.models import init_db
from claude_discord.database.repository import SessionRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.voice_tags import VoiceTagger


@pytest.fixture
async def stores(tmp_path):
    path = str(tmp_path / "sessions.db")
    await init_db(path)
    return SessionRepository(path), SettingsRepository(path)


def thread_at(thread_id=123):
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.name = "worker"
    thread.archived = False
    thread.locked = False
    thread.send = AsyncMock()
    thread.edit = AsyncMock()
    return thread


async def test_worker_create_event_cannot_take_a_tag_before_registration(stores):
    repo, settings = stores
    bot = MagicMock(settings_repo=settings)
    cog = ClaudeChatCog(bot=bot, repo=repo, runner=MagicMock(), settings_repo=settings)
    thread = thread_at()
    events = []

    async def create(**kwargs):
        events.append(asyncio.create_task(cog.on_thread_create(thread)))
        await asyncio.sleep(0)  # Discord event can precede the REST response
        return thread

    channel = MagicMock(create_thread=AsyncMock(side_effect=create))
    await cog.spawn_session(channel, "work", auto_start=False, voice_addressable=False)
    await asyncio.gather(*events)
    assert await settings.get("voice_label:123") is None
    assert await VoiceTagger(bot, settings).tag_thread(thread) is None
    views = [{"thread_id": 123, "closed": False}]
    await VoiceTagger(bot, settings).apply(views)
    assert views[0]["voice_label"] is None
    thread.edit.assert_not_awaited()


async def test_excluded_worker_releases_old_tag_even_outside_page(stores):
    _, settings = stores
    await settings.set("voice_addressable:123", "false")
    await settings.set("voice_label:123", "zoro")
    await settings.set("voice_label:124", "nami")
    views = [{"thread_id": 124, "closed": False}]
    await VoiceTagger(MagicMock(), settings).apply(views)
    assert await settings.get("voice_label:123") is None
    assert views[0]["voice_label"] == "nami"


@pytest.mark.parametrize("manifest", [False, True])
async def test_archive_closes_worker_record_and_releases_tag(stores, manifest):
    repo, settings = stores
    await repo.save(123, "abc-def", working_dir="/project")
    await settings.set("voice_label:123", "zoro")
    thread = thread_at()
    chat = SimpleNamespace(repo=repo, _settings_repo=settings, _active_runners={})
    bot = MagicMock(cogs={"ClaudeChatCog": chat})
    bot.get_channel.return_value = thread
    cog = TaskLoopCog(bot)
    running = SimpleNamespace(thread=thread, worker_thread_id=999)
    state = MagicMock()
    state.unarchived_threads.return_value = [("task", 123)]
    cog._build_state = MagicMock(return_value=state)
    if manifest:
        await cog._archive_worker_thread(running, "task")
        state.mark_archived.assert_called_once_with("task", thread_id=123)
    else:
        await cog._close_worker_thread(thread)
    record = await repo.get(123)
    assert record.is_closed
    assert record.session_id == "abc-def"  # history survives closure
    assert await settings.get("voice_label:123") is None
    assert thread.edit.await_args.kwargs["archived"] is True


async def test_failed_worker_close_does_not_claim_archive_complete(stores):
    repo, settings = stores
    await repo.save(123, "abc-def")
    repo.mark_closed = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    thread = thread_at()
    chat = SimpleNamespace(repo=repo, _settings_repo=settings, _active_runners={})
    bot = MagicMock(cogs={"ClaudeChatCog": chat})
    bot.get_channel.return_value = thread
    cog = TaskLoopCog(bot)
    state = MagicMock()
    state.unarchived_threads.return_value = [("task", 123)]
    cog._build_state = MagicMock(return_value=state)
    await cog._archive_worker_thread(SimpleNamespace(worker_thread_id=999), "task")
    state.mark_archived.assert_not_called()
    thread.edit.assert_not_awaited()
