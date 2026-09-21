"""T26 — the planning prompt is refined, not replaced.

These are static examples checked against the repo's own plans: they show
what the prompt preserves and asks for. They do not measure how a live
model behaves.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_core.gowork_prompts import (
    COMMUNICATION_RULES,
    PLANNER_RULES,
    planning_prompt,
)
from claude_code_core.task_loop import goal_interview_prompt

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


def test_the_report_plan_keeps_its_real_chain_and_promises_no_parallelism() -> None:
    plan = REPO / "local-plans" / "generation-wealth-feedback-gowork.md"
    text = planning_prompt(
        plan.read_text(encoding="utf-8"),
        plan_path=plan,
        known_answers=[],
        project_facts=["six tasks, each reading the previous one's output"],
    )
    for step in (
        "transcript source inventory",
        "Extract everything",
        "answerable themes",
        "Draft the long feedback report",
        "Create the first PDF",
        "Review the PDF",
    ):
        assert step in text  # the agreed scope travels with the prompt
    assert "each reading the previous one's output" in text
    assert "must wait for another result" in text  # the planner records dependencies
    assert "Do not add workers just to increase their count" in text
    assert "whole plan" in text and "do not automatically send" in text.lower()


def test_the_business_plan_keeps_every_project_and_asks_one_question() -> None:
    plan = FIXTURES / "validated-plan.md"
    text = planning_prompt(
        plan.read_text(encoding="utf-8"),
        plan_path=plan,
        known_answers=[("Which comes first?", "the product catalog contract")],
        project_facts=["four projects: control, product, website, marketing"],
    )
    for word in ("product", "website", "marketing"):
        assert word in text
    assert "ONE question" in text and "lettered choices" in text
    assert "Settled already (never ask these again):" in text
    assert 'They said: "the product catalog contract"' in text
    assert "recommendation first" in text


def test_a_resumed_answer_is_not_asked_again_and_capacity_stays_automatic() -> None:
    plan = FIXTURES / "validated-plan.md"
    text = planning_prompt(
        plan.read_text(encoding="utf-8"),
        plan_path=plan,
        known_answers=[
            ("How many workers at once?", "as many as the computer can handle — automatic"),
            ("Which comes first?", "the product catalog contract"),
        ],
        project_facts=[],
        unclear=True,
    )
    assert "How many workers at once?" in text and "automatic" in text
    assert "the running system determines available capacity" in text
    assert "one concrete example from this project" in text  # asked for when unclear
    assert "hypothetical" in text  # and labelled as such
    assert text.count("Settled already") == 1


def test_the_goal_interview_carries_the_same_communication_rules() -> None:
    text = goal_interview_prompt(Path("/p/PLAN.md"), Path("/p/PLAN.progress.md"), [])
    assert "Never ask what these already answer" in text  # the existing flow is intact
    for rule in (
        "understandable without rereading",
        "concrete consequence",
        "Do not send full-plan cards",
    ):
        assert rule in text  # the shared rules were added, not a new ritual
    assert COMMUNICATION_RULES.strip() in text


def test_the_rules_are_repo_owned_text_not_installed_anywhere() -> None:
    assert "Continue the user's existing planning conversation" in PLANNER_RULES
    assert "preserve settled answers" in PLANNER_RULES
    for path in (Path.home() / ".claude" / "CLAUDE.md", Path.home() / ".codex" / "AGENTS.md"):
        if path.is_file():
            assert "Continue the user's existing planning conversation" not in path.read_text(
                encoding="utf-8", errors="replace"
            )
