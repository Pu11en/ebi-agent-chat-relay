"""Session repository for thread/channel-to-session mapping.

Frontend-agnostic: works with any integer key (Discord thread ID,
Teams conversation ID, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from .types import RateLimitInfo

logger = logging.getLogger(__name__)


class LifecycleState(Enum):
    """Where a session sits between open and closed.

    ``CLOSING`` is not a transient in-memory flag: a close asked for during an
    active turn is written down so the wrap-up still happens after the turn
    ends, even if the bot restarts in between.
    """

    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class CloseAuthority(Enum):
    """Who authorized a close.

    Closing is destructive to a conversation's availability, so it is only ever
    done on inherited authority. A model deciding it is finished is not on this
    list and cannot be stored, which is what makes an agent-initiated close
    auditable rather than a matter of trust.
    """

    #: A person pressed Close or ran /close themselves.
    DIRECT_INTERACTION = "direct_interaction"
    #: The current request explicitly told the agent to close when done.
    USER_INSTRUCTION = "user_instruction"
    #: A preauthorized workflow completed with close-on-done enabled.
    WORKFLOW_CLOSE_ON_DONE = "workflow_close_on_done"


def _coerce_authority(authority: CloseAuthority | str | None) -> CloseAuthority:
    """Resolve an authority source, refusing anything unrecognized.

    Fails closed on purpose: an unknown string raises rather than being stored,
    so no caller can invent an authority by passing free text.
    """
    if isinstance(authority, CloseAuthority):
        return authority
    try:
        return CloseAuthority(authority)
    except ValueError:
        raise ValueError(
            f"Unknown close authority {authority!r}; "
            f"expected one of {[a.value for a in CloseAuthority]}"
        ) from None


def _state_value(state: LifecycleState | str) -> str:
    return state.value if isinstance(state, LifecycleState) else LifecycleState(state).value


@dataclass
class SessionRecord:
    """A stored session mapping."""

    thread_id: int
    session_id: str
    working_dir: str | None
    model: str | None
    origin: str
    summary: str | None
    created_at: str
    last_used_at: str
    context_window: int | None = None
    context_used: int | None = None
    backend: str | None = None
    lifecycle_state: str = LifecycleState.OPEN.value
    close_requested_at: str | None = None
    close_authority: str | None = None
    wrap_up: str | None = None
    closed_at: str | None = None

    @property
    def is_open(self) -> bool:
        return self.lifecycle_state == LifecycleState.OPEN.value

    @property
    def close_pending(self) -> bool:
        """A close was asked for and the wrap-up has not been written yet."""
        return self.lifecycle_state == LifecycleState.CLOSING.value

    @property
    def is_closed(self) -> bool:
        return self.lifecycle_state == LifecycleState.CLOSED.value


#: How long a write waits for another writer before giving up. The default 5 s
#: once failed a whole reply ("database is locked") during a busy moment.
DB_BUSY_TIMEOUT_SECONDS = 30.0


class SessionRepository:
    """CRUD operations for session records."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def get(self, thread_id: int) -> SessionRecord | None:
        """Get session by thread/channel ID."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return SessionRecord(**dict(row))

    async def save(
        self,
        thread_id: int,
        session_id: str,
        working_dir: str | None = None,
        model: str | None = None,
        origin: str = "discord",
        summary: str | None = None,
        backend: str | None = None,
    ) -> SessionRecord:
        """Create or update a session mapping.

        ``backend`` records which CLI minted ``session_id``. Session stores are
        not interoperable across backends, so callers must know who owns an ID
        before passing it to ``--resume`` / ``codex exec resume``.
        """
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                """INSERT INTO sessions
                     (thread_id, session_id, working_dir, model, origin, summary, backend)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                     session_id = excluded.session_id,
                     working_dir = COALESCE(excluded.working_dir, sessions.working_dir),
                     model = COALESCE(excluded.model, sessions.model),
                     origin = COALESCE(excluded.origin, sessions.origin),
                     summary = COALESCE(excluded.summary, sessions.summary),
                     backend = COALESCE(excluded.backend, sessions.backend),
                     last_used_at = datetime('now', 'localtime')""",
                (thread_id, session_id, working_dir, model, origin, summary, backend),
            )
            await db.commit()

        record = await self.get(thread_id)
        if record is None:
            raise RuntimeError(f"Failed to retrieve session after save for thread {thread_id}")
        return record

    async def ensure_working_dir(
        self,
        thread_id: int,
        working_dir: str,
        origin: str = "discord",
    ) -> SessionRecord:
        """Give an unbound conversation a directory without changing its identity.

        A newly created conversation has no CLI session ID until its first
        system event arrives.  Persisting the directory before launch closes
        that gap.  An existing directory remains canonical so a caller's
        fallback cannot silently move a resumed session to another project.
        """
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                """INSERT INTO sessions (thread_id, session_id, working_dir, origin)
                   VALUES (?, '', ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                     working_dir = COALESCE(sessions.working_dir, excluded.working_dir),
                     last_used_at = datetime('now', 'localtime')""",
                (thread_id, working_dir, origin),
            )
            await db.commit()

        record = await self.get(thread_id)
        if record is None:
            raise RuntimeError(
                f"Failed to retrieve session after ensuring directory for thread {thread_id}"
            )
        return record

    async def get_by_session_id(self, session_id: str) -> SessionRecord | None:
        """Reverse lookup: get session by Claude Code session ID."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return SessionRecord(**dict(row))

    async def list_all(
        self,
        limit: int = 50,
        origin: str | None = None,
        *,
        lifecycle_state: LifecycleState | str | None = None,
    ) -> list[SessionRecord]:
        """List all sessions ordered by most recently used.

        Closed sessions are included unless filtered out: a closed session stays
        findable and reopenable, so hiding it by default would lose it.

        Args:
            limit: Maximum number of records to return.
            origin: Optional filter by origin ('discord', 'cli'). None returns all.
            lifecycle_state: Optional filter by lifecycle state. None returns all.
        """
        conditions: list[str] = []
        params: list[object] = []
        if origin:
            conditions.append("origin = ?")
            params.append(origin)
        if lifecycle_state is not None:
            conditions.append("lifecycle_state = ?")
            params.append(_state_value(lifecycle_state))

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM sessions{where} ORDER BY last_used_at DESC LIMIT ?"  # noqa: S608
        params.append(limit)

        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [SessionRecord(**dict(row)) for row in rows]

    async def search(
        self,
        query: str | None = None,
        *,
        origin: str | None = None,
        limit: int = 50,
        thread_ids: list[int] | None = None,
        exclude_thread_ids: list[int] | None = None,
        lifecycle_state: LifecycleState | str | None = None,
    ) -> list[SessionRecord]:
        """Search sessions by keyword with optional filters.

        Args:
            query: Search term matched against summary and working_dir (LIKE).
                   Empty string or None returns all sessions.
            origin: Filter by origin ('discord', 'cli'). None returns all.
            limit: Maximum number of records to return.
            thread_ids: If set, only return sessions with these thread IDs.
            exclude_thread_ids: If set, exclude sessions with these thread IDs.
            lifecycle_state: Filter by lifecycle state. None returns open and closed alike.
        """
        conditions: list[str] = []
        params: list[object] = []

        if query:
            conditions.append("(summary LIKE ? OR working_dir LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])

        if origin:
            conditions.append("origin = ?")
            params.append(origin)

        if thread_ids is not None:
            placeholders = ",".join("?" for _ in thread_ids)
            conditions.append(f"thread_id IN ({placeholders})")
            params.extend(thread_ids)

        if exclude_thread_ids:
            placeholders = ",".join("?" for _ in exclude_thread_ids)
            conditions.append(f"thread_id NOT IN ({placeholders})")
            params.extend(exclude_thread_ids)

        if lifecycle_state is not None:
            conditions.append("lifecycle_state = ?")
            params.append(_state_value(lifecycle_state))

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM sessions{where} ORDER BY last_used_at DESC LIMIT ?"  # noqa: S608
        params.append(limit)

        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [SessionRecord(**dict(row)) for row in rows]

    async def delete(self, thread_id: int) -> bool:
        """Delete a session mapping. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            cursor = await db.execute(
                "DELETE FROM sessions WHERE thread_id = ?",
                (thread_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cleanup_old(self, days: int = 30) -> int:
        """Delete sessions older than N days. Returns count deleted."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            query = (
                "DELETE FROM sessions"
                " WHERE julianday('now', 'localtime') - julianday(last_used_at) >= ?"
            )
            cursor = await db.execute(query, (days,))
            await db.commit()
            return cursor.rowcount

    async def request_close(
        self,
        thread_id: int,
        authority: CloseAuthority | str,
    ) -> SessionRecord | None:
        """Mark that an authorized close was asked for, without closing yet.

        Used when a turn is still running: the request is durable, so the turn
        finishes and the wrap-up happens afterwards even across a restart. A
        session that is already closing or closed keeps its original request —
        a repeated close must not rewrite who asked or when.

        Returns the current record, or ``None`` if no session is bound to
        ``thread_id``. Raises ``ValueError`` for an unrecognized authority.
        """
        source = _coerce_authority(authority)
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                """UPDATE sessions
                      SET lifecycle_state = ?,
                          close_requested_at = datetime('now', 'localtime'),
                          close_authority = ?
                    WHERE thread_id = ? AND lifecycle_state = ?""",
                (
                    LifecycleState.CLOSING.value,
                    source.value,
                    thread_id,
                    LifecycleState.OPEN.value,
                ),
            )
            await db.commit()
        return await self.get(thread_id)

    async def mark_closed(
        self,
        thread_id: int,
        wrap_up: str,
        authority: CloseAuthority | str | None = None,
    ) -> SessionRecord | None:
        """Record the wrap-up and finish the close.

        Works for an idle close (no prior request) and for one that completes a
        pending request, in which case the original authority is kept. The row,
        its bound folder and its session id are all preserved — closing archives
        a conversation, it does not discard one.

        A second close is a no-op: the first wrap-up and ``closed_at`` stand.
        Raises ``ValueError`` for a blank wrap-up or an unrecognized authority.
        """
        if not wrap_up or not wrap_up.strip():
            raise ValueError("A close needs a wrap-up; pass a deterministic summary instead.")
        source = _coerce_authority(authority) if authority is not None else None

        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                """UPDATE sessions
                      SET lifecycle_state = ?,
                          wrap_up = ?,
                          closed_at = datetime('now', 'localtime'),
                          close_requested_at = COALESCE(
                              close_requested_at, datetime('now', 'localtime')
                          ),
                          close_authority = COALESCE(close_authority, ?)
                    WHERE thread_id = ? AND lifecycle_state != ?""",
                (
                    LifecycleState.CLOSED.value,
                    wrap_up,
                    source.value if source else None,
                    thread_id,
                    LifecycleState.CLOSED.value,
                ),
            )
            await db.commit()
        return await self.get(thread_id)

    async def reopen(self, thread_id: int) -> SessionRecord | None:
        """Return a closed (or closing) session to open, keeping its history.

        Clears the close markers so the session is live again and no longer
        looks pending, but keeps the stored wrap-up: it is the record of what
        the session had done by the time it was closed.
        """
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                """UPDATE sessions
                      SET lifecycle_state = ?,
                          close_requested_at = NULL,
                          close_authority = NULL,
                          closed_at = NULL,
                          last_used_at = datetime('now', 'localtime')
                    WHERE thread_id = ?""",
                (LifecycleState.OPEN.value, thread_id),
            )
            await db.commit()
        return await self.get(thread_id)

    async def list_pending_closes(self, limit: int = 50) -> list[SessionRecord]:
        """Sessions whose close was requested but never finished.

        Read on startup: each one is a turn that was interrupted mid-close and
        still owes a wrap-up.
        """
        return await self.list_all(limit=limit, lifecycle_state=LifecycleState.CLOSING)

    async def update_context_stats(
        self,
        thread_id: int,
        context_window: int,
        context_used: int,
    ) -> None:
        """Persist context window stats for a session."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                "UPDATE sessions SET context_window = ?, context_used = ? WHERE thread_id = ?",
                (context_window, context_used, thread_id),
            )
            await db.commit()


class UsageStatsRepository:
    """CRUD for rate limit usage stats (one row per rate_limit_type, upserted)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def upsert(self, info: RateLimitInfo) -> None:
        """Insert or replace the latest rate limit info for the given type."""
        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            await db.execute(
                """INSERT INTO usage_stats
                     (rate_limit_type, status, utilization, resets_at, is_using_overage)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(rate_limit_type) DO UPDATE SET
                     status = excluded.status,
                     utilization = excluded.utilization,
                     resets_at = excluded.resets_at,
                     is_using_overage = excluded.is_using_overage,
                     recorded_at = datetime('now', 'localtime')""",
                (
                    info.rate_limit_type,
                    info.status,
                    info.utilization,
                    info.resets_at,
                    int(info.is_using_overage),
                ),
            )
            await db.commit()

    async def get_latest(self) -> list[RateLimitInfo]:
        """Return all stored rate limit entries (one per type)."""
        from .types import RateLimitInfo as _RateLimitInfo

        async with aiosqlite.connect(self.db_path, timeout=DB_BUSY_TIMEOUT_SECONDS) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM usage_stats ORDER BY rate_limit_type")
            rows = await cursor.fetchall()
            return [
                _RateLimitInfo(
                    rate_limit_type=row["rate_limit_type"],
                    status=row["status"],
                    utilization=row["utilization"],
                    resets_at=row["resets_at"],
                    is_using_overage=bool(row["is_using_overage"]),
                )
                for row in rows
            ]
