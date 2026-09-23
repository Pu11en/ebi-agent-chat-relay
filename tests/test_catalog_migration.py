"""Legacy launcher favorites/recents → catalog identities (task 2.2).

The old launcher stored raw absolute paths under ``launcher.favorites:*`` and
``launcher.recents:*``.  Those that are direct children of an approved root
become catalog metadata; the rest stay exactly where they were so the manual
browser keeps offering them.  Nothing is deleted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePath, PureWindowsPath

import pytest

from claude_discord.catalog_migration import (
    MigrationReport,
    map_legacy_path,
    map_legacy_paths,
    migrate_launcher_metadata,
)
from claude_discord.database.models import init_db
from claude_discord.database.project_catalog_repo import ProjectCatalogRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.project_catalog import ApprovedRoot, ProjectIdentity

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def root(tmp_path: Path, key: str = "main") -> ApprovedRoot:
    return ApprovedRoot(key=key, path=PurePath(tmp_path / key), owner="drew", computer="drewai")


def test_direct_child_of_a_root_maps_without_touching_disk(tmp_path):
    main = root(tmp_path)
    identity = map_legacy_path(str(tmp_path / "main" / "alpha"), [main])
    assert identity == ProjectIdentity("drew", "drewai", "main", "alpha")
    assert not (tmp_path / "main" / "alpha").exists()  # absence is fine: metadata survives


def test_nested_outside_and_root_itself_do_not_map(tmp_path):
    main = root(tmp_path)
    assert map_legacy_path(str(tmp_path / "main" / "alpha" / "src"), [main]) is None
    assert map_legacy_path(str(tmp_path / "elsewhere" / "alpha"), [main]) is None
    assert map_legacy_path(str(tmp_path / "main"), [main]) is None
    assert map_legacy_path("relative/alpha", [main]) is None
    assert map_legacy_path("", [main]) is None


def test_trailing_separator_and_dot_segments_are_normalized(tmp_path):
    main = root(tmp_path)
    messy = str(tmp_path / "main") + "/./alpha/"
    assert map_legacy_path(messy, [main]) == ProjectIdentity("drew", "drewai", "main", "alpha")


def test_windows_paths_compare_case_insensitively():
    win = ApprovedRoot(
        key="main", path=PureWindowsPath(r"C:\Users\david\projects"), owner="david", computer="pc"
    )
    identity = map_legacy_path(r"c:\users\DAVID\projects\Alpha", [win], case_insensitive=True)
    assert identity == ProjectIdentity("david", "pc", "main", "Alpha")  # spelling kept


def test_same_name_under_two_roots_maps_to_the_right_root(tmp_path):
    main, work = root(tmp_path, "main"), root(tmp_path, "work")
    mapping = map_legacy_paths(
        [str(tmp_path / "work" / "alpha"), str(tmp_path / "main" / "alpha"), "/nowhere/alpha"],
        [main, work],
    )
    assert [i.root_key for _, i in mapping.mapped] == ["work", "main"]
    assert mapping.unmapped == ("/nowhere/alpha",)


@pytest.fixture
async def stores(tmp_path: Path) -> tuple[SettingsRepository, ProjectCatalogRepository]:
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    return SettingsRepository(db), ProjectCatalogRepository(db)


async def test_migration_maps_valid_entries_and_leaves_raw_settings_alone(stores, tmp_path):
    settings, catalog = stores
    main = root(tmp_path)
    favorites = [str(tmp_path / "main" / "alpha"), "/legacy/outside"]
    recents = [str(tmp_path / "main" / "beta"), str(tmp_path / "main" / "alpha"), "/old/place"]
    await settings.set("launcher.favorites:10:42", json.dumps(favorites))
    await settings.set("launcher.recents:10:42", json.dumps(recents))

    report = await migrate_launcher_metadata(settings, catalog, [main], 10, 42, now=NOW)

    assert report == MigrationReport(
        favorites_mapped=1,
        favorites_unmapped=("/legacy/outside",),
        recents_mapped=2,
        recents_unmapped=("/old/place",),
    )
    alpha = await catalog.get(10, 42, ProjectIdentity("drew", "drewai", "main", "alpha"))
    assert alpha is not None and alpha.favorite and alpha.last_opened_at is not None
    assert [m.identity.name for m in await catalog.recents(10, 42)] == ["beta", "alpha"]
    # The raw settings are exactly as they were: the manual browser still sees them.
    assert json.loads(await settings.get("launcher.favorites:10:42") or "[]") == favorites
    assert json.loads(await settings.get("launcher.recents:10:42") or "[]") == recents


async def test_migration_runs_once_and_never_re_favorites_after_the_user_changed_their_mind(
    stores, tmp_path
):
    settings, catalog = stores
    main = root(tmp_path)
    await settings.set("launcher.favorites:10:42", json.dumps([str(tmp_path / "main" / "alpha")]))
    first = await migrate_launcher_metadata(settings, catalog, [main], 10, 42, now=NOW)
    assert first.favorites_mapped == 1
    identity = ProjectIdentity("drew", "drewai", "main", "alpha")
    await catalog.set_favorite(10, 42, identity, False)
    second = await migrate_launcher_metadata(settings, catalog, [main], 10, 42, now=NOW)
    assert second.skipped and second.favorites_mapped == 0
    assert await catalog.get(10, 42, identity) is None


async def test_migration_tolerates_missing_or_malformed_legacy_values(stores, tmp_path):
    settings, catalog = stores
    report = await migrate_launcher_metadata(settings, catalog, [root(tmp_path)], 10, 42, now=NOW)
    assert report.favorites_mapped == 0 and report.recents_mapped == 0 and not report.skipped
    await settings.set("launcher.favorites:10:43", "not json")
    await settings.set("launcher.recents:10:43", json.dumps({"bad": 1}))
    report = await migrate_launcher_metadata(settings, catalog, [root(tmp_path)], 10, 43, now=NOW)
    assert report.favorites_mapped == 0 and report.recents_mapped == 0
    assert await settings.get("launcher.favorites:10:43") == "not json"


async def test_migrated_metadata_applies_when_the_folder_appears_later(stores, tmp_path):
    """Absence at migration time is not a reason to drop the favorite."""
    settings, catalog = stores
    main = root(tmp_path)
    await settings.set("launcher.favorites:10:42", json.dumps([str(tmp_path / "main" / "gone")]))
    await migrate_launcher_metadata(settings, catalog, [main], 10, 42, now=NOW)
    (tmp_path / "main" / "gone").mkdir(parents=True)
    stored = await catalog.get(10, 42, ProjectIdentity("drew", "drewai", "main", "gone"))
    assert stored is not None and stored.favorite
