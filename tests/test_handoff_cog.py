"""Tests for the Discord side of trusted agent handoffs: rendering, the Cog, reconnect."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claude_code_core.handoffs import protocol as p
from claude_discord.handoff_discord import (
    DISCORD_MESSAGE_LIMIT,
    ensure_job_thread,
    job_thread_name,
    parse_event_message,
    post_task_starter,
    render_event_message,
    render_task_starter,
    short_task_id,
)
from claude_discord.handoff_messages import HandoffEnvelopeError, format_handoff_message

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
GUILD = 1001
CHANNEL = 2002
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"
EVENT_ID_2 = "1f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d43"


def make_task(
    sender: str = "drewai",
    recipient: str = "david",
    *,
    goal: str = "Find the visual picker skill",
    findings: tuple[str, ...] = (),
    authority: p.AuthorityScope | None = None,
    task_id: str = TASK_ID,
) -> p.HandoffTask:
    origin = p.ConversationCoordinate(guild_id=GUILD, channel_id=5005, thread_id=6006, message_id=7)
    return p.HandoffTask(
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        origin=origin,
        origin_human_id="777",
        project=p.ProjectLocator(owner="drew", folder="main-projects"),
        goal=goal,
        authority=authority or p.AuthorityScope(read=True),
        expected_result="Exact paths and a short summary.",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=6),
        findings=findings,
    )


def make_task_event(task: p.HandoffTask | None = None, event_id: str = EVENT_ID) -> p.HandoffEvent:
    task = task or make_task()
    return p.HandoffEvent(
        event_id=event_id,
        kind=p.HandoffEventKind.TASK,
        task_id=task.task_id,
        sender=task.sender,
        recipient=task.recipient,
        sequence=0,
        created_at=NOW,
        task=task,
    )


def make_event(
    kind: p.HandoffEventKind,
    *,
    sender: str = "david",
    recipient: str = "drewai",
    sequence: int = 1,
    payload: dict[str, str | int | bool] | None = None,
    event_id: str = EVENT_ID_2,
    task_id: str = TASK_ID,
) -> p.HandoffEvent:
    return p.HandoffEvent(
        event_id=event_id,
        kind=kind,
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        sequence=sequence,
        created_at=NOW,
        payload=payload or {},
    )


class TestRendering:
    def test_short_id_and_thread_name(self) -> None:
        assert short_task_id(TASK_ID) == "6d9f6ad0"
        assert job_thread_name(TASK_ID) == "handoff-6d9f6ad0"

    def test_task_starter_is_human_readable_and_parseable(self) -> None:
        event = make_task_event()
        text = render_task_starter(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert "6d9f6ad0" in text
        assert "drewai → david" in text
        assert "drew/main-projects" in text
        assert "Find the visual picker skill" in text
        assert "read-only" in text
        assert parse_event_message(text) == event

    def test_starter_stays_within_discord_bounds_for_a_large_goal(self) -> None:
        goal = "x" * 1000
        event = make_task_event(make_task(goal=goal))
        text = render_task_starter(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert parse_event_message(text) == event

    def test_starter_never_emits_a_transcript(self) -> None:
        # The starter carries the packet fields and nothing else: a packet
        # that would only fit if the human text were dropped is refused.
        findings = tuple("f" * p.MAX_FINDING_CHARS for _ in range(p.MAX_FINDINGS))
        event = make_task_event(make_task(findings=findings))
        with pytest.raises(HandoffEnvelopeError):
            render_task_starter(event)

    @pytest.mark.parametrize(
        ("kind", "payload", "needle"),
        [
            (p.HandoffEventKind.ACK, {}, "accepted"),
            (p.HandoffEventKind.STATE, {"state": "queued", "note": "waiting"}, "queued"),
            (p.HandoffEventKind.STATE, {"state": "blocked", "note": "needs edit"}, "blocked"),
            (p.HandoffEventKind.QUESTION, {"question": "Which folder?"}, "Which folder?"),
            (p.HandoffEventKind.ANSWER, {"answer": "the second one"}, "the second one"),
            (p.HandoffEventKind.RESULT, {"outcome": "completed", "summary": "Found it"}, "Found"),
            (p.HandoffEventKind.RESULT, {"outcome": "failed", "summary": "No such dir"}, "failed"),
        ],
    )
    def test_typed_events_round_trip_with_a_human_line(
        self, kind: p.HandoffEventKind, payload: dict[str, str | int | bool], needle: str
    ) -> None:
        event = make_event(kind, payload=payload)
        text = render_event_message(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert needle in text
        assert parse_event_message(text) == event

    def test_result_summary_is_bounded_in_the_human_line(self) -> None:
        summary = "s" * p.MAX_PAYLOAD_VALUE_CHARS
        event = make_event(
            p.HandoffEventKind.RESULT, payload={"outcome": "completed", "summary": summary}
        )
        text = render_event_message(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert parse_event_message(text) == event

    def test_parse_accepts_the_strict_envelope_and_ignores_chatter(self) -> None:
        event = make_task_event()
        assert parse_event_message(format_handoff_message(event)) == event
        assert parse_event_message("hello there") is None
        assert parse_event_message("") is None

    def test_parse_rejects_two_markers_or_a_broken_fence(self) -> None:
        event = make_task_event()
        good = render_event_message(event)
        with pytest.raises(HandoffEnvelopeError):
            parse_event_message(good + "\n" + good)
        with pytest.raises(HandoffEnvelopeError):
            parse_event_message("CCDB_HANDOFF_V1\nnot a fence")


class FakeThread:
    def __init__(self, thread_id: int, name: str, *, archived: bool = False) -> None:
        self.id = thread_id
        self.name = name
        self.archived = archived
        self.parent_id = CHANNEL
        self.sent: list[str] = []
        self.edits: list[dict[str, object]] = []

    async def send(self, content: str) -> SimpleNamespace:
        self.sent.append(content)
        return SimpleNamespace(id=len(self.sent), content=content)

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)
        if kwargs.get("archived") is False:
            self.archived = False


class FakeStarterMessage:
    def __init__(self, message_id: int, *, existing: FakeThread | None = None) -> None:
        self.id = message_id
        self.thread = existing
        self.create_calls = 0

    async def create_thread(self, *, name: str, **_: object) -> FakeThread:
        self.create_calls += 1
        if self.thread is not None:
            raise RuntimeError("thread already exists")
        self.thread = FakeThread(self.id, name)
        return self.thread


class TestJobThread:
    @pytest.mark.asyncio
    async def test_post_task_starter_posts_once_and_opens_one_thread(self) -> None:
        posted: list[str] = []

        async def send(content: str) -> FakeStarterMessage:
            posted.append(content)
            return FakeStarterMessage(4242)

        channel = SimpleNamespace(id=CHANNEL, send=send)
        event = make_task_event()
        starter, thread = await post_task_starter(channel, event)
        assert posted == [render_task_starter(event)]
        assert starter.id == 4242
        assert thread.id == 4242
        assert thread.name == job_thread_name(TASK_ID)

    @pytest.mark.asyncio
    async def test_ensure_job_thread_reuses_an_existing_thread(self) -> None:
        existing = FakeThread(4242, job_thread_name(TASK_ID), archived=True)
        starter = FakeStarterMessage(4242, existing=existing)
        thread = await ensure_job_thread(starter, TASK_ID)
        assert thread is existing
        assert starter.create_calls == 0
        # An archived thread is reopened so the acknowledgement is visible.
        assert existing.archived is False

    @pytest.mark.asyncio
    async def test_ensure_job_thread_falls_back_to_fetch_when_create_races(self) -> None:
        existing = FakeThread(4242, job_thread_name(TASK_ID))
        starter = FakeStarterMessage(4242)
        starter.thread = None

        async def create_thread(**_: object) -> FakeThread:
            raise RuntimeError("already has a thread")

        starter.create_thread = create_thread  # type: ignore[method-assign]
        fetch = AsyncMock(return_value=existing)
        thread = await ensure_job_thread(starter, TASK_ID, fetch_channel=fetch)
        assert thread is existing
        fetch.assert_awaited_once_with(4242)


class TestChatCogHandoffTurn:
    @pytest.mark.asyncio
    async def test_run_handoff_turn_pins_dir_and_harness_then_runs_in_the_thread(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        thread = FakeThread(4242, "handoff-6d9f6ad0")
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append({"seed": seed, "thread": th, "prompt": prompt, **kw})

        settings = SimpleNamespace(set_backend=AsyncMock(), set_model=AsyncMock())
        cog = SimpleNamespace(
            repo=SimpleNamespace(ensure_working_dir=AsyncMock(), get=AsyncMock(return_value=None)),
            _backend_settings=settings,
            _run_claude=_run_claude,
            _session_id_for_current_backend=AsyncMock(),
        )
        sink = AsyncMock()

        await ClaudeChatCog.run_handoff_turn(
            cog,  # type: ignore[arg-type]
            thread,
            "do the thing",
            working_dir="/srv/proj",
            result_sink=sink,
            backend="claude",
            model="haiku",
        )
        await asyncio.sleep(0)

        assert thread.sent and "Handoff turn" in thread.sent[0]
        cog.repo.ensure_working_dir.assert_awaited_once_with(4242, "/srv/proj")
        settings.set_backend.assert_awaited_once_with("claude", thread_id=4242)
        settings.set_model.assert_awaited_once_with("claude", "haiku", thread_id=4242)
        assert len(run_calls) == 1
        assert run_calls[0]["thread"] is thread
        assert run_calls[0]["session_id"] is None
        assert run_calls[0]["result_sink"] is sink
        assert run_calls[0]["lounge"] is False

    @pytest.mark.asyncio
    async def test_run_handoff_turn_resume_uses_the_threads_session(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        thread = FakeThread(5150, "existing")
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append(kw)

        record = SimpleNamespace(session_id="abc-123")
        cog = SimpleNamespace(
            repo=SimpleNamespace(
                ensure_working_dir=AsyncMock(), get=AsyncMock(return_value=record)
            ),
            _backend_settings=None,
            _run_claude=_run_claude,
            _session_id_for_current_backend=AsyncMock(return_value="abc-123"),
        )

        await ClaudeChatCog.run_handoff_turn(
            cog,  # type: ignore[arg-type]
            thread,
            "continue",
            working_dir=None,
            result_sink=AsyncMock(),
            resume=True,
        )
        await asyncio.sleep(0)

        assert run_calls[0]["session_id"] == "abc-123"

    def test_capacity_follows_the_concurrency_limit(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        cog = SimpleNamespace(active_session_count=2, _max_concurrent=3)
        assert ClaudeChatCog.handoff_capacity_available(cog) is True  # type: ignore[arg-type]
        cog.active_session_count = 3
        assert ClaudeChatCog.handoff_capacity_available(cog) is False  # type: ignore[arg-type]
