"""Storage for the session close lifecycle.

Close is a durable transition, not a variant of stop: a session leaves `open`
for `closing` when someone with authority asks for it, and reaches `closed`
only once a wrap-up has been recorded. Every test here is about what survives —
a bot restart mid-close, a second `/close` on an already-closed session, a
database that predates these columns — because the lifecycle is worthless if
the record it lives on can be lost or silently overwritten.
"""

from __future__ import annotations

import aiosqlite
import pytest

from claude_code_core.session_repo import (
    CloseAuthority,
    LifecycleState,
    SessionRepository,
)
from claude_discord.database.models import init_db

LEGACY_SCHEMA = """
CREATE TABLE sessions (
    thread_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    working_dir TEXT,
    model TEXT,
    origin TEXT NOT NULL DEFAULT 'discord',
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_used_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


@pytest.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "lifecycle.db")
    await init_db(db_path)
    return SessionRepository(db_path)


class TestMigration:
    async def test_fresh_database_defaults_to_open(self, repo):
        record = await repo.save(thread_id=1, session_id="sess-1")

        assert record.lifecycle_state == LifecycleState.OPEN.value
        assert record.close_requested_at is None
        assert record.close_authority is None
        assert record.wrap_up is None
        assert record.closed_at is None

    async def test_existing_rows_survive_migration_as_open(self, tmp_path):
        """A database written before this change keeps its rows and gains `open`."""
        db_path = str(tmp_path / "legacy.db")
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(LEGACY_SCHEMA)
            await db.execute(
                "INSERT INTO sessions (thread_id, session_id, working_dir) VALUES (?, ?, ?)",
                (77, "old-session", "/home/drew/legacy"),
            )
            await db.commit()

        await init_db(db_path)

        record = await SessionRepository(db_path).get(77)
        assert record is not None
        assert record.session_id == "old-session"
        assert record.working_dir == "/home/drew/legacy"
        assert record.lifecycle_state == LifecycleState.OPEN.value
        assert record.is_open

    async def test_init_db_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "twice.db")
        await init_db(db_path)
        repo = SessionRepository(db_path)
        await repo.save(thread_id=5, session_id="sess-5")

        await init_db(db_path)

        record = await repo.get(5)
        assert record is not None
        assert record.lifecycle_state == LifecycleState.OPEN.value


class TestRequestClose:
    async def test_records_pending_close_without_closing(self, repo):
        await repo.save(thread_id=10, session_id="sess-10", working_dir="/tmp/p")

        record = await repo.request_close(10, CloseAuthority.DIRECT_INTERACTION)

        assert record is not None
        assert record.lifecycle_state == LifecycleState.CLOSING.value
        assert record.close_requested_at is not None
        assert record.close_authority == CloseAuthority.DIRECT_INTERACTION.value
        assert record.close_pending
        assert not record.is_closed
        assert record.wrap_up is None

    async def test_repeated_request_keeps_the_first_authority(self, repo):
        await repo.save(thread_id=11, session_id="sess-11")
        first = await repo.request_close(11, CloseAuthority.USER_INSTRUCTION)
        assert first is not None

        again = await repo.request_close(11, CloseAuthority.WORKFLOW_CLOSE_ON_DONE)

        assert again is not None
        assert again.close_authority == CloseAuthority.USER_INSTRUCTION.value
        assert again.close_requested_at == first.close_requested_at

    async def test_request_on_closed_session_changes_nothing(self, repo):
        await repo.save(thread_id=12, session_id="sess-12")
        await repo.mark_closed(12, wrap_up="done", authority=CloseAuthority.DIRECT_INTERACTION)

        record = await repo.request_close(12, CloseAuthority.WORKFLOW_CLOSE_ON_DONE)

        assert record is not None
        assert record.lifecycle_state == LifecycleState.CLOSED.value
        assert record.wrap_up == "done"
        assert record.close_authority == CloseAuthority.DIRECT_INTERACTION.value

    async def test_unknown_thread_returns_none(self, repo):
        assert await repo.request_close(999, CloseAuthority.DIRECT_INTERACTION) is None

    @pytest.mark.parametrize("authority", [None, "", "because-i-felt-done", "model_preference"])
    async def test_authority_must_be_a_known_source(self, repo, authority):
        """A model's own preference is not an authority; storage fails closed."""
        await repo.save(thread_id=13, session_id="sess-13")

        with pytest.raises(ValueError):
            await repo.request_close(13, authority)  # type: ignore[arg-type]

        record = await repo.get(13)
        assert record is not None
        assert record.lifecycle_state == LifecycleState.OPEN.value

    async def test_accepts_the_authority_value_as_a_string(self, repo):
        await repo.save(thread_id=14, session_id="sess-14")

        record = await repo.request_close(14, "workflow_close_on_done")

        assert record is not None
        assert record.close_authority == CloseAuthority.WORKFLOW_CLOSE_ON_DONE.value

    async def test_list_pending_closes_returns_only_closing_sessions(self, repo):
        await repo.save(thread_id=20, session_id="a")
        await repo.save(thread_id=21, session_id="b")
        await repo.save(thread_id=22, session_id="c")
        await repo.request_close(21, CloseAuthority.DIRECT_INTERACTION)
        await repo.request_close(22, CloseAuthority.USER_INSTRUCTION)
        await repo.mark_closed(22, wrap_up="finished")

        pending = await repo.list_pending_closes()

        assert [r.thread_id for r in pending] == [21]


class TestMarkClosed:
    async def test_idle_close_stores_wrap_up_and_keeps_continuity(self, repo):
        await repo.save(
            thread_id=30,
            session_id="sess-30",
            working_dir="/home/drew/project",
            model="opus",
        )

        record = await repo.mark_closed(
            30,
            wrap_up="Fixed the importer and left tests green.",
            authority=CloseAuthority.DIRECT_INTERACTION,
        )

        assert record is not None
        assert record.lifecycle_state == LifecycleState.CLOSED.value
        assert record.is_closed
        assert not record.close_pending
        assert record.closed_at is not None
        assert record.wrap_up == "Fixed the importer and left tests green."
        assert record.session_id == "sess-30"
        assert record.working_dir == "/home/drew/project"
        assert record.model == "opus"

    async def test_close_after_a_pending_request_keeps_its_authority(self, repo):
        await repo.save(thread_id=31, session_id="sess-31")
        await repo.request_close(31, CloseAuthority.WORKFLOW_CLOSE_ON_DONE)

        record = await repo.mark_closed(31, wrap_up="Turn finished, then closed.")

        assert record is not None
        assert record.lifecycle_state == LifecycleState.CLOSED.value
        assert record.close_authority == CloseAuthority.WORKFLOW_CLOSE_ON_DONE.value
        assert record.wrap_up == "Turn finished, then closed."

    async def test_second_close_does_not_duplicate_the_wrap_up(self, repo):
        await repo.save(thread_id=32, session_id="sess-32")
        first = await repo.mark_closed(32, wrap_up="First summary.")
        assert first is not None

        again = await repo.mark_closed(32, wrap_up="Second summary.")

        assert again is not None
        assert again.wrap_up == "First summary."
        assert again.closed_at == first.closed_at

    async def test_deterministic_summary_is_accepted_when_the_model_cannot_write_one(self, repo):
        await repo.save(thread_id=33, session_id="sess-33")

        record = await repo.mark_closed(33, wrap_up="Closed with 4 messages; no wrap-up available.")

        assert record is not None
        assert record.is_closed
        assert record.wrap_up == "Closed with 4 messages; no wrap-up available."

    async def test_wrap_up_must_not_be_empty(self, repo):
        await repo.save(thread_id=34, session_id="sess-34")

        with pytest.raises(ValueError):
            await repo.mark_closed(34, wrap_up="   ")

        record = await repo.get(34)
        assert record is not None
        assert record.lifecycle_state == LifecycleState.OPEN.value

    async def test_unknown_thread_returns_none(self, repo):
        assert await repo.mark_closed(998, wrap_up="nothing here") is None

    async def test_closing_never_deletes_the_record(self, repo):
        await repo.save(thread_id=35, session_id="sess-35", working_dir="/keep/me")
        await repo.mark_closed(35, wrap_up="bye")

        assert await repo.get(35) is not None
        assert await repo.get_by_session_id("sess-35") is not None


class TestReopen:
    async def test_reopen_clears_the_close_markers_and_keeps_the_wrap_up(self, repo):
        await repo.save(thread_id=40, session_id="sess-40", working_dir="/home/drew/p")
        await repo.request_close(40, CloseAuthority.DIRECT_INTERACTION)
        await repo.mark_closed(40, wrap_up="Wrapped up.")

        record = await repo.reopen(40)

        assert record is not None
        assert record.lifecycle_state == LifecycleState.OPEN.value
        assert record.is_open
        assert record.close_requested_at is None
        assert record.close_authority is None
        assert record.closed_at is None
        assert record.wrap_up == "Wrapped up."
        assert record.session_id == "sess-40"
        assert record.working_dir == "/home/drew/p"

    async def test_reopen_cancels_a_pending_close(self, repo):
        await repo.save(thread_id=41, session_id="sess-41")
        await repo.request_close(41, CloseAuthority.USER_INSTRUCTION)

        record = await repo.reopen(41)

        assert record is not None
        assert record.lifecycle_state == LifecycleState.OPEN.value
        assert record.close_requested_at is None
        assert await repo.list_pending_closes() == []

    async def test_reopen_of_an_open_session_is_harmless(self, repo):
        await repo.save(thread_id=42, session_id="sess-42")

        record = await repo.reopen(42)

        assert record is not None
        assert record.lifecycle_state == LifecycleState.OPEN.value

    async def test_unknown_thread_returns_none(self, repo):
        assert await repo.reopen(997) is None


class TestQueries:
    async def test_closed_sessions_stay_listed(self, repo):
        await repo.save(thread_id=50, session_id="open-one")
        await repo.save(thread_id=51, session_id="closed-one")
        await repo.mark_closed(51, wrap_up="done")

        listed = await repo.list_all()

        assert {r.thread_id for r in listed} == {50, 51}

    async def test_list_all_can_filter_by_lifecycle_state(self, repo):
        await repo.save(thread_id=60, session_id="open-one")
        await repo.save(thread_id=61, session_id="closed-one")
        await repo.mark_closed(61, wrap_up="done")

        assert [r.thread_id for r in await repo.list_all(lifecycle_state=LifecycleState.OPEN)] == [
            60
        ]
        assert [
            r.thread_id for r in await repo.list_all(lifecycle_state=LifecycleState.CLOSED)
        ] == [61]

    async def test_search_can_filter_by_lifecycle_state(self, repo):
        await repo.save(thread_id=70, session_id="a", working_dir="/home/drew/alpha")
        await repo.save(thread_id=71, session_id="b", working_dir="/home/drew/alpha")
        await repo.mark_closed(71, wrap_up="done")

        found = await repo.search("alpha", lifecycle_state=LifecycleState.CLOSED)

        assert [r.thread_id for r in found] == [71]

    async def test_search_without_a_filter_returns_both(self, repo):
        await repo.save(thread_id=80, session_id="a", working_dir="/home/drew/beta")
        await repo.save(thread_id=81, session_id="b", working_dir="/home/drew/beta")
        await repo.mark_closed(81, wrap_up="done")

        found = await repo.search("beta")

        assert {r.thread_id for r in found} == {80, 81}

    async def test_save_does_not_reopen_a_closed_session(self, repo):
        """Storage never decides a lifecycle transition as a side effect."""
        await repo.save(thread_id=90, session_id="sess-90")
        await repo.mark_closed(90, wrap_up="done")

        await repo.save(thread_id=90, session_id="sess-90b", model="sonnet")

        record = await repo.get(90)
        assert record is not None
        assert record.lifecycle_state == LifecycleState.CLOSED.value
        assert record.session_id == "sess-90b"

    async def test_ensure_working_dir_starts_a_row_as_open(self, repo):
        record = await repo.ensure_working_dir(thread_id=91, working_dir="/home/drew/new")

        assert record.lifecycle_state == LifecycleState.OPEN.value
