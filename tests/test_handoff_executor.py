"""Tests for starting project lookup workers from accepted handoff tasks."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claude_code_core.handoffs.state import HandoffState
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.handoff_repo import HandoffRepository
from claude_discord.database.models import init_db
from claude_discord.handoff_executor import execute_ready_handoff_tasks
from claude_discord.handoff_messages import format_handoff_message
from claude_discord.handoff_sender import build_project_lookup_handoff_event
from claude_discord.handoff_triggers import DrewAILookupTrigger

NOW = datetime(2026, 9, 20, 23, 50, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


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


async def _record_task(repo: HandoffRepository) -> None:
    event = _handoff_event()
    assert event.task is not None
    await repo.record_task(event.task, now=NOW)
    await repo.record_event(event)


@pytest.mark.asyncio
async def test_execute_ready_handoff_tasks_starts_lookup_worker_and_records_thread(
    handoff_repo: HandoffRepository,
    project_root: str,
) -> None:
    await _record_task(handoff_repo)
    parent_channel = SimpleNamespace(id=777)
    worker_thread = SimpleNamespace(id=999, name="🔎 Project lookup")
    chat = SimpleNamespace(spawn_session=AsyncMock(return_value=worker_thread))

    results = await execute_ready_handoff_tasks(
        repo=handoff_repo,
        chat=chat,
        parent_channel=parent_channel,
        local_agent_id="drewai",
        now=NOW,
    )

    assert len(results) == 1
    assert results[0].task_id == TASK_ID
    assert results[0].thread_id == 999
    chat.spawn_session.assert_awaited_once()
    channel, prompt = chat.spawn_session.await_args.args
    assert channel is parent_channel
    assert "DrewAI's project lookup worker" in prompt
    assert "Pinterest visual picker process" in prompt
    assert project_root in prompt
    assert chat.spawn_session.await_args.kwargs["working_dir"] == project_root
    assert chat.spawn_session.await_args.kwargs["auto_start"] is True
    assert "Project lookup" in chat.spawn_session.await_args.kwargs["thread_name"]
    assert chat.spawn_session.await_args.kwargs["backend"] == "claude"
    assert await handoff_repo.get_job_thread(TASK_ID, "drewai") == 999
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.RUNNING


@pytest.mark.asyncio
async def test_execute_ready_handoff_tasks_does_not_start_running_job_twice(
    handoff_repo: HandoffRepository,
    project_root: str,
) -> None:
    await _record_task(handoff_repo)
    parent_channel = SimpleNamespace(id=777)
    chat = SimpleNamespace(
        spawn_session=AsyncMock(return_value=SimpleNamespace(id=999, name="lookup"))
    )

    first = await execute_ready_handoff_tasks(
        repo=handoff_repo,
        chat=chat,
        parent_channel=parent_channel,
        local_agent_id="drewai",
        now=NOW,
    )
    second = await execute_ready_handoff_tasks(
        repo=handoff_repo,
        chat=chat,
        parent_channel=parent_channel,
        local_agent_id="drewai",
        now=NOW,
    )

    assert len(first) == 1
    assert second == []
    assert chat.spawn_session.await_count == 1
    attempts = await handoff_repo.list_attempts(TASK_ID, "drewai")
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_execute_ready_handoff_tasks_uses_only_local_agent_jobs(
    handoff_repo: HandoffRepository,
    project_root: str,
) -> None:
    await _record_task(handoff_repo)
    chat = SimpleNamespace(spawn_session=AsyncMock())

    results = await execute_ready_handoff_tasks(
        repo=handoff_repo,
        chat=chat,
        parent_channel=SimpleNamespace(id=777),
        local_agent_id="imac",
        now=NOW,
    )

    assert results == []
    chat.spawn_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_cog_starts_executor_after_receiving_handoff(
    handoff_repo: HandoffRepository,
    project_root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CCDB_AGENT_ID", "drewai")
    parent_channel = SimpleNamespace(id=222)
    message = SimpleNamespace(
        content=format_handoff_message(_handoff_event()),
        channel=SimpleNamespace(id=777, parent=parent_channel, send=AsyncMock()),
    )
    seen: dict[str, object] = {}

    async def fake_execute_ready_handoff_tasks(**kwargs: object) -> list[object]:
        seen.update(kwargs)
        return [SimpleNamespace(task_id=TASK_ID, thread_id=999)]

    monkeypatch.setattr(
        "claude_discord.cogs.claude_chat.execute_ready_handoff_tasks",
        fake_execute_ready_handoff_tasks,
    )
    cog = SimpleNamespace(
        _handoff_repo=handoff_repo,
        _handoff_worker_parent_channel=lambda message: parent_channel,
    )

    handled = await ClaudeChatCog._try_receive_handoff_message(cog, message)

    assert handled is True
    assert seen["repo"] is handoff_repo
    assert seen["chat"] is cog
    assert seen["parent_channel"] is parent_channel
    assert seen["local_agent_id"] == "drewai"
