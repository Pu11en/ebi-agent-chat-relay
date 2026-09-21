"""The operator guidance is a contract with the CLI, so it is checked against it.

`skill/references/coordinator.md` names commands and flags an agent will type
verbatim; a flag that no longer exists is an operator stuck mid-build. These
tests read the real `--help` output and refuse any documented option or
subcommand the parser does not know. `SKILL.md` is checked for the topics the
parallel-gowork change requires it to cover, so a later rewrite cannot drop
one silently.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "extensions" / "feature_workflow" / "skill" / "SKILL.md"
GUIDE = ROOT / "extensions" / "feature_workflow" / "skill" / "references" / "coordinator.md"

_FLAG_RE = re.compile(r"`(--[a-z][a-z0-9-]*)")
_SUBCOMMAND_RE = re.compile(r"`([a-z][a-z-]*)(?: --[^`]*)?`")


def _help(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "extensions.feature_workflow.coordinator", *args, "--help"],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout


@pytest.fixture(scope="module")
def cli() -> dict[str, str]:
    top = _help()
    subcommands = re.search(r"\{([a-z,-]+)\}", top)
    assert subcommands, top
    names = subcommands.group(1).split(",")
    stub = ["--repo", ".", "--manifest", "x", "--state-dir", "state"]
    return {"": top, **{name: _help(*stub, name) for name in names}}


class TestCoordinatorGuide:
    def test_every_documented_flag_exists(self, cli: dict[str, str]) -> None:
        known = set()
        for text in cli.values():
            known.update(re.findall(r"(--[a-z][a-z0-9-]*)", text))
        documented = set(_FLAG_RE.findall(GUIDE.read_text(encoding="utf-8")))
        assert documented, "the guide names no flags at all"
        assert documented <= known, sorted(documented - known)

    def test_every_documented_subcommand_exists(self, cli: dict[str, str]) -> None:
        subcommands = set(cli) - {""}
        text = GUIDE.read_text(encoding="utf-8")
        documented = {
            name
            for name in _SUBCOMMAND_RE.findall(text)
            if name in subcommands or name in {"approve", "tick", "watch", "collect", "status"}
        }
        assert {"approve", "tick", "status", "collect", "integrate", "reconcile", "watch"} <= (
            documented
        )
        assert documented <= subcommands, sorted(documented - subcommands)

    def test_guide_covers_the_parallel_gowork_topics(self) -> None:
        text = GUIDE.read_text(encoding="utf-8").lower()
        for topic in (
            "depends on:",
            "owns:",
            "check:",
            "worker cap",
            "queue",
            "integration owner",
            ".worktrees/wt-",
            "session/",
            "correlation",
            "rollback",
            "archive",
        ):
            assert topic in text, topic

    def test_guide_does_not_recommend_a_worker_number(self) -> None:
        text = GUIDE.read_text(encoding="utf-8").lower()
        assert "start with three" not in text
        assert "three outstanding workers" not in text


class TestSkill:
    def test_skill_covers_the_parallel_gowork_topics(self) -> None:
        text = SKILL.read_text(encoding="utf-8").lower()
        for topic in (
            "depends on",
            "owns",
            "make this separate",
            "child planning thread",
            "no fixed",
            "queue",
            "one integration owner",
            "rollback",
            "archive",
        ):
            assert topic in text, topic

    def test_skill_points_at_the_guide(self) -> None:
        assert "references/coordinator.md" in SKILL.read_text(encoding="utf-8")
