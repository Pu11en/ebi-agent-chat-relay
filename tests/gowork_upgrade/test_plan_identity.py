"""Stable plan identity and hierarchy tests for the Go Work execution spine."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core.gowork_plan import (
    PlanValidationError,
    load_plan_tree,
    parse_plan_tree,
    render_plan_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_legacy_checkbox_plan_loads_as_one_stable_plan(tmp_path: Path) -> None:
    project = tmp_path / "old-project"
    project.mkdir()
    source = project / "PLAN.md"
    source.write_text((FIXTURES / "legacy-plan.md").read_text(encoding="utf-8"))

    first = load_plan_tree(source)
    source.write_text(source.read_text(encoding="utf-8").replace("- [ ]", "- [x]", 1))
    second = load_plan_tree(source)

    assert first == second
    assert first.is_legacy is True
    assert first.master.parent_id is None
    assert first.master.version == 1
    assert first.master.project_path == project.resolve()
    assert first.master.plan_id.startswith("legacy-")
    assert first.children(first.master.plan_id) == ()


def test_multi_project_nested_tree_round_trips_with_canonical_paths(tmp_path: Path) -> None:
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "multi-project-plan.md").read_text(encoding="utf-8"))

    tree = load_plan_tree(source)

    assert tree.is_legacy is False
    assert tree.master.plan_id == "business"
    assert [plan.plan_id for plan in tree.children("business")] == [
        "product",
        "website",
        "marketing",
    ]
    assert [plan.plan_id for plan in tree.children("product")] == ["product-api"]
    assert tree.get("website").project_path == (tmp_path / "website").resolve()
    assert tree.get("product-api").project_path == (tmp_path / "product").resolve()

    rendered = render_plan_manifest(tree, relative_to=source.parent)
    reparsed = parse_plan_tree(rendered, source_path=source)

    assert reparsed == tree


def test_duplicate_plan_ids_are_rejected(tmp_path: Path) -> None:
    text = """```gowork-plan
{"schema_version": 1, "plans": [
  {"id": "same", "version": 1, "project_path": "."},
  {"id": "same", "version": 2, "parent_id": "same", "project_path": "other"}
]}
```"""

    with pytest.raises(PlanValidationError, match="duplicate plan id 'same'"):
        parse_plan_tree(text, source_path=tmp_path / "PLAN.md")


def test_parent_cycles_are_rejected_with_the_cycle(tmp_path: Path) -> None:
    text = """```gowork-plan
{"schema_version": 1, "plans": [
  {"id": "master", "version": 1, "project_path": "."},
  {"id": "alpha", "version": 1, "parent_id": "beta", "project_path": "a"},
  {"id": "beta", "version": 1, "parent_id": "alpha", "project_path": "b"}
]}
```"""

    with pytest.raises(PlanValidationError, match=r"parent cycle.*alpha.*beta.*alpha"):
        parse_plan_tree(text, source_path=tmp_path / "PLAN.md")


def test_unknown_parent_is_rejected(tmp_path: Path) -> None:
    text = """```gowork-plan
{"schema_version": 1, "plans": [
  {"id": "master", "version": 1, "project_path": "."},
  {"id": "child", "version": 1, "parent_id": "missing", "project_path": "child"}
]}
```"""

    with pytest.raises(PlanValidationError, match="unknown parent 'missing'"):
        parse_plan_tree(text, source_path=tmp_path / "PLAN.md")
