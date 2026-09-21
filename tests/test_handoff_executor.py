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
    monkeypatch.setenv("CCDB_HANDOFF_TRUSTED_BOT_IDS", "4242")  # the sending bot is trusted
    monkeypatch.delenv("CCDB_HANDOFF_AGENTS", raising=False)
    message = SimpleNamespace(
        content=format_handoff_message(_handoff_event()),
        channel=SimpleNamespace(id=777, parent=parent_channel, send=AsyncMock()),
        author=SimpleNamespace(id=4242, bot=True),
        webhook_id=None,
        guild=SimpleNamespace(id=111),  # the packet's origin guild
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


# ---------------------------------------------------------------------------
# The execution coordinator (task 3.3) and review fix (a)
# ---------------------------------------------------------------------------

from datetime import timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from claude_code_core.handoffs import protocol as p  # noqa: E402
from claude_code_core.handoffs.state import HandoffTrigger, apply  # noqa: E402
from claude_discord.handoff_authority import RecipientPolicy  # noqa: E402
from claude_discord.handoff_executor import (  # noqa: E402
    ExecutionMode,
    HandoffExecutor,
    build_handoff_prompt,
)
from claude_discord.handoff_projects import ApprovedRootResolver  # noqa: E402


def _general_task(
    *,
    goal: str = "List the folders and report their remotes",
    authority: p.AuthorityScope | None = None,
    folder: str = "main-projects",
    ttl: timedelta = timedelta(hours=6),
) -> p.HandoffTask:
    origin = p.ConversationCoordinate(guild_id=111, channel_id=222, thread_id=333, message_id=444)
    return p.HandoffTask(
        task_id=TASK_ID,
        sender="david",
        recipient="drewai",
        origin=origin,
        origin_human_id="555",
        project=p.ProjectLocator(owner="drew", folder=folder),
        goal=goal,
        authority=authority or p.AuthorityScope(read=True),
        expected_result="A short report.",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + ttl,
    )


async def _store(repo: HandoffRepository, task: p.HandoffTask) -> None:
    await repo.record_task(task, now=NOW)


class FakeChat:
    """The slice of ClaudeChatCog the executor talks to."""

    def __init__(self, *, capacity: bool = True, fail_spawn: bool = False) -> None:
        self.bot = MagicMock()
        self.bot.get_channel.return_value = None
        self.bot.fetch_channel = AsyncMock(side_effect=RuntimeError("no such channel"))
        self.capacity = capacity
        self.fail_spawn = fail_spawn
        self.turns: list[dict[str, object]] = []
        self.spawn_session = AsyncMock(return_value=SimpleNamespace(id=999, name="lookup"))

    def handoff_capacity_available(self) -> bool:
        return self.capacity

    async def run_handoff_turn(
        self,
        thread: object,
        prompt: str,
        *,
        working_dir: str | None,
        result_sink: object,
        resume: bool = False,
        backend: str | None = None,
        model: str | None = None,
        read_only: bool = False,
    ) -> None:
        if self.fail_spawn:
            raise RuntimeError("harness unavailable")
        self.turns.append(
            {
                "thread": thread,
                "prompt": prompt,
                "working_dir": working_dir,
                "result_sink": result_sink,
                "resume": resume,
                "backend": backend,
                "model": model,
                "read_only": read_only,
            }
        )


def _executor(
    repo: HandoffRepository,
    root: str,
    *,
    policy: RecipientPolicy | None = None,
    **kw: object,
) -> HandoffExecutor:
    return HandoffExecutor(
        repo=repo,
        local_agent_id="drewai",
        resolver=ApprovedRootResolver(roots={"drew": (Path(root).parent,)}),
        policy=policy or RecipientPolicy(),
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_claim_precedes_the_running_transition_so_a_lost_claim_never_sticks(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    """Review fix (a): the attempt is claimed first; a lost claim leaves the job untouched."""
    await _store(handoff_repo, _general_task())
    await handoff_repo.claim_attempt(TASK_ID, "drewai", attempt=1, now=NOW)  # someone else won
    chat = FakeChat()

    started = await _executor(handoff_repo, project_root).run_ready(
        chat=chat, parent_channel=SimpleNamespace(id=777), now=NOW
    )

    assert started == []
    assert chat.turns == [] and chat.spawn_session.await_count == 0
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None and job.state is HandoffState.ACCEPTED


@pytest.mark.asyncio
async def test_spawn_failure_never_leaves_the_job_running(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    """Review fix (a): a failed spawn closes the attempt and requeues a retry, or fails."""
    await _store(handoff_repo, _general_task())
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 4242)
    chat = FakeChat(fail_spawn=True)
    executor = _executor(handoff_repo, project_root, threads={4242: SimpleNamespace(id=4242)})

    started = await executor.run_ready(chat=chat, parent_channel=None, now=NOW)

    assert started == []
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.QUEUED, "a retryable failure is requeued as a new attempt"
    assert job.attempt == 2
    attempts = await handoff_repo.list_attempts(TASK_ID, "drewai")
    assert attempts[0].is_finished and attempts[0].outcome == "failed"
    assert "harness unavailable" in (attempts[0].detail or "")


@pytest.mark.asyncio
async def test_spawn_failures_are_bounded_by_the_attempt_budget(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task())
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 4242)
    chat = FakeChat(fail_spawn=True)
    executor = _executor(handoff_repo, project_root, threads={4242: SimpleNamespace(id=4242)})

    for _ in range(5):
        await executor.run_ready(chat=chat, parent_channel=None, now=NOW)

    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.FAILED
    assert job.attempt == job.max_attempts
    assert len(await handoff_repo.list_attempts(TASK_ID, "drewai")) == job.max_attempts


@pytest.mark.asyncio
async def test_restart_reconciliation_requeues_and_resumes_once(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task())
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 4242)
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    await handoff_repo.claim_attempt(TASK_ID, "drewai", attempt=1, now=NOW)
    await handoff_repo.save_transition(apply(job, HandoffTrigger.START, now=NOW))
    chat = FakeChat()
    executor = _executor(handoff_repo, project_root, threads={4242: SimpleNamespace(id=4242)})

    requeued = await executor.reconcile_after_restart(now=NOW + timedelta(minutes=1))
    assert [j.task_id for j in requeued] == [TASK_ID]
    later = NOW + timedelta(minutes=2)
    first = await executor.run_ready(chat=chat, parent_channel=None, now=later)
    second = await executor.run_ready(chat=chat, parent_channel=None, now=later)

    assert len(first) == 1 and second == []
    assert len(chat.turns) == 1
    attempts = await handoff_repo.list_attempts(TASK_ID, "drewai")
    assert len(attempts) == 1 and attempts[0].attempt == 1, "resumed, not retried"
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None and job.state is HandoffState.RUNNING and job.attempt == 1


@pytest.mark.asyncio
async def test_capacity_wait_stays_queued_and_starts_later(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task())
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 4242)
    chat = FakeChat(capacity=False)
    executor = _executor(handoff_repo, project_root, threads={4242: SimpleNamespace(id=4242)})

    assert await executor.run_ready(chat=chat, parent_channel=None, now=NOW) == []
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.QUEUED
    assert job.note and "capacity" in job.note
    assert await handoff_repo.list_attempts(TASK_ID, "drewai") == []

    chat.capacity = True
    started = await executor.run_ready(chat=chat, parent_channel=None, now=NOW)
    assert len(started) == 1
    assert started[0].mode is ExecutionMode.FRESH_TURN


@pytest.mark.asyncio
async def test_unauthorized_edit_blocks_before_anything_runs(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task(goal="Fix the failing test and commit"))
    chat = FakeChat()
    executor = _executor(handoff_repo, project_root, policy=RecipientPolicy(allow_edit=True))

    assert await executor.run_ready(chat=chat, parent_channel=None, now=NOW) == []
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.BLOCKED
    assert job.note and "edit" in job.note
    assert chat.turns == [] and chat.spawn_session.await_count == 0


@pytest.mark.asyncio
async def test_missing_project_folder_blocks(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task(folder="does-not-exist"))
    chat = FakeChat()

    started = await _executor(handoff_repo, project_root).run_ready(
        chat=chat, parent_channel=None, now=NOW
    )
    assert started == []
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.BLOCKED
    assert job.note and "does not exist" in job.note


@pytest.mark.asyncio
async def test_expired_task_fails_without_running(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task(ttl=timedelta(minutes=5)))
    chat = FakeChat()

    await _executor(handoff_repo, project_root).run_ready(
        chat=chat, parent_channel=None, now=NOW + timedelta(hours=1)
    )
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None
    assert job.state is HandoffState.FAILED
    assert job.note and "expired" in job.note
    assert chat.turns == []


@pytest.mark.asyncio
async def test_fresh_turn_runs_in_the_known_job_thread(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    task = _general_task()
    await _store(handoff_repo, task)
    await handoff_repo.set_job_thread(TASK_ID, "drewai", 4242)
    thread = SimpleNamespace(id=4242)
    chat = FakeChat()
    executor = _executor(handoff_repo, project_root, threads={4242: thread})

    started = await executor.run_ready(chat=chat, parent_channel=None, now=NOW)

    assert len(started) == 1 and started[0].thread_id == 4242
    assert chat.spawn_session.await_count == 0
    turn = chat.turns[0]
    assert turn["thread"] is thread
    assert turn["resume"] is False
    assert turn["working_dir"] == project_root
    assert "read-only" in str(turn["prompt"]).lower()
    assert task.goal in str(turn["prompt"])
    assert turn["backend"] == "claude"


@pytest.mark.asyncio
async def test_existing_session_is_used_only_when_explicitly_selected(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task())
    session_thread = SimpleNamespace(id=5150)
    chat = FakeChat()
    executor = _executor(
        handoff_repo,
        project_root,
        threads={5150: session_thread},
        session_selector=lambda task: 5150,
    )

    started = await executor.run_ready(chat=chat, parent_channel=None, now=NOW)

    assert started[0].mode is ExecutionMode.EXISTING_SESSION
    assert chat.turns[0]["thread"] is session_thread
    assert chat.turns[0]["resume"] is True


@pytest.mark.asyncio
async def test_deterministic_operation_needs_no_model(
    handoff_repo: HandoffRepository, project_root: str
) -> None:
    await _store(handoff_repo, _general_task(goal="List the folders"))
    chat = FakeChat()
    origin = SimpleNamespace(id=333, send=AsyncMock(), guild=SimpleNamespace(id=111))
    chat.bot.get_channel.return_value = origin

    class ListFolders:
        def matches(self, task: p.HandoffTask) -> bool:
            return task.goal.lower().startswith("list the folders")

        async def run(self, task: p.HandoffTask, project: object) -> str:
            return "folders: youtube-money"

    executor = _executor(handoff_repo, project_root, deterministic=(ListFolders(),))
    started = await executor.run_ready(chat=chat, parent_channel=None, now=NOW)

    assert started[0].mode is ExecutionMode.DETERMINISTIC
    assert chat.turns == [] and chat.spawn_session.await_count == 0
    job = await handoff_repo.get_job(TASK_ID, "drewai")
    assert job is not None and job.state is HandoffState.COMPLETED
    result = await handoff_repo.get_result(TASK_ID, "drewai")
    assert result is not None and "youtube-money" in (result.summary or "")
    origin.send.assert_awaited_once()


def test_handoff_prompt_carries_scope_and_never_a_transcript() -> None:
    task = _general_task(
        goal="Fix the failing test", authority=p.AuthorityScope(read=True, edit=True)
    )
    prompt = build_handoff_prompt(task, project_path="/srv/proj", effective=task.authority)
    assert "Fix the failing test" in prompt
    assert "/srv/proj" in prompt
    assert "edit" in prompt.lower()
    assert "do not deploy" in prompt.lower()
    read_only = build_handoff_prompt(
        _general_task(), project_path="/srv/proj", effective=p.AuthorityScope(read=True)
    )
    assert "read-only" in read_only.lower()
