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


# ---------------------------------------------------------------------------
# The tag in the Discord title — the only place it is read at a glance
# ---------------------------------------------------------------------------

from claude_discord.voice_labels import (  # noqa: E402
    MAX_THREAD_NAME,
    strip_title_tag,
    tagged_title,
    title_tag,
)


def test_a_tag_is_put_at_the_front_of_the_title() -> None:
    assert tagged_title("📂 ebi-agent-chat-relay", "bravo") == "[bravo] 📂 ebi-agent-chat-relay"


def test_reapplying_a_tag_never_stacks_it() -> None:
    once = tagged_title("📂 repo", "bravo")
    assert tagged_title(once, "bravo") == once


def test_a_changed_tag_replaces_the_old_one() -> None:
    assert tagged_title("[alpha] 📂 repo", "bravo") == "[bravo] 📂 repo"


def test_a_tag_can_be_read_back_and_removed() -> None:
    assert title_tag("[charlie] 📂 repo") == "charlie"
    assert title_tag("📂 repo") is None
    assert strip_title_tag("[charlie] 📂 repo") == "📂 repo"


def test_no_tag_leaves_the_title_alone() -> None:
    assert tagged_title("📂 repo", None) == "📂 repo"
    assert tagged_title("[alpha] 📂 repo", None) == "📂 repo"


def test_the_name_is_truncated_not_the_tag() -> None:
    """A title missing its last word is usable; a tag missing a letter is not."""
    result = tagged_title("x" * 150, "november")

    assert len(result) <= MAX_THREAD_NAME
    assert result.startswith("[november] ")
    assert title_tag(result) == "november"


# ---------------------------------------------------------------------------
# The rename itself
# ---------------------------------------------------------------------------

import tempfile  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

import discord  # noqa: E402
import pytest  # noqa: E402

from claude_discord.database.models import init_db  # noqa: E402
from claude_discord.database.notification_repo import NotificationRepository  # noqa: E402
from claude_discord.database.settings_repo import SettingsRepository  # noqa: E402
from claude_discord.ext.api_server import ApiServer  # noqa: E402


def _thread(thread_id: int, name: str, *, archived: bool = False, locked: bool = False):
    t = MagicMock(spec=discord.Thread)
    t.id = thread_id
    t.name = name
    t.archived = archived
    t.locked = locked
    t.edit = AsyncMock()
    return t


@pytest.fixture
async def api(tmp_path) -> ApiServer:
    import os

    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(db)
    repo = NotificationRepository(db)
    await repo.init_db()
    server = ApiServer(repo=repo, bot=MagicMock(), host="127.0.0.1", port=0)
    server.settings_repo = SettingsRepository(db)
    yield server
    os.unlink(db)


async def test_the_tag_is_written_into_the_thread_title(api: ApiServer) -> None:
    thread = _thread(1, "📂 ebi-agent-chat-relay")
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": "📂 ebi-agent-chat-relay"}]

    await api._apply_voice_labels(views)

    thread.edit.assert_awaited_once_with(name="[alpha] 📂 ebi-agent-chat-relay")
    assert views[0]["voice_label"] == "alpha"
    assert views[0]["thread_name"] == "[alpha] 📂 ebi-agent-chat-relay"


async def test_a_title_that_already_shows_its_tag_is_left_alone(api: ApiServer) -> None:
    """Discord rate-limits renames hard, so this must be once per thread."""
    thread = _thread(1, "[alpha] 📂 repo")
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": "[alpha] 📂 repo"}]

    await api._apply_voice_labels(views)
    await api._apply_voice_labels(views)

    thread.edit.assert_not_awaited()


async def test_an_archived_thread_keeps_its_title(api: ApiServer) -> None:
    thread = _thread(1, "📂 repo", archived=True)
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": "📂 repo"}]

    await api._apply_voice_labels(views)

    thread.edit.assert_not_awaited()
    assert views[0]["voice_label"] == "alpha"  # still addressable by tag


async def test_a_rename_failure_never_fails_the_request(api: ApiServer) -> None:
    thread = _thread(1, "📂 repo")
    thread.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited"))
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": "📂 repo"}]

    await api._apply_voice_labels(views)

    assert views[0]["voice_label"] == "alpha"
    assert views[0]["thread_name"] == "📂 repo"


async def test_a_full_set_is_retitled_across_calls_not_all_at_once(api: ApiServer) -> None:
    threads = {i: _thread(i, f"📂 repo-{i}") for i in range(1, 11)}
    api.bot.get_channel.side_effect = lambda tid: threads[tid]
    views = [{"thread_id": i, "thread_name": f"📂 repo-{i}"} for i in range(1, 11)]

    await api._apply_voice_labels(views)

    assert sum(t.edit.await_count for t in threads.values()) == api._MAX_RETITLES_PER_CALL


async def test_tags_persist_so_a_title_is_not_rewritten_after_a_restart(api: ApiServer) -> None:
    api.bot.get_channel.return_value = None
    await api._apply_voice_labels([{"thread_id": 7, "thread_name": "📂 repo"}])

    stored = await api.settings_repo.get_all()
    assert stored["voice_label:7"] == "alpha"
