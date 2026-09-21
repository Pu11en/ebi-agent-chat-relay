"""The one catalog service every consumer shares (task 3.1).

Built once in ``setup_bridge`` from a :class:`CatalogConfig` and the metadata
repository; the launcher, the REST control plane and custom Cogs all hold the
same instance, so they cannot disagree about identities or availability.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_discord.catalog_config import CatalogConfig
from claude_discord.catalog_service import (
    CatalogEntry,
    ProjectCatalogService,
    entry_to_dict,
    resolution_to_dict,
)
from claude_discord.database.models import init_db
from claude_discord.database.project_catalog_repo import ProjectCatalogRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.project_catalog import ProjectIdentity, ResolutionKind

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
async def service(tmp_path: Path) -> ProjectCatalogService:
    main = tmp_path / "main"
    work = tmp_path / "work"
    for name in ("alpha", "beta", "gamma"):
        (main / name).mkdir(parents=True)
    (work / "alpha").mkdir(parents=True)  # same name, second root
    (main / "notes.txt").write_text("not a project", encoding="utf-8")
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    config = CatalogConfig.from_env(
        {
            "CCDB_PROJECT_ROOTS": f"{main},{work},{tmp_path / 'missing'}",
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
            "CCDB_CATALOG_COMPUTERS": "david/davidpc",
        }
    )
    return ProjectCatalogService(
        config, ProjectCatalogRepository(db), settings=SettingsRepository(db), cache_ttl=0
    )


async def test_listing_shows_every_direct_child_with_disambiguated_labels(service):
    listing = await service.list_projects(10, 42)
    labels = [entry.label for entry in listing.entries]
    assert labels == ["alpha (main)", "alpha (work)", "beta", "gamma"]
    assert all(entry.project.is_available for entry in listing.entries)
    assert [s.root.key for s in listing.unavailable_roots] == ["missing"]
    assert "notes.txt" not in labels


async def test_favorites_sort_first_and_hidden_are_omitted_from_browse_but_found_by_search(
    service,
):
    gamma = ProjectIdentity("drew", "drewai", "main", "gamma")
    beta = ProjectIdentity("drew", "drewai", "main", "beta")
    await service.set_favorite(10, 42, gamma.key, True)
    await service.set_hidden(10, 42, beta.key, True)
    listing = await service.list_projects(10, 42)
    assert [e.label for e in listing.entries] == ["gamma", "alpha (main)", "alpha (work)"]
    assert listing.entries[0].favorite
    # Another user's view is untouched.
    assert [e.label for e in (await service.list_projects(10, 43)).entries] == [
        "alpha (main)",
        "alpha (work)",
        "beta",
        "gamma",
    ]
    # An explicit search still finds the hidden project and says it is hidden.
    found = await service.list_projects(10, 42, query="beta")
    assert [(e.label, e.hidden) for e in found.entries] == [("beta", True)]


async def test_recents_follow_opens_and_skip_folders_that_vanished(service, tmp_path):
    alpha = ProjectIdentity("drew", "drewai", "main", "alpha")
    gamma = ProjectIdentity("drew", "drewai", "main", "gamma")
    assert await service.remember_path(10, 42, str(tmp_path / "main" / "alpha"), now=NOW) == alpha
    later = NOW.replace(minute=5)
    await service.remember_path(10, 42, str(tmp_path / "main" / "gamma"), now=later)
    assert await service.remember_path(10, 42, str(tmp_path / "elsewhere"), now=NOW) is None
    assert [e.project.identity for e in await service.recents(10, 42)] == [gamma, alpha]
    (tmp_path / "main" / "gamma").rmdir()
    assert [e.project.identity for e in await service.recents(10, 42)] == [alpha]
    # The metadata itself survives the absence.
    stored = await service.metadata.get(10, 42, gamma)
    assert stored is not None and stored.last_opened_at is not None


async def test_find_revalidates_and_refuses_anything_outside_its_root(service, tmp_path):
    alpha = ProjectIdentity("drew", "drewai", "main", "alpha")
    project = await service.find(alpha.key)
    assert project is not None and project.working_directory == tmp_path / "main" / "alpha"
    assert await service.find("drew:drewai:main:..") is None
    assert await service.find("drew:drewai:other:alpha") is None
    assert await service.find("garbage") is None
    (tmp_path / "main" / "alpha").rmdir()
    stale = await service.find(alpha.key)
    assert stale is not None and not stale.is_available and stale.working_directory is None


async def test_resolve_and_search_share_the_resolver_and_serialize_without_paths_for_remote(
    service,
):
    local = await service.resolve("beta")
    assert local.kind is ResolutionKind.LOCAL_AVAILABLE
    assert resolution_to_dict(local)["path"].endswith("beta")
    remote = await service.resolve("David's alpha")
    assert remote.kind is ResolutionKind.REMOTE_TARGET
    payload = resolution_to_dict(remote)
    assert payload["computer"] == "davidpc" and "path" not in payload
    assert payload["locally_verified"] is False
    ambiguous = await service.resolve("alpha")
    assert ambiguous.kind is ResolutionKind.AMBIGUOUS_OWNER
    assert len(resolution_to_dict(ambiguous)["candidates"]) == 2
    snapshot = await service.search("alp", limit=1)
    assert len(snapshot.projects) == 1 and snapshot.truncated


async def test_entry_dict_carries_identity_availability_and_actions_only(service):
    listing = await service.list_projects(10, 42)
    payload = entry_to_dict(listing.entries[0])
    assert set(payload) == {
        "key",
        "name",
        "label",
        "owner",
        "computer",
        "root",
        "path",
        "availability",
        "favorite",
        "hidden",
        "last_opened_at",
        "actions",
    }
    assert payload["actions"] == ["session", "create", "clone"]
    assert isinstance(listing.entries[0], CatalogEntry)


async def test_legacy_launcher_metadata_is_migrated_once_on_first_use(service, tmp_path):
    import json

    assert service.settings is not None
    await service.settings.set(
        "launcher.favorites:10:42", json.dumps([str(tmp_path / "main" / "beta"), "/outside"])
    )
    listing = await service.list_projects(10, 42)
    assert listing.entries[0].label == "beta" and listing.entries[0].favorite
    assert (await service.legacy_unmapped(10, 42)).favorites == ("/outside",)
    await service.set_favorite(10, 42, ProjectIdentity("drew", "drewai", "main", "beta").key, False)
    listing = await service.list_projects(10, 42)
    assert not listing.entries[0].favorite or listing.entries[0].label != "beta"
