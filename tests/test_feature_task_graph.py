"""The task graph is the scheduling contract, so its parser is tested as a gate.

Every test here is a rejection the scheduler is entitled to rely on: if the graph
parses, two of its concurrent tasks cannot write the same file and no dependency
can be unknown, circular, or outside the repository.
"""

from __future__ import annotations

import pytest

from extensions.feature_workflow.task_graph import (
    TASK_GRAPH_VERSION,
    TaskGraph,
    TaskGraphError,
    parse_task_graph,
    parse_task_plan,
)

STRUCTURED = """\
Check: `uv run pytest -q`

Try: `uv run python -m extensions.feature_workflow.coordinator --help`

## 1. Shared contracts

- [ ] 1.1 **Depends on: none; owns: `pkg/graph.py`, `tests/test_graph.py`.** Parse the graph.
- [x] 1.2 **Depends on: none; owns: `pkg/state.py`; check: `uv run pytest tests/test_state.py -q`.**
  Persist state.

## 2. Dispatch

- [ ] 2.1 **Depends on: 1.1, 1.2; owns: `pkg/loop.py`.** Route ready tasks.
- [ ] 2.2 **Depends on: 2.1; integration owner only; owns: no source files.** Record the evidence.
"""

LEGACY = """\
Check: `uv run pytest -q`

## 1. Fixes

- [x] Fix the broken login page
- [ ] Fix the export button
- [ ] Add a smoke test
"""


def graph(body: str, **kwargs) -> TaskGraph:
    return parse_task_graph(body, **kwargs)


def structured(*rows: str, header: str = "Check: `uv run pytest -q`\n\n## 1. S\n\n") -> str:
    return header + "\n".join(rows) + "\n"


class TestStructuredPlans:
    def test_parses_ids_dependencies_ownership_and_checks(self) -> None:
        parsed = graph(STRUCTURED)

        assert parsed.version == TASK_GRAPH_VERSION
        assert not parsed.legacy
        assert [task.id for task in parsed.tasks] == ["1.1", "1.2", "2.1", "2.2"]
        assert parsed["1.1"].owned_paths == ("pkg/graph.py", "tests/test_graph.py")
        assert parsed["2.1"].depends_on == ("1.1", "1.2")
        assert parsed["2.2"].owned_paths == ()
        assert parsed["1.1"].section == "1. Shared contracts"
        assert parsed["2.1"].title == "Route ready tasks."

    def test_task_check_overrides_the_plan_check(self) -> None:
        parsed = graph(STRUCTURED)

        assert parsed.plan_check == "uv run pytest -q"
        assert parsed.try_command == (
            "uv run python -m extensions.feature_workflow.coordinator --help"
        )
        assert parsed.check_for("1.2") == "uv run pytest tests/test_state.py -q"
        assert parsed.check_for("1.1") == "uv run pytest -q"

    def test_done_and_integration_only_markers_are_kept(self) -> None:
        parsed = graph(STRUCTURED)

        assert parsed["1.2"].done is True
        assert parsed["1.1"].done is False
        assert parsed["2.2"].integration_only is True
        assert parsed["2.1"].integration_only is False
        assert [task.id for task in parsed.open_tasks()] == ["1.1", "2.1", "2.2"]

    def test_task_ids_are_versioned_by_the_plan_revision(self) -> None:
        parsed = graph(STRUCTURED)
        same = graph(STRUCTURED)
        edited = graph(STRUCTURED.replace("Route ready tasks.", "Route every ready task."))

        assert parsed.revision == same.revision
        assert parsed["1.1"].uid == same["1.1"].uid
        assert parsed.revision != edited.revision
        assert parsed["1.1"].uid != edited["1.1"].uid
        assert parsed["1.1"].uid.endswith(":1.1")

    def test_trailing_whitespace_does_not_change_the_revision(self) -> None:
        assert graph(STRUCTURED).revision == graph(STRUCTURED.replace("\n", "   \n")).revision

    def test_a_structured_task_without_any_check_is_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="focused check"):
            graph("## 1. S\n\n- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** Do it.\n")

    def test_unknown_metadata_keys_are_rejected_rather_than_ignored(self) -> None:
        with pytest.raises(TaskGraphError, match="owner"):
            graph(structured("- [ ] 1.1 **Depends on: none; owner: `pkg/a.py`.** Do it."))


class TestDependencyValidation:
    def test_unknown_dependency_is_named(self) -> None:
        with pytest.raises(TaskGraphError, match="9.9"):
            graph(structured("- [ ] 1.1 **Depends on: 9.9; owns: `pkg/a.py`.** Do it."))

    def test_self_dependency_is_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="1.1"):
            graph(structured("- [ ] 1.1 **Depends on: 1.1; owns: `pkg/a.py`.** Do it."))

    def test_cycle_is_rejected_and_the_cycle_is_named(self) -> None:
        with pytest.raises(TaskGraphError) as error:
            graph(
                structured(
                    "- [ ] 1.1 **Depends on: 1.3; owns: `pkg/a.py`.** A.",
                    "- [ ] 1.2 **Depends on: 1.1; owns: `pkg/b.py`.** B.",
                    "- [ ] 1.3 **Depends on: 1.2; owns: `pkg/c.py`.** C.",
                )
            )

        assert "cycle" in str(error.value)
        assert {"1.1", "1.2", "1.3"} <= set(str(error.value).replace("->", " ").split())

    def test_duplicate_task_ids_are_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="1.1"):
            graph(
                structured(
                    "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** A.",
                    "- [ ] 1.1 **Depends on: none; owns: `pkg/b.py`.** B.",
                )
            )

    def test_transitive_dependencies_and_dependents_are_exposed(self) -> None:
        parsed = graph(STRUCTURED)

        assert parsed.dependencies_of("2.2") == frozenset({"1.1", "1.2", "2.1"})
        assert parsed.dependencies_of("1.1") == frozenset()
        assert parsed.dependents_of("1.1") == frozenset({"2.1", "2.2"})

    def test_may_run_together_is_false_along_a_dependency_chain(self) -> None:
        parsed = graph(STRUCTURED)

        assert parsed.may_run_together("1.1", "1.2")
        assert not parsed.may_run_together("1.1", "2.2")
        assert not parsed.may_run_together("2.2", "1.1")


class TestOwnedPathValidation:
    @pytest.mark.parametrize(
        "path",
        ["/etc/passwd", "../outside.py", "pkg/../../escape.py", "pkg\\win.py", ".", "", "   "],
    )
    def test_absolute_traversal_and_non_posix_paths_are_rejected(self, path: str) -> None:
        with pytest.raises(TaskGraphError):
            graph(structured(f"- [ ] 1.1 **Depends on: none; owns: `{path}`.** Do it."))

    @pytest.mark.parametrize(
        "path", [".git/config", "openspec/changes/x/tasks.md", ".worktrees/wt-1/a.py"]
    )
    def test_protected_paths_cannot_be_owned(self, path: str) -> None:
        with pytest.raises(TaskGraphError, match="protected"):
            graph(structured(f"- [ ] 1.1 **Depends on: none; owns: `{path}`.** Do it."))

    def test_caller_supplied_protected_paths_are_honored(self) -> None:
        body = structured("- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** Do it.")

        assert graph(body)["1.1"].owned_paths == ("pkg/a.py",)
        with pytest.raises(TaskGraphError, match="protected"):
            graph(body, protected_paths=["pkg/"])

    def test_the_plan_file_itself_is_protected(self) -> None:
        with pytest.raises(TaskGraphError, match="protected"):
            graph(
                structured("- [ ] 1.1 **Depends on: none; owns: `plan/tasks.md`.** Do it."),
                plan_path="plan/tasks.md",
            )

    def test_repeated_paths_inside_one_task_are_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="pkg/a.py"):
            graph(structured("- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`, `pkg/a.py`.** Do."))


class TestOwnershipOverlap:
    def test_independent_tasks_sharing_a_file_are_rejected(self) -> None:
        with pytest.raises(TaskGraphError) as error:
            graph(
                structured(
                    "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** A.",
                    "- [ ] 1.2 **Depends on: none; owns: `pkg/a.py`, `pkg/b.py`.** B.",
                )
            )

        assert "pkg/a.py" in str(error.value)
        assert "1.1" in str(error.value) and "1.2" in str(error.value)

    def test_independent_directory_scope_containing_another_task_is_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="pkg/"):
            graph(
                structured(
                    "- [ ] 1.1 **Depends on: none; owns: `pkg/`.** A.",
                    "- [ ] 1.2 **Depends on: none; owns: `pkg/b.py`.** B.",
                )
            )

    def test_directory_scope_is_detected_without_a_trailing_slash(self) -> None:
        with pytest.raises(TaskGraphError, match="pkg/nested"):
            graph(
                structured(
                    "- [ ] 1.1 **Depends on: none; owns: `pkg/nested`.** A.",
                    "- [ ] 1.2 **Depends on: none; owns: `pkg/nested/b.py`.** B.",
                )
            )

    def test_similar_prefixes_that_are_different_paths_do_not_conflict(self) -> None:
        parsed = graph(
            structured(
                "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** A.",
                "- [ ] 1.2 **Depends on: none; owns: `pkg/a_helper.py`.** B.",
            )
        )

        assert parsed.may_run_together("1.1", "1.2")

    def test_dependency_ordered_tasks_may_share_a_path(self) -> None:
        parsed = graph(
            structured(
                "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** A.",
                "- [ ] 1.2 **Depends on: 1.1; owns: `pkg/a.py`.** B.",
            )
        )

        assert not parsed.may_run_together("1.1", "1.2")

    def test_indirect_dependency_also_permits_shared_ownership(self) -> None:
        parsed = graph(
            structured(
                "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** A.",
                "- [ ] 1.2 **Depends on: 1.1; owns: `pkg/b.py`.** B.",
                "- [ ] 1.3 **Depends on: 1.2; owns: `pkg/a.py`.** C.",
            )
        )

        assert not parsed.may_run_together("1.1", "1.3")


class TestLegacyPlans:
    def test_metadata_free_plans_become_a_safe_sequential_graph(self) -> None:
        parsed = graph(LEGACY)

        assert parsed.legacy
        assert [task.id for task in parsed.tasks] == ["t1", "t2", "t3"]
        assert parsed["t1"].depends_on == ()
        assert parsed["t2"].depends_on == ("t1",)
        assert parsed["t3"].depends_on == ("t2",)
        assert parsed["t1"].title == "Fix the broken login page"
        assert parsed["t1"].done is True

    def test_no_two_legacy_tasks_may_run_together(self) -> None:
        parsed = graph(LEGACY)
        ids = [task.id for task in parsed.tasks]

        assert all(not parsed.may_run_together(a, b) for a in ids for b in ids if a != b)

    def test_legacy_tasks_own_nothing_and_need_no_focused_check(self) -> None:
        parsed = graph("## 1. Fixes\n\n- [ ] Fix the export button\n")

        assert parsed.legacy
        assert parsed["t1"].owned_paths == ()
        assert parsed.check_for("t1") is None

    def test_numbered_legacy_tasks_keep_their_numbers(self) -> None:
        parsed = graph("## 1. Fixes\n\n- [ ] 1.1 Fix login\n- [ ] 1.2 Fix export\n")

        assert [task.id for task in parsed.tasks] == ["1.1", "1.2"]
        assert parsed["1.2"].depends_on == ("1.1",)

    def test_mixing_structured_and_legacy_tasks_is_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="mix"):
            graph(
                structured(
                    "- [ ] 1.1 **Depends on: none; owns: `pkg/a.py`.** A.",
                    "- [ ] 1.2 Just do the thing.",
                )
            )

    def test_a_plan_without_tasks_is_rejected(self) -> None:
        with pytest.raises(TaskGraphError, match="no tasks"):
            graph("## 1. Fixes\n\nNothing to do here.\n")


class TestBounds:
    def test_too_many_tasks_are_rejected(self) -> None:
        rows = [f"- [ ] 1.{n} **Depends on: none; owns: `pkg/a{n}.py`.** A." for n in range(300)]
        with pytest.raises(TaskGraphError, match="too many"):
            graph(structured(*rows))

    def test_an_over_long_owned_path_is_rejected(self) -> None:
        long_path = "pkg/" + "a" * 400 + ".py"
        with pytest.raises(TaskGraphError, match="too long"):
            graph(structured(f"- [ ] 1.1 **Depends on: none; owns: `{long_path}`.** A."))

    def test_too_many_owned_paths_are_rejected(self) -> None:
        owns = ", ".join(f"`pkg/a{n}.py`" for n in range(50))
        with pytest.raises(TaskGraphError, match="owned paths"):
            graph(structured(f"- [ ] 1.1 **Depends on: none; owns: {owns}.** A."))


class TestPlanFiles:
    def test_parse_task_plan_reads_a_file_and_protects_it(self, tmp_path) -> None:
        plan = tmp_path / "tasks.md"
        plan.write_text(STRUCTURED)

        parsed = parse_task_plan(plan, repo_root=tmp_path)

        assert parsed.revision == graph(STRUCTURED).revision
        assert parsed.plan_path == "tasks.md"

    def test_parse_task_plan_rejects_a_task_owning_the_plan(self, tmp_path) -> None:
        plan = tmp_path / "tasks.md"
        plan.write_text(structured("- [ ] 1.1 **Depends on: none; owns: `tasks.md`.** A."))

        with pytest.raises(TaskGraphError, match="protected"):
            parse_task_plan(plan, repo_root=tmp_path)

    def test_the_live_parallel_gowork_plan_parses(self) -> None:
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        plan = repo / "openspec/changes/archive/2026-09-21-parallel-gowork/tasks.md"
        parsed = parse_task_plan(plan, repo_root=repo)

        assert not parsed.legacy
        assert parsed["1.1"].owned_paths == (
            "extensions/feature_workflow/task_graph.py",
            "tests/test_feature_task_graph.py",
        )
        assert parsed["2.1"].depends_on == ("1.1", "1.3")
        assert parsed["5.3"].integration_only is True
        assert parsed["5.3"].owned_paths == ()
        assert parsed.may_run_together("1.1", "4.1")
        assert not parsed.may_run_together("1.1", "1.2")
