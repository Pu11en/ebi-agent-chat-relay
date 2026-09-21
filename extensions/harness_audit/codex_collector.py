"""Codex CLI effective-input collector (task 2.3).

Resolves what a Discord-launched Codex session actually receives, without
running the CLI or asking a model:

* **Native config** — ``config.toml`` in ``CODEX_HOME`` (model, reasoning
  effort, sandbox, approval policy, MCP servers) and ``AGENTS.md`` there and
  in the project.
* **Invocation** — ``-c key=value`` overrides beat the file; ``--sandbox`` and
  ``-a`` are permissions; ``-c developer_instructions=…`` is the relay's bot
  addition and is a loaded input whose body is withheld.
* **Symlinks** — ``~/.codex/AGENTS.md → ~/AGENTS.md`` is one input with two
  sources.
* **Skill discovery** — Codex reads ``~/.codex/skills`` *and*
  ``~/.claude/skills`` (measured on 0.147.0; CLAUDE.md decision 13), so the
  Claude skill directory is part of the Codex inventory.
* **Rollout metadata** — ``session_meta`` in an existing rollout carries the
  cwd, the CLI version and the instructions the run loaded.  The collector
  hashes the instructions with the discovery salt and compares hashes; it
  never keeps the text.  A developer turn that names skills proves the skill
  index reached the model.

Model and reasoning-effort settings are inventoried so the report can say
which path it inspected; every such item is marked *excluded from cleanup*,
and nothing here writes to ``config.toml`` or anywhere else.
"""

from __future__ import annotations

import json
import re
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
    WITHHELD_PLACEHOLDER,
    PrivateBody,
    redact_environment,
    safe_hash,
    withhold_body,
)
from extensions.harness_audit.sources import SourceManifest

_ENV_PREFIXES = ("CODEX_", "OPENAI_", "CCDB_", "PATH")
_MAX_ROLLOUT_LINES = 400
_PRECEDENCE = {"cli": 1, "project": 2, "user": 3}

#: Keys whose values are safe to print: they name a path the audit inspected.
_SAFE_SETTING_KEYS = ("model", "model_reasoning_effort", "sandbox_mode", "approval_policy")
_EXCLUDED_FROM_CLEANUP = ("model", "model_reasoning_effort", "model_provider")
_BOT_ADDITION_KEYS = ("developer_instructions", "model_instructions")

_SKILL_NAME = re.compile(r"""<skill\b[^>]*\bname=['"](?P<name>[\w.\-]+)['"]""")
_SKILL_LINE = re.compile(r"(?m)^\s*(?:-\s*)?name:\s*(?P<name>[\w.\-]+)\s*$")


@dataclass(frozen=True, slots=True)
class CodexInvocation(Invocation):
    """The relay's Codex argv, with the overrides that decide what loads."""

    @property
    def overrides(self) -> Mapping[str, str]:
        result: dict[str, str] = {}
        for value in self.options("-c", "--config"):
            key, _, raw = value.partition("=")
            if key.strip():
                result[key.strip()] = raw.strip().strip("\"'")
        return result

    @property
    def sandbox(self) -> str | None:
        return self.option("--sandbox", "-s")

    @property
    def approval(self) -> str | None:
        return self.option("--ask-for-approval", "-a")

    @property
    def model(self) -> str | None:
        return self.option("--model", "-m")

    @property
    def profile(self) -> str | None:
        return self.option("--profile", "-p")


# --------------------------------------------------------------------------- #
# Rollout metadata
# --------------------------------------------------------------------------- #


def read_codex_rollouts(
    transcripts: Iterable[TranscriptFile], *, salt: str = ""
) -> tuple[SessionObservation, ...]:
    """Read ``session_meta`` and skill names from each rollout; keep no text."""
    observations: list[SessionObservation] = []
    for transcript in transcripts:
        if transcript.harness is not Harness.CODEX:
            continue
        cwd = version = instructions_hash = ""
        skills: set[str] = set()
        error = ""
        try:
            with transcript.absolute.open("r", encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if index >= _MAX_ROLLOUT_LINES:
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, Mapping):
                        continue
                    payload = record.get("payload")
                    if not isinstance(payload, Mapping):
                        continue
                    if record.get("type") == "session_meta":
                        cwd = cwd or str(payload.get("cwd", "") or "")
                        version = version or str(payload.get("cli_version", "") or "")
                        instructions = payload.get("instructions")
                        if isinstance(instructions, str) and instructions:
                            instructions_hash = safe_hash(instructions, salt=salt)
                    elif payload.get("role") in ("developer", "user", "system"):
                        skills.update(_skill_names(payload))
        except OSError as caught:
            error = f"{type(caught).__name__}: unreadable"
        if not (cwd or version) and not error:
            error = "no session_meta found"
        observations.append(
            SessionObservation(
                harness=Harness.CODEX,
                path=transcript.path,
                cwd=cwd,
                version=version,
                instructions_hash=instructions_hash,
                skill_names=tuple(sorted(skills)),
                parse_error=error,
            )
        )
    return tuple(observations)


def _skill_names(payload: Mapping[str, object]) -> set[str]:
    names: set[str] = set()
    content = payload.get("content")
    texts: list[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                texts.append(str(part["text"]))
    for text in texts:
        names.update(match.group("name") for match in _SKILL_NAME.finditer(text))
        if "<skill" not in text and "skills" in text.lower():
            names.update(match.group("name") for match in _SKILL_LINE.finditer(text))
    return names


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #


def collect_codex(
    discovery: DiscoveryResult,
    *,
    machine: Machine,
    invocation: CodexInvocation | None,
    manifest: SourceManifest,
    collected_at: datetime,
    salt: str = "",
) -> CollectionResult:
    """Build the Codex inventory for one machine from local evidence only."""
    target = AuditTarget(machine, Harness.CODEX)
    files = list(discovery.for_harness(Harness.CODEX))
    # Codex discovers ~/.claude/skills as well (decision 13); they are Codex inputs too.
    files.extend(
        entry
        for entry in discovery.files
        if entry.harness is Harness.CLAUDE
        and entry.kind is SourceKind.SKILL
        and entry.scope is Scope.GLOBAL
    )
    rollouts = read_codex_rollouts(discovery.transcripts, salt=salt)
    notes: list[str] = []

    proof = _rollout_proof(rollouts, invocation)
    version = _version(invocation, proof)
    guidance = manifest.require(Harness.CODEX, AuditCheck.LOAD_BEHAVIOR)
    covered: bool | None = None if not version else guidance.covers_version(version)
    if invocation is None:
        notes.append(
            "no invocation record: nothing can be proven loaded; evidence stops at configured"
        )
    elif proof is None:
        notes.append("no rollout for the invocation cwd; evidence stops at configured")
    if version and covered is False:
        notes.append(
            f"codex {version} is outside the pinned guidance range {guidance.version_range};"
            " load evidence is unknown until the manifest is refreshed"
        )

    ctx = _Context(
        target=target,
        invocation=invocation,
        proof=proof,
        version=version,
        covered=covered,
        salt=salt,
    )
    items: list[InventoryItem] = []
    signals: dict[str, ContentSignals] = {}
    settings: dict[str, SettingsFacts] = {}
    file_index: dict[str, DiscoveredFile] = {}
    private_bodies: list[PrivateBody] = []
    config_facts: dict[str, DiscoveredFile] = {}

    for entry in files:
        if entry.harness is None and entry.kind is SourceKind.GLOBAL_INSTRUCTIONS:
            continue  # ~/AGENTS.md reaches Codex through the ~/.codex/AGENTS.md link
        for item in _items_for(entry, ctx):
            items.append(item)
            signals[item.item_id] = entry.signals
            file_index[item.item_id] = entry
            if entry.settings is not None:
                settings[item.item_id] = entry.settings
        if entry.kind is SourceKind.HARNESS_SETTING and entry.settings is not None:
            config_facts[entry.path] = entry

    items.extend(_setting_items(config_facts, ctx, private_bodies))

    environment: Mapping[str, str] = {}
    if invocation is not None:
        relevant = {
            name: "" for name in invocation.environment_names if name.startswith(_ENV_PREFIXES)
        }
        environment = redact_environment(relevant).record
        items.extend(_environment_item(name, ctx) for name in environment)

    inventory = HarnessInventory(target=target, collected_at=collected_at, items=tuple(items))
    gap: CoverageGap | None = None
    if not files and invocation is None:
        gap = CoverageGap(
            target=target,
            reason="no Codex configuration was readable and no invocation record was given",
            missing_evidence=("codex home contents", "Discord invocation record"),
            observed_at=collected_at,
        )
    return CollectionResult(
        inventory=inventory,
        private_bodies=tuple(private_bodies),
        environment=environment,
        signals=signals,
        settings=settings,
        files=file_index,
        sessions=rollouts,
        coverage_gap=gap,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Context:
    target: AuditTarget
    invocation: CodexInvocation | None
    proof: SessionObservation | None
    version: str
    covered: bool | None
    salt: str = ""

    @property
    def can_prove_loaded(self) -> bool:
        return self.invocation is not None and self.proof is not None and self.covered is True


def _rollout_proof(
    rollouts: Iterable[SessionObservation], invocation: CodexInvocation | None
) -> SessionObservation | None:
    if invocation is None:
        return None
    for rollout in rollouts:
        if rollout.parse_error:
            continue
        if rollout.cwd and same_directory(rollout.cwd, invocation.cwd):
            return rollout
    return None


def _version(invocation: CodexInvocation | None, proof: SessionObservation | None) -> str:
    if invocation is not None and invocation.cli_version:
        return invocation.cli_version
    if proof is not None:
        return proof.version
    return ""


def _name(path: str) -> str:
    """A transcript's file name: its directory encodes an absolute path."""
    return path.rsplit("/", 1)[-1]


def _item_id(kind: SourceKind, reference: str) -> str:
    return f"codex/{kind.value}/{reference}"


def _start_evidence(entry: DiscoveredFile, ctx: _Context, what: str) -> EvidenceRecord:
    if entry.read_error:
        return unknown(
            "read-file", f"{what} could not be read", "readable file", reference=entry.path
        )
    if entry.settings is not None and entry.settings.parse_error:
        return unknown(
            "parse-config",
            f"{what} is not parseable ({entry.settings.parse_error})",
            "parseable config.toml",
            reference=entry.path,
        )
    if ctx.version and ctx.covered is False:
        return unknown(
            "rollout+invocation",
            f"codex {ctx.version} is outside the pinned guidance; load behavior of {what}"
            " cannot be asserted",
            f"official guidance covering codex {ctx.version}",
            reference=entry.path,
        )
    if ctx.can_prove_loaded and ctx.proof is not None:
        if (
            entry.kind is SourceKind.GLOBAL_INSTRUCTIONS
            and ctx.proof.instructions_hash
            and ctx.proof.instructions_hash == entry.content_hash
        ):
            return evidence(
                EvidenceLevel.LOADED,
                "rollout-instructions-hash-match",
                f"rollout {_name(ctx.proof.path)} records instructions whose salted hash equals"
                f" the hash of {entry.resolved_path}",
                reference=entry.path,
                vendor=True,
            )
        return evidence(
            EvidenceLevel.LOADED,
            "rollout+invocation",
            f"rollout {_name(ctx.proof.path)} shows codex {ctx.proof.version or ctx.version} ran in"
            f" the invocation cwd, where {what} is read",
            reference=entry.path,
            vendor=True,
        )
    return evidence(
        EvidenceLevel.CONFIGURED,
        "on-disk-in-read-location",
        f"{what} is in a location Codex reads at start; no rollout proves a session",
        reference=entry.path,
        vendor=True,
    )


def _items_for(entry: DiscoveredFile, ctx: _Context) -> list[InventoryItem]:
    kind = entry.kind
    if kind in (SourceKind.GLOBAL_INSTRUCTIONS, SourceKind.PROJECT_INSTRUCTIONS):
        return [_plain_item(entry, ctx, _start_evidence(entry, ctx, entry.path))]
    if kind is SourceKind.HARNESS_SETTING:
        return [_plain_item(entry, ctx, _start_evidence(entry, ctx, entry.path))] + (
            _connector_items(entry, ctx)
        )
    if kind is SourceKind.SKILL:
        return [_skill_item(entry, ctx)]
    if kind is SourceKind.COMMAND:
        record = evidence(
            EvidenceLevel.INSTALLED_ONLY,
            "vendor-documented-on-invoke",
            f"{entry.path} is a custom prompt read only when invoked; nothing reaches the"
            " session at start",
            reference=entry.path,
            vendor=True,
        )
        return [_plain_item(entry, ctx, record)]
    return [_plain_item(entry, ctx, _start_evidence(entry, ctx, entry.path))]


def _plain_item(
    entry: DiscoveredFile,
    ctx: _Context,
    record: EvidenceRecord,
    *,
    effective_behavior: str = "",
) -> InventoryItem:
    def source_evidence(method: str, detail: str) -> EvidenceRecord:
        if record.level is EvidenceLevel.UNKNOWN:
            return record
        return evidence(record.level, method, detail)

    sources = [
        ItemSource(
            reference=entry.path,
            scope=entry.scope,
            evidence=source_evidence("read-location", f"Codex looks for {entry.path}"),
            precedence=1,
        )
    ]
    behavior = effective_behavior
    if entry.is_symlink and entry.resolved_path != entry.path:
        sources.append(
            ItemSource(
                reference=entry.resolved_path,
                scope=entry.scope,
                evidence=source_evidence(
                    "resolved-symlink", f"{entry.path} is a link to {entry.resolved_path}"
                ),
                precedence=2,
            )
        )
        behavior = behavior or (
            f"Codex reads {entry.path}, which resolves to the canonical {entry.resolved_path};"
            " one copy of the content reaches the session"
        )
    return InventoryItem(
        item_id=_item_id(entry.kind, entry.path),
        target=ctx.target,
        kind=entry.kind,
        label=entry.path,
        sources=tuple(sources),
        evidence=record,
        size=entry.size,
        permissions=entry.permissions,
        content_hash=entry.content_hash,
        redaction=entry.redaction,
        effective_behavior=behavior,
    )


def _skill_item(entry: DiscoveredFile, ctx: _Context) -> InventoryItem:
    name = entry.signals.declared_name or entry.path.rsplit("/", 2)[-2]
    if ctx.can_prove_loaded and ctx.proof is not None and name in ctx.proof.skill_names:
        record = evidence(
            EvidenceLevel.LOADED,
            "rollout-skill-index",
            f"rollout {_name(ctx.proof.path)} names skill {name} in the developer turn",
            reference=entry.path,
            vendor=True,
        )
    elif ctx.version and ctx.covered is False:
        record = _start_evidence(entry, ctx, entry.path)
    elif ctx.proof is not None and ctx.can_prove_loaded:
        record = evidence(
            EvidenceLevel.CONFIGURED,
            "rollout-skill-index",
            f"rollout {_name(ctx.proof.path)} does not name skill {name}; the directory is"
            " one Codex scans, but this run did not show it",
            reference=entry.path,
            vendor=True,
        )
    else:
        record = evidence(
            EvidenceLevel.CONFIGURED,
            "on-disk-in-read-location",
            f"{entry.path} is in a skills directory Codex scans; no rollout proves a session",
            reference=entry.path,
            vendor=True,
        )
    return _plain_item(
        entry,
        ctx,
        record,
        effective_behavior=(
            f"skill index entry ({entry.signals.frontmatter_bytes} bytes of frontmatter) reaches"
            f" the session; the body ({entry.size.byte_size} bytes) loads on invocation"
        ),
    )


def _connector_items(entry: DiscoveredFile, ctx: _Context) -> list[InventoryItem]:
    facts = entry.settings
    if facts is None or facts.parse_error:
        return []
    items: list[InventoryItem] = []
    for server in facts.mcp_servers:
        reference = f"{entry.path}#{server}"
        if server in facts.disabled_mcp_servers:
            record = evidence(
                EvidenceLevel.INSTALLED_ONLY,
                "config-disabled-server",
                f"{server} has enabled = false in {entry.path}",
                reference=entry.path,
                vendor=True,
            )
        else:
            record = evidence(
                EvidenceLevel.CONFIGURED,
                "config-mcp-server",
                f"{server} is configured in {entry.path}; no rollout proves it connected"
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
                sources=(ItemSource(reference, entry.scope, record, _PRECEDENCE["user"]),),
                evidence=record,
                permissions=entry.permissions,
                redaction=entry.redaction,
            )
        )
    return items


def _setting_items(
    configs: Mapping[str, DiscoveredFile], ctx: _Context, private_bodies: list[PrivateBody]
) -> list[InventoryItem]:
    """One item per notable setting key, with every source that sets it."""
    sources: dict[str, list[tuple[ItemSource, str]]] = {}

    def add(key: str, source: ItemSource, shown: str) -> None:
        sources.setdefault(key, []).append((source, shown))

    invocation = ctx.invocation
    if invocation is not None:
        cli: dict[str, str | None] = dict(invocation.overrides)
        cli.setdefault("model", invocation.model)
        cli.setdefault("sandbox_mode", invocation.sandbox)
        cli.setdefault("approval_policy", invocation.approval)
        for key, value in cli.items():
            if value is None:
                continue
            if key in _BOT_ADDITION_KEYS:
                private_bodies.append(_bot_addition(key, value, ctx))
                continue
            shown = value if key in _SAFE_SETTING_KEYS else WITHHELD_PLACEHOLDER
            record = evidence(
                EvidenceLevel.LOADED,
                "invocation-argument",
                f"{key} is set on the command line for every launch",
                reference=f"invocation -c {key}",
                vendor=True,
            )
            add(
                key,
                ItemSource(f"invocation -c {key}", Scope.SESSION, record, _PRECEDENCE["cli"]),
                shown,
            )
    for path, entry in configs.items():
        facts = entry.settings
        if facts is None or facts.parse_error:
            continue
        base = _start_evidence(entry, ctx, path)
        for key, value in (
            ("model", facts.model),
            ("model_reasoning_effort", facts.reasoning_effort),
            ("sandbox_mode", facts.sandbox_mode),
            ("approval_policy", facts.approval_policy),
        ):
            if not value:
                continue
            level = base.level
            record = (
                base
                if level is EvidenceLevel.UNKNOWN
                else evidence(
                    level, "config-key", f"{key} is set in {path}", reference=path, vendor=True
                )
            )
            scope_name = "project" if entry.scope is Scope.PROJECT else "user"
            add(key, ItemSource(path, entry.scope, record, _PRECEDENCE[scope_name]), value)

    items: list[InventoryItem] = []
    for key in sorted(sources):
        entries = sorted(sources[key], key=lambda pair: pair[0].precedence or 0)
        winner_source, winner_value = entries[0]
        label = f"{key} = {winner_value}"
        if len(entries) > 1:
            label += " (" + "; ".join(f"{s.reference}: {v}" for s, v in entries[1:]) + ")"
        excluded = key in _EXCLUDED_FROM_CLEANUP
        behavior = (
            f"{winner_source.reference} wins (command line beats config.toml)"
            if len(entries) > 1
            else f"set only by {winner_source.reference}"
        )
        if excluded:
            behavior += "; model/reasoning selection is excluded from cleanup by the audit's scope"
        else:
            behavior += "; recorded to identify the inspected path"
        items.append(
            InventoryItem(
                item_id=f"codex/harness-setting/{key}",
                target=ctx.target,
                kind=SourceKind.HARNESS_SETTING,
                label=label,
                sources=tuple(source for source, _ in entries),
                evidence=winner_source.evidence,
                redaction=RedactionStatus.WITHHELD
                if any(v == WITHHELD_PLACEHOLDER for _, v in entries)
                else RedactionStatus.NONE_NEEDED,
                effective_behavior=behavior,
            )
        )
    items.extend(_bot_items(private_bodies, ctx))
    return items


def _bot_addition(key: str, text: str, ctx: _Context) -> PrivateBody:
    return withhold_body(text, field=f"codex/bot-addition/{key}", salt=ctx.salt)


def _bot_items(private_bodies: Iterable[PrivateBody], ctx: _Context) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for body in private_bodies:
        key = body.field.rsplit("/", 1)[-1]
        record = evidence(
            EvidenceLevel.LOADED,
            "invocation-argument",
            f"-c {key} is passed on every launch; the body is withheld and represented by"
            f" {body.content_hash[:19]}",
            reference=f"invocation -c {key}",
            vendor=True,
        )
        items.append(
            InventoryItem(
                item_id=body.field,
                target=ctx.target,
                kind=SourceKind.BOT_ADDITION,
                label=f"bot -c {key}",
                sources=(
                    ItemSource(f"invocation -c {key}", Scope.SESSION, record, _PRECEDENCE["cli"]),
                ),
                evidence=record,
                size=body.size,
                content_hash=body.content_hash,
                redaction=RedactionStatus.WITHHELD,
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
        item_id=f"codex/environment/{name}",
        target=ctx.target,
        kind=SourceKind.ENVIRONMENT,
        label=name,
        sources=(ItemSource(f"environment {name}", Scope.SESSION, record),),
        evidence=record,
        redaction=RedactionStatus.REDACTED,
    )


__all__ = [
    "CodexInvocation",
    "collect_codex",
    "read_codex_rollouts",
]
