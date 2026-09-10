"""Tests for WorkdirCommandCog (/cd, /cdnew)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from workdir_command import WorkdirCommandCog

CHANNEL_ID = 1000
THREAD_ID = 2000
USER_ID = 42


def _interaction(channel: MagicMock, user_id: int = USER_ID) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.channel = channel
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _thread(thread_id: int = THREAD_ID, parent_id: int = CHANNEL_ID) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.parent_id = parent_id
    return thread


def _text_channel(channel_id: int = CHANNEL_ID) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    return channel


def _make_cog(tmp_path: Path) -> tuple[WorkdirCommandCog, MagicMock]:
    repo = MagicMock()
    repo.save = AsyncMock()
    bot = MagicMock()
    cog = WorkdirCommandCog(
        bot,
        repo=repo,
        claude_channel_id=CHANNEL_ID,
        allowed_user_ids={USER_ID},
        claude_channel_ids={CHANNEL_ID},
        project_roots=[tmp_path],
    )
    return cog, repo


class TestAutocomplete:
    @pytest.mark.asyncio
    async def test_lists_directories(self, tmp_path: Path) -> None:
        (tmp_path / "quiet-ink").mkdir()
        (tmp_path / "plex-stack").mkdir()
        cog, _ = _make_cog(tmp_path)
        choices = await cog._path_autocomplete(_interaction(_thread()), "")
        values = {c.value for c in choices}
        assert str(tmp_path / "quiet-ink") in values
        assert str(tmp_path / "plex-stack") in values

    @pytest.mark.asyncio
    async def test_filters_by_query(self, tmp_path: Path) -> None:
        (tmp_path / "quiet-ink").mkdir()
        (tmp_path / "plex-stack").mkdir()
        cog, _ = _make_cog(tmp_path)
        choices = await cog._path_autocomplete(_interaction(_thread()), "plex")
        assert [c.value for c in choices] == [str(tmp_path / "plex-stack")]


class TestCd:
    @pytest.mark.asyncio
    async def test_retargets_thread(self, tmp_path: Path) -> None:
        (tmp_path / "proj").mkdir()
        cog, repo = _make_cog(tmp_path)
        interaction = _interaction(_thread())
        await cog.cd.callback(cog, interaction, path=str(tmp_path / "proj"))
        repo.save.assert_awaited_once_with(THREAD_ID, "", working_dir=str(tmp_path / "proj"))
        interaction.response.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_outside_thread(self, tmp_path: Path) -> None:
        (tmp_path / "proj").mkdir()
        cog, repo = _make_cog(tmp_path)
        interaction = _interaction(_text_channel())  # not a thread
        await cog.cd.callback(cog, interaction, path=str(tmp_path / "proj"))
        repo.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_non_directory(self, tmp_path: Path) -> None:
        cog, repo = _make_cog(tmp_path)
        interaction = _interaction(_thread())
        await cog.cd.callback(cog, interaction, path=str(tmp_path / "nope"))
        repo.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized(self, tmp_path: Path) -> None:
        (tmp_path / "proj").mkdir()
        cog, repo = _make_cog(tmp_path)
        interaction = _interaction(_thread(), user_id=999)
        await cog.cd.callback(cog, interaction, path=str(tmp_path / "proj"))
        repo.save.assert_not_awaited()


class TestCdNew:
    @pytest.mark.asyncio
    async def test_creates_thread_and_seeds_dir(self, tmp_path: Path) -> None:
        (tmp_path / "proj").mkdir()
        cog, repo = _make_cog(tmp_path)
        new_thread = MagicMock()
        new_thread.add_user = AsyncMock()
        new_thread.id = 5555
        new_thread.mention = "<#5555>"
        channel = _text_channel()
        channel.create_thread = AsyncMock(return_value=new_thread)
        interaction = _interaction(channel)

        await cog.cdnew.callback(cog, interaction, path=str(tmp_path / "proj"))

        channel.create_thread.assert_awaited_once()
        repo.save.assert_awaited_once_with(5555, "", working_dir=str(tmp_path / "proj"))
        new_thread.add_user.assert_awaited_once_with(interaction.user)
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rejects_non_directory(self, tmp_path: Path) -> None:
        cog, repo = _make_cog(tmp_path)
        channel = _text_channel()
        channel.create_thread = AsyncMock()
        interaction = _interaction(channel)
        await cog.cdnew.callback(cog, interaction, path=str(tmp_path / "nope"))
        channel.create_thread.assert_not_awaited()
        repo.save.assert_not_awaited()
