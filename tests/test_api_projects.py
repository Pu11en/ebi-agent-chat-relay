"""REST catalog operations (OpenSpec shared-project-catalog task 3.2).

``GET /api/projects`` (bounded list/search), ``POST /api/projects/resolve``
(one typed resolution) and ``GET /api/projects/{key}`` (revalidated find) sit
on the localhost control plane, behind the same Bearer middleware as every
other endpoint. They answer with project *metadata* only — never a folder's
contents — and a malformed request is refused before the catalog is asked.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.catalog_config import CatalogConfig
from claude_discord.catalog_service import ProjectCatalogService
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.project_catalog_repo import ProjectCatalogRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.project_catalog import MAX_QUERY_LIMIT, ProjectIdentity

SECRET_LINE = "TOP-SECRET-FILE-CONTENT-9f3a"
NESTED_FOLDER = "nested-folder-must-not-leak"


@pytest.fixture
async def db_path() -> AsyncIterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def roots(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    work = tmp_path / "work"
    for name in ("alpha", "beta", "gamma"):
        (main / name).mkdir(parents=True)
    (work / "alpha").mkdir(parents=True)
    (main / "alpha" / "README.md").write_text(SECRET_LINE, encoding="utf-8")
    (main / "alpha" / NESTED_FOLDER).mkdir()
    (main / "notes.txt").write_text("not a project", encoding="utf-8")
    return tmp_path


@pytest.fixture
async def catalog(roots: Path, db_path: str) -> ProjectCatalogService:
    config = CatalogConfig.from_env(
        {
            "CCDB_PROJECT_ROOTS": f"{roots / 'main'},{roots / 'work'},{roots / 'missing'}",
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
            "CCDB_CATALOG_COMPUTERS": "david/davidpc",
        }
    )
    return ProjectCatalogService(config, ProjectCatalogRepository(db_path), cache_ttl=0)


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.cogs = {}
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    bot.guilds = []
    return bot


async def _make_api(db_path: str, catalog: ProjectCatalogService | None, **kw: object) -> ApiServer:
    notif_repo = NotificationRepository(db_path)
    await notif_repo.init_db()
    api = ApiServer(
        repo=notif_repo,
        bot=_bot(),
        default_channel_id=12345,
        host="127.0.0.1",
        port=0,
        **kw,  # type: ignore[arg-type]
    )
    api.project_catalog = catalog
    return api


@pytest.fixture
async def api_client(db_path: str, catalog: ProjectCatalogService) -> AsyncIterator[TestClient]:
    api = await _make_api(db_path, catalog)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()


# --- GET /api/projects -----------------------------------------------------


async def test_list_returns_every_direct_child_with_labels_roots_and_no_contents(
    api_client: TestClient,
) -> None:
    resp = await api_client.get("/api/projects")
    assert resp.status == 200
    body = await resp.json()
    assert body["computer"] == "drew/drewai"
    assert [p["label"] for p in body["projects"]] == [
        "alpha (main)",
        "alpha (work)",
        "beta",
        "gamma",
    ]
    assert all(p["availability"] == "available" for p in body["projects"])
    assert {r["key"]: r["availability"] for r in body["roots"]} == {
        "main": "available",
        "work": "available",
        "missing": "missing",
    }
    assert body["truncated"] is False
    text = await resp.text()
    assert SECRET_LINE not in text
    assert NESTED_FOLDER not in text
    assert "notes.txt" not in text
    assert "README" not in text


async def test_search_is_bounded_and_marks_truncation(api_client: TestClient) -> None:
    resp = await api_client.get("/api/projects", params={"q": "alp", "limit": "1"})
    assert resp.status == 200
    body = await resp.json()
    assert len(body["projects"]) == 1
    assert body["truncated"] is True
    resp = await api_client.get("/api/projects", params={"q": "zzz"})
    assert (await resp.json())["projects"] == []


async def test_list_with_a_user_applies_their_favorites_and_hidden_state(
    api_client: TestClient, catalog: ProjectCatalogService
) -> None:
    gamma = ProjectIdentity("drew", "drewai", "main", "gamma")
    beta = ProjectIdentity("drew", "drewai", "main", "beta")
    await catalog.set_favorite(10, 42, gamma.key, True)
    await catalog.set_hidden(10, 42, beta.key, True)
    resp = await api_client.get("/api/projects", params={"guild_id": "10", "user_id": "42"})
    body = await resp.json()
    labels = [p["label"] for p in body["projects"]]
    assert labels == ["gamma", "alpha (main)", "alpha (work)"]
    assert body["projects"][0]["favorite"] is True
    # A search by that user still finds the hidden project, flagged.
    resp = await api_client.get(
        "/api/projects", params={"guild_id": "10", "user_id": "42", "q": "beta"}
    )
    assert [(p["label"], p["hidden"]) for p in (await resp.json())["projects"]] == [("beta", True)]
    # Nobody else's view is affected.
    resp = await api_client.get("/api/projects")
    assert [p["favorite"] for p in (await resp.json())["projects"]] == [False] * 4


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "abc"},
        {"limit": str(MAX_QUERY_LIMIT + 1)},
        {"q": "x" * 201},
        {"owner": "!!"},
        {"owner": "x" * 65},
        {"guild_id": "10"},
        {"guild_id": "ten", "user_id": "42"},
        {"refresh": "maybe"},
    ],
)
async def test_list_rejects_malformed_queries(api_client: TestClient, params: dict) -> None:
    resp = await api_client.get("/api/projects", params=params)
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_owner_scope_limits_to_that_owner_or_says_remote(api_client: TestClient) -> None:
    resp = await api_client.get("/api/projects", params={"owner": "drew"})
    assert len((await resp.json())["projects"]) == 4
    resp = await api_client.get("/api/projects", params={"owner": "david"})
    assert resp.status == 200
    assert (await resp.json())["projects"] == []


# --- POST /api/projects/resolve --------------------------------------------


async def test_resolve_local_remote_ambiguous_and_no_match(api_client: TestClient) -> None:
    resp = await api_client.post("/api/projects/resolve", json={"text": "beta"})
    assert resp.status == 200
    body = await resp.json()
    assert body["kind"] == "local_available"
    assert body["path"].endswith("beta") and body["locally_verified"] is True

    resp = await api_client.post("/api/projects/resolve", json={"text": "David's alpha"})
    body = await resp.json()
    assert body["kind"] == "remote_target"
    assert body["computer"] == "davidpc"
    assert "path" not in body and body["locally_verified"] is False

    resp = await api_client.post("/api/projects/resolve", json={"text": "alpha"})
    body = await resp.json()
    assert body["kind"] == "ambiguous_owner"
    assert len(body["candidates"]) == 2

    resp = await api_client.post("/api/projects/resolve", json={"text": "nothing-here"})
    assert (await resp.json())["kind"] == "no_match"

    resp = await api_client.post("/api/projects/resolve", json={"text": "alpha", "owner": "drew"})
    body = await resp.json()
    assert body["kind"] == "ambiguous_owner"  # two local alphas: ask which, never guess


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        [],
        {},
        {"text": ""},
        {"text": 5},
        {"text": "x" * 201},
        {"text": "alpha", "owner": 7},
        {"text": "alpha", "owner": "!!"},
    ],
)
async def test_resolve_rejects_malformed_bodies(api_client: TestClient, body: object) -> None:
    if isinstance(body, str):
        resp = await api_client.post(
            "/api/projects/resolve", data=body, headers={"Content-Type": "application/json"}
        )
    else:
        resp = await api_client.post("/api/projects/resolve", json=body)
    assert resp.status == 400


# --- GET /api/projects/{key} -----------------------------------------------


async def test_find_revalidates_and_reports_unavailable_without_a_working_directory(
    api_client: TestClient, roots: Path
) -> None:
    key = ProjectIdentity("drew", "drewai", "main", "beta").key
    resp = await api_client.get(f"/api/projects/{key}")
    assert resp.status == 200
    body = await resp.json()
    assert body["availability"] == "available"
    assert body["working_directory"] == str(roots / "main" / "beta")
    (roots / "main" / "beta").rmdir()
    resp = await api_client.get(f"/api/projects/{key}")
    assert resp.status == 200
    body = await resp.json()
    assert body["availability"] == "missing" and body["working_directory"] is None


@pytest.mark.parametrize(
    "key",
    ["garbage", "drew:drewai:other:alpha", "david:davidpc:main:alpha", "drew:drewai:main:.."],
)
async def test_find_refuses_keys_this_computer_never_issued(
    api_client: TestClient, key: str
) -> None:
    resp = await api_client.get(f"/api/projects/{key}")
    assert resp.status == 404


# --- trust boundary ---------------------------------------------------------


async def test_catalog_endpoints_require_the_bearer_token_when_one_is_set(
    db_path: str, catalog: ProjectCatalogService
) -> None:
    api = await _make_api(db_path, catalog, api_secret="s3cret")
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        assert (await client.get("/api/projects")).status == 401
        assert (await client.post("/api/projects/resolve", json={"text": "beta"})).status == 401
        assert (await client.get("/api/projects/drew:drewai:main:beta")).status == 401
        ok = await client.get("/api/projects", headers={"Authorization": "Bearer s3cret"})
        assert ok.status == 200
    finally:
        await client.close()


async def test_catalog_endpoints_answer_503_without_a_catalog(db_path: str) -> None:
    api = await _make_api(db_path, None)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        assert (await client.get("/api/projects")).status == 503
        assert (await client.post("/api/projects/resolve", json={"text": "beta"})).status == 503
        assert (await client.get("/api/projects/drew:drewai:main:beta")).status == 503
    finally:
        await client.close()


async def test_catalog_endpoints_never_leave_localhost(
    db_path: str, catalog: ProjectCatalogService
) -> None:
    api = await _make_api(db_path, catalog)
    external = {resource.canonical for resource in api.external_app.router.resources()}
    assert not any(path.startswith("/api/projects") for path in external)
