"""Identity model and parser for legacy and multi-plan Go Work documents.

New plans may contain one ``gowork-plan`` JSON fence describing a rooted plan
tree.  Keeping identity metadata in a dedicated fence leaves the surrounding
Markdown readable by the existing checkbox runner.  A document without the
fence is a supported legacy single-plan tree with a deterministic identity.
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
class PlanTree:
    """A validated rooted tree of plans, potentially spanning projects."""

    plans: tuple[PlanIdentity, ...]
    schema_version: int = SCHEMA_VERSION
    is_legacy: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise PlanValidationError(
                f"unsupported gowork-plan schema_version {self.schema_version!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        _validate_tree(self.plans)

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


def _project_root(source_path: Path) -> Path:
    """Find the containing repository without invoking git; otherwise use the plan folder."""
    for candidate in (source_path.parent, *source_path.parents):
        if (candidate / ".git").exists():
            return candidate.resolve(strict=False)
    return source_path.parent.resolve(strict=False)


def _legacy_tree(source_path: Path) -> PlanTree:
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
    return PlanTree(plans=(plan,), is_legacy=True)


def parse_plan_tree(text: str, *, source_path: Path) -> PlanTree:
    """Parse identity metadata from a Markdown plan.

    A plan without a manifest remains valid as a deterministic, version-one,
    single-plan tree.  Relative project paths in a manifest are resolved from
    the Markdown file's directory and stored canonically.
    """
    matches = list(_MANIFEST_RE.finditer(text))
    if not matches:
        return _legacy_tree(source_path)
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
    base_dir = source_path.resolve(strict=False).parent
    plans = tuple(_parse_identity(item, index, base_dir) for index, item in enumerate(raw_plans))
    return PlanTree(plans=plans, schema_version=schema_version)


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
    body = json.dumps(manifest, indent=2)
    return f"```gowork-plan\n{body}\n```\n"
