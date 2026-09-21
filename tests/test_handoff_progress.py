"""Tests for visible, bounded handoff progress posts (task 3.4)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_core.handoffs import protocol as p
from claude_code_core.handoffs.state import HandoffState, HandoffTrigger, apply
from claude_discord.database.handoff_repo import HandoffRepository
from claude_discord.database.models import init_db
from claude_discord.handoff_authority import RecipientPolicy
from claude_discord.handoff_discord import DISCORD_MESSAGE_LIMIT, parse_event_message
from claude_discord.handoff_executor import HandoffExecutor
from claude_discord.handoff_progress import HandoffProgressPoster
from claude_discord.handoff_projects import ApprovedRootResolver

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


@pytest.fixture
async def repo() -> AsyncIterator[HandoffRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield HandoffRepository(path)
    finally:
        os.unlink(path)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "drewp" / "main-projects"
    root.mkdir(parents=True)
    return root


class FakeThread:
    def __init__(self, thread_id: int, *, guild_id: int | None = 111) -> None:
        self.id = thread_id
        self.guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
        self.sent: list[str] = []

    async def send(self, content: str) -> SimpleNamespace:
        self.sent.append(content)
        return SimpleNamespace(id=len(self.sent))


def _task(goal: str = "List the folders", authority: p.AuthorityScope | None = None):
    origin = p.ConversationCoordinate(guild_id=111, channel_id=222, thread_id=333, message_id=444)
    return p.HandoffTask(
        task_id=TASK_ID,
        sender="david",
        recipient="drewai",
        origin=origin,
        origin_human_id="555",
        project=p.ProjectLocator(owner="drew", folder="main-projects"),
        goal=goal,
        authority=authority or p.AuthorityScope(read=True),
        expected_result="A short report.",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=6),
    )


def _task_event(task: p.HandoffTask) -> p.HandoffEvent:
    return p.HandoffEvent(
        event_id=EVENT_ID,
        kind=p.HandoffEventKind.TASK,
        task_id=task.task_id,
        sender=task.sender,
        recipient=task.recipient,
        sequence=0,
        created_at=NOW,
        task=task,
    )


class FakeChat:
    def __init__(self, origin: FakeThread) -> None:
        self.bot = MagicMock()
        self.bot.get_channel.side_effect = lambda cid: origin if cid == 333 else None
        self.bot.fetch_channel = AsyncMock(side_effect=RuntimeError("missing"))
        self.sinks: list[object] = []

    def handoff_capacity_available(self) -> bool:
        return True

    async def run_handoff_turn(self, thread: object, prompt: str, **kw: object) -> None:
        self.sinks.append(kw["result_sink"])


def _poster(repo: HandoffRepository, job_thread: FakeThread, origin: FakeThread):
    async def lookup(thread_id: int):
        return {job_thread.id: job_thread, origin.id: origin}.get(thread_id)

    return HandoffProgressPoster(repo=repo, local_agent_id="drewai", thread_lookup=lookup)


@pytest.mark.asyncio
async def test_every_durable_transition_is_visible_and_reconstructible(
    repo: HandoffRepository, project_root: Path
) -> None:
    task = _task()
    job_thread, origin = FakeThread(4242), FakeThread(333)
    await repo.record_task(task, now=NOW)
    await repo.record_event(_task_event(task))
    await repo.set_job_thread(TASK_ID, "drewai", 4242)
    poster = _poster(repo, job_thread, origin)
    await poster.announce_accepted(task)
    executor = HandoffExecutor(
        repo=repo,
        local_agent_id="drewai",
        resolver=ApprovedRootResolver(roots={"drew": (project_root.parent,)}),
        policy=RecipientPolicy(),
        threads={4242: job_thread},
        on_transition=poster,
    )
    chat = FakeChat(origin)

    await executor.run_ready(chat=chat, parent_channel=None, now=NOW)
    await chat.sinks[0]("Found youtube-money and two more.", None)

    parsed = [parse_event_message(text) for text in job_thread.sent]
    assert all(event is not None for event in parsed)
    assert all(len(text) <= DISCORD_MESSAGE_LIMIT for text in job_thread.sent)
    kinds = [event.kind for event in parsed if event is not None]
    assert kinds == [
        p.HandoffEventKind.ACK,
        p.HandoffEventKind.STATE,
        p.HandoffEventKind.RESULT,
    ]
    states = [event.payload.get("state") for event in parsed if event is not None]
    assert states[1] == "running"
    assert parsed[2] is not None and parsed[2].payload["outcome"] == "completed"
    # Discord plus the ledger reconstruct the same story: every posted event is
    # stored, sequences climb, and the ledger's terminal state matches.
    stored = {event.event_id: event for event in await repo.list_events(TASK_ID)}
    for event in parsed:
        assert event is not None and event.event_id in stored
    sequences = [event.sequence for event in parsed if event is not None]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    job = await repo.get_job(TASK_ID, "drewai")
    assert job is not None and job.state is HandoffState.COMPLETED
    # The origin got the result (through the outbox) exactly once.
    assert len(origin.sent) == 1 and "youtube-money" in origin.sent[0]


@pytest.mark.asyncio
async def test_blocked_is_posted_to_the_job_thread_and_the_origin(
    repo: HandoffRepository, project_root: Path
) -> None:
    task = _task(goal="Delete the old builds")
    job_thread, origin = FakeThread(4242), FakeThread(333)
    await repo.record_task(task, now=NOW)
    await repo.set_job_thread(TASK_ID, "drewai", 4242)
    executor = HandoffExecutor(
        repo=repo,
        local_agent_id="drewai",
        resolver=ApprovedRootResolver(roots={"drew": (project_root.parent,)}),
        policy=RecipientPolicy(),
        threads={4242: job_thread},
        on_transition=_poster(repo, job_thread, origin),
    )

    await executor.run_ready(chat=FakeChat(origin), parent_channel=None, now=NOW)

    assert len(job_thread.sent) == 1
    event = parse_event_message(job_thread.sent[0])
    assert event is not None and event.payload["state"] == "blocked"
    assert "destructive" in str(event.payload["note"])
    assert len(origin.sent) == 1
    assert "blocked" in origin.sent[0] and "6d9f6ad0" in origin.sent[0]
    assert parse_event_message(origin.sent[0]) is None, "the origin gets prose, not protocol"


@pytest.mark.asyncio
@pytest.mark.parametrize("guild_id", [999, None])
async def test_a_blocker_never_reaches_an_origin_outside_the_reply_guild(
    repo: HandoffRepository, project_root: Path, guild_id: int | None
) -> None:
    """reply_to says guild 111; a channel with that id in another guild is not the origin."""
    task = _task(goal="Delete the old builds")
    job_thread, elsewhere = FakeThread(4242), FakeThread(333, guild_id=guild_id)
    await repo.record_task(task, now=NOW)
    await repo.set_job_thread(TASK_ID, "drewai", 4242)
    executor = HandoffExecutor(
        repo=repo,
        local_agent_id="drewai",
        resolver=ApprovedRootResolver(roots={"drew": (project_root.parent,)}),
        policy=RecipientPolicy(),
        threads={4242: job_thread},
        on_transition=_poster(repo, job_thread, elsewhere),
    )

    await executor.run_ready(chat=FakeChat(elsewhere), parent_channel=None, now=NOW)

    assert len(job_thread.sent) == 1, "the job thread still shows the block"
    assert elsewhere.sent == []


@pytest.mark.asyncio
async def test_queued_and_requeued_posts_are_bounded_and_ordered(
    repo: HandoffRepository, project_root: Path
) -> None:
    task = _task()
    job_thread, origin = FakeThread(4242), FakeThread(333)
    await repo.record_task(task, now=NOW)
    await repo.set_job_thread(TASK_ID, "drewai", 4242)
    poster = _poster(repo, job_thread, origin)
    job = await repo.get_job(TASK_ID, "drewai")
    assert job is not None
    waiting = apply(job, HandoffTrigger.CAPACITY_WAIT, now=NOW)
    await repo.save_transition(waiting)
    await poster(task, waiting)
    started = apply(waiting.job, HandoffTrigger.START, now=NOW)
    await repo.save_transition(started)
    await poster(task, started)
    executor = HandoffExecutor(
        repo=repo,
        local_agent_id="drewai",
        resolver=ApprovedRootResolver(roots={"drew": (project_root.parent,)}),
        policy=RecipientPolicy(),
        threads={4242: job_thread},
        on_transition=poster,
    )

    await executor.reconcile_after_restart(now=NOW + timedelta(minutes=1))

    states = [
        (e.payload.get("state"), e.sequence)
        for e in (parse_event_message(t) for t in job_thread.sent)
        if e is not None
    ]
    assert [s for s, _ in states] == ["queued", "running", "queued"]
    assert [n for _, n in states] == sorted(n for _, n in states)
    assert origin.sent == [], "capacity and restart notes stay in the job thread"


@pytest.mark.asyncio
async def test_posting_survives_a_missing_thread(repo: HandoffRepository) -> None:
    task = _task()
    await repo.record_task(task, now=NOW)

    async def lookup(thread_id: int) -> None:
        return None

    poster = HandoffProgressPoster(repo=repo, local_agent_id="drewai", thread_lookup=lookup)
    job = await repo.get_job(TASK_ID, "drewai")
    assert job is not None
    transition = apply(job, HandoffTrigger.CAPACITY_WAIT, now=NOW)
    await poster(task, transition)  # no thread known, no exception
    events = await repo.list_events(TASK_ID)
    assert [e.kind for e in events] == [p.HandoffEventKind.STATE], "the ledger still has it"
