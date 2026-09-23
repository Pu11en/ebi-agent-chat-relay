"""Tests for handoff status summaries and API view."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.handoffs.state import HandoffTrigger, apply
from claude_discord.database.handoff_repo import HandoffRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.handoff_sender import build_project_lookup_handoff_event
from claude_discord.handoff_status import load_handoff_status, render_handoff_status
from claude_discord.handoff_triggers import DrewAILookupTrigger

NOW = datetime(2026, 9, 21, 0, 45, tzinfo=UTC)


@pytest.fixture
async def handoff_repo() -> AsyncIterator[HandoffRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield HandoffRepository(path)
    finally:
        os.unlink(path)


def _origin_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=444,
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, parent_id=222),
        author=SimpleNamespace(id=555),
    )


async def _record_lookup(
    repo: HandoffRepository,
    *,
    query: str,
    task_id: str,
    event_id: str,
    state: str,
) -> None:
    event = build_project_lookup_handoff_event(
        DrewAILookupTrigger(query),
        origin_message=_origin_message(),
        sender_agent_id="david",
        now=NOW,
        id_factory=lambda: (task_id, event_id),
    )
    assert event.task is not None
    await repo.record_task(event.task, now=NOW)
    await repo.record_event(event)
    job = await repo.get_job(task_id, "drewai")
    assert job is not None
    if state in {"running", "completed"}:
        await repo.save_transition(apply(job, HandoffTrigger.START, now=NOW))
        await repo.claim_attempt(task_id, "drewai", attempt=1, now=NOW)
    if state == "completed":
        job = await repo.get_job(task_id, "drewai")
        assert job is not None
        await repo.save_transition(apply(job, HandoffTrigger.COMPLETE, now=NOW))
        await repo.finish_attempt(task_id, "drewai", attempt=1, outcome="completed", now=NOW)


@pytest.mark.asyncio
async def test_render_handoff_status_shows_counts_and_recent_items(
    handoff_repo: HandoffRepository,
) -> None:
    await _record_lookup(
        handoff_repo,
        query="Pinterest process",
        task_id="11111111-1111-4111-8111-111111111111",
        event_id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
        state="accepted",
    )
    await _record_lookup(
        handoff_repo,
        query="Realpage folder",
        task_id="22222222-2222-4222-8222-222222222222",
        event_id="bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb",
        state="running",
    )
    await _record_lookup(
        handoff_repo,
        query="handoff_return.py",
        task_id="33333333-3333-4333-8333-333333333333",
        event_id="cccccccc-3333-4333-8333-cccccccccccc",
        state="completed",
    )

    status = await load_handoff_status(handoff_repo, recipient="drewai", limit=10)
    text = render_handoff_status(status)

    assert status.counts == {"accepted": 1, "running": 1, "completed": 1}
    assert "accepted: 1" in text
    assert "running: 1" in text
    assert "completed: 1" in text
    assert "Pinterest process" in text
    assert "Realpage folder" in text
    assert "handoff_return.py" in text


@pytest.mark.asyncio
async def test_handoff_status_api_returns_json(
    handoff_repo: HandoffRepository,
    tmp_path,
) -> None:
    notification_repo = NotificationRepository(str(tmp_path / "notifications.db"))
    await notification_repo.init_db()
    await _record_lookup(
        handoff_repo,
        query="Pinterest process",
        task_id="11111111-1111-4111-8111-111111111111",
        event_id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa",
        state="running",
    )
    api = ApiServer(repo=notification_repo, bot=MagicMock(), default_channel_id=123)
    api.handoff_repo = handoff_repo
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    try:
        resp = await client.get("/api/handoffs/status?recipient=drewai")
        body = await resp.json()
    finally:
        await client.close()

    assert resp.status == 200
    assert body["counts"]["running"] == 1
    assert body["items"][0]["recipient"] == "drewai"
    assert "Pinterest process" in body["text"]
