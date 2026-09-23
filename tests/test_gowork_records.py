"""Tests for claude_code_core.gowork_records — the per-step records gowork learns from."""

from __future__ import annotations

from pathlib import Path

from claude_code_core import gowork_records as gr


def _rec(ai: str, result: str, seconds: float = 60, step: str = "s") -> dict:
    return {"ai": ai, "result": result, "seconds": seconds, "step": step, "repo": "r"}


def test_records_are_appended_and_read_back(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    gr.append_record(path, _rec("claude · opus", "done"))
    gr.append_record(path, _rec("codex · gpt-5.5", "stuck"))
    assert [r["result"] for r in gr.read_records(path)] == ["done", "stuck"]


def test_missing_or_broken_file_reads_as_empty(tmp_path: Path) -> None:
    assert gr.read_records(tmp_path / "none.jsonl") == []
    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n" + '{"ai": "x", "result": "done", "seconds": 1}\n')
    assert len(gr.read_records(bad)) == 1


def test_track_record_summarises_each_ai() -> None:
    records = [
        _rec("claude · opus", "done", 240),
        _rec("claude · opus", "done", 120),
        _rec("claude · opus", "retry", 60),
        _rec("codex · gpt-5.5", "stuck", 600),
    ]
    lines = gr.track_record(records)
    opus = next(line for line in lines if line.startswith("claude · opus"))
    assert "3 steps" in opus and "2 done" in opus and "1 retry" in opus
    assert any("codex · gpt-5.5" in line and "1 stuck" in line for line in lines)


def test_lessons_prompt_carries_the_builds_records_and_recaps() -> None:
    p = gr.lessons_prompt([_rec("claude · opus", "stuck", step="T2 login")], ["T1: made the page"])
    assert "T2 login" in p and "stuck" in p and "T1: made the page" in p
