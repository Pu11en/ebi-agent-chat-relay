"""Tests for returning handoff worker results to the origin thread."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_core.handoffs.state import HandoffState, HandoffTrigger, apply
from claude_discord.database.handoff_repo import HandoffRepository, OutboxStatus
from claude_discord.database.models import init_db
from claude_discord.handoff_executor import execute_ready_handoff_tasks
from claude_discord.handoff_return import record_and_deliver_handoff_result
from claude_discord.handoff_sender import build_project_lookup_handoff_event
from claude_discord.handoff_triggers import DrewAILookupTrigger

NOW = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"
RESULT_ID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"


@pytest.fixture
async def handoff_repo() -> AsyncIterator[HandoffRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield HandoffRepository(path)
    finally:
        os.unlink(path)


@pytest.fixture
def project_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = tmp_path / "main-projects"
    root.mkdir()
    monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(root))
    return str(root)


def _origin_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=444,
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, parent_id=222),
        author=SimpleNamespace(id=555),
    )


def _handoff_event():
    return build_project_lookup_handoff_event(
        DrewAILookupTrigger("the Pinterest visual picker process"),
        origin_message=_origin_message(),
        sender_agent_id="david",
        now=NOW,
        id_factory=lambda: (TASK_ID, EVENT_ID),
    )


async def _record_running_task(repo: HandoffRepository) -> None:
    event = _handoff_event()
    assert event.task is not None
    await repo.record_task(event.task, now=NOW)
    await repo.record_event(event)
    job = await repo.get_job(TASK_ID, "drewai")
    assert job is not None
    await repo.save_transition(apply(job, HandoffTrigger.START, now=NOW))
    await repo.claim_attempt(TASK_ID, "drewai", attempt=1, now=NOW)


@pytest.mark.asyncio
async def test_record_and_deliver_handoff_result_posts_to_origin_thread(
    handoff_repo: HandoffRepository,
) -> None:
    await _record_running_task(handoff_repo)
    origin_thread = SimpleNamespace(id=333, send=AsyncMock())
    bot = MagicMock()
    bot.get_channel.return_value = origin_thread

    delivered = await record_and_deliver_handoff_result(
        repo=handoff_repo,
        bot=bot,
        task_id=TASK_ID,
        local_agent_id="drewai",
        text="Found `/home/drewp/main-projects/youtube-money/.agents/skills/visual-picker`.",
        error=None,
        now=NOW,
        event_id_factory=lambda: RESULT_ID,
    )

    assert delivered is True
    result = await handoff_repo.get_result(TASK_ID, "drewai")
    assert result is not None
    assert result.outcome == "completed"
    assert "visual-picker" in (result.summary or "")
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.COMPLETED
    attempts = await handoff_repo.list_attempts(TASK_ID, "drewai")
    assert attempts[0].is_finished
    assert attempts[0].outcome == "completed"
    origin_thread.send.assert_awaited_once()
    assert "DrewAI result" in origin_thread.send.await_args.args[0]
    assert "visual-picker" in origin_thread.send.await_args.args[0]
    deliveries = await handoff_repo.pending_deliveries(now=NOW)
    assert deliveries == []


@pytest.mark.asyncio
async def test_record_and_deliver_handoff_result_archives_worker_thread_after_delivery(
    handoff_repo: HandoffRepository,
) -> None:
    await _record_running_task(handoff_repo)
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 999)
    origin_thread = SimpleNamespace(id=333, send=AsyncMock())
    worker_thread = SimpleNamespace(id=999, edit=AsyncMock())
    bot = MagicMock()
    bot.get_channel.side_effect = lambda channel_id: {
        333: origin_thread,
        999: worker_thread,
    }.get(channel_id)

    delivered = await record_and_deliver_handoff_result(
        repo=handoff_repo,
        bot=bot,
        task_id=TASK_ID,
        local_agent_id="drewai",
        text="Found the Pinterest visual-picker process.",
        error=None,
        now=NOW,
        event_id_factory=lambda: RESULT_ID,
    )

    assert delivered is True
    origin_thread.send.assert_awaited_once()
    worker_thread.edit.assert_awaited_once_with(archived=True, reason="handoff completed")


@pytest.mark.asyncio
async def test_record_and_deliver_handoff_result_keeps_worker_open_until_origin_receives_result(
    handoff_repo: HandoffRepository,
) -> None:
    await _record_running_task(handoff_repo)
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 999)
    worker_thread = SimpleNamespace(id=999, edit=AsyncMock())
    bot = MagicMock()
    bot.get_channel.side_effect = lambda channel_id: worker_thread if channel_id == 999 else None
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("missing"))

    delivered = await record_and_deliver_handoff_result(
        repo=handoff_repo,
        bot=bot,
        task_id=TASK_ID,
        local_agent_id="drewai",
        text="Found it.",
        error=None,
        now=NOW,
        event_id_factory=lambda: RESULT_ID,
    )

    assert delivered is False
    worker_thread.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_and_deliver_handoff_result_keeps_outbox_pending_when_origin_missing(
    handoff_repo: HandoffRepository,
) -> None:
    await _record_running_task(handoff_repo)
    bot = MagicMock()
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("missing"))

    delivered = await record_and_deliver_handoff_result(
        repo=handoff_repo,
        bot=bot,
        task_id=TASK_ID,
        local_agent_id="drewai",
        text="Found it.",
        error=None,
        now=NOW,
        event_id_factory=lambda: RESULT_ID,
    )

    assert delivered is False
    pending = await handoff_repo.pending_deliveries(now=NOW + timedelta(minutes=2))
    assert len(pending) == 1
    assert pending[0].status is OutboxStatus.PENDING


@pytest.mark.asyncio
async def test_executor_passes_result_sink_that_returns_worker_result(
    handoff_repo: HandoffRepository,
    project_root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _handoff_event()
    assert event.task is not None
    await handoff_repo.record_task(event.task, now=NOW)
    await handoff_repo.record_event(event)
    origin_thread = SimpleNamespace(id=333, send=AsyncMock())
    bot = MagicMock()
    bot.get_channel.return_value = origin_thread
    worker_thread = SimpleNamespace(id=999, name="lookup")
    chat = SimpleNamespace(
        bot=bot,
        spawn_session=AsyncMock(return_value=worker_thread),
    )
    monkeypatch.setattr(
        "claude_discord.handoff_return.uuid.uuid4",
        lambda: type("FakeUUID", (), {"__str__": lambda self: RESULT_ID})(),
    )

    await execute_ready_handoff_tasks(
        repo=handoff_repo,
        chat=chat,
        parent_channel=SimpleNamespace(id=777),
        local_agent_id="drewai",
        now=NOW,
    )

    sink = chat.spawn_session.await_args.kwargs["result_sink"]
    await sink("Found Pinterest skill files.", None)

    origin_thread.send.assert_awaited_once()
    assert "Found Pinterest skill files." in origin_thread.send.await_args.args[0]
