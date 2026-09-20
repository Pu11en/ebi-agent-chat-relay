"""Durable pending-turn storage for model capacity recovery.

This slice intentionally stops at persistence. It proves the store can keep a
recoverable turn alive across process restarts and races, but it does not start
models, retry work, or load pending rows on bot startup.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite
import pytest

from claude_discord.database.capacity_recovery_repo import (
    CapacityPendingTurn,
    CapacityPendingTurnCreate,
    CapacityRecoveryRepository,
    PendingTurnState,
)
from claude_discord.database.models import (
    _CORE_SCHEMA,
    _HANDOFF_SCHEMA,
    _MIGRATIONS,
    init_db,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
async def db_path() -> AsyncIterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield path
    finally:
        os.unlink(path)


@pytest.fixture
async def repo(db_path: str) -> CapacityRecoveryRepository:
    return CapacityRecoveryRepository(db_path)


def make_turn(
    *,
    turn_key: str = "turn-1",
    thread_id: int = 123,
    prompt_ref: str = "prompt:local:turn-1",
    next_attempt_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> CapacityPendingTurnCreate:
    return CapacityPendingTurnCreate(
        turn_key=turn_key,
        frontend="discord",
        thread_id=thread_id,
        session_id="session-abc",
        prompt_ref=prompt_ref,
        backend="claude",
        model="opus",
        fallback_chain=[{"backend": "codex", "model": "gpt-5.5", "authority": "session"}],
        next_attempt_at=next_attempt_at,
        expires_at=expires_at or NOW + timedelta(hours=2),
    )


@contextlib.asynccontextmanager
async def _pre_capacity_db() -> AsyncIterator[str]:
    """A database holding sessions and handoffs but no recovery table yet."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        async with aiosqlite.connect(path) as db:
            await db.executescript(_CORE_SCHEMA + _HANDOFF_SCHEMA)
            await db.commit()
        yield path
    finally:
        os.unlink(path)


async def _count_capacity_rows(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM capacity_pending_turns")
        row: Any = await cursor.fetchone()
    return int(row[0])


async def test_capacity_table_is_created_without_losing_existing_rows() -> None:
    async with _pre_capacity_db() as path:
        async with aiosqlite.connect(path) as db:
            await db.execute("INSERT INTO sessions (thread_id, session_id) VALUES (7, 'keep-me')")
            await db.execute(
                """
                INSERT INTO handoff_tasks (
                    task_id, recipient_agent_id, sender_agent_id, packet_json,
                    expires_at, created_at, updated_at
                ) VALUES ('task-1', 'local', 'remote', '{}', 'x', 'x', 'x')
                """
            )
            await db.commit()

        await init_db(path)

        async with aiosqlite.connect(path) as db:
            sessions = await db.execute_fetchall("SELECT session_id FROM sessions")
            handoffs = await db.execute_fetchall("SELECT task_id FROM handoff_tasks")
            tables = await db.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )

    assert [row[0] for row in sessions] == ["keep-me"]
    assert [row[0] for row in handoffs] == ["task-1"]
    assert "capacity_pending_turns" in {row[0] for row in tables}


async def test_migration_list_alone_adds_capacity_pending_turns() -> None:
    async with _pre_capacity_db() as path, aiosqlite.connect(path) as db:
        for statement in _MIGRATIONS:
            with contextlib.suppress(Exception):
                await db.execute(statement)
        await db.commit()
        tables = await db.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")

    assert "capacity_pending_turns" in {row[0] for row in tables}


async def test_create_is_idempotent_and_round_trips_payload(
    repo: CapacityRecoveryRepository,
) -> None:
    created, first = await repo.create_pending(make_turn(), now=NOW)
    created_again, second = await repo.create_pending(
        make_turn(prompt_ref="prompt:local:changed"), now=NOW + timedelta(minutes=5)
    )

    assert created is True
    assert created_again is False
    assert first == second
    assert first.state is PendingTurnState.PENDING
    assert first.attempt == 0
    assert first.fallback_chain == [
        {"backend": "codex", "model": "gpt-5.5", "authority": "session"}
    ]


async def test_create_keeps_prompt_payload_out_of_unrelated_session_rows(
    repo: CapacityRecoveryRepository, db_path: str
) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO sessions (thread_id, session_id) VALUES (123, 'session-abc')")
        await db.commit()

    await repo.create_pending(make_turn(prompt_ref="secret-prompt-reference"), now=NOW)

    async with aiosqlite.connect(db_path) as db:
        rows = list(await db.execute_fetchall("SELECT * FROM sessions"))
    assert len(rows) == 1
    assert "secret-prompt-reference" not in json.dumps([tuple(row) for row in rows])


async def test_conditional_claim_has_one_winner(repo: CapacityRecoveryRepository) -> None:
    await repo.create_pending(make_turn(), now=NOW)

    claimed = await repo.claim_due("turn-1", claim_token="worker-a", now=NOW)
    duplicate = await repo.claim_due("turn-1", claim_token="worker-b", now=NOW)

    assert claimed is not None
    assert duplicate is None
    assert claimed.state is PendingTurnState.RUNNING
    assert claimed.attempt == 1
    assert claimed.claim_token == "worker-a"


async def test_claim_skips_future_accepted_and_expired_turns(
    repo: CapacityRecoveryRepository,
) -> None:
    await repo.create_pending(
        make_turn(turn_key="future", next_attempt_at=NOW + timedelta(minutes=5)), now=NOW
    )
    await repo.create_pending(make_turn(turn_key="accepted"), now=NOW)
    await repo.accept_once("accepted", accepted_result_ref="result:a", now=NOW)
    await repo.create_pending(
        make_turn(turn_key="expired", expires_at=NOW - timedelta(seconds=1)),
        now=NOW,
    )

    assert await repo.claim_due("future", claim_token="worker", now=NOW) is None
    assert await repo.claim_due("accepted", claim_token="worker", now=NOW) is None
    assert await repo.claim_due("expired", claim_token="worker", now=NOW) is None


async def test_schedule_retry_releases_the_claim_and_persists_next_time(
    repo: CapacityRecoveryRepository,
) -> None:
    await repo.create_pending(make_turn(), now=NOW)
    await repo.claim_due("turn-1", claim_token="worker-a", now=NOW)

    scheduled = await repo.schedule_retry(
        "turn-1",
        claim_token="worker-a",
        next_attempt_at=NOW + timedelta(minutes=3),
        now=NOW + timedelta(seconds=1),
    )
    stale = await repo.schedule_retry(
        "turn-1",
        claim_token="worker-a",
        next_attempt_at=NOW + timedelta(minutes=4),
        now=NOW + timedelta(seconds=2),
    )

    assert scheduled is not None
    assert stale is None
    assert scheduled.state is PendingTurnState.SCHEDULED
    assert scheduled.claim_token is None
    assert scheduled.next_attempt_at == NOW + timedelta(minutes=3)
    assert await repo.claim_due("turn-1", claim_token="worker-b", now=NOW) is None
    assert (
        await repo.claim_due("turn-1", claim_token="worker-b", now=NOW + timedelta(minutes=3))
    ) is not None


async def test_accept_once_wins_over_late_duplicates(repo: CapacityRecoveryRepository) -> None:
    await repo.create_pending(make_turn(), now=NOW)
    first = await repo.accept_once("turn-1", accepted_result_ref="result:original", now=NOW)
    second = await repo.accept_once(
        "turn-1",
        accepted_result_ref="result:retry",
        now=NOW + timedelta(seconds=5),
    )
    reloaded = await repo.get("turn-1")

    assert first is not None
    assert second is None
    assert reloaded is not None
    assert reloaded.state is PendingTurnState.ACCEPTED
    assert reloaded.accepted_result_ref == "result:original"


async def test_expiry_marks_only_unaccepted_pending_turns(
    repo: CapacityRecoveryRepository,
) -> None:
    await repo.create_pending(
        make_turn(turn_key="old", expires_at=NOW - timedelta(seconds=1)),
        now=NOW,
    )
    await repo.create_pending(
        make_turn(turn_key="done", expires_at=NOW - timedelta(seconds=1)), now=NOW
    )
    await repo.accept_once("done", accepted_result_ref="result:done", now=NOW)
    await repo.create_pending(make_turn(turn_key="fresh"), now=NOW)

    expired = await repo.expire_before(NOW)

    assert expired == 1
    assert (await repo.get("old")).state is PendingTurnState.EXPIRED  # type: ignore[union-attr]
    assert (await repo.get("done")).state is PendingTurnState.ACCEPTED  # type: ignore[union-attr]
    assert (await repo.get("fresh")).state is PendingTurnState.PENDING  # type: ignore[union-attr]


async def test_restart_reload_lists_due_records_without_claiming(
    repo: CapacityRecoveryRepository,
) -> None:
    await repo.create_pending(make_turn(turn_key="due-a", next_attempt_at=NOW), now=NOW)
    await repo.create_pending(make_turn(turn_key="due-b", next_attempt_at=NOW), now=NOW)
    await repo.create_pending(
        make_turn(turn_key="later", next_attempt_at=NOW + timedelta(minutes=10)), now=NOW
    )
    await repo.create_pending(make_turn(turn_key="accepted"), now=NOW)
    await repo.accept_once("accepted", accepted_result_ref="result:accepted", now=NOW)

    due = await repo.reload_due(now=NOW, limit=10)

    assert [turn.turn_key for turn in due] == ["due-a", "due-b"]
    assert [turn.state for turn in due] == [PendingTurnState.PENDING, PendingTurnState.PENDING]
    assert all(isinstance(turn, CapacityPendingTurn) for turn in due)
    assert await _count_capacity_rows(repo.db_path) == 4
