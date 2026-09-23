"""Per-instance trust configuration for cross-computer agent handoffs.

Discord identity plus explicit configuration is what authenticates a handoff:
the packet says who sent it, and this module checks that the Discord author,
guild, and channel agree. Nothing here is inferred — a bot that is not mapped
to an agent id is not a peer, a message outside the configured channel is not
protocol, and a webhook or a human can never create a protocol event.

The feature is **disabled unless the configuration is complete**. A partial
configuration (say, a guild without a channel) returns ``None`` from
:meth:`HandoffConfig.from_env`; a *malformed* one raises so a typo in a bot id
is noticed at startup rather than silently shrinking the trust set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import Any

from claude_code_core.handoffs.protocol import (
    HandoffEvent,
    HandoffProtocolError,
    validate_agent_id,
    validate_snowflake,
)

ENV_AGENT_ID = "CCDB_AGENT_ID"
ENV_GUILD_ID = "CCDB_HANDOFF_GUILD_ID"
ENV_CHANNEL_ID = "CCDB_HANDOFF_CHANNEL_ID"
ENV_AGENTS = "CCDB_HANDOFF_AGENTS"
ENV_RETENTION_DAYS = "CCDB_HANDOFF_RETENTION_DAYS"
ENV_LEGACY_TRUSTED_BOT_IDS = "CCDB_HANDOFF_TRUSTED_BOT_IDS"

DEFAULT_RETENTION = timedelta(days=7)
MAX_RETENTION_DAYS = 90


class HandoffConfigError(ValueError):
    """The handoff configuration is present but cannot be trusted as written."""


class HandoffTrustError(PermissionError):
    """An inbound message failed one of the trust-boundary checks."""


def _parse_snowflake(raw: str, name: str) -> int:
    text = raw.strip()
    if not text.isdigit():
        raise HandoffConfigError(f"{name} must be a Discord id, got {raw!r}")
    try:
        return validate_snowflake(int(text), name)
    except HandoffProtocolError as exc:
        raise HandoffConfigError(str(exc)) from exc


def _parse_agents(raw: str) -> dict[str, int]:
    mapping: dict[str, int] = {}
    seen_bots: dict[int, str] = {}
    for entry in raw.replace(";", ",").split(","):
        item = entry.strip()
        if not item:
            continue
        agent_raw, sep, bot_raw = item.partition("=")
        if not sep:
            raise HandoffConfigError(f"{ENV_AGENTS} entry {item!r} needs the form agent=bot_id")
        try:
            agent = validate_agent_id(agent_raw)
        except HandoffProtocolError as exc:
            raise HandoffConfigError(f"{ENV_AGENTS}: {exc}") from exc
        bot_id = _parse_snowflake(bot_raw, f"{ENV_AGENTS} bot id for {agent}")
        if agent in mapping:
            raise HandoffConfigError(f"{ENV_AGENTS} maps agent {agent!r} twice")
        if bot_id in seen_bots:
            raise HandoffConfigError(
                f"{ENV_AGENTS} maps bot {bot_id} to both {seen_bots[bot_id]!r} and {agent!r}"
            )
        mapping[agent] = bot_id
        seen_bots[bot_id] = agent
    return mapping


@dataclass(frozen=True)
class HandoffConfig:
    """Everything a recipient must know before it may trust a packet."""

    local_agent_id: str
    guild_id: int
    channel_id: int
    bot_ids: Mapping[str, int] = field(default_factory=dict)
    retention: timedelta = DEFAULT_RETENTION

    def __post_init__(self) -> None:
        try:
            local = validate_agent_id(self.local_agent_id)
            guild = validate_snowflake(self.guild_id, "guild id")
            channel = validate_snowflake(self.channel_id, "channel id")
        except HandoffProtocolError as exc:
            raise HandoffConfigError(str(exc)) from exc
        mapping = dict(self.bot_ids)
        if not any(agent != local for agent in mapping):
            raise HandoffConfigError("handoff configuration needs at least one peer agent")
        if len(set(mapping.values())) != len(mapping):
            raise HandoffConfigError("handoff bot mapping must be one-to-one")
        if self.retention <= timedelta(0) or self.retention > timedelta(days=MAX_RETENTION_DAYS):
            raise HandoffConfigError(
                f"retention must be between 1 and {MAX_RETENTION_DAYS} days, got {self.retention}"
            )
        set_ = object.__setattr__
        set_(self, "local_agent_id", local)
        set_(self, "guild_id", guild)
        set_(self, "channel_id", channel)
        set_(self, "bot_ids", MappingProxyType(mapping))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HandoffConfig | None:
        """Build the configuration, or ``None`` when it is incomplete.

        Incomplete means disabled; malformed raises :class:`HandoffConfigError`.
        """
        source: Mapping[str, str] = os.environ if env is None else env
        values = {
            name: (source.get(name) or "").strip()
            for name in (ENV_AGENT_ID, ENV_GUILD_ID, ENV_CHANNEL_ID, ENV_AGENTS)
        }
        if not all(values.values()):
            return None
        retention = DEFAULT_RETENTION
        raw_days = (source.get(ENV_RETENTION_DAYS) or "").strip()
        if raw_days:
            if not raw_days.isdigit() or int(raw_days) <= 0:
                raise HandoffConfigError(f"{ENV_RETENTION_DAYS} must be a positive integer")
            retention = timedelta(days=int(raw_days))
        try:
            local = validate_agent_id(values[ENV_AGENT_ID])
        except HandoffProtocolError as exc:
            raise HandoffConfigError(f"{ENV_AGENT_ID}: {exc}") from exc
        return cls(
            local_agent_id=local,
            guild_id=_parse_snowflake(values[ENV_GUILD_ID], ENV_GUILD_ID),
            channel_id=_parse_snowflake(values[ENV_CHANNEL_ID], ENV_CHANNEL_ID),
            bot_ids=_parse_agents(values[ENV_AGENTS]),
            retention=retention,
        )

    # -- lookups -------------------------------------------------------------

    @property
    def peers(self) -> tuple[str, ...]:
        """Every configured agent other than this one, in a stable order."""
        return tuple(sorted(agent for agent in self.bot_ids if agent != self.local_agent_id))

    def bot_for_agent(self, agent_id: str) -> int | None:
        return self.bot_ids.get(agent_id)

    def agent_for_bot(self, bot_id: int) -> str | None:
        for agent, known in self.bot_ids.items():
            if known == bot_id:
                return agent
        return None

    def in_handoff_scope(self, channel: Any) -> bool:
        """True for the handoff channel itself or any thread under it."""
        if getattr(channel, "id", None) == self.channel_id:
            return True
        return getattr(channel, "parent_id", None) == self.channel_id

    # -- the trust boundary --------------------------------------------------

    def verify_inbound(self, message: Any, event: HandoffEvent) -> str:
        """Check a Discord message against the packet it carries.

        Returns the sender agent id when every check passes; raises
        :class:`HandoffTrustError` naming the first failed check otherwise.
        Every field of ``message`` is untrusted and read defensively.
        """
        if getattr(message, "webhook_id", None) is not None:
            raise HandoffTrustError("webhook messages cannot carry handoff events")
        author = getattr(message, "author", None)
        if not getattr(author, "bot", False):
            raise HandoffTrustError("a human account cannot create handoff protocol events")
        guild = getattr(message, "guild", None)
        guild_id = getattr(guild, "id", None)
        if not isinstance(guild_id, int) or guild_id != self.guild_id:
            raise HandoffTrustError("message is not from the configured handoff guild")
        if not self.in_handoff_scope(getattr(message, "channel", None)):
            raise HandoffTrustError("message is not in the configured handoff channel")
        author_id = getattr(author, "id", None)
        if not isinstance(author_id, int):
            raise HandoffTrustError("message author has no usable id")
        sender = self.agent_for_bot(author_id)
        if sender is None:
            raise HandoffTrustError(f"bot {author_id} is not a configured handoff agent")
        if sender != event.sender:
            raise HandoffTrustError(
                f"packet sender {event.sender!r} does not match the posting bot ({sender!r})"
            )
        if event.recipient != self.local_agent_id:
            raise HandoffTrustError(
                f"packet recipient {event.recipient!r} is not this agent ({self.local_agent_id!r})"
            )
        task = event.task
        if task is not None and (
            task.origin.guild_id != self.guild_id or task.reply_to.guild_id != self.guild_id
        ):
            raise HandoffTrustError("task origin and reply location must be in this guild")
        return sender


def legacy_trusted_bot_ids(raw: str | None = None) -> frozenset[int]:
    """The bot accounts ``CCDB_HANDOFF_TRUSTED_BOT_IDS`` names; empty means off."""
    text = os.getenv(ENV_LEGACY_TRUSTED_BOT_IDS, "") if raw is None else raw
    return frozenset(
        int(part) for part in text.replace(";", ",").split(",") if part.strip().isdigit()
    )


def legacy_sender_trusted(message: Any, *, trusted_ids: str | None = None) -> bool:
    """The pre-configuration trust rule for the narrow project-lookup slice.

    ``CCDB_HANDOFF_TRUSTED_BOT_IDS`` is the whole trust set: a bot account it
    lists, posting as itself (not through a webhook) inside a guild, may hand
    a job over. Without the list nothing is trusted — "any bot that happens
    to be a member of this server" is not an allowlist, it is every bot the
    server admins ever invited. Instances with a complete
    :class:`HandoffConfig` use :meth:`HandoffConfig.verify_inbound` instead.
    """
    allowed = legacy_trusted_bot_ids(trusted_ids)
    if not allowed:
        return False
    if getattr(message, "webhook_id", None) is not None:
        return False
    author = getattr(message, "author", None)
    if not getattr(author, "bot", False):
        return False
    author_id = getattr(author, "id", None)
    if not isinstance(author_id, int):
        return False
    guild_id = getattr(getattr(message, "guild", None), "id", None)
    if not isinstance(guild_id, int):
        return False
    return author_id in allowed


__all__ = [
    "DEFAULT_RETENTION",
    "ENV_AGENTS",
    "ENV_AGENT_ID",
    "ENV_CHANNEL_ID",
    "ENV_GUILD_ID",
    "ENV_LEGACY_TRUSTED_BOT_IDS",
    "ENV_RETENTION_DAYS",
    "HandoffConfig",
    "HandoffConfigError",
    "HandoffTrustError",
    "legacy_sender_trusted",
    "legacy_trusted_bot_ids",
]
