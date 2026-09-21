"""Personal catalog metadata (OpenSpec shared-project-catalog task 2.1).

Favorite, Hide and recency are presentation metadata keyed by the stable
catalog identity, never by path.  The repository does not know whether a
project exists — that is discovery's job — so metadata for a project that is
temporarily absent stays put and applies again when the folder returns.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_discord.database.models import init_db
from claude_discord.database.project_catalog_repo import (
    ProjectCatalogRepository,
    ProjectMetadata,
)
from claude_discord.project_catalog import ProjectIdentity


def identity(name: str = "alpha", *, root_key: str = "main") -> ProjectIdentity:
    return ProjectIdentity(owner="drew", computer="drewai", root_key=root_key, name=name)


@pytest.fixture
async def repo(tmp_path: Path) -> ProjectCatalogRepository:
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    return ProjectCatalogRepository(db)


NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


async def test_favorite_and_hidden_are_stored_by_identity_and_returned_intact(repo):
    await repo.set_favorite(10, 42, identity(), True)
    await repo.set_hidden(10, 42, identity("beta"), True)
    favorite = await repo.get(10, 42, identity())
    hidden = await repo.get(10, 42, identity("beta"))
    assert favorite == ProjectMetadata(identity(), favorite=True, hidden=False, last_opened_at=None)
    assert hidden is not None and hidden.hidden and not hidden.favorite
    assert favorite.identity == identity()  # the exact identity, not a path or a string
    assert favorite.identity.key == "drew:drewai:main:alpha"


async def test_metadata_is_isolated_per_user_and_per_guild(repo):
    await repo.set_favorite(10, 42, identity(), True)
    assert await repo.get(10, 43, identity()) is None
    assert await repo.get(11, 42, identity()) is None
    assert [m.identity for m in await repo.favorites(10, 42)] == [identity()]
    assert await repo.favorites(10, 43) == []
    assert await repo.favorites(11, 42) == []


async def test_unknown_project_has_no_metadata_and_can_be_cleared_safely(repo):
    assert await repo.get(10, 42, identity("never")) is None
    # Clearing a flag on a project that was never stored is a no-op, not an error.
    await repo.set_favorite(10, 42, identity("never"), False)
    await repo.set_hidden(10, 42, identity("never"), False)
    assert await repo.get(10, 42, identity("never")) is None
    assert await repo.list_for_user(10, 42) == {}


async def test_concurrent_updates_keep_every_flag(repo):
    await asyncio.gather(
        repo.set_favorite(10, 42, identity(), True),
        repo.set_hidden(10, 42, identity(), True),
        repo.record_recent(10, 42, identity(), now=NOW),
        *(repo.set_favorite(10, 42, identity(f"p{i}"), True) for i in range(8)),
    )
    stored = await repo.get(10, 42, identity())
    assert stored is not None
    assert stored.favorite and stored.hidden and stored.last_opened_at == NOW
    assert len(await repo.favorites(10, 42)) == 9


async def test_recents_are_newest_first_and_bounded(repo):
    for index in range(5):
        await repo.record_recent(10, 42, identity(f"p{index}"), now=NOW + timedelta(minutes=index))
    recent = await repo.recents(10, 42, limit=3)
    assert [m.identity.name for m in recent] == ["p4", "p3", "p2"]
    # Re-opening moves a project to the front without duplicating it.
    await repo.record_recent(10, 42, identity("p0"), now=NOW + timedelta(hours=1))
    assert [m.identity.name for m in await repo.recents(10, 42)][:2] == ["p0", "p4"]
    assert len(await repo.recents(10, 42)) == 5


async def test_same_name_in_two_roots_are_two_metadata_rows(repo):
    await repo.set_favorite(10, 42, identity("alpha", root_key="main"), True)
    await repo.set_hidden(10, 42, identity("alpha", root_key="work"), True)
    main = await repo.get(10, 42, identity("alpha", root_key="main"))
    work = await repo.get(10, 42, identity("alpha", root_key="work"))
    assert main is not None and main.favorite and not main.hidden
    assert work is not None and work.hidden and not work.favorite


async def test_metadata_survives_absence_and_is_reapplied_on_lookup(repo):
    """The repository never deletes on absence: a returned folder finds its flags."""
    await repo.set_favorite(10, 42, identity(), True)
    await repo.set_hidden(10, 42, identity(), True)
    # Nothing here says the folder exists; a later query for the same identity
    # gets the same answer.
    stored = await repo.list_for_user(10, 42)
    assert stored[identity()].favorite and stored[identity()].hidden
    await repo.set_hidden(10, 42, identity(), False)
    again = await repo.get(10, 42, identity())
    assert again is not None and again.favorite and not again.hidden


async def test_rows_that_carry_nothing_are_removed(repo):
    await repo.set_favorite(10, 42, identity(), True)
    await repo.set_favorite(10, 42, identity(), False)
    assert await repo.get(10, 42, identity()) is None
    assert await repo.count(10, 42) == 0


async def test_malformed_stored_key_is_skipped_not_raised(repo):
    import aiosqlite

    async with aiosqlite.connect(repo.db_path) as db:
        await db.execute(
            "INSERT INTO project_catalog_metadata "
            "(guild_id, user_id, project_key, favorite, hidden, updated_at) "
            "VALUES (10, 42, 'not-a-key', 1, 0, '2026-01-01T00:00:00+00:00')"
        )
        await db.commit()
    await repo.set_favorite(10, 42, identity(), True)
    assert [m.identity for m in await repo.favorites(10, 42)] == [identity()]
