"""The briefing is what a fresh worker session knows, so its gaps are tested.

A worker cannot ask the parent thread a question: whatever the brief omits, it
invents, and whatever the brief smuggles in, it acts on. Every test here is
therefore either a required field the brief must state on its own, or a class of
content the brief must refuse to carry.
"""

from __future__ import annotations

import pytest

from extensions.feature_workflow.briefing import (
    BRIEFING_VERSION,
    MAX_BRIEFING_CHARS,
    MAX_DECISION_CHARS,
    MAX_DECISIONS,
    REQUIRED_RESULT_FIELDS,
    RESTRICTIONS,
    SECTION_NAMES,
    BriefingError,
    BuildContext,
    Decision,
    DependencyFoundation,
    ParentLink,
    ResultDestination,
    WorkerAssignment,
    build_worker_briefing,
)

FOUNDATION_SHA = "09c98b59349e7a069119059fb694134ef0b48880"
DIGEST = "8d33de8e06287c83e036338d358bdf365a86d36a6b42f359fb4d907a05e2983c"


def build(**kwargs) -> BuildContext:
    return BuildContext(
        run_id=kwargs.pop("run_id", "parallel-gowork-20260920"),
        goal=kwargs.pop(
            "goal",
            "Let one /gowork plan run every independent task at the same time "
            "with deterministic integration.",
        ),
        plan_revision=kwargs.pop("plan_revision", "391fb4c792d7ff88438f3e030ed69367"),
        plan_files=kwargs.pop("plan_files", ("openspec/changes/parallel-gowork/tasks.md",)),
        **kwargs,
    )


def assignment(**kwargs) -> WorkerAssignment:
    return WorkerAssignment(
        task_id=kwargs.pop("task_id", "2.3"),
        title=kwargs.pop("title", "Build the compact fresh-session task brief"),
        instruction=kwargs.pop(
            "instruction",
            "Use TDD to build the briefing from typed inputs and nothing else.",
        ),
        owned_paths=kwargs.pop(
            "owned_paths",
            ("extensions/feature_workflow/briefing.py", "tests/test_feature_briefing.py"),
        ),
        checks=kwargs.pop("checks", ("uv run pytest tests/test_feature_briefing.py -q",)),
        worktree=kwargs.pop("worktree", "/repo/.worktrees/wt-42"),
        branch=kwargs.pop("branch", "session/42"),
    )


def foundation(**kwargs) -> DependencyFoundation:
    return DependencyFoundation(
        commit=kwargs.pop("commit", FOUNDATION_SHA),
        repo_path=kwargs.pop("repo_path", "/repo"),
        integrated_tasks=kwargs.pop("integrated_tasks", ("1.1",)),
    )


def destination(**kwargs) -> ResultDestination:
    return ResultDestination(
        path=kwargs.pop("path", "/state/results/2.3.json"),
        approval_digest=kwargs.pop("approval_digest", DIGEST),
        **kwargs,
    )


def parent(**kwargs) -> ParentLink:
    return ParentLink(
        plan_owner_thread=kwargs.pop("plan_owner_thread", "1550757693784989707"),
        integration_owner_thread=kwargs.pop("integration_owner_thread", "1550757693784989707"),
        **kwargs,
    )


def brief(**kwargs):
    return build_worker_briefing(
        build=kwargs.pop("build", build()),
        assignment=kwargs.pop("assignment", assignment()),
        foundation=kwargs.pop("foundation", foundation()),
        decisions=kwargs.pop(
            "decisions",
            (Decision("D1", "Workers never merge; one integration owner serializes commits."),),
        ),
        destination=kwargs.pop("destination", destination()),
        parent=kwargs.pop("parent", parent()),
        **kwargs,
    )


class TestSelfContainedContent:
    def test_states_goal_task_decisions_paths_checks_result_and_parent(self) -> None:
        result = brief()
        text = result.text

        assert result.version == BRIEFING_VERSION
        assert result.task_id == "2.3"
        assert "run every independent task at the same time" in text
        assert "2.3" in text and "Build the compact fresh-session task brief" in text
        assert "Use TDD to build the briefing from typed inputs" in text
        assert "one integration owner serializes commits" in text
        assert "extensions/feature_workflow/briefing.py" in text
        assert "tests/test_feature_briefing.py" in text
        assert "uv run pytest tests/test_feature_briefing.py -q" in text
        assert "/state/results/2.3.json" in text
        assert DIGEST in text
        assert "1550757693784989707" in text

    def test_states_the_dependency_foundation_and_workspace(self) -> None:
        text = brief().text

        assert FOUNDATION_SHA in text
        assert "session/42" in text
        assert "/repo/.worktrees/wt-42" in text
        assert "1.1" in text

    def test_says_plainly_when_no_dependency_task_precedes_this_one(self) -> None:
        text = brief(foundation=foundation(integrated_tasks=())).text

        assert "no dependency tasks" in text.lower()

    def test_names_every_required_result_field(self) -> None:
        text = brief().text

        for field in REQUIRED_RESULT_FIELDS:
            assert field in text

    def test_keeps_the_declared_sections_in_order_and_all_nonempty(self) -> None:
        result = brief()

        assert tuple(name for name, _ in result.sections) == SECTION_NAMES
        assert all(body.strip() for _, body in result.sections)
        assert result.section("Checks").strip()

    def test_says_plainly_when_the_task_declares_no_focused_check(self) -> None:
        text = brief(assignment=assignment(checks=())).text

        assert "no focused check" in text.lower()

    def test_exposes_a_durable_record_without_rendering_it_twice(self) -> None:
        record = brief().as_dict()

        assert record["task_id"] == "2.3"
        assert record["version"] == BRIEFING_VERSION
        assert record["plan_revision"] == "391fb4c792d7ff88438f3e030ed69367"
        assert record["sections"]["Build goal"]


class TestExcludesTheParentTranscript:
    @pytest.mark.parametrize(
        "transcript",
        [
            "User: can you also fix the login page while you are there?",
            "Assistant: sure, I will start with the scheduler.\nUser: thanks",
            "[03:12] drew: ship it tonight",
            "<transcript>earlier planning conversation</transcript>",
        ],
    )
    def test_rejects_conversation_pasted_into_any_field(self, transcript: str) -> None:
        with pytest.raises(BriefingError, match="transcript"):
            brief(decisions=(Decision("D1", transcript),))

        with pytest.raises(BriefingError, match="transcript"):
            brief(build=build(goal=transcript))

        with pytest.raises(BriefingError, match="transcript"):
            brief(assignment=assignment(instruction=transcript))

    def test_accepts_a_decision_that_merely_mentions_those_words(self) -> None:
        statement = "The user picked queueing over blocking when relay capacity is short."

        assert statement in brief(decisions=(Decision("D1", statement),)).text

    def test_rejects_control_characters_smuggled_through_a_field(self) -> None:
        with pytest.raises(BriefingError, match="control character"):
            brief(build=build(goal="Ship the loop\x07 tonight"))


class TestRestrictions:
    def test_states_every_restriction_verbatim(self) -> None:
        text = brief().text

        for restriction in RESTRICTIONS:
            assert restriction in text

    def test_forbids_merging_scope_expansion_and_worker_spawning(self) -> None:
        lowered = brief().section("Restrictions").lower()

        assert "do not merge" in lowered
        assert "expand scope" in lowered
        assert "do not spawn" in lowered


class TestBounds:
    def test_rejects_a_task_that_owns_nothing(self) -> None:
        with pytest.raises(BriefingError, match="owned path"):
            brief(assignment=assignment(owned_paths=()))

    def test_rejects_too_many_decisions(self) -> None:
        many = tuple(Decision(f"D{n}", f"Decision {n}.") for n in range(MAX_DECISIONS + 1))

        with pytest.raises(BriefingError, match="decisions"):
            brief(decisions=many)

    def test_rejects_a_decision_longer_than_its_limit(self) -> None:
        with pytest.raises(BriefingError, match="too long"):
            brief(decisions=(Decision("D1", "x" * (MAX_DECISION_CHARS + 1)),))

    def test_rejects_a_briefing_whose_valid_fields_still_exceed_the_budget(self) -> None:
        fat = tuple(Decision(f"D{n}", "y" * MAX_DECISION_CHARS) for n in range(MAX_DECISIONS))

        with pytest.raises(BriefingError, match="too long"):
            brief(decisions=fat)

    def test_stays_inside_the_budget_for_a_realistic_briefing(self) -> None:
        assert len(brief().text) <= MAX_BRIEFING_CHARS

    def test_rejects_a_foundation_that_is_not_a_full_commit_sha(self) -> None:
        with pytest.raises(BriefingError, match="commit"):
            brief(foundation=foundation(commit="09c98b5"))

    def test_rejects_an_approval_digest_that_is_not_a_full_digest(self) -> None:
        with pytest.raises(BriefingError, match="digest"):
            brief(destination=destination(approval_digest="deadbeef"))

    def test_rejects_a_parent_thread_that_is_not_an_id(self) -> None:
        with pytest.raises(BriefingError, match="thread"):
            brief(parent=parent(plan_owner_thread="the planning thread"))

    def test_rejects_missing_plan_files_and_revision(self) -> None:
        with pytest.raises(BriefingError, match="plan file"):
            brief(build=build(plan_files=()))

        with pytest.raises(BriefingError, match="revision"):
            brief(build=build(plan_revision=""))
