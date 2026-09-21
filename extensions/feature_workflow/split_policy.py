"""Whether a proposed build gets its own planning thread: a rule, not a guess.

During planning the model may notice that a conversation has started describing
a second build. It is allowed to *propose* that as a structured candidate — a
goal, acceptance criteria, writable scope, and how the candidate relates to the
build already being planned. It is not allowed to decide. The decision is made
here, from those fields alone, so the same candidate produces the same verdict
in this process or the next one, and a person can read why.

The spec names the test of independence: a distinct goal that can be planned
and delivered without changing the active build's acceptance criteria and
without the same writable files. Each of those is one field, and each field has
three states — clearly satisfied, clearly violated, or unknown — which is where
the three verdicts come from:

* every field clear → ``SPLIT`` with no question;
* a field violated in a way that makes the candidate part of the active build
  (it changes the active criteria, or has a hard dependency on it) → ``KEEP``;
* a field unknown or borderline → ``ASK``, and ask *one* question, about the
  first doubt in a fixed order, so the person is never handed a checklist.

An explicit "make this separate" overrides doubt but not a hard technical
dependency; that case is ``REFUSE`` with an explanation for the parent thread.
Overlapping writable scope is never dropped on the floor: whatever the verdict,
the guidance names the paths and the two ways out — an ordering dependency on
the active build, or one owner for those files — and the dependency it would
add to the child's handoff is already built.

This module holds no state, performs no I/O, and calls no model.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from extensions.feature_workflow import planning_models
from extensions.feature_workflow.planning_models import (
    MAX_DEPENDENCIES,
    MAX_GOAL_CHARS,
    MAX_SCOPES,
    DependencyKind,
    OwnedScope,
    PlanDependency,
    PlanningModelError,
)

MAX_CRITERIA = 20

# Word overlap at or above this makes two goals "the same work, said twice" for
# an automatic decision; a person is asked rather than a child created.
SIMILAR_GOAL_RATIO = 0.6

_WORD_RE = re.compile(r"[a-z0-9]+")

# "make this separate", "split it out", "its own thread", "break that out",
# "a separate build/plan/feature/thread/change". The noun list is deliberate:
# "a separate table column" is a design remark, not a request.
_SEPARATION_RE = re.compile(
    r"\bmake\s+(?:this|that|it|the\s+\w+)\s+(?:a\s+)?separate\b|"
    r"\bsplit\s+(?:this|that|it)\s+(?:out|off)\b|"
    r"\bbreak\s+(?:this|that|it)\s+out\b|"
    r"\b(?:its|it's)\s+own\s+(?:build|thread|plan|feature|change)\b|"
    r"\bseparate\s+(?:build|thread|plan|feature|change)\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(?:don't|dont|do\s+not|never|not|no)\b", re.IGNORECASE)


class SplitPolicyError(Exception):
    """A candidate or active build is malformed, so no verdict is produced."""


class SplitDecision(StrEnum):
    SPLIT = "split"
    ASK = "ask"
    KEEP = "keep-together"
    REFUSE = "refuse"


def _text(label: str, raw: object, limit: int = planning_models.MAX_TEXT_CHARS) -> str:
    try:
        return planning_models._text(label, raw, limit)
    except PlanningModelError as exc:
        raise SplitPolicyError(str(exc)) from exc


def _texts(label: str, raw: object, limit: int) -> tuple[str, ...]:
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise SplitPolicyError(f"{label} must be a sequence of text")
    items = tuple(raw)
    if len(items) > limit:
        raise SplitPolicyError(f"{label} has too many entries: {len(items)} (limit {limit})")
    return tuple(_text(f"{label} entry", item) for item in items)


def _records(label: str, raw: object, kind: type, limit: int) -> tuple:
    if isinstance(raw, str) or not isinstance(raw, Iterable):
        raise SplitPolicyError(f"{label} must be a sequence of {kind.__name__} records")
    items = tuple(raw)
    if len(items) > limit:
        raise SplitPolicyError(f"{label} has too many entries: {len(items)} (limit {limit})")
    for item in items:
        if not isinstance(item, kind):
            raise SplitPolicyError(f"{label} must contain {kind.__name__} records")
    return items


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(text.lower()))


@dataclass(frozen=True, slots=True)
class ActiveBuild:
    """The build the parent thread is planning now, as far as the policy needs it."""

    id: str
    goal: str
    acceptance_criteria: tuple[str, ...] = ()
    ownership: tuple[OwnedScope, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text("active build id", self.id, MAX_GOAL_CHARS))
        object.__setattr__(self, "goal", _text("active build goal", self.goal, MAX_GOAL_CHARS))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _texts("active acceptance criteria", self.acceptance_criteria, MAX_CRITERIA),
        )
        object.__setattr__(
            self, "ownership", _records("active ownership", self.ownership, OwnedScope, MAX_SCOPES)
        )


@dataclass(frozen=True, slots=True)
class SplitCandidate:
    """What the planner proposes to split off. Unknown fields stay unknown.

    ``changes_active_criteria`` is three-valued on purpose: ``False`` is a
    claim the planner made, ``None`` is a claim it could not make, and only the
    first supports an automatic split.
    """

    goal: str
    acceptance_criteria: tuple[str, ...] = ()
    ownership: tuple[OwnedScope, ...] = ()
    changes_active_criteria: bool | None = None
    dependencies: tuple[PlanDependency, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "goal", _text("candidate goal", self.goal, MAX_GOAL_CHARS))
        object.__setattr__(
            self,
            "acceptance_criteria",
            _texts("candidate acceptance criteria", self.acceptance_criteria, MAX_CRITERIA),
        )
        object.__setattr__(
            self,
            "ownership",
            _records("candidate ownership", self.ownership, OwnedScope, MAX_SCOPES),
        )
        if self.changes_active_criteria not in (True, False, None):
            raise SplitPolicyError("changes_active_criteria must be true, false or unknown")
        object.__setattr__(
            self,
            "dependencies",
            _records("candidate dependencies", self.dependencies, PlanDependency, MAX_DEPENDENCIES),
        )


@dataclass(frozen=True, slots=True)
class SplitGuidance:
    """One way the parent can make overlapping scope safe before dispatch."""

    kind: str  # "ordering" | "single-owner"
    paths: tuple[str, ...]
    text: str

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "paths": list(self.paths), "text": self.text}


@dataclass(frozen=True, slots=True)
class SplitVerdict:
    """The decision, why, and what the parent must do with it."""

    decision: SplitDecision
    reasons: tuple[str, ...]
    question: str | None = None
    explanation: str | None = None
    guidance: tuple[SplitGuidance, ...] = ()
    dependencies: tuple[PlanDependency, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reasons": list(self.reasons),
            "question": self.question,
            "explanation": self.explanation,
            "guidance": [item.to_dict() for item in self.guidance],
            "dependencies": [item.to_dict() for item in self.dependencies],
        }


def detect_explicit_separation(text: str) -> bool:
    """True when the person asked for a separate build in so many words.

    A negated sentence ("don't make this separate") is not a request. The check
    is per sentence, so an earlier "no" in the message does not cancel a later
    request.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    for sentence in re.split(r"[.!?\n]+", text):
        match = _SEPARATION_RE.search(sentence)
        if match and not _NEGATION_RE.search(sentence[: match.start()]):
            return True
    return False


def _overlap(candidate: SplitCandidate, active: ActiveBuild) -> tuple[str, ...]:
    """Every candidate path that a writable scope of the active build also covers."""
    found: list[str] = []
    for mine in candidate.ownership:
        for theirs in active.ownership:
            if not mine.conflicts_with(theirs):
                continue
            found.extend(
                path
                for path in mine.paths
                if any(planning_models._paths_overlap(path, other) for other in theirs.paths)
                and path not in found
            )
    return tuple(found)


def _goal_relation(candidate: SplitCandidate, active: ActiveBuild) -> str:
    """'same', 'similar' or 'distinct'."""
    mine, theirs = _words(candidate.goal), _words(active.goal)
    if mine == theirs:
        return "same"
    if not mine:
        return "same"
    if mine <= theirs:
        return "similar"
    ratio = len(mine & theirs) / len(mine | theirs)
    return "similar" if ratio >= SIMILAR_GOAL_RATIO else "distinct"


def _hard_dependency(candidate: SplitCandidate, active: ActiveBuild) -> PlanDependency | None:
    return next(
        (
            item
            for item in candidate.dependencies
            if item.kind is DependencyKind.HARD and item.on == active.id
        ),
        None,
    )


def _guidance(candidate: SplitCandidate, active: ActiveBuild, paths: tuple[str, ...]) -> tuple:
    listed = ", ".join(paths)
    return (
        SplitGuidance(
            kind="ordering",
            paths=paths,
            text=(
                f"Record an ordering dependency: '{candidate.goal}' starts only after "
                f"'{active.goal}' has integrated its changes to {listed}."
            ),
        ),
        SplitGuidance(
            kind="single-owner",
            paths=paths,
            text=(
                f"Or give {listed} one owner: move those edits into '{active.goal}' and have "
                f"'{candidate.goal}' consume the result instead of writing the same files."
            ),
        ),
    )


def decide_split(
    candidate: SplitCandidate, active: ActiveBuild, *, explicit: bool = False
) -> SplitVerdict:
    """Decide split / ask / keep-together / refuse for one candidate.

    Precedence: the same goal is the same build (keep, even when asked); a hard
    dependency keeps an implicit candidate together and refuses an explicit one
    with an explanation; an explicit request then splits, carrying any scope
    guidance; otherwise a violated field keeps, a doubtful field asks one
    question, and a clear candidate splits.
    """
    if not isinstance(candidate, SplitCandidate) or not isinstance(active, ActiveBuild):
        raise SplitPolicyError("decide_split needs a SplitCandidate and an ActiveBuild")
    reasons: list[str] = []
    overlap = _overlap(candidate, active)
    guidance = _guidance(candidate, active, overlap) if overlap else ()
    dependencies: tuple[PlanDependency, ...] = candidate.dependencies
    if overlap:
        reasons.append(
            f"writable scope overlaps the active build: {', '.join(overlap)}; "
            "the parent must record an ordering dependency or a single owner before dispatch"
        )
        if not any(item.on == active.id for item in dependencies):
            dependencies += (
                PlanDependency(
                    on=active.id,
                    kind=DependencyKind.SHARED_SCOPE,
                    reason=f"both builds write {', '.join(overlap)}",
                ),
            )

    relation = _goal_relation(candidate, active)
    if relation == "same":
        reasons.append("the candidate goal is the active build's goal; there is nothing to split")
        return SplitVerdict(SplitDecision.KEEP, tuple(reasons), guidance=guidance)

    hard = _hard_dependency(candidate, active)
    if hard is not None:
        reasons.append(
            f"hard dependency on the active build: {hard.reason}; "
            "independent planning is unsafe until that is settled"
        )
        if explicit:
            return SplitVerdict(
                SplitDecision.REFUSE,
                tuple(reasons),
                explanation=(
                    f"'{candidate.goal}' cannot be planned separately yet: it has a hard "
                    f"dependency on '{active.goal}' ({hard.reason}). Settle that in this "
                    "thread first, then ask again."
                ),
                guidance=guidance,
                dependencies=dependencies,
            )
        return SplitVerdict(
            SplitDecision.KEEP, tuple(reasons), guidance=guidance, dependencies=dependencies
        )

    if explicit:
        reasons.append("explicit separation request")
        if candidate.changes_active_criteria:
            reasons.append(
                "the candidate changes the active build's acceptance criteria; "
                "the parent must revise them after the split"
            )
        return SplitVerdict(
            SplitDecision.SPLIT, tuple(reasons), guidance=guidance, dependencies=dependencies
        )

    if candidate.changes_active_criteria is True:
        reasons.append(
            "the candidate changes the active build's acceptance criteria, "
            "so it is part of that build"
        )
        return SplitVerdict(
            SplitDecision.KEEP, tuple(reasons), guidance=guidance, dependencies=dependencies
        )

    # Doubts, in the order the one question is chosen. Every doubt is recorded
    # as a reason; only the first becomes the question.
    doubts: list[tuple[str, str]] = []
    if relation == "similar":
        doubts.append(
            (
                "the candidate goal reads like part of the active build's goal",
                f"Is '{candidate.goal}' part of '{active.goal}', or a separate deliverable "
                "with its own plan?",
            )
        )
    if not candidate.acceptance_criteria:
        doubts.append(
            (
                "the candidate has no acceptance criteria of its own",
                f"Should '{candidate.goal}' be a separate build with its own acceptance "
                f"criteria, or stay part of '{active.goal}'?",
            )
        )
    if candidate.changes_active_criteria is None:
        doubts.append(
            (
                "unknown whether the candidate changes the active build's acceptance criteria",
                f"Would '{candidate.goal}' change what '{active.goal}' must deliver (keep it "
                "together), or leave the active build's acceptance criteria as they are "
                "(split it)?",
            )
        )
    if not candidate.ownership:
        doubts.append(
            (
                "the candidate's writable scope is unknown",
                f"Which files would '{candidate.goal}' write — should it be a separate build, "
                f"or is it part of '{active.goal}'?",
            )
        )
    if overlap:
        doubts.append(
            (
                "shared writable scope makes the boundary uncertain",
                f"'{candidate.goal}' would write {', '.join(overlap)}, which '{active.goal}' "
                "also owns — split it with an ordering dependency on the active build, or "
                "keep it together?",
            )
        )
    if doubts:
        reasons.extend(reason for reason, _ in doubts)
        return SplitVerdict(
            SplitDecision.ASK,
            tuple(reasons),
            question=doubts[0][1],
            guidance=guidance,
            dependencies=dependencies,
        )

    reasons.append(
        "distinct goal, own acceptance criteria, active criteria unchanged, "
        "no shared writable scope"
    )
    return SplitVerdict(
        SplitDecision.SPLIT, tuple(reasons), guidance=guidance, dependencies=dependencies
    )
