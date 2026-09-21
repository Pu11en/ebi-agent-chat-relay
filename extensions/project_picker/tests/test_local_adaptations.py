"""Focused checks for the small current-Ebi adapter changes."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_workdir_command import CHANNEL_ID, THREAD_ID, _interaction, _make_cog, _thread
from workdir_command import WorkdirCommandCog, setup


@pytest.mark.asyncio
async def test_autocomplete_checks_owner(tmp_path):
    (tmp_path / "private-project").mkdir()
    cog, _ = _make_cog(tmp_path)
    assert await cog._path_autocomplete(_interaction(_thread(), user_id=999), "") == []


@pytest.mark.asyncio
async def test_spaces_and_case_insensitive_name(tmp_path):
    project = tmp_path / "meme explorer"
    project.mkdir()
    cog, _ = _make_cog(tmp_path)
    choices = await cog._path_autocomplete(_interaction(_thread()), "MEME")
    assert [choice.value for choice in choices] == [str(project)]


@pytest.mark.asyncio
async def test_cd_does_not_retarget_active_run(tmp_path):
    cog, repo = _make_cog(tmp_path)
    cog._chat_cog = SimpleNamespace(_thread_locks={}, _active_runners={THREAD_ID: object()})
    await cog.cd.callback(cog, _interaction(_thread()), path=str(tmp_path))
    repo.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_cd_does_not_wait_on_busy_thread(tmp_path):
    cog, repo = _make_cog(tmp_path)
    lock = asyncio.Lock()
    cog._chat_cog = SimpleNamespace(_thread_locks={THREAD_ID: lock}, _active_runners={})
    async with lock:
        await cog.cd.callback(cog, _interaction(_thread()), path=str(tmp_path))
    repo.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_uses_existing_permissions_and_repos(monkeypatch):
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(CHANNEL_ID))
    chat = SimpleNamespace(_channel_ids={CHANNEL_ID}, _allowed_user_ids={42})
    bot = MagicMock()
    bot.get_cog.return_value = chat
    bot.add_cog = AsyncMock()
    repo = MagicMock()
    await setup(bot, None, SimpleNamespace(session_repo=repo))
    cog = bot.add_cog.call_args.args[0]
    assert isinstance(cog, WorkdirCommandCog)
    assert cog.repo is repo
    assert cog._allowed_user_ids == {42}
    assert cog._chat_cog is chat


@pytest.mark.asyncio
async def test_seeded_directory_reaches_stock_codex_runner(tmp_path):
    from claude_code_core.codex_runner import CodexRunner
    from claude_discord.database.models import init_db
    from claude_discord.database.repository import SessionRepository

    # Exercise the real database, not the command mock.
    repo = SessionRepository(str(tmp_path / "sessions.db"))
    await init_db(str(tmp_path / "sessions.db"))
    directory = tmp_path / "meme explorer"
    directory.mkdir()
    await repo.save(123, "", working_dir=str(directory))
    record = await repo.get(123)
    assert record.session_id == ""
    runner = CodexRunner(working_dir=record.working_dir)
    args = runner._build_args("read only check", None)
    assert args[args.index("--cd") + 1] == str(directory)


def test_normalizes_relative_components(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    assert WorkdirCommandCog._validate_dir(str(nested / "..")) == tmp_path


def test_invalid_null_character():
    assert WorkdirCommandCog._validate_dir("\0") is None
