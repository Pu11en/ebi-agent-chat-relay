"""Versioned evidence contracts for the professional harness audit.

Collection, rules, parity checks and reports all speak this vocabulary, so the
schema — not a later report — is where the audit's safety properties live:

* **Existing is not loading.**  Every record carries an :class:`EvidenceLevel`,
  and only ``loaded`` counts as effective context.  Built-in material may not
  even enter the inventory without ``loaded`` evidence, so a report can never
  quietly promote a file that merely exists.
* **Unknown stays unknown.**  An unreadable machine, an unfamiliar CLI layout
  or an unprovable load order is recorded as ``unknown`` together with the
  evidence that is missing.  It never decays into a pass, a failure, or a
  removal: :class:`ClassificationRecord` refuses ``Remove`` whenever the
  evidence is incomplete.
* **Shape is checked, content is not guessed.**  Deserialization rejects a
  payload with unknown or missing fields rather than ignoring the surprise, and
  a collector that must carry forward data from a newer CLI puts it in the
  declared :attr:`InventoryItem.unrecognized` bucket where it is visible.

Serialization is deterministic: ``to_dict`` emits a fixed key order, so
``to_json`` is byte-stable and two machines that observed the same thing
produce the same bytes.  This module is local operational tooling under
``extensions/``; it is deliberately not part of the reusable relay core, and it
never touches the filesystem, a network, or a model.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, Self

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

_HTTPS = "https://"


class SchemaError(ValueError):
    """A record is malformed, or a payload does not match this schema."""


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class Machine(StrEnum):
    """An approved computer in this audit."""

    DREWAI = "drewai"
    IMAC = "imac"


class Harness(StrEnum):
    """An approved Discord-launched CLI."""

    CLAUDE = "claude"
    CODEX = "codex"


class EvidenceLevel(StrEnum):
    """How strongly the evidence supports "this reaches the model"."""

    LOADED = "loaded"
    CONFIGURED = "configured"
    INSTALLED_ONLY = "installed-only"
    UNKNOWN = "unknown"

    @property
    def is_effective(self) -> bool:
        """True only for ``loaded``; nothing else counts toward context."""
        return self is EvidenceLevel.LOADED


class SourceKind(StrEnum):
    """What kind of input an inventory item is."""

    GLOBAL_INSTRUCTIONS = "global-instructions"
    PROJECT_INSTRUCTIONS = "project-instructions"
    MEMORY = "memory"
    SKILL = "skill"
    TOOL = "tool"
    PLUGIN = "plugin"
    CONNECTOR = "connector"
    COMMAND = "command"
    HOOK = "hook"
    BOT_ADDITION = "bot-addition"
    HARNESS_SETTING = "harness-setting"
    ENVIRONMENT = "environment"


class Scope(StrEnum):
    """Where an input applies."""

    GLOBAL = "global"
    PROJECT = "project"
    HARNESS = "harness"
    MACHINE = "machine"
    SESSION = "session"


class RedactionStatus(StrEnum):
    """What the redactor did to this item's evidence (rules land in task 1.2)."""

    NONE_NEEDED = "none-needed"
    REDACTED = "redacted"
    WITHHELD = "withheld"


class TokenCountKind(StrEnum):
    """Whether a token number was measured, estimated, or is absent."""

    UNKNOWN = "unknown"
    ESTIMATED = "estimated"
    MEASURED = "measured"


class AuditCheck(StrEnum):
    """The professional checks applied to every effective item."""

    SCOPE = "scope"
    DUPLICATION = "duplication"
    SIZE = "size"
    PERMISSIONS = "permissions"
    LOAD_BEHAVIOR = "load-behavior"
    PRECEDENCE = "precedence"
    DEAD_CONFIGURATION = "dead-configuration"
    PROJECT_CONTENT_IS_GLOBAL = "project-content-is-global"
    PARITY = "parity"


class CheckOutcome(StrEnum):
    """A check result; ``unknown`` is a real answer, not a soft failure."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    """Ranking input for the fixed rule precedence in task 3.2."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Classification(StrEnum):
    """The five approved verdicts; declaration order is precedence order."""

    KEEP = "keep"
    FIX = "fix"
    MOVE_TO_PROJECT = "move-to-project"
    LOAD_ON_DEMAND = "load-only-when-needed"
    REMOVE = "remove"

    @property
    def is_destructive(self) -> bool:
        return self is Classification.REMOVE


class ExceptionKind(StrEnum):
    """Why a deliberate machine or harness difference exists."""

    SUBSCRIPTION = "subscription"
    MODEL_AVAILABILITY = "model-availability"
    OPERATING_SYSTEM = "operating-system"
    INSTALLED_TOOL = "installed-tool"
    HARNESS_FORMAT = "harness-format"
    PERMISSIONS = "permissions"


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #


def _mapping(payload: object, where: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise SchemaError(f"{where}: expected an object, got {type(payload).__name__}")
    for key in payload:
        if not isinstance(key, str):
            raise SchemaError(f"{where}: object keys must be strings")
    return payload


def _reject_unknown(payload: Mapping[str, object], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise SchemaError(f"{where}: unknown field(s) {', '.join(unknown)}")


def _get(payload: Mapping[str, object], key: str, where: str) -> object:
    if key not in payload:
        raise SchemaError(f"{where}: missing required field {key!r}")
    return payload[key]


def _req_str(payload: Mapping[str, object], key: str, where: str) -> str:
    value = _get(payload, key, where)
    if not isinstance(value, str):
        raise SchemaError(f"{where}: field {key!r} must be a string")
    return value


def _opt_str(payload: Mapping[str, object], key: str, where: str, default: str = "") -> str:
    if key not in payload or payload[key] is None:
        return default
    value = payload[key]
    if not isinstance(value, str):
        raise SchemaError(f"{where}: field {key!r} must be a string")
    return value


def _req_int(payload: Mapping[str, object], key: str, where: str) -> int:
    value = _get(payload, key, where)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SchemaError(f"{where}: field {key!r} must be an integer")
    return value


def _opt_int(payload: Mapping[str, object], key: str, where: str) -> int | None:
    if key not in payload or payload[key] is None:
        return None
    return _req_int(payload, key, where)


def _opt_bool(payload: Mapping[str, object], key: str, where: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise SchemaError(f"{where}: field {key!r} must be true or false")
    return value


def _str_tuple(payload: Mapping[str, object], key: str, where: str) -> tuple[str, ...]:
    value = payload.get(key, ())
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise SchemaError(f"{where}: field {key!r} must be a list of strings")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise SchemaError(f"{where}: field {key!r} must be a list of strings")
        items.append(entry)
    return tuple(items)


def _list(payload: Mapping[str, object], key: str, where: str) -> list[object]:
    value = payload.get(key, ())
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise SchemaError(f"{where}: field {key!r} must be a list")
    return list(value)


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, key: str, where: str) -> EnumT:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise SchemaError(f"{where}: field {key!r} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        raise SchemaError(f"{where}: field {key!r} has unknown value {value!r}") from error


def _req_enum[EnumT: StrEnum](
    enum_type: type[EnumT], payload: Mapping[str, object], key: str, where: str
) -> EnumT:
    return _enum(enum_type, _get(payload, key, where), key, where)


def _aware(value: datetime, field_name: str, where: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise SchemaError(f"{where}: {field_name} must carry a time zone")
    return value


def _req_datetime(payload: Mapping[str, object], key: str, where: str) -> datetime:
    raw = _req_str(payload, key, where)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise SchemaError(f"{where}: field {key!r} is not an ISO timestamp") from error
    return _aware(parsed, key, where)


def _req_date(payload: Mapping[str, object], key: str, where: str) -> date:
    raw = _req_str(payload, key, where)
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise SchemaError(f"{where}: field {key!r} is not an ISO date") from error


def _text(value: str, field_name: str, where: str) -> str:
    if not value.strip():
        raise SchemaError(f"{where}: {field_name} must not be empty")
    return value


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AuditTarget:
    """One approved machine-and-harness pair, e.g. ``drewai/claude``."""

    machine: Machine
    harness: Harness

    def __post_init__(self) -> None:
        object.__setattr__(self, "machine", _enum(Machine, self.machine, "machine", "target"))
        object.__setattr__(self, "harness", _enum(Harness, self.harness, "harness", "target"))

    @property
    def key(self) -> str:
        return f"{self.machine.value}/{self.harness.value}"

    @classmethod
    def from_key(cls, key: str) -> AuditTarget:
        parts = key.split("/")
        if len(parts) != 2:
            raise SchemaError(f"target: {key!r} is not a 'machine/harness' key")
        machine, harness = parts
        return cls(
            _enum(Machine, machine, "machine", "target"),
            _enum(Harness, harness, "harness", "target"),
        )

    def to_dict(self) -> dict[str, object]:
        return {"machine": self.machine.value, "harness": self.harness.value}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "target"
        data = _mapping(payload, where)
        _reject_unknown(data, ("machine", "harness"), where)
        return cls(
            _req_enum(Machine, data, "machine", where),
            _req_enum(Harness, data, "harness", where),
        )


APPROVED_TARGETS: tuple[AuditTarget, ...] = tuple(
    AuditTarget(machine, harness) for machine in Machine for harness in Harness
)


def _targets_from(payload: Mapping[str, object], key: str, where: str) -> tuple[AuditTarget, ...]:
    return tuple(
        sorted(
            (AuditTarget.from_dict(_mapping(entry, where)) for entry in _list(payload, key, where)),
            key=lambda target: target.key,
        )
    )


@dataclass(frozen=True, slots=True)
class SizeMeasurement:
    """Authoritative bytes and characters, plus a clearly labeled token number.

    A token number is never presented on its own: it always names the method
    that produced it and says it is not provider billing data.
    """

    byte_size: int
    characters: int
    token_count: int | None = None
    token_count_kind: TokenCountKind = TokenCountKind.UNKNOWN
    token_method: str = ""

    def __post_init__(self) -> None:
        where = "size"
        object.__setattr__(
            self,
            "token_count_kind",
            _enum(TokenCountKind, self.token_count_kind, "token_count_kind", where),
        )
        if self.byte_size < 0 or self.characters < 0:
            raise SchemaError(f"{where}: byte_size and characters must not be negative")
        if self.token_count_kind is TokenCountKind.UNKNOWN:
            if self.token_count is not None or self.token_method.strip():
                raise SchemaError(
                    f"{where}: an unknown token_count_kind cannot carry a count or method"
                )
        else:
            if self.token_count is None:
                raise SchemaError(f"{where}: a {self.token_count_kind} token count is missing")
            if self.token_count < 0:
                raise SchemaError(f"{where}: token_count must not be negative")
            _text(self.token_method, "token_method", where)

    @property
    def label(self) -> str:
        """A phrase safe to print in a report."""
        exact = f"{self.byte_size} bytes / {self.characters} characters"
        if self.token_count_kind is TokenCountKind.UNKNOWN:
            return f"{exact}; no token count"
        return (
            f"{exact}; {self.token_count} tokens {self.token_count_kind.value}"
            f" via {self.token_method} (not provider billing data)"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "byte_size": self.byte_size,
            "characters": self.characters,
            "token_count": self.token_count,
            "token_count_kind": self.token_count_kind.value,
            "token_method": self.token_method,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "size"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            ("byte_size", "characters", "token_count", "token_count_kind", "token_method"),
            where,
        )
        return cls(
            byte_size=_req_int(data, "byte_size", where),
            characters=_req_int(data, "characters", where),
            token_count=_opt_int(data, "token_count", where),
            token_count_kind=_enum(
                TokenCountKind,
                data.get("token_count_kind", TokenCountKind.UNKNOWN.value),
                "token_count_kind",
                where,
            ),
            token_method=_opt_str(data, "token_method", where),
        )


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One deterministic observation and how strongly it proves loading."""

    level: EvidenceLevel
    method: str
    detail: str
    source_reference: str = ""
    missing_evidence: tuple[str, ...] = ()
    depends_on_vendor_behavior: bool = False

    def __post_init__(self) -> None:
        where = "evidence"
        object.__setattr__(self, "level", _enum(EvidenceLevel, self.level, "level", where))
        object.__setattr__(self, "missing_evidence", tuple(self.missing_evidence))
        _text(self.method, "method", where)
        _text(self.detail, "detail", where)
        if self.level is EvidenceLevel.UNKNOWN and not self.missing_evidence:
            raise SchemaError(f"{where}: unknown evidence must name its missing_evidence")
        if self.level is not EvidenceLevel.UNKNOWN and self.missing_evidence:
            raise SchemaError(f"{where}: only unknown evidence may list missing_evidence")

    @property
    def is_effective(self) -> bool:
        return self.level.is_effective

    def to_dict(self) -> dict[str, object]:
        return {
            "level": self.level.value,
            "method": self.method,
            "detail": self.detail,
            "source_reference": self.source_reference,
            "missing_evidence": list(self.missing_evidence),
            "depends_on_vendor_behavior": self.depends_on_vendor_behavior,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "evidence"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            (
                "level",
                "method",
                "detail",
                "source_reference",
                "missing_evidence",
                "depends_on_vendor_behavior",
            ),
            where,
        )
        return cls(
            level=_req_enum(EvidenceLevel, data, "level", where),
            method=_req_str(data, "method", where),
            detail=_req_str(data, "detail", where),
            source_reference=_opt_str(data, "source_reference", where),
            missing_evidence=_str_tuple(data, "missing_evidence", where),
            depends_on_vendor_behavior=_opt_bool(data, "depends_on_vendor_behavior", where),
        )


@dataclass(frozen=True, slots=True)
class VendorCitation:
    """Pinned official guidance: a URL is not enough without a date and a pin."""

    source_id: str
    url: str
    retrieved_on: date
    applies_to_versions: str = ""
    content_hash: str = ""
    quoted_proposition: str = ""

    def __post_init__(self) -> None:
        where = "vendor source"
        _text(self.source_id, "source_id", where)
        _text(self.url, "url", where)
        if not self.url.startswith(_HTTPS):
            raise SchemaError(f"{where}: an official source url must start with {_HTTPS}")
        if not (self.content_hash.strip() or self.quoted_proposition.strip()):
            raise SchemaError(
                f"{where}: a citation needs a content_hash or a quoted_proposition to stay pinned"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "retrieved_on": self.retrieved_on.isoformat(),
            "applies_to_versions": self.applies_to_versions,
            "content_hash": self.content_hash,
            "quoted_proposition": self.quoted_proposition,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "vendor source"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            (
                "source_id",
                "url",
                "retrieved_on",
                "applies_to_versions",
                "content_hash",
                "quoted_proposition",
            ),
            where,
        )
        return cls(
            source_id=_req_str(data, "source_id", where),
            url=_req_str(data, "url", where),
            retrieved_on=_req_date(data, "retrieved_on", where),
            applies_to_versions=_opt_str(data, "applies_to_versions", where),
            content_hash=_opt_str(data, "content_hash", where),
            quoted_proposition=_opt_str(data, "quoted_proposition", where),
        )


@dataclass(frozen=True, slots=True)
class ItemSource:
    """One place an item reaches a target from, with its scope and precedence."""

    reference: str
    scope: Scope
    evidence: EvidenceRecord
    precedence: int | None = None

    def __post_init__(self) -> None:
        where = "item source"
        object.__setattr__(self, "scope", _enum(Scope, self.scope, "scope", where))
        _text(self.reference, "reference", where)

    def to_dict(self) -> dict[str, object]:
        return {
            "reference": self.reference,
            "scope": self.scope.value,
            "evidence": self.evidence.to_dict(),
            "precedence": self.precedence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "item source"
        data = _mapping(payload, where)
        _reject_unknown(data, ("reference", "scope", "evidence", "precedence"), where)
        return cls(
            reference=_req_str(data, "reference", where),
            scope=_req_enum(Scope, data, "scope", where),
            evidence=EvidenceRecord.from_dict(_mapping(_get(data, "evidence", where), where)),
            precedence=_opt_int(data, "precedence", where),
        )


@dataclass(frozen=True, slots=True)
class InventoryItem:
    """One audited input on one target, with every source that supplies it."""

    item_id: str
    target: AuditTarget
    kind: SourceKind
    label: str
    sources: tuple[ItemSource, ...]
    evidence: EvidenceRecord
    size: SizeMeasurement | None = None
    permissions: str = ""
    content_hash: str = ""
    redaction: RedactionStatus = RedactionStatus.NONE_NEEDED
    is_builtin: bool = False
    effective_behavior: str = ""
    unrecognized: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        where = "inventory item"
        object.__setattr__(self, "kind", _enum(SourceKind, self.kind, "kind", where))
        object.__setattr__(
            self, "redaction", _enum(RedactionStatus, self.redaction, "redaction", where)
        )
        object.__setattr__(self, "sources", tuple(self.sources))
        unrecognized = dict(self.unrecognized)
        for key, value in unrecognized.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise SchemaError(f"{where}: unrecognized entries must be strings")
        object.__setattr__(self, "unrecognized", MappingProxyType(unrecognized))
        _text(self.item_id, "item_id", where)
        _text(self.label, "label", where)
        if not self.sources:
            raise SchemaError(f"{where}: {self.item_id!r} needs at least one source")
        if len(self.sources) > 1 and not self.effective_behavior.strip():
            raise SchemaError(
                f"{where}: {self.item_id!r} has several sources and must state"
                " the effective_behavior on the target"
            )
        if self.is_builtin and not self.evidence.level.is_effective:
            raise SchemaError(
                f"{where}: built-in {self.item_id!r} may only be inventoried with loaded evidence"
            )

    @property
    def is_effective(self) -> bool:
        """True only when the evidence proves the item reaches the model."""
        return self.evidence.level.is_effective

    @property
    def effective_source(self) -> ItemSource:
        """The winning source: lowest known precedence, else the first listed."""
        ranked = [source for source in self.sources if source.precedence is not None]
        if ranked:
            return min(ranked, key=lambda source: source.precedence or 0)
        return self.sources[0]

    @property
    def scope(self) -> Scope:
        return self.effective_source.scope

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "target": self.target.to_dict(),
            "kind": self.kind.value,
            "label": self.label,
            "sources": [source.to_dict() for source in self.sources],
            "evidence": self.evidence.to_dict(),
            "size": None if self.size is None else self.size.to_dict(),
            "permissions": self.permissions,
            "content_hash": self.content_hash,
            "redaction": self.redaction.value,
            "is_builtin": self.is_builtin,
            "effective_behavior": self.effective_behavior,
            "unrecognized": dict(sorted(self.unrecognized.items())),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "inventory item"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            (
                "item_id",
                "target",
                "kind",
                "label",
                "sources",
                "evidence",
                "size",
                "permissions",
                "content_hash",
                "redaction",
                "is_builtin",
                "effective_behavior",
                "unrecognized",
            ),
            where,
        )
        raw_size = data.get("size")
        raw_unrecognized = data.get("unrecognized") or {}
        unrecognized: dict[str, str] = {}
        for key, value in _mapping(raw_unrecognized, where).items():
            if not isinstance(value, str):
                raise SchemaError(f"{where}: unrecognized entries must be strings")
            unrecognized[key] = value
        return cls(
            item_id=_req_str(data, "item_id", where),
            target=AuditTarget.from_dict(_mapping(_get(data, "target", where), where)),
            kind=_req_enum(SourceKind, data, "kind", where),
            label=_req_str(data, "label", where),
            sources=tuple(
                ItemSource.from_dict(_mapping(entry, where))
                for entry in _list(data, "sources", where)
            ),
            evidence=EvidenceRecord.from_dict(_mapping(_get(data, "evidence", where), where)),
            size=None if raw_size is None else SizeMeasurement.from_dict(_mapping(raw_size, where)),
            permissions=_opt_str(data, "permissions", where),
            content_hash=_opt_str(data, "content_hash", where),
            redaction=_enum(
                RedactionStatus,
                data.get("redaction", RedactionStatus.NONE_NEEDED.value),
                "redaction",
                where,
            ),
            is_builtin=_opt_bool(data, "is_builtin", where),
            effective_behavior=_opt_str(data, "effective_behavior", where),
            unrecognized=unrecognized,
        )


@dataclass(frozen=True, slots=True)
class HarnessInventory:
    """Every inventoried item for one target, at one collection time."""

    target: AuditTarget
    collected_at: datetime
    items: tuple[InventoryItem, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        where = "inventory"
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise SchemaError(
                f"{where}: schema_version {self.schema_version} is not supported"
                f" (supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )
        _aware(self.collected_at, "collected_at", where)
        ordered = tuple(sorted(self.items, key=lambda item: item.item_id))
        seen: set[str] = set()
        for item in ordered:
            if item.item_id in seen:
                raise SchemaError(f"{where}: duplicate item_id {item.item_id!r}")
            seen.add(item.item_id)
            if item.target != self.target:
                raise SchemaError(
                    f"{where}: item {item.item_id!r} belongs to target"
                    f" {item.target.key!r}, not {self.target.key!r}"
                )
        object.__setattr__(self, "items", ordered)

    @property
    def effective_items(self) -> tuple[InventoryItem, ...]:
        """Only the items proven to load; installed-only never counts."""
        return tuple(item for item in self.items if item.is_effective)

    def get(self, item_id: str) -> InventoryItem | None:
        for item in self.items:
            if item.item_id == item_id:
                return item
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "target": self.target.to_dict(),
            "collected_at": self.collected_at.isoformat(),
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "inventory"
        data = _mapping(payload, where)
        _reject_unknown(data, ("schema_version", "target", "collected_at", "items"), where)
        return cls(
            target=AuditTarget.from_dict(_mapping(_get(data, "target", where), where)),
            collected_at=_req_datetime(data, "collected_at", where),
            items=tuple(
                InventoryItem.from_dict(_mapping(entry, where))
                for entry in _list(data, "items", where)
            ),
            schema_version=_req_int(data, "schema_version", where),
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """One deterministic check result, with the evidence that produced it."""

    finding_id: str
    check: AuditCheck
    outcome: CheckOutcome
    severity: Severity
    summary: str
    item_ids: tuple[str, ...]
    targets: tuple[AuditTarget, ...]
    evidence: tuple[EvidenceRecord, ...]
    vendor_sources: tuple[VendorCitation, ...] = ()
    comparison_method: str = ""
    duplicated_bytes: int | None = None
    missing_evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        where = "finding"
        object.__setattr__(self, "check", _enum(AuditCheck, self.check, "check", where))
        object.__setattr__(self, "outcome", _enum(CheckOutcome, self.outcome, "outcome", where))
        object.__setattr__(self, "severity", _enum(Severity, self.severity, "severity", where))
        object.__setattr__(self, "item_ids", tuple(sorted(set(self.item_ids))))
        object.__setattr__(
            self, "targets", tuple(sorted(set(self.targets), key=lambda target: target.key))
        )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "vendor_sources", tuple(self.vendor_sources))
        object.__setattr__(self, "missing_evidence", tuple(self.missing_evidence))
        _text(self.finding_id, "finding_id", where)
        _text(self.summary, "summary", where)
        if not self.evidence:
            raise SchemaError(f"{where}: {self.finding_id!r} needs at least one evidence record")
        if not self.targets:
            raise SchemaError(f"{where}: {self.finding_id!r} must name its targets")
        if any(record.depends_on_vendor_behavior for record in self.evidence) and (
            not self.vendor_sources
        ):
            raise SchemaError(
                f"{where}: {self.finding_id!r} depends on vendor behavior and must cite"
                " an official vendor source"
            )
        if self.check is AuditCheck.DUPLICATION:
            if not self.comparison_method.strip():
                raise SchemaError(
                    f"{where}: a duplication finding must state its comparison_method"
                )
            if len(self.item_ids) < 2:
                raise SchemaError(f"{where}: a duplication finding must name two items")
        if self.outcome is CheckOutcome.UNKNOWN and not self.missing_evidence:
            raise SchemaError(f"{where}: an unknown outcome must list its missing_evidence")
        if self.outcome is not CheckOutcome.UNKNOWN and self.missing_evidence:
            raise SchemaError(f"{where}: only an unknown outcome may list missing_evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "check": self.check.value,
            "outcome": self.outcome.value,
            "severity": self.severity.value,
            "summary": self.summary,
            "item_ids": list(self.item_ids),
            "targets": [target.to_dict() for target in self.targets],
            "evidence": [record.to_dict() for record in self.evidence],
            "vendor_sources": [citation.to_dict() for citation in self.vendor_sources],
            "comparison_method": self.comparison_method,
            "duplicated_bytes": self.duplicated_bytes,
            "missing_evidence": list(self.missing_evidence),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "finding"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            (
                "finding_id",
                "check",
                "outcome",
                "severity",
                "summary",
                "item_ids",
                "targets",
                "evidence",
                "vendor_sources",
                "comparison_method",
                "duplicated_bytes",
                "missing_evidence",
            ),
            where,
        )
        return cls(
            finding_id=_req_str(data, "finding_id", where),
            check=_req_enum(AuditCheck, data, "check", where),
            outcome=_req_enum(CheckOutcome, data, "outcome", where),
            severity=_req_enum(Severity, data, "severity", where),
            summary=_req_str(data, "summary", where),
            item_ids=_str_tuple(data, "item_ids", where),
            targets=_targets_from(data, "targets", where),
            evidence=tuple(
                EvidenceRecord.from_dict(_mapping(entry, where))
                for entry in _list(data, "evidence", where)
            ),
            vendor_sources=tuple(
                VendorCitation.from_dict(_mapping(entry, where))
                for entry in _list(data, "vendor_sources", where)
            ),
            comparison_method=_opt_str(data, "comparison_method", where),
            duplicated_bytes=_opt_int(data, "duplicated_bytes", where),
            missing_evidence=_str_tuple(data, "missing_evidence", where),
        )


@dataclass(frozen=True, slots=True)
class ClassificationRecord:
    """Exactly one verdict for one item, with a reversible next action.

    Incomplete evidence can never produce ``Remove``: an item nobody can prove
    is unused stays ``Keep`` or ``Fix`` with the uncertainty written down.
    """

    item_id: str
    classification: Classification
    reason: str
    evidence: tuple[EvidenceRecord, ...]
    affected_targets: tuple[AuditTarget, ...]
    proposed_scope: Scope
    risk: str
    reversible_action: str
    finding_ids: tuple[str, ...] = ()
    activation_boundary: str = ""
    uncertainty: str = ""

    def __post_init__(self) -> None:
        where = "classification"
        object.__setattr__(
            self,
            "classification",
            _enum(Classification, self.classification, "classification", where),
        )
        object.__setattr__(
            self, "proposed_scope", _enum(Scope, self.proposed_scope, "proposed_scope", where)
        )
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(
            self,
            "affected_targets",
            tuple(sorted(set(self.affected_targets), key=lambda target: target.key)),
        )
        object.__setattr__(self, "finding_ids", tuple(sorted(set(self.finding_ids))))
        _text(self.item_id, "item_id", where)
        _text(self.reason, "reason", where)
        _text(self.risk, "risk", where)
        if not self.reversible_action.strip():
            raise SchemaError(f"{where}: {self.item_id!r} must state a reversible_action")
        if not self.evidence:
            raise SchemaError(f"{where}: {self.item_id!r} needs at least one evidence record")
        if not self.affected_targets:
            raise SchemaError(f"{where}: {self.item_id!r} must list its affected_targets")
        incomplete = any(record.level is EvidenceLevel.UNKNOWN for record in self.evidence)
        if incomplete and not self.uncertainty.strip():
            raise SchemaError(
                f"{where}: {self.item_id!r} has unknown evidence and must record its uncertainty"
            )
        if self.classification is Classification.REMOVE and (
            incomplete or self.uncertainty.strip()
        ):
            raise SchemaError(
                f"{where}: {self.item_id!r} cannot be classified Remove while the evidence"
                " is incomplete; use Keep or Fix"
            )
        if self.classification is Classification.LOAD_ON_DEMAND and (
            not self.activation_boundary.strip()
        ):
            raise SchemaError(
                f"{where}: {self.item_id!r} is load-only-when-needed and must state a concrete"
                " activation_boundary"
            )
        if self.classification is Classification.MOVE_TO_PROJECT and (
            self.proposed_scope is not Scope.PROJECT
        ):
            raise SchemaError(
                f"{where}: {self.item_id!r} moves to a project and must propose project scope"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "classification": self.classification.value,
            "reason": self.reason,
            "evidence": [record.to_dict() for record in self.evidence],
            "affected_targets": [target.to_dict() for target in self.affected_targets],
            "proposed_scope": self.proposed_scope.value,
            "risk": self.risk,
            "reversible_action": self.reversible_action,
            "finding_ids": list(self.finding_ids),
            "activation_boundary": self.activation_boundary,
            "uncertainty": self.uncertainty,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "classification"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            (
                "item_id",
                "classification",
                "reason",
                "evidence",
                "affected_targets",
                "proposed_scope",
                "risk",
                "reversible_action",
                "finding_ids",
                "activation_boundary",
                "uncertainty",
            ),
            where,
        )
        return cls(
            item_id=_req_str(data, "item_id", where),
            classification=_req_enum(Classification, data, "classification", where),
            reason=_req_str(data, "reason", where),
            evidence=tuple(
                EvidenceRecord.from_dict(_mapping(entry, where))
                for entry in _list(data, "evidence", where)
            ),
            affected_targets=_targets_from(data, "affected_targets", where),
            proposed_scope=_req_enum(Scope, data, "proposed_scope", where),
            risk=_req_str(data, "risk", where),
            reversible_action=_req_str(data, "reversible_action", where),
            finding_ids=_str_tuple(data, "finding_ids", where),
            activation_boundary=_opt_str(data, "activation_boundary", where),
            uncertainty=_opt_str(data, "uncertainty", where),
        )


@dataclass(frozen=True, slots=True)
class MachineException:
    """A deliberate machine or harness difference, named rather than "fixed".

    An unsupported model or a native config format is an exception, never a
    parity failure — the audit must not recommend fabricating what a machine
    cannot have.
    """

    exception_id: str
    machine: Machine
    kind: ExceptionKind
    description: str
    preserved_outcome: str
    evidence: EvidenceRecord
    harness: Harness | None = None

    def __post_init__(self) -> None:
        where = "machine exception"
        object.__setattr__(self, "machine", _enum(Machine, self.machine, "machine", where))
        object.__setattr__(self, "kind", _enum(ExceptionKind, self.kind, "kind", where))
        if self.harness is not None:
            object.__setattr__(self, "harness", _enum(Harness, self.harness, "harness", where))
        _text(self.exception_id, "exception_id", where)
        _text(self.description, "description", where)
        if not self.preserved_outcome.strip():
            raise SchemaError(
                f"{where}: {self.exception_id!r} must state the preserved_outcome that stays"
                " the same for the user"
            )

    @property
    def is_parity_failure(self) -> bool:
        """Always False: a named exception is the opposite of a parity failure."""
        return False

    @property
    def targets(self) -> tuple[AuditTarget, ...]:
        harnesses = (self.harness,) if self.harness is not None else tuple(Harness)
        return tuple(AuditTarget(self.machine, harness) for harness in harnesses)

    def to_dict(self) -> dict[str, object]:
        return {
            "exception_id": self.exception_id,
            "machine": self.machine.value,
            "kind": self.kind.value,
            "description": self.description,
            "preserved_outcome": self.preserved_outcome,
            "evidence": self.evidence.to_dict(),
            "harness": None if self.harness is None else self.harness.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "machine exception"
        data = _mapping(payload, where)
        _reject_unknown(
            data,
            (
                "exception_id",
                "machine",
                "kind",
                "description",
                "preserved_outcome",
                "evidence",
                "harness",
            ),
            where,
        )
        raw_harness = data.get("harness")
        return cls(
            exception_id=_req_str(data, "exception_id", where),
            machine=_req_enum(Machine, data, "machine", where),
            kind=_req_enum(ExceptionKind, data, "kind", where),
            description=_req_str(data, "description", where),
            preserved_outcome=_req_str(data, "preserved_outcome", where),
            evidence=EvidenceRecord.from_dict(_mapping(_get(data, "evidence", where), where)),
            harness=None if raw_harness is None else _enum(Harness, raw_harness, "harness", where),
        )


@dataclass(frozen=True, slots=True)
class CoverageGap:
    """A target the audit could not inspect, named instead of omitted."""

    target: AuditTarget
    reason: str
    missing_evidence: tuple[str, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        where = "coverage gap"
        object.__setattr__(self, "missing_evidence", tuple(self.missing_evidence))
        _text(self.reason, "reason", where)
        _aware(self.observed_at, "observed_at", where)
        if not self.missing_evidence:
            raise SchemaError(
                f"{where}: {self.target.key} must name the missing_evidence it could not collect"
            )

    @property
    def claims_full_coverage(self) -> bool:
        """Always False: a gap is the audit's statement that coverage is partial."""
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target.to_dict(),
            "reason": self.reason,
            "missing_evidence": list(self.missing_evidence),
            "observed_at": self.observed_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "coverage gap"
        data = _mapping(payload, where)
        _reject_unknown(data, ("target", "reason", "missing_evidence", "observed_at"), where)
        return cls(
            target=AuditTarget.from_dict(_mapping(_get(data, "target", where), where)),
            reason=_req_str(data, "reason", where),
            missing_evidence=_str_tuple(data, "missing_evidence", where),
            observed_at=_req_datetime(data, "observed_at", where),
        )


# --------------------------------------------------------------------------- #
# Deterministic JSON
# --------------------------------------------------------------------------- #


class SchemaRecord(Protocol):
    """Anything in this module: serializable both ways with a fixed key order."""

    def to_dict(self) -> dict[str, object]: ...

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self: ...


def to_json(record: SchemaRecord) -> str:
    """Serialize deterministically: same record in, same bytes out."""
    return json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))


def from_json[RecordT: SchemaRecord](record_type: type[RecordT], text: str) -> RecordT:
    """Parse a record, rejecting anything this schema does not recognize."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise SchemaError(f"{record_type.__name__}: payload is not valid JSON") from error
    return record_type.from_dict(_mapping(payload, record_type.__name__))
