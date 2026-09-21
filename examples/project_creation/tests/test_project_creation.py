from __future__ import annotations

import contextlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

SOURCE = Path(__file__).parents[1] / "project_creation.py"
spec = importlib.util.spec_from_file_location("project_creation", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "name", ["../x", "a/b", "a\\b", "CON", "nul.txt", "a.", "a ", "", "a:b", ".git"]
)
def test_reject_invalid_windows_folder_names(tmp_path, name):
    with pytest.raises(ValueError):
        module.destination(tmp_path, name)


def test_existing_folder_is_never_reused(tmp_path):
    (tmp_path / "Project").mkdir()
    with pytest.raises(ValueError):
        module.destination(tmp_path, "project")


@pytest.mark.parametrize(
    "repo",
    [
        "--help",
        "file:///tmp/x",
        "https://other.test/a/b",
        "a/b/c",
        "a/b;cmd",
        "https://user:secret@github.com/a/b",
    ],
)
def test_reject_non_github_repository_input(repo):
    with pytest.raises(ValueError):
        module.repository(repo)


def test_normalize_github_link():
    assert module.repository("https://github.com/owner/repo.git") == (
        "https://github.com/owner/repo.git",
        "repo",
    )
    assert module.repository("owner/repo") == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.asyncio
async def test_clone_reuses_gh_and_publishes_new_folder(tmp_path, monkeypatch):
    calls = []

    async def run(*args):
        calls.append(args)
        (Path(args[-1]) / "README.md").write_text("cloned")

    monkeypatch.setattr(module, "run_gh", run)
    path = await module.create_project(tmp_path, "app", "owner/repo")
    assert path == tmp_path / "app"
    assert (path / "README.md").read_text() == "cloned"
    assert calls[0][:3] == ("repo", "clone", "https://github.com/owner/repo.git")
    assert not list(tmp_path.glob(".clone-*"))


@pytest.mark.asyncio
async def test_failed_clone_leaves_existing_projects_and_no_new_project(tmp_path, monkeypatch):
    existing = tmp_path / "keep"
    existing.mkdir()
    (existing / "data").write_text("mine")

    async def fail(*args):
        (Path(args[-1]) / "partial").write_text("partial")
        raise ValueError("GitHub clone failed")

    monkeypatch.setattr(module, "run_gh", fail)
    with pytest.raises(ValueError):
        await module.create_project(tmp_path, "app", "owner/repo")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["keep"]
    assert (existing / "data").read_text() == "mine"


@pytest.mark.asyncio
async def test_destination_created_during_clone_is_not_overwritten(tmp_path, monkeypatch):
    async def run(*args):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "keep").write_text("mine")

    monkeypatch.setattr(module, "run_gh", run)
    with pytest.raises((ValueError, FileExistsError)):
        await module.create_project(tmp_path, "app", "owner/repo")
    assert (tmp_path / "app" / "keep").read_text() == "mine"


@pytest.mark.asyncio
async def test_create_empty_folder(tmp_path):
    path = await module.create_project(tmp_path, "My project")
    assert path.is_dir() and list(path.iterdir()) == []


@pytest.mark.asyncio
async def test_clone_subprocess_has_no_prompt_or_discord_secret(monkeypatch):
    process = MagicMock(returncode=0)
    process.wait = AsyncMock(return_value=0)
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module.shutil, "which", lambda x: "/bin/gh")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "do-not-pass")
    monkeypatch.setenv("GH_TOKEN", "existing-github-login")
    await module.run_gh("repo", "clone", "https://github.com/a/b.git", "/tmp/new")
    env = spawn.call_args.kwargs["env"]
    assert "DISCORD_BOT_TOKEN" not in env
    assert env["GH_TOKEN"] == "existing-github-login"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "shell" not in spawn.call_args.kwargs


@pytest.mark.asyncio
async def test_timeout_kills_and_waits_for_clone(monkeypatch):
    process = MagicMock(returncode=None)
    process.wait = AsyncMock(side_effect=[TimeoutError, 1])
    process.kill = MagicMock()
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(module.shutil, "which", lambda x: "/bin/gh")
    with pytest.raises(ValueError, match="timed out"):
        await module.run_gh("repo", "clone", "https://github.com/a/b.git", "/tmp/new")
    process.kill.assert_called_once()
    assert process.wait.await_count == 2


def cog(tmp_path):
    bot = MagicMock()
    return module.ProjectCreationCog(
        bot, tmp_path / "projects", tmp_path / "recent.json", 10, 20, {1, 2}
    )


@pytest.mark.asyncio
async def test_recents_persist_per_user_and_ignore_missing_paths(tmp_path):
    c = cog(tmp_path)
    c.root.mkdir()
    (c.root / "first").mkdir()
    (c.root / "second").mkdir()
    await c.remember(1, "first")
    await c.remember(1, "second")
    await c.remember(2, "first")
    again = cog(tmp_path)
    assert again.projects(1, recent=True) == ["second", "first"]
    assert again.projects(2, recent=True) == ["first"]
    (c.root / "first").rmdir()
    assert again.projects(1, recent=True) == ["second"]


def test_all_projects_excludes_staging_and_symlinks(tmp_path):
    c = cog(tmp_path)
    c.root.mkdir()
    (c.root / "app").mkdir()
    (c.root / ".clone-pending").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with contextlib.suppress(OSError):
        (c.root / "link").symlink_to(outside, target_is_directory=True)
    assert c.projects(1) == ["app"]


@pytest.mark.asyncio
async def test_authorization_rejects_foreign_channel_and_user(tmp_path):
    c = cog(tmp_path)
    i = SimpleNamespace(
        channel_id=10,
        user=SimpleNamespace(id=9),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    assert not await c.authorize(i)
    i.user.id = 1
    i.channel_id = 999
    assert not await c.authorize(i)
    i.channel_id = 10
    assert await c.authorize(i)


@pytest.mark.asyncio
async def test_session_is_idle_bound_to_project_and_invites_requester(tmp_path):
    c = cog(tmp_path)
    path = c.root / "app"
    path.mkdir(parents=True)
    chat = SimpleNamespace(spawn_session=AsyncMock(return_value=SimpleNamespace(mention="#new")))
    c.bot.get_cog.return_value = chat
    channel = MagicMock()
    channel.guild.id = 50
    channel.category_id = 60
    c.bot.get_channel.return_value = channel
    i = SimpleNamespace(
        user=SimpleNamespace(id=1),
        followup=SimpleNamespace(send=AsyncMock()),
        guild_id=50,
        channel=SimpleNamespace(category_id=60),
    )
    await c.open_session(i, path)
    kwargs = chat.spawn_session.call_args.kwargs
    assert kwargs["auto_start"] is False
    assert kwargs["working_dir"] == str(path.resolve())
    assert kwargs["invite_user_id"] == 1
    assert "session_id" not in kwargs


@pytest.mark.asyncio
async def test_session_cannot_route_to_another_category(tmp_path):
    c = cog(tmp_path)
    path = c.root / "app"
    path.mkdir(parents=True)
    chat = SimpleNamespace(spawn_session=AsyncMock())
    c.bot.get_cog.return_value = chat
    c.bot.get_channel.return_value = SimpleNamespace(guild=SimpleNamespace(id=50), category_id=999)
    i = SimpleNamespace(
        guild_id=50,
        channel=SimpleNamespace(category_id=60),
        user=SimpleNamespace(id=1),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    with pytest.raises(ValueError, match="category"):
        await c.open_session(i, path)
    chat.spawn_session.assert_not_called()


@pytest.mark.asyncio
async def test_new_command_takes_no_arguments_and_shows_menu(tmp_path):
    c = cog(tmp_path)
    i = SimpleNamespace(
        channel_id=10,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock()),
    )
    await c.new.callback(c, i)
    view = i.response.send_message.call_args.kwargs["view"]
    assert {child.label for child in view.children} == {"Recent", "All projects", "Create project"}
    assert c.new.parameters == []


@pytest.mark.asyncio
async def test_create_menu_has_both_creation_options(tmp_path):
    c = cog(tmp_path)
    view = module.HomeView(c, 1)
    i = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
    await view.create.callback(i)
    submenu = i.response.edit_message.call_args.kwargs["view"]
    assert {child.label for child in submenu.children} == {
        "Empty folder",
        "Clone GitHub repo",
        "Back",
    }


@pytest.mark.asyncio
async def test_foreign_user_cannot_submit_clone_modal(tmp_path):
    c = cog(tmp_path)
    view = module.HomeView(c, 1)
    modal = module.CreateModal(view, True)
    i = SimpleNamespace(
        channel_id=10,
        user=SimpleNamespace(id=2),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )
    await modal.on_submit(i)
    i.response.defer.assert_not_called()
    assert not c.root.exists()


@pytest.mark.asyncio
async def test_duplicate_creation_submission_does_not_create_twice(tmp_path):
    c = cog(tmp_path)
    view = module.HomeView(c, 1)
    view.busy = True
    modal = module.CreateModal(view, True)
    i = SimpleNamespace(
        channel_id=10,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )
    await modal.on_submit(i)
    i.response.defer.assert_not_called()


@pytest.mark.asyncio
async def test_project_pages_keep_every_project_available(tmp_path):
    c = cog(tmp_path)
    names = [f"project{i}" for i in range(60)]
    view = module.ProjectList(c, 1, names, 2)
    picker = next(child for child in view.children if isinstance(child, module.discord.ui.Select))
    assert [o.label for o in picker.options] == names[50:]
    assert view.next_page.disabled
    assert not view.previous.disabled


@pytest.mark.asyncio
async def test_windows_timeout_kills_only_clone_tree(monkeypatch):
    process = SimpleNamespace(returncode=None, pid=12345, wait=AsyncMock())
    killer = SimpleNamespace(wait=AsyncMock())
    spawn = AsyncMock(return_value=killer)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    await module.kill_clone(process)
    assert spawn.call_args.args == ("taskkill.exe", "/PID", "12345", "/T", "/F")
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_clone_modal_creates_project_then_idle_session(tmp_path, monkeypatch):
    c = cog(tmp_path)
    c.open_session = AsyncMock()
    modal = module.CreateModal(module.HomeView(c, 1), True)
    modal.repo._value = "owner/my-app"
    modal.folder._value = ""
    i = SimpleNamespace(
        channel_id=10,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    run = AsyncMock()
    monkeypatch.setattr(module, "run_gh", run)
    await modal.on_submit(i)
    assert (c.root / "my-app").is_dir()
    c.open_session.assert_awaited_once_with(i, c.root / "my-app")


@pytest.mark.asyncio
async def test_clone_failure_does_not_open_session_and_allows_retry(tmp_path, monkeypatch):
    c = cog(tmp_path)
    c.open_session = AsyncMock()
    view = module.HomeView(c, 1)
    modal = module.CreateModal(view, True)
    modal.repo._value = "owner/my-app"
    modal.folder._value = ""
    i = SimpleNamespace(
        channel_id=10,
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    monkeypatch.setattr(module, "run_gh", AsyncMock(side_effect=ValueError("Clone failed")))
    await modal.on_submit(i)
    c.open_session.assert_not_called()
    assert not view.busy
    assert not (c.root / "my-app").exists()
