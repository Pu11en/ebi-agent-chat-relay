"""Spoken names must keep their owner across pagination and storage failures."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord
import pytest
from aiohttp.test_utils import make_mocked_request

from claude_code_core.session_repo import CloseAuthority
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.repository import SessionRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.voice_labels import SPOKEN_LABELS, label_key
from claude_discord.voice_tags import VoiceTagger


@dataclass
class Roster:
    sessions: SessionRepository
    settings: SettingsRepository
    bot: MagicMock
    api: ApiServer


@pytest.fixture
async def roster(tmp_path: Path) -> Roster:
    path = str(tmp_path / "roster.db")
    await init_db(path)
    sessions, settings = SessionRepository(path), SettingsRepository(path)
    notifications = NotificationRepository(path)
    await notifications.init_db()
    bot = MagicMock(session_repo=sessions, cogs={})
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(return_value=None)
    api = ApiServer(repo=notifications, bot=bot, session_repo=sessions)
    api.settings_repo = settings
    return Roster(sessions, settings, bot, api)


def thread_at(thread_id: int, name: str = "conversation") -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id, thread.name = thread_id, name
    thread.archived, thread.locked = False, False
    thread.edit = AsyncMock()
    return thread


async def fill_pool(roster: Roster) -> dict[str, str]:
    for thread_id, label in enumerate(SPOKEN_LABELS, 1):
        await roster.sessions.save(thread_id, f"native-{thread_id}")
        await roster.settings.set(label_key(thread_id), label)
    return await roster.settings.get_all()


async def close(roster: Roster, thread_id: int) -> None:
    await roster.sessions.mark_closed(thread_id, "Done", CloseAuthority.DIRECT_INTERACTION)


@pytest.mark.parametrize("closed_holder", [None, len(SPOKEN_LABELS)])
async def test_paginated_api_never_reclaims_an_open_holder(
    roster: Roster, closed_holder: int | None
) -> None:
    before = await fill_pool(roster)
    if closed_holder is not None:
        await close(roster, closed_holder)
    async with aiosqlite.connect(roster.sessions.db_path) as db:
        await db.execute("UPDATE sessions SET last_used_at = '2000-01-01 00:00:00'")
        await db.commit()
    await roster.sessions.save(999, "new-native")

    response = await roster.api.list_sessions(make_mocked_request("GET", "/api/sessions?limit=1"))

    rows = json.loads(response.body)["sessions"]
    assert [row["thread_id"] for row in rows] == [999], "exercise the limited page"
    expected = None if closed_holder is None else SPOKEN_LABELS[-1]
    assert rows[0]["voice_label"] == expected
    after = await roster.settings.get_all()
    for key, label in before.items():
        if closed_holder is not None and key == label_key(closed_holder):
            assert key not in after
        else:
            assert after[key] == label


async def test_closed_off_page_holder_is_released_without_a_new_allocation(roster: Roster) -> None:
    await fill_pool(roster)
    await close(roster, 1)
    await roster.api._apply_voice_labels([{"thread_id": 2, "closed": False}])
    assert await roster.settings.get(label_key(1)) is None
    assert await roster.settings.get(label_key(2)) == SPOKEN_LABELS[1]


async def test_archived_but_open_holder_keeps_its_name(roster: Roster) -> None:
    before = await fill_pool(roster)
    archived = thread_at(1)
    archived.archived = True
    roster.bot.get_channel.side_effect = lambda tid: archived if tid == 1 else None
    await roster.api._apply_voice_labels([{"thread_id": 999, "closed": False}])
    assert await roster.settings.get_all() == before
    assert (await roster.sessions.get(1)).is_open
    archived.edit.assert_not_awaited()


@pytest.mark.parametrize("bulk", [False, True])
async def test_failed_assignment_never_promises_or_displays_a_name(
    roster: Roster, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, bulk: bool
) -> None:
    thread = thread_at(999)
    roster.bot.get_channel.return_value = thread
    original_set = roster.settings.set
    failure = AsyncMock(side_effect=OSError("injected assignment failure"))
    monkeypatch.setattr(roster.settings, "set", failure)
    if bulk:
        views = [{"thread_id": 999, "thread_name": thread.name}]
        await roster.api._apply_voice_labels(views)
        label = views[0].get("voice_label")
    else:
        label = await VoiceTagger(roster.bot, roster.settings).tag_thread(thread)

    failure.assert_awaited_once()
    assert label is None
    assert await roster.settings.get(label_key(999)) is None
    thread.edit.assert_not_awaited()
    assert "injected assignment failure" in caplog.text
    monkeypatch.setattr(roster.settings, "set", original_set)
    label = await VoiceTagger(roster.bot, roster.settings).tag_thread(thread)
    assert label is not None
    assert await roster.settings.get(label_key(999)) == label


@pytest.mark.parametrize("bulk", [False, True])
async def test_failed_release_cannot_create_two_owners(
    roster: Roster, monkeypatch: pytest.MonkeyPatch, bulk: bool
) -> None:
    before = await fill_pool(roster)
    await close(roster, 1)
    original_delete = roster.settings.delete
    failure = AsyncMock(side_effect=OSError("injected release failure"))
    monkeypatch.setattr(roster.settings, "delete", failure)
    if bulk:
        views = [{"thread_id": 1, "closed": True}, {"thread_id": 999, "closed": False}]
        await roster.api._apply_voice_labels(views)
        label = views[-1]["voice_label"]
    else:
        label = await VoiceTagger(roster.bot, roster.settings).tag_thread(thread_at(999))
    failure.assert_awaited_once()
    assert label is None
    assert await roster.settings.get_all() == before
    monkeypatch.setattr(roster.settings, "delete", original_delete)
    label = await VoiceTagger(roster.bot, roster.settings).tag_thread(thread_at(999))
    assert label == SPOKEN_LABELS[0]
    after = await roster.settings.get_all()
    assert label_key(1) not in after
    assert len(set(after.values())) == len(SPOKEN_LABELS)


async def test_reopened_holder_is_not_released_by_a_stale_closed_view(roster: Roster) -> None:
    before = await fill_pool(roster)
    await close(roster, 1)
    await roster.sessions.reopen(1)
    views = [{"thread_id": 1, "closed": True}, {"thread_id": 999}]
    await roster.api._apply_voice_labels(views)
    assert views[0]["voice_label"] == SPOKEN_LABELS[0]
    assert views[-1]["voice_label"] is None
    assert await roster.settings.get_all() == before


async def test_unknown_holder_is_not_reclaimed_even_without_a_session_row(roster: Roster) -> None:
    before = await fill_pool(roster)
    # A missing row is not an explicit closure and must not give authority to steal.
    await roster.sessions.delete(1)
    await roster.api._apply_voice_labels([{"thread_id": 999}])
    assert await roster.settings.get_all() == before


async def test_failed_snapshot_does_not_erase_existing_title(
    roster: Roster, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = thread_at(1, f"[{SPOKEN_LABELS[0]}] conversation")
    roster.bot.get_channel.return_value = thread
    monkeypatch.setattr(roster.settings, "get_all", AsyncMock(side_effect=OSError("unavailable")))
    await roster.api._apply_voice_labels([{"thread_id": 1, "thread_name": thread.name}])
    thread.edit.assert_not_awaited()


async def test_unavailable_lifecycle_evidence_never_reclaims_a_holder(
    roster: Roster, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = await fill_pool(roster)
    monkeypatch.setattr(roster.sessions, "get", AsyncMock(side_effect=OSError("unavailable")))
    views = [{"thread_id": 999}]
    await roster.api._apply_voice_labels(views)
    assert views[0].get("voice_label") is None
    assert await roster.settings.get_all() == before


async def test_new_creation_reuses_only_a_confirmed_closed_holders_tag(roster: Roster) -> None:
    await fill_pool(roster)
    await close(roster, len(SPOKEN_LABELS))
    label = await VoiceTagger(roster.bot, roster.settings).tag_thread(thread_at(999))
    assert label == SPOKEN_LABELS[-1]
    assert await roster.settings.get(label_key(len(SPOKEN_LABELS))) is None
    assert await roster.settings.get(label_key(999)) == label


async def test_closed_target_cannot_regain_a_tag_from_a_creation_event(roster: Roster) -> None:
    await roster.sessions.save(999, "native")
    await close(roster, 999)
    thread = thread_at(999)
    assert await VoiceTagger(roster.bot, roster.settings).tag_thread(thread) is None
    assert await roster.settings.get(label_key(999)) is None
    thread.edit.assert_not_awaited()


async def test_concurrent_list_and_creation_preserve_all_owners(roster: Roster) -> None:
    before = await fill_pool(roster)
    views = [{"thread_id": 999}]
    _bulk, label = await asyncio.gather(
        roster.api._apply_voice_labels(views),
        VoiceTagger(roster.bot, roster.settings).tag_thread(thread_at(1000)),
    )
    assert views[0]["voice_label"] is None
    assert label is None
    assert await roster.settings.get_all() == before


async def test_worker_is_excluded_before_list_and_gateway_event_can_assign(roster: Roster) -> None:
    await roster.sessions.save(1, "user-native")
    await roster.settings.set(label_key(1), SPOKEN_LABELS[0])
    thread = thread_at(999)
    thread.send = AsyncMock()
    chat = ClaudeChatCog(
        bot=roster.bot, repo=roster.sessions, runner=MagicMock(), settings_repo=roster.settings
    )
    pending: list[asyncio.Task] = []
    views = [{"thread_id": 999, "closed": False}]

    async def create(**kwargs: object) -> MagicMock:
        pending.append(asyncio.create_task(chat.on_thread_create(thread)))
        pending.append(asyncio.create_task(roster.api._apply_voice_labels(views)))
        await asyncio.sleep(0)
        return thread

    channel = MagicMock(create_thread=AsyncMock(side_effect=create))
    await chat.spawn_session(channel, "internal work", auto_start=False, voice_addressable=False)
    await asyncio.gather(*pending)

    assert await roster.settings.get("voice_addressable:999") == "false"
    assert await roster.settings.get(label_key(999)) is None
    assert await roster.settings.get(label_key(1)) == SPOKEN_LABELS[0]
    assert views[0]["voice_label"] is None
    # A new tagger sees the durable exclusion, even with unused words available.
    assert await VoiceTagger(roster.bot, roster.settings).tag_thread(thread) is None
    thread.edit.assert_not_awaited()
