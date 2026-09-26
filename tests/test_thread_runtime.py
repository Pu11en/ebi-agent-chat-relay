"""Tests for GET/POST /api/threads/{id}/runtime — which agent answers a thread.

The voice surface has no slash commands, so this is its route to the same
selection ``/backend`` and ``/model`` write.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.backend_settings import BackendSettings
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.ext.api_server import ApiServer

THREAD = 4242
OTHER = 5353


@pytest.fixture
async def api_client() -> TestClient:
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db)
    repo = NotificationRepository(db)
    await repo.init_db()
    api = ApiServer(repo=repo, bot=MagicMock(), host="127.0.0.1", port=0)
    api.backend_settings = BackendSettings(
        SettingsRepository(db),
        env_backend="claude",
        env_model_for_claude="sonnet",
        env_model_for_codex="gpt-5-codex",
    )
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()
    os.unlink(db)


async def test_reading_the_runtime_falls_back_to_the_configured_default(
    api_client: TestClient,
) -> None:
    resp = await api_client.get(f"/api/threads/{THREAD}/runtime")

    assert resp.status == 200
    body = await resp.json()
    assert body["backend"] == "claude"
    assert body["model"] == "sonnet"
    assert "codex" in body["backends"]


async def test_a_model_alone_switches_the_model(api_client: TestClient) -> None:
    resp = await api_client.post(f"/api/threads/{THREAD}/runtime", json={"model": "opus"})

    assert resp.status == 200
    body = await resp.json()
    assert body["model"] == "opus"
    assert body["backend"] == "claude"
    assert body["applies"] == "next turn"


async def test_a_model_alone_applies_to_the_backend_the_thread_is_on(
    api_client: TestClient,
) -> None:
    """"Use gpt-5.1 here" means on whatever agent this thread already uses."""
    await api_client.post(f"/api/threads/{THREAD}/runtime", json={"backend": "codex"})
    await api_client.post(f"/api/threads/{THREAD}/runtime", json={"model": "gpt-5.1-codex"})

    body = await (await api_client.get(f"/api/threads/{THREAD}/runtime")).json()
    assert body["backend"] == "codex"
    assert body["model"] == "gpt-5.1-codex"


async def test_backend_and_model_can_be_set_together(api_client: TestClient) -> None:
    resp = await api_client.post(
        f"/api/threads/{THREAD}/runtime", json={"backend": "codex", "model": "gpt-5.1-codex"}
    )

    assert resp.status == 200
    body = await resp.json()
    assert (body["backend"], body["model"]) == ("codex", "gpt-5.1-codex")


async def test_the_change_is_scoped_to_one_thread(api_client: TestClient) -> None:
    await api_client.post(f"/api/threads/{THREAD}/runtime", json={"backend": "codex"})

    other = await (await api_client.get(f"/api/threads/{OTHER}/runtime")).json()
    assert other["backend"] == "claude", "a spoken switch must not move every thread"


async def test_a_model_name_is_free_text_not_an_allowlist(api_client: TestClient) -> None:
    """Model names change on every launch; validating them here would go stale."""
    resp = await api_client.post(
        f"/api/threads/{THREAD}/runtime", json={"model": "claude-opus-5-20990101"}
    )
    assert resp.status == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"backend": "telepathy"},
        {"model": "   "},
        {"model": "x" * 101},
    ],
)
async def test_bad_input_is_refused(api_client: TestClient, payload: dict) -> None:
    resp = await api_client.post(f"/api/threads/{THREAD}/runtime", json=payload)
    assert resp.status == 400


async def test_without_backend_settings_the_endpoint_says_so(api_client: TestClient) -> None:
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db)
    repo = NotificationRepository(db)
    await repo.init_db()
    api = ApiServer(repo=repo, bot=MagicMock(), host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        assert (await client.get(f"/api/threads/{THREAD}/runtime")).status == 503
        assert (
            await client.post(f"/api/threads/{THREAD}/runtime", json={"model": "opus"})
        ).status == 503
    finally:
        await client.close()
        os.unlink(db)
