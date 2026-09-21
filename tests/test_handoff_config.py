"""Tests for the strict per-instance handoff trust configuration (task 2.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from claude_code_core.handoffs import protocol as p
from claude_discord.handoff_config import (
    HandoffConfig,
    HandoffConfigError,
    HandoffTrustError,
    legacy_sender_trusted,
    legacy_trusted_bot_ids,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
GUILD = 1001
CHANNEL = 2002
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"

FULL_ENV = {
    "CCDB_AGENT_ID": "david",
    "CCDB_HANDOFF_GUILD_ID": str(GUILD),
    "CCDB_HANDOFF_CHANNEL_ID": str(CHANNEL),
    "CCDB_HANDOFF_AGENTS": "drewai=111,imac=222,david=333",
}


def _config() -> HandoffConfig:
    config = HandoffConfig.from_env(FULL_ENV)
    assert config is not None
    return config


def _task(sender: str = "drewai", recipient: str = "david", guild: int = GUILD) -> p.HandoffTask:
    origin = p.ConversationCoordinate(guild_id=guild, channel_id=5005, thread_id=6006)
    return p.HandoffTask(
        task_id=TASK_ID,
        sender=sender,
        recipient=recipient,
        origin=origin,
        origin_human_id="777",
        project=p.ProjectLocator(owner="drew", folder="main-projects"),
        goal="Find the visual picker",
        authority=p.AuthorityScope(read=True),
        expected_result="paths",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def _task_event(sender: str = "drewai", recipient: str = "david", **kw: int) -> p.HandoffEvent:
    task = _task(sender, recipient, **kw)
    return p.HandoffEvent(
        event_id=EVENT_ID,
        kind=p.HandoffEventKind.TASK,
        task_id=TASK_ID,
        sender=sender,
        recipient=recipient,
        sequence=0,
        created_at=NOW,
        task=task,
    )


def _message(
    *,
    author_id: int = 111,
    bot: bool = True,
    webhook_id: int | None = None,
    guild_id: int | None = GUILD,
    channel_id: int = CHANNEL,
    parent_id: int | None = None,
) -> SimpleNamespace:
    channel = SimpleNamespace(id=channel_id, parent_id=parent_id)
    return SimpleNamespace(
        id=9009,
        author=SimpleNamespace(id=author_id, bot=bot),
        webhook_id=webhook_id,
        guild=None if guild_id is None else SimpleNamespace(id=guild_id),
        channel=channel,
    )


class TestFromEnv:
    def test_complete_env_enables_the_feature(self) -> None:
        config = _config()
        assert config.local_agent_id == "david"
        assert config.guild_id == GUILD
        assert config.channel_id == CHANNEL
        assert config.bot_for_agent("drewai") == 111
        assert config.agent_for_bot(222) == "imac"
        assert config.peers == ("drewai", "imac")

    @pytest.mark.parametrize("missing", sorted(FULL_ENV))
    def test_incomplete_env_disables_the_feature(self, missing: str) -> None:
        env = {k: v for k, v in FULL_ENV.items() if k != missing}
        assert HandoffConfig.from_env(env) is None

    def test_empty_env_is_disabled_without_error(self) -> None:
        assert HandoffConfig.from_env({}) is None

    @pytest.mark.parametrize(
        "agents",
        [
            "drewai=abc",  # not a snowflake
            "drewai=111,imac=111",  # two agents share one bot: not one-to-one
            "drewai=111,drewai=222",  # one agent with two bots
            "drewai",  # no bot id at all
            "Drew AI=111",  # invalid agent id
        ],
    )
    def test_malformed_mapping_raises(self, agents: str) -> None:
        env = {**FULL_ENV, "CCDB_HANDOFF_AGENTS": agents}
        with pytest.raises(HandoffConfigError):
            HandoffConfig.from_env(env)

    def test_malformed_ids_raise(self) -> None:
        with pytest.raises(HandoffConfigError):
            HandoffConfig.from_env({**FULL_ENV, "CCDB_HANDOFF_GUILD_ID": "guild"})
        with pytest.raises(HandoffConfigError):
            HandoffConfig.from_env({**FULL_ENV, "CCDB_HANDOFF_CHANNEL_ID": "0"})

    def test_mapping_needs_at_least_one_peer(self) -> None:
        env = {**FULL_ENV, "CCDB_HANDOFF_AGENTS": "david=333"}
        with pytest.raises(HandoffConfigError):
            HandoffConfig.from_env(env)

    def test_local_agent_may_be_omitted_from_mapping(self) -> None:
        env = {**FULL_ENV, "CCDB_HANDOFF_AGENTS": "drewai=111"}
        config = HandoffConfig.from_env(env)
        assert config is not None
        assert config.bot_for_agent("david") is None
        assert config.peers == ("drewai",)

    def test_retention_defaults_and_parses(self) -> None:
        assert _config().retention == timedelta(days=7)
        config = HandoffConfig.from_env({**FULL_ENV, "CCDB_HANDOFF_RETENTION_DAYS": "2"})
        assert config is not None
        assert config.retention == timedelta(days=2)
        with pytest.raises(HandoffConfigError):
            HandoffConfig.from_env({**FULL_ENV, "CCDB_HANDOFF_RETENTION_DAYS": "0"})


class TestChannelScope:
    def test_channel_and_threads_under_it_are_in_scope(self) -> None:
        config = _config()
        assert config.in_handoff_scope(SimpleNamespace(id=CHANNEL, parent_id=None))
        assert config.in_handoff_scope(SimpleNamespace(id=4, parent_id=CHANNEL))
        assert not config.in_handoff_scope(SimpleNamespace(id=4, parent_id=None))
        assert not config.in_handoff_scope(SimpleNamespace(id=4, parent_id=5))


class TestVerifyInbound:
    def test_valid_task_from_mapped_peer_passes(self) -> None:
        event = _task_event()
        sender = _config().verify_inbound(_message(), event)
        assert sender == "drewai"

    def test_events_inside_the_job_thread_pass(self) -> None:
        event = _task_event()
        message = _message(channel_id=4004, parent_id=CHANNEL)
        assert _config().verify_inbound(message, event) == "drewai"

    @pytest.mark.parametrize(
        ("message", "reason"),
        [
            (_message(webhook_id=42), "webhook"),
            (_message(bot=False), "human"),
            (_message(guild_id=None), "guild"),
            (_message(guild_id=GUILD + 1), "guild"),
            (_message(channel_id=CHANNEL + 1), "channel"),
            (_message(channel_id=4, parent_id=CHANNEL + 1), "channel"),
            (_message(author_id=999), "not a configured"),
            (_message(author_id=222), "sender"),  # imac's bot claiming to be drewai
        ],
    )
    def test_wrong_origin_fails_closed(self, message: SimpleNamespace, reason: str) -> None:
        with pytest.raises(HandoffTrustError, match=reason):
            _config().verify_inbound(message, _task_event())

    def test_task_addressed_elsewhere_fails_closed(self) -> None:
        event = _task_event(sender="drewai", recipient="imac")
        with pytest.raises(HandoffTrustError, match="recipient"):
            _config().verify_inbound(_message(), event)

    def test_task_whose_origin_is_another_guild_fails_closed(self) -> None:
        event = _task_event(guild=GUILD + 5)
        with pytest.raises(HandoffTrustError, match="origin"):
            _config().verify_inbound(_message(), event)

    def test_own_bot_is_never_a_trusted_sender_of_its_own_task(self) -> None:
        event = _task_event(sender="david", recipient="drewai")
        with pytest.raises(HandoffTrustError, match="recipient"):
            _config().verify_inbound(_message(author_id=333), event)

    def test_result_event_for_a_local_task_passes_for_the_origin(self) -> None:
        result = p.HandoffEvent(
            event_id=EVENT_ID,
            kind=p.HandoffEventKind.RESULT,
            task_id=TASK_ID,
            sender="drewai",
            recipient="david",
            sequence=3,
            created_at=NOW,
            payload={"outcome": "completed", "summary": "done"},
        )
        message = _message(channel_id=4004, parent_id=CHANNEL)
        assert _config().verify_inbound(message, result) == "drewai"


class TestLegacySenderTrusted:
    @pytest.mark.parametrize(
        ("env", "author_id", "webhook", "member", "expected"),
        [
            ("111,222", 111, None, True, True),
            ("111,222", 333, None, True, False),
            ("111", 111, 999, True, False),
            # No allowlist means the legacy path is off: a guild member is not enough.
            ("", 444, None, True, False),
            ("", 444, 999, True, False),
            ("", 444, None, False, False),
        ],
    )
    def test_matches_the_review_helper(
        self, env: str, author_id: int, webhook: int | None, member: bool, expected: bool
    ) -> None:
        guild = SimpleNamespace(id=GUILD, get_member=lambda _id: object() if member else None)
        message = SimpleNamespace(
            author=SimpleNamespace(id=author_id, bot=True), webhook_id=webhook, guild=guild
        )
        assert legacy_sender_trusted(message, trusted_ids=env) is expected

    def test_a_human_account_on_the_list_is_still_refused(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(id=111, bot=False),
            webhook_id=None,
            guild=SimpleNamespace(id=GUILD),
        )
        assert legacy_sender_trusted(message, trusted_ids="111") is False

    def test_a_listed_bot_outside_any_guild_is_refused(self) -> None:
        message = SimpleNamespace(
            author=SimpleNamespace(id=111, bot=True), webhook_id=None, guild=None
        )
        assert legacy_sender_trusted(message, trusted_ids="111") is False

    def test_trusted_ids_parse_from_the_env_and_ignore_junk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_HANDOFF_TRUSTED_BOT_IDS", " 111, abc ;222 ")
        assert legacy_trusted_bot_ids() == frozenset({111, 222})
        monkeypatch.delenv("CCDB_HANDOFF_TRUSTED_BOT_IDS")
        assert legacy_trusted_bot_ids() == frozenset()
