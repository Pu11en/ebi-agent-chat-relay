"""Tests for short spoken tags (thread → one phonetic word)."""

from __future__ import annotations

from claude_discord.voice_labels import (
    LABEL_ALIASES,
    SPOKEN_LABELS,
    assign_labels,
    heard_as,
)

#: The first tag the pool hands out. These tests are about the mechanism,
#: not about which words Drew picked, so they name it rather than spell it.
FIRST = SPOKEN_LABELS[0]


def test_tags_are_handed_out_in_order() -> None:
    labels, new, _ = assign_labels([10, 20, 30], {})

    assert labels == dict(zip([10, 20, 30], SPOKEN_LABELS[:3], strict=True))
    assert new == labels


def test_an_existing_tag_is_never_reshuffled() -> None:
    """The thread called bravo this morning is still bravo tonight."""
    first, second, third = SPOKEN_LABELS[:3]
    labels, new, _ = assign_labels([99, 10, 20], {10: first, 20: second})

    assert labels[10] == first
    assert labels[20] == second
    assert labels[99] == third
    assert new == {99: third}


def test_only_the_new_assignments_are_reported() -> None:
    _, new, _ = assign_labels([10], {10: SPOKEN_LABELS[0]})
    assert new == {}


def test_a_tag_is_kept_after_its_thread_scrolls_out_of_view() -> None:
    """A tag is a word Drew learned; it must not change meaning under him."""
    first, second, third = SPOKEN_LABELS[:3]
    labels, new, released = assign_labels([50], {10: first, 20: second})

    assert labels == {50: third}, "the first two are still promised"
    assert released == set()
    assert new == {50: third}


def test_a_thread_coming_back_into_view_gets_its_own_tag_again() -> None:
    first, second = SPOKEN_LABELS[:2]
    labels, new, _ = assign_labels([10], {10: first, 20: second})

    assert labels == {10: first}
    assert new == {}


def test_a_stored_tag_for_an_invisible_thread_is_not_returned() -> None:
    first, second = SPOKEN_LABELS[:2]
    labels, _, _ = assign_labels([10], {10: first, 20: second})
    assert labels == {10: first}


def test_the_oldest_absent_thread_gives_up_its_tag_when_the_pool_runs_dry() -> None:
    """Snowflake ids are chronological, so the smallest is the stalest tag."""
    stored = {100 + i: label for i, label in enumerate(SPOKEN_LABELS)}
    labels, new, released = assign_labels([9999], stored)

    assert labels == {9999: SPOKEN_LABELS[0]}, "the oldest absent thread held it"
    assert released == {100}
    assert new == {9999: SPOKEN_LABELS[0]}


def test_a_visible_thread_never_has_its_tag_taken() -> None:
    stored = {100 + i: label for i, label in enumerate(SPOKEN_LABELS)}
    visible = sorted(stored) + [9999]
    labels, _, released = assign_labels(visible, stored)

    assert released == set()
    assert 9999 not in labels, "untagged rather than stealing a live tag"
    assert all(labels[tid] == stored[tid] for tid in stored)


def test_more_threads_than_tags_leaves_the_remainder_untagged() -> None:
    """Reusing a tag would deliver a command to the wrong thread."""
    ids = list(range(1, len(SPOKEN_LABELS) + 4))
    labels, _, released = assign_labels(ids, {})

    assert len(labels) == len(SPOKEN_LABELS)
    assert len(set(labels.values())) == len(SPOKEN_LABELS)
    assert released == set()


def test_every_tag_is_one_lowercase_word() -> None:
    for label in SPOKEN_LABELS:
        assert label.isalpha() and label.islower()
    assert len(set(SPOKEN_LABELS)) == len(SPOKEN_LABELS)


# ---------------------------------------------------------------------------
# The tag in the Discord title — the only place it is read at a glance
# ---------------------------------------------------------------------------

#: The first tag the pool hands out — these tests are about the mechanism,
#: not about which words Drew picked.

from claude_discord.voice_labels import (  # noqa: E402
    MAX_THREAD_NAME,
    strip_title_tag,
    tagged_title,
    title_tag,
)


def test_a_tag_is_put_at_the_front_of_the_title() -> None:
    assert tagged_title("📂 ebi-agent-chat-relay", "zoro") == "[zoro] 📂 ebi-agent-chat-relay"


def test_reapplying_a_tag_never_stacks_it() -> None:
    once = tagged_title("📂 repo", "zoro")
    assert tagged_title(once, "zoro") == once


def test_a_changed_tag_replaces_the_old_one() -> None:
    assert tagged_title("[luffy] 📂 repo", "zoro") == "[zoro] 📂 repo"


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
from claude_discord.voice_tags import MAX_RETITLES_PER_CALL  # noqa: E402


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

    thread.edit.assert_awaited_once_with(name=f"[{FIRST}] 📂 ebi-agent-chat-relay")
    assert views[0]["voice_label"] == FIRST
    assert views[0]["thread_name"] == f"[{FIRST}] 📂 ebi-agent-chat-relay"


async def test_a_title_that_already_shows_its_tag_is_left_alone(api: ApiServer) -> None:
    """Discord rate-limits renames hard, so this must be once per thread."""
    thread = _thread(1, f"[{FIRST}] 📂 repo")
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": f"[{FIRST}] 📂 repo"}]

    await api._apply_voice_labels(views)
    await api._apply_voice_labels(views)

    thread.edit.assert_not_awaited()


async def test_an_archived_thread_keeps_its_title(api: ApiServer) -> None:
    thread = _thread(1, "📂 repo", archived=True)
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": "📂 repo"}]

    await api._apply_voice_labels(views)

    thread.edit.assert_not_awaited()
    assert views[0]["voice_label"] == FIRST  # still addressable by tag


async def test_a_rename_failure_never_fails_the_request(api: ApiServer) -> None:
    thread = _thread(1, "📂 repo")
    thread.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited"))
    api.bot.get_channel.return_value = thread
    views = [{"thread_id": 1, "thread_name": "📂 repo"}]

    await api._apply_voice_labels(views)

    assert views[0]["voice_label"] == FIRST
    assert views[0]["thread_name"] == "📂 repo"


async def test_the_retitling_is_spread_across_calls_not_done_all_at_once(
    api: ApiServer,
) -> None:
    """The cap now bounds stale-tag *removal*, which is the case that can be large.

    It used to bound handing tags out, but the pool is ten and the cap is twelve,
    so it can no longer bind there. Cleaning old tags off titles still can: every
    thread that was tagged before a pool change has a word in its title it no
    longer owns, and resolving each one costs a Discord call.
    """
    total = MAX_RETITLES_PER_CALL + 6
    threads = {i: _thread(i, f"[bravo] 📂 repo-{i}") for i in range(1, total + 1)}
    api.bot.get_channel.side_effect = lambda tid: threads[tid]
    # No tag is available for any of them, so each needs its stale one removed.
    for i in range(1, len(SPOKEN_LABELS) + 1):
        await api.settings_repo.set(f"voice_label:{10_000 + i}", SPOKEN_LABELS[i - 1])
    views = [{"thread_id": i, "thread_name": f"[bravo] 📂 repo-{i}"} for i in range(1, total + 1)]
    views += [
        {"thread_id": 10_000 + i, "thread_name": f"t{i}"} for i in range(1, len(SPOKEN_LABELS) + 1)
    ]

    await api._apply_voice_labels(views)

    assert sum(t.edit.await_count for t in threads.values()) == MAX_RETITLES_PER_CALL


async def test_tags_persist_so_a_title_is_not_rewritten_after_a_restart(api: ApiServer) -> None:
    api.bot.get_channel.return_value = None
    await api._apply_voice_labels([{"thread_id": 7, "thread_name": "📂 repo"}])

    stored = await api.settings_repo.get_all()
    assert stored["voice_label:7"] == FIRST


async def test_a_spawn_view_gets_its_tag_without_the_whole_session_list(
    api: ApiServer,
) -> None:
    """A thread opened by voice must be addressable at once, not next poll."""
    thread = _thread(4242, "the aldus")
    api.bot.get_channel.return_value = thread
    view = [{"thread_id": 4242, "thread_name": "the aldus"}]

    await api._apply_voice_labels(view)

    assert view[0]["voice_label"] == FIRST
    assert view[0]["thread_name"] == f"[{FIRST}] the aldus"
    thread.edit.assert_awaited_once_with(name=f"[{FIRST}] the aldus")


async def test_a_spawned_thread_does_not_steal_a_live_tag(api: ApiServer) -> None:
    await api.settings_repo.set("voice_label:1", FIRST)
    thread = _thread(4242, "the aldus")
    api.bot.get_channel.return_value = thread
    view = [{"thread_id": 4242, "thread_name": "the aldus"}]

    await api._apply_voice_labels(view)

    assert view[0]["voice_label"] == SPOKEN_LABELS[1]


class TestLabelKey:
    """The settings key a tag is stored under is shared, so it lives with the tags.

    It used to be private to api_server.py, which meant anything else that had
    to move a tag — the context handoff, for one — had to re-spell the string.
    """

    def test_key_is_stable_and_round_trips(self) -> None:
        from claude_discord.voice_labels import VOICE_LABEL_PREFIX, label_key, thread_id_from_key

        assert label_key(42) == f"{VOICE_LABEL_PREFIX}42"
        assert thread_id_from_key(label_key(42)) == 42

    def test_a_foreign_key_is_not_a_tag(self) -> None:
        from claude_discord.voice_labels import thread_id_from_key

        assert thread_id_from_key("claude_model") is None
        assert thread_id_from_key("voice_label:not-a-number") is None


# ---------------------------------------------------------------------------
# The tag pool, and what the recogniser does to it
# ---------------------------------------------------------------------------


class TestThePool:
    """The words are Drew's choice; being mutually unmistakable is the constraint."""

    def test_every_word_in_the_pool_is_distinct(self) -> None:
        assert len(set(SPOKEN_LABELS)) == len(SPOKEN_LABELS)

    def test_no_two_tags_start_with_the_same_two_letters(self) -> None:
        """Two tags an edit apart is how a command lands in the wrong repository."""
        starts = [label[:2] for label in SPOKEN_LABELS]
        assert len(set(starts)) == len(starts), sorted(s for s in starts if starts.count(s) > 1)

    def test_a_tag_is_not_an_everyday_english_word(self) -> None:
        """A tag that occurs in conversation addresses a thread by accident."""
        common = {
            "the",
            "and",
            "law",
            "ace",
            "brook",
            "robin",
            "smoker",
            "dragon",
            "carrot",
            "bear",
            "king",
            "queen",
            "pudding",
            "stone",
        }
        assert not (set(SPOKEN_LABELS) & common)


class TestMishearings:
    """The recogniser writes what it knows, not what was said.

    "Aldus" came through as "oldest" and always will (see folders.mjs). A name
    outside the model's vocabulary gets substituted the same way, so each tag
    carries the substitutions actually seen for it. They are matched exactly —
    a fuzzy tag match is how a command reaches the wrong thread, since the
    consonant skeleton of a four-letter name is two characters long.
    """

    def test_a_mishearing_resolves_to_its_tag(self) -> None:
        assert heard_as("lucy") == "luffy"
        assert heard_as("zorro") == "zoro"

    def test_a_tag_resolves_to_itself(self) -> None:
        for label in SPOKEN_LABELS:
            assert heard_as(label) == label

    def test_something_that_is_not_a_tag_resolves_to_nothing(self) -> None:
        assert heard_as("aldus") is None
        assert heard_as("") is None

    def test_matching_ignores_case_and_punctuation(self) -> None:
        assert heard_as("Luffy,") == "luffy"

    def test_every_alias_points_at_a_real_tag(self) -> None:
        assert set(LABEL_ALIASES.values()) <= set(SPOKEN_LABELS)

    def test_no_alias_is_itself_a_tag(self) -> None:
        """Otherwise one tag quietly swallows another."""
        assert not (set(LABEL_ALIASES) & set(SPOKEN_LABELS))


class TestRetiredTags:
    """Changing the pool has to reach the threads that already have a tag.

    A tag is held for as long as possible, which is right while the pool is
    fixed and wrong the moment it changes: without this, every thread tagged
    before the change keeps a word that is no longer in the list, and the two
    naming schemes coexist for as long as those threads live.
    """

    def test_a_tag_outside_the_pool_is_replaced(self) -> None:
        labels, new, released = assign_labels([10], {10: "bravo"})

        assert labels[10] == SPOKEN_LABELS[0]
        assert new == {10: SPOKEN_LABELS[0]}
        assert 10 in released or new[10] != "bravo"

    def test_a_retired_tag_does_not_hold_a_slot(self) -> None:
        """All 26 old names stored, all 26 new ones must still be available."""
        retired = [
            "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf",
            "hotel", "india", "juliet", "kilo", "lima", "mike", "november",
            "oscar", "papa", "quebec", "romeo", "sierra", "tango", "uniform",
            "victor", "whiskey", "xray", "yankee", "zulu",
        ]  # fmt: skip
        stored = {100 + i: name for i, name in enumerate(retired)}
        visible = sorted(stored)
        labels, _, _ = assign_labels(visible, stored)

        assert set(labels.values()) == set(SPOKEN_LABELS[: len(visible)])


class TestAliasesReachTheVoiceLayer:
    """The matching happens in the voice companion, so it has to be told.

    The pool and its mishearings are owned here — ccdb assigns the tags — so the
    alias list travels with the session rather than being spelled a second time
    in JavaScript, where it would drift the first time a name changed.
    """

    def test_a_tag_carries_the_words_it_gets_confused_with(self) -> None:
        from claude_discord.voice_labels import aliases_for

        assert "lucy" in aliases_for("luffy")
        assert "luffy" not in aliases_for("luffy"), "the tag itself is sent separately"

    def test_a_tag_with_no_known_mishearing_carries_none(self) -> None:
        from claude_discord.voice_labels import aliases_for

        assert aliases_for("nosuchtag") == ()

    def test_every_alias_reaches_exactly_one_tag(self) -> None:
        from claude_discord.voice_labels import aliases_for

        for label in SPOKEN_LABELS:
            for alias in aliases_for(label):
                assert heard_as(alias) == label


class TestAClosedSessionHoldsNoTag:
    """A tag on a finished session is worse than no tag.

    Two things went wrong with it. The pool is 26 long and closed sessions were
    holding most of it, so live threads went untagged. And the tag still
    resolved: saying it delivered an instruction into a session that was over,
    which answered "this session is already closed" — the speaker's own word
    reaching a corpse.
    """

    async def test_a_closed_session_is_not_given_a_tag(self, api: ApiServer) -> None:
        api.bot.get_channel.return_value = None
        views = [
            {"thread_id": 1, "thread_name": "done", "closed": True},
            {"thread_id": 2, "thread_name": "live", "closed": False},
        ]

        await api._apply_voice_labels(views)

        assert views[0]["voice_label"] is None
        assert views[1]["voice_label"] == FIRST, "the live thread gets the first tag"

    async def test_closing_a_session_hands_its_tag_back(self, api: ApiServer) -> None:
        api.bot.get_channel.return_value = None
        await api._apply_voice_labels([{"thread_id": 1, "thread_name": "live"}])
        assert (await api.settings_repo.get_all())["voice_label:1"] == FIRST

        # The same thread comes round again, now closed.
        views = [
            {"thread_id": 1, "thread_name": "done", "closed": True},
            {"thread_id": 2, "thread_name": "next", "closed": False},
        ]
        await api._apply_voice_labels(views)

        stored = await api.settings_repo.get_all()
        assert "voice_label:1" not in stored, "the word is free again"

    async def test_the_freed_word_is_not_handed_straight_to_another_thread(
        self, api: ApiServer
    ) -> None:
        """A tag must not change meaning under the speaker.

        Freeing it is right — the pool is 26 long. Handing it to a different live
        thread in the same breath is not: the next thing said with that word
        would reach somewhere new, which is worse than reaching a closed
        session and being told so. So a freed word goes to the back of the queue
        and is only reused once every unused one is gone.
        """
        api.bot.get_channel.return_value = None
        await api._apply_voice_labels([{"thread_id": 1, "thread_name": "live"}])

        views = [
            {"thread_id": 1, "thread_name": "done", "closed": True},
            {"thread_id": 2, "thread_name": "next", "closed": False},
        ]
        await api._apply_voice_labels(views)

        assert views[1]["voice_label"] != FIRST
        assert views[1]["voice_label"] in SPOKEN_LABELS

    async def test_a_view_that_never_says_is_treated_as_open(self, api: ApiServer) -> None:
        """Every other caller of this builds views without the field."""
        api.bot.get_channel.return_value = None
        views = [{"thread_id": 1, "thread_name": "live"}]

        await api._apply_voice_labels(views)

        assert views[0]["voice_label"] == FIRST

    async def test_the_freed_word_is_reused_once_the_pool_runs_out(self, api: ApiServer) -> None:
        """The other side of the trade-off: without reuse the pool dies in a week.

        A closed session's word does go back into circulation. That is safe in a
        way reusing a *live* thread's word never is — the closed thread is gone
        from the sidebar and its title no longer shows the tag, so there is
        nothing left prompting the speaker to say it.
        """
        api.bot.get_channel.return_value = None
        n = len(SPOKEN_LABELS)
        everything = [{"thread_id": i, "thread_name": f"t{i}"} for i in range(1, n + 1)]
        await api._apply_voice_labels(everything)
        assert all(v["voice_label"] for v in everything), "the whole pool handed out"

        # One closes; a new thread appears and must still get a word.
        everything[0]["closed"] = True
        everything.append({"thread_id": 99, "thread_name": "new"})
        await api._apply_voice_labels(everything)

        assert everything[0]["voice_label"] is None
        assert everything[-1]["voice_label"] == FIRST, "the freed word, reused"


class TestAStaleTagLeavesTheTitle:
    """A title showing a tag the thread no longer owns is worse than no tag.

    ``_show_tags_in_titles`` only ever *added* a tag: a view with no tag was
    skipped, so a thread that lost its word kept displaying it. Two threads then
    read "[bravo]" while only one answered to it, and the sidebar — the one place
    the tag is read from — was lying.
    """

    async def test_a_live_thread_that_lost_its_tag_has_it_removed(self, api: ApiServer) -> None:
        """Happens when the pool is exhausted: the thread is live but untagged.

        A *closed* thread is handled elsewhere — it is archived, so its title can
        no longer change, and the tag is taken off as it closes
        (lifecycle_adapters.py).
        """
        stale = _thread(99, "[bravo] 📂 repo")
        api.bot.get_channel.side_effect = lambda tid: stale if tid == 99 else None
        # Every word is promised to a live thread, so 99 gets none.
        for i, name in enumerate(SPOKEN_LABELS, start=1):
            await api.settings_repo.set(f"voice_label:{i}", name)
        views = [{"thread_id": 99, "thread_name": "[bravo] 📂 repo"}]
        views += [
            {"thread_id": i, "thread_name": f"t{i}"} for i in range(1, len(SPOKEN_LABELS) + 1)
        ]

        await api._apply_voice_labels(views)

        assert views[0]["voice_label"] is None, "no word was available"
        stale.edit.assert_awaited_once_with(name="📂 repo")
        assert views[0]["thread_name"] == "📂 repo"

    async def test_an_untagged_title_is_left_alone(self, api: ApiServer) -> None:
        """A no-op rename is not free, and this endpoint is polled constantly."""
        thread = _thread(1, "📂 repo")
        api.bot.get_channel.return_value = thread
        views = [{"thread_id": 1, "thread_name": "📂 repo", "closed": True}]

        await api._apply_voice_labels(views)

        thread.edit.assert_not_awaited()

    async def test_a_thread_the_bot_has_not_cached_is_still_renamed(self, api: ApiServer) -> None:
        """get_channel only sees the cache, and an uncached thread kept a stale tag."""
        thread = _thread(1, "📂 repo")
        api.bot.get_channel.return_value = None
        api.bot.fetch_channel = AsyncMock(return_value=thread)
        views = [{"thread_id": 1, "thread_name": "📂 repo"}]

        await api._apply_voice_labels(views)

        thread.edit.assert_awaited_once_with(name=f"[{FIRST}] 📂 repo")


class TestTitleWorkIsBounded:
    """Looking a thread up costs a Discord call, so the budget must count those.

    The per-call cap only counted *successful* renames. Falling back to
    ``fetch_channel`` for an uncached thread then made every skipped view — an
    archived one, a locked one — cost an HTTP call that counted against nothing.
    With a hundred closed sessions and `/api/sessions` polled every five seconds
    by the voice companion, that is a hundred Discord calls every five seconds:
    the same rate-limit failure this code is supposed to avoid.
    """

    async def test_lookups_count_against_the_budget_not_just_renames(self, api: ApiServer) -> None:
        # Every thread is archived, so nothing can be renamed and the old cap
        # never advanced.
        archived = _thread(1, "📂 repo", archived=True)
        api.bot.get_channel.return_value = None
        api.bot.fetch_channel = AsyncMock(return_value=archived)
        views = [{"thread_id": i, "thread_name": f"📂 repo-{i}"} for i in range(1, 60)]

        await api._apply_voice_labels(views)

        assert api.bot.fetch_channel.await_count <= MAX_RETITLES_PER_CALL

    async def test_a_closed_session_is_never_fetched(self, api: ApiServer) -> None:
        """It is archived, so it cannot be renamed — the call is pure waste.

        Its tag is taken out of the title when the session closes, while the
        thread is still editable (lifecycle_adapters.py).
        """
        api.bot.get_channel.return_value = None
        api.bot.fetch_channel = AsyncMock()
        views = [
            {"thread_id": i, "thread_name": f"[luffy] repo-{i}", "closed": True}
            for i in range(1, 40)
        ]

        await api._apply_voice_labels(views)

        api.bot.fetch_channel.assert_not_awaited()

    async def test_a_cached_thread_costs_no_call(self, api: ApiServer) -> None:
        thread = _thread(1, "📂 repo")
        api.bot.get_channel.return_value = thread
        api.bot.fetch_channel = AsyncMock()
        views = [{"thread_id": 1, "thread_name": "📂 repo"}]

        await api._apply_voice_labels(views)

        api.bot.fetch_channel.assert_not_awaited()
        thread.edit.assert_awaited_once()


class TestTheShortPool:
    """Ten names, not twenty-six.

    Twenty-six was chosen to match an alphabet, not to match how many
    conversations are open at once. Drew asked for fewer so the words in play are
    always familiar ones. Ten is the Straw Hat crew minus the members whose names
    are everyday English — `robin` and `brook` are both disqualified for the same
    reason `law` and `ace` were: a tag that occurs in ordinary speech addresses a
    thread by accident.

    The trade is real and worth stating: past ten live threads the rest go
    untagged and cannot be reached by voice. That only became affordable once
    closed sessions stopped holding tags — before that, 25 of 26 were held by dead
    conversations.
    """

    def test_the_pool_is_ten(self) -> None:
        assert len(SPOKEN_LABELS) == 10

    def test_the_crew_comes_first(self) -> None:
        assert SPOKEN_LABELS[:3] == ("luffy", "zoro", "nami")

    def test_no_two_still_share_their_first_two_letters(self) -> None:
        starts = [label[:2] for label in SPOKEN_LABELS]
        assert len(set(starts)) == len(starts)

    def test_every_name_still_carries_its_mishearings(self) -> None:
        from claude_discord.voice_labels import aliases_for

        assert any(aliases_for(label) for label in SPOKEN_LABELS)
        for label in SPOKEN_LABELS:
            for alias in aliases_for(label):
                assert heard_as(alias) == label

    def test_no_alias_points_at_a_name_that_is_gone(self) -> None:
        """Shrinking the pool must not leave an alias aimed at nothing."""
        assert set(LABEL_ALIASES.values()) <= set(SPOKEN_LABELS)

    def test_an_eleventh_thread_goes_untagged_rather_than_stealing(self) -> None:
        stored = {i + 1: name for i, name in enumerate(SPOKEN_LABELS)}
        visible = sorted(stored) + [9999]
        labels, _new, released = assign_labels(visible, stored)

        assert released == set()
        assert 9999 not in labels, "untagged beats taking a live thread's word"
