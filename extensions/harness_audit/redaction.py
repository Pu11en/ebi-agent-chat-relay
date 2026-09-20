"""Fail-closed redaction for the professional harness audit (task 1.2).

The audit reads personal configuration on DrewAI and the iMac and then hands
one machine's evidence to the other, so this module is the boundary those
bytes have to cross.  It answers a single question — *may this leave the
machine?* — and it answers conservatively:

* **Secret values never cross.**  Credentials, cookies, authorization headers,
  private keys, URL credentials and every environment value are replaced
  before a record is built, not filtered out of a report afterwards.
* **Private bodies never cross at all.**  A prompt or file body is represented
  by :class:`PrivateBody`: its size and a salted hash, never its content.
* **Safe metadata stays usable.**  Identifiers, kinds, scopes, precedence,
  permissions, exact sizes and hashes survive untouched, because a redactor
  that deleted everything would be safe and worthless.
* **Serialization fails closed.**  :func:`serialize_bundle` re-scans its own
  output and raises rather than emitting a payload that still matches a secret
  rule, so a record assembled without redaction cannot slip through.

Every rule is idempotent: redacted text scans clean, so the fail-closed check
can never fire on the redactor's own placeholder.  Like
:mod:`extensions.harness_audit.models`, this module touches no filesystem, no
network and no model, and serialization is deterministic.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType
from typing import Self

from extensions.harness_audit.models import (
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ClassificationRecord,
    CoverageGap,
    EvidenceRecord,
    Finding,
    HarnessInventory,
    InventoryItem,
    ItemSource,
    Machine,
    MachineException,
    RedactionStatus,
    SchemaError,
    SizeMeasurement,
    TokenCountKind,
    VendorCitation,
)

SECRET_PLACEHOLDER = "[redacted]"
"""What a removed secret value is replaced with."""

WITHHELD_PLACEHOLDER = "[withheld]"
"""What a value is replaced with when even its shape is not trusted."""

HASH_ALGORITHM = "sha256"
TOKEN_ESTIMATE_METHOD = "characters/4 heuristic"


class RedactionError(SchemaError):
    """Something unsafe reached the boundary, so nothing is emitted."""


# --------------------------------------------------------------------------- #
# Detection rules
# --------------------------------------------------------------------------- #

# Key words that mark an assignment's value as a secret.  ``token`` is here
# because credentials are usually called one; the audit's own token *counts*
# are protected by _SAFE_KEYS below rather than by weakening the rule.
_SECRET_KEY_WORDS = (
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "secret",
    "credential",
    "cookie",
    "api[_-]?key",
    "apikey",
    "access[_-]?key",
    "private[_-]?key",
    "signing[_-]?key",
    "client[_-]?secret",
    "authorization",
    "session[_-]?(?:id|key|token|cookie|secret)",
    "token",
)

# Audit fields that legitimately contain one of those words.  A measured token
# count is evidence, not a credential.
_SAFE_KEYS = frozenset(
    {
        "token_count",
        "token_count_kind",
        "token_method",
        "tokens",
        "token_estimate",
        "token_impact",
        "duplicated_tokens",
    }
)

# Values that are already safe: placeholders, scalars and the schema's enums.
# Checking these is what makes every rule idempotent.
_SAFE_VALUES = frozenset(
    {
        "",
        "redacted",
        "withheld",
        "none-needed",
        "none",
        "null",
        "nil",
        "true",
        "false",
        "unknown",
        "estimated",
        "measured",
        "n/a",
    }
)

_KEY_WORDS = "|".join(_SECRET_KEY_WORDS)
_KEY = rf"[A-Za-z0-9_.\-]*(?:{_KEY_WORDS})[A-Za-z0-9_.\-]*"
# A value ends at whitespace, a quote, a separator or a closing bracket, so a
# redacted placeholder is recaptured whole and recognized as already safe.
_VALUE = r"[^\s\"',;)\]}>]+"
_STRIP = " \t\"'`[](){}<>,;:="


@dataclass(frozen=True, slots=True)
class _Rule:
    """One detector: a pattern plus which group holds the secret."""

    name: str
    pattern: re.Pattern[str]

    def apply(self, text: str) -> tuple[str, int]:
        """Replace every unsafe ``value`` group; return the text and the hits."""
        pieces: list[str] = []
        cursor = 0
        hits = 0
        for match in self.pattern.finditer(text):
            key = match.groupdict().get("key")
            if key is not None and key.lower() in _SAFE_KEYS:
                continue
            if _is_safe_value(match.group("value")):
                continue
            start, end = match.span("value")
            pieces.append(text[cursor:start])
            pieces.append(SECRET_PLACEHOLDER)
            cursor = end
            hits += 1
        if not hits:
            return text, 0
        pieces.append(text[cursor:])
        return "".join(pieces), hits


def _is_safe_value(value: str) -> bool:
    normalized = value.strip().strip(_STRIP).lower()
    if normalized in _SAFE_VALUES:
        return True
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", normalized))


# Order matters: the widest rules run first so a narrower one cannot leave a
# tail behind (an ``Authorization`` header is consumed whole, not down to the
# first space).
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "private-key-block",
        re.compile(
            r"(?P<value>-----BEGIN[ A-Z]*PRIVATE KEY-----"
            r"(?:[\s\S]*?-----END[ A-Z]*PRIVATE KEY-----|[\s\S]*))"
        ),
    ),
    _Rule(
        "credential-header",
        re.compile(
            r"(?i)\b(?:authorization|proxy-authorization|(?:set-)?cookie)"
            r"[\"']?\s*[:=]\s*[\"']?(?P<value>[^\n\"]+)"
        ),
    ),
    _Rule(
        "named-secret-assignment",
        re.compile(rf"(?i)\b(?P<key>{_KEY})[\"']?\s*[:=]\s*[\"']?(?P<value>{_VALUE})"),
    ),
    _Rule("bearer-value", re.compile(rf"(?i)\bbearer\s+(?P<value>{_VALUE})")),
    _Rule("url-credentials", re.compile(r"://(?P<value>[^/\s\"'@]+:[^/\s\"'@]+)@")),
    _Rule(
        "credential-shape",
        re.compile(
            r"(?P<value>"
            r"sk-[A-Za-z0-9_\-]{16,}"
            r"|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|github_pat_[A-Za-z0-9_]{20,}"
            r"|xox[abposr]-[A-Za-z0-9\-]{10,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|AIza[0-9A-Za-z_\-]{30,}"
            r"|npm_[A-Za-z0-9]{30,}"
            r"|hf_[A-Za-z0-9]{30,}"
            r"|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{5,}"
            r")"
        ),
    ),
)

_HASH_SHAPE = re.compile(r"[a-z0-9]+:[0-9a-f]{32,128}|[0-9a-f]{32,128}")


# --------------------------------------------------------------------------- #
# Notes and results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RedactionNote:
    """One thing the boundary removed, described without the value itself."""

    field: str
    rule: str
    status: RedactionStatus
    occurrences: int = 1

    def __post_init__(self) -> None:
        where = "redaction note"
        object.__setattr__(
            self, "status", _enum_value(RedactionStatus, self.status, "status", where)
        )
        if not self.field.strip():
            raise RedactionError(f"{where}: field must not be empty")
        if not self.rule.strip():
            raise RedactionError(f"{where}: rule must not be empty")
        if self.status is RedactionStatus.NONE_NEEDED:
            raise RedactionError(f"{where}: a note records something that was removed")
        if self.occurrences < 1:
            raise RedactionError(f"{where}: occurrences must be at least 1")

    @property
    def sort_key(self) -> tuple[str, str, str]:
        return (self.field, self.rule, self.status.value)

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "rule": self.rule,
            "status": self.status.value,
            "occurrences": self.occurrences,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "redaction note"
        data = _payload(payload, where)
        _reject_unknown(data, ("field", "rule", "status", "occurrences"), where)
        return cls(
            field=_req_str(data, "field", where),
            rule=_req_str(data, "rule", where),
            status=_enum_value(RedactionStatus, _req_str(data, "status", where), "status", where),
            occurrences=_req_int(data, "occurrences", where),
        )


@dataclass(frozen=True, slots=True)
class RedactedText:
    """Text that has crossed the boundary, plus what was taken out of it."""

    text: str
    notes: tuple[RedactionNote, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.notes


@dataclass(frozen=True, slots=True)
class RedactionResult[RecordT]:
    """A redacted record and the notes explaining what it no longer carries."""

    record: RecordT
    notes: tuple[RedactionNote, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.notes


@dataclass(frozen=True, slots=True)
class PrivateBody:
    """A private body represented by its size and hash — never its content."""

    field: str
    size: SizeMeasurement
    content_hash: str
    redaction: RedactionStatus = RedactionStatus.WITHHELD

    def __post_init__(self) -> None:
        where = "private body"
        object.__setattr__(
            self, "redaction", _enum_value(RedactionStatus, self.redaction, "redaction", where)
        )
        if not self.field.strip():
            raise RedactionError(f"{where}: field must not be empty")
        if self.redaction is RedactionStatus.NONE_NEEDED:
            raise RedactionError(
                f"{where}: {self.field!r} stands in for content that was withheld"
                " and cannot claim that nothing was hidden"
            )
        if not _HASH_SHAPE.fullmatch(self.content_hash):
            raise RedactionError(f"{where}: {self.field!r} needs a hash, not content")

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "size": self.size.to_dict(),
            "content_hash": self.content_hash,
            "redaction": self.redaction.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "private body"
        data = _payload(payload, where)
        _reject_unknown(data, ("field", "size", "content_hash", "redaction"), where)
        return cls(
            field=_req_str(data, "field", where),
            size=SizeMeasurement.from_dict(_payload(data["size"], where)),
            content_hash=_req_str(data, "content_hash", where),
            redaction=_enum_value(
                RedactionStatus, _req_str(data, "redaction", where), "redaction", where
            ),
        )


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #


def safe_hash(content: str | bytes, *, salt: str = "") -> str:
    """A salted content hash: comparable across machines, never reversible.

    The salt exists because equality itself can be sensitive; two machines that
    share a salt can still prove two files are the same file.
    """
    raw = content.encode("utf-8") if isinstance(content, str) else content
    digest = hashlib.sha256(salt.encode("utf-8") + b"\x00" + raw).hexdigest()
    return f"{HASH_ALGORITHM}:{digest}"


def measure(content: str | bytes, *, estimate_tokens: bool = False) -> SizeMeasurement:
    """Exact bytes and characters, with an optional clearly labeled estimate."""
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
        byte_size = len(content)
    else:
        text = content
        byte_size = len(content.encode("utf-8"))
    if not estimate_tokens:
        return SizeMeasurement(byte_size=byte_size, characters=len(text))
    return SizeMeasurement(
        byte_size=byte_size,
        characters=len(text),
        token_count=len(text) // 4,
        token_count_kind=TokenCountKind.ESTIMATED,
        token_method=TOKEN_ESTIMATE_METHOD,
    )


def scan(text: str, *, field: str = "text") -> tuple[RedactionNote, ...]:
    """Report what a text still carries, without changing it."""
    _, counts = _scrub(text)
    return _notes(counts, field, RedactionStatus.REDACTED)


def redact_text(value: str, *, field: str = "text") -> RedactedText:
    """Replace every detected secret value; safe metadata is left alone."""
    text, counts = _scrub(value)
    return RedactedText(text, _notes(counts, field, RedactionStatus.REDACTED))


def assert_no_secrets(text: str, *, field: str = "bundle") -> None:
    """Fail closed: raise rather than let a matching payload be emitted."""
    notes = scan(text, field=field)
    if notes:
        rules = ", ".join(sorted({note.rule for note in notes}))
        raise RedactionError(f"{field}: refusing to emit; still matches {rules}")


def withhold_body(content: str | bytes, *, field: str, salt: str = "") -> PrivateBody:
    """Keep a private body's size and hash and drop the body itself."""
    return PrivateBody(
        field=field,
        size=measure(content, estimate_tokens=True),
        content_hash=safe_hash(content, salt=salt),
    )


def redact_environment(
    environment: Mapping[str, str], *, field: str = "environment"
) -> RedactionResult[Mapping[str, str]]:
    """Keep variable names as evidence; never keep a variable's value.

    A name that is itself credential-shaped is not evidence, so the entry is
    withheld behind a hash of the name instead.
    """
    safe: dict[str, str] = {}
    notes: list[RedactionNote] = []
    for name, _value in sorted(environment.items()):
        if scan(name):
            key = f"[withheld:{safe_hash(name)[-12:]}]"
            notes.append(RedactionNote(field, "unsafe-variable-name", RedactionStatus.WITHHELD))
        else:
            key = name
        safe[key] = SECRET_PLACEHOLDER
        notes.append(RedactionNote(field, "environment-value", RedactionStatus.REDACTED))
    return RedactionResult(MappingProxyType(safe), _merge(notes))


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


def redact_evidence(record: EvidenceRecord, *, field: str = "evidence") -> EvidenceRecord:
    """Redact an evidence record in place of its caller; notes are the caller's."""
    return _evidence(record, field, [])


def redact_item(item: InventoryItem, *, salt: str = "") -> RedactionResult[InventoryItem]:
    """Strip an inventory item's secrets, keeping everything the rules read."""
    notes: list[RedactionNote] = []
    where = f"item[{item.item_id}]"
    unrecognized: dict[str, str] = {}
    for key, value in item.unrecognized.items():
        safe_key = _scrubbed(key, f"{where}.unrecognized.name", notes)
        unrecognized[safe_key] = WITHHELD_PLACEHOLDER
        if value != WITHHELD_PLACEHOLDER:
            # Data carried forward from an unfamiliar CLI has no known shape,
            # so its value is withheld rather than pattern-matched.
            notes.append(
                RedactionNote(
                    f"{where}.unrecognized[{safe_key}]",
                    "unrecognized-value",
                    RedactionStatus.WITHHELD,
                )
            )
    redacted = replace(
        item,
        item_id=_scrubbed(item.item_id, f"{where}.item_id", notes),
        label=_scrubbed(item.label, f"{where}.label", notes),
        sources=tuple(
            _source(source, f"{where}.sources[{index}]", notes)
            for index, source in enumerate(item.sources)
        ),
        evidence=_evidence(item.evidence, f"{where}.evidence", notes),
        permissions=_scrubbed(item.permissions, f"{where}.permissions", notes),
        content_hash=_hash_field(item.content_hash, f"{where}.content_hash", notes, salt),
        effective_behavior=_scrubbed(item.effective_behavior, f"{where}.effective_behavior", notes),
        redaction=_status(item.redaction, notes),
        unrecognized=unrecognized,
    )
    return RedactionResult(redacted, _merge(notes))


def redact_inventory(
    inventory: HarnessInventory, *, salt: str = ""
) -> RedactionResult[HarnessInventory]:
    """Redact every item in one target's inventory, keeping the target itself."""
    notes: list[RedactionNote] = []
    items: list[InventoryItem] = []
    for item in inventory.items:
        result = redact_item(item, salt=salt)
        items.append(result.record)
        notes.extend(result.notes)
    return RedactionResult(replace(inventory, items=tuple(items)), _merge(notes))


def redact_finding(finding: Finding) -> RedactionResult[Finding]:
    """Redact a finding's prose while keeping its check, counts and citations."""
    notes: list[RedactionNote] = []
    where = f"finding[{finding.finding_id}]"
    redacted = replace(
        finding,
        finding_id=_scrubbed(finding.finding_id, f"{where}.finding_id", notes),
        summary=_scrubbed(finding.summary, f"{where}.summary", notes),
        item_ids=tuple(_scrubbed(value, f"{where}.item_ids", notes) for value in finding.item_ids),
        evidence=tuple(
            _evidence(record, f"{where}.evidence[{index}]", notes)
            for index, record in enumerate(finding.evidence)
        ),
        vendor_sources=tuple(
            _citation(citation, f"{where}.vendor_sources[{index}]", notes)
            for index, citation in enumerate(finding.vendor_sources)
        ),
        comparison_method=_scrubbed(finding.comparison_method, f"{where}.comparison_method", notes),
        missing_evidence=tuple(
            _scrubbed(value, f"{where}.missing_evidence", notes)
            for value in finding.missing_evidence
        ),
    )
    return RedactionResult(redacted, _merge(notes))


def redact_classification(
    record: ClassificationRecord,
) -> RedactionResult[ClassificationRecord]:
    """Redact a verdict's prose while keeping the verdict and its next action."""
    notes: list[RedactionNote] = []
    where = f"classification[{record.item_id}]"
    redacted = replace(
        record,
        item_id=_scrubbed(record.item_id, f"{where}.item_id", notes),
        reason=_scrubbed(record.reason, f"{where}.reason", notes),
        evidence=tuple(
            _evidence(entry, f"{where}.evidence[{index}]", notes)
            for index, entry in enumerate(record.evidence)
        ),
        risk=_scrubbed(record.risk, f"{where}.risk", notes),
        reversible_action=_scrubbed(record.reversible_action, f"{where}.reversible_action", notes),
        finding_ids=tuple(
            _scrubbed(value, f"{where}.finding_ids", notes) for value in record.finding_ids
        ),
        activation_boundary=_scrubbed(
            record.activation_boundary, f"{where}.activation_boundary", notes
        ),
        uncertainty=_scrubbed(record.uncertainty, f"{where}.uncertainty", notes),
    )
    return RedactionResult(redacted, _merge(notes))


def redact_machine_exception(
    exception: MachineException,
) -> RedactionResult[MachineException]:
    """Redact a named exception's prose while keeping the exception itself."""
    notes: list[RedactionNote] = []
    where = f"exception[{exception.exception_id}]"
    redacted = replace(
        exception,
        exception_id=_scrubbed(exception.exception_id, f"{where}.exception_id", notes),
        description=_scrubbed(exception.description, f"{where}.description", notes),
        preserved_outcome=_scrubbed(
            exception.preserved_outcome, f"{where}.preserved_outcome", notes
        ),
        evidence=_evidence(exception.evidence, f"{where}.evidence", notes),
    )
    return RedactionResult(redacted, _merge(notes))


def redact_coverage_gap(gap: CoverageGap) -> RedactionResult[CoverageGap]:
    """Redact a gap's prose; a gap stays a gap, it never becomes coverage."""
    notes: list[RedactionNote] = []
    where = f"gap[{gap.target.key}]"
    redacted = replace(
        gap,
        reason=_scrubbed(gap.reason, f"{where}.reason", notes),
        missing_evidence=tuple(
            _scrubbed(value, f"{where}.missing_evidence", notes) for value in gap.missing_evidence
        ),
    )
    return RedactionResult(redacted, _merge(notes))


# --------------------------------------------------------------------------- #
# Bundles
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RedactedBundle:
    """One machine's redacted evidence, in the form the other machine sees.

    The bundle is the only thing that crosses computers, so it is also where
    the boundary is enforced: everything in it belongs to one machine, and
    :func:`serialize_bundle` re-checks the bytes before they exist.
    """

    machine: Machine
    created_at: datetime
    inventories: tuple[HarnessInventory, ...] = ()
    findings: tuple[Finding, ...] = ()
    classifications: tuple[ClassificationRecord, ...] = ()
    exceptions: tuple[MachineException, ...] = ()
    coverage_gaps: tuple[CoverageGap, ...] = ()
    private_bodies: tuple[PrivateBody, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    notes: tuple[RedactionNote, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        where = "bundle"
        object.__setattr__(self, "machine", _enum_value(Machine, self.machine, "machine", where))
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise RedactionError(
                f"{where}: schema_version {self.schema_version} is not supported"
                f" (supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)})"
            )
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise RedactionError(f"{where}: created_at must be timezone-aware")
        object.__setattr__(
            self,
            "inventories",
            tuple(sorted(self.inventories, key=lambda entry: entry.target.key)),
        )
        object.__setattr__(
            self, "findings", tuple(sorted(self.findings, key=lambda entry: entry.finding_id))
        )
        object.__setattr__(
            self,
            "classifications",
            tuple(sorted(self.classifications, key=lambda entry: entry.item_id)),
        )
        object.__setattr__(
            self,
            "exceptions",
            tuple(sorted(self.exceptions, key=lambda entry: entry.exception_id)),
        )
        object.__setattr__(
            self,
            "coverage_gaps",
            tuple(sorted(self.coverage_gaps, key=lambda entry: entry.target.key)),
        )
        object.__setattr__(
            self,
            "private_bodies",
            tuple(sorted(self.private_bodies, key=lambda entry: entry.field)),
        )
        environment = dict(self.environment)
        for name, value in environment.items():
            if value != SECRET_PLACEHOLDER:
                raise RedactionError(f"{where}: environment value for {name!r} was not redacted")
        object.__setattr__(self, "environment", MappingProxyType(dict(sorted(environment.items()))))
        object.__setattr__(self, "notes", _merge(self.notes))
        for inventory in self.inventories:
            if inventory.target.machine is not self.machine:
                raise RedactionError(
                    f"{where}: inventory for {inventory.target.key!r} does not belong"
                    f" to machine {self.machine.value!r}"
                )
        for exception in self.exceptions:
            if exception.machine is not self.machine:
                raise RedactionError(
                    f"{where}: exception {exception.exception_id!r} belongs to"
                    f" {exception.machine.value!r}, not {self.machine.value!r}"
                )
        for gap in self.coverage_gaps:
            if gap.target.machine is not self.machine:
                raise RedactionError(
                    f"{where}: coverage gap for {gap.target.key!r} does not belong"
                    f" to machine {self.machine.value!r}"
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "machine": self.machine.value,
            "created_at": self.created_at.isoformat(),
            "inventories": [entry.to_dict() for entry in self.inventories],
            "findings": [entry.to_dict() for entry in self.findings],
            "classifications": [entry.to_dict() for entry in self.classifications],
            "exceptions": [entry.to_dict() for entry in self.exceptions],
            "coverage_gaps": [entry.to_dict() for entry in self.coverage_gaps],
            "private_bodies": [entry.to_dict() for entry in self.private_bodies],
            "environment": dict(self.environment),
            "notes": [note.to_dict() for note in self.notes],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        where = "bundle"
        data = _payload(payload, where)
        _reject_unknown(
            data,
            (
                "schema_version",
                "machine",
                "created_at",
                "inventories",
                "findings",
                "classifications",
                "exceptions",
                "coverage_gaps",
                "private_bodies",
                "environment",
                "notes",
            ),
            where,
        )
        environment: dict[str, str] = {}
        for name, value in _payload(data.get("environment", {}), where).items():
            if not isinstance(value, str):
                raise RedactionError(f"{where}: environment values must be strings")
            environment[name] = value
        return cls(
            machine=_enum_value(Machine, _req_str(data, "machine", where), "machine", where),
            created_at=_req_datetime(data, "created_at", where),
            inventories=tuple(
                HarnessInventory.from_dict(_payload(entry, where))
                for entry in _entries(data, "inventories", where)
            ),
            findings=tuple(
                Finding.from_dict(_payload(entry, where))
                for entry in _entries(data, "findings", where)
            ),
            classifications=tuple(
                ClassificationRecord.from_dict(_payload(entry, where))
                for entry in _entries(data, "classifications", where)
            ),
            exceptions=tuple(
                MachineException.from_dict(_payload(entry, where))
                for entry in _entries(data, "exceptions", where)
            ),
            coverage_gaps=tuple(
                CoverageGap.from_dict(_payload(entry, where))
                for entry in _entries(data, "coverage_gaps", where)
            ),
            private_bodies=tuple(
                PrivateBody.from_dict(_payload(entry, where))
                for entry in _entries(data, "private_bodies", where)
            ),
            environment=environment,
            notes=tuple(
                RedactionNote.from_dict(_payload(entry, where))
                for entry in _entries(data, "notes", where)
            ),
            schema_version=_req_int(data, "schema_version", where),
        )


def build_redacted_bundle(
    *,
    machine: Machine,
    created_at: datetime,
    inventories: Sequence[HarnessInventory] = (),
    findings: Sequence[Finding] = (),
    classifications: Sequence[ClassificationRecord] = (),
    exceptions: Sequence[MachineException] = (),
    coverage_gaps: Sequence[CoverageGap] = (),
    private_bodies: Sequence[PrivateBody] = (),
    environment: Mapping[str, str] | None = None,
    salt: str = "",
) -> RedactedBundle:
    """Redact every record, then assemble the bundle that may cross machines."""
    notes: list[RedactionNote] = []

    def collect[RecordT](result: RedactionResult[RecordT]) -> RecordT:
        notes.extend(result.notes)
        return result.record

    safe_environment: Mapping[str, str] = {}
    if environment:
        safe_environment = collect(redact_environment(environment))
    for body in private_bodies:
        notes.append(RedactionNote(body.field, "private-body", RedactionStatus.WITHHELD))
    return RedactedBundle(
        machine=machine,
        created_at=created_at,
        inventories=tuple(collect(redact_inventory(entry, salt=salt)) for entry in inventories),
        findings=tuple(collect(redact_finding(entry)) for entry in findings),
        classifications=tuple(collect(redact_classification(entry)) for entry in classifications),
        exceptions=tuple(collect(redact_machine_exception(entry)) for entry in exceptions),
        coverage_gaps=tuple(collect(redact_coverage_gap(entry)) for entry in coverage_gaps),
        private_bodies=tuple(private_bodies),
        environment=safe_environment,
        notes=tuple(notes),
    )


def serialize_bundle(bundle: RedactedBundle) -> str:
    """Serialize deterministically, refusing to emit anything still unsafe."""
    from extensions.harness_audit.models import to_json

    text = to_json(bundle)
    assert_no_secrets(text, field="bundle")
    return text


def parse_bundle(text: str) -> RedactedBundle:
    """Parse a bundle, rejecting any payload this schema does not recognize."""
    from extensions.harness_audit.models import from_json

    return from_json(RedactedBundle, text)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _scrub(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for rule in _RULES:
        text, hits = rule.apply(text)
        if hits:
            counts[rule.name] = counts.get(rule.name, 0) + hits
    return text, counts


def _notes(
    counts: Mapping[str, int], field_name: str, status: RedactionStatus
) -> tuple[RedactionNote, ...]:
    return tuple(RedactionNote(field_name, rule, status, counts[rule]) for rule in sorted(counts))


def _merge(notes: Iterable[RedactionNote]) -> tuple[RedactionNote, ...]:
    totals: dict[tuple[str, str, str], RedactionNote] = {}
    for note in notes:
        existing = totals.get(note.sort_key)
        totals[note.sort_key] = (
            note
            if existing is None
            else replace(note, occurrences=existing.occurrences + note.occurrences)
        )
    return tuple(totals[key] for key in sorted(totals))


def _scrubbed(value: str, field_name: str, notes: list[RedactionNote]) -> str:
    result = redact_text(value, field=field_name)
    notes.extend(result.notes)
    return result.text


def _evidence(record: EvidenceRecord, where: str, notes: list[RedactionNote]) -> EvidenceRecord:
    return replace(
        record,
        method=_scrubbed(record.method, f"{where}.method", notes),
        detail=_scrubbed(record.detail, f"{where}.detail", notes),
        source_reference=_scrubbed(record.source_reference, f"{where}.source_reference", notes),
        missing_evidence=tuple(
            _scrubbed(value, f"{where}.missing_evidence", notes)
            for value in record.missing_evidence
        ),
    )


def _source(source: ItemSource, where: str, notes: list[RedactionNote]) -> ItemSource:
    return replace(
        source,
        reference=_scrubbed(source.reference, f"{where}.reference", notes),
        evidence=_evidence(source.evidence, f"{where}.evidence", notes),
    )


def _citation(citation: VendorCitation, where: str, notes: list[RedactionNote]) -> VendorCitation:
    return replace(
        citation,
        url=_scrubbed(citation.url, f"{where}.url", notes),
        applies_to_versions=_scrubbed(
            citation.applies_to_versions, f"{where}.applies_to_versions", notes
        ),
        quoted_proposition=_scrubbed(
            citation.quoted_proposition, f"{where}.quoted_proposition", notes
        ),
    )


def _hash_field(value: str, field_name: str, notes: list[RedactionNote], salt: str) -> str:
    """A hash field must hold a hash; anything else is treated as content."""
    if not value.strip():
        return value
    if _HASH_SHAPE.fullmatch(value.strip()):
        return value
    notes.append(RedactionNote(field_name, "unhashed-content", RedactionStatus.REDACTED))
    return safe_hash(value, salt=salt)


def _status(current: RedactionStatus, notes: Sequence[RedactionNote]) -> RedactionStatus:
    if any(note.status is RedactionStatus.WITHHELD for note in notes):
        return RedactionStatus.WITHHELD
    if notes:
        return RedactionStatus.REDACTED
    return current


def _enum_value[EnumT: RedactionStatus | Machine](
    enum_type: type[EnumT], value: object, key: str, where: str
) -> EnumT:
    try:
        return enum_type(value)
    except ValueError as error:
        raise RedactionError(
            f"{where}: {key} {value!r} is not a valid {enum_type.__name__}"
        ) from error


def _payload(payload: object, where: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise RedactionError(f"{where}: expected an object, got {type(payload).__name__}")
    for key in payload:
        if not isinstance(key, str):
            raise RedactionError(f"{where}: object keys must be strings")
    return payload


def _reject_unknown(payload: Mapping[str, object], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise RedactionError(f"{where}: unknown field(s) {', '.join(unknown)}")


def _req_str(payload: Mapping[str, object], key: str, where: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RedactionError(f"{where}: {key} must be a string")
    return value


def _req_int(payload: Mapping[str, object], key: str, where: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RedactionError(f"{where}: {key} must be an integer")
    return value


def _req_datetime(payload: Mapping[str, object], key: str, where: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_req_str(payload, key, where))
    except ValueError as error:
        raise RedactionError(f"{where}: {key} is not an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RedactionError(f"{where}: {key} must be timezone-aware")
    return parsed


def _entries(payload: Mapping[str, object], key: str, where: str) -> list[object]:
    value = payload.get(key, [])
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise RedactionError(f"{where}: {key} must be a list")
    return list(value)
