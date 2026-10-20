"""Read real Codex usage envelopes without confusing totals with context."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from claude_code_core.codex_context import read_codex_context

SESSION = "01a156dd-4284-7751-a6ce-ee1500a50ab0"
START = 1792458702.075
USAGE = {"input_tokens": 20240, "cached_input_tokens": 12288, "output_tokens": 9}


def _event(kind: str, **payload: object) -> dict:
    return {"type": "event_msg", "payload": {"type": kind, **payload}}


def _records() -> list[dict]:
    # Sanitized metadata captured from codex-cli 0.160.1, 2026-10-19.
    # Its stdout had thread.started, turn.started, item.completed, turn.completed;
    # only the native rollout contained this token_count envelope.
    return [
        {"type": "session_meta", "payload": {"id": SESSION}},
        _event("task_started", turn_id="turn-1", started_at=1792458705),
        _event(
            "token_count",
            info={
                "total_token_usage": USAGE,
                "last_token_usage": USAGE,
                "model_context_window": 258400,
            },
        ),
        _event("task_complete", turn_id="turn-1", started_at=1792458705),
    ]


def _store(home: Path, records: list[dict]) -> Path:
    path = home / "sessions" / "2026" / "10" / "19" / f"rollout-{SESSION}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
        db.execute("INSERT INTO threads VALUES (?, ?)", (SESSION, str(path)))
    return path


def test_real_rollout_envelope(tmp_path: Path) -> None:
    _store(tmp_path, _records())
    measurement = read_codex_context(tmp_path, SESSION, START, USAGE)
    assert measurement is not None
    assert measurement.used == 20240  # Cached input is already included.
    assert measurement.window == 258400


def test_resumed_turn_uses_last_request_not_cumulative_usage(tmp_path: Path) -> None:
    records = _records()
    records[-2]["payload"]["info"]["last_token_usage"] = {
        "input_tokens": 6000,
        "cached_input_tokens": 4000,
        "output_tokens": 3,
    }
    _store(tmp_path, records)
    measurement = read_codex_context(tmp_path, SESSION, START, USAGE)
    assert measurement is not None
    assert measurement.used == 6000


@pytest.mark.parametrize("failure", ["old_turn", "wrong_id", "different_totals", "no_token"])
def test_stale_or_mismatched_context_is_unknown(tmp_path: Path, failure: str) -> None:
    records = _records()
    if failure == "old_turn":
        records[-1]["payload"]["started_at"] = START - 60
    elif failure == "wrong_id":
        records[0]["payload"]["id"] = "another-session"
    elif failure == "different_totals":
        records[-2]["payload"]["info"]["total_token_usage"] = {**USAGE, "input_tokens": 1}
    else:
        # An old token exists, but not inside the current turn.
        records.insert(1, records.pop(2))
    _store(tmp_path, records)
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None


@pytest.mark.parametrize("value", [None, {}, True, -1, "200000"])
def test_invalid_window_is_unknown(tmp_path: Path, value: object) -> None:
    records = _records()
    records[-2]["payload"]["info"]["model_context_window"] = value
    _store(tmp_path, records)
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None


def test_compaction_without_fresh_usage_invalidates_old_measurement(tmp_path: Path) -> None:
    records = _records()
    records.insert(-1, {"type": "compacted", "payload": {}})
    path = _store(tmp_path, records)
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None
    fresh = _event(
        "token_count",
        info={
            "total_token_usage": USAGE,
            "last_token_usage": {**USAGE, "input_tokens": 15000},
            "model_context_window": 475000,
        },
    )
    records.insert(-1, fresh)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    measurement = read_codex_context(tmp_path, SESSION, START, USAGE)
    assert measurement is not None
    assert (measurement.used, measurement.window) == (15000, 475000)


def test_bounded_tail_does_not_load_large_tool_output(tmp_path: Path) -> None:
    path = _store(tmp_path, _records())
    records = _records()
    with path.open("w") as f:
        f.write(json.dumps(records[0]) + "\n")
        f.write(json.dumps({"type": "response_item", "payload": "x" * 2_000_000}) + "\n")
        f.writelines(json.dumps(r) + "\n" for r in records[1:])
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is not None


def test_missing_or_malformed_native_store_does_not_break_completion(tmp_path: Path) -> None:
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None
    (tmp_path / "state_5.sqlite").write_text("not sqlite")
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None


def test_index_cannot_point_outside_native_sessions(tmp_path: Path) -> None:
    path = _store(tmp_path, _records())
    outside = tmp_path / "private.jsonl"
    path.rename(outside)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET rollout_path = ?", (str(outside),))
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None


def test_in_progress_new_turn_does_not_reuse_previous_completion(tmp_path: Path) -> None:
    records = _records() + [_event("task_started", turn_id="turn-2", started_at=START + 50)]
    _store(tmp_path, records)
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None


def test_unfinished_new_usage_cannot_reuse_previous_completion(tmp_path: Path) -> None:
    records = _records()
    records.append(records[2])
    _store(tmp_path, records)
    assert read_codex_context(tmp_path, SESSION, START, USAGE) is None
