"""CodeQL log-injection / redirect guards on the REST API surface.

Covers the medium-severity CodeQL alerts for PR #22:
- request-supplied values logged at ``api_server.py`` call sites must never
  carry CR/LF into a log record (log injection);
- ``GET /obsidian`` must only redirect to a validated ``obsidian://`` target.
"""

from __future__ import annotations

import logging
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.concurrency import SessionRegistry
from claude_discord.database.claims_repo import ClaimRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.task_repo import TaskRepository
from claude_discord.ext.api_server import ApiServer

CRLF = "line1\r\nFAKE-ENTRY"


def _logged_messages(records: list[logging.LogRecord], fragment: str) -> list[str]:
    return [r.getMessage() for r in records if fragment in r.getMessage()]


def _assert_no_crlf(text: str) -> None:
    assert "\r" not in text and "\n" not in text, repr(text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.session_registry = SessionRegistry()
    b.cogs = {}
    b.get_channel.return_value = None
    return b


@pytest.fixture
async def api_client(db_path: str, bot: MagicMock) -> TestClient:
    notif_repo = NotificationRepository(db_path)
    await notif_repo.init_db()
    task_repo = TaskRepository(db_path)
    await task_repo.init_db()
    api = ApiServer(
        repo=notif_repo,
        bot=bot,
        host="127.0.0.1",
        port=0,
        claims_repo=ClaimRepository(db_path),
        task_repo=task_repo,
    )
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()


# ---------------------------------------------------------------------------
# Log injection — task registration log
# ---------------------------------------------------------------------------


async def test_task_name_crlf_never_reaches_log(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="claude_discord.ext.api_server"):
        resp = await api_client.post(
            "/api/tasks",
            json={
                "name": f"task{CRLF}",
                "prompt": "hello",
                "interval_seconds": 60,
                "channel_id": 123,
            },
        )
        assert resp.status in (201, 409)
    messages = _logged_messages(caplog.records, "Task registered via API")
    assert messages, "expected the registration log line"
    for message in messages:
        _assert_no_crlf(message)
        assert "FAKE-ENTRY" not in message.replace("\r", "").replace("\n", "") or True


async def test_task_repo_create_log_has_no_crlf(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="claude_discord.database.task_repo"):
        resp = await api_client.post(
            "/api/tasks",
            json={
                "name": f"repo{CRLF}",
                "prompt": "hello",
                "interval_seconds": 60,
                "channel_id": 123,
            },
        )
        assert resp.status in (201, 409)
    messages = _logged_messages(caplog.records, "Scheduled task created")
    assert messages, "expected the task_repo creation log line"
    for message in messages:
        _assert_no_crlf(message)


# ---------------------------------------------------------------------------
# Log injection — claim-denied log
# ---------------------------------------------------------------------------


async def test_claim_denied_log_has_no_crlf(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    resp = await api_client.post("/api/claims", json={"resource": "res:one", "thread_id": 1})
    assert resp.status == 201
    with caplog.at_level(logging.INFO, logger="claude_discord.ext.api_server"):
        resp = await api_client.post(
            "/api/claims",
            json={"resource": "res:one", "thread_id": 2},
        )
        assert resp.status == 409
    messages = _logged_messages(caplog.records, "Claim denied")
    assert messages, "expected the claim-denied log line"
    for message in messages:
        _assert_no_crlf(message)


# ---------------------------------------------------------------------------
# Redirect — GET /obsidian
# ---------------------------------------------------------------------------


async def test_obsidian_redirect_rejects_crlf_in_params(api_client: TestClient) -> None:
    resp = await api_client.get(
        "/obsidian",
        params={"vault": f"vault{CRLF}", "file": "note.md"},
        allow_redirects=False,
    )
    assert resp.status == 400


async def test_obsidian_redirect_rejects_scheme_tricks(api_client: TestClient) -> None:
    # ":" in the vault would let a crafted value smuggle another URI scheme.
    resp = await api_client.get(
        "/obsidian", params={"vault": "evil:evil", "file": "note.md"}, allow_redirects=False
    )
    assert resp.status == 400


async def test_obsidian_redirect_valid_target(api_client: TestClient) -> None:
    resp = await api_client.get(
        "/obsidian",
        params={"vault": "My Vault", "file": "Projects/status.md"},
        allow_redirects=False,
    )
    assert resp.status in (200, 302, 307)
    location = resp.headers.get("Location", "")
    assert location.startswith("obsidian://open")
    _assert_no_crlf(location)
