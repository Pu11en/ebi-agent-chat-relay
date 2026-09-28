"""The owner turn feed reads a bounded journal page without changing EBI state."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.session_repo import SessionRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer


@pytest.mark.asyncio
async def test_jester_turn_feed_is_bounded_cursor_read_only(tmp_path) -> None:
    db_path = str(tmp_path / "sessions.db")
    await init_db(db_path)
    with sqlite3.connect(db_path) as db:
        for key, stamp, state, next_at, expiry in [
            ("discord:1:a", "2026-09-28T12:00:01+00:00", "accepted", "future", "end"),
            ("discord:2:b", "2026-09-28T12:00:02+00:00", "scheduled", "end", "end"),
            ("discord:3:c", "2026-09-28T12:00:03+00:00", "running", "future", "end"),
        ]:
            db.execute(
                "INSERT INTO capacity_pending_turns "
                "(turn_key, frontend, thread_id, prompt_ref, backend, state, "
                "next_attempt_at, expires_at, created_at, updated_at) "
                "VALUES (?, 'discord', ?, 'ref', 'codex', ?, ?, ?, ?, ?)",
                (key, int(key.split(":")[1]), state, next_at, expiry, stamp, stamp),
            )
    api = ApiServer(repo=NotificationRepository(db_path), bot=MagicMock())
    api.session_repo = SessionRepository(db_path)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        first = await client.get("/api/jester/turns?since=2026-09-28T12:00:00%2B00:00&limit=2")
        assert first.status == 200
        page = await first.json()
        assert [t["thread_id"] for t in page["turns"]] == ["1", "2"]
        assert page["turns"][1]["parked"] is True
        assert page["has_more"] is True
        cursor = page["next"]
        second = await client.get(
            "/api/jester/turns", params={"since": cursor["since"], "after": cursor["after"]}
        )
        assert [t["thread_id"] for t in (await second.json())["turns"]] == ["3"]
        assert (await client.get("/api/jester/turns?since=bad")).status == 400
    finally:
        await client.close()
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM capacity_pending_turns").fetchone()[0] == 3
