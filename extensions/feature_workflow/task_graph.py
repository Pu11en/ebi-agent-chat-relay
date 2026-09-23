"""The versioned task graph that decides what may run at the same time.

A `/gowork` plan is Markdown a human edits, but parallel safety cannot rest on
prose or on a model's reading of it. This module turns a plan into an immutable
graph whose very existence is the guarantee the scheduler relies on:

* every task has a stable ID, versioned by the plan revision it came from, so a
  dispatch recorded before a crash can never be matched to an edited plan;
* dependencies are closed and acyclic, named explicitly when they are not;
* owned paths are relative POSIX paths inside the repository, never the plan,
  the Git directory, or another session's worktree;
* no two tasks that may run at the same time own the same file or a directory
  containing it — the one property no model output can prove.

Plans without that metadata are not guessed at. They become a legacy graph: the
same tasks chained one after another, which is exactly today's sequential
behavior and never claims parallel safety it cannot demonstrate.

This module is deliberately free of Git, Discord, and runtime state. It reads
text and answers questions about it; dispatch, verification, and integration
live in their own modules.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

TASK_GRAPH_VERSION = 1

# Bounds. A plan is a work order, not a document store: these keep one malformed
# or generated plan from turning into a quadratic ownership check or a task list
# no human reviewed.
MAX_TASKS = 200
MAX_OWNED_PATHS = 20
MAX_PATH_CHARS = 200
MAX_TITLE_CHARS = 1000
MAX_DEPENDENCIES = 20

# Paths no task may own, whatever the plan says. The plan itself is added by
# `parse_task_plan`; a worker that could rewrite its own instructions or another
# worker's checkout would make every other check advisory.
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (".git/", ".worktrees/", "openspec/")

_CHECKBOX_RE = re.compile(r"^(?P<indent>\s*)[-*]\s+\[(?P<mark>[ xX])\]\s*(?P<body>.*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
_DIRECTIVE_RE = re.compile(r"^(?P<key>Check|Try|Open):\s*(?P<value>.+?)\s*$")
_ID_PREFIX_RE = re.compile(r"^(?P<id>\d+(?:\.\d+)*)[.)]?\s+(?P<rest>.*)$", re.DOTALL)
_METADATA_RE = re.compile(r"^\*\*(?P<meta>.+?)\*\*\s*(?P<title>.*)$", re.DOTALL)
_TASK_ID_RE = re.compile(r"^\d+(?:\.\d+)*$")

_NONE_VALUES = frozenset({"none", "-", "n/a", "no dependencies"})
_NO_PATH_VALUES = frozenset({"none", "-", "n/a", "no source files", "no files"})
_METADATA_FLAGS = frozenset({"integration owner only", "integration only"})
_METADATA_KEYS = frozenset({"depends on", "depends", "owns", "check"})


class TaskGraphError(Exception):
    """A plan cannot be scheduled safely; no task from it may be dispatched."""


def _strip_code(value: str) -> str:
    return value.strip().strip("`").strip()


def _overlaps(first: str, second: str) -> bool:
    """True when two owned paths can name the same file.

    A trailing slash makes a directory scope explicit, but `pkg/nested` and
    `pkg/nested/a.py` collide just as surely, so containment is checked both
    ways regardless of how the plan wrote it.
    """
    left, right = first.rstrip("/"), second.rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _validate_path(task_id: str, raw: str, protected: Sequence[str]) -> str:
    value = raw.strip()
    if not value:
        raise TaskGraphError(f"Task {task_id} declares an empty owned path")
    if len(value) > MAX_PATH_CHARS:
        raise TaskGraphError(f"Task {task_id} owns a path that is too long: {value[:60]}...")
    if "\\" in value:
        raise TaskGraphError(f"Task {task_id} owns a non-POSIX path: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise TaskGraphError(f"Task {task_id} owns an absolute path: {value}")
    if value in {".", "./"} or any(part in {"..", "."} for part in path.parts):
        raise TaskGraphError(f"Task {task_id} owns a path that escapes the repository: {value}")
    for guard in protected:
        if _overlaps(value, guard):
            raise TaskGraphError(f"Task {task_id} owns a protected path: {value} ({guard})")
    return value


@dataclass(frozen=True, slots=True)
class TaskNode:
    """One checkbox, with everything the scheduler needs and nothing else."""

    id: str
    uid: str
    title: str
    section: str
    depends_on: tuple[str, ...]
    owned_paths: tuple[str, ...]
    check: str | None
    integration_only: bool
    done: bool
    line: int


@dataclass(frozen=True, slots=True)
class TaskGraph:
    """An immutable, validated plan. Constructing one is the safety claim."""

    version: int
    revision: str
    legacy: bool
    tasks: tuple[TaskNode, ...]
    plan_check: str | None = None
    try_command: str | None = None
    open_command: str | None = None
    plan_path: str | None = None
    _index: dict[str, TaskNode] = field(default_factory=dict, repr=False, compare=False)
    _ancestors: dict[str, frozenset[str]] = field(default_factory=dict, repr=False, compare=False)

    def __getitem__(self, task_id: str) -> TaskNode:
        try:
            return self._index[task_id]
        except KeyError as exc:
            raise TaskGraphError(f"Unknown task: {task_id}") from exc

    def __contains__(self, task_id: str) -> bool:
        return task_id in self._index

    def __iter__(self):
        return iter(self.tasks)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.tasks)

    def open_tasks(self) -> tuple[TaskNode, ...]:
        return tuple(task for task in self.tasks if not task.done)

    def dependencies_of(self, task_id: str) -> frozenset[str]:
        """Every task that must be integrated before this one starts."""
        self[task_id]
        return self._ancestors[task_id]

    def dependents_of(self, task_id: str) -> frozenset[str]:
        self[task_id]
        return frozenset(other.id for other in self.tasks if task_id in self._ancestors[other.id])

    def may_run_together(self, first: str, second: str) -> bool:
        """False when either task must wait for the other, directly or not."""
        if first == second:
            return False
        return second not in self.dependencies_of(first) and first not in self.dependencies_of(
            second
        )

    def check_for(self, task_id: str) -> str | None:
        """The focused check a worker must report, or the plan-level fallback."""
        return self[task_id].check or self.plan_check


@dataclass(slots=True)
class _Item:
    """One checkbox plus the wrapped lines that belong to it."""

    line: int
    section: str
    done: bool
    body: str

    def append(self, text: str) -> None:
        self.body = f"{self.body} {text}".strip()


def _logical_items(source: str) -> tuple[list[_Item], dict[str, str]]:
    """Fold wrapped Markdown into one record per checkbox, plus plan directives."""
    items: list[_Item] = []
    directives: dict[str, str] = {}
    section = ""
    current: _Item | None = None

    for number, raw in enumerate(source.splitlines(), start=1):
        line = raw.rstrip()
        checkbox = _CHECKBOX_RE.match(line)
        if checkbox:
            current = _Item(number, section, checkbox["mark"] in "xX", checkbox["body"].strip())
            items.append(current)
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            current, section = None, heading["title"]
            continue
        if current is not None and line.startswith((" ", "\t")) and line.strip():
            current.append(line.strip())
            continue
        current = None
        directive = _DIRECTIVE_RE.match(line)
        if directive:
            directives.setdefault(directive["key"].lower(), _strip_code(directive["value"]))
    return items, directives


def _parse_metadata(task_id: str, meta: str) -> tuple[tuple[str, ...], list[str], str | None, bool]:
    depends: tuple[str, ...] = ()
    owns: list[str] = []
    check: str | None = None
    integration_only = False
    seen: set[str] = set()

    for segment in meta.split(";"):
        text = segment.strip().rstrip(".").strip()
        if not text:
            continue
        key, _, value = text.partition(":")
        key = key.strip().lower()
        if not _:
            if key not in _METADATA_FLAGS:
                raise TaskGraphError(f"Task {task_id} has an unknown metadata flag: {key}")
            integration_only = True
            continue
        if key not in _METADATA_KEYS:
            raise TaskGraphError(f"Task {task_id} has an unknown metadata key: {key}")
        if key in seen:
            raise TaskGraphError(f"Task {task_id} repeats the metadata key: {key}")
        seen.add(key)
        value = value.strip()
        if key in {"depends on", "depends"}:
            if value.strip("`").strip().lower() not in _NONE_VALUES:
                depends = tuple(_strip_code(part) for part in value.split(",") if part.strip())
        elif key == "owns":
            if value.strip("`").strip().lower() not in _NO_PATH_VALUES:
                owns = [_strip_code(part) for part in value.split(",") if part.strip()]
        else:
            check = _strip_code(value) or None
    return depends, owns, check, integration_only


def _cycle_path(nodes: dict[str, tuple[str, ...]]) -> list[str] | None:
    state: dict[str, int] = dict.fromkeys(nodes, 0)
    stack: list[str] = []

    def visit(name: str) -> list[str] | None:
        state[name] = 1
        stack.append(name)
        for dependency in nodes[name]:
            if state[dependency] == 1:
                return stack[stack.index(dependency) :] + [dependency]
            if state[dependency] == 0:
                found = visit(dependency)
                if found:
                    return found
        stack.pop()
        state[name] = 2
        return None

    for name in nodes:
        if state[name] == 0:
            found = visit(name)
            if found:
                return found
    return None


def _ancestor_map(nodes: dict[str, tuple[str, ...]]) -> dict[str, frozenset[str]]:
    resolved: dict[str, frozenset[str]] = {}

    def resolve(name: str) -> frozenset[str]:
        if name not in resolved:
            gathered: set[str] = set()
            for dependency in nodes[name]:
                gathered.add(dependency)
                gathered |= resolve(dependency)
            resolved[name] = frozenset(gathered)
        return resolved[name]

    return {name: resolve(name) for name in nodes}


def plan_revision(source: str) -> str:
    """A stable identity for the plan text, insensitive to trailing whitespace."""
    normalized = "\n".join(line.rstrip() for line in source.splitlines()).strip("\n")
    return hashlib.sha256(f"v{TASK_GRAPH_VERSION}\n{normalized}".encode()).hexdigest()


def parse_task_graph(
    source: str,
    *,
    protected_paths: Iterable[str] = (),
    plan_path: str | None = None,
) -> TaskGraph:
    """Parse a `/gowork` plan into a validated graph, or refuse to.

    Raises `TaskGraphError` — never a partial graph — when the plan mixes task
    styles, names an unknown or circular dependency, owns a path outside the
    repository or inside protected state, or lets two concurrent tasks write the
    same file.
    """
    items, directives = _logical_items(source)
    if not items:
        raise TaskGraphError("Plan contains no tasks")
    if len(items) > MAX_TASKS:
        raise TaskGraphError(f"Plan declares too many tasks: {len(items)} > {MAX_TASKS}")

    protected = [*DEFAULT_PROTECTED_PATHS, *(p.strip() for p in protected_paths if p.strip())]
    if plan_path:
        protected.append(plan_path)

    parsed: list[dict] = []
    structured_count = 0
    for position, item in enumerate(items, start=1):
        prefix = _ID_PREFIX_RE.match(item.body)
        task_id = prefix["id"] if prefix else None
        rest = (prefix["rest"] if prefix else item.body).strip()
        metadata = _METADATA_RE.match(rest)
        if metadata:
            structured_count += 1
            if task_id is None:
                raise TaskGraphError(f"Structured task on line {item.line} has no task ID")
            depends, owns, check, integration_only = _parse_metadata(task_id, metadata["meta"])
            title = metadata["title"].strip()
        else:
            depends, owns, check, integration_only = (), [], None, False
            title = rest
        parsed.append(
            {
                "id": task_id or f"t{position}",
                "line": item.line,
                "section": item.section,
                "done": item.done,
                "title": title,
                "depends": depends,
                "owns": owns,
                "check": check,
                "integration_only": integration_only,
                "structured": metadata is not None,
            }
        )

    if structured_count and structured_count != len(parsed):
        plain = next(entry for entry in parsed if not entry["structured"])
        raise TaskGraphError(
            "Plan mixes tasks with and without parallel metadata; "
            f"line {plain['line']} has no dependency or ownership declaration"
        )
    legacy = structured_count == 0

    seen: set[str] = set()
    for entry in parsed:
        task_id = entry["id"]
        if task_id in seen:
            raise TaskGraphError(f"Duplicate task ID: {task_id}")
        seen.add(task_id)
        if not _TASK_ID_RE.fullmatch(task_id) and not legacy:
            raise TaskGraphError(f"Task ID must be a dotted number: {task_id}")
        if not entry["title"]:
            raise TaskGraphError(f"Task {task_id} has no description")
        if len(entry["title"]) > MAX_TITLE_CHARS:
            raise TaskGraphError(f"Task {task_id} has an over-long description")

    if legacy:
        # No metadata means no proof of independence, so the only safe reading is
        # the one `/gowork` already uses: one task at a time, in written order.
        previous: str | None = None
        for entry in parsed:
            entry["depends"] = () if previous is None else (previous,)
            previous = entry["id"]

    nodes: dict[str, tuple[str, ...]] = {}
    for entry in parsed:
        depends = tuple(dict.fromkeys(entry["depends"]))
        if len(depends) > MAX_DEPENDENCIES:
            raise TaskGraphError(f"Task {entry['id']} declares too many dependencies")
        for dependency in depends:
            if dependency == entry["id"]:
                raise TaskGraphError(f"Task {entry['id']} depends on itself")
            if dependency not in seen:
                raise TaskGraphError(f"Task {entry['id']} depends on unknown task {dependency}")
        entry["depends"] = depends
        nodes[entry["id"]] = depends

    cycle = _cycle_path(nodes)
    if cycle:
        raise TaskGraphError("Task dependencies contain a cycle: " + " -> ".join(cycle))
    ancestors = _ancestor_map(nodes)

    for entry in parsed:
        task_id = entry["id"]
        if len(entry["owns"]) > MAX_OWNED_PATHS:
            raise TaskGraphError(f"Task {task_id} declares too many owned paths")
        owned: list[str] = []
        for raw in entry["owns"]:
            path = _validate_path(task_id, raw, protected)
            for existing in owned:
                if _overlaps(path, existing):
                    raise TaskGraphError(f"Task {task_id} declares overlapping paths: {path}")
            owned.append(path)
        entry["owns"] = tuple(owned)
        if not legacy and not (entry["check"] or directives.get("check")):
            raise TaskGraphError(f"Task {task_id} has no focused check and the plan declares none")

    for index, entry in enumerate(parsed):
        for other in parsed[index + 1 :]:
            if entry["id"] in ancestors[other["id"]] or other["id"] in ancestors[entry["id"]]:
                continue
            for mine in entry["owns"]:
                for theirs in other["owns"]:
                    if _overlaps(mine, theirs):
                        raise TaskGraphError(
                            f"Tasks {entry['id']} and {other['id']} may run together but both "
                            f"own {mine} / {theirs}"
                        )

    revision = plan_revision(source)
    tasks = tuple(
        TaskNode(
            id=entry["id"],
            uid=f"{revision[:12]}:{entry['id']}",
            title=entry["title"],
            section=entry["section"],
            depends_on=entry["depends"],
            owned_paths=entry["owns"],
            check=entry["check"],
            integration_only=entry["integration_only"],
            done=entry["done"],
            line=entry["line"],
        )
        for entry in parsed
    )
    return TaskGraph(
        version=TASK_GRAPH_VERSION,
        revision=revision,
        legacy=legacy,
        tasks=tasks,
        plan_check=directives.get("check"),
        try_command=directives.get("try"),
        open_command=directives.get("open"),
        plan_path=plan_path,
        _index={task.id: task for task in tasks},
        _ancestors=ancestors,
    )


def parse_task_plan(
    path: Path | str,
    *,
    repo_root: Path | str | None = None,
    protected_paths: Iterable[str] = (),
) -> TaskGraph:
    """Read a plan file and parse it, protecting the plan from its own tasks."""
    plan = Path(path)
    relative: str | None = None
    if repo_root is not None:
        try:
            relative = plan.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError as exc:
            raise TaskGraphError("Plan file must live inside the repository") from exc
    return parse_task_graph(
        plan.read_text(encoding="utf-8"),
        protected_paths=protected_paths,
        plan_path=relative,
    )
