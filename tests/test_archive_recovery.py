"""Cross-surface close effects survive restart without overwriting newer intent."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.session_repo import CloseAuthority, SessionRepository
from claude_discord.database.models import init_db
from claude_discord.lifecycle_adapters import DiscordThreadSurface
from claude_discord.session_lifecycle import (
    CloseAuthorization,
    CloseState,
    SessionLifecycleService,
)

HUMAN = CloseAuthorization.from_interaction("owner")


@pytest.fixture
async def repo(tmp_path: Path) -> SessionRepository:
    path = str(tmp_path / "sessions.db")
    await init_db(path)
    result = SessionRepository(path)
    await result.save(1, "native-id", working_dir="/project")
    return result


@pytest.mark.parametrize("failure", [False, OSError("injected archive outage")])
async def test_failed_archive_retries_after_service_and_repository_reconstruction(
    repo: SessionRepository, failure: bool | OSError
) -> None:
    surface = AsyncMock()
    surface.archive.side_effect = [failure]
    first = SessionLifecycleService(repo, surface=surface)
    with contextlib.suppress(OSError):
        await first.close(1, HUMAN)
    before = await repo.get(1)
    assert before is not None and before.is_closed
    assert before.archive_pending
    surface.archive.assert_awaited_once_with(1)

    recovered_surface, writer = AsyncMock(), AsyncMock()
    recovered_surface.archive.return_value = True
    second = SessionLifecycleService(
        SessionRepository(repo.db_path), surface=recovered_surface, wrap_up_writer=writer
    )
    await second.reconcile_pending_closes()
    recovered_surface.archive.assert_awaited_once_with(1)
    writer.summarize.assert_not_awaited()
    after = await repo.get(1)
    assert after is not None and after.is_closed
    assert not after.archive_pending
    assert (after.session_id, after.wrap_up, after.closed_at) == (
        before.session_id,
        before.wrap_up,
        before.closed_at,
    )
    await second.reconcile_pending_closes()
    recovered_surface.archive.assert_awaited_once()


async def test_reopen_cancels_failed_archive_without_reclosing(repo: SessionRepository) -> None:
    surface = AsyncMock()
    surface.archive.return_value = False
    await SessionLifecycleService(repo, surface=surface).close(1, HUMAN)
    await SessionLifecycleService(repo, surface=surface).reopen(1)
    recovered_surface = AsyncMock()
    second = SessionLifecycleService(SessionRepository(repo.db_path), surface=recovered_surface)
    await second.reconcile_pending_closes()
    assert (await repo.get(1)).is_open
    recovered_surface.archive.assert_not_awaited()


async def test_reinitialization_preserves_pending_work_and_stale_ack_cannot_clear_new_close(
    repo: SessionRepository,
) -> None:
    surface = AsyncMock()
    surface.archive.return_value = False
    service = SessionLifecycleService(repo, surface=surface)
    await service.close(1, HUMAN)
    first = await repo.get(1)
    await init_db(repo.db_path)
    pending = await repo.list_pending_archives()
    assert len(pending) == 1 and pending[0].lifecycle_version == first.lifecycle_version

    await service.reopen(1)
    await service.close(1, HUMAN)
    await repo.mark_archived(1, expected_version=first.lifecycle_version)
    current = await repo.get(1)
    assert current.archive_pending
    assert current.lifecycle_version > first.lifecycle_version


async def test_close_without_a_surface_does_not_schedule_an_external_archive(
    repo: SessionRepository,
) -> None:
    await SessionLifecycleService(repo).close(1, HUMAN)
    surface = AsyncMock()
    await SessionLifecycleService(repo, surface=surface).reconcile_pending_closes()
    surface.archive.assert_not_awaited()


async def test_a_deleted_thread_settles_its_pending_archive_in_one_pass(
    repo: SessionRepository,
) -> None:
    """Nothing is left to archive, so the close is acknowledged instead of retried for ever."""
    await repo.mark_closed(1, "closed while Discord was down", HUMAN.source, archive_pending=True)
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    service = SessionLifecycleService(repo, surface=DiscordThreadSurface(bot, repo))

    outcomes = await service.reconcile_pending_closes()

    assert [(o.state, o.archived) for o in outcomes] == [(CloseState.ALREADY_CLOSED, True)]
    after = await repo.get(1)
    assert after is not None and after.is_closed
    assert not after.archive_pending
    await service.reconcile_pending_closes()
    bot.fetch_channel.assert_awaited_once()


async def test_a_thread_the_bot_may_not_read_keeps_its_archive_pending(
    repo: SessionRepository,
) -> None:
    await repo.mark_closed(1, "closed while Discord was down", HUMAN.source, archive_pending=True)
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    service = SessionLifecycleService(repo, surface=DiscordThreadSurface(bot, repo))

    await service.reconcile_pending_closes()

    after = await repo.get(1)
    assert after is not None and after.is_closed
    assert after.archive_pending
    await service.reconcile_pending_closes()
    assert bot.fetch_channel.await_count == 2


async def test_a_discord_close_interrupted_by_a_restart_finishes_without_discord(
    repo: SessionRepository,
) -> None:
    """The thread was archived out from under a run and the bot died before wrapping up."""
    await repo.request_close(1, CloseAuthority.DISCORD_ARCHIVED)
    bot, writer = MagicMock(), AsyncMock()
    bot.fetch_channel = AsyncMock()
    service = SessionLifecycleService(
        repo, surface=DiscordThreadSurface(bot, repo), wrap_up_writer=writer
    )

    outcomes = await service.reconcile_pending_closes()

    assert [o.state for o in outcomes] == [CloseState.CLOSED]
    after = await repo.get(1)
    assert after is not None and after.is_closed
    assert not after.archive_pending
    assert after.close_authority == CloseAuthority.DISCORD_ARCHIVED.value
    assert after.wrap_up and after.session_id == "native-id"
    writer.summarize.assert_not_awaited()
    bot.fetch_channel.assert_not_awaited()
    bot.get_channel.assert_not_called()


@pytest.mark.parametrize("new_close", [False, True])
async def test_old_wrapup_cannot_close_a_reopened_or_newly_closing_session(
    repo: SessionRepository, new_close: bool
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def summary(record: object) -> str:
        entered.set()
        await release.wait()
        return "old close summary"

    writer, surface = AsyncMock(), AsyncMock()
    writer.summarize.side_effect = summary
    service = SessionLifecycleService(repo, surface=surface, wrap_up_writer=writer)
    closing = asyncio.create_task(service.close(1, HUMAN))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await repo.reopen(1)
        if new_close:
            await repo.request_close(1, CloseAuthority.USER_INSTRUCTION)
        release.set()
        await closing
        record = await repo.get(1)
        assert record is not None
        assert record.close_pending if new_close else record.is_open
        assert record.wrap_up != "old close summary"
        surface.archive.assert_not_awaited()
    finally:
        release.set()
        await closing


async def test_reopen_waits_for_inflight_archive_across_service_instances(
    repo: SessionRepository,
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    effects: list[str] = []

    async def archive(thread_id: int) -> bool:
        entered.set()
        await release.wait()
        effects.append("archive")
        return True

    async def unarchive(thread_id: int) -> bool:
        effects.append("unarchive")
        return True

    surface = AsyncMock()
    surface.archive.side_effect = archive
    surface.unarchive.side_effect = unarchive
    first = SessionLifecycleService(repo, surface=surface)
    second = SessionLifecycleService(SessionRepository(repo.db_path), surface=surface)
    closing = asyncio.create_task(first.close(1, HUMAN))
    opening = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        opening = asyncio.create_task(second.reopen(1))
        # Give the independent service a chance to reach the surface boundary.
        for _ in range(20):
            await asyncio.sleep(0.005)
        release.set()
        await asyncio.gather(closing, opening)
        assert effects == ["archive", "unarchive"]
        assert (await repo.get(1)).is_open
    finally:
        release.set()
        await asyncio.gather(closing, *([opening] if opening else []))
