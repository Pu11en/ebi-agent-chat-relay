"""Tests for auto-joining authorized users to every ccdb thread.

Covers the shared ID helpers, the ClaudeChatCog membership helper/backfill,
and the dashboard's multi-user WAITING_INPUT mention.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.discord_ui.thread_dashboard import ThreadState, ThreadStatusDashboard
from claude_discord.utils.ids import (
    build_allowed_user_ids,
    build_thread_member_ids,
    parse_user_ids,
)

# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


class TestIdHelpers:
    def test_parse_comma_separated(self) -> None:
        assert parse_user_ids("111,222") == {111, 222}

    def test_parse_ignores_blanks_and_non_numeric(self) -> None:
        assert parse_user_ids(" 111 , ,abc,222,") == {111, 222}

    def test_parse_empty_is_empty(self) -> None:
        assert parse_user_ids("") == set()

    def test_build_allowed_unions_owner_and_extras(self) -> None:
        assert build_allowed_user_ids(1, "2,3") == {1, 2, 3}

    def test_build_allowed_nothing_is_none(self) -> None:
        assert build_allowed_user_ids(None, "") is None

    def test_members_default_to_allowed_set(self) -> None:
        assert build_thread_member_ids(1, {1, 2}, "") == {1, 2}

    def test_members_explicit_list_is_used(self) -> None:
        # An explicit member list narrows membership away from the allowlist,
        # so granting execution does not automatically mean thread visibility.
        assert build_thread_member_ids(1, {1, 2, 3}, "2") == {1, 2}

    def test_members_explicit_always_include_owner(self) -> None:
        assert build_thread_member_ids(1, {1, 2}, "2") == {1, 2}

    def test_members_nothing_is_none(self) -> None:
        assert build_thread_member_ids(None, None, "") is None


# ---------------------------------------------------------------------------
# ClaudeChatCog membership
# ---------------------------------------------------------------------------


def _make_thread(thread_id: int = 10, category_id: int | None = None) -> MagicMock:
    thread = MagicMock()
    thread.id = thread_id
    thread.add_user = AsyncMock()
    parent = MagicMock()
    parent.category_id = category_id
    thread.parent = parent
    return thread


class _AsyncIter:
    """Minimal async iterator so archived_threads() can be mocked."""

    def __init__(self, items: list[MagicMock]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> _AsyncIter:
        return self

    async def __anext__(self) -> MagicMock:
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


def _make_cog(
    *,
    member_ids: set[int] | None = None,
    exclude_categories: set[int] | None = None,
) -> ClaudeChatCog:
    bot = MagicMock()
    bot.channel_id = 999
    bot.guilds = []
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    runner = MagicMock()
    runner.clone = MagicMock(return_value=MagicMock())
    return ClaudeChatCog(
        bot=bot,
        repo=repo,
        runner=runner,
        thread_member_ids=member_ids,
        thread_member_exclude_category_ids=exclude_categories,
    )


class TestEnsureThreadMembers:
    @pytest.mark.asyncio
    async def test_adds_every_member(self) -> None:
        cog = _make_cog(member_ids={7, 8})
        thread = _make_thread()

        await cog._ensure_thread_members(thread)

        thread.add_user.assert_any_await(discord.Object(id=7))
        thread.add_user.assert_any_await(discord.Object(id=8))
        assert thread.add_user.await_count == 2

    @pytest.mark.asyncio
    async def test_noop_without_members(self) -> None:
        cog = _make_cog(member_ids=None)
        thread = _make_thread()

        await cog._ensure_thread_members(thread)

        thread.add_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_excluded_category_skips(self) -> None:
        cog = _make_cog(member_ids={7}, exclude_categories={99})
        thread = _make_thread(category_id=99)

        await cog._ensure_thread_members(thread)

        thread.add_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unlisted_category_is_joined(self) -> None:
        cog = _make_cog(member_ids={7}, exclude_categories={99})
        thread = _make_thread(category_id=1)

        await cog._ensure_thread_members(thread)

        thread.add_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_is_suppressed(self) -> None:
        cog = _make_cog(member_ids={7, 8})
        thread = _make_thread()
        thread.add_user.side_effect = discord.HTTPException(MagicMock(), "nope")

        # Best-effort visibility: a failed add must never break the run.
        await cog._ensure_thread_members(thread)

    @pytest.mark.asyncio
    async def test_successful_add_is_not_repeated(self) -> None:
        cog = _make_cog(member_ids={7})
        thread = _make_thread()

        assert await cog._ensure_thread_members(thread) == 1
        assert await cog._ensure_thread_members(thread) == 0

        assert thread.add_user.await_count == 1

    @pytest.mark.asyncio
    async def test_failed_add_is_retried_on_next_call(self) -> None:
        # A transient failure must not be cached: otherwise the member stays
        # out of the thread for the rest of the process, silently.
        cog = _make_cog(member_ids={7})
        thread = _make_thread()
        thread.add_user.side_effect = [
            discord.HTTPException(MagicMock(), "nope"),
            None,
        ]

        assert await cog._ensure_thread_members(thread) == 0
        assert await cog._ensure_thread_members(thread) == 1

        assert thread.add_user.await_count == 2

    @pytest.mark.asyncio
    async def test_partial_failure_retries_only_the_missing_member(self) -> None:
        cog = _make_cog(member_ids={7, 8})
        thread = _make_thread()
        attempts: list[int] = []

        async def add_user(obj: discord.Object) -> None:
            attempts.append(obj.id)
            if obj.id == 8 and attempts.count(8) == 1:
                raise discord.HTTPException(MagicMock(), "nope")

        thread.add_user.side_effect = add_user

        assert await cog._ensure_thread_members(thread) == 1
        assert await cog._ensure_thread_members(thread) == 1

        # 7 succeeded once and was never retried; 8 failed then succeeded.
        assert attempts == [7, 8, 8]


class TestSpawnSessionMembers:
    @pytest.mark.asyncio
    async def test_spawn_adds_configured_members(self) -> None:
        cog = _make_cog(member_ids={7})
        channel = MagicMock(spec=discord.TextChannel)
        thread = _make_thread()
        thread.send = AsyncMock(return_value=MagicMock())
        channel.create_thread = AsyncMock(return_value=thread)

        result = await cog.spawn_session(channel, "hello", auto_start=False)

        assert result is thread
        thread.add_user.assert_awaited_once_with(discord.Object(id=7))


class TestThreadMemberBackfill:
    @pytest.mark.asyncio
    async def test_backfill_walks_guild_and_channel_threads(self) -> None:
        cog = _make_cog(member_ids={7})
        guild_thread = _make_thread(1)
        channel_thread = _make_thread(2)
        guild = MagicMock()
        guild.threads = [guild_thread]
        channel = MagicMock()
        channel.threads = [channel_thread]
        channel.archived_threads = MagicMock(return_value=_AsyncIter([]))
        guild.text_channels = [channel]
        guild.forums = []
        cog.bot.guilds = [guild]

        await cog._backfill_thread_members()

        guild_thread.add_user.assert_awaited_once()
        channel_thread.add_user.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_backfill_reaches_archived_threads(self) -> None:
        # Archived threads are hidden from ``Guild.threads``; without this the
        # member is missing the moment somebody unarchives one.
        cog = _make_cog(member_ids={7})
        archived = _make_thread(3)
        guild = MagicMock()
        guild.threads = []
        channel = MagicMock()
        channel.threads = []
        channel.archived_threads = MagicMock(return_value=_AsyncIter([archived]))
        guild.text_channels = [channel]
        guild.forums = []
        cog.bot.guilds = [guild]

        await cog._backfill_thread_members()

        archived.add_user.assert_awaited_once_with(discord.Object(id=7))

    @pytest.mark.asyncio
    async def test_backfill_survives_unreadable_archived_threads(self) -> None:
        cog = _make_cog(member_ids={7})
        guild = MagicMock()
        guild.threads = []
        channel = MagicMock()
        channel.threads = []

        def forbidden(**_: object) -> None:
            raise discord.Forbidden(MagicMock(), "no access")

        channel.archived_threads = forbidden
        guild.text_channels = [channel]
        guild.forums = []
        cog.bot.guilds = [guild]

        # A channel the bot cannot read must not abort the whole backfill.
        await cog._backfill_thread_members()

    @pytest.mark.asyncio
    async def test_on_ready_backfills_only_once(self) -> None:
        cog = _make_cog(member_ids={7})
        # Isolate the backfill trigger from the unrelated resume path.
        cog._resume_repo = None
        await cog.on_ready()
        await cog.on_ready()

        assert cog._thread_members_backfilled is True


# ---------------------------------------------------------------------------
# Dashboard multi-user mention
# ---------------------------------------------------------------------------


class TestDashboardMultiUserMention:
    @pytest.mark.asyncio
    async def test_mentions_owner_and_extra_members(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)
        dashboard = ThreadStatusDashboard(
            channel=channel,
            owner_id=42,
            mention_user_ids={42, 43},
        )
        await dashboard.initialize()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 10
        thread.send = AsyncMock()

        await dashboard.set_state(10, ThreadState.PROCESSING, "working", thread=thread)
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "working", thread=thread)

        thread.send.assert_awaited_once()
        text = thread.send.call_args.args[0]
        assert "<@42>" in text
        assert "<@43>" in text

    @pytest.mark.asyncio
    async def test_owner_only_keeps_legacy_text(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)
        dashboard = ThreadStatusDashboard(channel=channel, owner_id=42)
        await dashboard.initialize()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 10
        thread.send = AsyncMock()

        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "working", thread=thread)

        assert (
            thread.send.call_args.args[0]
            == "🟡 <@42> The agent has finished — your reply is needed here."
        )

    @pytest.mark.asyncio
    async def test_muted_user_is_not_mentioned(self) -> None:
        """A muted member keeps thread access but is skipped by the ping."""
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)
        dashboard = ThreadStatusDashboard(
            channel=channel,
            owner_id=42,
            mention_user_ids={42, 43},
            muted_user_ids={43},
        )
        await dashboard.initialize()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 10
        thread.send = AsyncMock()

        await dashboard.set_state(10, ThreadState.PROCESSING, "working", thread=thread)
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "working", thread=thread)

        text = thread.send.call_args.args[0]
        assert "<@42>" in text
        assert "<@43>" not in text

    @pytest.mark.asyncio
    async def test_muted_owner_is_not_mentioned(self) -> None:
        """An explicit mute beats the "owner is always pinged" default."""
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)
        dashboard = ThreadStatusDashboard(
            channel=channel,
            owner_id=42,
            mention_user_ids={42, 43},
            muted_user_ids={42},
        )
        await dashboard.initialize()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 10
        thread.send = AsyncMock()

        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "working", thread=thread)

        text = thread.send.call_args.args[0]
        assert "<@42>" not in text
        assert "<@43>" in text

    @pytest.mark.asyncio
    async def test_no_ping_when_everyone_is_muted(self) -> None:
        """With nobody left to notify, no message is posted at all."""
        channel = MagicMock(spec=discord.TextChannel)
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=msg)
        dashboard = ThreadStatusDashboard(
            channel=channel,
            owner_id=42,
            mention_user_ids={42, 43},
            muted_user_ids={42, 43},
        )
        await dashboard.initialize()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 10
        thread.send = AsyncMock()

        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "working", thread=thread)

        thread.send.assert_not_called()
