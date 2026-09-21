"""Harness adapters: what is specific to Claude Code, Codex and DSH.

The shared adapter covers the kinds every harness keeps in the same place.
These three cover the rest — commands, hooks, plugins, connectors and harness
settings — and each reads exactly one declared :class:`SetupRoot`, so a Claude
adapter never learns what is in ``~/.codex`` and a project adapter never
reaches above the project.

Three honesty rules, enforced here rather than documented:

* **A file is discovered or configured, never verified.**  Verified loading
  needs :class:`LoaderEvidence` — the harness's own account of what it loaded,
  handed in by whoever measured it.  Evidence for another harness is ignored,
  because Codex having loaded a command says nothing about Claude.
* **A harness that is not set up on this computer supports nothing.**  A
  :class:`HarnessProfile` with ``configured=False`` turns every availability
  row for that harness into *unsupported* with the reason, so a computer that
  never installed Codex is not shown a column of green checks.
* **A deliberate difference is a missing prerequisite with a reason.**  The
  profile's :class:`DeliberateException` entries (a subscription this machine
  does not have, a connector that needs a network it is not on) become a
  named, missing prerequisite and a *missing prerequisite* availability that
  says "deliberate" — Compare computers then shows a choice, not an error.

And the safety rule every adapter here shares: a credential *value* — a
settings ``env`` entry, a hook's ``--token`` flag, a connector's ``env`` block,
a route's API key — never reaches an item or a diagnostic.  Environment and
connector prerequisites report presence only; settings values are summarised
by shape when their name or value looks secret; and hook commands go through
the redactor before they can become a summary.
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from claude_discord.ai_setup_collector import AdapterResult, CollectionContext
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    DiagnosticSeverity,
    HarnessAvailability,
    InventoryDiagnostic,
    InventoryItem,
    ItemIdentity,
    Measurement,
    OwnershipClass,
    Prerequisite,
    PrerequisiteKind,
    PrerequisiteState,
    SetupKind,
    normalize_token,
    safe_text,
)
from claude_discord.ai_setup_redaction import (
    RedactionError,
    connector_prerequisites,
    environment_prerequisites,
    is_secret_name,
    safe_message,
    secret_reason,
)

from ._files import (
    FileFacts,
    HarnessLayout,
    SetupRoot,
    SourceError,
    modified_time,
    read_file_facts,
    read_text_bounded,
    sorted_children,
)
from .shared import KNOWN_HARNESSES, availability_for

#: Every harness an adapter here speaks for; used when a run declares none.
ALL_HARNESSES: tuple[str, ...] = (*KNOWN_HARNESSES, "dsh")

HARNESS_KINDS: tuple[SetupKind, ...] = (
    SetupKind.COMMAND,
    SetupKind.HOOK,
    SetupKind.PLUGIN,
    SetupKind.CONNECTOR,
    SetupKind.HARNESS_SETTING,
)

#: The DSH provider routes the runtime ships and the credential each one names.
DSH_ROUTES: tuple[tuple[str, str], ...] = (
    ("deepseek-official", "DEEPSEEK_API_KEY"),
    ("zai", "ZAI_API_KEY"),
)
#: Environment overrides a computer may set for DSH; reported as set, never read.
DSH_OVERRIDE_VARIABLES: tuple[str, ...] = (
    "DEEPSEEK_BASE_URL",
    "ZAI_BASE_URL",
    "CCDB_DSH_PATCH",
    "CCDB_DSH_CONTEXT_WINDOW",
    "DSH_ROUTES",
    "DSH_MODELS_PATH",
)
DSH_PATCH_FILE = "providers.patch.yml"

# Settings keys that are inventoried as their own kind rather than as a setting.
_CLAUDE_SETTINGS_AS_KINDS = frozenset({"hooks", "enabledPlugins", "mcpServers"})
_SUMMARY_LIMIT = 120
_MAX_CONFIG_BYTES = 1024 * 1024


# ---------------------------------------------------------------------------
# Evidence, profiles and deliberate exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoaderEvidence:
    """What one harness on this computer reported having loaded, and when."""

    harness: str
    verified_at: datetime
    loaded: frozenset[str]
    evidence: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "harness", normalize_token(self.harness, kind="harness"))
        object.__setattr__(self, "loaded", frozenset(self.loaded))
        object.__setattr__(self, "evidence", safe_text(self.evidence, kind="evidence"))
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("Loader evidence must carry a timezone-aware verification time")

    def covers(self, key: str) -> bool:
        return key in self.loaded


@dataclass(frozen=True, slots=True)
class DeliberateException:
    """A declared difference: this computer does not have ``item`` on purpose.

    ``item`` is an identity key or a key prefix (``connector:claude-home:``
    covers every connector from that source).
    """

    item: str
    reason: str
    kind: PrerequisiteKind = PrerequisiteKind.OTHER

    def __post_init__(self) -> None:
        item = self.item.strip()
        if not item:
            raise ValueError("A deliberate exception must name an item or an item prefix")
        object.__setattr__(self, "item", item)
        object.__setattr__(self, "reason", safe_text(self.reason, kind="exception reason"))
        object.__setattr__(self, "kind", PrerequisiteKind(self.kind))

    def applies_to(self, key: str) -> bool:
        return key.startswith(self.item)

    @property
    def prerequisite(self) -> Prerequisite:
        return Prerequisite(
            name=self.reason,
            state=PrerequisiteState.MISSING,
            kind=self.kind,
            detail="Deliberate difference declared for this computer",
        )


@dataclass(frozen=True, slots=True)
class HarnessProfile:
    """What this computer declares about one harness."""

    harness: str
    configured: bool = True
    exceptions: tuple[DeliberateException, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "harness", normalize_token(self.harness, kind="harness"))
        object.__setattr__(self, "exceptions", tuple(self.exceptions))

    def exception_for(self, key: str) -> DeliberateException | None:
        for entry in self.exceptions:
            if entry.applies_to(key):
                return entry
        return None


def load_exceptions(path: Path) -> tuple[DeliberateException, ...]:
    """Read declared exceptions from a JSON list; anything malformed is empty.

    Each entry is ``{"item": key-or-prefix, "reason": text, "kind"?: name}``.
    A file that cannot be read or parsed contributes nothing rather than
    stopping the inventory — an exception list is a refinement, not a source.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, list):
        return ()
    loaded: list[DeliberateException] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item = entry.get("item")
        reason = entry.get("reason")
        if not isinstance(item, str) or not isinstance(reason, str):
            continue
        kind_name = entry.get("kind")
        try:
            kind = PrerequisiteKind(kind_name) if isinstance(kind_name, str) else None
            loaded.append(
                DeliberateException(item=item, reason=reason, kind=kind or PrerequisiteKind.OTHER)
            )
        except ValueError:
            continue
    return tuple(loaded)


# ---------------------------------------------------------------------------
# Shared machinery
# ---------------------------------------------------------------------------


def _plural(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count} {noun if count == 1 else (plural or noun + 's')}"


def _safe_summary(text: str) -> str | None:
    """One bounded, redacted line — or the program name alone, or nothing."""
    try:
        summary = safe_message(text, kind="item summary", limit=_SUMMARY_LIMIT)
    except (RedactionError, ValueError):
        return None
    if secret_reason(summary) is None:
        return summary
    head = text.split(maxsplit=1)[0] if text.split() else ""
    if head and secret_reason(head) is None:
        return safe_text(head[:_SUMMARY_LIMIT], kind="item summary")
    return None


def _shape_summary(value: object) -> str:
    """Describe a structured value by shape only."""
    if isinstance(value, Mapping):
        return _plural(len(value), "entry", "entries")
    if isinstance(value, list | tuple):
        return _plural(len(value), "entry", "entries")
    return "configured"


def _setting_summary(name: str, value: object) -> str | None:
    """A setting's summary: the value when it is safe, its shape otherwise."""
    if name == "env" and isinstance(value, Mapping):
        return _plural(len(value), "variable set", "variables set")
    if is_secret_name(name):
        return "configured"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return "configured" if secret_reason(value) is not None else _safe_summary(value)
    return _shape_summary(value)


def _connector_summary(config: Mapping[str, object]) -> str:
    """``stdio: <program>`` or ``<transport>: <host>`` — never args, env or query."""
    kind = config.get("type")
    url = config.get("url")
    command = config.get("command")
    if isinstance(url, str) and url.strip() and (not isinstance(kind, str) or kind != "stdio"):
        transport = kind if isinstance(kind, str) and kind.strip() else "http"
        host = urlsplit(url.strip()).hostname or "unknown host"
        return f"{transport}: {host}"
    if isinstance(command, str) and command.strip():
        program = Path(command.split()[0]).name
        return f"stdio: {program}"
    return "connector"


def _mapping_or_none(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return value  # pyright: ignore[reportUnknownVariableType]
    return None


class _HarnessAdapterBase:
    """The scaffolding the three adapters share: one root, evidence, a profile."""

    harness: str = ""
    kinds: tuple[SetupKind, ...] = HARNESS_KINDS
    unsupported_by: Mapping[str, str] = {}

    def __init__(
        self,
        root: SetupRoot,
        *,
        evidence: Iterable[LoaderEvidence] = (),
        profile: HarnessProfile | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.root = root
        self.evidence: tuple[LoaderEvidence, ...] = tuple(
            entry for entry in evidence if entry.harness == self.harness
        )
        if profile is not None and profile.harness != self.harness:
            raise ValueError(
                f"A {profile.harness} profile cannot describe the {self.harness} harness"
            )
        self.profile = profile
        self.environ: Mapping[str, str] = os.environ if environ is None else environ

    @property
    def source_keys(self) -> tuple[str, ...]:
        return (self.root.key,)

    # -- what each subclass fills in ------------------------------------------

    def _gather(self, context: CollectionContext, sink: _Sink) -> None:
        raise NotImplementedError

    # -- the collection run -----------------------------------------------------

    def collect(self, context: CollectionContext) -> AdapterResult:
        sink = _Sink(self.root, context)
        if not self.root.exists:
            sink.warn(self.root.missing_message())
            return sink.result()
        self._gather(context, sink)
        return self._finished(sink)

    def _finished(self, sink: _Sink) -> AdapterResult:
        items = tuple(self._finish(item) for item in sink.items)
        return AdapterResult(items=items, diagnostics=tuple(sink.diagnostics))

    def _finish(self, item: InventoryItem) -> InventoryItem:
        """Apply the profile and the evidence to one item, in that order."""
        key = item.identity.key
        current = item.availability_for(self.harness)
        computer = item.source.computer
        if self.profile is not None and not self.profile.configured:
            return item.with_availability(
                HarnessAvailability(
                    harness=self.harness,
                    state=AvailabilityState.UNSUPPORTED,
                    computer=computer,
                    detail=f"The {self.harness} harness is not configured on this computer",
                )
            )
        exception = self.profile.exception_for(key) if self.profile is not None else None
        if exception is not None:
            prerequisites = (*item.prerequisites, exception.prerequisite)
            return item.with_prerequisites(*prerequisites).with_availability(
                HarnessAvailability(
                    harness=self.harness,
                    state=AvailabilityState.MISSING_PREREQUISITE,
                    computer=computer,
                    detail=f"Deliberate difference: {exception.reason}",
                )
            )
        for evidence in self.evidence:
            if evidence.covers(key) and (current is None or not current.is_verified):
                return item.with_availability(
                    HarnessAvailability(
                        harness=self.harness,
                        state=AvailabilityState.VERIFIED_LOADED,
                        computer=computer,
                        evidence=evidence.evidence,
                        verified_at=evidence.verified_at,
                    )
                )
        return item

    # -- helpers for subclasses --------------------------------------------------

    def _availability(
        self, context: CollectionContext, state: AvailabilityState
    ) -> tuple[HarnessAvailability, ...]:
        return availability_for(
            context,
            loaders=(self.harness,),
            unsupported=dict(self.unsupported_by),
            state=state,
            known=ALL_HARNESSES,
        )


@dataclass(slots=True)
class _Sink:
    """Where a gather run puts what it found; it also builds every item."""

    root: SetupRoot
    context: CollectionContext
    items: list[InventoryItem] = field(default_factory=list)
    diagnostics: list[InventoryDiagnostic] = field(default_factory=list)
    _names: set[tuple[SetupKind, str]] = field(default_factory=set)

    def warn(
        self, message: str, *, severity: DiagnosticSeverity = DiagnosticSeverity.WARNING
    ) -> None:
        self.diagnostics.append(self.context.diagnostic(self.root.key, severity, message))

    def result(self) -> AdapterResult:
        return AdapterResult(items=tuple(self.items), diagnostics=tuple(self.diagnostics))

    def unique_name(self, kind: SetupKind, name: str) -> str:
        candidate = name
        counter = 2
        while (kind, candidate) in self._names:
            candidate = f"{name} #{counter}"
            counter += 1
        self._names.add((kind, candidate))
        return candidate

    def add(
        self,
        kind: SetupKind,
        name: str,
        path: Path,
        *,
        availability: tuple[HarnessAvailability, ...],
        summary: str | None = None,
        classification: Classification = Classification.CUSTOM,
        ownership: OwnershipClass | None = None,
        prerequisites: tuple[Prerequisite, ...] = (),
        facts: FileFacts | None = None,
        modified_at: datetime | None = None,
    ) -> InventoryItem:
        name = self.unique_name(kind, name)
        effective_ownership = ownership or self.root.ownership
        changed = facts.modified_at if facts is not None else modified_at
        item = InventoryItem(
            identity=ItemIdentity(kind=kind, source_key=self.root.key, name=name),
            display_name=name,
            source=self.root.source_for(path, self.context, modified_at=changed),
            scope=self.root.scope_for(self.context, ownership=effective_ownership),
            classification=classification,
            ownership=effective_ownership,
            availability=availability,
            prerequisites=prerequisites,
            measurement=facts.measurement if facts is not None else Measurement.unknown(),
            fingerprint=facts.fingerprint if facts is not None else None,
            last_changed_at=changed,
            summary=summary,
        )
        self.items.append(item)
        return item

    def read_json(self, path: Path) -> Mapping[str, object] | None:
        """Parse one JSON object, or record why it could not be and return None."""
        if not (path.is_file() or path.is_symlink()):
            return None
        locator = self.root.locator_for(path)
        try:
            text = read_text_bounded(self.root, path, limit=_MAX_CONFIG_BYTES)
            raw = json.loads(text)
        except SourceError as problem:
            self.warn(str(problem))
            return None
        except ValueError:
            self.warn(f"{locator} could not be parsed as JSON and was skipped")
            return None
        data = _mapping_or_none(raw)
        if data is None:
            self.warn(f"{locator} is not a JSON object and was skipped")
        return data

    def read_toml(self, path: Path) -> Mapping[str, object] | None:
        if not (path.is_file() or path.is_symlink()):
            return None
        locator = self.root.locator_for(path)
        try:
            text = read_text_bounded(self.root, path, limit=_MAX_CONFIG_BYTES)
            return tomllib.loads(text)
        except SourceError as problem:
            self.warn(str(problem))
        except (tomllib.TOMLDecodeError, ValueError):
            self.warn(f"{locator} could not be parsed as TOML and was skipped")
        return None

    def facts(self, path: Path) -> FileFacts | None:
        try:
            return read_file_facts(self.root, path)
        except SourceError as problem:
            self.warn(str(problem))
            return None

    def mtime(self, path: Path) -> datetime | None:
        try:
            return modified_time(path)
        except OSError:
            return None


def _markdown_commands(sink: _Sink, directory: Path) -> Iterable[tuple[str, Path]]:
    if not directory.is_dir():
        return ()
    found: list[tuple[str, Path]] = []
    for entry in sorted_children(directory):
        if entry.suffix == ".md" and (entry.is_file() or entry.is_symlink()):
            found.append((entry.stem, entry))
        elif entry.is_dir():
            for nested in sorted_children(entry):
                if nested.suffix == ".md" and nested.is_file():
                    found.append((f"{entry.name}/{nested.stem}", nested))
    return found


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


class ClaudeHarnessAdapter(_HarnessAdapterBase):
    """``settings*.json``, hooks, ``commands/``, plugins and MCP servers.

    From a Claude home: ``settings.json``, ``settings.local.json`` (one
    computer), ``commands/*.md``, ``plugins/marketplaces/*/plugins/*`` and the
    ``mcpServers`` of the ``~/.claude.json`` companion.  From a project:
    ``.claude/settings*.json``, ``.claude/commands/`` and ``.mcp.json``.
    """

    name = "claude-harness"
    harness = "claude"
    unsupported_by = {"codex": "Codex does not read Claude Code settings, hooks or commands"}

    def _gather(self, context: CollectionContext, sink: _Sink) -> None:
        root = self.root
        if root.layout is HarnessLayout.CLAUDE_HOME:
            config_dir = root.path
            connectors = [entry for entry in root.companions if entry.name == ".claude.json"]
        elif root.layout is HarnessLayout.PROJECT:
            config_dir = root.path / ".claude"
            connectors = [root.path / ".mcp.json"]
        else:
            sink.warn(f"The {root.label} root is not a Claude Code layout")
            return
        configured = self._availability(context, AvailabilityState.CONFIGURED)
        discovered = self._availability(context, AvailabilityState.DISCOVERED)

        settings = self._settings(sink, config_dir / "settings.json", prefix="", ownership=None)
        self._settings(
            sink,
            config_dir / "settings.local.json",
            prefix="local/",
            ownership=OwnershipClass.MACHINE,
        )
        for name, path in _markdown_commands(sink, config_dir / "commands"):
            sink.add(SetupKind.COMMAND, name, path, availability=discovered, facts=sink.facts(path))
        if root.layout is HarnessLayout.CLAUDE_HOME:
            self._plugins(sink, settings, configured)
        for path in connectors:
            data = sink.read_json(path)
            if data is None:
                continue
            self._connectors(sink, path, _mapping_or_none(data.get("mcpServers")) or {})

    def _settings(
        self,
        sink: _Sink,
        path: Path,
        *,
        prefix: str,
        ownership: OwnershipClass | None,
    ) -> Mapping[str, object]:
        data = sink.read_json(path)
        if data is None:
            return {}
        modified = sink.mtime(path)
        availability = self._availability(sink.context, AvailabilityState.CONFIGURED)
        for key, value in data.items():
            if key in _CLAUDE_SETTINGS_AS_KINDS or value is None:
                continue
            sink.add(
                SetupKind.HARNESS_SETTING,
                f"{prefix}{key}",
                path,
                availability=availability,
                summary=_setting_summary(key, value),
                ownership=ownership,
                modified_at=modified,
            )
        hooks = _mapping_or_none(data.get("hooks"))
        if hooks:
            self._hooks(sink, path, hooks, prefix=prefix, ownership=ownership, modified=modified)
        return data

    def _hooks(
        self,
        sink: _Sink,
        path: Path,
        hooks: Mapping[str, object],
        *,
        prefix: str,
        ownership: OwnershipClass | None,
        modified: datetime | None,
    ) -> None:
        availability = self._availability(sink.context, AvailabilityState.CONFIGURED)
        for event, groups in hooks.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                group_map = _mapping_or_none(group)
                if group_map is None:
                    continue
                matcher = group_map.get("matcher")
                name = f"{prefix}{event}"
                if isinstance(matcher, str) and matcher.strip():
                    name = f"{name}/{matcher.strip()}"
                commands: list[str] = []
                entries = group_map.get("hooks")
                for entry in entries if isinstance(entries, list) else []:
                    entry_map = _mapping_or_none(entry)
                    if entry_map is None:
                        continue
                    command = entry_map.get("command")
                    hook_type = entry_map.get("type")
                    if isinstance(command, str) and command.strip():
                        commands.append(command)
                    elif isinstance(hook_type, str):
                        commands.append(f"{hook_type} hook")
                summaries = [text for text in (_safe_summary(c) for c in commands) if text]
                summary = _safe_summary("; ".join(summaries)) if summaries else None
                sink.add(
                    SetupKind.HOOK,
                    name,
                    path,
                    availability=availability,
                    summary=summary,
                    ownership=ownership,
                    modified_at=modified,
                )

    def _plugins(
        self,
        sink: _Sink,
        settings: Mapping[str, object],
        configured: tuple[HarnessAvailability, ...],
    ) -> None:
        enabled_raw = _mapping_or_none(settings.get("enabledPlugins")) or {}
        enabled = {name for name, flag in enabled_raw.items() if flag is True}
        seen: set[str] = set()
        marketplaces = self.root.path / "plugins" / "marketplaces"
        for market in sorted_children(marketplaces) if marketplaces.is_dir() else []:
            plugins_dir = market / "plugins"
            if not plugins_dir.is_dir():
                continue
            for plugin_dir in sorted_children(plugins_dir):
                if not plugin_dir.is_dir():
                    continue
                name = f"{plugin_dir.name}@{market.name}"
                seen.add(name)
                manifest = plugin_dir / "plugin.json"
                facts = sink.facts(manifest) if manifest.is_file() else None
                is_enabled = name in enabled
                sink.add(
                    SetupKind.PLUGIN,
                    name,
                    manifest if manifest.is_file() else plugin_dir,
                    availability=configured
                    if is_enabled
                    else self._availability(sink.context, AvailabilityState.DISCOVERED),
                    summary="enabled" if is_enabled else "available, not enabled",
                    classification=Classification.OVERRIDDEN_BUILTIN
                    if is_enabled
                    else Classification.BUILTIN,
                    facts=facts,
                    modified_at=sink.mtime(plugin_dir) if facts is None else None,
                )
        settings_path = self.root.path / "settings.json"
        for name in sorted(enabled - seen):
            sink.add(
                SetupKind.PLUGIN,
                name,
                settings_path,
                availability=configured,
                summary="enabled",
                modified_at=sink.mtime(settings_path),
            )

    def _connectors(self, sink: _Sink, path: Path, servers: Mapping[str, object]) -> None:
        modified = sink.mtime(path)
        for name, raw in servers.items():
            config = _mapping_or_none(raw)
            if config is None:
                continue
            env = _mapping_or_none(config.get("env")) or {}
            prerequisites = connector_prerequisites(env, required=sorted(env))
            state = (
                AvailabilityState.MISSING_PREREQUISITE
                if any(entry.is_missing for entry in prerequisites)
                else AvailabilityState.CONFIGURED
            )
            sink.add(
                SetupKind.CONNECTOR,
                name,
                path,
                availability=self._availability(sink.context, state),
                summary=_connector_summary(config),
                prerequisites=prerequisites,
                modified_at=modified,
            )


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


class CodexHarnessAdapter(_HarnessAdapterBase):
    """``config.toml`` (settings, profiles, hooks, MCP servers) and ``prompts/``."""

    name = "codex-harness"
    harness = "codex"
    unsupported_by = {"claude": "Claude Code does not read the Codex configuration"}

    def _gather(self, context: CollectionContext, sink: _Sink) -> None:
        if self.root.layout is not HarnessLayout.CODEX_HOME:
            sink.warn(f"The {self.root.label} root is not a Codex layout")
            return
        config = self.root.path / "config.toml"
        data = sink.read_toml(config)
        if data is not None:
            self._config(sink, config, data)
        discovered = self._availability(context, AvailabilityState.DISCOVERED)
        for name, path in _markdown_commands(sink, self.root.path / "prompts"):
            sink.add(SetupKind.COMMAND, name, path, availability=discovered, facts=sink.facts(path))

    def _config(self, sink: _Sink, path: Path, data: Mapping[str, object]) -> None:
        modified = sink.mtime(path)
        configured = self._availability(sink.context, AvailabilityState.CONFIGURED)
        for key, value in data.items():
            if key == "mcp_servers":
                self._connectors(sink, path, _mapping_or_none(value) or {}, modified)
                continue
            if key == "hooks":
                for hook, command in (_mapping_or_none(value) or {}).items():
                    summary = (
                        _safe_summary(command)
                        if isinstance(command, str)
                        else _shape_summary(command)
                    )
                    sink.add(
                        SetupKind.HOOK,
                        hook,
                        path,
                        availability=configured,
                        summary=summary,
                        modified_at=modified,
                    )
                continue
            if key == "profiles":
                for profile, table in (_mapping_or_none(value) or {}).items():
                    sink.add(
                        SetupKind.HARNESS_SETTING,
                        f"profiles.{profile}",
                        path,
                        availability=configured,
                        summary=_shape_summary(table),
                        modified_at=modified,
                    )
                continue
            sink.add(
                SetupKind.HARNESS_SETTING,
                key,
                path,
                availability=configured,
                summary=_setting_summary(key, value),
                modified_at=modified,
            )

    def _connectors(
        self,
        sink: _Sink,
        path: Path,
        servers: Mapping[str, object],
        modified: datetime | None,
    ) -> None:
        for name, raw in servers.items():
            config = _mapping_or_none(raw)
            if config is None:
                continue
            env = _mapping_or_none(config.get("env")) or {}
            prerequisites = list(connector_prerequisites(env, required=sorted(env)))
            variable = config.get("bearer_token_env_var")
            if isinstance(variable, str) and variable.strip():
                prerequisites.extend(
                    environment_prerequisites((variable.strip(),), environ=self.environ)
                )
            state = (
                AvailabilityState.MISSING_PREREQUISITE
                if any(entry.is_missing for entry in prerequisites)
                else AvailabilityState.CONFIGURED
            )
            sink.add(
                SetupKind.CONNECTOR,
                name,
                path,
                availability=self._availability(sink.context, state),
                summary=_connector_summary(config),
                prerequisites=tuple(prerequisites),
                modified_at=modified,
            )


# ---------------------------------------------------------------------------
# DSH
# ---------------------------------------------------------------------------


class DshHarnessAdapter(_HarnessAdapterBase):
    """Provider routes (credential presence), environment overrides, the patch file.

    DSH has no home directory of its own worth walking: its routes are built
    into the runtime and the user's part is a credential per route, a few
    environment overrides and one patch file.  The patch file is fingerprinted
    and measured, never parsed — it may carry an API key.
    """

    name = "dsh-harness"
    harness = "dsh"
    kinds = (SetupKind.CONNECTOR, SetupKind.HARNESS_SETTING)

    def _gather(self, context: CollectionContext, sink: _Sink) -> None:
        anchor = self.root.path
        for route, variable in DSH_ROUTES:
            prerequisites = environment_prerequisites((variable,), environ=self.environ)
            present = all(entry.is_satisfied for entry in prerequisites)
            sink.add(
                SetupKind.CONNECTOR,
                route,
                anchor,
                availability=self._availability(
                    context,
                    AvailabilityState.CONFIGURED
                    if present
                    else AvailabilityState.MISSING_PREREQUISITE,
                ),
                summary="DSH provider route",
                classification=Classification.CUSTOM if present else Classification.BUILTIN,
                prerequisites=prerequisites,
            )
        configured = self._availability(context, AvailabilityState.CONFIGURED)
        for variable in DSH_OVERRIDE_VARIABLES:
            if (self.environ.get(variable) or "").strip():
                sink.add(
                    SetupKind.HARNESS_SETTING,
                    variable,
                    anchor,
                    availability=configured,
                    summary="set",
                    ownership=OwnershipClass.MACHINE,
                )
        patch = anchor / DSH_PATCH_FILE
        if self.root.exists and (patch.is_file() or patch.is_symlink()):
            facts = sink.facts(patch)
            sink.add(
                SetupKind.HARNESS_SETTING,
                DSH_PATCH_FILE,
                patch,
                availability=configured,
                summary="extra provider routes (patch layer)",
                facts=facts,
                modified_at=sink.mtime(patch) if facts is None else None,
            )

    def collect(self, context: CollectionContext) -> AdapterResult:
        sink = _Sink(self.root, context)
        if not self.root.exists:
            sink.warn(
                f"{self.root.missing_message()}; built-in routes and overrides were still checked",
                severity=DiagnosticSeverity.INFO,
            )
        self._gather(context, sink)
        return self._finished(sink)


__all__ = [
    "ALL_HARNESSES",
    "DSH_OVERRIDE_VARIABLES",
    "DSH_PATCH_FILE",
    "DSH_ROUTES",
    "HARNESS_KINDS",
    "ClaudeHarnessAdapter",
    "CodexHarnessAdapter",
    "DeliberateException",
    "DshHarnessAdapter",
    "HarnessProfile",
    "LoaderEvidence",
    "load_exceptions",
]
