"""E2 — what the loop store and the build ledger read back is data, not orders.

``gowork-loops.json`` names the copy to remove (``git worktree remove --force``),
the branch to delete (``git branch -D``) and the ledger file to open
(``builds/<build_id>.json``). A tampered record used to reach all three
verbatim. Records are now validated on load: the build id has no separators,
the branch has the ``gowork/<slug>-<stamp>`` shape the code creates, and the
copy lies under the work-copy area. Invalid records are skipped with a warning
and never used. The ledger's remembered project copies get the same treatment,
and a ledger whose ``tasks`` is the wrong shape is refused cleanly instead of
crashing the build with a ``TypeError``.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_state import StaleAttemptError, open_build_state
from claude_code_core.loop_store import LoopRecord, LoopStore
from claude_code_core.work_copy import WorkCopy, is_gowork_branch, is_under_work_root
from claude_discord.cogs.task_loop import TaskLoopCog

FIXTURES = Path(__file__).parent / "fixtures"
GOOD_BRANCH = "gowork/plan-20260921-120000"


def _record(tmp_path: Path, **overrides: object) -> dict:
    copies = tmp_path / "copies"
    fields: dict = {
        "repo_dir": str(tmp_path / "proj"),
        "plan_path": str(tmp_path / "proj" / "PLAN.md"),
        "copy_path": str(copies / "proj-plan-20260921-120000"),
        "copy_plan": str(copies / "proj-plan-20260921-120000" / "PLAN.md"),
        "branch": GOOD_BRANCH,
        "worker_thread_id": 11,
        "report_channel_id": 22,
        "build_id": "thread-11",
    }
    fields.update(overrides)
    return fields


def _store(tmp_path: Path, *items: dict) -> LoopStore:
    path = tmp_path / "loops.json"
    path.write_text(json.dumps(list(items)), encoding="utf-8")
    return LoopStore(path, work_root=tmp_path / "copies")


def test_branch_and_copy_shapes_match_what_the_code_creates(tmp_path: Path) -> None:
    assert is_gowork_branch(GOOD_BRANCH)
    assert is_gowork_branch("gowork/my-plan-20260921-120000")
    for bad in ("main", "gowork/../main", "gowork/plan", "gowork/plan-2026-01-01", "", "x"):
        assert not is_gowork_branch(bad), bad
    root = tmp_path / "copies"
    assert is_under_work_root(root / "proj-plan-20260921-120000", root)
    assert not is_under_work_root(root, root)  # the area itself is not a copy
    assert not is_under_work_root(tmp_path / "proj", root)
    assert not is_under_work_root(root / ".." / "proj", root)


def test_a_good_record_loads_and_the_store_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path, _record(tmp_path))
    records = store.all()
    assert [r.build_id for r in records] == ["thread-11"]
    store.save(LoopRecord(**_record(tmp_path, worker_thread_id=12, build_id="thread-12")))
    assert [r.build_id for r in store.all()] == ["thread-11", "thread-12"]


@pytest.mark.parametrize(
    ("field", "value", "why"),
    [
        ("build_id", "../../etc", "separators"),
        ("build_id", "thread 11", "a space"),
        ("branch", "main", "the current branch"),
        ("branch", "gowork/../../main", "traversal"),
        ("branch", "refs/heads/main", "not a gowork branch"),
    ],
)
def test_records_with_a_bad_id_or_branch_are_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, field: str, value: str, why: str
) -> None:
    store = _store(tmp_path, _record(tmp_path, **{field: value}), _record(tmp_path, build_id="ok"))

    with caplog.at_level(logging.WARNING, logger="claude_code_core.loop_store"):
        records = store.all()

    assert [r.build_id for r in records] == ["ok"], why
    assert any(field in rec.getMessage() for rec in caplog.records), why


def test_a_copy_outside_the_work_area_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    outside = tmp_path / "proj"  # the person's real project: never a copy to remove
    tampered = _record(tmp_path, copy_path=str(outside), copy_plan=str(outside / "PLAN.md"))
    store = _store(tmp_path, tampered)

    with caplog.at_level(logging.WARNING, logger="claude_code_core.loop_store"):
        assert store.all() == []
    assert any("copy_path" in rec.getMessage() for rec in caplog.records)

    # The plan inside the copy must be inside that copy, too.
    stray = _record(tmp_path, copy_plan=str(tmp_path / "proj" / "PLAN.md"))
    assert _store(tmp_path, stray).all() == []


def test_a_store_without_a_work_area_still_checks_shapes(tmp_path: Path) -> None:
    path = tmp_path / "loops.json"
    path.write_text(
        json.dumps([_record(tmp_path), _record(tmp_path, branch="main", build_id="bad")]),
        encoding="utf-8",
    )
    assert [r.build_id for r in LoopStore(path).all()] == ["thread-11"]


def test_the_cog_gives_its_store_the_work_area(tmp_path: Path) -> None:
    bot = MagicMock()
    bot.cogs = {}
    store = LoopStore(tmp_path / "loops.json")
    TaskLoopCog(bot, work_root=tmp_path / "copies", store=store)
    assert store.work_root == tmp_path / "copies"


def test_a_ledger_with_the_wrong_task_shape_is_refused_not_crashed(tmp_path: Path) -> None:
    plan = tmp_path / "PLAN.md"
    plan.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    tree = load_plan_tree(plan)
    ledger = tmp_path / "b.json"
    good = open_build_state(ledger, tree, build_id="b")
    document = json.loads(ledger.read_text(encoding="utf-8"))
    assert good.build_id == "b"

    for key, wrong in (("tasks", []), ("plan_versions", []), ("events", {})):
        broken = {**document, key: wrong}
        ledger.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(StaleAttemptError, match=key):
            open_build_state(ledger, tree, build_id="b")


def test_remembered_project_copies_are_validated_before_use(tmp_path: Path) -> None:
    plan = tmp_path / "PLAN.md"
    plan.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    tree = load_plan_tree(plan)
    ledger = tmp_path / "b.json"
    state = open_build_state(ledger, tree, build_id="b")
    state.note_project_copy(
        str(tmp_path / "other"), path=str(tmp_path / "copies" / "x"), branch=GOOD_BRANCH
    )
    document = json.loads(ledger.read_text(encoding="utf-8"))
    document["project_copies"][str(tmp_path / "victim")] = {
        "path": str(tmp_path / "victim"),
        "branch": "main",
    }
    document["project_copies"]["junk"] = "not a mapping"
    document["project_copies"]["nopath"] = {"branch": GOOD_BRANCH}
    ledger.write_text(json.dumps(document), encoding="utf-8")

    copies = open_build_state(ledger, tree, build_id="b").project_copies()

    assert list(copies) == [str(tmp_path / "other")]


def test_the_cog_refuses_a_remembered_copy_outside_its_work_area(tmp_path: Path) -> None:
    bot = MagicMock()
    bot.cogs = {}
    tmp = Path(tempfile.mkdtemp(prefix="gowork-trust-"))
    cog = TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))
    victim = tmp_path / "victim"
    victim.mkdir()
    inside = tmp / "copies" / "other-x-20260921-120000"
    inside.mkdir(parents=True)

    assert cog._trusted_copy(victim, {"path": str(victim), "branch": GOOD_BRANCH}) is None
    assert cog._trusted_copy(victim, {"path": str(inside), "branch": "main"}) is None
    trusted = cog._trusted_copy(victim, {"path": str(inside), "branch": GOOD_BRANCH})
    assert trusted == WorkCopy(
        source_repo=victim, path=inside, branch=GOOD_BRANCH, plan_path=inside
    )
