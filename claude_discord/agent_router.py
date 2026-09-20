"""Friendly agent-name routing for cross-thread relays.

The low-level relay API speaks in Discord thread IDs because that is the
durable address ccdb can deliver to. Humans and peer agents speak in names like
``DrewAI`` or ``iMac``. This module keeps that translation small, explicit and
configuration-driven.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class AgentRoute:
    """One configured agent destination."""

    agent_id: str
    thread_id: int
    aliases: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        """Return the safe listing shape for API callers."""
        return {
            "agent_id": self.agent_id,
            "thread_id": self.thread_id,
            "aliases": list(self.aliases),
        }


class AgentDirectory:
    """Lookup table for configured agents and their aliases."""

    def __init__(self, routes: list[AgentRoute]) -> None:
        self._routes = tuple(routes)
        self._by_name: dict[str, AgentRoute] = {}
        for route in routes:
            names = (route.agent_id, *route.aliases)
            for name in names:
                key = normalize_agent_id(name)
                existing = self._by_name.get(key)
                if existing is not None and existing != route:
                    raise ValueError(f"duplicate agent route alias: {name}")
                self._by_name[key] = route

    @property
    def routes(self) -> tuple[AgentRoute, ...]:
        return self._routes

    def resolve(self, name: str) -> AgentRoute:
        """Resolve *name* to a configured route.

        Raises:
            KeyError: when no route or alias matches.
        """
        return self._by_name[normalize_agent_id(name)]

    def known_agent_ids(self) -> tuple[str, ...]:
        return tuple(route.agent_id for route in self._routes)


def normalize_agent_id(value: str) -> str:
    """Normalize names so ``Drew AI``, ``DrewAI`` and ``drew-ai`` match."""
    normalized = _NON_ALNUM.sub("", value.casefold())
    if not normalized:
        raise ValueError("agent name must contain at least one letter or number")
    return normalized


def parse_agent_routes(raw: str | None) -> AgentDirectory:
    """Parse ``CCDB_AGENT_ROUTES`` into an :class:`AgentDirectory`.

    Accepted shapes:

    - ``drewai=1550757693784989707|drew,drew ai;imac=222``
    - ``drewai:1550757693784989707``
    - JSON object: ``{"drewai": 155, "imac": {"thread_id": 222, "aliases": ["mac"]}}``
    """
    if raw is None or not raw.strip():
        return AgentDirectory([])
    value = raw.strip()
    if value.startswith("{"):
        return AgentDirectory(_parse_json_routes(value))
    return AgentDirectory(_parse_text_routes(value))


def _parse_text_routes(raw: str) -> list[AgentRoute]:
    routes: list[AgentRoute] = []
    for part in re.split(r"[;\n]", raw):
        entry = part.strip()
        if not entry:
            continue
        separator = "=" if "=" in entry else ":"
        if separator not in entry:
            raise ValueError("agent route entries must use name=thread_id or name:thread_id")
        name, rest = entry.split(separator, 1)
        thread_text, _, aliases_text = rest.partition("|")
        aliases = tuple(alias.strip() for alias in aliases_text.split(",") if alias.strip())
        routes.append(
            AgentRoute(
                agent_id=normalize_agent_id(name.strip()),
                thread_id=_parse_thread_id(thread_text),
                aliases=aliases,
            )
        )
    return routes


def _parse_json_routes(raw: str) -> list[AgentRoute]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("agent routes JSON is invalid") from exc
    if not isinstance(data, dict):
        raise ValueError("agent routes JSON must be an object")

    routes: list[AgentRoute] = []
    for name, spec in data.items():
        if isinstance(spec, int | str):
            routes.append(
                AgentRoute(
                    agent_id=normalize_agent_id(str(name)),
                    thread_id=_parse_thread_id(spec),
                    aliases=(),
                )
            )
            continue
        if not isinstance(spec, dict):
            raise ValueError("agent route values must be thread ids or objects")
        thread_id = spec.get("thread_id")
        aliases = _parse_aliases(spec.get("aliases", ()))
        routes.append(
            AgentRoute(
                agent_id=normalize_agent_id(str(name)),
                thread_id=_parse_thread_id(thread_id),
                aliases=aliases,
            )
        )
    return routes


def _parse_aliases(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        raise ValueError("agent route aliases must be a string or list of strings")
    aliases: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("agent route aliases must be strings")
        aliases.append(item)
    return tuple(aliases)


def _parse_thread_id(value: object) -> int:
    try:
        thread_id = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("agent route thread_id must be an integer") from exc
    if thread_id <= 0:
        raise ValueError("agent route thread_id must be positive")
    return thread_id


__all__ = ["AgentDirectory", "AgentRoute", "normalize_agent_id", "parse_agent_routes"]
