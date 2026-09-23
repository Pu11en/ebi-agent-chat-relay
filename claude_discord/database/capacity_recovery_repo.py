"""Durable pending-turn storage for model capacity recovery.

The repository is intentionally small and state-only. It does not decide retry
policy, start a backend, or load rows on restart; later orchestration asks it
which turn is due and uses conditional updates to ensure only one worker runs a
given attempt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

import aiosqlite


class PendingTurnState(Enum):
    """Stored lifecycle of a capacity-recovery turn."""

    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


@dataclass(frozen=True)
class CapacityPendingTurnCreate:
    """Data captured when a recoverable logical turn is first persisted."""

    turn_key: str
    frontend: str
    thread_id: int
    session_id: str | None
    prompt_ref: str
    backend: str
    model: str | None
    fallback_chain: list[dict[str, object]]
    next_attempt_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CapacityPendingTurn:
    """One persisted pending turn."""

    turn_key: str
    frontend: str
    thread_id: int
    session_id: str | None
    prompt_ref: str
    backend: str
    model: str | None
    fallback_chain: list[dict[str, object]]
    state: PendingTurnState
    attempt: int
    next_attempt_at: datetime
    claim_token: str | None
    claimed_at: datetime | None
    accepted_at: datetime | None
    accepted_result_ref: str | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("capacity recovery timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _require(value: str | None, field: str) -> datetime:
    parsed = _parse(value)
    if parsed is None:  # pragma: no cover - NOT NULL columns guarantee this
        raise ValueError(f"stored pending turn is missing {field}")
    return parsed


def _turn_from_row(row: aiosqlite.Row) -> CapacityPendingTurn:
    fallback_chain = json.loads(row["fallback_chain_json"])
    if not isinstance(fallback_chain, list):
        fallback_chain = []
    return CapacityPendingTurn(
        turn_key=row["turn_key"],
        frontend=row["frontend"],
        thread_id=int(row["thread_id"]),
        session_id=row["session_id"],
        prompt_ref=row["prompt_ref"],
        backend=row["backend"],
        model=row["model"],
        fallback_chain=fallback_chain,
        state=PendingTurnState(row["state"]),
        attempt=int(row["attempt"]),
        next_attempt_at=_require(row["next_attempt_at"], "next_attempt_at"),
        claim_token=row["claim_token"],
        claimed_at=_parse(row["claimed_at"]),
        accepted_at=_parse(row["accepted_at"]),
        accepted_result_ref=row["accepted_result_ref"],
        expires_at=_require(row["expires_at"], "expires_at"),
        created_at=_require(row["created_at"], "created_at"),
        updated_at=_require(row["updated_at"], "updated_at"),
    )


class CapacityRecoveryRepository:
    """SQLite access for model-capacity pending turns."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> str:
        """The database path, exposed for black-box tests."""
        return self._db_path

    async def create_pending(
        self, turn: CapacityPendingTurnCreate, *, now: datetime
    ) -> tuple[bool, CapacityPendingTurn]:
        """Insert a pending turn once, or return the existing stored row."""
        stamp = _iso(now)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO capacity_pending_turns (
                    turn_key, frontend, thread_id, session_id, prompt_ref,
                    backend, model, fallback_chain_json, state, attempt,
                    next_attempt_at, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    turn.turn_key,
                    turn.frontend,
                    int(turn.thread_id),
                    turn.session_id,
                    turn.prompt_ref,
                    turn.backend,
                    turn.model,
                    json.dumps(turn.fallback_chain, ensure_ascii=False, sort_keys=True),
                    PendingTurnState.PENDING.value,
                    _iso(turn.next_attempt_at),
                    _iso(turn.expires_at),
                    stamp,
                    stamp,
                ),
            )
            row = await self._fetch(db, turn.turn_key)
            await db.commit()
        if row is None:  # pragma: no cover - insert or existing PK guarantees a row
            raise RuntimeError(f"failed to read pending turn {turn.turn_key}")
        return cursor.rowcount > 0, _turn_from_row(row)

    async def get(self, turn_key: str) -> CapacityPendingTurn | None:
        """Fetch one persisted turn."""
        row = await self._one(
            "SELECT * FROM capacity_pending_turns WHERE turn_key = ?",
            (turn_key,),
        )
        return None if row is None else _turn_from_row(row)

    async def claim_due(
        self, turn_key: str, *, claim_token: str, now: datetime
    ) -> CapacityPendingTurn | None:
        """Claim a due, unaccepted turn for execution.

        The conditional update is the worker lock: a turn can be claimed only
        when it is due, not expired, not accepted, and not already running.
        """
        stamp = _iso(now)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE capacity_pending_turns
                   SET state = ?, attempt = attempt + 1, claim_token = ?,
                       claimed_at = ?, updated_at = ?
                 WHERE turn_key = ?
                   AND state IN (?, ?)
                   AND next_attempt_at <= ?
                   AND expires_at > ?
                   AND accepted_at IS NULL
                   AND claim_token IS NULL
                """,
                (
                    PendingTurnState.RUNNING.value,
                    claim_token,
                    stamp,
                    stamp,
                    turn_key,
                    PendingTurnState.PENDING.value,
                    PendingTurnState.SCHEDULED.value,
                    stamp,
                    stamp,
                ),
            )
            row = await self._fetch(db, turn_key) if cursor.rowcount > 0 else None
            await db.commit()
        return None if row is None else _turn_from_row(row)

    async def schedule_retry(
        self,
        turn_key: str,
        *,
        claim_token: str,
        next_attempt_at: datetime,
        now: datetime,
    ) -> CapacityPendingTurn | None:
        """Put a claimed turn back into the due queue for a later attempt."""
        stamp = _iso(now)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE capacity_pending_turns
                   SET state = ?, next_attempt_at = ?, claim_token = NULL,
                       claimed_at = NULL, updated_at = ?
                 WHERE turn_key = ?
                   AND state = ?
                   AND claim_token = ?
                   AND accepted_at IS NULL
                   AND expires_at > ?
                """,
                (
                    PendingTurnState.SCHEDULED.value,
                    _iso(next_attempt_at),
                    stamp,
                    turn_key,
                    PendingTurnState.RUNNING.value,
                    claim_token,
                    stamp,
                ),
            )
            row = await self._fetch(db, turn_key) if cursor.rowcount > 0 else None
            await db.commit()
        return None if row is None else _turn_from_row(row)

    async def accept_once(
        self, turn_key: str, *, accepted_result_ref: str, now: datetime
    ) -> CapacityPendingTurn | None:
        """Accept the first terminal result for a logical turn."""
        stamp = _iso(now)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE capacity_pending_turns
                   SET state = ?, accepted_at = ?, accepted_result_ref = ?,
                       claim_token = NULL, claimed_at = NULL, updated_at = ?
                 WHERE turn_key = ?
                   AND accepted_at IS NULL
                   AND state != ?
                """,
                (
                    PendingTurnState.ACCEPTED.value,
                    stamp,
                    accepted_result_ref,
                    stamp,
                    turn_key,
                    PendingTurnState.EXPIRED.value,
                ),
            )
            row = await self._fetch(db, turn_key) if cursor.rowcount > 0 else None
            await db.commit()
        return None if row is None else _turn_from_row(row)

    async def expire_before(self, now: datetime) -> int:
        """Mark overdue, unaccepted rows as expired."""
        stamp = _iso(now)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE capacity_pending_turns
                   SET state = ?, claim_token = NULL, claimed_at = NULL, updated_at = ?
                 WHERE expires_at <= ?
                   AND accepted_at IS NULL
                   AND state != ?
                """,
                (
                    PendingTurnState.EXPIRED.value,
                    stamp,
                    stamp,
                    PendingTurnState.EXPIRED.value,
                ),
            )
            await db.commit()
            return cursor.rowcount

    async def reload_due(self, *, now: datetime, limit: int = 50) -> list[CapacityPendingTurn]:
        """List due pending turns for a future restart loader without claiming them."""
        rows = await self._all(
            """
            SELECT * FROM capacity_pending_turns
             WHERE state IN (?, ?)
               AND next_attempt_at <= ?
               AND expires_at > ?
               AND accepted_at IS NULL
               AND claim_token IS NULL
             ORDER BY next_attempt_at, created_at, turn_key
             LIMIT ?
            """,
            (
                PendingTurnState.PENDING.value,
                PendingTurnState.SCHEDULED.value,
                _iso(now),
                _iso(now),
                int(limit),
            ),
        )
        return [_turn_from_row(row) for row in rows]

    @staticmethod
    async def _fetch(db: aiosqlite.Connection, turn_key: str) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM capacity_pending_turns WHERE turn_key = ?",
            (turn_key,),
        )
        return await cursor.fetchone()

    async def _one(self, sql: str, params: tuple[object, ...]) -> aiosqlite.Row | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            return await cursor.fetchone()

    async def _all(self, sql: str, params: tuple[object, ...]) -> list[aiosqlite.Row]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            return list(await cursor.fetchall())


__all__ = [
    "CapacityPendingTurn",
    "CapacityPendingTurnCreate",
    "CapacityRecoveryRepository",
    "PendingTurnState",
]
