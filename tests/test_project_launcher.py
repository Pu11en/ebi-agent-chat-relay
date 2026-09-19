"""Launcher behavior without Discord network calls or model execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.project_launcher import LauncherView, ProjectLauncherCog
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository


def interaction(user: int = 42, channel: int = 100) -> MagicMock:
    item = MagicMock(spec=discord.Interaction)
    item.user = MagicMock(spec=discord.Member)
    item.user.id = user
    item.guild_id = 10
    item.channel_id = channel
    item.channel = MagicMock(spec=discord.TextChannel)
    item.channel.id = channel
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.defer = AsyncMock()
    item.response.send_modal = AsyncMock()
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    item.edit_original_response = AsyncMock()
    return item


@pytest.fixture
async def cog(tmp_path: Path) -> ProjectLauncherCog:
    db = str(tmp_path / "settings.db")
    await init_db(db)
    bot = MagicMock()
    bot.user = SimpleNamespace(id=999, display_name="Test computer")
    chat = SimpleNamespace(
        _allowed_user_ids={42, 43},
        _ensure_thread_members=AsyncMock(),
    )
    repo = SimpleNamespace(
        save=AsyncMock(), list_all=AsyncMock(return_value=[]), get=AsyncMock(return_value=None)
    )
    return ProjectLauncherCog(
        bot,
        repo,
        SettingsRepository(db),
        chat,
        channel_id=100,
        channel_ids={100},
        working_dir=str(tmp_path),
    )


async def test_favorites_persist_and_are_personal(cog, tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    await asyncio.gather(
        cog.change_favorite(10, 42, str(first), add=True),
        cog.change_favorite(10, 42, str(second), add=True),
    )
    assert set(await cog.favorites(10, 42)) == {str(first), str(second)}
    assert await cog.favorites(10, 43) == []
    assert await cog.favorites(11, 42) == []
    fresh = ProjectLauncherCog(
        cog.bot,
        cog.repo,
        cog.settings,
        cog.chat,
        channel_id=100,
        channel_ids={100},
        working_dir=str(tmp_path),
    )
    assert len(await fresh.favorites(10, 42)) == 2
    await fresh.change_favorite(10, 42, str(first), add=False)
    assert await fresh.favorites(10, 42) == [str(second)]


async def test_favorite_rejects_missing_and_relative_directory(cog, tmp_path):
    for path in (str(tmp_path / "missing"), "."):
        with pytest.raises(ValueError):
            await cog.change_favorite(10, 42, path, add=True)
    assert await cog.favorites(10, 42) == []


async def test_new_session_binds_folder_and_joins_shared_members(cog, tmp_path):
    event = interaction()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 333
    thread.mention = "<#333>"
    thread.add_user = AsyncMock()
    thread.send = AsyncMock()
    event.channel.create_thread = AsyncMock(return_value=thread)
    await cog.new_session(event, str(tmp_path))
    cog.repo.save.assert_awaited_once_with(333, "", working_dir=str(tmp_path))
    cog.chat._ensure_thread_members.assert_awaited_once_with(thread)
    thread.add_user.assert_awaited_once_with(event.user)
    assert str(tmp_path) in thread.send.call_args.args[0]
    event.response.defer.assert_awaited_once_with(ephemeral=True)
    assert await cog.recents(10, 42) == [str(tmp_path)]


@pytest.mark.parametrize("user,channel", [(99, 100), (42, 200)])
async def test_unauthorized_or_other_computer_channel_cannot_start(cog, tmp_path, user, channel):
    event = interaction(user, channel)
    await cog.new_session(event, str(tmp_path))
    cog.repo.save.assert_not_awaited()


async def test_deleted_folder_cannot_create_thread(cog, tmp_path):
    event = interaction()
    await cog.new_session(event, str(tmp_path / "deleted"))
    cog.repo.save.assert_not_awaited()
    assert "folder" in event.followup.send.call_args.args[0].lower()


async def test_favorites_offer_existing_folders_without_typing_paths(cog, tmp_path, monkeypatch):
    monkeypatch.setenv("CCDB_PROJECT_ROOTS", str(tmp_path))
    project = tmp_path / "use-this-folder"
    project.mkdir()
    event = interaction()
    await cog.show_folders(event, manage=True)
    view = event.followup.send.call_args.kwargs["view"]
    select = next(item for item in view.children if isinstance(item, discord.ui.Select))
    selected = next(option for option in select.options if option.label == project.name)
    select._values = [selected.value]
    await select.callback(event)
    assert await cog.favorites(10, 42) == [str(project)]


async def test_new_session_database_failure_removes_only_new_empty_thread(cog, tmp_path):
    event = interaction()
    thread = MagicMock(spec=discord.Thread)
    thread.id = 333
    thread.delete = AsyncMock()
    event.channel.create_thread = AsyncMock(return_value=thread)
    cog.repo.save.side_effect = RuntimeError("Database unavailable")
    with pytest.raises(RuntimeError):
        await cog.new_session(event, str(tmp_path))
    thread.delete.assert_awaited_once()


async def test_one_menu_cannot_create_two_threads(cog, tmp_path):
    await cog.change_favorite(10, 42, str(tmp_path), add=True)
    event = interaction()
    await cog.show_folders(event)
    select = event.followup.send.call_args.kwargs["view"].children[0]
    cog.show_browser = AsyncMock()
    select._values = ["0"]
    await select.callback(event)
    await select.callback(event)
    cog.show_browser.assert_awaited_once()


async def test_persistent_buttons_and_menu_owner(cog):
    panel = LauncherView(cog)
    assert panel.is_persistent()
    assert {button.label for button in panel.children} == {
        "Favorite folders",
        "New session",
        "Resume",
    }
    assert await panel.interaction_check(interaction())
    assert not await panel.interaction_check(interaction(99))
    await cog.show_folders(interaction())
    event = interaction()
    await cog.show_folders(event)
    view = event.followup.send.call_args.kwargs["view"]
    assert not await view.interaction_check(interaction(43))


async def test_resume_filters_deleted_foreign_and_inaccessible_threads(cog):
    records = [SimpleNamespace(thread_id=i, working_dir="/project") for i in (1, 2, 3, 4)]
    cog.repo.list_all.return_value = records
    live = MagicMock(spec=discord.Thread)
    live.id = 1
    live.parent_id = 100
    live.guild.id = 10
    live.name = "Existing work"
    live.permissions_for.return_value.view_channel = True
    live.is_private.return_value = False
    foreign = MagicMock(spec=discord.Thread)
    foreign.id = 3
    foreign.parent_id = 200
    foreign.guild.id = 10
    hidden = MagicMock(spec=discord.Thread)
    hidden.id = 4
    hidden.parent_id = 100
    hidden.guild.id = 10
    hidden.permissions_for.return_value.view_channel = False
    cog.bot.fetch_channel = AsyncMock(
        side_effect=[
            live,
            discord.NotFound(MagicMock(status=404), "deleted"),
            foreign,
            hidden,
        ]
    )
    event = interaction()
    await cog.show_resume(event)
    view = event.followup.send.call_args.kwargs["view"]
    select = view.children[0]
    assert [option.value for option in select.options] == ["1"]
    select._values = ["1"]
    cog.bot.fetch_channel = AsyncMock(return_value=live)
    await select.callback(event)
    assert "https://discord.com/channels/10/1" in event.followup.send.call_args.args[0]
    cog.repo.save.assert_not_awaited()


async def test_reconnect_updates_one_panel(cog):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 100
    message = MagicMock(spec=discord.Message)
    message.id = 555
    message.author.id = cog.bot.user.id
    message.pin = AsyncMock()
    message.edit = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(return_value=message)
    cog.bot.get_channel.return_value = channel
    await cog.on_ready()
    await cog.on_ready()
    channel.send.assert_awaited_once()
    message.edit.assert_awaited_once()
    assert "Test computer" in channel.send.call_args.kwargs["embed"].title


async def test_deleted_panel_is_recreated(cog):
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "deleted")
    )
    message = MagicMock(spec=discord.Message)
    message.id = 556
    message.pinned = False
    message.pin = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    cog.bot.get_channel.return_value = channel
    await cog.settings.set("launcher.panel:100", "555")
    await cog.on_ready()
    assert await cog.settings.get("launcher.panel:100") == "556"
    message.pin.assert_awaited_once()


async def test_private_thread_requires_membership(cog):
    thread = MagicMock(spec=discord.Thread)
    thread.parent_id = 100
    thread.guild.id = 10
    thread.permissions_for.return_value.view_channel = True
    thread.permissions_for.return_value.manage_threads = False
    thread.is_private.return_value = True
    thread.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "not a member")
    )
    cog.bot.fetch_channel = AsyncMock(return_value=thread)
    assert await cog.visible_thread(333, interaction()) is None


async def test_revoked_operator_cannot_use_existing_personal_menu(cog):
    event = interaction()
    await cog.show_folders(event)
    view = event.followup.send.call_args.kwargs["view"]
    cog.chat._allowed_user_ids.remove(42)
    assert not await view.interaction_check(event)


async def test_cog_load_registers_persistent_view_and_unload_stops_it(cog):
    await cog.cog_load()
    view = cog.bot.add_view.call_args.args[0]
    assert view.is_persistent()
    await cog.cog_unload()
    assert view.is_finished()


async def test_favorites_and_recents_are_separate_and_deduplicated(cog, tmp_path):
    favorite = tmp_path / "favorite"
    recent = tmp_path / "recent"
    favorite.mkdir()
    recent.mkdir()
    await cog.change_favorite(10, 42, str(favorite), add=True)
    await cog.remember_folder(10, 42, str(recent))
    await cog.remember_folder(10, 42, str(favorite))
    event = interaction()
    await cog.show_folders(event)
    selects = [
        c
        for c in event.followup.send.call_args.kwargs["view"].children
        if isinstance(c, discord.ui.Select)
    ]
    assert [c.placeholder for c in selects] == ["Favorite folders", "Recent folders"]
    assert [o.label for o in selects[0].options] == ["favorite"]
    assert [o.label for o in selects[1].options] == ["recent"]


async def test_recent_order_persists_and_is_personal(cog, tmp_path):
    for name in ("first", "second", "first"):
        await cog.remember_folder(10, 42, str(tmp_path / name))
    assert await cog.recents(10, 42) == [str(tmp_path / "first"), str(tmp_path / "second")]
    assert await cog.recents(10, 43) == []
    assert await cog.recents(11, 42) == []
    fresh = ProjectLauncherCog(
        cog.bot, cog.repo, cog.settings, cog.chat, channel_id=100, channel_ids={100}
    )
    assert await fresh.recents(10, 42) == await cog.recents(10, 42)


async def test_recent_selector_creates_one_folder_bound_session(cog, tmp_path):
    await cog.remember_folder(10, 42, str(tmp_path))
    event = interaction()
    await cog.show_folders(event)
    selects = [
        c
        for c in event.followup.send.call_args.kwargs["view"].children
        if isinstance(c, discord.ui.Select)
    ]
    recent = next(c for c in selects if c.placeholder == "Recent folders")
    recent._values = ["0"]
    cog.show_browser = AsyncMock()
    await recent.callback(event)
    await recent.callback(event)
    cog.show_browser.assert_awaited_once_with(event, str(tmp_path), edit=True)


async def test_browser_navigates_outside_project_root_without_starting(cog, tmp_path):
    nested = tmp_path / "outside" / "deep"
    nested.mkdir(parents=True)
    cog.new_session = AsyncMock()
    event = interaction()
    await cog.show_browser(event, str(tmp_path))
    view = event.followup.send.call_args.kwargs["view"]
    select = next(c for c in view.children if isinstance(c, discord.ui.Select))
    select._values = [next(o.value for o in select.options if o.label == "outside")]
    await select.callback(event)
    view = event.edit_original_response.call_args.kwargs["view"]
    select = next(c for c in view.children if isinstance(c, discord.ui.Select))
    assert [o.label for o in select.options] == ["deep"]
    cog.new_session.assert_not_awaited()


async def test_browser_paginates_all_folders_and_shows_hidden_folders(cog, tmp_path):
    for n in range(30):
        (tmp_path / f"project-{n:02}").mkdir()
    (tmp_path / ".hidden-project").mkdir()
    event = interaction()
    await cog.show_browser(event, str(tmp_path))
    view = event.followup.send.call_args.kwargs["view"]
    select = next(c for c in view.children if isinstance(c, discord.ui.Select))
    assert len(select.options) == 25
    assert select.options[0].label == ".hidden-project"
    next_button = next(
        c for c in view.children if isinstance(c, discord.ui.Button) and c.label == "Next"
    )
    await next_button.callback(event)
    view = event.edit_original_response.call_args.kwargs["view"]
    select = next(c for c in view.children if isinstance(c, discord.ui.Select))
    assert len(select.options) == 6
    assert select.options[-1].label == "project-29"


async def test_start_here_is_explicit_and_single_use(cog, tmp_path):
    event = interaction()
    await cog.show_browser(event, str(tmp_path))
    view = event.followup.send.call_args.kwargs["view"]
    start = next(
        c for c in view.children if isinstance(c, discord.ui.Button) and c.label == "Start here"
    )
    cog.new_session = AsyncMock()
    await start.callback(event)
    await start.callback(event)
    cog.new_session.assert_awaited_once_with(event, str(tmp_path))


async def test_fixed_launcher_home_creates_sessions_in_worker_channel(cog, tmp_path):
    fixed = ProjectLauncherCog(
        cog.bot,
        cog.repo,
        cog.settings,
        cog.chat,
        channel_id=100,
        channel_ids={100, 200},
        home_channel_id=500,
        session_channel_id=200,
    )
    worker = MagicMock(spec=discord.TextChannel)
    worker.id = 200
    thread = MagicMock(spec=discord.Thread)
    thread.id = 333
    thread.add_user = AsyncMock()
    thread.send = AsyncMock()
    worker.create_thread = AsyncMock(return_value=thread)
    cog.bot.get_channel.return_value = worker
    event = interaction(channel=500)
    await fixed.new_session(event, str(tmp_path))
    worker.create_thread.assert_awaited_once()
    event.channel.create_thread.assert_not_called()
    assert fixed.channel_id == 500


async def test_browse_button_lists_folders_without_a_typing_modal(cog, tmp_path, monkeypatch):
    monkeypatch.setenv("CCDB_PROJECT_ROOTS", str(tmp_path))
    (tmp_path / "another-project").mkdir()
    await cog.change_favorite(10, 42, str(tmp_path), add=True)
    event = interaction()
    await cog.show_folders(event)
    view = event.followup.send.call_args.kwargs["view"]
    browse = next(
        child
        for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == "Browse folders"
    )
    await browse.callback(event)
    event.response.send_modal.assert_not_awaited()
    view = event.edit_original_response.call_args.kwargs["view"]
    options = [
        option.label
        for child in view.children
        if isinstance(child, discord.ui.Select)
        for option in child.options
    ]
    assert "another-project" in options


async def test_launcher_category_scope_also_guards_buttons(cog, monkeypatch):
    monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "123")
    event = interaction()
    event.channel.category_id = 456
    assert not await cog.authorize(event)
