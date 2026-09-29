"""The consistency diagnostic reads everything and changes nothing (task 5.1)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from claude_discord.consistency_check import inspect_database
from claude_discord.database.models import init_db
from claude_discord.database.repository import SessionRepository
from claude_discord.database.settings_repo import SettingsRepository


def digest(path: Path) -> dict[str, str]:
    """Every database file, including WAL/SHM side files, byte for byte."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.parent.glob(path.name + "*"))
    }


@pytest.fixture
async def db(tmp_path: Path) -> Path:
    path = tmp_path / "sessions.db"
    await init_db(str(path))
    sessions, settings = SessionRepository(str(path)), SettingsRepository(str(path))
    # 1: tagged user session. 2: untagged user session while words are free.
    # 3: internal worker, legitimately untagged. 4: closed session still holding a tag.
    # 5 and 6: two open threads answering to one word. 7: closing, archive owed.
    for thread_id in range(1, 8):
        await sessions.save(thread_id, f"native-{thread_id}")
    await settings.set("voice_label:1", "luffy")
    await settings.set("voice_addressable:3", "false")
    await sessions.request_close(4, "direct_interaction")
    await sessions.mark_closed(4, "done", archive_pending=True)
    await settings.set("voice_label:4", "zoro")
    await settings.set("voice_label:5", "nami")
    await settings.set("voice_label:6", "nami")
    await sessions.request_close(7, "direct_interaction")
    return path


async def test_inspection_changes_no_byte_and_calls_no_process(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_process(*args: object, **kwargs: object) -> None:
        raise AssertionError("a diagnostic must not start any process or model")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    before = digest(db)
    report = inspect_database(db)
    assert digest(db) == before

    assert report.available and report.coverage == "all 7 session rows"
    assert report.closed_holding_tags == [4]
    assert report.duplicate_tags == {"nami": [5, 6]}
    assert report.untagged_user_sessions == [2, 7]
    assert report.free_tags == 7
    assert report.untagged_workers == [3]
    assert report.pending_closes == [7]
    assert report.pending_archives == [4]
    assert [problem.split(":")[0] for problem in report.problems] == [
        "closed sessions holding tags",
        "tags shared by several open threads",
        "open user sessions untagged while tags are free",
    ]


async def test_pool_exhaustion_is_not_reported_as_a_failure(tmp_path: Path) -> None:
    path = tmp_path / "sessions.db"
    await init_db(str(path))
    sessions, settings = SessionRepository(str(path)), SettingsRepository(str(path))
    words = ["luffy", "zoro", "nami", "sanji", "chopper", "franky", "jinbe", "usopp"]
    words += ["shanks", "mihawk"]
    for thread_id in range(1, 12):
        await sessions.save(thread_id, f"native-{thread_id}")
    for thread_id, word in enumerate(words, start=1):
        await settings.set(f"voice_label:{thread_id}", word)
    report = inspect_database(path)
    assert report.untagged_user_sessions == [11]
    assert report.free_tags == 0
    assert report.problems == []


def test_missing_database_is_reported_unavailable(tmp_path: Path) -> None:
    missing = tmp_path / "absent.db"
    report = inspect_database(missing)
    assert not report.available
    assert "unavailable" in report.coverage
    assert not missing.exists(), "inspection must not create a database"


async def test_live_wal_database_and_log_bytes_are_unchanged(db: Path) -> None:
    """While the bot holds the database open, only SQLite's reader index is shared."""
    import sqlite3

    writer = sqlite3.connect(db)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE sessions SET summary = 'live' WHERE thread_id = 1")
        writer.commit()
        wal = db.with_name(db.name + "-wal")
        assert wal.exists()
        before = {p: p.read_bytes() for p in (db, wal)}
        report = inspect_database(db)
        assert report.available and report.coverage == "all 7 session rows"
        assert {p: p.read_bytes() for p in (db, wal)} == before
    finally:
        writer.close()


def test_older_schema_without_archive_state_is_still_inspected(tmp_path: Path) -> None:
    """The live database predates the archive outbox; report what it has."""
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE sessions (thread_id INTEGER PRIMARY KEY, session_id TEXT, "
            "lifecycle_state TEXT NOT NULL DEFAULT 'open')"
        )
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO sessions VALUES (1, 'a', 'closed')")
        conn.execute("INSERT INTO settings VALUES ('voice_label:1', 'zoro')")
    conn.close()
    report = inspect_database(path)
    assert report.available
    assert report.closed_holding_tags == [1]
    assert "archive state not recorded" in report.coverage
