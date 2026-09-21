"""Tests for discord_ui.plan_card — the fixed four-slot plan as Markdown."""

from __future__ import annotations

from claude_discord.discord_ui.plan_card import _MAX_FIELD_CHARS, _MAX_STEPS, plan_markdown


class TestPlanMarkdown:
    def test_all_slots_become_sections(self) -> None:
        md = plan_markdown(
            {
                "goal": "G",
                "exists": "E",
                "steps": [{"do": "D1", "outcome": "O1"}, "D2"],
                "done": "X",
                "notes": "N",
            }
        )
        assert md.startswith("# Plan: G")
        for part in ("E", "1. D1", "O1", "2. D2", "X", "N"):
            assert part in md

    def test_missing_slots_are_visible(self) -> None:
        md = plan_markdown({})
        assert "goal missing" in md
        assert "no steps provided" in md
        assert "done-when missing" in md

    def test_non_list_steps_do_not_raise(self) -> None:
        assert "no steps provided" in plan_markdown({"steps": "just a string"})

    def test_long_fields_are_clipped(self) -> None:
        md = plan_markdown({"goal": "x" * (_MAX_FIELD_CHARS + 50)})
        assert "…(truncated)" in md

    def test_step_count_is_capped_with_an_overflow_note(self) -> None:
        md = plan_markdown({"steps": [f"s{i}" for i in range(_MAX_STEPS + 3)]})
        assert "+3 more steps" in md
