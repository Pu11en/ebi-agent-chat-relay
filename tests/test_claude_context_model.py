"""Context limits must describe the primary request, not a helper model."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from claude_code_core.parser import parse_line
from claude_code_core.runner import ClaudeRunner

PRIMARY = "claude-opus-4-8"
HELPER = "claude-haiku-4-5-20251001"
MIXED = {HELPER: {"contextWindow": 200000}, PRIMARY: {"contextWindow": 1000000}}


def result(usage: object) -> dict:
    return {"type": "result", "subtype": "success", "modelUsage": usage}


async def read(runner: ClaudeRunner, records: list[dict]) -> list:
    process = AsyncMock()
    process.stdout.readline = AsyncMock(
        side_effect=[json.dumps(record).encode() + b"\n" for record in records] + [b""]
    )
    runner._process = process
    return [event async for event in runner._read_stream()]


@pytest.mark.parametrize("reverse", [False, True])
async def test_primary_assistant_selects_its_own_limit(reverse: bool) -> None:
    usage = dict(reversed(list(MIXED.items()))) if reverse else MIXED
    events = await read(
        ClaudeRunner(),
        [
            {"type": "system", "subtype": "init", "model": "opus"},
            {"type": "assistant", "message": {"model": PRIMARY, "content": []}},
            {
                "type": "assistant",
                "parent_tool_use_id": "child-tool",
                "message": {"model": HELPER, "content": []},
            },
            {"type": "assistant", "message": {"model": "<synthetic>", "content": []}},
            result(usage),
        ],
    )
    assert events[-1].context_window == 1000000
    assert all(event.raw == {} for event in events)


async def test_missing_primary_does_not_borrow_helper_limit() -> None:
    events = await read(
        ClaudeRunner(),
        [
            {"type": "system", "subtype": "init", "model": PRIMARY},
            result({HELPER: {"contextWindow": 200000}}),
        ],
    )
    assert events[-1].context_window is None
    assert events[-1].context_source == "unknown"


async def test_model_identity_does_not_leak_between_streams() -> None:
    runner = ClaudeRunner()
    first = await read(
        runner, [{"type": "system", "subtype": "init", "model": PRIMARY}, result(MIXED)]
    )
    second = await read(runner, [result(MIXED)])
    assert first[-1].context_window == 1000000
    assert second[-1].context_window is None
    assert second[-1].context_source == "unknown"


@pytest.mark.parametrize(
    "usage",
    [
        MIXED,
        None,
        [],
        {},
        {PRIMARY: {"contextWindow": True}},
        {PRIMARY: {"contextWindow": -1}},
        {PRIMARY: {"contextWindow": 0}},
        {PRIMARY: {"contextWindow": "1000000"}},
        {PRIMARY: {"contextWindow": 1000000}, HELPER: {}},
    ],
)
def test_unattributable_or_invalid_window_is_explicitly_unknown(usage: object) -> None:
    event = parse_line(json.dumps(result(usage)))
    assert event is not None
    assert event.context_window is None
    assert event.context_source == "unknown"


@pytest.mark.parametrize(
    "usage",
    [
        {PRIMARY: {"contextWindow": 1000000}},
        {PRIMARY: {"contextWindow": 1000000}, HELPER: {"contextWindow": 1000000}},
    ],
)
def test_unambiguous_legacy_window_still_works(usage: dict) -> None:
    event = parse_line(json.dumps(result(usage)))
    assert event is not None
    assert event.context_window == 1000000
