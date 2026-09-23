"""The close hint: a session told to close out must be able to do it itself."""

from __future__ import annotations

from claude_discord.close_hint import build_close_hint


def test_hint_names_the_endpoint_the_thread_and_the_actor():
    hint = build_close_hint(thread_id=555, actor_id=42)
    assert "/api/threads/555/close" in hint
    assert '"actor": "42"' in hint
    assert "$CCDB_API_SECRET" in hint


def test_hint_says_it_only_applies_when_asked():
    hint = build_close_hint(thread_id=1, actor_id=2)
    assert "asks you to close" in hint
    assert "nothing is deleted" in hint.lower()


def test_hint_is_small_enough_to_inject_every_turn():
    assert len(build_close_hint(thread_id=1, actor_id=2)) < 700
