"""T27 — export validated plans in the form the installed runner can build.

The exporter renders one plan document from a validated tree: header lines,
saved decisions, agreed outcomes, the ``gowork-plan`` manifest and the
checkbox lines in dependency order. What it writes is parsed back through
the implemented parser before anything reaches disk, so an impossible plan
is refused instead of exported, and an older runner (no manifest dispatch)
gets a sequential single-project checklist or a clear refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.gowork_export import (
    ExportError,
    PlanHeader,
    RuntimeSupport,
    check_plan,
    export_plan,
    installed_runtime,
    plan_template,
    plan_tree_from_manifest,
    write_plan,
)
from claude_code_core.gowork_handoff import plan_decisions
from claude_code_core.gowork_plan import (
    PlanValidationError,
    has_manifest,
    load_plan_tree,
    parse_plan_tree,
)
from claude_code_core.gowork_schedule import ReadyTask, ready_tasks
from claude_code_core.gowork_state import open_build_state
from claude_code_core.task_loop import (
    LoopOutcome,
    ManifestResult,
    Status,
    TaskLoop,
    open_tasks,
    plan_goal,
)

FIXTURES = Path(__file__).parent / "fixtures"
API, PAGE, STYLES, POST = (
    "product.catalog-api",
    "website.catalog-page",
    "website.page-styles",
    "marketing.launch-post",
)
HEADER = PlanHeader(
    title="Launch the business",
    goal="Customers can browse the catalog and hear about the launch",
    done_when="The catalog page is live locally and the announcement is reviewed",
    check="uv run pytest tests -q",
    try_command="uv run python -m website",
    decisions=("Catalog contract first; the page follows it", "No paid tools during the build"),
)


def _manifest(text: str) -> dict:
    start = text.index("```gowork-plan\n") + len("```gowork-plan\n")
    return json.loads(text[start : text.index("\n```", start)])


@pytest.fixture
def tree(tmp_path: Path):
    source = tmp_path / "master-plan.md"
    source.write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    return load_plan_tree(source)


def test_a_multi_project_plan_validates_and_schedules_through_the_parser(tmp_path, tree) -> None:
    export = export_plan(tree, HEADER, runtime=RuntimeSupport(manifest_dispatch=True))
    path = write_plan(tmp_path / "exported-plan.md", export)

    assert export.format == "manifest" and has_manifest(path.read_text(encoding="utf-8"))
    loaded = load_plan_tree(path)
    assert loaded.plans == tree.plans
    assert loaded.tasks == tree.tasks
    assert loaded.requirements == tree.requirements
    text = path.read_text(encoding="utf-8")
    assert plan_goal(text) == (HEADER.goal, HEADER.done_when)
    assert plan_decisions(text) == HEADER.decisions
    assert "Check: uv run pytest tests -q" in text
    assert "REQ-LAUNCH-POST: A launch announcement is ready to publish" in text
    # The checkbox lines are the same tasks in an order an older runner may follow.
    assert open_tasks(text) == [tree.task(t).outcome for t in (API, PAGE, STYLES, POST)]
    state = open_build_state(tmp_path / "build.json", loaded, build_id="b")
    assert [t.task_id for t in ready_tasks(state)] == [API, STYLES, POST]
    assert ready_tasks(state)[0] == ReadyTask(
        task_id=API, plan_id="product", project_path=loaded.get("product").project_path
    )
    report = check_plan(text, source_path=path, runtime=RuntimeSupport(manifest_dispatch=True))
    assert report.ok and report.format == "manifest"
    assert report.ready_now == (API, STYLES, POST)
    assert report.projects == 4 and report.tasks == 4


def test_an_old_checkbox_plan_still_works_on_both_runtimes(tmp_path: Path) -> None:
    text = (FIXTURES / "legacy-plan.md").read_text(encoding="utf-8")
    path = tmp_path / "PLAN.md"
    path.write_text(text, encoding="utf-8")
    for runtime in (RuntimeSupport(manifest_dispatch=True), RuntimeSupport.LEGACY):
        report = check_plan(text, source_path=path, runtime=runtime)
        assert report.ok and report.format == "checkbox", report.problems
        assert report.tasks == len(open_tasks(text)) + text.count("- [x]")
        assert report.ready_now == (parse_plan_tree(text, source_path=path).tasks[0].task_id,)
    assert open_tasks(text) == open_tasks(text)  # the exporter never rewrites an old plan


def test_an_older_runtime_gets_a_sequential_single_project_checklist(tmp_path: Path) -> None:
    raw = _manifest((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    for plan in raw["plans"]:
        plan["project_path"] = "shop"  # one project: the legacy runner can build it
    tree = plan_tree_from_manifest(raw, base_dir=tmp_path)

    export = export_plan(tree, HEADER, runtime=RuntimeSupport.LEGACY)

    assert export.format == "checkbox" and not has_manifest(export.text)
    assert open_tasks(export.text) == [tree.task(t).outcome for t in (API, PAGE, STYLES, POST)]
    assert "Depends on: Publish the checked product catalog contract" in export.text
    assert "Files: src/pages/catalog.tsx" in export.text
    assert "Inputs: product.catalog-api: versioned catalog contract" in export.text
    assert "Verify: npm test -- catalog-page" in export.text
    assert any("one at a time" in note for note in export.notes)
    legacy = parse_plan_tree(export.text, source_path=tmp_path / "PLAN.md")
    assert legacy.is_legacy and len(legacy.tasks) == 4
    assert plan_decisions(export.text) == HEADER.decisions


def test_an_older_runtime_refuses_a_multi_project_plan_clearly(tree) -> None:
    with pytest.raises(ExportError) as exc:
        export_plan(tree, HEADER, runtime=RuntimeSupport.LEGACY)
    message = str(exc.value)
    assert "one project" in message and "product" in message and "website" in message
    assert "upgrade" in message.lower()


def test_an_impossible_plan_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    raw = _manifest((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    raw["tasks"][0]["dependencies"] = [PAGE]  # api ↔ page: a cycle
    with pytest.raises(PlanValidationError, match="cycle"):
        plan_tree_from_manifest(raw, base_dir=tmp_path)

    raw = _manifest((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    raw["tasks"][3]["owned_files"] = ["../outside.md"]
    with pytest.raises(PlanValidationError, match="inside its project"):
        plan_tree_from_manifest(raw, base_dir=tmp_path)
    assert not (tmp_path / "PLAN.md").exists()


async def test_a_hand_written_impossible_manifest_cannot_start_by_accident(tmp_path: Path) -> None:
    raw = _manifest((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    raw["tasks"][0]["dependencies"] = [PAGE]
    text = "# Bad plan\n\nGoal: x\n\n```gowork-plan\n" + json.dumps(raw) + "\n```\n"
    path = tmp_path / "master-plan.md"
    path.write_text(text, encoding="utf-8")

    report = check_plan(text, source_path=path, runtime=RuntimeSupport(manifest_dispatch=True))
    assert not report.ok and any("cycle" in p for p in report.problems)
    assert report.ready_now == ()

    started: list[str] = []
    reports: list[str] = []

    async def worker(task: ReadyTask) -> ManifestResult:
        started.append(task.task_id)
        return ManifestResult(task.task_id, True, commit="c")

    async def never(*_: object) -> None:
        raise AssertionError("never called")

    async def report_line(line: str) -> None:
        reports.append(line)

    loop = TaskLoop(
        plan_path=path,
        repo_dir=tmp_path,
        run_round=never,  # type: ignore[arg-type]
        ask=never,  # type: ignore[arg-type]
        report=report_line,
        manifest_worker=worker,
        state_path=tmp_path / "build.json",
        build_id="b",
    )
    outcome: LoopOutcome = await loop.run()
    assert outcome.status is Status.STUCK and "cycle" in outcome.detail
    assert started == []
    assert not (tmp_path / "build.json").exists()


def test_check_names_unsupported_behaviour_on_an_older_runtime(tmp_path: Path) -> None:
    text = (FIXTURES / "validated-plan.md").read_text(encoding="utf-8")
    path = tmp_path / "master-plan.md"
    path.write_text(text, encoding="utf-8")

    report = check_plan(text, source_path=path, runtime=RuntimeSupport.LEGACY)

    assert not report.ok
    assert any("one project" in p for p in report.problems)
    assert any("manifest" in p for p in report.problems)
    assert report.ready_now == ()


def test_the_template_is_itself_a_valid_manifest_plan(tmp_path: Path) -> None:
    text = plan_template()
    path = tmp_path / "PLAN.md"
    report = check_plan(text, source_path=path, runtime=RuntimeSupport(manifest_dispatch=True))
    assert report.ok and report.format == "manifest", report.problems
    for field in (
        "Goal:",
        "Done when:",
        "Check:",
        "## Decisions",
        "## Agreed outcomes",
        '"owned_files"',
        '"required_inputs"',
        '"acceptance_check"',
        '"dependencies"',
        "Files:",
        "Verify:",
    ):
        assert field in text
    assert "replace every example value" in text.lower()


def test_write_plan_never_overwrites_by_accident(tmp_path: Path, tree) -> None:
    export = export_plan(tree, HEADER, runtime=RuntimeSupport(manifest_dispatch=True))
    path = tmp_path / "exported-plan.md"
    write_plan(path, export)
    path.write_text("hand edits\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_plan(path, export)
    assert path.read_text(encoding="utf-8") == "hand edits\n"
    write_plan(path, export, overwrite=True)
    assert has_manifest(path.read_text(encoding="utf-8"))
    assert not list(tmp_path.glob("*.tmp"))


def test_the_installed_runtime_builds_manifests() -> None:
    assert installed_runtime().manifest_dispatch is True
    assert RuntimeSupport.LEGACY.manifest_dispatch is False
