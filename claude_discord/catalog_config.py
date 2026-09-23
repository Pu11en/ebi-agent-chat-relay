"""Catalog configuration: a shared profile plus one machine's own facts.

Two computers can behave the same without being the same.  What they agree on
— which owners and computers exist and what they are called — is the
:class:`SharedProfile`; DrewAI and the iMac read one profile file.  What only
this computer knows — its identity, its approved roots and the actions it
deliberately cannot offer — is the :class:`MachineConfig`.  Both are plain
environment configuration; nothing here reads the disk beyond the profile
file, and a malformed profile raises rather than quietly running with no
trusted peers.

Environment:

* ``CCDB_PROJECT_ROOTS`` — the approved roots (same setting Create/Clone use).
  A root that does not exist is *kept*, so discovery reports it as unavailable
  instead of a project silently vanishing from the list.
* ``CCDB_CATALOG_OWNER`` / ``CCDB_CATALOG_COMPUTER`` — this machine's owner
  and computer tokens; default ``CCDB_AGENT_ID`` / ``CCDB_COMPUTER_NAME``,
  then ``local`` / the hostname.
* ``CCDB_CATALOG_PROFILE`` — a JSON file ``{"computers": [{"owner", "computer",
  "aliases"}]}``; or ``CCDB_CATALOG_COMPUTERS`` inline as
  ``owner/computer:alias|alias, ...``.
* ``CCDB_CATALOG_CAPABILITIES`` — the actions this machine offers (default:
  ``session, create, clone`` plus ``handoff`` when handoff peers are
  configured); ``CCDB_CATALOG_DISABLED`` — ``action=reason`` exceptions, so a
  hidden action can still explain itself.
"""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from .project_catalog import ApprovedRoot, OwnerRegistry, TrustedComputer, normalize_token
from .project_creation import configured_root_list

ENV_PROFILE = "CCDB_CATALOG_PROFILE"
ENV_COMPUTERS = "CCDB_CATALOG_COMPUTERS"
ENV_OWNER = "CCDB_CATALOG_OWNER"
ENV_COMPUTER = "CCDB_CATALOG_COMPUTER"
ENV_CAPABILITIES = "CCDB_CATALOG_CAPABILITIES"
ENV_DISABLED = "CCDB_CATALOG_DISABLED"

#: Every action a catalog entry can offer.  ``handoff`` is what a remote
#: target needs; the other three act on a local project.
CATALOG_ACTIONS: tuple[str, ...] = ("session", "create", "clone", "handoff")
DEFAULT_CAPABILITIES: frozenset[str] = frozenset({"session", "create", "clone"})


def _hostname() -> str:
    return platform.node()


def _split(raw: str, *separators: str) -> list[str]:
    text = raw
    for sep in separators[1:]:
        text = text.replace(sep, separators[0])
    return [part.strip() for part in text.split(separators[0]) if part.strip()]


@dataclass(frozen=True, slots=True)
class SharedProfile:
    """The trusted computers and aliases every machine on the profile shares."""

    computers: tuple[TrustedComputer, ...] = ()
    name: str | None = None

    @classmethod
    def parse(cls, raw: str, *, name: str | None = None) -> SharedProfile:
        """Inline form: ``owner/computer:alias|alias, owner/computer``."""
        computers: list[TrustedComputer] = []
        for entry in _split(raw, ",", ";", "\n"):
            head, _, alias_text = entry.partition(":")
            parts = head.split("/")
            if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
                raise ValueError(
                    f"{ENV_COMPUTERS} entry {entry!r} must look like owner/computer[:alias|alias]"
                )
            aliases = tuple(_split(alias_text, "|"))
            computers.append(TrustedComputer(owner=parts[0], computer=parts[1], aliases=aliases))
        return cls(computers=tuple(computers), name=name)

    @classmethod
    def from_file(cls, path: str | Path) -> SharedProfile:
        file = Path(path).expanduser()
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Catalog profile {file} could not be read: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"Catalog profile {file} must be a JSON object")
        entries = data.get("computers", [])
        if not isinstance(entries, list):
            raise ValueError(f"Catalog profile {file}: 'computers' must be a list")
        computers: list[TrustedComputer] = []
        for entry in entries:
            if not isinstance(entry, dict) or "owner" not in entry or "computer" not in entry:
                raise ValueError(
                    f"Catalog profile {file}: every computer needs 'owner' and 'computer'"
                )
            aliases = entry.get("aliases", [])
            if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
                raise ValueError(f"Catalog profile {file}: 'aliases' must be a list of strings")
            computers.append(
                TrustedComputer(
                    owner=str(entry["owner"]),
                    computer=str(entry["computer"]),
                    aliases=tuple(aliases),
                )
            )
        name = data.get("name")
        return cls(computers=tuple(computers), name=str(name) if name else file.stem)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SharedProfile:
        source: Mapping[str, str] = os.environ if env is None else env
        profile_path = source.get(ENV_PROFILE, "").strip()
        if profile_path:
            return cls.from_file(profile_path)
        inline = source.get(ENV_COMPUTERS, "").strip()
        if inline:
            return cls.parse(inline)
        return cls()


def derive_root_keys(paths: Iterable[str | PurePath]) -> tuple[str, ...]:
    """Stable per-root keys from folder names; a repeated name gets ``-2``, ``-3``."""
    keys: list[str] = []
    for raw in paths:
        path = PurePath(raw)
        base = path.name or path.anchor or "root"
        try:
            key = normalize_token(base, kind="root key")
        except ValueError:
            key = "root"
        candidate, counter = key, 1
        while candidate in keys:
            counter += 1
            candidate = f"{key}-{counter}"
        keys.append(candidate)
    return tuple(keys)


@dataclass(frozen=True, slots=True)
class MachineConfig:
    """What only this computer knows: identity, roots and capability exceptions."""

    owner: str
    computer: str
    roots: tuple[ApprovedRoot, ...] = ()
    capabilities: frozenset[str] = DEFAULT_CAPABILITIES
    exceptions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, fallback_root: str | None = None
    ) -> MachineConfig:
        source: Mapping[str, str] = os.environ if env is None else env
        owner = source.get(ENV_OWNER, "").strip() or source.get("CCDB_AGENT_ID", "").strip()
        computer = (
            source.get(ENV_COMPUTER, "").strip()
            or source.get("CCDB_COMPUTER_NAME", "").strip()
            or _hostname().strip()
        )
        owner_token = owner or "local"
        computer_token = computer or "local"
        try:
            normalize_token(computer_token, kind="computer")
        except ValueError:
            computer_token = "local"

        raw_roots = configured_root_list(source, fallback=fallback_root)
        paths = [Path(raw).expanduser() for raw in raw_roots]
        paths = [path if path.is_absolute() else path.resolve() for path in paths]
        keys = derive_root_keys(paths)
        roots = tuple(
            ApprovedRoot(key=key, path=PurePath(path), owner=owner_token, computer=computer_token)
            for key, path in zip(keys, paths, strict=True)
        )

        capabilities = set(DEFAULT_CAPABILITIES)
        if source.get("CCDB_HANDOFF_AGENTS", "").strip():
            capabilities.add("handoff")
        configured = source.get(ENV_CAPABILITIES, "").strip()
        if configured:
            capabilities = {item.lower() for item in _split(configured, ",", ";")}

        exceptions: dict[str, str] = {}
        for entry in _split(source.get(ENV_DISABLED, ""), ";", ","):
            action, _, reason = entry.partition("=")
            action = action.strip().lower()
            if action:
                exceptions[action] = reason.strip() or "not available on this computer"
                capabilities.discard(action)

        return cls(
            owner=owner_token,
            computer=computer_token,
            roots=roots,
            capabilities=frozenset(capabilities),
            exceptions=exceptions,
        )

    def supports(self, action: str) -> bool:
        return action in self.capabilities and action not in self.exceptions

    def available_actions(self, actions: Iterable[str] = CATALOG_ACTIONS) -> tuple[str, ...]:
        """The subset of ``actions`` this computer offers, in the order given."""
        return tuple(action for action in actions if self.supports(action))

    def unavailable_reason(self, action: str) -> str | None:
        """Why an action is not offered, or None when it is."""
        if self.supports(action):
            return None
        return self.exceptions.get(action, f"{action} is not available on this computer")

    def local_computer(self, aliases: Iterable[str] = ()) -> TrustedComputer:
        return TrustedComputer(
            owner=self.owner, computer=self.computer, aliases=tuple(aliases), is_local=True
        )


@dataclass(frozen=True, slots=True)
class CatalogConfig:
    """A shared profile applied to one machine."""

    profile: SharedProfile
    machine: MachineConfig

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, fallback_root: str | None = None
    ) -> CatalogConfig:
        return cls(
            profile=SharedProfile.from_env(env),
            machine=MachineConfig.from_env(env, fallback_root=fallback_root),
        )

    @property
    def roots(self) -> tuple[ApprovedRoot, ...]:
        return self.machine.roots

    def registry(self) -> OwnerRegistry:
        """Profile computers plus this machine, which is the only local one.

        A profile entry matching this machine's owner and computer is merged
        (its aliases kept) and marked local; every other entry is remote.
        """
        local_key = (self.machine.owner, self.machine.computer)
        merged: list[TrustedComputer] = []
        found_local = False
        for entry in self.profile.computers:
            if (entry.owner, entry.computer) == local_key:
                merged.append(self.machine.local_computer(entry.aliases))
                found_local = True
            else:
                merged.append(
                    TrustedComputer(
                        owner=entry.owner,
                        computer=entry.computer,
                        aliases=entry.aliases,
                        is_local=False,
                    )
                )
        if not found_local:
            merged.insert(0, self.machine.local_computer())
        return OwnerRegistry(merged)

    def available_actions(self, actions: Iterable[str] = CATALOG_ACTIONS) -> tuple[str, ...]:
        return self.machine.available_actions(actions)

    def unavailable_reason(self, action: str) -> str | None:
        return self.machine.unavailable_reason(action)


__all__ = [
    "CATALOG_ACTIONS",
    "DEFAULT_CAPABILITIES",
    "ENV_CAPABILITIES",
    "ENV_COMPUTER",
    "ENV_COMPUTERS",
    "ENV_DISABLED",
    "ENV_OWNER",
    "ENV_PROFILE",
    "CatalogConfig",
    "MachineConfig",
    "SharedProfile",
    "derive_root_keys",
]
