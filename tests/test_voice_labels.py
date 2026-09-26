"""Tests for short spoken tags (thread → one phonetic word)."""

from __future__ import annotations

from claude_discord.voice_labels import SPOKEN_LABELS, assign_labels


def test_tags_are_handed_out_in_order() -> None:
    labels, new = assign_labels([10, 20, 30], {})

    assert labels == {10: "alpha", 20: "bravo", 30: "charlie"}
    assert new == labels


def test_an_existing_tag_is_never_reshuffled() -> None:
    """The thread called bravo this morning is still bravo tonight."""
    labels, new = assign_labels([99, 10, 20], {10: "alpha", 20: "bravo"})

    assert labels[10] == "alpha"
    assert labels[20] == "bravo"
    assert labels[99] == "charlie"
    assert new == {99: "charlie"}


def test_only_the_new_assignments_are_reported() -> None:
    _, new = assign_labels([10], {10: "alpha"})
    assert new == {}


def test_a_tag_is_recycled_once_its_thread_leaves_the_visible_set() -> None:
    """26 tags cannot cover hundreds of archived threads."""
    labels, _ = assign_labels([50], {10: "alpha", 20: "bravo"})

    assert labels == {50: "alpha"}


def test_a_stored_tag_for_an_invisible_thread_is_not_returned() -> None:
    labels, _ = assign_labels([10], {10: "alpha", 20: "bravo"})
    assert labels == {10: "alpha"}


def test_more_threads_than_tags_leaves_the_remainder_untagged() -> None:
    """Reusing a tag would deliver a command to the wrong thread."""
    ids = list(range(1, len(SPOKEN_LABELS) + 4))
    labels, _ = assign_labels(ids, {})

    assert len(labels) == len(SPOKEN_LABELS)
    assert len(set(labels.values())) == len(SPOKEN_LABELS)


def test_every_tag_is_one_lowercase_word() -> None:
    for label in SPOKEN_LABELS:
        assert label.isalpha() and label.islower()
    assert len(set(SPOKEN_LABELS)) == len(SPOKEN_LABELS)
