"""Identity and task-assignment parser for Go Work plan documents.

New plans may contain one ``gowork-plan`` JSON fence describing a rooted plan
tree.  Keeping identity metadata in a dedicated fence leaves the surrounding
Markdown readable by the existing checkbox runner.  A document without the
fence is a supported legacy single-plan tree whose tasks run conservatively in
document order because the old format does not declare safe ownership.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MANIFEST_RE = re.compile(
    r"^[ \t]*```gowork-plan[ \t]*\r?\n(?P<body>.*?)(?:\r?\n)?^[ \t]*```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
_TASK_RE = re.compile(r"^\s*[-*]\s+\[(?: |x|X)\]\s+(.*\S)")
_GOAL_RE = re.compile(r"^\s*\**Goal:\**\s*(.+?)\s*$", re.IGNORECASE)
_CHECK_RE = re.compile(r"^\s*\**Check:\**\s*`?(.+?)`?\s*$", re.IGNORECASE)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PATTERN_CHARS = frozenset("*?[")

_TASK_FIELDS = (
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
)


class PlanValidationError(ValueError):
    """A Go Work plan manifest cannot safely identify its plan tree."""


@dataclass(frozen=True, slots=True)
class PlanIdentity:
    """Stable identity and location for one plan in a master/child tree."""

    plan_id: str
    version: int
    project_path: Path
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskAssignment:
    """Everything a fresh worker needs to own and check one outcome."""

    task_id: str
    plan_id: str
    plan_version: int
    outcome: str
    dependencies: tuple[str, ...]
    owned_files: tuple[str, ...]
    owned_resources: tuple[str, ...]
    required_inputs: tuple[str, ...]
    output: str
    acceptance_check: str
    source_requirement: str


@dataclass(frozen=True, slots=True)
class Requirement:
    """One outcome Drew agreed to, which at least one task must deliver."""

    requirement_id: str
    outcome: str


@dataclass(frozen=True, slots=True)
class OwnershipConflict:
    """Two tasks that must not run at the same time, and what they share."""

    first_task_id: str
    second_task_id: str
    files: tuple[str, ...]
    resources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanTree:
    """A validated rooted tree of plans, potentially spanning projects."""

    plans: tuple[PlanIdentity, ...]
    tasks: tuple[TaskAssignment, ...] = ()
    schema_version: int = SCHEMA_VERSION
    is_legacy: bool = False
    requirements: tuple[Requirement, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise PlanValidationError(
                f"unsupported gowork-plan schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        _validate_tree(self.plans)
        _validate_assignments(self.plans, self.tasks)
        _validate_dependencies(self.plans, self.tasks)
        _validate_owned_files(self.tasks)
        if not self.is_legacy:
            _validate_coverage(self.requirements, self.tasks)

    @property
    def master(self) -> PlanIdentity:
        """Return the one root plan."""
        return next(plan for plan in self.plans if plan.parent_id is None)

    def get(self, plan_id: str) -> PlanIdentity:
        """Return *plan_id*, raising ``KeyError`` when it is not in this tree."""
        for plan in self.plans:
            if plan.plan_id == plan_id:
                return plan
        raise KeyError(plan_id)

    def children(self, plan_id: str) -> tuple[PlanIdentity, ...]:
        """Return direct children in their declared order."""
        return tuple(plan for plan in self.plans if plan.parent_id == plan_id)

    def task(self, task_id: str) -> TaskAssignment:
        """Return *task_id*, raising ``KeyError`` when it is not assigned."""
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(task_id)

    def tasks_for(self, plan_id: str) -> tuple[TaskAssignment, ...]:
        """Return tasks owned by *plan_id* in their declared order."""
        return tuple(task for task in self.tasks if task.plan_id == plan_id)

    def ownership_conflicts(self) -> tuple[OwnershipConflict, ...]:
        """Return every task pair whose ownership overlaps, in declared order.

        A conflict keeps that pair from being dispatched together; it does not
        invalidate the plan or hold back unrelated tasks.
        """
        conflicts: list[OwnershipConflict] = []
        for index, first in enumerate(self.tasks):
            for second in self.tasks[index + 1 :]:
                conflict = self._conflict(first, second)
                if conflict is not None:
                    conflicts.append(conflict)
        return tuple(conflicts)

    def can_run_together(self, first_task_id: str, second_task_id: str) -> bool:
        """Return whether two tasks own nothing in common."""
        first = self.task(first_task_id)
        second = self.task(second_task_id)
        return first.task_id != second.task_id and self._conflict(first, second) is None

    def _conflict(self, first: TaskAssignment, second: TaskAssignment) -> OwnershipConflict | None:
        files: list[str] = []
        if self.get(first.plan_id).project_path == self.get(second.plan_id).project_path:
            for first_file in first.owned_files:
                for second_file in second.owned_files:
                    shared = _shared_file(first_file, second_file)
                    if shared is not None and shared not in files:
                        files.append(shared)
        resources = tuple(item for item in first.owned_resources if item in second.owned_resources)
        if not files and not resources:
            return None
        return OwnershipConflict(
            first_task_id=first.task_id,
            second_task_id=second.task_id,
            files=tuple(files),
            resources=resources,
        )


def _canonical_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve(strict=False)


def _validate_plan_id(value: object, index: int) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PlanValidationError(
            f"plan {index} id must start with a letter or number and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    return value


def _validate_version(value: object, plan_id: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PlanValidationError(f"plan '{plan_id}' version must be a positive integer")
    return value


def _required_field(raw: dict[str, object], field: str, index: int) -> object:
    if field not in raw:
        raise PlanValidationError(f"task {index} missing required field '{field}'")
    return raw[field]


def _non_empty_string(value: object, *, field: str, task_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError(f"task {task_name} field '{field}' must be a non-empty string")
    return value.strip()


def _string_list(value: object, *, field: str, task_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PlanValidationError(f"task {task_name} field '{field}' must be a list")
    items: list[str] = []
    for item_index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise PlanValidationError(
                f"task {task_name} field '{field}' item {item_index} must be a non-empty string"
            )
        items.append(item.strip())
    return tuple(items)


def _parse_assignment(raw: object, index: int) -> TaskAssignment:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"task {index} must be an object")
    for field in _TASK_FIELDS:
        _required_field(raw, field, index)

    task_id = _validate_task_id(raw["id"], index)
    task_name = f"'{task_id}'"
    plan_id = _non_empty_string(raw["plan_id"], field="plan_id", task_name=task_name)
    if not _ID_RE.fullmatch(plan_id):
        raise PlanValidationError(
            f"task {task_name} plan_id must start with a letter or number and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    plan_version = raw["plan_version"]
    if isinstance(plan_version, bool) or not isinstance(plan_version, int) or plan_version < 1:
        raise PlanValidationError(f"task {task_name} plan_version must be a positive integer")
    dependencies = _string_list(raw["dependencies"], field="dependencies", task_name=task_name)
    for dependency in dependencies:
        if not _ID_RE.fullmatch(dependency):
            raise PlanValidationError(
                f"task {task_name} dependency '{dependency}' is not a valid task id"
            )
    owned_files = _string_list(raw["owned_files"], field="owned_files", task_name=task_name)
    owned_resources = _string_list(
        raw["owned_resources"], field="owned_resources", task_name=task_name
    )
    if not owned_files and not owned_resources:
        raise PlanValidationError(
            f"task {task_name} must declare at least one owned file or resource"
        )
    required_inputs = _string_list(
        raw["required_inputs"], field="required_inputs", task_name=task_name
    )
    if not required_inputs:
        raise PlanValidationError(f"task {task_name} required_inputs must not be empty")
    return TaskAssignment(
        task_id=task_id,
        plan_id=plan_id,
        plan_version=plan_version,
        outcome=_non_empty_string(raw["outcome"], field="outcome", task_name=task_name),
        dependencies=dependencies,
        owned_files=owned_files,
        owned_resources=owned_resources,
        required_inputs=required_inputs,
        output=_non_empty_string(raw["output"], field="output", task_name=task_name),
        acceptance_check=_non_empty_string(
            raw["acceptance_check"], field="acceptance_check", task_name=task_name
        ),
        source_requirement=_non_empty_string(
            raw["source_requirement"], field="source_requirement", task_name=task_name
        ),
    )


def _validate_task_id(value: object, index: int) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PlanValidationError(
            f"task {index} id must start with a letter or number and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    return value


def _parse_identity(raw: object, index: int, base_dir: Path) -> PlanIdentity:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"plan {index} must be an object")
    plan_id = _validate_plan_id(raw.get("id"), index)
    version = _validate_version(raw.get("version"), plan_id)
    project_value = raw.get("project_path")
    if not isinstance(project_value, str) or not project_value.strip():
        raise PlanValidationError(f"plan '{plan_id}' project_path must be a non-empty string")
    parent_value = raw.get("parent_id")
    if parent_value is not None:
        parent_value = _validate_plan_id(parent_value, index)
    return PlanIdentity(
        plan_id=plan_id,
        version=version,
        project_path=_canonical_path(project_value, base_dir),
        parent_id=parent_value,
    )


def _validate_tree(plans: tuple[PlanIdentity, ...]) -> None:
    if not plans:
        raise PlanValidationError("plan manifest must contain at least one plan")

    by_id: dict[str, PlanIdentity] = {}
    for plan in plans:
        if plan.plan_id in by_id:
            raise PlanValidationError(f"duplicate plan id '{plan.plan_id}'")
        by_id[plan.plan_id] = plan

    for plan in plans:
        if plan.parent_id is not None and plan.parent_id not in by_id:
            raise PlanValidationError(
                f"plan '{plan.plan_id}' has unknown parent '{plan.parent_id}'"
            )

    finished: set[str] = set()
    for start in by_id:
        if start in finished:
            continue
        trail: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current not in finished:
            if current in positions:
                cycle = [*trail[positions[current] :], current]
                raise PlanValidationError(f"parent cycle detected: {' -> '.join(cycle)}")
            positions[current] = len(trail)
            trail.append(current)
            current = by_id[current].parent_id
        finished.update(trail)

    roots = [plan.plan_id for plan in plans if plan.parent_id is None]
    if len(roots) != 1:
        raise PlanValidationError(
            f"plan manifest must have exactly one master plan; found {len(roots)}"
        )


def _validate_assignments(
    plans: tuple[PlanIdentity, ...], tasks: tuple[TaskAssignment, ...]
) -> None:
    plans_by_id = {plan.plan_id: plan for plan in plans}
    task_ids: set[str] = set()
    for task in tasks:
        if task.task_id in task_ids:
            raise PlanValidationError(f"duplicate task id '{task.task_id}'")
        task_ids.add(task.task_id)
        plan = plans_by_id.get(task.plan_id)
        if plan is None:
            raise PlanValidationError(
                f"task '{task.task_id}' refers to unknown plan '{task.plan_id}'"
            )
        if task.plan_version != plan.version:
            raise PlanValidationError(
                f"task '{task.task_id}' plan_version {task.plan_version} does not match "
                f"plan '{plan.plan_id}' version {plan.version}"
            )


def _validate_dependencies(
    plans: tuple[PlanIdentity, ...], tasks: tuple[TaskAssignment, ...]
) -> None:
    plans_by_id = {plan.plan_id: plan for plan in plans}
    by_id = {task.task_id: task for task in tasks}
    for task in tasks:
        seen: set[str] = set()
        for dependency in task.dependencies:
            if dependency == task.task_id:
                raise PlanValidationError(f"task '{task.task_id}' depends on itself")
            if dependency in seen:
                raise PlanValidationError(
                    f"task '{task.task_id}' lists dependency '{dependency}' more than once"
                )
            seen.add(dependency)
            if dependency not in by_id:
                raise PlanValidationError(
                    f"task '{task.task_id}' depends on unknown task '{dependency}'"
                )

    finished: set[str] = set()
    for start in by_id:
        if start in finished:
            continue
        trail: list[str] = [start]
        pending = [iter(by_id[start].dependencies)]
        while pending:
            dependency = next(pending[-1], None)
            if dependency is None:
                pending.pop()
                finished.add(trail.pop())
            elif dependency in trail:
                cycle = [*trail[trail.index(dependency) :], dependency]
                raise PlanValidationError(f"dependency cycle detected: {' -> '.join(cycle)}")
            elif dependency not in finished:
                trail.append(dependency)
                pending.append(iter(by_id[dependency].dependencies))

    # Projects are combined separately, so a result crossing between them must be
    # named as an input; otherwise the worker has no evidence of what it consumes.
    for task in tasks:
        project = plans_by_id[task.plan_id].project_path
        named = {item.split(":", 1)[0].strip() for item in task.required_inputs}
        for dependency in task.dependencies:
            other = plans_by_id[by_id[dependency].plan_id].project_path
            if other != project and dependency not in named:
                raise PlanValidationError(
                    f"task '{task.task_id}' depends on '{dependency}' in another project but "
                    f"no required input names it; add a required input starting with "
                    f"'{dependency}:'"
                )


def _validate_owned_files(tasks: tuple[TaskAssignment, ...]) -> None:
    for task in tasks:
        for value in task.owned_files:
            problem = _unsafe_path_reason(value)
            if problem is not None:
                raise PlanValidationError(f"task '{task.task_id}' owned file '{value}' {problem}")


def _unsafe_path_reason(value: str) -> str | None:
    if _CONTROL_RE.search(value):
        return "must not contain control characters"
    if "\\" in value:
        return "must use '/' separators"
    if value.startswith("~"):
        return "must not start with '~'"
    if value.startswith("/") or _DRIVE_RE.match(value):
        return "must be relative to its project"
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if ".." in parts:
        return "must stay inside its project (no '..' parts)"
    if parts and parts[0].lower() == ".git":
        return "must not be inside '.git'"
    return None


def _owned_prefix(value: str) -> tuple[str, ...]:
    """Return the literal leading parts of an owned path, stopping at any pattern."""
    parts: list[str] = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if _PATTERN_CHARS.intersection(part):
            break
        parts.append(part)
    return tuple(parts)


def _shared_file(first: str, second: str) -> str | None:
    """Return the narrower path when one owned path equals or contains the other."""
    first_parts = _owned_prefix(first)
    second_parts = _owned_prefix(second)
    shorter = min(len(first_parts), len(second_parts))
    if first_parts[:shorter] != second_parts[:shorter]:
        return None
    return first if len(first_parts) >= len(second_parts) else second


def _validate_coverage(
    requirements: tuple[Requirement, ...], tasks: tuple[TaskAssignment, ...]
) -> None:
    seen: set[str] = set()
    for requirement in requirements:
        if requirement.requirement_id in seen:
            raise PlanValidationError(f"duplicate requirement id '{requirement.requirement_id}'")
        seen.add(requirement.requirement_id)
    if tasks and not requirements:
        raise PlanValidationError(
            "a plan with tasks must list the agreed outcomes under 'requirements'"
        )
    for task in tasks:
        if task.source_requirement not in seen:
            raise PlanValidationError(
                f"task '{task.task_id}' source_requirement '{task.source_requirement}' is not "
                "an agreed outcome"
            )
    covered = {task.source_requirement for task in tasks}
    for requirement in requirements:
        if requirement.requirement_id not in covered:
            raise PlanValidationError(
                f"agreed outcome '{requirement.requirement_id}' is not covered by any task"
            )


def _parse_requirement(raw: object, index: int) -> Requirement:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"requirement {index} must be an object")
    requirement_id = raw.get("id")
    if not isinstance(requirement_id, str) or not _ID_RE.fullmatch(requirement_id):
        raise PlanValidationError(
            f"requirement {index} id must start with a letter or number and contain only "
            "letters, numbers, '.', '_' or '-'"
        )
    outcome = raw.get("outcome")
    if not isinstance(outcome, str) or not outcome.strip():
        raise PlanValidationError(
            f"requirement '{requirement_id}' field 'outcome' must be a non-empty string"
        )
    return Requirement(requirement_id=requirement_id, outcome=outcome.strip())


def _project_root(source_path: Path) -> Path:
    """Find the containing repository without invoking git; otherwise use the plan folder."""
    for candidate in (source_path.parent, *source_path.parents):
        if (candidate / ".git").exists():
            return candidate.resolve(strict=False)
    return source_path.parent.resolve(strict=False)


def _legacy_goal(text: str) -> str | None:
    for line in text.splitlines():
        if match := _GOAL_RE.match(line):
            return match.group(1).strip("* ")
    return None


def _legacy_check(text: str) -> str:
    for line in text.splitlines():
        if match := _CHECK_RE.match(line):
            quoted = re.search(r"`([^`]+)`", line)
            return (quoted.group(1) if quoted else match.group(1)).strip()
    return "commit the result, tick this checkbox, and leave a clean tree"


def _legacy_tree(text: str, source_path: Path) -> PlanTree:
    project = _project_root(source_path)
    canonical_source = source_path.resolve(strict=False)
    try:
        plan_name = canonical_source.relative_to(project).as_posix()
    except ValueError:
        plan_name = canonical_source.as_posix()
    identity_source = f"{project.as_posix()}\0{plan_name}".encode()
    digest = hashlib.sha256(identity_source).hexdigest()[:20]
    plan = PlanIdentity(
        plan_id=f"legacy-{digest}",
        version=1,
        project_path=project,
    )
    labels = [
        match.group(1).strip() for line in text.splitlines() if (match := _TASK_RE.match(line))
    ]
    source_requirement = _legacy_goal(text)
    acceptance_check = _legacy_check(text)
    tasks: list[TaskAssignment] = []
    for index, label in enumerate(labels, start=1):
        task_id = f"{plan.plan_id}.task-{index}"
        dependencies = (tasks[-1].task_id,) if tasks else ()
        tasks.append(
            TaskAssignment(
                task_id=task_id,
                plan_id=plan.plan_id,
                plan_version=plan.version,
                outcome=label,
                dependencies=dependencies,
                owned_files=(),
                owned_resources=(f"legacy-plan:{plan.plan_id}",),
                required_inputs=("Read the complete legacy plan before working.",),
                output=f"Completed legacy task: {label}",
                acceptance_check=acceptance_check,
                source_requirement=source_requirement or label,
            )
        )
    return PlanTree(plans=(plan,), tasks=tuple(tasks), is_legacy=True)


def has_manifest(text: str) -> bool:
    """Whether a plan document carries a ``gowork-plan`` manifest (the multi-plan path)."""
    return _MANIFEST_RE.search(text) is not None


def parse_plan_tree(text: str, *, source_path: Path) -> PlanTree:
    """Parse identity metadata from a Markdown plan.

    A plan without a manifest remains valid as a deterministic, version-one,
    single-plan tree.  Relative project paths in a manifest are resolved from
    the Markdown file's directory and stored canonically.
    """
    matches = list(_MANIFEST_RE.finditer(text))
    if not matches:
        return _legacy_tree(text, source_path)
    if len(matches) > 1:
        raise PlanValidationError("plan document must contain only one gowork-plan manifest")
    try:
        raw = json.loads(matches[0].group("body"))
    except json.JSONDecodeError as exc:
        raise PlanValidationError(f"invalid gowork-plan JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise PlanValidationError("gowork-plan manifest must be a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise PlanValidationError(
            f"unsupported gowork-plan schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )
    raw_plans = raw.get("plans")
    if not isinstance(raw_plans, list):
        raise PlanValidationError("gowork-plan manifest plans must be a list")
    raw_tasks = raw.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raise PlanValidationError("gowork-plan manifest tasks must be a list")
    raw_requirements = raw.get("requirements", [])
    if not isinstance(raw_requirements, list):
        raise PlanValidationError("gowork-plan manifest requirements must be a list")
    base_dir = source_path.resolve(strict=False).parent
    requirements = tuple(
        _parse_requirement(item, index) for index, item in enumerate(raw_requirements)
    )
    plans = tuple(_parse_identity(item, index, base_dir) for index, item in enumerate(raw_plans))
    tasks = tuple(_parse_assignment(item, index) for index, item in enumerate(raw_tasks))
    return PlanTree(
        plans=plans, tasks=tasks, schema_version=schema_version, requirements=requirements
    )


def load_plan_tree(source_path: Path) -> PlanTree:
    """Read *source_path* and parse its Go Work plan identities."""
    text = source_path.read_text(encoding="utf-8", errors="replace")
    return parse_plan_tree(text, source_path=source_path)


def _display_path(project_path: Path, relative_to: Path | None) -> str:
    if relative_to is None:
        return project_path.as_posix()
    return os.path.relpath(project_path, relative_to.resolve(strict=False))


def render_plan_manifest(tree: PlanTree, *, relative_to: Path | None = None) -> str:
    """Render a manifest fence that parses back into *tree*.

    ``relative_to`` makes project locations portable inside a related directory
    tree.  Absolute canonical locations are emitted when it is omitted.
    """
    manifest: dict[str, Any] = {
        "schema_version": tree.schema_version,
        "plans": [],
        "requirements": [
            {"id": item.requirement_id, "outcome": item.outcome} for item in tree.requirements
        ],
        "tasks": [],
    }
    rendered_plans: list[dict[str, object]] = []
    for plan in tree.plans:
        item: dict[str, object] = {
            "id": plan.plan_id,
            "version": plan.version,
            "project_path": _display_path(plan.project_path, relative_to),
        }
        if plan.parent_id is not None:
            item["parent_id"] = plan.parent_id
        rendered_plans.append(item)
    manifest["plans"] = rendered_plans
    rendered_tasks: list[dict[str, object]] = []
    for task in tree.tasks:
        rendered_tasks.append(
            {
                "id": task.task_id,
                "plan_id": task.plan_id,
                "plan_version": task.plan_version,
                "outcome": task.outcome,
                "dependencies": list(task.dependencies),
                "owned_files": list(task.owned_files),
                "owned_resources": list(task.owned_resources),
                "required_inputs": list(task.required_inputs),
                "output": task.output,
                "acceptance_check": task.acceptance_check,
                "source_requirement": task.source_requirement,
            }
        )
    manifest["tasks"] = rendered_tasks
    body = json.dumps(manifest, indent=2)
    return f"```gowork-plan\n{body}\n```\n"
