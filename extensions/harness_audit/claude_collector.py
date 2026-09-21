"""Claude Code effective-input collector (task 2.2).

Resolves what a Discord-launched Claude session actually receives from four
kinds of evidence, without ever running the CLI or asking a model:

* **Configuration** — the files discovery found in the locations the pinned
  vendor guidance names (memory, settings, skills, commands, MCP config).
* **Invocation** — the argv the relay builds.  ``--setting-sources`` decides
  which scopes exist at all, ``--strict-mcp-config`` decides which servers can
  connect, ``--append-system-prompt`` *is* a loaded input, and ``--model`` /
  ``--permission-mode`` are recorded only so the report can say which path it
  inspected.
* **Symlinks** — a memory file that resolves to the canonical ``~/AGENTS.md``
  is one input with two sources, not two inputs.
* **Session metadata** — an existing transcript under
  ``<claude_home>/projects/`` proves the CLI ran in that project on a given
  version.  Only its ``cwd``, ``sessionId`` and ``version`` keys are read.

Evidence levels are earned, never assumed:

==================  ===========================================================
``loaded``          transcript for the cwd + invocation does not exclude the
                    scope + the version is covered by the pinned guidance
``configured``      on disk in a read location, no session proof (or an MCP
                    server, which no transcript proves connected)
``installed-only``  read only on invocation (commands, agents), a scope the
                    invocation excludes, a disabled or non-strict MCP server,
                    a file Claude does not read (``AGENTS.md``)
``unknown``         unparseable file, unreadable file, or a CLI version the
                    manifest does not cover
==================  ===========================================================
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from extensions.harness_audit.collection import (
    CollectionResult,
    Invocation,
    SessionObservation,
    evidence,
    same_directory,
    unknown,
)
from extensions.harness_audit.discovery import (
    CLAUDE_HOOK_EVENTS,
    ContentSignals,
    DiscoveredFile,
    DiscoveryResult,
    SettingsFacts,
    TranscriptFile,
)
from extensions.harness_audit.models import (
    AuditCheck,
    AuditTarget,
    CoverageGap,
    EvidenceLevel,
    EvidenceRecord,
    Harness,
    HarnessInventory,
    InventoryItem,
    ItemSource,
    Machine,
    RedactionStatus,
    Scope,
    SourceKind,
)
from extensions.harness_audit.redaction import (
    PrivateBody,
    measure,
    redact_environment,
    withhold_body,
)
from extensions.harness_audit.sources import SourceManifest

_ALL_SETTING_SOURCES = ("user", "project", "local")
_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "CCDB_", "PATH")
_MAX_TRANSCRIPT_LINES = 200

# Precedence as documented: command line beats local beats project beats user.
_PRECEDENCE = {"cli": 1, "local": 2, "project": 3, "user": 4}


@dataclass(frozen=True, slots=True)
class ClaudeInvocation(Invocation):
    """The relay's Claude argv, with the flags that decide what loads."""

    @property
    def setting_sources(self) -> tuple[str, ...]:
        raw = self.option("--setting-sources")
        if raw is None:
            return _ALL_SETTING_SOURCES
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    @property
    def append_system_prompt(self) -> str | None:
        return self.option("--append-system-prompt")

    @property
    def system_prompt(self) -> str | None:
        return self.option("--system-prompt")

    @property
    def strict_mcp_config(self) -> bool:
        return self.has_flag("--strict-mcp-config")

    @property
    def mcp_configs(self) -> tuple[str, ...]:
        return self.options("--mcp-config")

    @property
    def disallowed_tools(self) -> tuple[str, ...]:
        tools: list[str] = []
        for value in self.options("--disallowedTools", "--disallowed-tools"):
            tools.extend(part.strip() for part in value.split(",") if part.strip())
        return tuple(tools)

    @property
    def model(self) -> str | None:
        return self.option("--model")

    @property
    def permission_mode(self) -> str | None:
        return self.option("--permission-mode")


# --------------------------------------------------------------------------- #
# Session metadata
# --------------------------------------------------------------------------- #


def read_claude_sessions(transcripts: Iterable[TranscriptFile]) -> tuple[SessionObservation, ...]:
    """Read only ``cwd``/``version`` from each Claude transcript; never a message."""
    observations: list[SessionObservation] = []
    for transcript in transcripts:
        if transcript.harness is not Harness.CLAUDE:
            continue
        cwd = version = ""
        error = ""
        try:
            with transcript.absolute.open("r", encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index >= _MAX_TRANSCRIPT_LINES or (cwd and version):
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, Mapping):
                        continue
                    cwd = cwd or str(record.get("cwd", "") or "")
                    version = version or str(record.get("version", "") or "")
        except OSError as caught:
            error = f"{type(caught).__name__}: unreadable"
        if not (cwd or version) and not error:
            error = "no session metadata found"
        observations.append(
            SessionObservation(
                harness=Harness.CLAUDE,
                path=transcript.path,
                cwd=cwd,
                version=version,
                parse_error=error,
            )
        )
    return tuple(observations)


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #


def collect_claude(
    discovery: DiscoveryResult,
    *,
    machine: Machine,
    invocation: ClaudeInvocation | None,
    manifest: SourceManifest,
    collected_at: datetime,
) -> CollectionResult:
    """Build the Claude inventory for one machine from local evidence only."""
    target = AuditTarget(machine, Harness.CLAUDE)
    files = discovery.for_harness(Harness.CLAUDE)
    by_path = {entry.path: entry for entry in discovery.files}
    sessions = read_claude_sessions(discovery.transcripts)
    notes: list[str] = []

    proof = _session_proof(sessions, invocation)
    version = _version(invocation, proof)
    guidance = manifest.require(Harness.CLAUDE, AuditCheck.LOAD_BEHAVIOR)
    covered: bool | None = None if not version else guidance.covers_version(version)
    if invocation is None:
        notes.append(
            "no invocation record: nothing can be proven loaded; evidence stops at configured"
        )
    elif proof is None:
        notes.append("no session transcript for the invocation cwd; evidence stops at configured")
    if version and covered is False:
        notes.append(
            f"claude {version} is outside the pinned guidance range {guidance.version_range};"
            " load evidence is unknown until the manifest is refreshed"
        )

    ctx = _Context(
        target=target,
        invocation=invocation,
        proof=proof,
        version=version,
        covered=covered,
        by_path=by_path,
    )
    items: list[InventoryItem] = []
    signals: dict[str, ContentSignals] = {}
    settings: dict[str, SettingsFacts] = {}
    file_index: dict[str, DiscoveredFile] = {}
    private_bodies: list[PrivateBody] = []

    for entry in files:
        if entry.harness is None and entry.kind is SourceKind.GLOBAL_INSTRUCTIONS:
            # ~/AGENTS.md reaches Claude only through a link; the link is the item.
            continue
        for item in _items_for(entry, ctx):
            items.append(item)
            signals[item.item_id] = entry.signals
            file_index[item.item_id] = entry
            if entry.settings is not None:
                settings[item.item_id] = entry.settings

    environment: Mapping[str, str] = {}
    if invocation is not None:
        items.extend(_invocation_items(invocation, ctx, private_bodies))
        relevant = {
            name: "" for name in invocation.environment_names if name.startswith(_ENV_PREFIXES)
        }
        environment = redact_environment(relevant).record
        for name in environment:
            items.append(_environment_item(name, ctx))

    inventory = HarnessInventory(target=target, collected_at=collected_at, items=tuple(items))
    gap: CoverageGap | None = None
    if not files and invocation is None:
        gap = CoverageGap(
            target=target,
            reason="no Claude configuration was readable and no invocation record was given",
            missing_evidence=("claude home contents", "Discord invocation record"),
            observed_at=collected_at,
        )
    return CollectionResult(
        inventory=inventory,
        private_bodies=tuple(private_bodies),
        environment=environment,
        signals=signals,
        settings=settings,
        files=file_index,
        sessions=sessions,
        coverage_gap=gap,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Context:
    target: AuditTarget
    invocation: ClaudeInvocation | None
    proof: SessionObservation | None
    version: str
    covered: bool | None
    by_path: Mapping[str, DiscoveredFile]

    @property
    def can_prove_loaded(self) -> bool:
        return self.invocation is not None and self.proof is not None and self.covered is True


def _session_proof(
    sessions: Iterable[SessionObservation], invocation: ClaudeInvocation | None
) -> SessionObservation | None:
    if invocation is None:
        return None
    for session in sessions:
        if session.parse_error:
            continue
        if session.cwd and same_directory(session.cwd, invocation.cwd):
            return session
    return None


def _version(invocation: ClaudeInvocation | None, proof: SessionObservation | None) -> str:
    if invocation is not None and invocation.cli_version:
        return invocation.cli_version
    if proof is not None:
        return proof.version
    return ""


def _item_id(kind: SourceKind, reference: str) -> str:
    return f"claude/{kind.value}/{reference}"


def _scope_source(entry: DiscoveredFile) -> str:
    if entry.scope is Scope.PROJECT:
        return "local" if ".local." in entry.path.rsplit("/", 1)[-1] else "project"
    return "local" if ".local." in entry.path.rsplit("/", 1)[-1] else "user"


def _start_evidence(entry: DiscoveredFile, ctx: _Context, what: str) -> EvidenceRecord:
    """The shared chain for anything Claude reads at session start."""
    if entry.read_error:
        return unknown(
            "read-file", f"{what} could not be read", "readable file", reference=entry.path
        )
    if entry.settings is not None and entry.settings.parse_error:
        return unknown(
            "parse-settings",
            f"{what} is not parseable ({entry.settings.parse_error})",
            "parseable settings file",
            reference=entry.path,
        )
    scope_name = _scope_source(entry)
    if ctx.invocation is not None and scope_name not in ctx.invocation.setting_sources:
        return evidence(
            EvidenceLevel.INSTALLED_ONLY,
            "invocation-setting-sources",
            f"--setting-sources {','.join(ctx.invocation.setting_sources) or '""'} excludes"
            f" the {scope_name} scope, so {what} is never read",
            reference=entry.path,
            vendor=True,
        )
    if ctx.version and ctx.covered is False:
        return unknown(
            "session-transcript+invocation",
            f"claude {ctx.version} is outside the pinned guidance; load behavior of {what}"
            " cannot be asserted",
            f"official guidance covering claude {ctx.version}",
            reference=entry.path,
        )
    if ctx.can_prove_loaded and ctx.proof is not None:
        return evidence(
            EvidenceLevel.LOADED,
            "session-transcript+invocation",
            f"transcript {ctx.proof.path} shows claude {ctx.proof.version or ctx.version} ran in"
            f" the invocation cwd with the {scope_name} scope enabled",
            reference=entry.path,
            vendor=True,
        )
    return evidence(
        EvidenceLevel.CONFIGURED,
        "on-disk-in-read-location",
        f"{what} is in a location Claude reads at start; no transcript proves a session",
        reference=entry.path,
        vendor=True,
    )


def _on_invoke_evidence(entry: DiscoveredFile, what: str, doc: str) -> EvidenceRecord:
    return evidence(
        EvidenceLevel.INSTALLED_ONLY,
        "vendor-documented-on-invoke",
        f"{what} is read only when invoked ({doc}); nothing reaches the session at start",
        reference=entry.path,
        vendor=True,
    )


def _items_for(entry: DiscoveredFile, ctx: _Context) -> list[InventoryItem]:
    kind = entry.kind
    if kind in (SourceKind.GLOBAL_INSTRUCTIONS, SourceKind.PROJECT_INSTRUCTIONS):
        return [_memory_item(entry, ctx)]
    if kind is SourceKind.HARNESS_SETTING:
        return _settings_items(entry, ctx)
    if kind is SourceKind.SKILL:
        return [_skill_item(entry, ctx)]
    if kind in (SourceKind.COMMAND, SourceKind.TOOL):
        doc = "slash command" if kind is SourceKind.COMMAND else "subagent"
        return [_plain_item(entry, ctx, _on_invoke_evidence(entry, entry.path, doc))]
    if kind is SourceKind.CONNECTOR:
        return _connector_items(entry, ctx)
    if kind is SourceKind.PLUGIN:
        return [_plugin_item(entry, ctx)]
    return [_plain_item(entry, ctx, _start_evidence(entry, ctx, entry.path))]


def _plain_item(
    entry: DiscoveredFile,
    ctx: _Context,
    record: EvidenceRecord,
    *,
    kind: SourceKind | None = None,
    effective_behavior: str = "",
    size_override: int | None = None,
) -> InventoryItem:
    del size_override
    sources = [
        ItemSource(
            reference=entry.path,
            scope=entry.scope,
            evidence=evidence(record.level, "read-location", f"Claude looks for {entry.path}")
            if record.level is not EvidenceLevel.UNKNOWN
            else record,
            precedence=1,
        )
    ]
    behavior = effective_behavior
    if entry.is_symlink and entry.resolved_path != entry.path:
        sources.append(
            ItemSource(
                reference=entry.resolved_path,
                scope=entry.scope,
                evidence=evidence(
                    record.level if record.level is not EvidenceLevel.UNKNOWN else record.level,
                    "resolved-symlink",
                    f"{entry.path} is a link to {entry.resolved_path}",
                )
                if record.level is not EvidenceLevel.UNKNOWN
                else record,
                precedence=2,
            )
        )
        behavior = behavior or (
            f"Claude reads {entry.path}, which resolves to the canonical {entry.resolved_path};"
            " one copy of the content reaches the session"
        )
    return InventoryItem(
        item_id=_item_id(kind or entry.kind, entry.path),
        target=ctx.target,
        kind=kind or entry.kind,
        label=entry.path,
        sources=tuple(sources),
        evidence=record,
        size=entry.size,
        permissions=entry.permissions,
        content_hash=entry.content_hash,
        redaction=entry.redaction,
        effective_behavior=behavior,
    )


def _memory_item(entry: DiscoveredFile, ctx: _Context) -> InventoryItem:
    name = entry.path.rsplit("/", 1)[-1]
    if name == "AGENTS.md":
        # Claude reads CLAUDE.md; a sibling AGENTS.md is only reachable through
        # an import, which this collector does not resolve.
        sibling = ctx.by_path.get(entry.path.rsplit("/", 1)[0] + "/CLAUDE.md")
        if sibling is not None and sibling.signals.import_references:
            record = unknown(
                "memory-import",
                f"{sibling.path} declares imports; whether one of them is {entry.path}"
                " is not resolved",
                "resolved @import targets",
                reference=entry.path,
            )
        else:
            record = evidence(
                EvidenceLevel.INSTALLED_ONLY,
                "vendor-documented-file-name",
                f"{entry.path} is not a Claude memory file name; nothing imports it",
                reference=entry.path,
                vendor=True,
            )
        return _plain_item(entry, ctx, record)
    return _plain_item(entry, ctx, _start_evidence(entry, ctx, entry.path))


def _settings_items(entry: DiscoveredFile, ctx: _Context) -> list[InventoryItem]:
    record = _start_evidence(entry, ctx, entry.path)
    scope_name = _scope_source(entry)
    items = [
        _plain_item(
            entry,
            ctx,
            record,
            effective_behavior=f"{scope_name} settings; precedence {_PRECEDENCE[scope_name]}"
            " (command line beats local beats project beats user)",
        )
    ]
    facts = entry.settings
    if facts is None:
        return items
    for hook in facts.hooks:
        reference = f"{entry.path}#{hook.event}[{hook.matcher or '*'}]"
        known = hook.event in CLAUDE_HOOK_EVENTS
        detail = f"registered in {entry.path} for {hook.event}" + (
            "" if known else " (not a documented lifecycle event)"
        )
        hook_evidence = (
            record
            if record.level is EvidenceLevel.UNKNOWN
            else evidence(
                record.level,
                "settings-hook-registration",
                detail,
                reference=entry.path,
                vendor=True,
            )
        )
        items.append(
            InventoryItem(
                item_id=_item_id(SourceKind.HOOK, reference),
                target=ctx.target,
                kind=SourceKind.HOOK,
                label=f"hook {hook.event} matcher={hook.matcher or '*'}",
                sources=(
                    ItemSource(reference, entry.scope, hook_evidence, _PRECEDENCE[scope_name]),
                ),
                evidence=hook_evidence,
                size=None,
                permissions=entry.permissions,
                content_hash=hook.command_hash,
                redaction=RedactionStatus.WITHHELD,
                effective_behavior=""
                if known
                else f"event {hook.event} is not documented; the hook can never fire",
            )
        )
    return items


def _skill_item(entry: DiscoveredFile, ctx: _Context) -> InventoryItem:
    if ctx.invocation is not None and "Skill" in ctx.invocation.disallowed_tools:
        record = evidence(
            EvidenceLevel.INSTALLED_ONLY,
            "invocation-disallowed-tools",
            "the Skill tool is disallowed by the invocation, so no skill index is offered",
            reference=entry.path,
            vendor=True,
        )
    else:
        record = _start_evidence(entry, ctx, entry.path)
    frontmatter = entry.signals.frontmatter_bytes
    return _plain_item(
        entry,
        ctx,
        record,
        effective_behavior=(
            f"name and description ({frontmatter} bytes of frontmatter) load at session start;"
            f" the body ({entry.size.byte_size} bytes) loads on invocation"
        ),
    )


def _connector_items(entry: DiscoveredFile, ctx: _Context) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    facts = entry.settings
    base = _start_evidence(entry, ctx, entry.path)
    if facts is None or base.level is EvidenceLevel.UNKNOWN:
        return [_plain_item(entry, ctx, base, kind=SourceKind.HARNESS_SETTING)]
    for server in facts.mcp_servers:
        reference = f"{entry.path}#{server}"
        if server in facts.disabled_mcp_servers:
            record = evidence(
                EvidenceLevel.INSTALLED_ONLY,
                "settings-disabled-server",
                f"{server} is listed as disabled in {entry.path}",
                reference=entry.path,
                vendor=True,
            )
        elif ctx.invocation is not None and ctx.invocation.strict_mcp_config:
            record = evidence(
                EvidenceLevel.INSTALLED_ONLY,
                "invocation-strict-mcp-config",
                f"--strict-mcp-config limits servers to --mcp-config; {server} from {entry.path}"
                " is not offered",
                reference=entry.path,
                vendor=True,
            )
        elif base.level is EvidenceLevel.INSTALLED_ONLY:
            record = base
        else:
            record = evidence(
                EvidenceLevel.CONFIGURED,
                "settings-mcp-server",
                f"{server} is configured in {entry.path}; no transcript proves it connected"
                " or exposed tools",
                reference=entry.path,
                vendor=True,
            )
        items.append(
            InventoryItem(
                item_id=_item_id(SourceKind.CONNECTOR, reference),
                target=ctx.target,
                kind=SourceKind.CONNECTOR,
                label=f"MCP server {server}",
                sources=(
                    ItemSource(reference, entry.scope, record, _PRECEDENCE[_scope_source(entry)]),
                ),
                evidence=record,
                size=None,
                permissions=entry.permissions,
                content_hash="",
                redaction=entry.redaction,
            )
        )
    if not items:
        items.append(_plain_item(entry, ctx, base, kind=SourceKind.HARNESS_SETTING))
    return items


def _plugin_item(entry: DiscoveredFile, ctx: _Context) -> InventoryItem:
    enabled_somewhere = any(
        other.settings is not None and "enabledPlugins" in other.settings.keys
        for other in ctx.by_path.values()
        if other.harness is Harness.CLAUDE
    )
    if enabled_somewhere:
        record = _start_evidence(entry, ctx, entry.path)
        if record.level is EvidenceLevel.LOADED:
            record = evidence(
                EvidenceLevel.CONFIGURED,
                "settings-enabled-plugins",
                "an enabledPlugins entry exists; whether this plugin is enabled is not resolved",
                reference=entry.path,
                vendor=True,
            )
    else:
        record = unknown(
            "plugin-registry",
            f"{entry.path} is installed but no settings file lists enabledPlugins",
            "enabledPlugins entry naming this plugin",
            reference=entry.path,
        )
    return _plain_item(entry, ctx, record)


def _invocation_items(
    invocation: ClaudeInvocation, ctx: _Context, private_bodies: list[PrivateBody]
) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for flag, text in (
        ("append-system-prompt", invocation.append_system_prompt),
        ("system-prompt", invocation.system_prompt),
    ):
        if text is None:
            continue
        field = f"claude/bot-addition/{flag}"
        body = withhold_body(text, field=field)
        private_bodies.append(body)
        record = evidence(
            EvidenceLevel.LOADED,
            "invocation-argument",
            f"--{flag} is passed on every launch; the body is withheld and represented by"
            f" {body.content_hash[:19]}",
            reference=f"invocation --{flag}",
            vendor=True,
        )
        items.append(
            InventoryItem(
                item_id=field,
                target=ctx.target,
                kind=SourceKind.BOT_ADDITION,
                label=f"bot --{flag}",
                sources=(
                    ItemSource(f"invocation --{flag}", Scope.SESSION, record, _PRECEDENCE["cli"]),
                ),
                evidence=record,
                size=measure(text, estimate_tokens=True),
                content_hash=body.content_hash,
                redaction=RedactionStatus.WITHHELD,
            )
        )
    for name, value in (
        ("model", invocation.model),
        ("permission-mode", invocation.permission_mode),
        ("setting-sources", invocation.option("--setting-sources")),
    ):
        if value is None:
            continue
        record = evidence(
            EvidenceLevel.LOADED,
            "invocation-argument",
            f"--{name} {value} is passed on every launch",
            reference=f"invocation --{name}",
            vendor=True,
        )
        behavior = (
            "model selection; excluded from cleanup by the audit's scope"
            if name == "model"
            else f"{name} chosen by the relay; recorded to identify the inspected path"
        )
        items.append(
            InventoryItem(
                item_id=f"claude/harness-setting/invocation#{name}",
                target=ctx.target,
                kind=SourceKind.HARNESS_SETTING,
                label=f"--{name} {value}",
                sources=(
                    ItemSource(f"invocation --{name}", Scope.SESSION, record, _PRECEDENCE["cli"]),
                ),
                evidence=record,
                effective_behavior=behavior,
            )
        )
    return items


def _environment_item(name: str, ctx: _Context) -> InventoryItem:
    record = evidence(
        EvidenceLevel.LOADED,
        "invocation-environment",
        f"{name} is present in the subprocess environment; its value is never recorded",
        reference=f"environment {name}",
    )
    return InventoryItem(
        item_id=f"claude/environment/{name}",
        target=ctx.target,
        kind=SourceKind.ENVIRONMENT,
        label=name,
        sources=(ItemSource(f"environment {name}", Scope.SESSION, record),),
        evidence=record,
        redaction=RedactionStatus.REDACTED,
    )
