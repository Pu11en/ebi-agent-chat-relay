"""Dependency, ownership, path-safety and outcome-coverage validation tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from claude_code_core.gowork_plan import (
    OwnershipConflict,
    PlanValidationError,
    load_plan_tree,
    parse_plan_tree,
    render_plan_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures"
_FENCE_OPEN = "```gowork-plan\n"


def _valid_manifest() -> dict[str, Any]:
    text = (FIXTURES / "validated-plan.md").read_text(encoding="utf-8")
    body = text.split(_FENCE_OPEN, 1)[1].split("\n```", 1)[0]
    return json.loads(body)


def _task(manifest: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(task for task in manifest["tasks"] if task["id"] == task_id)


def _parse(manifest: dict[str, Any], tmp_path: Path):  # noqa: ANN202
    text = f"{_FENCE_OPEN}{json.dumps(manifest)}\n```"
    return parse_plan_tree(text, source_path=tmp_path / "master-plan.md")


def test_valid_independent_work_passes_and_round_trips(tmp_path: Path) -> None:
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))

    tree = load_plan_tree(source)

    assert [item.requirement_id for item in tree.requirements] == [
        "REQ-CATALOG",
        "REQ-WEBSITE-CATALOG",
        "REQ-LAUNCH-POST",
    ]
    assert tree.can_run_together("product.catalog-api", "marketing.launch-post")
    assert tree.can_run_together("website.catalog-page", "marketing.launch-post")
    assert parse_plan_tree(
        render_plan_manifest(tree, relative_to=tmp_path), source_path=source
    ) == (tree)


def test_overlapping_ownership_blocks_only_the_overlapping_pair(tmp_path: Path) -> None:
    tree = _parse(_valid_manifest(), tmp_path)

    assert tree.ownership_conflicts() == (
        OwnershipConflict(
            first_task_id="website.catalog-page",
            second_task_id="website.page-styles",
            files=("src/pages/catalog.tsx",),
            resources=(),
        ),
    )
    assert not tree.can_run_together("website.catalog-page", "website.page-styles")
    assert not tree.can_run_together("website.page-styles", "website.catalog-page")
    assert tree.can_run_together("website.page-styles", "marketing.launch-post")
    with pytest.raises(KeyError):
        tree.can_run_together("website.page-styles", "missing.task")


def test_same_relative_file_in_different_projects_does_not_conflict(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _task(manifest, "marketing.launch-post")["owned_files"] = ["src/catalog.py"]

    tree = _parse(manifest, tmp_path)

    assert tree.can_run_together("product.catalog-api", "marketing.launch-post")


def test_plans_sharing_one_project_conflict_on_the_same_file(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    manifest["plans"][3]["project_path"] = "product"
    _task(manifest, "marketing.launch-post")["owned_files"] = ["./src//catalog.py"]

    tree = _parse(manifest, tmp_path)

    assert not tree.can_run_together("product.catalog-api", "marketing.launch-post")


def test_shared_resources_and_patterns_conflict_across_projects(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    _task(manifest, "marketing.launch-post")["owned_resources"] = ["catalog-schema"]
    _task(manifest, "website.page-styles")["owned_files"] = ["src/pages/*.tsx"]

    tree = _parse(manifest, tmp_path)

    assert not tree.can_run_together("product.catalog-api", "marketing.launch-post")
    assert not tree.can_run_together("website.catalog-page", "website.page-styles")


def _missing_dependency(manifest: dict[str, Any]) -> None:
    _task(manifest, "website.catalog-page")["dependencies"] = ["product.missing"]


def _self_dependency(manifest: dict[str, Any]) -> None:
    _task(manifest, "marketing.launch-post")["dependencies"] = ["marketing.launch-post"]


def _dependency_cycle(manifest: dict[str, Any]) -> None:
    product = _task(manifest, "product.catalog-api")
    product["dependencies"] = ["website.catalog-page"]
    product["required_inputs"].append("website.catalog-page: page feedback")


def _repeated_dependency(manifest: dict[str, Any]) -> None:
    _task(manifest, "website.catalog-page")["dependencies"] *= 2


def _owned_file(value: str) -> Callable[[dict[str, Any]], None]:
    def mutate(manifest: dict[str, Any]) -> None:
        _task(manifest, "marketing.launch-post")["owned_files"] = [value]

    return mutate


def _cross_project_without_input(manifest: dict[str, Any]) -> None:
    _task(manifest, "website.catalog-page")["required_inputs"] = ["REQ-WEBSITE-CATALOG"]


def _uncovered_outcome(manifest: dict[str, Any]) -> None:
    manifest["requirements"].append({"id": "REQ-PRICING", "outcome": "Pricing is published"})


def _unknown_source_requirement(manifest: dict[str, Any]) -> None:
    _task(manifest, "marketing.launch-post")["source_requirement"] = "REQ-INVENTED"


def _no_requirements(manifest: dict[str, Any]) -> None:
    del manifest["requirements"]


def _duplicate_requirement(manifest: dict[str, Any]) -> None:
    manifest["requirements"].append(dict(manifest["requirements"][0]))


def _requirement_without_outcome(manifest: dict[str, Any]) -> None:
    manifest["requirements"][0]["outcome"] = " "


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            _missing_dependency,
            r"task 'website.catalog-page' depends on unknown task 'product.missing'",
        ),
        (_self_dependency, r"task 'marketing.launch-post' depends on itself"),
        (
            _dependency_cycle,
            r"dependency cycle detected: product.catalog-api -> website.catalog-page "
            r"-> product.catalog-api",
        ),
        (
            _repeated_dependency,
            r"task 'website.catalog-page' lists dependency 'product.catalog-api' more than once",
        ),
        (
            _owned_file("/etc/passwd"),
            r"task 'marketing.launch-post' owned file '/etc/passwd' must be relative",
        ),
        (
            _owned_file("C:/Windows/system.ini"),
            r"owned file 'C:/Windows/system.ini' must be relative",
        ),
        (
            _owned_file("../product/src/catalog.py"),
            r"owned file '../product/src/catalog.py' must stay inside its project "
            r"\(no '\.\.' parts\)",
        ),
        (_owned_file("~/notes.md"), r"owned file '~/notes.md' must not start with '~'"),
        (_owned_file("posts\\launch.md"), r"must use '/' separators"),
        (_owned_file(".git/config"), r"owned file '.git/config' must not be inside '.git'"),
        (_owned_file("posts/\nlaunch.md"), r"must not contain control characters"),
        (
            _cross_project_without_input,
            r"task 'website.catalog-page' depends on 'product.catalog-api' in another project "
            r"but no required input names it",
        ),
        (_uncovered_outcome, r"agreed outcome 'REQ-PRICING' is not covered by any task"),
        (
            _unknown_source_requirement,
            r"task 'marketing.launch-post' source_requirement 'REQ-INVENTED' is not an "
            r"agreed outcome",
        ),
        (_no_requirements, r"must list the agreed outcomes under 'requirements'"),
        (_duplicate_requirement, r"duplicate requirement id 'REQ-CATALOG'"),
        (
            _requirement_without_outcome,
            r"requirement 'REQ-CATALOG' field 'outcome' must be a non-empty string",
        ),
    ],
)
def test_each_invalid_plan_explains_the_specific_issue(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    manifest = _valid_manifest()
    mutate(manifest)

    with pytest.raises(PlanValidationError, match=message):
        _parse(manifest, tmp_path)


def test_same_project_dependency_needs_no_cross_project_input(tmp_path: Path) -> None:
    manifest = _valid_manifest()
    styles = _task(manifest, "website.page-styles")
    styles["dependencies"] = ["website.catalog-page"]

    tree = _parse(manifest, tmp_path)

    assert tree.task("website.page-styles").dependencies == ("website.catalog-page",)


def test_legacy_plan_stays_valid_without_declared_outcomes(tmp_path: Path) -> None:
    source = tmp_path / "PLAN.md"
    source.write_text((FIXTURES / "legacy-plan.md").read_text(encoding="utf-8"))

    tree = load_plan_tree(source)

    assert tree.requirements == ()
    one, two = tree.tasks
    assert not tree.can_run_together(one.task_id, two.task_id)
