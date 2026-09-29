"""EBI follows Discord's archive state both ways, and the owner's threads get words first.

The repository is real SQLite and the lifecycle is the real service over the
real Discord surface; only the Discord objects are mocks. Every Discord write
(``send``, ``edit``) and every Discord read that costs a call (``fetch_channel``)
is a mock the tests can count, because "a clean pass makes zero Discord calls"
is the property that matters when this runs every five minutes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord
import pytest
from discord.raw_models import RawThreadDeleteEvent, RawThreadUpdateEvent

from claude_code_core.session_repo import CloseAuthority, SessionRepository
from claude_discord.cogs.thread_follow import ThreadFollowCog
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.lifecycle_adapters import build_lifecycle_service
from claude_discord.voice_labels import SPOKEN_LABELS, label_key
from claude_discord.voice_tags import VoiceTagger

FIRST = SPOKEN_LABELS[0]


@dataclass
class World:
    repo: SessionRepository
    settings: SettingsRepository
    bot: MagicMock
    chat: Any
    cog: ThreadFollowCog


def thread_at(
    thread_id: int, name: str = "📂 repo", *, archived: bool = False, locked: bool = False
) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id, thread.name = thread_id, name
    thread.archived, thread.locked = archived, locked
    thread.send = AsyncMock()

    async def edit(**changes: Any) -> None:
        for key, value in changes.items():
            setattr(thread, key, value)

    thread.edit = AsyncMock(side_effect=edit)
    return thread


def not_found() -> discord.NotFound:
    return discord.NotFound(MagicMock(status=404), "Unknown Channel")


def update(thread_id: int, *, archived: bool, locked: bool = False, name: str = "📂 repo"):
    return RawThreadUpdateEvent(
        {
            "id": str(thread_id),
            "type": 11,
            "guild_id": "1",
            "parent_id": "2",
            "name": name,
            "thread_metadata": {
                "archived": archived,
                "locked": locked,
                "auto_archive_duration": 10080,
                "archive_timestamp": "2026-09-29T00:00:00+00:00",
            },
        }  # type: ignore[arg-type]
    )


def deleted(thread_id: int) -> RawThreadDeleteEvent:
    return RawThreadDeleteEvent(
        {"id": str(thread_id), "type": 11, "guild_id": "1", "parent_id": "2"}  # type: ignore[arg-type]
    )


def make_world(tmp_path: Path, chat: Any, repo: SessionRepository) -> World:
    settings = SettingsRepository(repo.db_path)
    bot = MagicMock()
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=AssertionError("no fetch expected"))
    lifecycle = build_lifecycle_service(bot, chat, repo)
    cog = ThreadFollowCog(bot, repo=repo, settings_repo=settings, lifecycle=lifecycle, chat=chat)
    return World(repo, settings, bot, chat, cog)


@pytest.fixture
async def repo(tmp_path: Path) -> SessionRepository:
    path = str(tmp_path / "follow.db")
    await init_db(path)
    return SessionRepository(path)


@pytest.fixture
async def world(tmp_path: Path, repo: SessionRepository) -> World:
    chat = SimpleNamespace(_active_runners={}, stop_thread_run=AsyncMock())
    return make_world(tmp_path, chat, repo)


def forbid_writes(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    for name in ("save", "request_close", "mark_closed", "reopen", "mark_archived", "delete"):
        monkeypatch.setattr(world.repo, name, AsyncMock(side_effect=AssertionError(name)))
    for name in ("set", "delete"):
        monkeypatch.setattr(world.settings, name, AsyncMock(side_effect=AssertionError(name)))


# -- archived in Discord ------------------------------------------------------


async def test_archive_in_discord_closes_the_open_row_and_frees_its_word(world: World) -> None:
    await world.repo.save(1, "native-1")
    await world.settings.set(label_key(1), FIRST)
    payload = update(1, archived=True)
    payload.thread = thread_at(1, f"[{FIRST}] 📂 repo")

    await world.cog.on_raw_thread_update(payload)

    record = await world.repo.get(1)
    assert record is not None and record.is_closed
    assert record.close_authority == CloseAuthority.DISCORD_ARCHIVED.value
    assert not record.archive_pending
    assert record.session_id == "native-1"
    assert await world.settings.get(label_key(1)) is None
    world.chat.stop_thread_run.assert_awaited_once_with(1)
    payload.thread.send.assert_not_awaited()
    payload.thread.edit.assert_not_awaited()
    world.bot.fetch_channel.assert_not_awaited()


async def test_archive_without_a_stop_hook_still_closes(
    tmp_path: Path, repo: SessionRepository
) -> None:
    world = make_world(tmp_path, SimpleNamespace(_active_runners={}), repo)
    await world.repo.save(1, "native-1")

    await world.cog.on_raw_thread_update(update(1, archived=True))

    record = await world.repo.get(1)
    assert record is not None and record.is_closed


async def test_the_same_payload_twice_and_a_rename_make_no_writes(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await world.repo.save(1, "native-1")
    await world.repo.save(2, "native-2")
    await world.cog.on_raw_thread_update(update(1, archived=True))
    before = await world.repo.get(1)
    forbid_writes(monkeypatch, world)

    await world.cog.on_raw_thread_update(update(1, archived=True))
    await world.cog.on_raw_thread_update(update(2, archived=False, name="renamed"))

    after = await world.repo.get(1)
    assert before is not None and after is not None
    assert after.lifecycle_version == before.lifecycle_version
    assert world.chat.stop_thread_run.await_count == 1


# -- unarchived in Discord ----------------------------------------------------


async def test_unarchive_reopens_a_closed_row_unlocks_it_and_tags_the_title(
    world: World,
) -> None:
    await world.repo.save(1, "native-1")
    await world.cog.on_raw_thread_update(update(1, archived=True))
    thread = thread_at(1, "📂 repo", locked=True)
    world.bot.get_channel.side_effect = lambda tid: thread if tid == 1 else None

    await world.cog.on_raw_thread_update(update(1, archived=False, locked=True))

    record = await world.repo.get(1)
    assert record is not None and record.is_open
    assert record.session_id == "native-1"
    assert thread.locked is False
    assert await world.settings.get(label_key(1)) == FIRST
    assert thread.name == f"[{FIRST}] 📂 repo"
    thread.send.assert_not_awaited()


async def test_unarchive_during_a_person_s_close_is_ignored(world: World) -> None:
    await world.repo.save(1, "native-1")
    await world.repo.request_close(1, CloseAuthority.DIRECT_INTERACTION)
    thread = thread_at(1)
    world.bot.get_channel.return_value = thread

    await world.cog.on_raw_thread_update(update(1, archived=False))

    record = await world.repo.get(1)
    assert record is not None and record.close_pending
    thread.edit.assert_not_awaited()
    assert await world.settings.get(label_key(1)) is None


# -- deleted in Discord -------------------------------------------------------


async def test_raw_delete_closes_the_row_without_a_fetch(world: World) -> None:
    await world.repo.save(1, "native-1")
    await world.settings.set(label_key(1), FIRST)

    await world.cog.on_raw_thread_delete(deleted(1))

    record = await world.repo.get(1)
    assert record is not None and record.is_closed
    assert not record.archive_pending
    assert record.close_authority == CloseAuthority.DISCORD_ARCHIVED.value
    assert await world.settings.get(label_key(1)) is None
    world.chat.stop_thread_run.assert_awaited_once_with(1)
    world.bot.fetch_channel.assert_not_awaited()


# -- the sweep ----------------------------------------------------------------


async def test_sweep_closes_every_archived_or_missing_row_and_reuses_the_word(
    world: World,
) -> None:
    for tid in range(1, 25):
        await world.repo.save(tid, f"native-{tid}")
    for tid, label in zip(range(1, 10), SPOKEN_LABELS[:9], strict=True):
        await world.settings.set(label_key(tid), label)
    await world.settings.set(label_key(23), SPOKEN_LABELS[9])
    archived = {tid: thread_at(tid, archived=True) for tid in range(1, 20)}
    live = {23: thread_at(23, f"[{SPOKEN_LABELS[9]}] 📂 a"), 24: thread_at(24, "📂 b")}

    async def fetch(tid: int) -> Any:
        if tid in archived:
            return archived[tid]
        raise not_found()

    world.bot.get_channel.side_effect = live.get
    world.bot.fetch_channel = AsyncMock(side_effect=fetch)

    report = await world.cog.sweep()

    assert report["checked"] == 24
    assert report["closed"] == 22
    assert report["errors"] == 0
    assert world.bot.fetch_channel.await_count == 22
    for tid in range(1, 23):
        record = await world.repo.get(tid)
        assert record is not None and record.is_closed, tid
        assert record.close_authority == CloseAuthority.DISCORD_ARCHIVED.value
        assert not record.archive_pending
    for thread in archived.values():
        thread.send.assert_not_awaited()
        thread.edit.assert_not_awaited()
    live[23].edit.assert_not_awaited()
    assert await world.settings.get(label_key(24)) == FIRST
    live[24].edit.assert_awaited_once_with(name=f"[{FIRST}] 📂 b")
    assert report["retagged"] == 1

    world.bot.fetch_channel.reset_mock()
    live[24].edit.reset_mock()
    again = await world.cog.sweep()

    assert again["closed"] == 0 and again["retagged"] == 0
    world.bot.fetch_channel.assert_not_awaited()
    for thread in live.values():
        thread.edit.assert_not_awaited()
        thread.send.assert_not_awaited()


async def test_sweep_ignores_rows_that_are_not_discord_threads(world: World) -> None:
    await world.repo.save(7, "native-7", origin="teams")
    channel = MagicMock(spec=discord.TextChannel)
    await world.repo.save(8, "native-8")
    world.bot.get_channel.side_effect = lambda tid: channel if tid == 8 else None

    report = await world.cog.sweep()

    assert report["closed"] == 0
    for tid in (7, 8):
        record = await world.repo.get(tid)
        assert record is not None and record.is_open
    world.bot.fetch_channel.assert_not_awaited()


async def test_a_refused_fetch_is_an_error_not_a_close(world: World) -> None:
    await world.repo.save(1, "native-1")
    world.bot.fetch_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Access")
    )

    report = await world.cog.sweep()

    assert report["errors"] == 1 and report["closed"] == 0
    record = await world.repo.get(1)
    assert record is not None and record.is_open


# -- who gets a word ----------------------------------------------------------


async def _bump(repo: SessionRepository, thread_id: int) -> None:
    """Make ``thread_id`` the most recently used row, so it is offered a word first."""
    async with aiosqlite.connect(repo.db_path) as db:
        await db.execute(
            "UPDATE sessions SET last_used_at = '2999-01-01 00:00:00' WHERE thread_id = ?",
            (thread_id,),
        )
        await db.commit()


async def _owners_holding(world: World, count: int) -> dict[int, MagicMock]:
    threads = {}
    for tid, label in zip(range(101, 101 + count), SPOKEN_LABELS[:count], strict=True):
        await world.repo.save(tid, f"native-{tid}")
        await world.settings.set(label_key(tid), label)
        threads[tid] = thread_at(tid, f"[{label}] 📂 owner-{tid}")
    return threads


async def test_the_owner_s_thread_gets_the_last_free_word_before_a_helper(
    world: World,
) -> None:
    threads = await _owners_holding(world, len(SPOKEN_LABELS) - 1)
    await world.repo.save(2, "native-2")
    await world.repo.save(1, "native-1")
    await _bump(world.repo, 1)  # the helper is the most recent, and still waits
    threads[1] = thread_at(1, "⚡ build step")
    threads[2] = thread_at(2, "📂 mine")
    world.bot.get_channel.side_effect = threads.get

    await world.cog.sweep()

    last = SPOKEN_LABELS[-1]
    assert await world.settings.get(label_key(2)) == last
    assert await world.settings.get(label_key(1)) is None
    assert threads[2].name == f"[{last}] 📂 mine"
    threads[1].edit.assert_not_awaited()


async def test_an_empty_pool_takes_a_helper_s_word_for_a_new_owner_thread(
    world: World,
) -> None:
    threads = await _owners_holding(world, len(SPOKEN_LABELS) - 1)
    last = SPOKEN_LABELS[-1]
    await world.repo.save(1, "native-1")
    await world.settings.set(label_key(1), last)
    threads[1] = thread_at(1, f"[{last}] 🔎 Project lookup · docs")
    await world.repo.save(2, "native-2")
    threads[2] = thread_at(2, "📂 new")
    world.bot.get_channel.side_effect = threads.get

    report = await world.cog.sweep()

    assert await world.settings.get(label_key(2)) == last
    assert await world.settings.get(label_key(1)) is None
    assert threads[1].name == "🔎 Project lookup · docs"
    assert threads[2].name == f"[{last}] 📂 new"
    assert report["reclaimed"] == 1
    for tid in range(101, 101 + len(SPOKEN_LABELS) - 1):
        threads[tid].edit.assert_not_awaited()


async def test_a_word_never_moves_between_two_owner_threads(world: World) -> None:
    threads = await _owners_holding(world, len(SPOKEN_LABELS))
    await world.repo.save(2, "native-2")
    threads[2] = thread_at(2, "📂 new")
    world.bot.get_channel.side_effect = threads.get
    before = await world.settings.get_all()

    await world.cog.sweep()

    assert await world.settings.get_all() == before
    for thread in threads.values():
        thread.edit.assert_not_awaited()


def test_helper_titles_are_recognised_through_a_tag() -> None:
    assert VoiceTagger.is_helper_title("[zoro] ⚡ step one")
    assert VoiceTagger.is_helper_title("🔁 Task loop · repo")
    assert VoiceTagger.is_helper_title("handoff-1234abcd")
    assert VoiceTagger.is_helper_title("🔎 Project lookup · x")
    assert not VoiceTagger.is_helper_title("[zoro] 📂 repo")
    assert not VoiceTagger.is_helper_title("")


# -- the loop and the wiring --------------------------------------------------


async def test_the_loop_starts_on_load_and_stops_on_unload(world: World) -> None:
    world.bot.wait_until_ready = AsyncMock()
    await world.cog.cog_load()
    assert world.cog.periodic_sweep.is_running()
    await world.cog.cog_unload()
    await asyncio.sleep(0)
    assert not world.cog.periodic_sweep.is_running()


@pytest.mark.parametrize(("value", "added"), [(None, True), ("false", False), ("0", False)])
async def test_setup_bridge_adds_the_cog_unless_opted_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None, added: bool
) -> None:
    from claude_discord.setup import setup_bridge

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("CUSTOM_COGS_DIR", str(tmp_path / "home" / "cogs"))
    if value is None:
        monkeypatch.delenv("CCDB_FOLLOW_DISCORD_THREADS", raising=False)
    else:
        monkeypatch.setenv("CCDB_FOLLOW_DISCORD_THREADS", value)
    bot = MagicMock()
    bot.channel_id = 123
    bot.user = SimpleNamespace(display_name="EbiBot")
    bot.add_cog = AsyncMock()
    bot.cogs = {}
    bot.wait_until_ready = AsyncMock()
    runner = MagicMock()
    runner.model = "sonnet"
    runner.working_dir = str(tmp_path / "proj")
    runner.api_port = None

    await setup_bridge(
        bot,
        runner,
        claude_channel_id=123,
        session_db_path=str(tmp_path / "sessions.db"),
        enable_scheduler=False,
        worktree_base_dir=str(tmp_path / "worktrees"),
    )

    cogs = [c.args[0] for c in bot.add_cog.await_args_list]
    follow = [c for c in cogs if isinstance(c, ThreadFollowCog)]
    assert len(follow) == (1 if added else 0)
    if added:
        assert follow[0].lifecycle is not None
