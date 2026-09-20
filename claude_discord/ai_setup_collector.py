"""The adapter registry and collector that turn many sources into one snapshot.

`ai_setup_inventory` holds the vocabulary and `ai_setup_redaction` holds the
safety boundary.  This module is the only place that *assembles* an inventory:
adapters declare what they understand, the collector runs them all and merges
what they report into a single :class:`~claude_discord.ai_setup_inventory.
InventorySnapshot`.

It is deliberately frontend-neutral — no Discord, no filesystem, no harness
knowledge.  Adapters (tasks 2.x) bring those; the registry only needs each one
to declare a name, the setup kinds it covers and the source keys it owns.

Four rules shape the collection:

* **No source can abort the whole inventory.**  An adapter that raises, returns
  the wrong type, reports an item outside its declared boundary, or hands over
  something the redactor refuses costs exactly its own contribution.  Every
  failure becomes an :class:`~claude_discord.ai_setup_inventory.
  InventoryDiagnostic` on that source, and the rest of the snapshot is built as
  usual, because *"preserve existing setup when an adapter fails"* is the
  behaviour the spec asks for.
* **Merging is by stable identity, and it never weakens a fact.**  Two adapters
  may legitimately see the same item — the folder that holds a skill and the
  loader that proves the skill loads.  Merging keeps the stronger evidence per
  harness, a missing prerequisite over a satisfied one, a measured token count
  over an estimate, and never lets ``unknown`` overwrite anything.
* **Classification is a merge outcome, not a guess.**  An identity reported as
  a built-in by the vendor baseline *and* as custom by the user's own source is
  an *overridden* built-in.  Reported only by one side, it stays unchanged
  built-in or custom.  Nothing here reads a filename to decide.
* **Nothing raw gets through.**  Every item passes
  :func:`~claude_discord.ai_setup_redaction.screen_item` before it can reach a
  snapshot, and every diagnostic is built through
  :func:`~claude_discord.ai_setup_redaction.safe_diagnostic`, so an adapter
  cannot publish a credential even by accident.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    ContentFingerprint,
    DiagnosticSeverity,
    Freshness,
    HarnessAvailability,
    InventoryDiagnostic,
    InventoryItem,
    InventorySnapshot,
    InventorySource,
    ItemIdentity,
    Measurement,
    MeasurementMethod,
    Prerequisite,
    PrerequisiteState,
    SetupKind,
    normalize_token,
    safe_text,
)
from claude_discord.ai_setup_redaction import safe_diagnostic, screen_item

_EPOCH = datetime.min.replace(tzinfo=UTC)

# How much a state is *worth* as evidence, not how good the news is: a harness
# that positively reports "I cannot load this" outranks a readable file, and
# ``unknown`` outranks nothing at all.
_AVAILABILITY_RANK: Mapping[AvailabilityState, int] = {
    AvailabilityState.VERIFIED_LOADED: 6,
    AvailabilityState.UNSUPPORTED: 5,
    AvailabilityState.MISSING_PREREQUISITE: 4,
    AvailabilityState.CONFIGURED: 3,
    AvailabilityState.DISCOVERED: 2,
    AvailabilityState.STALE: 1,
    AvailabilityState.UNREACHABLE: 1,
    AvailabilityState.UNKNOWN: 0,
}

# A problem the user can act on must survive a second source that saw no problem.
_PREREQUISITE_RANK: Mapping[PrerequisiteState, int] = {
    PrerequisiteState.MISSING: 2,
    PrerequisiteState.SATISFIED: 1,
    PrerequisiteState.UNKNOWN: 0,
}

_TOKEN_METHOD_RANK: Mapping[MeasurementMethod, int] = {
    MeasurementMethod.MEASURED: 2,
    MeasurementMethod.ESTIMATED: 1,
    MeasurementMethod.UNKNOWN: 0,
}

# Which contribution supplies the facts that cannot be merged — source, scope,
# summary.  The user's own copy is the one they came to look at.
_CLASSIFICATION_PRIORITY: Mapping[Classification, int] = {
    Classification.OVERRIDDEN_BUILTIN: 3,
    Classification.CUSTOM: 2,
    Classification.BUILTIN: 0,
}


# ---------------------------------------------------------------------------
# What a collection run knows about itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CollectionContext:
    """The computer, owner and moment one collection run speaks for."""

    computer: str
    owner: str
    collected_at: datetime
    primary_owner: str | None = None
    harnesses: tuple[str, ...] = ()
    source_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        object.__setattr__(self, "owner", normalize_token(self.owner, kind="owner"))
        if self.primary_owner is not None:
            object.__setattr__(
                self, "primary_owner", normalize_token(self.primary_owner, kind="owner")
            )
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise ValueError("An inventory collection time must be timezone-aware")
        object.__setattr__(
            self,
            "harnesses",
            tuple(normalize_token(entry, kind="harness") for entry in self.harnesses),
        )
        if self.source_label is not None:
            object.__setattr__(
                self, "source_label", safe_text(self.source_label, kind="snapshot source")
            )

    def supports(self, harness: str) -> bool:
        """Whether this run was asked to speak for a harness at all.

        An empty harness list means "not declared" — adapters then report what
        they find, and an unreported harness stays ``unknown`` either way.
        """
        return normalize_token(harness, kind="harness") in self.harnesses

    def diagnostic(
        self,
        source_key: str,
        severity: DiagnosticSeverity,
        message: str,
        *,
        identity: ItemIdentity | None = None,
    ) -> InventoryDiagnostic:
        """A diagnostic stamped with this run's computer and time, already safe."""
        return safe_diagnostic(
            source_key,
            severity,
            message,
            computer=self.computer,
            identity=identity,
            occurred_at=self.collected_at,
        )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdapterResult:
    """Everything one adapter has to say: safe items and its own diagnostics."""

    items: tuple[InventoryItem, ...] = ()
    diagnostics: tuple[InventoryDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @classmethod
    def empty(cls) -> AdapterResult:
        return cls()

    @property
    def has_errors(self) -> bool:
        return any(entry.is_error for entry in self.diagnostics)


@runtime_checkable
class SetupAdapter(Protocol):
    """One bounded inventory source: what it covers, and how to read it.

    ``kinds`` and ``source_keys`` are a contract, not documentation — the
    collector withholds anything an adapter reports outside them, so a broken
    adapter cannot quietly publish another source's items.
    """

    @property
    def name(self) -> str: ...

    @property
    def kinds(self) -> Sequence[SetupKind]: ...

    @property
    def source_keys(self) -> Sequence[str]: ...

    def collect(self, context: CollectionContext) -> AdapterResult: ...


@dataclass(frozen=True, slots=True)
class CallableAdapter:
    """An adapter built from one function — for fixtures and thin sources."""

    name: str
    kinds: tuple[SetupKind, ...]
    source_keys: tuple[str, ...]
    gather: Callable[[CollectionContext], AdapterResult]

    def collect(self, context: CollectionContext) -> AdapterResult:
        return self.gather(context)


@dataclass(frozen=True, slots=True)
class AdapterRegistration:
    """A registered adapter with its declared boundary normalized."""

    adapter: SetupAdapter
    name: str
    kinds: tuple[SetupKind, ...]
    source_keys: tuple[str, ...]

    @property
    def primary_source_key(self) -> str:
        """Where a whole-adapter failure is reported."""
        return self.source_keys[0]

    def covers_kind(self, kind: SetupKind) -> bool:
        return kind in self.kinds

    def covers_source(self, source_key: str) -> bool:
        return source_key in self.source_keys

    def collect(self, context: CollectionContext) -> AdapterResult:
        return self.adapter.collect(context)


@dataclass(slots=True)
class AdapterRegistry:
    """The set of sources one collection run will ask, in registration order."""

    _registrations: dict[str, AdapterRegistration] = field(default_factory=dict)

    def register(self, adapter: SetupAdapter) -> AdapterRegistration:
        """Add an adapter, rejecting a missing boundary or a duplicate name."""
        name = normalize_token(adapter.name, kind="adapter name")
        if name in self._registrations:
            raise ValueError(f"An inventory adapter named {name!r} is already registered")
        kinds = _unique(SetupKind(kind) for kind in adapter.kinds)
        if not kinds:
            raise ValueError(f"Adapter {name!r} must declare at least one setup kind")
        source_keys = _unique(
            normalize_token(key, kind="source key") for key in adapter.source_keys
        )
        if not source_keys:
            raise ValueError(f"Adapter {name!r} must declare at least one source key")
        registration = AdapterRegistration(
            adapter=adapter, name=name, kinds=kinds, source_keys=source_keys
        )
        self._registrations[name] = registration
        return registration

    @property
    def registrations(self) -> tuple[AdapterRegistration, ...]:
        return tuple(self._registrations.values())

    @property
    def adapter_names(self) -> tuple[str, ...]:
        return tuple(self._registrations)

    @property
    def kinds(self) -> frozenset[SetupKind]:
        return frozenset(
            kind for entry in self._registrations.values() for kind in entry.kinds
        )

    @property
    def source_keys(self) -> frozenset[str]:
        return frozenset(
            key for entry in self._registrations.values() for key in entry.source_keys
        )

    def get(self, name: str) -> AdapterRegistration | None:
        return self._registrations.get(normalize_token(name, kind="adapter name"))

    def for_kind(self, kind: SetupKind) -> tuple[AdapterRegistration, ...]:
        return tuple(entry for entry in self.registrations if entry.covers_kind(kind))

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def __iter__(self) -> Iterable[AdapterRegistration]:
        return iter(self.registrations)

    def __len__(self) -> int:
        return len(self._registrations)


def _unique[T](values: Iterable[T]) -> tuple[T, ...]:
    seen: list[T] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


# ---------------------------------------------------------------------------
# What a collection run produced
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdapterOutcome:
    """Per-source bookkeeping, so a quiet adapter is visibly quiet."""

    adapter: str
    source_keys: tuple[str, ...]
    reported: int = 0
    accepted: int = 0
    merged: int = 0
    diagnostics: tuple[InventoryDiagnostic, ...] = ()
    failed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_keys", tuple(self.source_keys))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def withheld(self) -> int:
        """Items the adapter reported that did not reach the snapshot."""
        return self.reported - self.accepted

    @property
    def has_errors(self) -> bool:
        return self.failed or any(entry.is_error for entry in self.diagnostics)

    @property
    def succeeded(self) -> bool:
        return not self.has_errors


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """One snapshot plus the record of how each source behaved."""

    snapshot: InventorySnapshot
    outcomes: tuple[AdapterOutcome, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcomes", tuple(self.outcomes))

    @property
    def items(self) -> tuple[InventoryItem, ...]:
        return self.snapshot.items

    @property
    def diagnostics(self) -> tuple[InventoryDiagnostic, ...]:
        return self.snapshot.diagnostics

    @property
    def failed_adapters(self) -> tuple[str, ...]:
        return tuple(entry.adapter for entry in self.outcomes if entry.failed)

    @property
    def has_errors(self) -> bool:
        return bool(self.failed_adapters) or any(entry.is_error for entry in self.diagnostics)

    @property
    def is_complete(self) -> bool:
        """True only when every source was read with nothing left unexplained."""
        return not self.failed_adapters and not self.diagnostics

    def outcome(self, adapter: str) -> AdapterOutcome | None:
        wanted = normalize_token(adapter, kind="adapter name")
        for entry in self.outcomes:
            if entry.adapter == wanted:
                return entry
        return None


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def merge_availability(
    entries: Iterable[HarnessAvailability],
) -> tuple[HarnessAvailability, ...]:
    """Keep the strongest evidence per harness, in first-seen harness order."""
    best: dict[str, HarnessAvailability] = {}
    for entry in entries:
        current = best.get(entry.harness)
        if current is None or _availability_key(entry) > _availability_key(current):
            best[entry.harness] = entry
    return tuple(best.values())


def _availability_key(entry: HarnessAvailability) -> tuple[int, datetime, bool]:
    return (
        _AVAILABILITY_RANK[entry.state],
        entry.verified_at or _EPOCH,
        entry.evidence is not None,
    )


def merge_prerequisites(entries: Iterable[Prerequisite]) -> tuple[Prerequisite, ...]:
    """One entry per requirement; a missing requirement is never hidden."""
    best: dict[str, Prerequisite] = {}
    for entry in entries:
        current = best.get(entry.name)
        if current is None or _PREREQUISITE_RANK[entry.state] > _PREREQUISITE_RANK[current.state]:
            best[entry.name] = entry
    return tuple(best.values())


def merge_classifications(values: Iterable[Classification]) -> Classification:
    """Custom *and* built-in for one identity means the built-in was overridden."""
    seen = set(values)
    if Classification.OVERRIDDEN_BUILTIN in seen:
        return Classification.OVERRIDDEN_BUILTIN
    if Classification.CUSTOM in seen:
        return (
            Classification.OVERRIDDEN_BUILTIN
            if Classification.BUILTIN in seen
            else Classification.CUSTOM
        )
    return Classification.BUILTIN


def merge_measurements(values: Sequence[Measurement]) -> Measurement:
    """Combine sizes field by field; the token facts travel together.

    A token count, its method and its tokenizer are only valid as a set, so the
    best-supported measurement supplies all three rather than being spliced.
    """
    if not values:
        return Measurement.unknown()
    tokens = max(values, key=lambda entry: _TOKEN_METHOD_RANK[entry.token_method])
    return Measurement(
        byte_size=_first_known(entry.byte_size for entry in values),
        character_count=_first_known(entry.character_count for entry in values),
        token_count=tokens.token_count,
        token_method=tokens.token_method,
        tokenizer=tokens.tokenizer,
        model=tokens.model,
    )


def _first_known(values: Iterable[int | None]) -> int | None:
    return next((value for value in values if value is not None), None)


def merge_items(
    items: Iterable[InventoryItem],
    *,
    computer: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[tuple[InventoryItem, ...], tuple[InventoryDiagnostic, ...]]:
    """Merge contributions by stable identity, keeping first-seen order.

    Returns the merged items and any diagnostics the merge itself produced —
    today that is only genuine disagreement about content, which must be told
    rather than silently resolved.
    """
    grouped: dict[ItemIdentity, list[InventoryItem]] = {}
    for entry in items:
        grouped.setdefault(entry.identity, []).append(entry)
    merged: list[InventoryItem] = []
    diagnostics: list[InventoryDiagnostic] = []
    for contributions in grouped.values():
        item, conflict = _merge_one(contributions, computer=computer, occurred_at=occurred_at)
        merged.append(item)
        if conflict is not None:
            diagnostics.append(conflict)
    return tuple(merged), tuple(diagnostics)


def _merge_one(
    contributions: Sequence[InventoryItem],
    *,
    computer: str | None,
    occurred_at: datetime | None,
) -> tuple[InventoryItem, InventoryDiagnostic | None]:
    primary = max(contributions, key=_contribution_priority)
    if len(contributions) == 1:
        return primary, None
    fingerprint, conflict = _merge_fingerprints(contributions, primary)
    merged = replace(
        primary,
        classification=merge_classifications(entry.classification for entry in contributions),
        ownership=primary.ownership
        or next((entry.ownership for entry in contributions if entry.ownership), None),
        availability=merge_availability(
            entry for contribution in contributions for entry in contribution.availability
        ),
        prerequisites=merge_prerequisites(
            entry for contribution in contributions for entry in contribution.prerequisites
        ),
        measurement=merge_measurements([entry.measurement for entry in contributions]),
        fingerprint=fingerprint,
        last_changed_at=_latest(entry.last_changed_at for entry in contributions),
        summary=primary.summary
        or next((entry.summary for entry in contributions if entry.summary), None),
        source=_merge_source(contributions, primary),
    )
    diagnostic = (
        safe_diagnostic(
            merged.identity.source_key,
            DiagnosticSeverity.WARNING,
            f"Two sources disagree about the content of {merged.display_name}; "
            "the copy from the source shown is the one described",
            computer=computer or merged.source.computer,
            identity=merged.identity,
            occurred_at=occurred_at,
        )
        if conflict
        else None
    )
    return merged, diagnostic


def _contribution_priority(item: InventoryItem) -> tuple[int, bool]:
    return (
        _CLASSIFICATION_PRIORITY[item.classification],
        item.source.has_known_modification_time,
    )


def _merge_source(
    contributions: Sequence[InventoryItem], primary: InventoryItem
) -> InventorySource:
    """Keep the primary source, but never lose a known modification time."""
    if primary.source.has_known_modification_time:
        return primary.source
    for entry in contributions:
        if entry.source.key == primary.source.key and entry.source.has_known_modification_time:
            return entry.source
    return primary.source


def _merge_fingerprints(
    contributions: Sequence[InventoryItem], primary: InventoryItem
) -> tuple[ContentFingerprint | None, bool]:
    known = [entry.fingerprint for entry in contributions if entry.fingerprint is not None]
    if not known:
        return None, False
    chosen = primary.fingerprint or known[0]
    return chosen, any(not chosen.matches(entry) for entry in known)


def _latest(values: Iterable[datetime | None]) -> datetime | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


# ---------------------------------------------------------------------------
# The collector
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InventoryCollector:
    """Runs every registered adapter and merges what survives into a snapshot."""

    registry: AdapterRegistry = field(default_factory=AdapterRegistry)

    def register(self, adapter: SetupAdapter) -> AdapterRegistration:
        return self.registry.register(adapter)

    def collect(self, context: CollectionContext) -> CollectionResult:
        """Collect once. No adapter's failure prevents the snapshot being built."""
        accepted: list[InventoryItem] = []
        diagnostics: list[InventoryDiagnostic] = []
        outcomes: list[AdapterOutcome] = []
        for registration in self.registry.registrations:
            outcome, items = self._run(registration, context)
            outcomes.append(outcome)
            accepted.extend(items)
            diagnostics.extend(outcome.diagnostics)
        items, merge_diagnostics = merge_items(
            accepted, computer=context.computer, occurred_at=context.collected_at
        )
        diagnostics.extend(merge_diagnostics)
        snapshot = InventorySnapshot(
            computer=context.computer,
            owner=context.owner,
            collected_at=context.collected_at,
            items=items,
            diagnostics=tuple(diagnostics),
            freshness=Freshness.LIVE,
            verified_at=context.collected_at,
            source_label=context.source_label,
        )
        return CollectionResult(snapshot=snapshot, outcomes=tuple(outcomes))

    def _run(
        self, registration: AdapterRegistration, context: CollectionContext
    ) -> tuple[AdapterOutcome, tuple[InventoryItem, ...]]:
        result, failure = _invoke(registration, context)
        diagnostics: list[InventoryDiagnostic] = []
        if failure is not None:
            diagnostics.append(failure)
        diagnostics.extend(result.diagnostics)
        accepted: list[InventoryItem] = []
        for item in result.items:
            kept, rejection = self._screen(registration, item, context)
            if rejection is not None:
                diagnostics.append(rejection)
            if kept is not None:
                accepted.append(kept)
        outcome = AdapterOutcome(
            adapter=registration.name,
            source_keys=registration.source_keys,
            reported=len(result.items),
            accepted=len(accepted),
            merged=len({item.identity for item in accepted}),
            diagnostics=tuple(diagnostics),
            failed=failure is not None,
        )
        return outcome, tuple(accepted)

    def _screen(
        self,
        registration: AdapterRegistration,
        item: InventoryItem,
        context: CollectionContext,
    ) -> tuple[InventoryItem | None, InventoryDiagnostic | None]:
        """Boundary first, then redaction — both withhold only this one item."""
        key = item.identity.key
        if not registration.covers_kind(item.kind):
            return None, context.diagnostic(
                registration.primary_source_key,
                DiagnosticSeverity.ERROR,
                f"Item {key} was withheld because its kind is outside the "
                f"{registration.name} source's declared kinds",
                identity=item.identity,
            )
        if not registration.covers_source(item.identity.source_key):
            return None, context.diagnostic(
                registration.primary_source_key,
                DiagnosticSeverity.ERROR,
                f"Item {key} was withheld because it is outside the "
                f"{registration.name} source's declared sources",
                identity=item.identity,
            )
        if item.source.computer != context.computer:
            return None, context.diagnostic(
                registration.primary_source_key,
                DiagnosticSeverity.ERROR,
                f"Item {key} was withheld because it was collected from another computer",
                identity=item.identity,
            )
        return screen_item(item, computer=context.computer, occurred_at=context.collected_at)


def _invoke(
    registration: AdapterRegistration, context: CollectionContext
) -> tuple[AdapterResult, InventoryDiagnostic | None]:
    """Call one adapter defensively: any failure becomes data, never an abort."""
    try:
        result = registration.collect(context)
    except Exception as error:  # noqa: BLE001 — a broken source must not stop the rest
        return AdapterResult.empty(), _failure_diagnostic(registration, context, error)
    if not isinstance(result, AdapterResult):
        return AdapterResult.empty(), context.diagnostic(
            registration.primary_source_key,
            DiagnosticSeverity.ERROR,
            f"The {registration.name} inventory source returned "
            f"{type(result).__name__} instead of an inventory result",
        )
    return result, None


def _failure_diagnostic(
    registration: AdapterRegistration, context: CollectionContext, error: BaseException
) -> InventoryDiagnostic:
    return context.diagnostic(
        registration.primary_source_key,
        DiagnosticSeverity.ERROR,
        f"The {registration.name} inventory source failed and was skipped "
        f"({type(error).__name__}) {error}",
    )


__all__ = [
    "AdapterOutcome",
    "AdapterRegistration",
    "AdapterRegistry",
    "AdapterResult",
    "CallableAdapter",
    "CollectionContext",
    "CollectionResult",
    "InventoryCollector",
    "SetupAdapter",
    "merge_availability",
    "merge_classifications",
    "merge_items",
    "merge_measurements",
    "merge_prerequisites",
]
