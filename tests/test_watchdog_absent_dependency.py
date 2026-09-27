"""A cog whose dependency is missing says so once and stops.

The Todoist watchdog logged the same error 467 times on 2026-09-26 and 17 more
after a restart, because `~/.claude/skills/todoist/scripts/todoist.sh` does not
exist on this machine and the 30-minute loop kept calling it anyway. Nobody saw
any of it: a log line is not a report, and a failure repeating on a timer is the
most ignorable kind there is.

The cog is the reference implementation for custom cogs, so this matters beyond
one machine — every consumer who copies it and has no Todoist gets the same noise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_COG = Path(__file__).resolve().parent.parent / "examples/ebibot/cogs/watchdog.py"


def _load(monkeypatch, script: Path | str):
    """Import the cog fresh with TODOIST_SH pointing at *script*."""
    monkeypatch.setenv("TODOIST_SH", str(script))
    sys.modules.pop("_watchdog_under_test", None)
    spec = importlib.util.spec_from_file_location("_watchdog_under_test", _COG)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_watchdog_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.add_cog = AsyncMock()
    return b


class TestAMissingScriptStopsTheLoop:
    async def test_the_loop_never_starts_without_its_script(self, monkeypatch, tmp_path) -> None:
        module = _load(monkeypatch, tmp_path / "nope.sh")
        cog = module.WatchdogCog(MagicMock())
        cog.check_overdue = MagicMock()

        await cog.cog_load()

        cog.check_overdue.start.assert_not_called()

    async def test_it_says_so_once_rather_than_every_thirty_minutes(
        self, monkeypatch, tmp_path, caplog
    ) -> None:
        module = _load(monkeypatch, tmp_path / "nope.sh")
        cog = module.WatchdogCog(MagicMock())
        cog.check_overdue = MagicMock()

        with caplog.at_level("WARNING"):
            await cog.cog_load()

        said = [r for r in caplog.records if "odoist" in r.message or "odoist" in str(r.args)]
        assert len(said) == 1, "one line, then silence"

    async def test_the_loop_starts_when_the_script_is_there(self, monkeypatch, tmp_path) -> None:
        script = tmp_path / "todoist.sh"
        script.write_text("#!/bin/sh\necho '[]'\n")
        script.chmod(0o755)
        module = _load(monkeypatch, script)
        cog = module.WatchdogCog(MagicMock())
        cog.check_overdue = MagicMock()

        await cog.cog_load()

        cog.check_overdue.start.assert_called_once()

    async def test_setup_adds_nothing_when_the_script_is_absent(
        self, monkeypatch, tmp_path, bot
    ) -> None:
        """No cog at all is tidier than a cog that can only fail."""
        module = _load(monkeypatch, tmp_path / "nope.sh")

        await module.setup(bot, MagicMock(), MagicMock())

        bot.add_cog.assert_not_awaited()
