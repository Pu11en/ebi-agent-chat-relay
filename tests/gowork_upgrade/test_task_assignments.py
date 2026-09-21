"""Complete Go Work task-assignment contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.gowork_plan import (
    PlanValidationError,
    load_plan_tree,
    parse_plan_tree,
    render_plan_manifest,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_complete_task_assignments_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "complete-task-plan.md").read_text(encoding="utf-8"))

    tree = load_plan_tree(source)

    assert [task.task_id for task in tree.tasks] == [
        "product.catalog-api",
        "website.catalog-page",
    ]
    product = tree.task("product.catalog-api")
    assert product.plan_id == "product"
    assert product.plan_version == 2
    assert product.outcome == "Publish the checked product catalog contract"
    assert product.dependencies == ()
    assert product.owned_files == ("src/catalog.py", "tests/test_catalog.py")
    assert product.owned_resources == ("catalog-schema",)
    assert product.required_inputs == ("REQ-CATALOG", "decision: product names are final")
    assert product.output == "A versioned catalog contract and passing contract tests"
    assert product.acceptance_check == "uv run pytest tests/test_catalog.py -q"
    assert product.source_requirement == "REQ-CATALOG"
    assert tree.tasks_for("website") == (tree.task("website.catalog-page"),)

    rendered = render_plan_manifest(tree, relative_to=source.parent)
    reparsed = parse_plan_tree(rendered, source_path=source)

    assert reparsed == tree


def _manifest_with_task(task: dict[str, object]) -> str:
    return (
        "```gowork-plan\n"
        + json.dumps(
            {
                "schema_version": 1,
                "plans": [{"id": "main", "version": 1, "project_path": "."}],
                "tasks": [task],
            }
        )
        + "\n```"
    )


def _complete_task() -> dict[str, object]:
    return {
        "id": "main.build",
        "plan_id": "main",
        "plan_version": 1,
        "outcome": "Build the agreed result",
        "dependencies": [],
        "owned_files": ["src/result.py"],
        "owned_resources": [],
        "required_inputs": ["REQ-1"],
        "output": "The agreed result",
        "acceptance_check": "pytest tests/test_result.py -q",
        "source_requirement": "REQ-1",
    }


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "plan_id",
        "plan_version",
        "outcome",
        "dependencies",
        "owned_files",
        "owned_resources",
        "required_inputs",
        "output",
        "acceptance_check",
        "source_requirement",
    ],
)
def test_incomplete_task_reports_the_missing_field(tmp_path: Path, field: str) -> None:
    task = _complete_task()
    del task[field]

    with pytest.raises(PlanValidationError, match=rf"task .* missing required field '{field}'"):
        parse_plan_tree(_manifest_with_task(task), source_path=tmp_path / "PLAN.md")


def test_duplicate_task_ids_are_rejected(tmp_path: Path) -> None:
    task = _complete_task()
    raw = json.loads(_manifest_with_task(task).split("\n", 1)[1].rsplit("\n", 1)[0])
    raw["tasks"].append({**task, "plan_id": "main"})
    text = f"```gowork-plan\n{json.dumps(raw)}\n```"

    with pytest.raises(PlanValidationError, match="duplicate task id 'main.build'"):
        parse_plan_tree(text, source_path=tmp_path / "PLAN.md")


def test_task_plan_version_must_match_its_plan(tmp_path: Path) -> None:
    task = _complete_task()
    task["plan_version"] = 2

    with pytest.raises(
        PlanValidationError,
        match=r"task 'main.build' plan_version 2 does not match plan 'main' version 1",
    ):
        parse_plan_tree(_manifest_with_task(task), source_path=tmp_path / "PLAN.md")


def test_legacy_checkboxes_become_one_conservative_sequence(tmp_path: Path) -> None:
    project = tmp_path / "old-project"
    project.mkdir()
    source = project / "PLAN.md"
    source.write_text((FIXTURES / "legacy-plan.md").read_text(encoding="utf-8"))

    first = load_plan_tree(source)
    source.write_text(source.read_text(encoding="utf-8").replace("- [ ]", "- [x]", 1))
    second = load_plan_tree(source)

    assert first == second
    assert len(first.tasks) == 2
    one, two = first.tasks
    assert one.task_id.endswith(".task-1")
    assert two.task_id.endswith(".task-2")
    assert one.dependencies == ()
    assert two.dependencies == (one.task_id,)
    assert one.owned_files == ()
    assert one.owned_resources == (f"legacy-plan:{first.master.plan_id}",)
    assert two.owned_resources == one.owned_resources
    assert one.required_inputs == ("Read the complete legacy plan before working.",)
    assert one.acceptance_check == "commit the result, tick this checkbox, and leave a clean tree"
    assert one.source_requirement == "Keep old checkbox plans usable."
