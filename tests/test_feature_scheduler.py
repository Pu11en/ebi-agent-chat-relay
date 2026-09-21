"""The ready set is the only answer to "what may start right now".

These tests pin the two claims the rest of the build leans on. First, readiness
is a property of the graph and the durable statuses alone: four safe tasks are
four dispatches, not three plus a queue, because no product-level worker count
exists to shorten the list. Second, every task left out says why in words a
person reading the parent thread can act on — an unintegrated dependency, a
blocked one, or a path someone else is writing right now.
"""

from __future__ import annotations

import inspect

import pytest

from extensions.feature_workflow.scheduler import (
    ACTIVE_STATUSES,
    SchedulerError,
    TaskStatus,
    compute_ready_set,
)
from extensions.feature_workflow.task_graph import TaskGraph, parse_task_graph

HEADER = "Check: `uv run pytest -q`\n\n## 1. Wave\n\n"

FOUR_SAFE = HEADER + "".join(
    f"- [ ] 1.{n} **Depends on: none; owns: `pkg/m{n}.py`, `tests/test_m{n}.py`.** Task {n}.\n"
    for n in range(1, 5)
)

CHAIN = HEADER + (
    "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** Contracts.\n"
    "- [ ] 1.2 **Depends on: 1.1; owns: `pkg/b.py`.** Middle.\n"
    "- [ ] 1.3 **Depends on: 1.2; owns: `pkg/c.py`.** Leaf.\n"
)

LEGACY = "Check: `uv run pytest -q`\n\n## 1. Fixes\n\n- [ ] Fix login\n- [ ] Fix export\n"


def graph(body: str = FOUR_SAFE) -> TaskGraph:
    return parse_task_graph(body)


class TestReadySet:
    def test_four_safe_tasks_are_all_ready(self) -> None:
        ready = compute_ready_set(graph())

        assert ready.ready_ids == ("1.1", "1.2", "1.3", "1.4")
        assert ready.pending_ids == ()
        assert ready.revision == graph().revision

    def test_ready_tasks_carry_what_a_worker_brief_needs(self) -> None:
        first = compute_ready_set(graph()).ready[0]

        assert first.id == "1.1"
        assert first.uid == graph()["1.1"].uid
        assert first.owned_paths == ("pkg/m1.py", "tests/test_m1.py")
        assert first.check == "uv run pytest -q"
        assert first.integration_only is False

    def test_readiness_has_no_product_worker_cap(self) -> None:
        wide = HEADER + "".join(
            f"- [ ] 1.{n} **Depends on: none; owns: `pkg/m{n}.py`.** Task {n}.\n"
            for n in range(1, 26)
        )

        ready = compute_ready_set(parse_task_graph(wide))

        assert len(ready.ready) == 25
        names = set(inspect.signature(compute_ready_set).parameters)
        assert not {name for name in names if any(w in name for w in ("max", "cap", "limit"))}

    def test_completed_checkboxes_are_not_dispatched_again(self) -> None:
        body = HEADER + (
            "- [x] 1.1 **Depends on: none; owns: `pkg/a.py`.** Done already.\n"
            "- [ ] 1.2 **Depends on: 1.1; owns: `pkg/b.py`.** Next.\n"
        )

        ready = compute_ready_set(parse_task_graph(body))

        assert ready.ready_ids == ("1.2",)

    def test_integration_only_tasks_are_kept_apart_from_worker_tasks(self) -> None:
        body = HEADER + (
            "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** Worker work.\n"
            "- [ ] 1.2 **Depends on: none; integration owner only; "
            "owns: `pkg/setup.py`.** Wiring.\n"
        )

        ready = compute_ready_set(parse_task_graph(body))

        assert ready.ready_ids == ("1.1", "1.2")
        assert ready.worker_ids == ("1.1",)
        assert ready.integration_ids == ("1.2",)

    def test_counts_summarize_the_whole_plan(self) -> None:
        ready = compute_ready_set(graph(CHAIN), {"1.1": "dispatched"})

        assert ready.counts == {"ready": 0, "pending": 2, "active": 1, "queued": 0, "integrated": 0}


class TestDependencies:
    def test_an_unintegrated_dependency_keeps_the_dependent_pending(self) -> None:
        ready = compute_ready_set(graph(CHAIN), {"1.1": TaskStatus.VERIFIED})

        assert ready.ready_ids == ()
        assert ready.pending_ids == ("1.2", "1.3")
        assert ready.kind_for("1.2") == "dependency"
        assert "1.1" in ready.reason_for("1.2")
        assert "verified" in ready.reason_for("1.2")
        assert ready.blockers_for("1.2") == ("1.1",)

    def test_only_an_integrated_dependency_releases_its_dependent(self) -> None:
        ready = compute_ready_set(graph(CHAIN), {"1.1": "integrated"})

        assert ready.ready_ids == ("1.2",)
        assert ready.kind_for("1.3") == "dependency"
        assert ready.blockers_for("1.3") == ("1.2",)

    def test_transitive_dependencies_are_enforced_not_just_direct_ones(self) -> None:
        ready = compute_ready_set(graph(CHAIN), {"1.1": "dispatched", "1.2": "pending"})

        assert ready.blockers_for("1.3") == ("1.1", "1.2")
        assert "1.1" in ready.reason_for("1.3")

    def test_a_blocked_dependency_is_reported_as_blocked_not_as_waiting(self) -> None:
        ready = compute_ready_set(graph(CHAIN), {"1.1": "blocked"})

        assert ready.kind_for("1.2") == "blocked-dependency"
        assert ready.kind_for("1.3") == "blocked-dependency"
        assert "blocked" in ready.reason_for("1.3")
        assert ready.blockers_for("1.3") == ("1.1",)

    def test_a_blocked_task_is_never_itself_ready(self) -> None:
        ready = compute_ready_set(graph(), {"1.2": "blocked"})

        assert ready.ready_ids == ("1.1", "1.3", "1.4")
        assert ready.kind_for("1.2") == "blocked"
        assert "blocked" in ready.reason_for("1.2")


class TestOwnership:
    def test_a_path_held_elsewhere_keeps_the_task_pending(self) -> None:
        ready = compute_ready_set(graph(), holds={"run-77/2.1": ["pkg/m3.py"]})

        assert ready.ready_ids == ("1.1", "1.2", "1.4")
        assert ready.kind_for("1.3") == "ownership"
        assert "pkg/m3.py" in ready.reason_for("1.3")
        assert "run-77/2.1" in ready.reason_for("1.3")
        assert ready.blockers_for("1.3") == ("run-77/2.1",)

    def test_a_held_directory_covers_the_files_inside_it(self) -> None:
        ready = compute_ready_set(graph(), holds={"other": ["tests/"]})

        assert ready.ready_ids == ()
        assert all(ready.kind_for(name) == "ownership" for name in ready.pending_ids)

    def test_an_unrelated_hold_changes_nothing(self) -> None:
        ready = compute_ready_set(graph(), holds={"other": ["docs/guide.md"]})

        assert ready.ready_ids == ("1.1", "1.2", "1.3", "1.4")

    def test_an_active_task_holds_its_own_paths_against_a_later_revision(self) -> None:
        # The graph proves no two concurrent tasks overlap, so an in-flight
        # conflict can only come from outside it — a task still finishing from
        # an earlier plan revision that owns the same file.
        ready = compute_ready_set(graph(), {"1.1": "dispatched"}, holds={"old-run": ["pkg/m2.py"]})

        assert ready.ready_ids == ("1.3", "1.4")
        assert ready.kind_for("1.2") == "ownership"
        assert ready.active_ids == ("1.1",)

    def test_a_dependency_reason_wins_over_an_ownership_reason(self) -> None:
        ready = compute_ready_set(graph(CHAIN), holds={"other": ["pkg/b.py"]})

        assert ready.kind_for("1.2") == "dependency"


class TestDurableStatuses:
    @pytest.mark.parametrize("status", sorted(ACTIVE_STATUSES))
    def test_an_active_task_is_never_dispatched_twice(self, status: str) -> None:
        ready = compute_ready_set(graph(), {"1.2": status})

        assert "1.2" not in ready.ready_ids
        assert "1.2" not in ready.pending_ids
        assert ready.active_ids == ("1.2",)

    def test_a_queued_task_reads_as_infrastructure_backpressure(self) -> None:
        ready = compute_ready_set(graph(), {"1.1": "queued"})

        assert ready.queued_ids == ("1.1",)
        assert ready.active_ids == ("1.1",)
        # Someone else's queue is not this plan's dependency: the rest still go.
        assert ready.ready_ids == ("1.2", "1.3", "1.4")

    def test_durable_state_overrides_the_plan_checkbox(self) -> None:
        body = HEADER + "- [x] 1.1 **Depends on: none; owns: `pkg/a.py`.** Marked done by hand.\n"

        ready = compute_ready_set(parse_task_graph(body), {"1.1": "pending"})

        assert ready.ready_ids == ("1.1",)

    def test_an_integrated_task_is_counted_not_rerun(self) -> None:
        ready = compute_ready_set(graph(), {"1.1": "integrated"})

        assert ready.ready_ids == ("1.2", "1.3", "1.4")
        assert ready.counts["integrated"] == 1

    def test_a_status_for_an_unknown_task_is_rejected(self) -> None:
        with pytest.raises(SchedulerError, match="unknown task"):
            compute_ready_set(graph(), {"9.9": "pending"})

    def test_an_unknown_status_value_is_rejected(self) -> None:
        with pytest.raises(SchedulerError, match="status"):
            compute_ready_set(graph(), {"1.1": "almost-done"})

    def test_an_invalid_hold_path_is_rejected(self) -> None:
        with pytest.raises(SchedulerError, match="Hold other"):
            compute_ready_set(graph(), holds={"other": ["/etc/passwd"]})

    def test_asking_about_a_task_that_is_not_pending_is_rejected(self) -> None:
        ready = compute_ready_set(graph())

        with pytest.raises(SchedulerError, match="not pending"):
            ready.reason_for("1.1")


class TestLegacyPlans:
    def test_a_legacy_plan_releases_one_task_at_a_time(self) -> None:
        ready = compute_ready_set(parse_task_graph(LEGACY))

        assert ready.ready_ids == ("t1",)
        assert ready.kind_for("t2") == "dependency"

    def test_a_legacy_task_waits_for_integration_not_for_a_finished_worker(self) -> None:
        ready = compute_ready_set(parse_task_graph(LEGACY), {"t1": "verified"})

        assert ready.ready_ids == ()
        assert ready.blockers_for("t2") == ("t1",)


class TestTheLivePlan:
    def test_the_parallel_gowork_plan_starts_every_remaining_independent_worker(self) -> None:
        from pathlib import Path

        from extensions.feature_workflow.task_graph import parse_task_plan

        repo = Path(__file__).resolve().parent.parent
        plan = parse_task_plan(repo / "openspec/changes/parallel-gowork/tasks.md", repo_root=repo)

        ready = compute_ready_set(plan, {"1.1": "integrated"})

        # 1.2, 1.3, 2.1, 2.3, 4.1, 4.3, 4.4 and 5.2 are ticked in the plan (built),
        # so every task that needed only them is a worker now; the rest still wait
        # on real dependencies.
        assert ready.worker_ids == ("2.2", "4.2")
        assert ready.kind_for("3.1") == "dependency"
        assert ready.kind_for("5.3") == "dependency"
        assert "5.1" in ready.blockers_for("5.3")
        assert "3.4" in ready.blockers_for("5.1")
