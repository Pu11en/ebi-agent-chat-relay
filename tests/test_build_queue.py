"""Tests for claude_code_core.build_queue — builds waiting in line."""

from __future__ import annotations

from pathlib import Path

from claude_code_core import build_queue as bq


def test_the_line_survives_a_restart(tmp_path: Path) -> None:
    q = bq.BuildQueue(tmp_path / "queue.json")
    assert q.add(bq.QueueItem(plan_path="/p/A.md", report_id=1)) == 1
    assert q.add(bq.QueueItem(plan_path="/p/B.md", report_id=1)) == 2
    again = bq.BuildQueue(tmp_path / "queue.json")
    assert [i.plan_path for i in again.state.waiting] == ["/p/A.md", "/p/B.md"]


def test_history_and_the_morning_summary(tmp_path: Path) -> None:
    q = bq.BuildQueue(tmp_path / "queue.json")
    item = bq.QueueItem(plan_path="/p/A.md", report_id=1)
    q.add(item)
    q.take(item)
    q.started(item, "realpage", 77)
    q.note(77, "kept in your project ✅")
    text = bq.morning_summary(q.unreported(), waiting=2)
    assert "A.md" in text and "realpage" in text and "kept in your project" in text
    assert "<#77>" in text and "2 more" in text
    q.mark_reported("2026-09-15")
    assert q.unreported() == []


def test_nothing_to_say_means_no_summary() -> None:
    assert bq.morning_summary([], waiting=0) == ""


def test_a_broken_file_starts_an_empty_line(tmp_path: Path) -> None:
    (tmp_path / "queue.json").write_text("{not json")
    assert bq.BuildQueue(tmp_path / "queue.json").state.waiting == []
