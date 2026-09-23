"""The session close lifecycle: its storage, and the service that drives it.

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
    SessionRecord,
    SessionRepository,
)
from claude_discord.database.models import init_db
from claude_discord.session_lifecycle import (
    CloseAuthorityError,
    CloseAuthorization,
    CloseState,
    ReopenState,
    SessionLifecycleService,
    deterministic_wrap_up,
)

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


# --------------------------------------------------------------------------
# The close/reopen service
#
# The service is the only thing that may decide a close. It speaks to the
# world through three small ports so that Discord, Teams, or a workflow all
# get identical behaviour, and so these tests can assert the two things the
# spec actually promises: nothing is destroyed, and no close happens without
# inherited authority.
# --------------------------------------------------------------------------


class FakeSurface:
    """A conversation surface that can only archive and unarchive.

    ``lock`` and ``delete`` exist purely to fail the test if the service ever
    reaches for them: closing must leave the thread readable and reopenable.
    """

    def __init__(self) -> None:
        self.archived: list[int] = []
        self.unarchived: list[int] = []

    async def archive(self, thread_id: int) -> bool:
        self.archived.append(thread_id)
        return True

    async def unarchive(self, thread_id: int) -> bool:
        self.unarchived.append(thread_id)
        return True

    async def lock(self, thread_id: int) -> bool:  # pragma: no cover - must never run
        raise AssertionError("closing must never lock the thread")

    async def delete(self, thread_id: int) -> bool:  # pragma: no cover - must never run
        raise AssertionError("closing must never delete the thread")


class FakeTurns:
    """Tracks which threads have a model turn in flight."""

    def __init__(self, *active: int) -> None:
        self.active = set(active)
        self.waited: list[int] = []
        self.killed: list[int] = []

    async def is_active(self, thread_id: int) -> bool:
        return thread_id in self.active

    async def wait_until_idle(self, thread_id: int) -> None:
        self.waited.append(thread_id)
        self.active.discard(thread_id)


class FakeWriter:
    """A wrap-up author that can succeed, come back empty, or fall over."""

    def __init__(self, text: str | None = "wrapped up", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[int] = []

    async def summarize(self, record: SessionRecord) -> str | None:
        self.calls.append(record.thread_id)
        if self.error is not None:
            raise self.error
        return self.text


class GuardedRepository(SessionRepository):
    """A repository that refuses to be deleted from.

    The spec's hardest promise is that closing keeps the record. Asserting it
    on the stored row only proves this one code path; forbidding the call
    proves the service has no destructive path at all.
    """

    async def delete(self, thread_id: int) -> bool:  # pragma: no cover - must never run
        raise AssertionError("closing must never delete the session record")


@pytest.fixture
async def guarded_repo(tmp_path):
    db_path = str(tmp_path / "service.db")
    await init_db(db_path)
    return GuardedRepository(db_path)


def make_service(repo, *, turns=None, writer=None, surface=None):
    return SessionLifecycleService(
        repo,
        surface=surface if surface is not None else FakeSurface(),
        turns=turns if turns is not None else FakeTurns(),
        wrap_up_writer=writer if writer is not None else FakeWriter(),
    )


HUMAN = CloseAuthorization.from_interaction("user:42")


class TestCloseAuthorization:
    def test_a_person_pressing_close_is_human_authority(self):
        auth = CloseAuthorization.from_interaction("user:42")

        assert auth.source is CloseAuthority.DIRECT_INTERACTION
        assert auth.is_human
        assert not auth.is_workflow

    def test_an_explicit_user_instruction_is_human_authority(self):
        auth = CloseAuthorization.from_user_instruction("user:42")

        assert auth.source is CloseAuthority.USER_INSTRUCTION
        assert auth.is_human

    def test_a_preauthorized_workflow_is_workflow_authority(self):
        auth = CloseAuthorization.from_workflow("nightly-build", close_on_done=True)

        assert auth.source is CloseAuthority.WORKFLOW_CLOSE_ON_DONE
        assert auth.is_workflow
        assert not auth.is_human
        assert auth.workflow_id == "nightly-build"

    def test_a_workflow_without_close_on_done_has_no_authority(self):
        with pytest.raises(CloseAuthorityError):
            CloseAuthorization.from_workflow("nightly-build", close_on_done=False)

    def test_a_workflow_must_name_itself(self):
        with pytest.raises(CloseAuthorityError):
            CloseAuthorization.from_workflow("   ", close_on_done=True)

    def test_a_human_authority_cannot_borrow_a_workflow_id(self):
        with pytest.raises(CloseAuthorityError):
            CloseAuthorization(
                source=CloseAuthority.DIRECT_INTERACTION,
                actor="user:42",
                workflow_id="nightly-build",
            )

    def test_authority_must_name_an_actor(self):
        with pytest.raises(CloseAuthorityError):
            CloseAuthorization.from_interaction("  ")

    def test_a_model_cannot_invent_its_own_authority(self):
        # "The model decided it was done" is not a value this type accepts.
        with pytest.raises(CloseAuthorityError):
            CloseAuthorization(source="model_finished", actor="assistant")  # type: ignore[arg-type]


class TestCloseAnIdleSession:
    async def test_close_records_wrap_up_marks_closed_and_archives(self, guarded_repo):
        await guarded_repo.save(thread_id=1, session_id="sess-1", working_dir="/home/drew/app")
        surface = FakeSurface()
        service = make_service(guarded_repo, surface=surface)

        outcome = await service.close(1, HUMAN)

        assert outcome.state is CloseState.CLOSED
        assert outcome.wrap_up == "wrapped up"
        assert outcome.archived is True
        assert surface.archived == [1]
        assert outcome.record is not None
        assert outcome.record.lifecycle_state == LifecycleState.CLOSED.value

    async def test_close_preserves_the_record_its_folder_and_its_session_id(self, guarded_repo):
        await guarded_repo.save(thread_id=2, session_id="sess-2", working_dir="/home/drew/app")
        service = make_service(guarded_repo)

        await service.close(2, HUMAN)

        stored = await guarded_repo.get(2)
        assert stored is not None
        assert stored.session_id == "sess-2"
        assert stored.working_dir == "/home/drew/app"
        assert stored.closed_at is not None

    async def test_close_stores_which_authority_allowed_it(self, guarded_repo):
        await guarded_repo.save(thread_id=3, session_id="sess-3")
        service = make_service(guarded_repo)

        await service.close(3, CloseAuthorization.from_workflow("nightly", close_on_done=True))

        stored = await guarded_repo.get(3)
        assert stored is not None
        assert stored.close_authority == CloseAuthority.WORKFLOW_CLOSE_ON_DONE.value

    async def test_close_without_a_session_changes_nothing(self, guarded_repo):
        surface = FakeSurface()
        service = make_service(guarded_repo, surface=surface)

        outcome = await service.close(404, HUMAN)

        assert outcome.state is CloseState.NO_SESSION
        assert outcome.record is None
        assert surface.archived == []

    async def test_close_refuses_an_untyped_authority(self, guarded_repo):
        await guarded_repo.save(thread_id=4, session_id="sess-4")
        service = make_service(guarded_repo)

        with pytest.raises(CloseAuthorityError):
            await service.close(4, "because I finished")  # type: ignore[arg-type]

        stored = await guarded_repo.get(4)
        assert stored is not None
        assert stored.is_open

    async def test_close_refuses_a_missing_authority(self, guarded_repo):
        await guarded_repo.save(thread_id=5, session_id="sess-5")
        service = make_service(guarded_repo)

        with pytest.raises(CloseAuthorityError):
            await service.close(5, None)  # type: ignore[arg-type]

        stored = await guarded_repo.get(5)
        assert stored is not None
        assert stored.is_open


class TestWrapUpFallback:
    async def test_a_failed_wrap_up_falls_back_to_a_deterministic_summary(self, guarded_repo):
        await guarded_repo.save(thread_id=10, session_id="sess-10", working_dir="/home/drew/app")
        writer = FakeWriter(error=RuntimeError("model unavailable"))
        service = make_service(guarded_repo, writer=writer)

        outcome = await service.close(10, HUMAN)

        assert outcome.state is CloseState.CLOSED
        assert outcome.wrap_up
        assert "/home/drew/app" in (outcome.wrap_up or "")
        stored = await guarded_repo.get(10)
        assert stored is not None
        assert stored.wrap_up == outcome.wrap_up

    async def test_an_empty_wrap_up_falls_back_too(self, guarded_repo):
        await guarded_repo.save(thread_id=11, session_id="sess-11")
        service = make_service(guarded_repo, writer=FakeWriter(text="   "))

        outcome = await service.close(11, HUMAN)

        assert outcome.state is CloseState.CLOSED
        assert (outcome.wrap_up or "").strip()

    async def test_a_service_without_a_writer_still_closes(self, guarded_repo):
        await guarded_repo.save(thread_id=12, session_id="sess-12")
        service = SessionLifecycleService(guarded_repo, surface=FakeSurface(), turns=FakeTurns())

        outcome = await service.close(12, HUMAN)

        assert outcome.state is CloseState.CLOSED
        assert (outcome.wrap_up or "").strip()

    def test_the_deterministic_summary_is_never_blank(self):
        record = SessionRecord(
            thread_id=1,
            session_id="",
            working_dir=None,
            model=None,
            origin="discord",
            summary=None,
            created_at="2026-09-20 10:00:00",
            last_used_at="2026-09-20 10:05:00",
        )

        assert deterministic_wrap_up(record).strip()


class TestCloseDuringAnActiveTurn:
    async def test_close_during_a_turn_is_recorded_and_left_pending(self, guarded_repo):
        await guarded_repo.save(thread_id=20, session_id="sess-20")
        surface = FakeSurface()
        turns = FakeTurns(20)
        service = make_service(guarded_repo, turns=turns, surface=surface)

        outcome = await service.close(20, HUMAN)

        assert outcome.state is CloseState.PENDING
        assert outcome.wrap_up is None
        assert surface.archived == []
        stored = await guarded_repo.get(20)
        assert stored is not None
        assert stored.close_pending
        assert stored.close_requested_at is not None

    async def test_close_during_a_turn_never_kills_the_turn(self, guarded_repo):
        await guarded_repo.save(thread_id=21, session_id="sess-21")
        turns = FakeTurns(21)
        service = make_service(guarded_repo, turns=turns)

        await service.close(21, HUMAN)

        assert turns.killed == []
        assert await turns.is_active(21)

    async def test_the_pending_close_completes_once_the_turn_ends(self, guarded_repo):
        await guarded_repo.save(thread_id=22, session_id="sess-22")
        surface = FakeSurface()
        turns = FakeTurns(22)
        service = make_service(guarded_repo, turns=turns, surface=surface)
        await service.close(22, HUMAN)

        turns.active.discard(22)
        outcome = await service.complete_pending_close(22)

        assert outcome.state is CloseState.CLOSED
        assert surface.archived == [22]
        stored = await guarded_repo.get(22)
        assert stored is not None
        assert stored.is_closed

    async def test_completing_while_the_turn_still_runs_leaves_it_pending(self, guarded_repo):
        await guarded_repo.save(thread_id=23, session_id="sess-23")
        surface = FakeSurface()
        turns = FakeTurns(23)
        service = make_service(guarded_repo, turns=turns, surface=surface)
        await service.close(23, HUMAN)

        outcome = await service.complete_pending_close(23)

        assert outcome.state is CloseState.PENDING
        assert surface.archived == []

    async def test_completing_a_close_nobody_asked_for_does_nothing(self, guarded_repo):
        await guarded_repo.save(thread_id=24, session_id="sess-24")
        surface = FakeSurface()
        service = make_service(guarded_repo, surface=surface)

        outcome = await service.complete_pending_close(24)

        assert outcome.state is CloseState.NOT_REQUESTED
        assert surface.archived == []
        stored = await guarded_repo.get(24)
        assert stored is not None
        assert stored.is_open

    async def test_close_when_idle_waits_for_the_turn_then_closes(self, guarded_repo):
        await guarded_repo.save(thread_id=25, session_id="sess-25")
        turns = FakeTurns(25)
        surface = FakeSurface()
        service = make_service(guarded_repo, turns=turns, surface=surface)

        outcome = await service.close_when_idle(25, HUMAN)

        assert turns.waited == [25]
        assert outcome.state is CloseState.CLOSED
        assert surface.archived == [25]

    async def test_close_when_idle_on_an_idle_session_does_not_wait(self, guarded_repo):
        await guarded_repo.save(thread_id=26, session_id="sess-26")
        turns = FakeTurns()
        service = make_service(guarded_repo, turns=turns)

        outcome = await service.close_when_idle(26, HUMAN)

        assert turns.waited == []
        assert outcome.state is CloseState.CLOSED

    async def test_a_restart_reconciles_every_unfinished_close(self, guarded_repo):
        await guarded_repo.save(thread_id=27, session_id="sess-27")
        await guarded_repo.save(thread_id=28, session_id="sess-28")
        await guarded_repo.request_close(27, CloseAuthority.DIRECT_INTERACTION)
        await guarded_repo.request_close(28, CloseAuthority.WORKFLOW_CLOSE_ON_DONE)
        surface = FakeSurface()
        # After a restart nothing is running, whatever was running before.
        service = make_service(guarded_repo, turns=FakeTurns(), surface=surface)

        outcomes = await service.reconcile_pending_closes()

        assert [o.state for o in outcomes] == [CloseState.CLOSED, CloseState.CLOSED]
        assert sorted(surface.archived) == [27, 28]
        assert await guarded_repo.list_pending_closes() == []

    async def test_reconciliation_leaves_a_still_running_session_pending(self, guarded_repo):
        await guarded_repo.save(thread_id=29, session_id="sess-29")
        await guarded_repo.request_close(29, CloseAuthority.DIRECT_INTERACTION)
        service = make_service(guarded_repo, turns=FakeTurns(29))

        outcomes = await service.reconcile_pending_closes()

        assert [o.state for o in outcomes] == [CloseState.PENDING]
        assert len(await guarded_repo.list_pending_closes()) == 1


class TestCloseIsIdempotent:
    async def test_a_second_close_reports_the_state_without_rewriting_it(self, guarded_repo):
        await guarded_repo.save(thread_id=30, session_id="sess-30")
        surface = FakeSurface()
        writer = FakeWriter()
        service = make_service(guarded_repo, writer=writer, surface=surface)
        first = await service.close(30, HUMAN)

        second = await service.close(30, HUMAN)

        assert second.state is CloseState.ALREADY_CLOSED
        assert second.wrap_up == first.wrap_up
        assert writer.calls == [30]
        assert surface.archived == [30]

    async def test_a_second_close_keeps_the_original_closed_at(self, guarded_repo):
        await guarded_repo.save(thread_id=31, session_id="sess-31")
        service = make_service(guarded_repo)
        first = await service.close(31, HUMAN)

        await service.close(31, CloseAuthorization.from_workflow("other", close_on_done=True))

        stored = await guarded_repo.get(31)
        assert stored is not None
        assert first.record is not None
        assert stored.closed_at == first.record.closed_at
        assert stored.close_authority == CloseAuthority.DIRECT_INTERACTION.value

    async def test_a_second_close_during_a_turn_keeps_the_first_request(self, guarded_repo):
        await guarded_repo.save(thread_id=32, session_id="sess-32")
        service = make_service(guarded_repo, turns=FakeTurns(32))
        first = await service.close(32, HUMAN)

        second = await service.close(32, CloseAuthorization.from_user_instruction("user:9"))

        assert second.state is CloseState.PENDING
        assert first.record is not None
        assert second.record is not None
        assert second.record.close_requested_at == first.record.close_requested_at
        assert second.record.close_authority == CloseAuthority.DIRECT_INTERACTION.value

    async def test_completing_a_finished_close_is_a_no_op(self, guarded_repo):
        await guarded_repo.save(thread_id=33, session_id="sess-33")
        surface = FakeSurface()
        writer = FakeWriter()
        service = make_service(guarded_repo, writer=writer, surface=surface)
        await service.close(33, HUMAN)

        outcome = await service.complete_pending_close(33)

        assert outcome.state is CloseState.ALREADY_CLOSED
        assert writer.calls == [33]
        assert surface.archived == [33]


class TestReopenService:
    async def test_reopen_unarchives_and_marks_the_session_open(self, guarded_repo):
        await guarded_repo.save(thread_id=40, session_id="sess-40", working_dir="/home/drew/app")
        surface = FakeSurface()
        service = make_service(guarded_repo, surface=surface)
        await service.close(40, HUMAN)

        outcome = await service.reopen(40)

        assert outcome.state is ReopenState.REOPENED
        assert outcome.unarchived is True
        assert surface.unarchived == [40]
        assert outcome.record is not None
        assert outcome.record.is_open

    async def test_reopen_keeps_the_conversation_and_its_folder(self, guarded_repo):
        await guarded_repo.save(thread_id=41, session_id="sess-41", working_dir="/home/drew/app")
        service = make_service(guarded_repo)
        await service.close(41, HUMAN)

        outcome = await service.reopen(41)

        assert outcome.record is not None
        assert outcome.record.session_id == "sess-41"
        assert outcome.record.working_dir == "/home/drew/app"
        assert outcome.record.wrap_up == "wrapped up"
        assert outcome.resume_session_id == "sess-41"
        assert outcome.requires_fresh_session is False

    async def test_reopening_an_open_session_changes_nothing(self, guarded_repo):
        await guarded_repo.save(thread_id=42, session_id="sess-42")
        surface = FakeSurface()
        service = make_service(guarded_repo, surface=surface)

        outcome = await service.reopen(42)

        assert outcome.state is ReopenState.ALREADY_OPEN
        assert surface.unarchived == []

    async def test_reopening_an_unknown_session_reports_it(self, guarded_repo):
        service = make_service(guarded_repo)

        outcome = await service.reopen(404)

        assert outcome.state is ReopenState.NO_SESSION
        assert outcome.record is None

    async def test_reopen_cancels_a_close_that_never_finished(self, guarded_repo):
        await guarded_repo.save(thread_id=43, session_id="sess-43")
        service = make_service(guarded_repo, turns=FakeTurns(43))
        await service.close(43, HUMAN)

        outcome = await service.reopen(43)

        assert outcome.state is ReopenState.REOPENED
        stored = await guarded_repo.get(43)
        assert stored is not None
        assert stored.is_open
        assert not stored.close_pending

    async def test_reopen_under_the_same_backend_resumes_the_stored_session(self, guarded_repo):
        await guarded_repo.save(thread_id=44, session_id="sess-44", backend="claude")
        service = make_service(guarded_repo)
        await service.close(44, HUMAN)

        outcome = await service.reopen(44, backend="claude")

        assert outcome.requires_fresh_session is False
        assert outcome.resume_session_id == "sess-44"

    async def test_reopen_under_another_backend_withholds_the_session_id(self, guarded_repo):
        await guarded_repo.save(thread_id=45, session_id="sess-45", backend="codex")
        service = make_service(guarded_repo)
        await service.close(45, HUMAN)

        outcome = await service.reopen(45, backend="claude")

        assert outcome.requires_fresh_session is True
        assert outcome.resume_session_id is None
        assert outcome.record is not None
        assert outcome.record.session_id == "sess-45"

    async def test_a_record_without_a_stored_backend_is_assumed_compatible(self, guarded_repo):
        await guarded_repo.save(thread_id=46, session_id="sess-46")
        service = make_service(guarded_repo)
        await service.close(46, HUMAN)

        outcome = await service.reopen(46, backend="claude")

        assert outcome.requires_fresh_session is False
        assert outcome.resume_session_id == "sess-46"


class TestServiceWithoutASurface:
    """The service is frontend-neutral: no archiver is a supported wiring."""

    async def test_close_and_reopen_work_with_no_surface(self, guarded_repo):
        await guarded_repo.save(thread_id=50, session_id="sess-50")
        service = SessionLifecycleService(guarded_repo, turns=FakeTurns())

        closed = await service.close(50, HUMAN)
        reopened = await service.reopen(50)

        assert closed.state is CloseState.CLOSED
        assert closed.archived is False
        assert reopened.state is ReopenState.REOPENED
        assert reopened.unarchived is False

    async def test_a_service_with_no_turn_tracker_treats_sessions_as_idle(self, guarded_repo):
        await guarded_repo.save(thread_id=51, session_id="sess-51")
        service = SessionLifecycleService(guarded_repo)

        outcome = await service.close(51, HUMAN)

        assert outcome.state is CloseState.CLOSED


class TestCloseOutcomeText:
    """The sentence every frontend sends is decided once, next to the states."""

    def test_every_state_has_a_distinct_sentence(self):
        from claude_discord.session_lifecycle import CloseOutcome, close_outcome_text

        texts = {state: close_outcome_text(CloseOutcome(state=state)) for state in CloseState}
        assert len(set(texts.values())) == len(CloseState)
        assert "still running" in texts[CloseState.PENDING]
        assert "already closed" in texts[CloseState.ALREADY_CLOSED]
        assert "nothing to close" in texts[CloseState.NO_SESSION]

    def test_closed_text_quotes_the_wrap_up(self):
        from claude_discord.session_lifecycle import CloseOutcome, close_outcome_text

        text = close_outcome_text(
            CloseOutcome(state=CloseState.CLOSED, wrap_up="Shipped the fix.", archived=True)
        )
        assert "archived" in text
        assert "Shipped the fix." in text
