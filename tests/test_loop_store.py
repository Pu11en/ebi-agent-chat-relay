"""Tests for claude_code_core.loop_store — running builds survive a bot restart."""

from __future__ import annotations

from pathlib import Path

from claude_code_core.loop_store import LoopRecord, LoopStore


def _rec(tmp_path: Path, repo: str = "proj") -> LoopRecord:
    return LoopRecord(
        repo_dir=str(tmp_path / repo),
        plan_path=str(tmp_path / repo / "PLAN.md"),
        copy_path=str(tmp_path / "copy"),
        copy_plan=str(tmp_path / "copy" / "PLAN.md"),
        branch="gowork/plan-1",
        worker_thread_id=11,
        report_channel_id=22,
        notify_user_id=33,
        harness="claude",
        model="sonnet",
    )


def test_round_trip(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loops.json")
    store.save(_rec(tmp_path))
    again = LoopStore(tmp_path / "loops.json").all()
    assert again == [_rec(tmp_path)]


def test_save_replaces_the_same_project_and_remove_forgets_it(tmp_path: Path) -> None:
    store = LoopStore(tmp_path / "loops.json")
    store.save(_rec(tmp_path))
    store.save(_rec(tmp_path, "other"))
    store.save(_rec(tmp_path))
    assert len(store.all()) == 2
    store.remove(str(tmp_path / "proj"))
    assert [r.repo_dir for r in store.all()] == [str(tmp_path / "other")]


def test_missing_or_broken_file_means_nothing_to_resume(tmp_path: Path) -> None:
    assert LoopStore(tmp_path / "nope.json").all() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert LoopStore(bad).all() == []
