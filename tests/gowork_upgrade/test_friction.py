"""T25 — the build keeps count of what slowed it, with identity, never with invented totals."""

from __future__ import annotations

from pathlib import Path

from claude_code_core.gowork_friction import (
    FrictionEvent,
    append_friction,
    friction_report,
    friction_summary,
    read_friction,
)


def _event(kind: str, task: str = "t1", detail: str = "", attempt: int = 1) -> FrictionEvent:
    return FrictionEvent(
        kind=kind,  # type: ignore[arg-type]
        build_id="thread-1",
        task_id=task,
        attempt_id=f"thread-1:{task}:{attempt}",
        plan_id="p",
        plan_version=2,
        detail=detail,
    )


def test_events_round_trip_and_keep_their_identity(tmp_path: Path) -> None:
    path = tmp_path / "friction.jsonl"
    append_friction(path, _event("repair", detail="tests failed"))
    append_friction(path, _event("review", task="t2", detail="changes: no version field"))
    events = read_friction(path)
    assert [e.kind for e in events] == ["repair", "review"]
    assert events[0].attempt_id == "thread-1:t1:1" and events[0].plan_version == 2
    assert events[1].at  # stamped


def test_counts_are_repeatable_and_carry_no_token_or_cost_totals(tmp_path: Path) -> None:
    path = tmp_path / "friction.jsonl"
    for event in (
        _event("queue_wait", detail="12s"),
        _event("queue_wait", task="t2", detail="3s"),
        _event("dependency_wait", task="t3", detail="waited for t1"),
        _event("repair", detail="tests failed"),
        _event("repair", task="t2", detail="lint"),
        _event("review", task="t1", detail="approve"),
        _event("review", task="t2", detail="changes: unclear"),
        _event("question", task="t2", detail="stuck"),
        _event("question", task="t2", detail="stuck again", attempt=2),
        _event("capacity", task="", detail="constrained: paused starts"),
        _event("rework", task="t1", detail="plan changed to v3", attempt=2),
    ):
        append_friction(path, event)
    summary = friction_summary(read_friction(path))
    assert summary == friction_summary(read_friction(path))  # deterministic
    assert summary["queue_wait"] == 2 and summary["dependency_wait"] == 1
    assert summary["repair"] == 2 and summary["review"] == 2 and summary["review_changes"] == 1
    assert summary["question"] == 2 and summary["question_repeat"] == 1
    assert summary["capacity"] == 1 and summary["rework"] == 1
    assert not any(k in summary for k in ("tokens", "cost", "usd"))


def test_the_report_is_plain_sentences_about_this_build_only(tmp_path: Path) -> None:
    path = tmp_path / "friction.jsonl"
    append_friction(path, _event("repair", detail="tests failed"))
    append_friction(path, _event("question", task="t2", detail="stuck"))
    append_friction(path, _event("question", task="t2", detail="stuck", attempt=2))
    append_friction(path, _event("capacity", task="", detail="critical: shed one worker"))
    other = FrictionEvent(
        kind="repair",
        build_id="thread-9",
        task_id="x",
        attempt_id="thread-9:x:1",
        plan_id="p",
        plan_version=1,
    )
    append_friction(path, other)

    lines = friction_report(read_friction(path), build_id="thread-1")
    assert "1 task needed a repair (t1: tests failed)" in lines
    assert "t2 was asked about twice" in " ".join(lines)
    assert any("capacity" in line for line in lines)
    assert not any("thread-9" in line or " x " in line for line in lines)
    assert all("$" not in line and "token" not in line.lower() for line in lines)


def test_a_broken_line_is_skipped_and_appending_never_raises(tmp_path: Path) -> None:
    path = tmp_path / "friction.jsonl"
    append_friction(path, _event("repair"))
    path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    assert [e.kind for e in read_friction(path)] == ["repair"]
    append_friction(tmp_path / "no" / "such" / "dir" / "x" / "friction.jsonl", _event("repair"))
    append_friction(Path("/nonexistent-root-\x00/x.jsonl"), _event("repair"))  # never raises
