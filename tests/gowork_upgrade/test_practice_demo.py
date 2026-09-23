"""T31 — the offline Go Work practice command.

``python -m claude_discord.gowork_demo`` drives the real cog, loop, ledger,
admission controller and capacity policy with fake workers, fake host
readings and a fake Discord, in a temporary git repository. It exits 0 only
when every behaviour it names was observed, and 1 with the first failure
named otherwise.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from claude_code_core import gowork_schedule
from claude_discord import gowork_demo
from claude_discord.cogs import _run_helper as helper

REPO = Path(__file__).resolve().parents[2]
PLAN = REPO / "docs" / "plans" / "gowork-upgrade-execution-plan.md"


@pytest.fixture
def clean_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "_global_semaphore", None)
    yield  # type: ignore[misc]
    helper.configure_adaptive_limit(controller=None, policy=None, probe=None)


async def test_the_demo_observes_every_behaviour_and_exits_zero(
    tmp_path: Path, clean_limits: None
) -> None:
    lines: list[str] = []
    code = await gowork_demo.run_demo(tmp_path / "demo", say=lines.append)
    output = "\n".join(lines)
    assert code == 0, output
    for behaviour in gowork_demo.BEHAVIOURS:
        assert f"✓ {behaviour}" in output, output
    assert "Pending limitations" in output
    assert "not the live bot" in output.lower() or "no bot process" in output.lower()
    assert len(lines) < 60, "the output must stay short"  # what/why per behaviour, not a log
    assert "✗" not in output


async def test_a_regression_makes_the_demo_fail_and_name_it(
    tmp_path: Path, clean_limits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A scheduler that releases dependents before their prerequisite is accepted.
    monkeypatch.setattr(gowork_schedule, "_releases", lambda _state, _dep: True)
    lines: list[str] = []
    code = await gowork_demo.run_demo(tmp_path / "demo", say=lines.append)
    output = "\n".join(lines)
    assert code == 1
    assert "✗ dependency wait" in output
    # A failed run leaves nothing behind: every build driver is cancelled before the
    # demo returns, so the loop that ran it can close (CI on Linux 3.12 hung here).
    leftovers = [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done() and "_drive" in repr(t)
    ]
    assert leftovers == []


def test_the_try_line_names_the_demo_command() -> None:
    text = PLAN.read_text(encoding="utf-8")
    try_line = next(line for line in text.splitlines() if line.startswith("Try:"))
    assert try_line == f"Try: {gowork_demo.COMMAND}"
    assert gowork_demo.COMMAND == "uv run python -m claude_discord.gowork_demo"
    assert re.search(r"Once T31 exists, run `uv run python -m claude_discord.gowork_demo`", text)
