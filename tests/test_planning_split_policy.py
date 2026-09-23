"""The split policy decides, deterministically, whether a proposed build gets its own thread.

The planner's model may *propose* a candidate; it never decides. These tests pin
the decisions to the fields the spec names — a distinct goal, its own acceptance
criteria, no change to the active build's criteria, and no shared writable
scope — and the three outcomes a person can act on:

* clear → split, with no question asked;
* uncertain → exactly one split-or-keep question naming the doubt;
* required part of the active build → keep together, and say why.

An explicit "make this separate" wins over uncertainty but not over a hard
technical dependency, which is explained in the parent instead. Overlapping
writable scope never disappears: whichever way the decision goes, the verdict
carries ordering/ownership guidance the parent records before dispatch.
"""

from __future__ import annotations

import pytest

from extensions.feature_workflow.planning_models import (
    DependencyKind,
    OwnedScope,
    PlanDependency,
)
from extensions.feature_workflow.split_policy import (
    ActiveBuild,
    SplitCandidate,
    SplitDecision,
    SplitPolicyError,
    SplitVerdict,
    decide_split,
    detect_explicit_separation,
)

ACTIVE_ID = "1550757693784989707:lead-table"


def active(**overrides: object) -> ActiveBuild:
    fields: dict[str, object] = {
        "id": ACTIVE_ID,
        "goal": "Build the Texas lead table from county permit feeds",
        "acceptance_criteria": ("permits from 12 counties land in one table nightly",),
        "ownership": (
            OwnedScope(
                project="realpage",
                paths=("sources/permits.py", "tables/leads.py"),
                owner="active",
            ),
        ),
    }
    fields.update(overrides)
    return ActiveBuild(**fields)  # type: ignore[arg-type]


def candidate(**overrides: object) -> SplitCandidate:
    fields: dict[str, object] = {
        "goal": "Send a weekly email digest of new leads to the sales team",
        "acceptance_criteria": ("one email every Monday with the week's new leads",),
        "ownership": (
            OwnedScope(project="realpage", paths=("digest/weekly.py",), owner="candidate"),
        ),
        "changes_active_criteria": False,
        "dependencies": (),
    }
    fields.update(overrides)
    return SplitCandidate(**fields)  # type: ignore[arg-type]


class TestClearSplit:
    def test_distinct_goal_criteria_and_scope_split_without_a_question(self) -> None:
        verdict = decide_split(candidate(), active())
        assert isinstance(verdict, SplitVerdict)
        assert verdict.decision is SplitDecision.SPLIT
        assert verdict.question is None
        assert verdict.guidance == ()
        assert verdict.reasons

    def test_different_project_cannot_share_writable_scope(self) -> None:
        other = candidate(
            ownership=(
                OwnedScope(project="website", paths=("sources/permits.py",), owner="candidate"),
            )
        )
        verdict = decide_split(other, active())
        assert verdict.decision is SplitDecision.SPLIT
        assert verdict.guidance == ()

    def test_ordering_dependency_on_the_active_build_is_carried_not_blocking(self) -> None:
        dependent = candidate(
            dependencies=(
                PlanDependency(
                    on=ACTIVE_ID, kind=DependencyKind.ORDERING, reason="reads the lead table"
                ),
            )
        )
        verdict = decide_split(dependent, active())
        assert verdict.decision is SplitDecision.SPLIT
        assert any(item.on == ACTIVE_ID for item in verdict.dependencies)

    def test_verdict_serializes_for_the_parent_status(self) -> None:
        data = decide_split(candidate(), active()).to_dict()
        assert data["decision"] == "split"
        assert data["question"] is None
        assert isinstance(data["reasons"], list)


class TestKeepTogether:
    def test_changing_the_active_criteria_keeps_it_together(self) -> None:
        verdict = decide_split(candidate(changes_active_criteria=True), active())
        assert verdict.decision is SplitDecision.KEEP
        assert verdict.question is None
        assert any("acceptance" in reason for reason in verdict.reasons)

    def test_hard_dependency_keeps_it_together(self) -> None:
        bound = candidate(
            dependencies=(
                PlanDependency(
                    on=ACTIVE_ID,
                    kind=DependencyKind.HARD,
                    reason="needs the table schema that is still being decided",
                ),
            )
        )
        verdict = decide_split(bound, active())
        assert verdict.decision is SplitDecision.KEEP
        assert any("hard" in reason for reason in verdict.reasons)

    def test_same_goal_is_the_same_build(self) -> None:
        same = candidate(goal="  build the TEXAS lead table from county permit feeds ")
        verdict = decide_split(same, active())
        assert verdict.decision is SplitDecision.KEEP


class TestUncertain:
    """Each fixture has one doubt; the verdict asks exactly one question about it."""

    @pytest.mark.parametrize(
        "overrides, doubt",
        [
            ({"acceptance_criteria": ()}, "acceptance"),
            ({"changes_active_criteria": None}, "acceptance"),
            ({"ownership": ()}, "write"),
            ({"goal": "Build the lead table"}, "part of"),
        ],
    )
    def test_uncertain_fixture_requires_one_question(
        self, overrides: dict[str, object], doubt: str
    ) -> None:
        verdict = decide_split(candidate(**overrides), active())
        assert verdict.decision is SplitDecision.ASK
        assert verdict.question is not None
        assert verdict.question.count("?") == 1
        assert doubt in verdict.question.lower()

    def test_several_doubts_still_ask_one_question(self) -> None:
        verdict = decide_split(
            candidate(acceptance_criteria=(), ownership=(), changes_active_criteria=None),
            active(),
        )
        assert verdict.decision is SplitDecision.ASK
        assert verdict.question is not None
        assert verdict.question.count("?") == 1

    def test_overlapping_writable_scope_asks_and_gives_guidance(self) -> None:
        overlapping = candidate(
            ownership=(
                OwnedScope(project="realpage", paths=("sources/permits.py",), owner="candidate"),
            )
        )
        verdict = decide_split(overlapping, active())
        assert verdict.decision is SplitDecision.ASK
        assert verdict.question is not None and verdict.question.count("?") == 1
        assert verdict.guidance
        kinds = {item.kind for item in verdict.guidance}
        assert kinds == {"ordering", "single-owner"}
        assert all("sources/permits.py" in item.paths for item in verdict.guidance)
        assert any(
            item.on == ACTIVE_ID and item.kind is DependencyKind.SHARED_SCOPE
            for item in verdict.dependencies
        )

    def test_directory_scope_overlaps_a_file_inside_it(self) -> None:
        overlapping = candidate(
            ownership=(OwnedScope(project="realpage", paths=("sources/",), owner="candidate"),)
        )
        verdict = decide_split(overlapping, active())
        assert verdict.decision is SplitDecision.ASK
        assert verdict.guidance


class TestExplicitRequest:
    def test_explicit_request_splits_an_uncertain_candidate(self) -> None:
        verdict = decide_split(candidate(acceptance_criteria=()), active(), explicit=True)
        assert verdict.decision is SplitDecision.SPLIT
        assert verdict.question is None

    def test_explicit_request_with_overlap_splits_with_guidance(self) -> None:
        overlapping = candidate(
            ownership=(
                OwnedScope(project="realpage", paths=("tables/leads.py",), owner="candidate"),
            )
        )
        verdict = decide_split(overlapping, active(), explicit=True)
        assert verdict.decision is SplitDecision.SPLIT
        assert verdict.guidance
        assert any(item.kind is DependencyKind.SHARED_SCOPE for item in verdict.dependencies)

    def test_hard_dependency_refuses_and_explains(self) -> None:
        bound = candidate(
            dependencies=(
                PlanDependency(
                    on=ACTIVE_ID,
                    kind=DependencyKind.HARD,
                    reason="needs the table schema that is still being decided",
                ),
            )
        )
        verdict = decide_split(bound, active(), explicit=True)
        assert verdict.decision is SplitDecision.REFUSE
        assert verdict.explanation is not None
        assert "table schema" in verdict.explanation
        assert verdict.question is None

    def test_explicit_request_on_the_same_goal_still_keeps_together(self) -> None:
        verdict = decide_split(candidate(goal=active().goal), active(), explicit=True)
        assert verdict.decision is SplitDecision.KEEP


class TestDetectExplicitSeparation:
    @pytest.mark.parametrize(
        "text",
        [
            "make this separate",
            "Make that a separate build please",
            "let's split this out into its own thread",
            "this should be its own build",
            "break it out as a separate plan",
            "Can you make the digest a separate feature?",
        ],
    )
    def test_recognizes_separation_requests(self, text: str) -> None:
        assert detect_explicit_separation(text)

    @pytest.mark.parametrize(
        "text",
        [
            "don't make this separate",
            "do not split this out",
            "keep this in the same build",
            "the separate table column should be nullable",
            "",
        ],
    )
    def test_ignores_other_text(self, text: str) -> None:
        assert not detect_explicit_separation(text)


class TestRefusals:
    def test_candidate_goal_is_required(self) -> None:
        with pytest.raises(SplitPolicyError):
            candidate(goal="   ")

    def test_active_build_needs_an_id(self) -> None:
        with pytest.raises(SplitPolicyError):
            active(id="")

    def test_candidate_scope_must_be_owned_scopes(self) -> None:
        with pytest.raises(SplitPolicyError):
            candidate(ownership=("sources/permits.py",))

    def test_transcript_in_the_goal_is_refused(self) -> None:
        with pytest.raises(SplitPolicyError):
            candidate(goal="User: do the digest\nAssistant: ok\nUser: go")
