"""T20 — blocker questions are durable and tied to one build, task and attempt.

Several blockers in one thread survive reopening the ledger; opening the same
blocker twice returns the first; a posted message id maps back to exactly one
blocker; resolving is once-only; recovery re-posts only what never reached
Discord.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_core.gowork_blockers import BlockerLedger


def _ledger(tmp_path: Path) -> BlockerLedger:
    return BlockerLedger(tmp_path / "blockers.json")


def test_several_blockers_in_one_thread_survive_reopen(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    a = ledger.open(
        "b1",
        "product.catalog-api",
        "b1:product.catalog-api:2",
        plan_id="product",
        plan_version=2,
        channel_id=10,
        question="Tests fail: retry, skip or tell me what to change?",
    )
    b = ledger.open(
        "b1",
        "marketing.launch-post",
        "b1:marketing.launch-post:1",
        plan_id="marketing",
        plan_version=1,
        channel_id=10,
        question="Which channel should the post go to?",
    )
    c = ledger.open(
        "b2",
        "website.catalog-page",
        "b2:website.catalog-page:1",
        plan_id="website",
        plan_version=4,
        channel_id=10,
        question="Another build's question",
    )
    ledger.note_posted(a.blocker_id, channel_id=10, message_id=1001)
    ledger.note_posted(b.blocker_id, channel_id=10, message_id=1002)

    again = _ledger(tmp_path)
    assert [x.blocker_id for x in again.unresolved()] == [a.blocker_id, b.blocker_id, c.blocker_id]
    assert [x.blocker_id for x in again.unresolved(build_id="b1")] == [a.blocker_id, b.blocker_id]
    assert again.by_message(1001) == again.get(a.blocker_id)
    assert again.by_message(1002).task_id == "marketing.launch-post"  # type: ignore[union-attr]
    assert again.by_message(9999) is None
    assert again.get(a.blocker_id).build_id == "b1" and again.get(c.blocker_id).build_id == "b2"  # type: ignore[union-attr]


def test_opening_the_same_blocker_twice_returns_the_first(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    first = ledger.open(
        "b1", "t", "b1:t:1", plan_id="p", plan_version=1, channel_id=10, question="q?"
    )
    ledger.note_posted(first.blocker_id, channel_id=10, message_id=5)
    second = ledger.open(
        "b1", "t", "b1:t:1", plan_id="p", plan_version=1, channel_id=10, question="q again?"
    )
    assert second.blocker_id == first.blocker_id and second.message_id == 5
    assert len(ledger.unresolved()) == 1
    later = ledger.open(
        "b1", "t", "b1:t:2", plan_id="p", plan_version=1, channel_id=10, question="q?"
    )
    assert later.blocker_id != first.blocker_id  # a new attempt is a new question


def test_resolving_is_once_only_and_keeps_the_answer(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    blocker = ledger.open(
        "b1", "t", "b1:t:1", plan_id="p", plan_version=1, channel_id=10, question="q?"
    )
    assert ledger.resolve(blocker.blocker_id, answer="retry", by_user_id=42, reply_message_id=77)
    assert not ledger.resolve(blocker.blocker_id, answer="skip", by_user_id=42, reply_message_id=78)
    resolved = _ledger(tmp_path).get(blocker.blocker_id)
    assert resolved is not None and resolved.resolved and resolved.answer == "retry"
    assert resolved.resolved_by == 42 and resolved.reply_message_id == 77
    assert _ledger(tmp_path).unresolved() == ()


def test_recovery_reposts_only_what_never_reached_discord(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    posted = ledger.open(
        "b1", "a", "b1:a:1", plan_id="p", plan_version=1, channel_id=10, question="a?"
    )
    ledger.note_posted(posted.blocker_id, channel_id=10, message_id=1)
    lost = ledger.open(
        "b1", "b", "b1:b:1", plan_id="p", plan_version=1, channel_id=10, question="b?"
    )
    done = ledger.open(
        "b1", "c", "b1:c:1", plan_id="p", plan_version=1, channel_id=10, question="c?"
    )
    ledger.resolve(done.blocker_id, answer="skip", by_user_id=1, reply_message_id=2)
    assert [x.blocker_id for x in _ledger(tmp_path).unposted(build_id="b1")] == [lost.blocker_id]


def test_a_broken_file_starts_empty_rather_than_crashing(tmp_path: Path) -> None:
    (tmp_path / "blockers.json").write_text("{not json", encoding="utf-8")
    assert _ledger(tmp_path).unresolved() == ()
