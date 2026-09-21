"""Export a validated plan in the form the installed runner can build (T27).

The planner ends with a tree of plans, agreed outcomes, complete task
assignments and saved decisions. This module turns that into one plan
document — header lines, ``## Decisions``, ``## Agreed outcomes``, the
``gowork-plan`` manifest and the checkbox lines in dependency order — and
proves the document by parsing it back through the implemented parser
(``gowork_plan``) before anything is written. A plan the parser refuses
(a dependency cycle, an unsafe owned path, an uncovered outcome) is never
exported, so it cannot start by accident.

Compatibility is conservative and explicit. A runner without manifest
dispatch (:data:`RuntimeSupport.LEGACY`) builds checkbox lines one at a time
in one project: such a runner gets a sequential checklist for a
single-project tree and a clear refusal for anything else. It is never
handed a manifest whose meaning it would silently lose. :func:`check_plan`
reports the same judgements for a plan written by hand.
"""

from __future__ import annotations

import inspect
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal

from claude_code_core.gowork_plan import (
    SCHEMA_VERSION,
    PlanTree,
    PlanValidationError,
    has_manifest,
    parse_plan_tree,
    render_plan_manifest,
)
from claude_code_core.gowork_schedule import ready_tasks
from claude_code_core.gowork_state import open_build_state

PlanFormat = Literal["manifest", "checkbox"]

_TASK_LINE_RE = re.compile(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*\S)")
_HEADING_RE = re.compile(r"^#{1,6}\s")


class ExportError(ValueError):
    """The installed runner cannot build this plan as asked; nothing was written."""


@dataclass(frozen=True, slots=True)
class RuntimeSupport:
    """What the runner that will build the plan understands."""

    #: T11b: builds a ``gowork-plan`` manifest from its ledger (several
    #: projects, dependencies, ownership). Without it only checkbox lines run,
    #: one at a time, in the plan's own project.
    manifest_dispatch: bool
    schema_version: int = SCHEMA_VERSION

    LEGACY: ClassVar[RuntimeSupport]

    @property
    def label(self) -> str:
        return "manifest runner" if self.manifest_dispatch else "checkbox-only runner"


RuntimeSupport.LEGACY = RuntimeSupport(manifest_dispatch=False)


def installed_runtime() -> RuntimeSupport:
    """What the ``claude_code_core`` installed here can build (inspected, not assumed)."""
    from claude_code_core.task_loop import TaskLoop

    parameters = inspect.signature(TaskLoop.__init__).parameters
    return RuntimeSupport(manifest_dispatch="manifest_worker" in parameters)


@dataclass(frozen=True, slots=True)
class PlanHeader:
    """The plan-level lines the runner and the person read first."""

    title: str
    goal: str
    check: str
    done_when: str = ""
    try_command: str = ""
    decisions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in ("title", "goal", "check"):
            if not getattr(self, field).strip():
                raise ExportError(f"the plan's {field} must not be empty")
        for text in (self.title, self.goal, self.done_when, self.check, self.try_command):
            if "\n" in text:
                raise ExportError("header lines must be single lines")


@dataclass(frozen=True, slots=True)
class PlanExport:
    """A plan document that parsed back correctly, and how it was adapted."""

    text: str
    format: PlanFormat
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanCheck:
    """What the installed runner would make of a plan document."""

    format: PlanFormat
    problems: tuple[str, ...]
    ready_now: tuple[str, ...]
    projects: int
    tasks: int
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def plan_tree_from_manifest(raw: dict, *, base_dir: Path) -> PlanTree:
    """Validate a manifest given as data through the same parser a plan file uses."""
    text = "```gowork-plan\n" + json.dumps(raw) + "\n```\n"
    return parse_plan_tree(text, source_path=Path(base_dir) / "plan.md")


def dependency_order(tree: PlanTree) -> tuple[str, ...]:
    """Task ids in an order every runner may follow one at a time.

    A task comes after all of its dependencies; among the tasks that could go
    next, plan order wins, so the result is stable and reads like the plan.
    """
    ids = [task.task_id for task in tree.tasks]
    done: list[str] = []
    remaining = list(ids)
    while remaining:
        for task_id in remaining:
            if all(dep in done for dep in tree.task(task_id).dependencies):
                done.append(task_id)
                remaining.remove(task_id)
                break
        else:  # pragma: no cover - the parser refuses cycles before this point
            raise PlanValidationError("dependency cycle detected: " + ", ".join(remaining))
    return tuple(done)


def _projects(tree: PlanTree) -> dict[Path, list[str]]:
    by_path: dict[Path, list[str]] = {}
    for plan in tree.plans:
        by_path.setdefault(plan.project_path, []).append(plan.plan_id)
    return by_path


def _multi_project_reason(tree: PlanTree) -> str | None:
    projects = _projects(tree)
    if len(projects) <= 1:
        return None
    names = ", ".join(
        f"{path.name or path.as_posix()} ({'/'.join(plans)})" for path, plans in projects.items()
    )
    return (
        "the installed runner builds one project per plan, but this plan spans "
        f"{len(projects)} projects: {names}. Upgrade the runner for manifest dispatch, "
        "or export one plan per project."
    )


def _task_lines(tree: PlanTree, task_id: str) -> list[str]:
    task = tree.task(task_id)
    plan = tree.get(task.plan_id)
    depends = (
        "; ".join(f"{tree.task(dep).outcome} (`{dep}`)" for dep in task.dependencies) or "none"
    )
    lines = [
        f"- [ ] {task.outcome}",
        f"  Task: `{task.task_id}` in plan `{task.plan_id}` "
        f"(project {plan.project_path.name or plan.project_path.as_posix()})",
        f"  Depends on: {depends}",
        "  Inputs: " + "; ".join(task.required_inputs),
        "  Files: " + (", ".join(task.owned_files) or "none"),
    ]
    if task.owned_resources:
        lines.append("  Resources: " + ", ".join(task.owned_resources))
    lines += [
        f"  Result: {task.output}",
        f"  Verify: {task.acceptance_check}",
        f"  Outcome: {task.source_requirement}",
    ]
    return lines


def _render(
    tree: PlanTree, header: PlanHeader, *, with_manifest: bool, relative_to: Path | None
) -> str:
    parts = [f"# {header.title}", "", f"Goal: {header.goal}"]
    if header.done_when:
        parts.append(f"Done when: {header.done_when}")
    parts.append(f"Check: {header.check}")
    if header.try_command:
        parts.append(f"Try: {header.try_command}")
    parts += ["", "## Decisions", ""]
    parts += [f"- {decision}" for decision in header.decisions] or ["- none recorded yet"]
    parts += ["", "## Agreed outcomes", ""]
    parts += [f"- {req.requirement_id}: {req.outcome}" for req in tree.requirements] or [
        "- (legacy plan: no agreed outcomes declared)"
    ]
    if with_manifest:
        parts += ["", render_plan_manifest(tree, relative_to=relative_to).rstrip()]
    parts += ["", "## Tasks", ""]
    for task_id in dependency_order(tree):
        parts += _task_lines(tree, task_id)
    return "\n".join(parts) + "\n"


def export_plan(
    tree: PlanTree,
    header: PlanHeader,
    *,
    runtime: RuntimeSupport | None = None,
    relative_to: Path | None = None,
) -> PlanExport:
    """Render *tree* for *runtime* and prove it by parsing the text back.

    ``relative_to`` is the folder the plan will be written in; project paths
    are then written relative to it. Raises :class:`ExportError` when the
    runner cannot build the plan as written, and never writes anything.
    """
    runtime = runtime or installed_runtime()
    if not tree.tasks:
        raise ExportError("the plan has no tasks to build")
    if tree.is_legacy:
        raise ExportError("a legacy checkbox plan is already in its runnable form; not exported")
    source = (relative_to or Path.cwd()) / "plan.md"
    if runtime.manifest_dispatch:
        text = _render(tree, header, with_manifest=True, relative_to=relative_to)
        try:
            parsed = parse_plan_tree(text, source_path=source)
        except PlanValidationError as exc:
            raise ExportError(f"the exported plan did not parse back: {exc}") from exc
        if (parsed.plans, parsed.tasks, parsed.requirements) != (
            tree.plans,
            tree.tasks,
            tree.requirements,
        ):
            raise ExportError("the exported plan did not round-trip through the parser")
        return PlanExport(
            text,
            "manifest",
            (
                "Older runners ignore the manifest and build the checkbox lines one at a "
                "time in the same dependency order.",
            ),
        )
    reason = _multi_project_reason(tree)
    if reason is not None:
        raise ExportError(reason)
    text = _render(tree, header, with_manifest=False, relative_to=relative_to)
    parsed = parse_plan_tree(text, source_path=source)
    if not parsed.is_legacy or len(parsed.tasks) != len(tree.tasks):
        raise ExportError("the exported checklist did not parse back as a checkbox plan")
    return PlanExport(
        text,
        "checkbox",
        (
            "Exported for a runner without manifest support: tasks run one at a time in "
            "dependency order, with no parallel safety claimed.",
        ),
    )


def write_plan(path: Path, export: PlanExport, *, overwrite: bool = False) -> Path:
    """Write the exported plan atomically; an existing file needs ``overwrite``."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; pass overwrite=True to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(export.text, encoding="utf-8")
    tmp.replace(path)
    return path


def _open_labels(text: str) -> list[str]:
    return [m.group(2).strip() for line in text.splitlines() if (m := _TASK_LINE_RE.match(line))]


def _checkbox_order_problem(tree: PlanTree, text: str) -> str | None:
    """Why an older runner building the checkbox lines would not build the tasks."""
    labels = _open_labels(text)
    by_outcome = {task.outcome: task.task_id for task in tree.tasks}
    order = [by_outcome.get(label) for label in labels]
    if None in order or sorted(t for t in order if t) != sorted(by_outcome.values()):
        return (
            "the checkbox lines don't match the manifest tasks one to one; an older runner "
            "would build the checkboxes as written and ignore the manifest"
        )
    seen: list[str] = []
    for task_id in order:
        assert task_id is not None
        for dep in tree.task(task_id).dependencies:
            if dep not in seen:
                return (
                    f"checkbox '{tree.task(task_id).outcome}' comes before its dependency "
                    f"'{tree.task(dep).outcome}'; an older runner would build it too early"
                )
        seen.append(task_id)
    return None


def _ready_now(tree: PlanTree) -> tuple[str, ...]:
    with tempfile.TemporaryDirectory() as folder:
        state = open_build_state(Path(folder) / "check.json", tree, build_id="check")
        return tuple(task.task_id for task in ready_tasks(state))


def check_plan(text: str, *, source_path: Path, runtime: RuntimeSupport | None = None) -> PlanCheck:
    """What *runtime* would do with a plan document, and what stops it."""
    runtime = runtime or installed_runtime()
    fmt: PlanFormat = "manifest" if has_manifest(text) else "checkbox"
    try:
        tree = parse_plan_tree(text, source_path=source_path)
    except PlanValidationError as exc:
        return PlanCheck(fmt, (str(exc),), (), 0, 0)
    problems: list[str] = []
    notes: list[str] = []
    if fmt == "manifest" and not runtime.manifest_dispatch:
        problems.append(
            "this plan carries a gowork-plan manifest, but the installed runner ignores "
            "manifests and builds the checkbox lines one at a time"
        )
        reason = _multi_project_reason(tree)
        if reason is not None:
            problems.append(reason)
        elif (order_problem := _checkbox_order_problem(tree, text)) is not None:
            problems.append(order_problem)
    if fmt == "checkbox":
        notes.append("checkbox plan: tasks run one at a time in document order")
    elif not tree.tasks:
        problems.append("the manifest declares no tasks")
    ready = _ready_now(tree) if not problems else ()
    return PlanCheck(
        fmt,
        tuple(problems),
        ready,
        len(_projects(tree)),
        len(tree.tasks),
        tuple(notes),
    )


_TEMPLATE_RAW = {
    "schema_version": SCHEMA_VERSION,
    "plans": [
        {"id": "shop", "version": 1, "project_path": "."},
        {"id": "shop-website", "version": 1, "parent_id": "shop", "project_path": "../website"},
    ],
    "requirements": [
        {"id": "REQ-CATALOG", "outcome": "The product catalog contract is published"},
        {"id": "REQ-CATALOG-PAGE", "outcome": "The website shows the product catalog"},
    ],
    "tasks": [
        {
            "id": "shop.catalog-contract",
            "plan_id": "shop",
            "plan_version": 1,
            "outcome": "Publish the checked product catalog contract",
            "dependencies": [],
            "owned_files": ["src/catalog.py", "tests/test_catalog.py"],
            "owned_resources": [],
            "required_inputs": ["REQ-CATALOG: the agreed catalog fields"],
            "output": "A versioned catalog contract with passing contract tests",
            "acceptance_check": "uv run pytest tests/test_catalog.py -q",
            "source_requirement": "REQ-CATALOG",
        },
        {
            "id": "website.catalog-page",
            "plan_id": "shop-website",
            "plan_version": 1,
            "outcome": "Show the product catalog on the website",
            "dependencies": ["shop.catalog-contract"],
            "owned_files": ["src/pages/catalog.tsx"],
            "owned_resources": [],
            "required_inputs": ["shop.catalog-contract: the versioned catalog contract"],
            "output": "A catalog page backed by the accepted contract",
            "acceptance_check": "npm test -- catalog-page",
            "source_requirement": "REQ-CATALOG-PAGE",
        },
    ],
}

_TEMPLATE_NOTE = (
    "This is a template, not a runnable plan: replace every example value before use. "
    "One task = one outcome a fresh worker can build and check in 15-30 minutes. A task "
    "in another project that consumes a result must name it as a required input "
    "(`<task-id>: what it consumes`). Two tasks that may run together may not own the "
    "same file or folder — give the shared file one owner or add a dependency."
)


def plan_template() -> str:
    """The planner-facing example of a complete plan; it validates as written."""
    with tempfile.TemporaryDirectory() as folder:
        tree = plan_tree_from_manifest(_TEMPLATE_RAW, base_dir=Path(folder))
        header = PlanHeader(
            title="Build name",
            goal="A concrete outcome the person asked for",
            done_when="Observable completion evidence",
            check="uv run pytest tests -q",
            try_command="uv run python -m shop",
            decisions=("Catalog contract first; the page follows it",),
        )
        export = export_plan(
            tree,
            header,
            runtime=RuntimeSupport(manifest_dispatch=True),
            relative_to=Path(folder),
        )
    lines = export.text.split("\n", 1)
    return lines[0] + "\n\n" + _TEMPLATE_NOTE + "\n" + lines[1]


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m claude_code_core.gowork_export",
        description="Check what the installed Go Work runner would make of a plan file.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate a plan file against the installed runner")
    check.add_argument("plan", type=Path)
    check.add_argument(
        "--legacy-runner",
        action="store_true",
        help="judge the plan as a runner without manifest dispatch would",
    )
    sub.add_parser("template", help="print the plan template")
    args = parser.parse_args(argv)
    if args.command == "template":
        sys.stdout.write(plan_template())
        return 0
    runtime = RuntimeSupport.LEGACY if args.legacy_runner else installed_runtime()
    text = args.plan.read_text(encoding="utf-8", errors="replace")
    report = check_plan(text, source_path=args.plan, runtime=runtime)
    print(
        f"{args.plan}: {report.format} plan, {report.tasks} task(s) in {report.projects} "
        f"project(s), judged for a {runtime.label}"
    )
    for note in report.notes:
        print(f"  note: {note}")
    for problem in report.problems:
        print(f"  problem: {problem}")
    if report.ok:
        print("  ready now: " + (", ".join(report.ready_now) or "nothing"))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
