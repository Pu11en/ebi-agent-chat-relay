"""T12 — a worker gets a compact, persisted handoff, not the chat.

The handoff names the attempt, the assignment (outcome, ownership, inputs,
expected output, acceptance check), the build's goal, the plan's saved
decisions and the evidence behind each required input — and nothing else.
Delivering it twice for the same attempt reuses the same file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.gowork_handoff import (
    WorkerHandoff,
    build_handoff,
    persist_handoff,
    plan_decisions,
    render_worker_prompt,
)
from claude_code_core.gowork_plan import load_plan_tree
from claude_code_core.gowork_state import BuildState, open_build_state

FIXTURES = Path(__file__).parent / "fixtures"
API, PAGE = "product.catalog-api", "website.catalog-page"


@pytest.fixture
def plan(tmp_path: Path) -> Path:
    source = tmp_path / "master-plan.md"
    text = (FIXTURES / "validated-plan.md").read_text(encoding="utf-8")
    text = "Goal: launch the business with a live catalog\n\n" + text
    text += "\n## Decisions\n\n- Ship the catalog as JSON, not XML\n- No paid services\n"
    source.write_text(text, encoding="utf-8")
    return source


@pytest.fixture
def state(plan: Path, tmp_path: Path) -> BuildState:
    return open_build_state(tmp_path / "build.json", load_plan_tree(plan), build_id="thread-9")


def test_plan_decisions_are_read_from_the_decisions_section(plan: Path) -> None:
    assert plan_decisions(plan.read_text(encoding="utf-8")) == (
        "Ship the catalog as JSON, not XML",
        "No paid services",
    )
    assert plan_decisions("# nothing here\n- [ ] a task\n") == ()


def test_handoff_carries_the_assignment_goal_decisions_and_input_evidence(
    plan: Path, state: BuildState
) -> None:
    attempt = state.begin(API)
    state.submit_result(API, attempt.attempt_id, commit="abc123", checks=["pytest: 12 passed"])
    state.accept(API, attempt.attempt_id)
    state.begin(PAGE)

    handoff = build_handoff(state, PAGE, plan_text=plan.read_text(encoding="utf-8"))

    assert handoff.attempt_id == "thread-9:website.catalog-page:1"
    assert handoff.task_id == PAGE and handoff.plan_id == "website"
    assert handoff.goal == "launch the business with a live catalog"
    assert handoff.decisions == ("Ship the catalog as JSON, not XML", "No paid services")
    assert handoff.owned_files == ("src/pages/catalog.tsx",)
    assert handoff.required_inputs == ("product.catalog-api: versioned catalog contract",)
    assert handoff.input_evidence == (
        "product.catalog-api: accepted at abc123 — pytest: 12 passed",
    )
    assert handoff.expected_output and handoff.acceptance_check
    assert "chat" not in json.dumps(handoff.to_json()).lower()
    assert WorkerHandoff.from_json(handoff.to_json()) == handoff


def test_duplicate_delivery_reuses_the_same_persisted_attempt(
    plan: Path, state: BuildState, tmp_path: Path
) -> None:
    state.begin(API)
    handoff = build_handoff(state, API, plan_text=plan.read_text(encoding="utf-8"))
    first = persist_handoff(tmp_path / "handoffs", handoff)
    again = persist_handoff(tmp_path / "handoffs", handoff)

    assert first == again and first.name == "thread-9_product.catalog-api_1.json"
    assert len(list((tmp_path / "handoffs").iterdir())) == 1
    assert WorkerHandoff.from_json(json.loads(first.read_text(encoding="utf-8"))) == handoff

    state.block(API, "sent back")
    state.retry(API)
    state.begin(API)
    second = persist_handoff(
        tmp_path / "handoffs", build_handoff(state, API, plan_text=plan.read_text(encoding="utf-8"))
    )
    assert second != first and second.name.endswith("_2.json")


def test_the_prompt_is_rendered_from_the_handoff_alone(
    plan: Path, state: BuildState, tmp_path: Path
) -> None:
    state.begin(API)
    handoff = build_handoff(state, API, plan_text=plan.read_text(encoding="utf-8"))
    cwd, saved = tmp_path / "work" / "product", tmp_path / "h.json"
    prompt = render_worker_prompt(handoff, cwd=cwd, handoff_path=saved)

    for needle in (
        handoff.outcome,
        "src/catalog.py",
        "catalog-schema",
        handoff.acceptance_check,
        "Ship the catalog as JSON, not XML",
        "REQ-CATALOG",
        str(cwd),
        str(saved),
        "DONE",
        "STUCK:",
    ):
        assert needle in prompt
    assert "history" not in prompt.lower() or "chat history" not in prompt.lower()


def test_a_handoff_needs_a_running_attempt(plan: Path, state: BuildState) -> None:
    with pytest.raises(ValueError, match="pending"):
        build_handoff(state, API, plan_text="")
