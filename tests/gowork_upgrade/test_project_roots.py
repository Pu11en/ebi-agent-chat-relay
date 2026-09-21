"""E2 — a manifest's ``project_path`` is confined to approved roots (pure module).

A secondary plan may only point at a repository the operator approved: the
configured project roots, or the parent folder of the master plan's repository
when nothing is configured. Anything else fails plan validation before a single
git command runs. Each plan may declare its own ``check`` so a secondary
repository is only integrated when its combined result can be verified.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from claude_code_core.gowork_plan import (
    PlanValidationError,
    load_plan_tree,
    parse_plan_tree,
    render_plan_manifest,
    validate_project_roots,
)


def _manifest(*plans: dict[str, object]) -> str:
    body = {"schema_version": 1, "plans": list(plans), "requirements": [], "tasks": []}
    return "# plan\n\n```gowork-plan\n" + json.dumps(body) + "\n```\n"


def _git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """``<tmp>/projects/main`` holds the plan; ``<tmp>/projects/other`` is a sibling."""
    _git_init(tmp_path / "projects" / "main")
    _git_init(tmp_path / "projects" / "other")
    _git_init(tmp_path / "elsewhere" / "secret")
    return tmp_path


def test_secondary_project_outside_every_root_fails_validation(home: Path) -> None:
    source = home / "projects" / "main" / "PLAN.md"
    outside = (home / "elsewhere" / "secret").as_posix()
    source.write_text(
        _manifest(
            {"id": "m", "version": 1, "project_path": "."},
            {"id": "s", "version": 1, "parent_id": "m", "project_path": outside},
        )
    )

    with pytest.raises(PlanValidationError, match="plan 's' project_path .* approved"):
        load_plan_tree(source, roots=[home / "projects"])


def test_secondary_project_inside_a_root_is_accepted(home: Path) -> None:
    source = home / "projects" / "main" / "PLAN.md"
    other = (home / "projects" / "other").as_posix()
    source.write_text(
        _manifest(
            {"id": "m", "version": 1, "project_path": "."},
            {"id": "s", "version": 1, "parent_id": "m", "project_path": other},
        )
    )

    tree = load_plan_tree(source, roots=[home / "projects"])

    assert tree.get("s").project_path == (home / "projects" / "other").resolve()


def test_paths_inside_the_plans_own_repository_need_no_approval(home: Path) -> None:
    source = home / "projects" / "main" / "PLAN.md"
    (home / "projects" / "main" / "sub").mkdir()
    source.write_text(
        _manifest(
            {"id": "m", "version": 1, "project_path": "control"},
            {"id": "s", "version": 1, "parent_id": "m", "project_path": "sub"},
        )
    )

    # The roots do not include the plan's repository at all; its own folders are fine.
    tree = load_plan_tree(source, roots=[home / "nowhere"])

    assert tree.get("s").project_path == (home / "projects" / "main" / "sub").resolve()


def test_home_relative_and_dotdot_paths_are_resolved_before_the_check(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    source = home / "projects" / "main" / "PLAN.md"
    source.write_text(
        _manifest(
            {"id": "m", "version": 1, "project_path": "."},
            {"id": "tilde", "version": 1, "parent_id": "m", "project_path": "~/elsewhere/secret"},
        )
    )
    with pytest.raises(PlanValidationError, match="plan 'tilde'"):
        load_plan_tree(source, roots=[home / "projects"])

    source.write_text(
        _manifest(
            {"id": "m", "version": 1, "project_path": "."},
            {"id": "up", "version": 1, "parent_id": "m", "project_path": "../../elsewhere/secret"},
        )
    )
    with pytest.raises(PlanValidationError, match="plan 'up'"):
        load_plan_tree(source, roots=[home / "projects"])


def test_no_roots_means_no_confinement_for_pure_callers(home: Path) -> None:
    """Export, demo and offline evaluation load plans with ``roots=None``; the cog
    always passes roots. The pure module never reads the environment itself."""
    source = home / "projects" / "main" / "PLAN.md"
    outside = (home / "elsewhere" / "secret").as_posix()
    text = _manifest(
        {"id": "m", "version": 1, "project_path": "."},
        {"id": "s", "version": 1, "parent_id": "m", "project_path": outside},
    )
    source.write_text(text)

    tree = parse_plan_tree(text, source_path=source)

    with pytest.raises(PlanValidationError, match="approved"):
        validate_project_roots(tree, roots=[home / "projects"], source_path=source)
    validate_project_roots(tree, roots=[home / "elsewhere"], source_path=source)


def test_empty_roots_refuse_every_secondary_repository(home: Path) -> None:
    source = home / "projects" / "main" / "PLAN.md"
    other = (home / "projects" / "other").as_posix()
    source.write_text(
        _manifest(
            {"id": "m", "version": 1, "project_path": "."},
            {"id": "s", "version": 1, "parent_id": "m", "project_path": other},
        )
    )

    with pytest.raises(PlanValidationError, match="no approved project root"):
        load_plan_tree(source, roots=[])


def test_per_plan_check_is_optional_validated_and_round_trips(home: Path) -> None:
    source = home / "projects" / "main" / "PLAN.md"
    other = (home / "projects" / "other").as_posix()
    text = _manifest(
        {"id": "m", "version": 1, "project_path": "."},
        {
            "id": "s",
            "version": 1,
            "parent_id": "m",
            "project_path": other,
            "check": "python -c pass",
        },
    )
    source.write_text(text)

    tree = load_plan_tree(source, roots=[home / "projects"])

    assert tree.get("m").check is None
    assert tree.get("s").check == "python -c pass"
    reparsed = parse_plan_tree(render_plan_manifest(tree), source_path=source)
    assert reparsed == tree

    bad = _manifest({"id": "m", "version": 1, "project_path": ".", "check": ["ls"]})
    with pytest.raises(PlanValidationError, match="plan 'm' check must be a non-empty string"):
        parse_plan_tree(bad, source_path=source)
