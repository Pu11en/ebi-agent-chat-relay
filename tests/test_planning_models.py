"""The planning records are a handoff contract, so they are tested as a gate.

A child planning thread gets a compact structured brief instead of the parent
conversation. Every test here is either a field the brief must still carry after
a restart, or a thing the brief must refuse to carry at all: a transcript, a
prompt history, or a secret. If these hold, the registry can persist a child
link and a bot restart can restore it without asking the parent thread anything.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from extensions.feature_workflow.planning_models import (
    PLANNING_MODELS_VERSION,
    REQUIRED_HANDOFF_FIELDS,
    ChildHandoff,
    ChildRecord,
    DecisionAck,
    DependencyKind,
    LockedDecision,
    OwnedScope,
    ParentLink,
    PlanDependency,
    PlanningModelError,
    PlanningState,
    ProjectTarget,
    SplitIdentity,
    summarize_children,
)

PARENT = 1550757693784989707
CHILD = 1551148966035333194


def parent_link(**overrides: object) -> ParentLink:
    fields: dict[str, object] = {
        "thread_id": PARENT,
        "url": f"https://discord.com/channels/1/{PARENT}",
        "plan_owner": "parent-planner",
    }
    fields.update(overrides)
    return ParentLink(**fields)  # type: ignore[arg-type]


def handoff(**overrides: object) -> ChildHandoff:
    fields: dict[str, object] = {
        "goal": "Add a weekly permit refresh to the Texas lead table",
        "target": ProjectTarget(project="realpage", computer="asset"),
        "decisions": (
            LockedDecision(
                id="d1",
                statement="Texas only; other states need a paying client first",
                revision="391fb4c7",
                source="parent-thread",
            ),
        ),
        "dependencies": (
            PlanDependency(
                on=f"{PARENT}:lead-table",
                kind=DependencyKind.ORDERING,
                reason="shares the table schema",
            ),
        ),
        "ownership": (
            OwnedScope(
                project="realpage", paths=("sources/permits.py",), owner="child", writable=True
            ),
        ),
        "restrictions": ("planning is read-only", "never merge"),
        "authority": "plan only; the parent thread dispatches the build",
        "expected_output": "one implementation-ready plan with a Check command",
        "parent": parent_link(),
    }
    fields.update(overrides)
    return ChildHandoff(**fields)  # type: ignore[arg-type]


def record(**overrides: object) -> ChildRecord:
    fields: dict[str, object] = {
        "identity": SplitIdentity.from_goal(PARENT, "Add a weekly permit refresh"),
        "handoff": handoff(),
        "state": PlanningState.PROPOSED,
        "created_at": "2026-09-20T03:30:00+00:00",
        "updated_at": "2026-09-20T03:30:00+00:00",
    }
    fields.update(overrides)
    return ChildRecord(**fields)  # type: ignore[arg-type]


class TestSplitIdentity:
    def test_same_goal_is_the_same_identity(self) -> None:
        """Recovery keys off this: a repeated split request is not a new child."""
        first = SplitIdentity.from_goal(PARENT, "Add a weekly permit refresh")
        second = SplitIdentity.from_goal(PARENT, "  add a WEEKLY permit   refresh  ")
        assert first == second
        assert first.key == second.key

    def test_different_goal_or_parent_is_a_different_identity(self) -> None:
        base = SplitIdentity.from_goal(PARENT, "Add a weekly permit refresh")
        assert base != SplitIdentity.from_goal(PARENT, "Add a monthly permit refresh")
        assert base != SplitIdentity.from_goal(CHILD, "Add a weekly permit refresh")

    def test_key_round_trips(self) -> None:
        identity = SplitIdentity.from_goal(PARENT, "Add a weekly permit refresh")
        assert SplitIdentity.from_key(identity.key) == identity

    def test_rejects_unusable_identity(self) -> None:
        with pytest.raises(PlanningModelError):
            SplitIdentity.from_goal(0, "Add a weekly permit refresh")
        with pytest.raises(PlanningModelError):
            SplitIdentity.from_goal(PARENT, "   ")
        with pytest.raises(PlanningModelError):
            SplitIdentity.from_key("not-a-key")


class TestBoundedText:
    def test_rejects_a_pasted_transcript(self) -> None:
        transcript = (
            "User: can you also do the permits thing\n"
            "Assistant: sure, here is what I would do\n"
            "User: ok go"
        )
        with pytest.raises(PlanningModelError, match="transcript"):
            handoff(goal=transcript)

    def test_rejects_prompt_history_markers(self) -> None:
        with pytest.raises(PlanningModelError, match="transcript"):
            handoff(authority="<transcript>earlier turns of the parent thread</transcript>")

    def test_rejects_a_secret(self) -> None:
        with pytest.raises(PlanningModelError, match="secret"):
            handoff(
                decisions=(
                    LockedDecision(
                        id="d1",
                        statement="use api_key=sk-ant-0123456789abcdef0123",
                        revision="391fb4c7",
                        source="parent-thread",
                    ),
                )
            )

    def test_rejects_an_oversized_goal(self) -> None:
        with pytest.raises(PlanningModelError, match="too long"):
            handoff(goal="permits " * 200)

    def test_rejects_too_many_decisions(self) -> None:
        many = tuple(
            LockedDecision(id=f"d{n}", statement=f"decision {n}", revision="r", source="parent")
            for n in range(40)
        )
        with pytest.raises(PlanningModelError, match="too many"):
            handoff(decisions=many)

    def test_rejects_an_empty_required_field(self) -> None:
        with pytest.raises(PlanningModelError):
            handoff(expected_output="   ")


class TestHandoffSerialization:
    def test_contains_every_required_field(self) -> None:
        data: Any = json.loads(json.dumps(handoff().to_dict()))
        assert set(data) == set(REQUIRED_HANDOFF_FIELDS)
        assert data["goal"]
        assert data["target"] == {"project": "realpage", "computer": "asset", "repo_path": None}
        assert data["parent"]["thread_id"] == PARENT
        assert data["decisions"][0]["revision"] == "391fb4c7"
        assert data["dependencies"][0]["kind"] == "ordering"
        assert data["ownership"][0]["paths"] == ["sources/permits.py"]
        assert data["restrictions"] == ["planning is read-only", "never merge"]

    def test_round_trips_through_json(self) -> None:
        original = handoff()
        restored = ChildHandoff.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored == original

    def test_serialization_carries_no_transcript_field(self) -> None:
        blob = json.dumps(handoff().to_dict()).lower()
        for banned in ("transcript", "messages", "history", "prompt", "token", "secret"):
            assert banned not in blob

    def test_rejects_a_missing_required_field(self) -> None:
        data = handoff().to_dict()
        del data["authority"]
        with pytest.raises(PlanningModelError, match="authority"):
            ChildHandoff.from_dict(data)

    def test_rejects_an_unknown_field(self) -> None:
        data = handoff().to_dict()
        data["transcript"] = "User: ...\nAssistant: ..."
        with pytest.raises(PlanningModelError, match="transcript"):
            ChildHandoff.from_dict(data)


class TestOwnership:
    def test_detects_overlapping_writable_scope(self) -> None:
        """The parent needs this before two children may build at the same time."""
        child_a = OwnedScope(project="realpage", paths=("sources/",), owner="a", writable=True)
        child_b = OwnedScope(
            project="realpage", paths=("sources/permits.py",), owner="b", writable=True
        )
        assert child_a.conflicts_with(child_b)
        assert child_b.conflicts_with(child_a)

    def test_read_only_or_other_project_does_not_conflict(self) -> None:
        writable = OwnedScope(project="realpage", paths=("sources/",), owner="a", writable=True)
        reader = OwnedScope(project="realpage", paths=("sources/",), owner="b", writable=False)
        elsewhere = OwnedScope(project="cranesignal", paths=("sources/",), owner="c", writable=True)
        assert not writable.conflicts_with(reader)
        assert not writable.conflicts_with(elsewhere)

    def test_disjoint_paths_do_not_conflict(self) -> None:
        first = OwnedScope(
            project="realpage", paths=("sources/permits.py",), owner="a", writable=True
        )
        second = OwnedScope(
            project="realpage", paths=("sources/sales.py",), owner="b", writable=True
        )
        assert not first.conflicts_with(second)

    def test_rejects_an_unsafe_path(self) -> None:
        for bad in ("/etc/passwd", "../other", ".git/config", ".worktrees/wt-1"):
            with pytest.raises(PlanningModelError):
                OwnedScope(project="realpage", paths=(bad,), owner="a", writable=True)


class TestChildRecord:
    def test_states_cover_parent_and_child_life(self) -> None:
        assert PlanningState.PROPOSED in PlanningState
        assert {state.value for state in PlanningState} == {
            "proposed",
            "creating",
            "planning",
            "awaiting-answer",
            "plan-ready",
            "dispatched",
            "integrated",
            "blocked",
            "cancelled",
        }

    def test_round_trips_through_json(self) -> None:
        original = record(
            state=PlanningState.PLANNING,
            thread_id=CHILD,
            plan_revision="391fb4c7",
            acks=(
                DecisionAck(
                    decision_id="d1",
                    revision="391fb4c7",
                    child_thread_id=CHILD,
                    acknowledged_at="2026-09-20T03:31:00+00:00",
                ),
            ),
        )
        restored = ChildRecord.from_json(original.to_json())
        assert restored == original
        assert restored.acks[0].decision_id == "d1"
        assert json.loads(original.to_json())["version"] == PLANNING_MODELS_VERSION

    def test_transitions_are_immutable_copies(self) -> None:
        start = record()
        started = start.with_state(PlanningState.PLANNING, at="2026-09-20T03:35:00+00:00")
        assert start.state is PlanningState.PROPOSED
        assert started.state is PlanningState.PLANNING
        assert started.updated_at == "2026-09-20T03:35:00+00:00"
        assert started.identity == start.identity

    def test_thread_is_recorded_once(self) -> None:
        created = record().with_thread(CHILD, at="2026-09-20T03:36:00+00:00")
        assert created.thread_id == CHILD
        assert created.state is PlanningState.CREATING
        with pytest.raises(PlanningModelError, match="already"):
            created.with_thread(999, at="2026-09-20T03:37:00+00:00")

    def test_blocked_requires_a_named_blocker(self) -> None:
        with pytest.raises(PlanningModelError, match="blocker"):
            record(state=PlanningState.BLOCKED)
        blocked = record().with_state(
            PlanningState.BLOCKED, at="2026-09-20T03:40:00+00:00", blocker="waiting on the schema"
        )
        assert blocked.blocker == "waiting on the schema"

    def test_acknowledges_a_changed_decision_once(self) -> None:
        live = record().with_thread(CHILD, at="2026-09-20T03:36:00+00:00")
        ack = DecisionAck(
            decision_id="d1",
            revision="391fb4c7",
            child_thread_id=CHILD,
            acknowledged_at="2026-09-20T03:41:00+00:00",
        )
        once = live.acknowledge(ack)
        twice = once.acknowledge(ack)
        assert len(once.acks) == 1
        assert once == twice
        assert once.has_acknowledged("d1", "391fb4c7")
        assert not once.has_acknowledged("d1", "other-revision")

    def test_rejects_a_bad_timestamp(self) -> None:
        with pytest.raises(PlanningModelError, match="timestamp"):
            record(created_at="yesterday")
        with pytest.raises(PlanningModelError, match="timestamp"):
            record(created_at="2026-09-20T03:30:00")


class TestParentSummary:
    def test_shows_each_child_link_goal_state_dependencies_and_blocker(self) -> None:
        first = record().with_thread(CHILD, at="2026-09-20T03:36:00+00:00")
        second = record(
            identity=SplitIdentity.from_goal(PARENT, "Rebuild the sales feed"),
            handoff=handoff(goal="Rebuild the sales feed"),
        ).with_state(
            PlanningState.BLOCKED, at="2026-09-20T03:37:00+00:00", blocker="needs the schema"
        )
        summary = summarize_children(PARENT, (first, second))
        assert summary.parent_thread_id == PARENT
        assert len(summary.children) == 2
        assert summary.children[0].url == f"https://discord.com/channels/1/{CHILD}"
        assert summary.children[0].goal.startswith("Add a weekly permit refresh")
        assert summary.children[0].state is PlanningState.CREATING
        assert summary.children[0].dependencies == (f"{PARENT}:lead-table",)
        assert summary.children[1].blocker == "needs the schema"
        assert summary.children[1].url is None

    def test_rejects_a_child_of_another_parent(self) -> None:
        with pytest.raises(PlanningModelError, match="parent"):
            summarize_children(CHILD, (record(),))

    def test_rejects_duplicate_identities(self) -> None:
        with pytest.raises(PlanningModelError, match="duplicate"):
            summarize_children(PARENT, (record(), record()))
