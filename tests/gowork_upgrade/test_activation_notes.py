"""T32 — the activation notes map every approved requirement to tests and never call
not-yet-live behaviour complete.

`docs/gowork-upgrade-activation.md` is the deployment/rollback record for the Go Work
upgrade. This test reads it the way an operator would: every ticked task in the plan has
a row naming tests that exist, every row's status is one the notes define, the saved
verification commands are real, and the live boundary is stated, not implied.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLAN = REPO / "docs" / "plans" / "gowork-upgrade-execution-plan.md"
NOTES = REPO / "docs" / "gowork-upgrade-activation.md"

ALLOWED_STATUS = {"implemented, checked offline", "implemented, checked offline; not yet live"}
REQUIRED_SECTIONS = (
    "## What is implemented, what is checked, what is not yet live",
    "## Saved verification commands and results",
    "## Requirement → test map",
    "## Backup and migration of the Go Work state",
    "## Shared-host coordination",
    "## Instruction install",
    "## Live activation",
    "## Rollback",
)


def _ticked_tasks() -> set[str]:
    text = PLAN.read_text(encoding="utf-8")
    return set(re.findall(r"^- \[x\] (T\d\d[a-c]?):", text, flags=re.M))


def _rows() -> dict[str, tuple[list[str], str]]:
    text = NOTES.read_text(encoding="utf-8")
    section = text.split("## Requirement → test map", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, tuple[list[str], str]] = {}
    for line in section.splitlines():
        match = re.match(r"^\| (T\d\d[a-c]?) \| .+? \| (.+?) \| (.+?) \|$", line)
        if match:
            tests = re.findall(r"`([^`]+)`", match.group(2))
            rows[match.group(1)] = (tests, match.group(3).strip())
    return rows


def test_every_ticked_requirement_maps_to_tests_that_exist() -> None:
    ticked = _ticked_tasks()
    rows = _rows()
    assert ticked, "the plan has ticked tasks"
    assert ticked <= set(rows), f"rows missing for {sorted(ticked - set(rows))}"
    for task_id, (tests, status) in rows.items():
        assert tests, f"{task_id} names no test"
        for reference in tests:
            path, _, name = reference.partition("::")
            file = REPO / path
            assert file.is_file(), f"{task_id}: {path} does not exist"
            if name:
                assert re.search(
                    rf"^(async )?def {re.escape(name)}\(", file.read_text("utf-8"), re.M
                ), f"{task_id}: {reference} is not a test in that file"
        assert status in ALLOWED_STATUS, f"{task_id}: status {status!r} is not defined"
    # Nothing in the map claims to be live: the only "live" wording allowed is "not yet live".
    assert all("live" not in s.replace("not yet live", "") for _t, s in rows.values())


def test_the_notes_state_the_live_boundary_and_the_saved_results() -> None:
    text = NOTES.read_text(encoding="utf-8")
    for heading in REQUIRED_SECTIONS:
        assert heading in text, heading
    assert "not deployed" in text.lower()
    assert "no bot was restarted" in text.lower()
    # The saved commands are the real ones.
    for command in (
        "uv run python scripts/check_gowork_upgrade.py",
        "bash scripts/test-clean-env.sh",
        "uv run python -m claude_discord.gowork_demo",
        "uv run python -m claude_code_core.gowork_contracts "
        "tests/gowork_upgrade/fixtures/contracts",
        "uv run ruff check claude_discord/ claude_code_core/ extensions/ scripts/",
        "uv run pyright claude_discord/ claude_code_core/",
        "python -m claude_code_core.gowork_guidance plan --home",
        "python -m claude_code_core.gowork_guidance stage --home",
        "python -m claude_code_core.gowork_guidance rollback --home",
    ):
        assert command in text, command
    # Results are recorded as numbers, not adjectives.
    assert re.search(r"check_gowork_upgrade\.py.*?(\d+) passed", text, re.S)
    assert re.search(r"test-clean-env\.sh.*?(\d+) passed", text, re.S)
    # The state files an operator must back up are all named.
    for name in (
        "gowork-loops.json",
        "gowork-admission.json",
        "gowork-blockers.json",
        "gowork-friction.jsonl",
        "builds/",
        "CCDB_GOWORK_STATE",
        "CCDB_GOWORK_ROOT",
        "MAX_CONCURRENT_SESSIONS",
    ):
        assert name in text, name


def test_nothing_pending_is_called_complete() -> None:
    text = NOTES.read_text(encoding="utf-8")
    pending = text.split("### Not yet live", 1)[1].split("\n## ", 1)[0]
    assert pending.count("\n- ") >= 4  # the boundary is a list, not a sentence
    for word in ("deployed", "activated", "installed on the live"):
        assert not re.search(rf"\b(is|was|has been) {word}\b", pending), word
