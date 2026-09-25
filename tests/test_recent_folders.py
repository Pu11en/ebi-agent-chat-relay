"""What `/cd` offers first is where you actually were, not where you launched from.

The launcher kept its own recents list, written only when a session was started
*through the launcher*. Work continues in a thread for days afterwards, and
sessions start plenty of other ways, so that list went stale immediately: asked
"recommend me the ones I use", it answered with a handful of launches from a
while ago. The session table already records every folder and when it was last
used — that is the real answer, and it costs one cached read.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.cogs.project_launcher import ProjectLauncherCog

pytestmark = pytest.mark.asyncio


class _Settings:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str, *, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


def _cog(sessions: list[str | None], stored: list[str] | None = None):
    settings = _Settings()
    if stored is not None:
        settings.values["launcher.recents:1:42"] = json.dumps(stored)
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[SimpleNamespace(working_dir=path) for path in sessions])
    cog = ProjectLauncherCog.__new__(ProjectLauncherCog)
    ProjectLauncherCog.__init__(
        cog, MagicMock(), repo, settings, MagicMock(), channel_id=1, channel_ids={1}
    )
    return cog


async def test_the_folders_worked_in_most_recently_come_first():
    cog = _cog(["/p/boa", "/p/aldus", "/p/lockin"])
    assert await cog.recent_folders(1, 42) == ["/p/boa", "/p/aldus", "/p/lockin"]


async def test_the_same_folder_is_listed_once_at_its_most_recent_position():
    cog = _cog(["/p/boa", "/p/aldus", "/p/boa"])
    assert await cog.recent_folders(1, 42) == ["/p/boa", "/p/aldus"]


async def test_sessions_with_no_folder_are_skipped():
    cog = _cog(["/p/boa", None, "", "/p/aldus"])
    assert await cog.recent_folders(1, 42) == ["/p/boa", "/p/aldus"]


async def test_launcher_starts_still_count_after_session_history():
    cog = _cog(["/p/boa"], stored=["/p/saved-by-launcher", "/p/boa"])
    assert await cog.recent_folders(1, 42) == ["/p/boa", "/p/saved-by-launcher"]


async def test_an_unreadable_session_table_falls_back_to_the_stored_list():
    """Autocomplete must never go empty because a read failed."""
    cog = _cog([], stored=["/p/saved-by-launcher"])
    cog.repo.list_all = AsyncMock(side_effect=RuntimeError("database is locked"))
    assert await cog.recent_folders(1, 42) == ["/p/saved-by-launcher"]


async def test_the_session_read_is_cached_across_keystrokes():
    """Autocomplete fires per keystroke; one read per interval, not per letter."""
    cog = _cog(["/p/boa"])
    for _ in range(5):
        await cog.recent_folders(1, 42)
    assert cog.repo.list_all.await_count == 1


async def test_remembering_a_launcher_folder_does_not_store_session_history():
    """`remember_folder` writes the launcher's own list; history is read, never copied."""
    cog = _cog(["/p/from-a-thread"], stored=["/p/old"])
    await cog.remember_folder(1, 42, "/p/new")
    assert json.loads(cog.settings.values["launcher.recents:1:42"]) == ["/p/new", "/p/old"]


# --- throwaway build directories are not places you work -------------------


async def test_ccdb_scratch_directories_are_not_offered_as_folders(monkeypatch, tmp_path):
    """Everything ccdb cuts for itself lives under its own state directory.

    `/gowork` builds, per-task worktrees, staging copies — they show up in the
    session table like any other folder and dominated the list, but they are
    deleted when the work ends, so offering one is offering a folder that will
    not be there. The project they were cut from is in the list already, on its
    own. Excluding the whole state directory is the rule; naming each kind of
    scratch folder is a list that goes stale on the next feature.
    """
    monkeypatch.setenv("CCDB_GOWORK_STATE", str(tmp_path / "ccdb" / "gowork-loops.json"))
    scratch = str(tmp_path / "ccdb" / "gowork" / "wt-123-some-plan")
    other_scratch = str(tmp_path / "ccdb" / "masiate-finish" / "pdf-renderer")
    cog = _cog(["/p/boa", scratch, other_scratch, "/p/aldus"])
    assert await cog.recent_folders(1, 42) == ["/p/boa", "/p/aldus"]


async def test_a_real_folder_outside_that_directory_is_still_offered(monkeypatch, tmp_path):
    monkeypatch.setenv("CCDB_GOWORK_STATE", str(tmp_path / "ccdb" / "gowork-loops.json"))
    mine = str(tmp_path / "my-actual-project")
    cog = _cog([mine])
    assert await cog.recent_folders(1, 42) == [mine]
