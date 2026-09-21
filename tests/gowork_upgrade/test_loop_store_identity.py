"""T04 — LoopStore keeps every build, not one per repository.

Two plans built in the same project used to overwrite each other because the
store keyed records by ``repo_dir``. A build now has a stable identity that
survives a restart, legacy records get the same identity every time they are
read, and migrating the file is repeatable and loses no run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.loop_store import LoopRecord, LoopStore


def _rec(tmp_path: Path, plan: str = "PLAN.md", thread: int = 11, repo: str = "proj") -> LoopRecord:
    return LoopRecord(
        repo_dir=str(tmp_path / repo),
        plan_path=str(tmp_path / repo / plan),
        copy_path=str(tmp_path / f"copy-{thread}"),
        copy_plan=str(tmp_path / f"copy-{thread}" / plan),
        branch=f"gowork/{plan}-{thread}",
        worker_thread_id=thread,
        report_channel_id=22,
    )


def test_a_build_has_a_stable_identity_derived_from_its_worker_thread(tmp_path: Path) -> None:
    record = _rec(tmp_path)
    assert record.build_id == "thread-11"
    assert LoopRecord(**{**record.__dict__, "build_id": "custom"}).build_id == "custom"


def test_two_plans_for_one_repo_survive_reopen(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loops.json")
    store.save(_rec(tmp_path, "PLAN-a.md", thread=11))
    store.save(_rec(tmp_path, "PLAN-b.md", thread=12))

    again = LoopStore(tmp_path / "loops.json").all()

    assert [r.build_id for r in again] == ["thread-11", "thread-12"]
    assert {r.repo_dir for r in again} == {str(tmp_path / "proj")}
    assert [r.plan_path for r in store.for_repo(str(tmp_path / "proj"))] == [
        str(tmp_path / "proj" / "PLAN-a.md"),
        str(tmp_path / "proj" / "PLAN-b.md"),
    ]


def test_save_replaces_the_same_build_and_remove_forgets_only_it(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loops.json")
    store.save(_rec(tmp_path, "PLAN-a.md", thread=11))
    store.save(_rec(tmp_path, "PLAN-b.md", thread=12))
    store.save(LoopRecord(**{**_rec(tmp_path, "PLAN-a.md", thread=11).__dict__, "model": "opus"}))

    assert [(r.build_id, r.model) for r in store.all()] == [
        ("thread-12", None),
        ("thread-11", "opus"),
    ]
    store.remove("thread-11")
    assert [r.build_id for r in store.all()] == ["thread-12"]
    assert store.get("thread-11") is None
    assert store.get("thread-12") is not None


def test_removing_by_repo_dir_still_works_for_old_callers(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loops.json")
    store.save(_rec(tmp_path, thread=11))
    store.save(_rec(tmp_path, thread=12, repo="other"))
    store.remove(str(tmp_path / "proj"))
    assert [r.build_id for r in store.all()] == ["thread-12"]


def test_legacy_records_load_with_the_same_identity_every_time(tmp_path: Path) -> None:
    legacy = {k: v for k, v in _rec(tmp_path).__dict__.items() if k != "build_id"}
    (tmp_path / "loops.json").write_text(json.dumps([legacy]), encoding="utf-8")
    store = LoopStore(tmp_path / "loops.json")

    first = store.all()
    second = store.all()

    assert first == second
    assert first[0].build_id == "thread-11"
    assert "build_id" not in json.loads((tmp_path / "loops.json").read_text())[0]  # read-only


def test_migration_is_repeatable_and_loses_no_run(tmp_path: Path) -> None:
    legacy_a = {
        k: v for k, v in _rec(tmp_path, "PLAN-a.md", 11).__dict__.items() if k != "build_id"
    }
    legacy_b = {
        k: v for k, v in _rec(tmp_path, "PLAN-b.md", 12).__dict__.items() if k != "build_id"
    }
    (tmp_path / "loops.json").write_text(json.dumps([legacy_a, legacy_b]), encoding="utf-8")
    store = LoopStore(tmp_path / "loops.json")

    assert store.migrate() == 2
    on_disk = json.loads((tmp_path / "loops.json").read_text())
    assert [item["build_id"] for item in on_disk] == ["thread-11", "thread-12"]
    assert store.migrate() == 0
    assert json.loads((tmp_path / "loops.json").read_text()) == on_disk
    assert [r.plan_path for r in store.all()] == [legacy_a["plan_path"], legacy_b["plan_path"]]


def test_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LoopStore(tmp_path / "loops.json")
    store.save(_rec(tmp_path, thread=11))
    before = (tmp_path / "loops.json").read_text()

    def boom(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        store.save(_rec(tmp_path, thread=12))
    assert (tmp_path / "loops.json").read_text() == before
