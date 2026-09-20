"""Immutable domain model for the My AI Setup inventory.

Discord's **My AI Setup** has to answer three questions about a custom AI
addition — what is it, where does it live, and does a harness actually load it —
across Claude, Codex, DSH and more than one computer.  This module holds only
that vocabulary: identities, sources, user-facing scopes, availability
evidence, prerequisites, measurements, diagnostics and snapshots.  Adapters
(tasks 2.x), redaction (task 1.2) and the collector (task 1.3) build on it.

Three rules shape every type here:

* **Presence is not loading.**  A readable file is ``discovered``; a harness
  entry that names it is ``configured``.  Only deterministic loader evidence —
  a resolved source set, a harness-generated inventory — may claim
  ``verified_loaded``, and :class:`HarnessAvailability` refuses to record that
  state without evidence and a verification time.  Everything the model cannot
  establish stays ``unknown`` rather than becoming a green check.
* **Nothing raw comes in.**  There is no field anywhere for file content, an
  environment value or a credential.  Sameness travels as a one-way
  :class:`ContentFingerprint`; every free-text field is a single bounded line,
  so a configuration file cannot be smuggled through a label, a locator or a
  diagnostic message.
* **Identity is computer-free.**  ``skill:claude-home:grilling`` means the same
  item on DrewAI and on the iMac, which is what makes *Compare computers*
  possible; the computer travels on the item's source and on the snapshot.

The internal :class:`OwnershipClass` is an inheritance and audit input only.
:meth:`EffectiveScope.for_ownership` maps it to a user-facing scope, and no
label produced here ever says "Mega Global".

Nothing in this module touches the filesystem.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

MAX_LABEL_LENGTH = 120
MAX_LOCATOR_LENGTH = 240
MAX_MESSAGE_LENGTH = 400
DEFAULT_SNAPSHOT_TTL = timedelta(hours=12)

_KEY_SEPARATOR = ":"
_INVALID_TOKEN_CHARS = re.compile(r"[^a-z0-9]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_HEX_DIGEST = re.compile(r"\A[0-9a-f]{32,128}\Z")


def normalize_token(value: str, *, kind: str) -> str:
    """Normalize a computer, owner, harness or source key into a portable token.

    Tokens are restricted to ``[a-z0-9-]`` so an identity key can be split back
    apart unambiguously — only the item name, which comes last, is free-form.
    """
    token = _INVALID_TOKEN_CHARS.sub("-", value.strip().lower()).strip("-")
    if not token:
        raise ValueError(f"An inventory {kind} must contain at least one letter or digit")
    return token


def safe_text(value: str, *, kind: str, limit: int = MAX_LABEL_LENGTH) -> str:
    """Accept one bounded, single-line, control-character-free display string.

    This is the structural half of the safety boundary: a label, locator,
    evidence note or diagnostic message cannot carry a configuration file or a
    multi-line secret block, whatever an adapter passes in.  Task 1.2's
    redactor adds the value-level checks on top.
    """
    text = value.strip()
    if not text:
        raise ValueError(f"An inventory {kind} must not be empty")
    if _CONTROL_CHARS.search(text):
        raise ValueError(f"An inventory {kind} must be a single line of readable text")
    if len(text) > limit:
        raise ValueError(f"An inventory {kind} is too long ({len(text)} > {limit} characters)")
    return text


def _require_aware(value: datetime | None, *, kind: str) -> datetime | None:
    """Timestamps cross computers, so a naive one is an error rather than UTC."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"An inventory {kind} must carry a time zone")
    return value


class SetupKind(StrEnum):
    """The custom setup kinds the inventory covers."""

    PREFERENCE = "preference"
    INSTRUCTION = "instruction"
    MEMORY = "memory"
    SKILL = "skill"
    TOOL = "tool"
    PLUGIN = "plugin"
    CONNECTOR = "connector"
    COMMAND = "command"
    HOOK = "hook"
    HARNESS_SETTING = "harness_setting"

    @property
    def label(self) -> str:
        """The plural heading Browse by kind shows for this kind."""
        return _KIND_LABELS[self]


_KIND_LABELS: Mapping[SetupKind, str] = {
    SetupKind.PREFERENCE: "Preferences",
    SetupKind.INSTRUCTION: "Instructions",
    SetupKind.MEMORY: "Memory",
    SetupKind.SKILL: "Skills",
    SetupKind.TOOL: "Tools",
    SetupKind.PLUGIN: "Plugins",
    SetupKind.CONNECTOR: "Connectors",
    SetupKind.COMMAND: "Commands",
    SetupKind.HOOK: "Hooks",
    SetupKind.HARNESS_SETTING: "Harness settings",
}


class ScopeKind(StrEnum):
    """Where an item takes effect, in the words the user sees."""

    EVERYWHERE = "everywhere"
    SHARED_PROFILE = "shared_profile"
    COMPUTER = "computer"
    OTHER_PROFILE = "other_profile"
    PROJECT = "project"


class OwnershipClass(StrEnum):
    """Internal inheritance/audit class — never rendered, never a route name.

    The collector may reason about ownership this way; the UI only ever sees
    the :class:`EffectiveScope` that :meth:`EffectiveScope.for_ownership`
    returns.
    """

    MEGA_GLOBAL = "mega_global"
    PROFILE = "profile"
    MACHINE = "machine"
    PROJECT = "project"


class Classification(StrEnum):
    """Whether an item is the user's, the user's override, or a stock default."""

    CUSTOM = "custom"
    OVERRIDDEN_BUILTIN = "overridden_builtin"
    BUILTIN = "builtin"

    @property
    def is_custom_view(self) -> bool:
        """True for the default view: custom additions and overrides only."""
        return self is not Classification.BUILTIN


class AvailabilityState(StrEnum):
    """What is actually known about one harness loading one item."""

    CONFIGURED = "configured"
    DISCOVERED = "discovered"
    VERIFIED_LOADED = "verified_loaded"
    UNSUPPORTED = "unsupported"
    MISSING_PREREQUISITE = "missing_prerequisite"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"

    @property
    def is_verified(self) -> bool:
        """Only deterministic loader evidence earns this."""
        return self is AvailabilityState.VERIFIED_LOADED

    @property
    def is_remote_problem(self) -> bool:
        """The fact is about the source computer, not about the item."""
        return self in (AvailabilityState.STALE, AvailabilityState.UNREACHABLE)

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").capitalize()


class PrerequisiteState(StrEnum):
    """Whether a requirement is met — never what its value is."""

    SATISFIED = "satisfied"
    MISSING = "missing"
    UNKNOWN = "unknown"


class PrerequisiteKind(StrEnum):
    """What sort of requirement an item has."""

    CREDENTIAL = "credential"
    BINARY = "binary"
    SUBSCRIPTION = "subscription"
    HARNESS = "harness"
    NETWORK = "network"
    OTHER = "other"


class MeasurementMethod(StrEnum):
    """How a token count was arrived at, so precision is never faked."""

    MEASURED = "measured"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class DiagnosticSeverity(StrEnum):
    """How badly one source or item failed to be inventoried."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Freshness(StrEnum):
    """How much a whole snapshot can still be trusted."""

    LIVE = "live"
    STALE = "stale"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EffectiveScope:
    """One user-facing scope: everywhere, a profile, a computer, or a project."""

    kind: ScopeKind
    owner: str | None = None
    computer: str | None = None
    project: str | None = None
    display_name: str | None = None

    def __post_init__(self) -> None:
        owner = normalize_token(self.owner, kind="owner") if self.owner else None
        computer = normalize_token(self.computer, kind="computer") if self.computer else None
        project = safe_text(self.project, kind="project name") if self.project else None
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "computer", computer)
        object.__setattr__(self, "project", project)
        if self.display_name is not None:
            object.__setattr__(
                self, "display_name", safe_text(self.display_name, kind="scope name")
            )
        if self.kind in (ScopeKind.SHARED_PROFILE, ScopeKind.OTHER_PROFILE) and not owner:
            raise ValueError(f"A {self.kind.value} scope needs the owner it belongs to")
        if self.kind is ScopeKind.COMPUTER and not computer:
            raise ValueError("A computer scope needs the computer it belongs to")
        if self.kind is ScopeKind.PROJECT and not project:
            raise ValueError("A project scope needs the project it belongs to")

    @classmethod
    def everywhere(cls) -> EffectiveScope:
        return cls(kind=ScopeKind.EVERYWHERE)

    @classmethod
    def shared_profile(cls, owner: str, *, display_name: str | None = None) -> EffectiveScope:
        return cls(kind=ScopeKind.SHARED_PROFILE, owner=owner, display_name=display_name)

    @classmethod
    def other_profile(cls, owner: str, *, display_name: str | None = None) -> EffectiveScope:
        return cls(kind=ScopeKind.OTHER_PROFILE, owner=owner, display_name=display_name)

    @classmethod
    def one_computer(cls, computer: str, *, display_name: str | None = None) -> EffectiveScope:
        return cls(kind=ScopeKind.COMPUTER, computer=computer, display_name=display_name)

    @classmethod
    def one_project(cls, project: str, *, computer: str | None = None) -> EffectiveScope:
        return cls(kind=ScopeKind.PROJECT, project=project, computer=computer)

    @classmethod
    def for_ownership(
        cls,
        ownership: OwnershipClass,
        *,
        owner: str | None = None,
        computer: str | None = None,
        project: str | None = None,
        primary_owner: str | None = None,
    ) -> EffectiveScope:
        """Translate an internal ownership class into what the user is shown."""
        match ownership:
            case OwnershipClass.MEGA_GLOBAL if owner is None:
                return cls.everywhere()
            case OwnershipClass.MEGA_GLOBAL | OwnershipClass.PROFILE:
                if owner is None:
                    raise ValueError("A profile ownership class needs its owner")
                is_primary = primary_owner is None or normalize_token(
                    owner, kind="owner"
                ) == normalize_token(primary_owner, kind="owner")
                return cls.shared_profile(owner) if is_primary else cls.other_profile(owner)
            case OwnershipClass.MACHINE:
                if computer is None:
                    raise ValueError("A machine ownership class needs its computer")
                return cls.one_computer(computer)
            case OwnershipClass.PROJECT:
                if project is None:
                    raise ValueError("A project ownership class needs its project")
                return cls.one_project(project, computer=computer)

    @property
    def owner_label(self) -> str:
        owner = self.owner or ""
        return self.display_name or owner.replace("-", " ").title()

    @property
    def label(self) -> str:
        """The scope wording shown in Discord; never internal vocabulary."""
        match self.kind:
            case ScopeKind.EVERYWHERE:
                return self.display_name or "Everywhere"
            case ScopeKind.SHARED_PROFILE:
                return f"Shared {self.owner_label} profile"
            case ScopeKind.OTHER_PROFILE:
                return f"{self.owner_label}'s profile"
            case ScopeKind.COMPUTER:
                return self.display_name or f"One computer ({self.computer})"
            case ScopeKind.PROJECT:
                return f"One project ({self.project})"

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True, slots=True)
class ContentFingerprint:
    """A one-way digest used only to tell two computers' copies apart."""

    digest: str
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", normalize_token(self.algorithm, kind="algorithm"))
        digest = self.digest.strip().lower()
        if not _HEX_DIGEST.match(digest):
            raise ValueError(f"A content fingerprint must be a hex digest, not {self.digest!r}")
        object.__setattr__(self, "digest", digest)

    @classmethod
    def of_bytes(cls, data: bytes, *, algorithm: str = "sha256") -> ContentFingerprint:
        """Hash content the adapter already holds; the content is not retained."""
        return cls(digest=hashlib.new(algorithm, data).hexdigest(), algorithm=algorithm)

    @classmethod
    def of_text(cls, text: str, *, algorithm: str = "sha256") -> ContentFingerprint:
        return cls.of_bytes(text.encode("utf-8"), algorithm=algorithm)

    @property
    def short(self) -> str:
        """A 12-character prefix for compact Discord rendering."""
        return self.digest[:12]

    def matches(self, other: ContentFingerprint | None) -> bool:
        if other is None:
            return False
        return (self.algorithm, self.digest) == (other.algorithm, other.digest)

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.short}"


@dataclass(frozen=True, slots=True)
class InventorySource:
    """Where an item came from: a safe locator and its modification time."""

    key: str
    computer: str
    label: str
    locator: str
    harness: str | None = None
    modified_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", normalize_token(self.key, kind="source key"))
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        object.__setattr__(self, "label", safe_text(self.label, kind="source label"))
        object.__setattr__(
            self, "locator", safe_text(self.locator, kind="locator", limit=MAX_LOCATOR_LENGTH)
        )
        if self.harness is not None:
            object.__setattr__(self, "harness", normalize_token(self.harness, kind="harness"))
        object.__setattr__(
            self, "modified_at", _require_aware(self.modified_at, kind="modification time")
        )

    @property
    def has_known_modification_time(self) -> bool:
        return self.modified_at is not None

    def describe(self) -> str:
        return f"{self.label} — {self.locator}"


@dataclass(frozen=True, slots=True)
class HarnessAvailability:
    """What one harness on one computer is known to do with an item."""

    harness: str
    state: AvailabilityState = AvailabilityState.UNKNOWN
    computer: str | None = None
    evidence: str | None = None
    verified_at: datetime | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "harness", normalize_token(self.harness, kind="harness"))
        if self.computer is not None:
            object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        if self.evidence is not None:
            object.__setattr__(self, "evidence", safe_text(self.evidence, kind="evidence"))
        if self.detail is not None:
            object.__setattr__(self, "detail", safe_text(self.detail, kind="availability detail"))
        object.__setattr__(
            self, "verified_at", _require_aware(self.verified_at, kind="verification time")
        )
        if self.state.is_verified and not (self.evidence and self.verified_at):
            raise ValueError(
                "A verified-loaded availability needs deterministic loader evidence "
                "and the time it was verified"
            )
        if self.state is AvailabilityState.STALE and self.verified_at is None:
            raise ValueError("A stale availability must carry its last verification time")

    @property
    def is_verified(self) -> bool:
        return self.state.is_verified

    @property
    def label(self) -> str:
        return self.state.label

    def mark_stale(self) -> HarnessAvailability:
        """Keep the fact and its verification time, drop the current claim."""
        if self.verified_at is None:
            raise ValueError("Only a previously verified availability can go stale")
        return replace(self, state=AvailabilityState.STALE)


@dataclass(frozen=True, slots=True)
class Prerequisite:
    """Something an item needs — reported as present or missing, never shown."""

    name: str
    state: PrerequisiteState = PrerequisiteState.UNKNOWN
    kind: PrerequisiteKind = PrerequisiteKind.OTHER
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", safe_text(self.name, kind="prerequisite name"))
        if self.detail is not None:
            object.__setattr__(self, "detail", safe_text(self.detail, kind="prerequisite detail"))

    @classmethod
    def credential(cls, name: str, *, present: bool | None = True) -> Prerequisite:
        """Record that a secret is configured or missing without reading it."""
        match present:
            case True:
                state = PrerequisiteState.SATISFIED
            case False:
                state = PrerequisiteState.MISSING
            case _:
                state = PrerequisiteState.UNKNOWN
        return cls(name=name, state=state, kind=PrerequisiteKind.CREDENTIAL)

    @property
    def is_satisfied(self) -> bool:
        return self.state is PrerequisiteState.SATISFIED

    @property
    def is_missing(self) -> bool:
        return self.state is PrerequisiteState.MISSING


@dataclass(frozen=True, slots=True)
class Measurement:
    """How big an item is, with the method that produced each number."""

    byte_size: int | None = None
    character_count: int | None = None
    token_count: int | None = None
    token_method: MeasurementMethod = MeasurementMethod.UNKNOWN
    tokenizer: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        for name in ("byte_size", "character_count", "token_count"):
            value = getattr(self, name)
            if value is not None and int(value) < 0:
                raise ValueError(f"An inventory {name.replace('_', ' ')} cannot be negative")
        if self.tokenizer is not None:
            object.__setattr__(self, "tokenizer", safe_text(self.tokenizer, kind="tokenizer"))
        if self.model is not None:
            object.__setattr__(self, "model", safe_text(self.model, kind="model name"))
        if self.token_method is MeasurementMethod.UNKNOWN and self.token_count is not None:
            raise ValueError("A token count with an unknown method would be invented precision")
        if self.token_method is not MeasurementMethod.UNKNOWN and self.token_count is None:
            raise ValueError(f"A {self.token_method.value} token method needs a token count")
        if self.token_method is MeasurementMethod.MEASURED and not self.tokenizer:
            raise ValueError("A measured token count must name the tokenizer that produced it")

    @classmethod
    def unknown(cls) -> Measurement:
        return UNKNOWN_MEASUREMENT

    @classmethod
    def measured(
        cls,
        *,
        byte_size: int | None = None,
        character_count: int | None = None,
        token_count: int,
        tokenizer: str,
        model: str | None = None,
    ) -> Measurement:
        return cls(
            byte_size=byte_size,
            character_count=character_count,
            token_count=token_count,
            token_method=MeasurementMethod.MEASURED,
            tokenizer=tokenizer,
            model=model,
        )

    @classmethod
    def estimated(
        cls,
        *,
        byte_size: int | None = None,
        character_count: int | None = None,
        token_count: int,
        tokenizer: str | None = None,
        model: str | None = None,
    ) -> Measurement:
        return cls(
            byte_size=byte_size,
            character_count=character_count,
            token_count=token_count,
            token_method=MeasurementMethod.ESTIMATED,
            tokenizer=tokenizer,
            model=model,
        )

    @property
    def has_size(self) -> bool:
        return self.byte_size is not None or self.character_count is not None

    @property
    def size_label(self) -> str:
        if self.byte_size is not None:
            return f"{self.byte_size:,} bytes"
        if self.character_count is not None:
            return f"{self.character_count:,} characters"
        return "unknown"

    @property
    def token_label(self) -> str:
        """Never a bare number: the method travels with the count."""
        if self.token_count is None:
            return "unknown"
        if self.token_method is MeasurementMethod.ESTIMATED:
            source = self.tokenizer or self.model
            suffix = f", {source}" if source else ""
            return f"~{self.token_count:,} tokens (estimated{suffix})"
        return f"{self.token_count:,} tokens ({self.tokenizer})"


UNKNOWN_MEASUREMENT = Measurement()


@dataclass(frozen=True, slots=True)
class ItemIdentity:
    """A stable, computer-free identity for one custom setup item."""

    kind: SetupKind
    source_key: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SetupKind(self.kind))
        object.__setattr__(self, "source_key", normalize_token(self.source_key, kind="source key"))
        object.__setattr__(self, "name", safe_text(self.name, kind="item name"))

    @property
    def key(self) -> str:
        """The storage key; the free-form name comes last so it may contain ``:``."""
        return _KEY_SEPARATOR.join((self.kind.value, self.source_key, self.name))

    @classmethod
    def from_key(cls, key: str) -> ItemIdentity:
        """Rebuild an identity written by :attr:`key`."""
        parts = key.split(_KEY_SEPARATOR, 2)
        if len(parts) != 3 or not all(part.strip() for part in parts):
            raise ValueError(f"Malformed inventory identity key: {key!r}")
        kind, source_key, name = parts
        try:
            setup_kind = SetupKind(kind)
        except ValueError as error:
            raise ValueError(f"Malformed inventory identity key: {key!r}") from error
        return cls(kind=setup_kind, source_key=source_key, name=name)

    def __str__(self) -> str:
        return self.key


@dataclass(frozen=True, slots=True)
class InventoryItem:
    """One custom addition or override, as safe metadata only."""

    identity: ItemIdentity
    display_name: str
    source: InventorySource
    scope: EffectiveScope
    classification: Classification = Classification.CUSTOM
    ownership: OwnershipClass | None = None
    availability: tuple[HarnessAvailability, ...] = ()
    prerequisites: tuple[Prerequisite, ...] = ()
    measurement: Measurement = UNKNOWN_MEASUREMENT
    fingerprint: ContentFingerprint | None = None
    last_changed_at: datetime | None = None
    summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "display_name", safe_text(self.display_name, kind="item name"))
        if self.summary is not None:
            object.__setattr__(self, "summary", safe_text(self.summary, kind="item summary"))
        object.__setattr__(self, "availability", _unique_availability(self.availability))
        object.__setattr__(self, "prerequisites", tuple(self.prerequisites))
        object.__setattr__(
            self, "last_changed_at", _require_aware(self.last_changed_at, kind="change time")
        )

    @property
    def kind(self) -> SetupKind:
        return self.identity.kind

    @property
    def computer(self) -> str:
        """The computer this copy was collected from; identity stays path-free."""
        return self.source.computer

    @property
    def is_custom(self) -> bool:
        return self.classification.is_custom_view

    @property
    def is_builtin(self) -> bool:
        return self.classification is Classification.BUILTIN

    @property
    def has_known_change_time(self) -> bool:
        return self.last_changed_at is not None

    @property
    def verified_harnesses(self) -> tuple[str, ...]:
        return tuple(entry.harness for entry in self.availability if entry.is_verified)

    @property
    def has_verified_availability(self) -> bool:
        return bool(self.verified_harnesses)

    @property
    def missing_prerequisites(self) -> tuple[Prerequisite, ...]:
        return tuple(item for item in self.prerequisites if item.is_missing)

    def availability_for(self, harness: str) -> HarnessAvailability | None:
        wanted = normalize_token(harness, kind="harness")
        for entry in self.availability:
            if entry.harness == wanted:
                return entry
        return None

    def state_for(self, harness: str) -> AvailabilityState:
        """An unreported harness is ``unknown``, never assumed to work."""
        entry = self.availability_for(harness)
        return entry.state if entry else AvailabilityState.UNKNOWN

    def is_verified_on(self, harness: str) -> bool:
        return self.state_for(harness).is_verified

    def with_availability(self, *entries: HarnessAvailability) -> InventoryItem:
        """Add or replace availability per harness, leaving the rest intact."""
        replaced = {entry.harness for entry in entries}
        kept = tuple(entry for entry in self.availability if entry.harness not in replaced)
        return replace(self, availability=kept + tuple(entries))

    def with_prerequisites(self, *prerequisites: Prerequisite) -> InventoryItem:
        return replace(self, prerequisites=tuple(prerequisites))

    def with_measurement(self, measurement: Measurement) -> InventoryItem:
        return replace(self, measurement=measurement)

    def with_classification(self, classification: Classification) -> InventoryItem:
        return replace(self, classification=classification)

    def with_fingerprint(self, fingerprint: ContentFingerprint) -> InventoryItem:
        return replace(self, fingerprint=fingerprint)

    def is_aligned_with(self, other: InventoryItem) -> bool:
        """Same item *and* same content; an unknown fingerprint is not parity."""
        if self.identity != other.identity or self.fingerprint is None:
            return False
        return self.fingerprint.matches(other.fingerprint)

    def __str__(self) -> str:
        return f"{self.display_name} ({self.kind.label})"


def _unique_availability(
    entries: Iterable[HarnessAvailability],
) -> tuple[HarnessAvailability, ...]:
    seen: set[str] = set()
    ordered: list[HarnessAvailability] = []
    for entry in entries:
        if entry.harness in seen:
            raise ValueError(
                f"Harness {entry.harness!r} reports availability more than once for one item"
            )
        seen.add(entry.harness)
        ordered.append(entry)
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class InventoryDiagnostic:
    """A source- or item-level problem, kept as data so collection continues."""

    source_key: str
    severity: DiagnosticSeverity
    message: str
    computer: str | None = None
    identity: ItemIdentity | None = None
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_key", normalize_token(self.source_key, kind="source key"))
        object.__setattr__(
            self,
            "message",
            safe_text(self.message, kind="diagnostic message", limit=MAX_MESSAGE_LENGTH),
        )
        if self.computer is not None:
            object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        object.__setattr__(
            self, "occurred_at", _require_aware(self.occurred_at, kind="diagnostic time")
        )

    @property
    def is_error(self) -> bool:
        return self.severity is DiagnosticSeverity.ERROR

    @property
    def affects_item(self) -> bool:
        return self.identity is not None


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    """One computer's safe inventory at one verification time."""

    computer: str
    owner: str
    collected_at: datetime
    items: tuple[InventoryItem, ...] = ()
    diagnostics: tuple[InventoryDiagnostic, ...] = ()
    freshness: Freshness = Freshness.LIVE
    verified_at: datetime | None = None
    source_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        collected_at = _require_aware(self.collected_at, kind="collection time")
        if collected_at is None:
            raise ValueError("An inventory snapshot must carry its collection time")
        object.__setattr__(
            self, "verified_at", _require_aware(self.verified_at, kind="verification time")
        )
        if self.verified_at is None:
            object.__setattr__(self, "verified_at", collected_at)
        if self.source_label is not None:
            object.__setattr__(
                self, "source_label", safe_text(self.source_label, kind="snapshot source")
            )
        object.__setattr__(self, "items", _unique_items(self.items))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.freshness is Freshness.UNREACHABLE and self.items:
            raise ValueError(
                "An unreachable snapshot has no items; a previous one is stale, not unreachable"
            )

    @property
    def is_current(self) -> bool:
        return self.freshness is Freshness.LIVE

    @property
    def custom_items(self) -> tuple[InventoryItem, ...]:
        return tuple(item for item in self.items if item.is_custom)

    @property
    def builtin_items(self) -> tuple[InventoryItem, ...]:
        return tuple(item for item in self.items if item.is_builtin)

    @property
    def undated_items(self) -> tuple[InventoryItem, ...]:
        """Recent changes lists these separately instead of guessing an order."""
        return tuple(item for item in self.items if not item.has_known_change_time)

    def visible_items(self, *, include_builtins: bool = False) -> tuple[InventoryItem, ...]:
        return self.items if include_builtins else self.custom_items

    def item(self, identity: ItemIdentity) -> InventoryItem | None:
        for item in self.items:
            if item.identity == identity:
                return item
        return None

    def counts_by_classification(self) -> Mapping[Classification, int]:
        counts = {classification: 0 for classification in Classification}
        for item in self.items:
            counts[item.classification] += 1
        return counts

    def items_by_kind(
        self, *, include_builtins: bool = False
    ) -> Mapping[SetupKind, tuple[InventoryItem, ...]]:
        visible = self.visible_items(include_builtins=include_builtins)
        grouped: dict[SetupKind, tuple[InventoryItem, ...]] = {}
        for kind in SetupKind:
            matching = tuple(item for item in visible if item.kind is kind)
            if matching:
                grouped[kind] = matching
        return grouped

    def items_by_scope(
        self, *, include_builtins: bool = False
    ) -> Mapping[ScopeKind, tuple[InventoryItem, ...]]:
        visible = self.visible_items(include_builtins=include_builtins)
        grouped: dict[ScopeKind, tuple[InventoryItem, ...]] = {}
        for scope in ScopeKind:
            matching = tuple(item for item in visible if item.scope.kind is scope)
            if matching:
                grouped[scope] = matching
        return grouped

    def recent_changes(
        self, *, limit: int | None = None, include_builtins: bool = False
    ) -> tuple[InventoryItem, ...]:
        """Newest known change first; undated items are not ranked at all."""
        dated = [
            item
            for item in self.visible_items(include_builtins=include_builtins)
            if item.last_changed_at is not None
        ]
        dated.sort(key=lambda item: item.last_changed_at or self.collected_at, reverse=True)
        return tuple(dated[:limit] if limit is not None else dated)

    def diagnostics_for(self, source_key: str) -> tuple[InventoryDiagnostic, ...]:
        wanted = normalize_token(source_key, kind="source key")
        return tuple(entry for entry in self.diagnostics if entry.source_key == wanted)

    def is_stale_at(self, now: datetime, *, max_age: timedelta = DEFAULT_SNAPSHOT_TTL) -> bool:
        verified_at = self.verified_at or self.collected_at
        return now - verified_at > max_age

    def aged(
        self, now: datetime, *, max_age: timedelta = DEFAULT_SNAPSHOT_TTL
    ) -> InventorySnapshot:
        """Return this snapshot, marked stale once its freshness policy expires."""
        if self.freshness is not Freshness.LIVE or not self.is_stale_at(now, max_age=max_age):
            return self
        return replace(self, freshness=Freshness.STALE)

    def with_freshness(self, freshness: Freshness) -> InventorySnapshot:
        return replace(self, freshness=freshness)


def _unique_items(items: Iterable[InventoryItem]) -> tuple[InventoryItem, ...]:
    seen: set[ItemIdentity] = set()
    ordered: list[InventoryItem] = []
    for item in items:
        if item.identity in seen:
            raise ValueError(f"Two items share the identity {item.identity.key!r} in one snapshot")
        seen.add(item.identity)
        ordered.append(item)
    return tuple(ordered)


__all__ = [
    "DEFAULT_SNAPSHOT_TTL",
    "MAX_LABEL_LENGTH",
    "MAX_LOCATOR_LENGTH",
    "MAX_MESSAGE_LENGTH",
    "UNKNOWN_MEASUREMENT",
    "AvailabilityState",
    "Classification",
    "ContentFingerprint",
    "DiagnosticSeverity",
    "EffectiveScope",
    "Freshness",
    "HarnessAvailability",
    "InventoryDiagnostic",
    "InventoryItem",
    "InventorySnapshot",
    "InventorySource",
    "ItemIdentity",
    "Measurement",
    "MeasurementMethod",
    "OwnershipClass",
    "Prerequisite",
    "PrerequisiteKind",
    "PrerequisiteState",
    "ScopeKind",
    "SetupKind",
    "normalize_token",
    "safe_text",
]
