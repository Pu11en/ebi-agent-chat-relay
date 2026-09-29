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


class CodexRunner:
    """Named like the real runner so identity is attributed to Codex."""

    def __init__(self, working_dir: str | None) -> None:
        self.working_dir = working_dir
        self.model = "codex-model"
        self.interrupt = AsyncMock()


async def test_late_result_from_an_evicted_run_cannot_replace_a_newer_binding(
    identity_repo: SessionRepository,
) -> None:
    """Task 2.3: a stale success must not undo a switch that happened meanwhile."""
    surface = MemorySurface()
    await identity_repo.save(surface.thread_key, "old-codex-id", backend="codex")
    stale = EventProcessor(
        RunConfig(
            surface=surface,
            runner=CodexRunner(surface.working_dir),
            prompt="Older queued reply",
            repo=identity_repo,
            session_id="old-codex-id",
            chat_only=True,
        )
    )
    # The newer Claude handoff binds its own conversation while the old run lingers.
    newer = _processor(identity_repo, surface)
    await newer.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="new-claude-id"))
    bound = await identity_repo.get(surface.thread_key)

    await stale.process(
        StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            session_id="old-codex-id",
            text="Late answer",
        )
    )
    await stale.finalize()
    await newer.finalize()

    record = await SessionRepository(identity_repo.db_path).get(surface.thread_key)
    assert record is not None and bound is not None
    assert (record.session_id, record.backend) == ("new-claude-id", "claude")


async def test_resumed_run_may_advance_its_own_binding(identity_repo: SessionRepository) -> None:
    """Control: the compare-and-set only rejects a binding another run replaced."""
    surface = MemorySurface()
    await identity_repo.save(surface.thread_key, "resumed-id", backend="claude")
    processor = _processor(identity_repo, surface, "resumed-id")
    await processor.process(
        StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            session_id="forked-id",
            text="Done",
        )
    )
    await processor.finalize()
    record = await identity_repo.get(surface.thread_key)
    assert record is not None and record.session_id == "forked-id"
    assert processor.session_id == "forked-id"


async def test_backend_switch_next_reply_and_reload_resume_the_new_binding(
    identity_repo: SessionRepository,
) -> None:
    """Design matrix row 1: A -> B -> next reply -> process reload."""
    import discord

    from claude_discord.cogs.claude_chat import ClaudeChatCog

    surface = MemorySurface()
    thread_id = surface.thread_key
    await identity_repo.save(thread_id, "codex-a", backend="codex")
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.send = AsyncMock()

    def chat(repo: SessionRepository) -> ClaudeChatCog:
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="claude")
        history = MagicMock()
        history.read.return_value = "User:\nold question\n\nAssistant:\nold answer"
        return ClaudeChatCog(
            bot=MagicMock(),
            repo=repo,
            runner=MagicMock(),
            backend_settings=settings,
            conversation_history=history,
        )

    first = chat(identity_repo)
    resume, prompt = await first._prepare_cross_backend_handoff(thread, "continue", "codex-a")
    assert resume is None and "old answer" in prompt
    first._conversation_history.read.assert_called_once_with("codex", "codex-a")
    handoff = _processor(identity_repo, surface, resume)
    await handoff.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="claude-b"))
    await handoff.process(
        StreamEvent(
            message_type=MessageType.RESULT, is_complete=True, session_id="claude-b", text="ok"
        )
    )
    await handoff.finalize()

    reloaded_repo = SessionRepository(identity_repo.db_path)  # a new process
    record = await reloaded_repo.get(thread_id)
    assert record is not None and (record.session_id, record.backend) == ("claude-b", "claude")
    reloaded = chat(reloaded_repo)
    resume, prompt = await reloaded._prepare_cross_backend_handoff(
        thread, "next reply", record.session_id
    )
    assert (resume, prompt) == ("claude-b", "next reply"), "native resume, no second handoff"
    reloaded._conversation_history.read.assert_not_called()
