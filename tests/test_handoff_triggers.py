"""Tests for natural-language DrewAI handoff triggers."""

from __future__ import annotations

import pytest

from claude_discord.handoff_triggers import parse_drewai_lookup_trigger


@pytest.mark.parametrize(
    ("text", "query"),
    [
        (
            "use DrewAI to find info on the Pinterest skill or process",
            "the Pinterest skill or process",
        ),
        (
            "Use drew ai to find info on the process that lets a session use Pinterest "
            "to find images and display them cheap",
            "the process that lets a session use Pinterest to find images and display them cheap",
        ),
        (
            "ask DrewAI to search Drew's projects for better Pinterest keyword rules",
            "better Pinterest keyword rules",
        ),
        (
            "can you ask drewai to look in drew's projects for the realpage folder",
            "the realpage folder",
        ),
        (
            "use DrewAI to find the visual picker skill",
            "the visual picker skill",
        ),
    ],
)
def test_parse_drewai_lookup_trigger_accepts_simple_phrases(text: str, query: str) -> None:
    trigger = parse_drewai_lookup_trigger(text)

    assert trigger is not None
    assert trigger.agent_id == "drewai"
    assert trigger.intent == "project_lookup"
    assert trigger.query == query


@pytest.mark.parametrize(
    "text",
    [
        "",
        "DrewAI is the main bot",
        "use the Pinterest skill here",
        "ask David to find info on Pinterest",
        "I might ask DrewAI later",
        "use DrewAI",
    ],
)
def test_parse_drewai_lookup_trigger_ignores_non_requests(text: str) -> None:
    assert parse_drewai_lookup_trigger(text) is None


def test_parse_drewai_lookup_trigger_limits_query_size() -> None:
    text = "use DrewAI to find info on " + ("x" * 900)

    trigger = parse_drewai_lookup_trigger(text)

    assert trigger is not None
    assert len(trigger.query) == 500
