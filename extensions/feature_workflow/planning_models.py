"""The records a child planning thread is built from, and their bounds.

When a planning conversation splits, the new thread must be able to plan its
build without the parent conversation. The tempting shortcut — copy the parent
transcript — is exactly what this module exists to prevent: it costs a fortune
in context, it goes stale the moment a decision changes, and it carries whatever
was pasted into the parent, secrets included.

So a child gets records instead of a conversation:

* a `SplitIdentity` derived from the parent thread and the goal, so the same
  split request after a crash finds the same child rather than making a second
  one;
* a `ChildHandoff` with the goal, project and computer, only the locked
  decisions that bind this build, dependencies, ownership, restrictions and
  authority, expected output, and a link back to the parent;
* a `ChildRecord` holding state, thread, plan revision, and acknowledgements,
  small enough to write atomically and re-read after a restart.

Every text field is bounded, scanned for transcript and prompt-history markers,
and scanned for credentials. A field that fails is refused at construction, not
redacted later, because a record that was once written is a record that was
already persisted somewhere.

This module holds no state and performs no I/O. Persistence lives in the
planning registry, policy in the split policy, and Discord in its own adapter.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

PLANNING_MODELS_VERSION = 1

# Bounds. A handoff is a work order a person can read in a minute; these keep a
# generated one from becoming a document store, and keep the registry's atomic
# writes small.
MAX_GOAL_CHARS = 400
MAX_TEXT_CHARS = 600
MAX_IDENT_CHARS = 100
MAX_PATH_CHARS = 200
MAX_LINES = 8
MAX_DECISIONS = 20
MAX_DEPENDENCIES = 20
MAX_SCOPES = 20
MAX_PATHS = 20
MAX_RESTRICTIONS = 10
MAX_ACKS = 40
MAX_CHILDREN = 20

# Paths a child may never claim, whatever the plan said. A child that could
# write another worker's checkout or the approved plan would make every other
# ownership rule advisory.
PROTECTED_PATHS: tuple[str, ...] = (".git/", ".worktrees/", "openspec/")

# The fields a child planning brief must carry. The spec requires all of them;
# the test asserts the serialized keys are exactly this set, so dropping one is
# a failure rather than a quieter brief.
REQUIRED_HANDOFF_FIELDS: tuple[str, ...] = (
    "goal",
    "target",
    "decisions",
    "dependencies",
    "ownership",
    "restrictions",
    "authority",
    "expected_output",
    "parent",
)

# Two or more role-prefixed lines is a pasted conversation, not a summary. The
# explicit markers catch the tool-shaped variants of the same mistake.
_ROLE_LINE_RE = re.compile(
    r"^\s*(user|assistant|human|system|claude|codex)\s*[:>]", re.IGNORECASE | re.MULTILINE
)
_TRANSCRIPT_MARKER_RE = re.compile(
    r"<\s*/?\s*(transcript|conversation|messages|chat_history|prompt_history)|"
    r"\b(prompt|message|chat|conversation)\s+history\b",
    re.IGNORECASE,
)

# Credential shapes, not credential names: a decision may legitimately say "use
# the Porkbun key from the keys file"; it may not carry the key.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{12,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]\s*\S{8,})",
    re.IGNORECASE,
)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_WORD_RE = re.compile(r"[a-z0-9]+")


class PlanningModelError(Exception):
    """A planning record cannot be built safely, so it is not built at all."""


class PlanningState(StrEnum):
    """One state shared by the parent's view of a child and the child itself.

    `PROPOSED` and `CREATING` exist so intent is durable before a Discord thread
    exists: a crash between deciding to split and creating the thread must be
    recoverable without guessing whether a child was already made.
    """

    PROPOSED = "proposed"
    CREATING = "creating"
    PLANNING = "planning"
    AWAITING_ANSWER = "awaiting-answer"
    PLAN_READY = "plan-ready"
    DISPATCHED = "dispatched"
    INTEGRATED = "integrated"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


# A child in one of these still owns its declared scope; the parent may not hand
# the same paths to another build.
ACTIVE_PLANNING_STATES = frozenset(
    {
        PlanningState.PROPOSED,
        PlanningState.CREATING,
        PlanningState.PLANNING,
        PlanningState.AWAITING_ANSWER,
        PlanningState.PLAN_READY,
        PlanningState.DISPATCHED,
    }
)

TERMINAL_PLANNING_STATES = frozenset({PlanningState.INTEGRATED, PlanningState.CANCELLED})


class DependencyKind(StrEnum):
    """Why one build must wait for another, phrased so the parent can act on it."""

    ORDERING = "ordering"
    HARD = "hard"
    SHARED_SCOPE = "shared-scope"


def _text(label: str, raw: object, limit: int = MAX_TEXT_CHARS, *, required: bool = True) -> str:
    """Bounded, transcript-free, secret-free text, or a refusal naming the field."""
    if raw is None and not required:
        return ""
    if not isinstance(raw, str):
        raise PlanningModelError(f"{label} must be text, got {type(raw).__name__}")
    value = raw.strip()
    if not value:
        if required:
            raise PlanningModelError(f"{label} is required and cannot be empty")
        return ""
    if len(value) > limit:
        raise PlanningModelError(f"{label} is too long: {len(value)} characters (limit {limit})")
    if len(value.splitlines()) > MAX_LINES:
        raise PlanningModelError(
            f"{label} reads like a transcript: {len(value.splitlines())} lines (limit {MAX_LINES})"
        )
    if _TRANSCRIPT_MARKER_RE.search(value) or len(_ROLE_LINE_RE.findall(value)) >= 2:
        raise PlanningModelError(f"{label} contains a transcript or prompt history")
    if _SECRET_RE.search(value):
        raise PlanningModelError(f"{label} contains what looks like a secret")
    return value


def _identifier(label: str, raw: object) -> str:
    return _text(label, raw, MAX_IDENT_CHARS)


def _thread_id(label: str, raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PlanningModelError(f"{label} must be a Discord snowflake, got {raw!r}")
    if raw <= 0:
        raise PlanningModelError(f"{label} must be a positive Discord snowflake, got {raw}")
    return raw


def _timestamp(label: str, raw: object) -> str:
    """An ISO 8601 instant with an explicit offset; local-looking time is refused."""
    value = _text(label, raw, MAX_IDENT_CHARS)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PlanningModelError(f"{label} is not an ISO 8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise PlanningModelError(f"{label} is a timestamp without a time zone: {value}")
    return value


def _bounded(label: str, items: object, limit: int) -> tuple:
    if isinstance(items, str) or not isinstance(items, Iterable):
        raise PlanningModelError(f"{label} must be a sequence")
    values = tuple(items)
    if len(values) > limit:
        raise PlanningModelError(f"{label} has too many entries: {len(values)} (limit {limit})")
    return values


def _mapping(label: str, raw: object) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise PlanningModelError(f"{label} must be an object, got {type(raw).__name__}")
    return raw


def _require(label: str, data: Mapping[str, object], key: str) -> object:
    if key not in data:
        raise PlanningModelError(f"{label} is missing the required field: {key}")
    return data[key]


def _reject_unknown(label: str, data: Mapping[str, object], allowed: Sequence[str]) -> None:
    extra = sorted(set(data) - set(allowed))
    if extra:
        raise PlanningModelError(f"{label} has unexpected fields: {', '.join(extra)}")


def _owned_path(raw: object) -> str:
    value = _text("owned path", raw, MAX_PATH_CHARS)
    if "\\" in value:
        raise PlanningModelError(f"owned path is not POSIX: {value}")
    path = PurePosixPath(value)
    if value.startswith("/") or path.is_absolute():
        raise PlanningModelError(f"owned path is absolute: {value}")
    if value in {".", "./"} or any(part in {"..", "."} for part in path.parts):
        raise PlanningModelError(f"owned path escapes the project: {value}")
    for guard in PROTECTED_PATHS:
        if _paths_overlap(value, guard):
            raise PlanningModelError(f"owned path is protected: {value} ({guard})")
    return value


def _paths_overlap(first: str, second: str) -> bool:
    """True when two declared paths can name the same file.

    A trailing slash makes a directory explicit, but `sources` and
    `sources/permits.py` collide just as surely, so containment is checked both
    ways regardless of how the plan wrote it.
    """
    left, right = first.rstrip("/"), second.rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


@dataclass(frozen=True, slots=True)
class SplitIdentity:
    """The idempotency key for one split request: parent thread plus goal.

    Derived, never assigned, so the same request produces the same key on a
    fresh process. Recovery compares keys; equality here is what stops a restart
    from opening a second child for a build that already has one.
    """

    parent_thread_id: int
    slug: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parent_thread_id", _thread_id("parent_thread_id", self.parent_thread_id)
        )
        slug = _identifier("slug", self.slug)
        if not _SLUG_RE.match(slug):
            raise PlanningModelError(f"split slug must be lowercase and dashed: {slug}")
        object.__setattr__(self, "slug", slug)

    @property
    def key(self) -> str:
        return f"{self.parent_thread_id}:{self.slug}"

    @classmethod
    def from_goal(cls, parent_thread_id: int, goal: str) -> SplitIdentity:
        """A stable slug: readable words from the goal, plus a digest of it."""
        parent = _thread_id("parent_thread_id", parent_thread_id)
        text = _text("goal", goal, MAX_GOAL_CHARS)
        words = _WORD_RE.findall(text.lower())
        if not words:
            raise PlanningModelError(f"goal has no usable words for a split identity: {text}")
        digest = hashlib.sha256(" ".join(words).encode()).hexdigest()[:8]
        stem = "-".join(words)[:48].strip("-")
        return cls(parent_thread_id=parent, slug=f"{stem}-{digest}")

    @classmethod
    def from_key(cls, key: str) -> SplitIdentity:
        value = _identifier("split key", key)
        parent, _, slug = value.partition(":")
        if not parent.isdigit() or not slug:
            raise PlanningModelError(f"split key must be '<parent-thread-id>:<slug>': {value}")
        return cls(parent_thread_id=int(parent), slug=slug)


@dataclass(frozen=True, slots=True)
class ProjectTarget:
    """Where this build happens. The computer matters: plans are not portable."""

    project: str
    computer: str
    repo_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project", _identifier("project", self.project))
        object.__setattr__(self, "computer", _identifier("computer", self.computer))
        if self.repo_path is not None:
            object.__setattr__(
                self, "repo_path", _text("repo_path", self.repo_path, MAX_PATH_CHARS)
            )

    def to_dict(self) -> dict[str, object]:
        return {"project": self.project, "computer": self.computer, "repo_path": self.repo_path}

    @classmethod
    def from_dict(cls, data: object) -> ProjectTarget:
        fields = _mapping("target", data)
        _reject_unknown("target", fields, ("project", "computer", "repo_path"))
        repo_path = fields.get("repo_path")
        return cls(
            project=str(_require("target", fields, "project")),
            computer=str(_require("target", fields, "computer")),
            repo_path=None if repo_path is None else str(repo_path),
        )


@dataclass(frozen=True, slots=True)
class ParentLink:
    """How a child reaches the thread that still coordinates it."""

    thread_id: int
    url: str
    plan_owner: str
    channel_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "thread_id", _thread_id("parent thread_id", self.thread_id))
        url = _text("parent url", self.url, MAX_PATH_CHARS)
        if not url.startswith("https://"):
            raise PlanningModelError(f"parent url must be an https link: {url}")
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "plan_owner", _identifier("plan_owner", self.plan_owner))
        if self.channel_id is not None:
            object.__setattr__(self, "channel_id", _thread_id("channel_id", self.channel_id))

    def sibling_url(self, thread_id: int) -> str:
        """The link to a child thread beside the parent, same guild and channel."""
        return f"{self.url.rsplit('/', 1)[0]}/{_thread_id('thread_id', thread_id)}"

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "url": self.url,
            "plan_owner": self.plan_owner,
            "channel_id": self.channel_id,
        }

    @classmethod
    def from_dict(cls, data: object) -> ParentLink:
        fields = _mapping("parent", data)
        _reject_unknown("parent", fields, ("thread_id", "url", "plan_owner", "channel_id"))
        channel_id = fields.get("channel_id")
        return cls(
            thread_id=int(_require("parent", fields, "thread_id")),  # type: ignore[arg-type]
            url=str(_require("parent", fields, "url")),
            plan_owner=str(_require("parent", fields, "plan_owner")),
            channel_id=None if channel_id is None else int(channel_id),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class LockedDecision:
    """One decision the child must honor, carried with the revision that set it.

    The revision is what makes a later change reportable: the parent can say
    which decision moved and from where, instead of resending the brief.
    """

    id: str
    statement: str
    revision: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier("decision id", self.id))
        object.__setattr__(self, "statement", _text("decision statement", self.statement))
        object.__setattr__(self, "revision", _identifier("decision revision", self.revision))
        object.__setattr__(self, "source", _identifier("decision source", self.source))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "statement": self.statement,
            "revision": self.revision,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: object) -> LockedDecision:
        fields = _mapping("decision", data)
        _reject_unknown("decision", fields, ("id", "statement", "revision", "source"))
        return cls(
            id=str(_require("decision", fields, "id")),
            statement=str(_require("decision", fields, "statement")),
            revision=str(_require("decision", fields, "revision")),
            source=str(_require("decision", fields, "source")),
        )


@dataclass(frozen=True, slots=True)
class DecisionAck:
    """A child's confirmation that it has the current form of one decision."""

    decision_id: str
    revision: str
    child_thread_id: int
    acknowledged_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _identifier("ack decision_id", self.decision_id))
        object.__setattr__(self, "revision", _identifier("ack revision", self.revision))
        object.__setattr__(
            self, "child_thread_id", _thread_id("ack child_thread_id", self.child_thread_id)
        )
        object.__setattr__(
            self, "acknowledged_at", _timestamp("ack acknowledged_at", self.acknowledged_at)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "revision": self.revision,
            "child_thread_id": self.child_thread_id,
            "acknowledged_at": self.acknowledged_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> DecisionAck:
        fields = _mapping("ack", data)
        allowed = ("decision_id", "revision", "child_thread_id", "acknowledged_at")
        _reject_unknown("ack", fields, allowed)
        return cls(
            decision_id=str(_require("ack", fields, "decision_id")),
            revision=str(_require("ack", fields, "revision")),
            child_thread_id=int(_require("ack", fields, "child_thread_id")),  # type: ignore[arg-type]
            acknowledged_at=str(_require("ack", fields, "acknowledged_at")),
        )


@dataclass(frozen=True, slots=True)
class PlanDependency:
    """One build this child must wait for, and the reason a person can check."""

    on: str
    kind: DependencyKind
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "on", _identifier("dependency target", self.on))
        try:
            object.__setattr__(self, "kind", DependencyKind(self.kind))
        except ValueError as exc:
            raise PlanningModelError(f"unknown dependency kind: {self.kind!r}") from exc
        object.__setattr__(self, "reason", _text("dependency reason", self.reason))

    def to_dict(self) -> dict[str, object]:
        return {"on": self.on, "kind": self.kind.value, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: object) -> PlanDependency:
        fields = _mapping("dependency", data)
        _reject_unknown("dependency", fields, ("on", "kind", "reason"))
        return cls(
            on=str(_require("dependency", fields, "on")),
            kind=DependencyKind(str(_require("dependency", fields, "kind"))),
            reason=str(_require("dependency", fields, "reason")),
        )


@dataclass(frozen=True, slots=True)
class OwnedScope:
    """What one build may write, so the parent can see an overlap before dispatch.

    Planning is read-only, so a read-only scope never conflicts; two writable
    scopes over the same project and the same paths are the case the parent must
    resolve with an ordering dependency or a single owner.
    """

    project: str
    paths: tuple[str, ...]
    owner: str
    writable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "project", _identifier("scope project", self.project))
        paths = _bounded("scope paths", self.paths, MAX_PATHS)
        if not paths:
            raise PlanningModelError("an owned scope must name at least one path")
        object.__setattr__(self, "paths", tuple(_owned_path(path) for path in paths))
        object.__setattr__(self, "owner", _identifier("scope owner", self.owner))
        if not isinstance(self.writable, bool):
            raise PlanningModelError("scope writable must be true or false")

    def conflicts_with(self, other: OwnedScope) -> bool:
        if not (self.writable and other.writable) or self.project != other.project:
            return False
        if self.owner == other.owner:
            return False
        return any(_paths_overlap(mine, theirs) for mine in self.paths for theirs in other.paths)

    def to_dict(self) -> dict[str, object]:
        return {
            "project": self.project,
            "paths": list(self.paths),
            "owner": self.owner,
            "writable": self.writable,
        }

    @classmethod
    def from_dict(cls, data: object) -> OwnedScope:
        fields = _mapping("scope", data)
        _reject_unknown("scope", fields, ("project", "paths", "owner", "writable"))
        return cls(
            project=str(_require("scope", fields, "project")),
            paths=tuple(
                str(path) for path in _bounded("scope paths", fields.get("paths", ()), MAX_PATHS)
            ),
            owner=str(_require("scope", fields, "owner")),
            writable=bool(fields.get("writable", True)),
        )


@dataclass(frozen=True, slots=True)
class ChildHandoff:
    """Everything a fresh planning thread needs, and nothing it was not given.

    This is the whole context a child starts with. It must therefore carry every
    required field — the spec's list is `REQUIRED_HANDOFF_FIELDS` and the
    serialized keys are exactly that set — while remaining small enough to post
    as one message.
    """

    goal: str
    target: ProjectTarget
    decisions: tuple[LockedDecision, ...]
    dependencies: tuple[PlanDependency, ...]
    ownership: tuple[OwnedScope, ...]
    restrictions: tuple[str, ...]
    authority: str
    expected_output: str
    parent: ParentLink

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _text("goal", self.goal, MAX_GOAL_CHARS))
        if not isinstance(self.target, ProjectTarget):
            raise PlanningModelError("target must be a ProjectTarget")
        if not isinstance(self.parent, ParentLink):
            raise PlanningModelError("parent must be a ParentLink")
        object.__setattr__(self, "decisions", _bounded("decisions", self.decisions, MAX_DECISIONS))
        object.__setattr__(
            self, "dependencies", _bounded("dependencies", self.dependencies, MAX_DEPENDENCIES)
        )
        object.__setattr__(self, "ownership", _bounded("ownership", self.ownership, MAX_SCOPES))
        restrictions = _bounded("restrictions", self.restrictions, MAX_RESTRICTIONS)
        object.__setattr__(
            self, "restrictions", tuple(_text("restriction", item) for item in restrictions)
        )
        object.__setattr__(self, "authority", _text("authority", self.authority))
        object.__setattr__(self, "expected_output", _text("expected_output", self.expected_output))
        for label, values, kind in (
            ("decisions", self.decisions, LockedDecision),
            ("dependencies", self.dependencies, PlanDependency),
            ("ownership", self.ownership, OwnedScope),
        ):
            for item in values:
                if not isinstance(item, kind):
                    raise PlanningModelError(f"{label} must contain {kind.__name__} records")

    def decision(self, decision_id: str) -> LockedDecision | None:
        return next((item for item in self.decisions if item.id == decision_id), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "goal": self.goal,
            "target": self.target.to_dict(),
            "decisions": [item.to_dict() for item in self.decisions],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "ownership": [item.to_dict() for item in self.ownership],
            "restrictions": list(self.restrictions),
            "authority": self.authority,
            "expected_output": self.expected_output,
            "parent": self.parent.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: object) -> ChildHandoff:
        fields = _mapping("handoff", data)
        _reject_unknown("handoff", fields, REQUIRED_HANDOFF_FIELDS)
        for name in REQUIRED_HANDOFF_FIELDS:
            _require("handoff", fields, name)
        return cls(
            goal=str(fields["goal"]),
            target=ProjectTarget.from_dict(fields["target"]),
            decisions=tuple(
                LockedDecision.from_dict(item)
                for item in _bounded("decisions", fields["decisions"], MAX_DECISIONS)
            ),
            dependencies=tuple(
                PlanDependency.from_dict(item)
                for item in _bounded("dependencies", fields["dependencies"], MAX_DEPENDENCIES)
            ),
            ownership=tuple(
                OwnedScope.from_dict(item)
                for item in _bounded("ownership", fields["ownership"], MAX_SCOPES)
            ),
            restrictions=tuple(
                str(item)
                for item in _bounded("restrictions", fields["restrictions"], MAX_RESTRICTIONS)
            ),
            authority=str(fields["authority"]),
            expected_output=str(fields["expected_output"]),
            parent=ParentLink.from_dict(fields["parent"]),
        )


@dataclass(frozen=True, slots=True)
class ChildRecord:
    """One child build, as the parent and the registry both see it.

    Transitions return new records rather than mutating this one: the registry
    writes a whole record atomically, and a half-applied transition is the one
    failure mode recovery cannot reason about.
    """

    identity: SplitIdentity
    handoff: ChildHandoff
    state: PlanningState
    created_at: str
    updated_at: str
    thread_id: int | None = None
    plan_revision: str | None = None
    blocker: str | None = None
    acks: tuple[DecisionAck, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, SplitIdentity):
            raise PlanningModelError("identity must be a SplitIdentity")
        if not isinstance(self.handoff, ChildHandoff):
            raise PlanningModelError("handoff must be a ChildHandoff")
        try:
            object.__setattr__(self, "state", PlanningState(self.state))
        except ValueError as exc:
            raise PlanningModelError(f"unknown planning state: {self.state!r}") from exc
        object.__setattr__(self, "created_at", _timestamp("created_at", self.created_at))
        object.__setattr__(self, "updated_at", _timestamp("updated_at", self.updated_at))
        if self.thread_id is not None:
            object.__setattr__(self, "thread_id", _thread_id("thread_id", self.thread_id))
        if self.plan_revision is not None:
            object.__setattr__(
                self, "plan_revision", _identifier("plan_revision", self.plan_revision)
            )
        if self.blocker is not None:
            object.__setattr__(self, "blocker", _text("blocker", self.blocker))
        if self.state is PlanningState.BLOCKED and not self.blocker:
            raise PlanningModelError("a blocked child must name its blocker")
        acks = _bounded("acks", self.acks, MAX_ACKS)
        for ack in acks:
            if not isinstance(ack, DecisionAck):
                raise PlanningModelError("acks must contain DecisionAck records")
        object.__setattr__(self, "acks", acks)

    @property
    def parent_thread_id(self) -> int:
        return self.handoff.parent.thread_id

    @property
    def url(self) -> str | None:
        """The child's own Discord link, once it has a thread."""
        if self.thread_id is None:
            return None
        return self.handoff.parent.sibling_url(self.thread_id)

    def with_state(
        self,
        state: PlanningState,
        *,
        at: str,
        blocker: str | None = None,
        plan_revision: str | None = None,
    ) -> ChildRecord:
        return replace(
            self,
            state=state,
            updated_at=at,
            blocker=blocker if state is PlanningState.BLOCKED else None,
            plan_revision=plan_revision or self.plan_revision,
        )

    def with_thread(self, thread_id: int, *, at: str) -> ChildRecord:
        """Record the created thread once; a second one would be a duplicate child."""
        if self.thread_id is not None:
            raise PlanningModelError(
                f"child {self.identity.key} already has thread {self.thread_id}"
            )
        return replace(self, thread_id=thread_id, state=PlanningState.CREATING, updated_at=at)

    def acknowledge(self, ack: DecisionAck) -> ChildRecord:
        """Idempotent: re-delivering the same acknowledgement changes nothing."""
        if not isinstance(ack, DecisionAck):
            raise PlanningModelError("acknowledge expects a DecisionAck")
        if self.thread_id is not None and ack.child_thread_id != self.thread_id:
            raise PlanningModelError(
                f"acknowledgement is from thread {ack.child_thread_id}, not {self.thread_id}"
            )
        if any(
            item.decision_id == ack.decision_id and item.revision == ack.revision
            for item in self.acks
        ):
            return self
        return replace(self, acks=self.acks + (ack,), updated_at=ack.acknowledged_at)

    def has_acknowledged(self, decision_id: str, revision: str) -> bool:
        return any(
            item.decision_id == decision_id and item.revision == revision for item in self.acks
        )

    def summary(self) -> ChildSummary:
        return ChildSummary(
            key=self.identity.key,
            goal=self.handoff.goal,
            state=self.state,
            thread_id=self.thread_id,
            url=self.url,
            project=self.handoff.target.project,
            computer=self.handoff.target.computer,
            dependencies=tuple(item.on for item in self.handoff.dependencies),
            blocker=self.blocker,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "version": PLANNING_MODELS_VERSION,
            "identity": self.identity.key,
            "handoff": self.handoff.to_dict(),
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "thread_id": self.thread_id,
            "plan_revision": self.plan_revision,
            "blocker": self.blocker,
            "acks": [ack.to_dict() for ack in self.acks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: object) -> ChildRecord:
        fields = _mapping("child record", data)
        allowed = (
            "version",
            "identity",
            "handoff",
            "state",
            "created_at",
            "updated_at",
            "thread_id",
            "plan_revision",
            "blocker",
            "acks",
        )
        _reject_unknown("child record", fields, allowed)
        version = fields.get("version", PLANNING_MODELS_VERSION)
        if version != PLANNING_MODELS_VERSION:
            raise PlanningModelError(f"unsupported planning record version: {version!r}")
        thread_id = fields.get("thread_id")
        plan_revision = fields.get("plan_revision")
        blocker = fields.get("blocker")
        return cls(
            identity=SplitIdentity.from_key(str(_require("child record", fields, "identity"))),
            handoff=ChildHandoff.from_dict(_require("child record", fields, "handoff")),
            state=PlanningState(str(_require("child record", fields, "state"))),
            created_at=str(_require("child record", fields, "created_at")),
            updated_at=str(_require("child record", fields, "updated_at")),
            thread_id=None if thread_id is None else int(thread_id),  # type: ignore[arg-type]
            plan_revision=None if plan_revision is None else str(plan_revision),
            blocker=None if blocker is None else str(blocker),
            acks=tuple(
                DecisionAck.from_dict(item)
                for item in _bounded("acks", fields.get("acks", ()), MAX_ACKS)
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> ChildRecord:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanningModelError(f"child record is not valid JSON: {exc}") from exc
        return cls.from_dict(data)


@dataclass(frozen=True, slots=True)
class ChildSummary:
    """One row of the parent's dashboard: link, goal, state, dependencies, blocker."""

    key: str
    goal: str
    state: PlanningState
    thread_id: int | None
    url: str | None
    project: str
    computer: str
    dependencies: tuple[str, ...]
    blocker: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "goal": self.goal,
            "state": self.state.value,
            "thread_id": self.thread_id,
            "url": self.url,
            "project": self.project,
            "computer": self.computer,
            "dependencies": list(self.dependencies),
            "blocker": self.blocker,
        }


@dataclass(frozen=True, slots=True)
class ParentSummary:
    """What the coordinating thread shows while its children are open."""

    parent_thread_id: int
    children: tuple[ChildSummary, ...]

    @property
    def active(self) -> tuple[ChildSummary, ...]:
        return tuple(row for row in self.children if row.state in ACTIVE_PLANNING_STATES)

    @property
    def blocked(self) -> tuple[ChildSummary, ...]:
        return tuple(row for row in self.children if row.state is PlanningState.BLOCKED)

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_thread_id": self.parent_thread_id,
            "children": [row.to_dict() for row in self.children],
        }


def summarize_children(parent_thread_id: int, records: Iterable[ChildRecord]) -> ParentSummary:
    """The parent's view of its children, refusing anything that is not its own."""
    parent = _thread_id("parent_thread_id", parent_thread_id)
    rows: list[ChildSummary] = []
    seen: set[str] = set()
    for record in _bounded("children", records, MAX_CHILDREN):
        if not isinstance(record, ChildRecord):
            raise PlanningModelError("children must be ChildRecord values")
        if record.parent_thread_id != parent:
            raise PlanningModelError(
                f"child {record.identity.key} belongs to parent {record.parent_thread_id}"
            )
        if record.identity.key in seen:
            raise PlanningModelError(f"duplicate child identity: {record.identity.key}")
        seen.add(record.identity.key)
        rows.append(record.summary())
    return ParentSummary(parent_thread_id=parent, children=tuple(rows))


def conflicting_scopes(records: Iterable[ChildRecord]) -> tuple[tuple[str, str, str], ...]:
    """Every writable overlap between two children, as (child, child, path).

    The parent needs an ordering dependency or a single owner for each of these
    before either build is dispatched.
    """
    scopes = [
        (record.identity.key, scope)
        for record in records
        for scope in record.handoff.ownership
        if scope.writable
    ]
    found: list[tuple[str, str, str]] = []
    for index, (left_key, left) in enumerate(scopes):
        for right_key, right in scopes[index + 1 :]:
            if left_key == right_key or left.project != right.project:
                continue
            for mine in left.paths:
                for theirs in right.paths:
                    if _paths_overlap(mine, theirs):
                        found.append((left_key, right_key, mine))
    return tuple(found)
