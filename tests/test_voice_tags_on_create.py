"""Every thread gets a tag when it is born, whoever created it.

Tagging used to happen in exactly two places, both inside the API server:
``GET /api/sessions`` and ``POST /api/spawn``. So a thread opened by voice was
tagged at once, and every *other* way a thread starts — the control center, a
typed message, ``/skill``, ``/fork`` — waited for something to poll
``/api/sessions``. That poll comes from the voice companion, which means a thread
created with voice switched off is never tagged at all: the tag quietly depended
on a separate service being alive.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.voice_labels import SPOKEN_LABELS
from claude_discord.voice_tags import VoiceTagger


class FakeSettings:
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store = dict(initial or {})

    async def get_all(self) -> dict[str, str]:
        return dict(self.store)

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


def _thread(thread_id: int, name: str) -> MagicMock:
    t = MagicMock(spec=discord.Thread)
    t.id = thread_id
    t.name = name
    t.archived = False
    t.locked = False
    t.edit = AsyncMock()
    return t


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.fetch_channel = AsyncMock(side_effect=AssertionError("should use the cache"))
    return b


class TestTaggedOnCreation:
    async def test_a_new_thread_is_tagged_without_anything_polling(self, bot) -> None:
        thread = _thread(10, "📂 the aldus")
        bot.get_channel.return_value = thread
        settings = FakeSettings()

        label = await VoiceTagger(bot, settings).tag_thread(thread)

        assert label == SPOKEN_LABELS[0]
        assert settings.store["voice_label:10"] == SPOKEN_LABELS[0]
        thread.edit.assert_awaited_once_with(name=f"[{SPOKEN_LABELS[0]}] 📂 the aldus")

    async def test_a_thread_keeps_the_tag_it_already_has(self, bot) -> None:
        thread = _thread(10, "[zoro] 📂 repo")
        bot.get_channel.return_value = thread
        settings = FakeSettings({"voice_label:10": "zoro"})

        label = await VoiceTagger(bot, settings).tag_thread(thread)

        assert label == "zoro"
        thread.edit.assert_not_awaited(), "the title already shows it"

    async def test_it_never_takes_a_word_another_thread_is_using(self, bot) -> None:
        thread = _thread(10, "📂 repo")
        bot.get_channel.return_value = thread
        settings = FakeSettings({"voice_label:1": SPOKEN_LABELS[0]})

        label = await VoiceTagger(bot, settings).tag_thread(thread)

        assert label == SPOKEN_LABELS[1]

    async def test_a_full_pool_leaves_it_untagged_rather_than_stealing(self, bot) -> None:
        """Reusing a live word would send a spoken instruction to the wrong repo."""
        thread = _thread(999, "📂 repo")
        bot.get_channel.return_value = thread
        settings = FakeSettings(
            {f"voice_label:{i + 1}": name for i, name in enumerate(SPOKEN_LABELS)}
        )

        label = await VoiceTagger(bot, settings).tag_thread(thread)

        assert label is None
        thread.edit.assert_not_awaited()

    async def test_no_settings_repo_means_no_tag_and_no_crash(self, bot) -> None:
        """The framework must work for a consumer who configured nothing."""
        thread = _thread(10, "📂 repo")
        assert await VoiceTagger(bot, None).tag_thread(thread) is None

    async def test_a_rename_failure_still_records_the_tag(self, bot) -> None:
        """The thread is addressable by tag even when its title could not change."""
        thread = _thread(10, "📂 repo")
        thread.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=429), "slow"))
        bot.get_channel.return_value = thread
        settings = FakeSettings()

        label = await VoiceTagger(bot, settings).tag_thread(thread)

        assert label == SPOKEN_LABELS[0]
        assert settings.store["voice_label:10"] == SPOKEN_LABELS[0]


class TestTheListenerCoversEveryPath:
    """One hook catches every way a thread can be born.

    There are fourteen ``create_thread`` calls across the codebase — the control
    center, a typed message, ``/skill``, ``/fork``, session sync, webhooks,
    handoffs. Tagging at each of them would be fourteen places to forget; Discord
    fires one event for all of them.
    """

    @staticmethod
    def _cog(settings: FakeSettings | None, channel_ids: set[int] | None = None):
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        cog = ClaudeChatCog.__new__(ClaudeChatCog)
        cog.bot = MagicMock()
        cog._settings_repo = settings
        cog._channel_ids = channel_ids if channel_ids is not None else {77}
        return cog

    async def test_a_thread_created_in_a_watched_channel_is_tagged(self) -> None:
        settings = FakeSettings()
        cog = self._cog(settings)
        thread = _thread(10, "📂 the aldus")
        thread.parent_id = 77
        cog.bot.get_channel.return_value = thread

        await cog.on_thread_create(thread)

        assert settings.store["voice_label:10"] == SPOKEN_LABELS[0]

    async def test_with_no_boundary_configured_every_thread_is_tagged(self) -> None:
        """Deliberate: the channel list was the wrong guard.

        It excluded threads the bot genuinely works in — see
        TestTheGuardCannotSilentlySkip. With no category boundary set, "everything
        here" is the same default the rest of the bot uses, and the 26-word pool is
        the only limit. Set CCDB_ALLOWED_CATEGORY_IDS to narrow it.
        """
        settings = FakeSettings()
        cog = self._cog(settings)
        thread = _thread(10, "a thread in some other channel")
        thread.parent_id = 999
        thread.parent = MagicMock(category_id=None)
        cog.bot.get_channel.return_value = thread

        await cog.on_thread_create(thread)

        assert settings.store.get("voice_label:10") == SPOKEN_LABELS[0]

    async def test_a_failure_never_breaks_thread_creation(self) -> None:
        """Losing a tag is a nuisance; losing the thread is not acceptable."""
        settings = FakeSettings()
        settings.get_all = AsyncMock(side_effect=RuntimeError("db down"))
        cog = self._cog(settings)
        thread = _thread(10, "📂 repo")
        thread.parent_id = 77

        await cog.on_thread_create(thread)  # must not raise


class TestTheAliasesActuallyReachTheEndpoint:
    """Asserting the helper works is not asserting the answer carries it.

    A refactor moved the tagging out of the API server and silently dropped the
    line that attached each tag's mishearings to the view. Every unit test still
    passed — `aliases_for` was fine, `session_view` was fine — and the live effect
    was that saying "Zorro" did not reach `zoro`, because the voice layer was told
    the tag had no alternative spellings. So this test reads the field the voice
    layer actually consumes, on the object the endpoint actually returns.
    """

    async def test_a_tagged_view_carries_the_words_it_is_misheard_as(self) -> None:
        settings = FakeSettings({"voice_label:10": "zoro"})
        bot = MagicMock()
        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(return_value=None)
        views = [{"thread_id": 10, "thread_name": "[zoro] repo"}]

        await VoiceTagger(bot, settings).apply(views)

        assert views[0]["voice_label"] == "zoro"
        assert "zorro" in views[0]["voice_label_aliases"], (
            "the voice layer cannot match a mishearing it was never told about"
        )

    async def test_an_untagged_view_carries_an_empty_list_not_a_missing_key(self) -> None:
        """The voice layer does `s.voice_label_aliases ?? []`; be explicit anyway."""
        settings = FakeSettings(
            {f"voice_label:{i + 1}": name for i, name in enumerate(SPOKEN_LABELS)}
        )
        bot = MagicMock()
        bot.get_channel.return_value = None
        # All 26 holders are visible here, so none of their words can be
        # reclaimed and 999 genuinely has nothing available.
        views = [{"thread_id": i + 1, "thread_name": f"t{i}"} for i in range(26)]
        views.append({"thread_id": 999, "thread_name": "repo"})

        await VoiceTagger(bot, settings).apply(views)

        assert views[-1]["voice_label"] is None
        assert views[-1]["voice_label_aliases"] == []


class TestTheGuardCannotSilentlySkip:
    """The channel list is not guaranteed to contain every channel the bot uses.

    `setup_bridge` adds `CCDB_LAUNCHER_SESSION_CHANNEL_ID` to the chat cog's
    channel set but **not** `CCDB_LAUNCHER_CHANNEL_ID`. On this machine both point
    at the same channel, so tagging control-center threads worked — by
    coincidence, not by design. Point the control center at its own channel and
    every thread it opens is silently untagged, with nothing anywhere saying so.

    So the guard is a category boundary — the bot's own notion of "channels I work
    in", the same one `on_message` uses — with the channel list as an *additional*
    way in rather than the only one.
    """

    @staticmethod
    def _cog(settings, channel_ids):
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        cog = ClaudeChatCog.__new__(ClaudeChatCog)
        cog.bot = MagicMock()
        cog._settings_repo = settings
        cog._channel_ids = channel_ids
        return cog

    async def test_a_thread_outside_the_channel_list_is_still_tagged(self) -> None:
        """The control center pointed at its own channel is the real case."""
        settings = FakeSettings()
        cog = self._cog(settings, {77})
        thread = _thread(10, "📂 the aldus")
        thread.parent_id = 12345  # a launcher channel nobody added to the list
        thread.parent = MagicMock(category_id=None)
        cog.bot.get_channel.return_value = thread

        await cog.on_thread_create(thread)

        assert settings.store.get("voice_label:10") == SPOKEN_LABELS[0]

    async def test_a_thread_outside_the_category_boundary_is_not_tagged(self, monkeypatch) -> None:
        """A configured boundary still fails closed — that is what it is for."""
        monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "555")
        settings = FakeSettings()
        cog = self._cog(settings, set())
        thread = _thread(10, "someone else's thread")
        thread.parent_id = 999
        thread.parent = MagicMock(category_id=42)

        await cog.on_thread_create(thread)

        assert settings.store == {}

    async def test_a_thread_inside_the_category_boundary_is_tagged(self, monkeypatch) -> None:
        monkeypatch.setenv("CCDB_ALLOWED_CATEGORY_IDS", "555")
        settings = FakeSettings()
        cog = self._cog(settings, set())
        thread = _thread(10, "📂 repo")
        thread.parent_id = 999
        thread.parent = MagicMock(category_id=555)
        cog.bot.get_channel.return_value = thread

        await cog.on_thread_create(thread)

        assert settings.store.get("voice_label:10") == SPOKEN_LABELS[0]
