"""Native identity must survive rejected resumes without any real model calls."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_core.frontend import NoticeLevel, StatusKind
from claude_code_core.memory_surface import MemorySurface
from claude_discord.claude.types import MessageType, StreamEvent
from claude_discord.cogs.event_processor import EventProcessor
from claude_discord.cogs.run_config import RunConfig
from claude_discord.database.models import init_db
from claude_discord.database.repository import SessionRepository


@pytest.fixture
async def identity_repo(tmp_path: Path) -> SessionRepository:
    path = str(tmp_path / "sessions.db")
    await init_db(path)
    return SessionRepository(path)


def _processor(
    repo: SessionRepository,
    surface: MemorySurface,
    session_id: str | None = None,
) -> EventProcessor:
    runner = MagicMock()
    runner.interrupt = AsyncMock()
    runner.working_dir = surface.working_dir
    runner.model = "test-model"
    return EventProcessor(
        RunConfig(
            surface=surface,
            runner=runner,
            prompt="Continue the existing task",
            repo=repo,
            session_id=session_id,
            chat_only=True,
        )
    )


def _rejected(session_id: str) -> StreamEvent:
    return StreamEvent(
        message_type=MessageType.RESULT,
        is_complete=True,
        session_id=session_id,
        error=f"No conversation found with session ID: {session_id}",
    )


@pytest.mark.parametrize("stored_backend", ["codex", None])
async def test_rejected_resume_cannot_claim_another_backends_id(
    identity_repo: SessionRepository, stored_backend: str | None
) -> None:
    """A failure echo proves neither ownership nor legacy ID provenance."""
    surface = MemorySurface()
    before = await identity_repo.save(
        surface.thread_key, "old-native-id", backend=stored_backend, summary="Keep history"
    )
    processor = _processor(identity_repo, surface, "old-native-id")

    await processor.process(_rejected("old-native-id"))
    await processor.finalize()

    assert await identity_repo.get(surface.thread_key) == before
    assert processor.final_error == "No conversation found with session ID: old-native-id"
    assert surface.statuses[-1] is StatusKind.ERROR
    assert surface.notices[-1].level is NoticeLevel.ERROR


async def test_rejected_echo_after_init_preserves_verified_binding(
    identity_repo: SessionRepository,
) -> None:
    surface = MemorySurface()
    await identity_repo.save(surface.thread_key, "old-native-id", backend="codex")
    processor = _processor(identity_repo, surface)
    await processor.process(
        StreamEvent(message_type=MessageType.SYSTEM, session_id="new-claude-id")
    )
    before = await identity_repo.get(surface.thread_key)

    await processor.process(_rejected("old-native-id"))
    await processor.finalize()

    # Check fresh repository access as well as the in-memory resume ID.
    reopened_repo = SessionRepository(identity_repo.db_path)
    assert await reopened_repo.get(surface.thread_key) == before
    assert processor.session_id == "new-claude-id"
    following_turn = _processor(reopened_repo, surface, processor.session_id)
    assert following_turn.session_id == "new-claude-id"
    await following_turn.finalize()


async def test_rejected_id_without_init_does_not_create_a_binding(
    identity_repo: SessionRepository,
) -> None:
    surface = MemorySurface()
    processor = _processor(identity_repo, surface)

    await processor.process(_rejected("unknown-id"))
    await processor.finalize()

    assert await identity_repo.get(surface.thread_key) is None
    assert processor.session_id is None


@pytest.mark.parametrize("with_init", [False, True])
async def test_success_still_persists_and_resumes_identity(
    identity_repo: SessionRepository, with_init: bool
) -> None:
    surface = MemorySurface()
    processor = _processor(identity_repo, surface)
    if with_init:
        await processor.process(
            StreamEvent(message_type=MessageType.SYSTEM, session_id="new-claude-id")
        )
    await processor.process(
        StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            session_id="new-claude-id",
            text="Finished",
        )
    )
    await processor.finalize()

    record = await SessionRepository(identity_repo.db_path).get(surface.thread_key)
    assert record is not None
    assert (record.session_id, record.backend) == ("new-claude-id", "claude")
    assert processor.session_id == record.session_id
    assert surface.conformance_sent_text == ["Finished"]
