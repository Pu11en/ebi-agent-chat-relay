"""The queries My AI Setup shows: pure functions over snapshots.

Browse by kind, Where it lives, Compare computers, search and filters, Recent
changes, the explicit built-in filter, paging, and the facts an item's detail
view lists.  Everything here is deterministic metadata work — no model, no
Discord, no filesystem — so the Discord views (``discord_ui/my_ai_setup.py``)
only render what these return, and a Teams or CLI front end could render the
same.

Three honesty rules are enforced in the results rather than in the renderer:

* The default view is custom additions and overrides; built-ins appear only
  with :attr:`InventoryFilter.include_builtins` and the counts stay split.
* An unmeasured size or token count is *unknown*; an undated item is listed
  apart in Recent changes rather than ranked by a guessed time; a file time
  is always called *source modification time*, and what the repository saw
  change is *observed*.
* Scope groups and detail facts use :class:`EffectiveScope`'s labels only.
  The internal ownership class is never a group, a label or a fact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_inventory import (
    DEFAULT_SNAPSHOT_TTL,
    AvailabilityState,
    Classification,
    EffectiveScope,
    InventoryItem,
    InventorySnapshot,
    ScopeKind,
    SetupKind,
    normalize_token,
)
from claude_discord.ai_setup_remote import ComputerComparison, compare_snapshots
from claude_discord.database.ai_setup_repo import ObservedChange

DEFAULT_PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# Filters and search
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InventoryFilter:
    """What the user asked to see; every field narrows, none invents."""

    query: str = ""
    kinds: frozenset[SetupKind] = field(default_factory=frozenset)
    scopes: frozenset[ScopeKind] = field(default_factory=frozenset)
    states: frozenset[AvailabilityState] = field(default_factory=frozenset)
    harness: str | None = None
    computer: str | None = None
    include_builtins: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", " ".join(self.query.split()))
        object.__setattr__(self, "kinds", frozenset(SetupKind(kind) for kind in self.kinds))
        object.__setattr__(self, "scopes", frozenset(ScopeKind(scope) for scope in self.scopes))
        object.__setattr__(
            self, "states", frozenset(AvailabilityState(state) for state in self.states)
        )
        if self.harness is not None:
            object.__setattr__(self, "harness", normalize_token(self.harness, kind="harness"))
        if self.computer is not None:
            object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))

    @property
    def is_default(self) -> bool:
        return self == InventoryFilter()

    def matches(self, item: InventoryItem) -> bool:
        if not self.include_builtins and item.is_builtin:
            return False
        if self.kinds and item.kind not in self.kinds:
            return False
        if self.scopes and item.scope.kind not in self.scopes:
            return False
        if self.computer is not None and item.computer != self.computer:
            return False
        if self.harness is not None and self.harness not in _harnesses_of(item):
            return False
        if self.states and not any(entry.state in self.states for entry in item.availability):
            return False
        return not self.query or _query_matches(self.query, item)

    def apply(self, items: Iterable[InventoryItem]) -> tuple[InventoryItem, ...]:
        return tuple(item for item in items if self.matches(item))

    def describe(self) -> str:
        """One line for the view header."""
        parts: list[str] = []
        if self.query:
            parts.append(f"matching “{self.query}”")
        if self.kinds:
            parts.append(", ".join(kind.label for kind in sorted(self.kinds, key=_kind_order)))
        if self.scopes:
            parts.append(", ".join(scope.value.replace("_", " ") for scope in sorted(self.scopes)))
        if self.harness:
            parts.append(f"on {self.harness}")
        if self.computer:
            parts.append(f"from {self.computer}")
        if self.states:
            parts.append(", ".join(state.label.lower() for state in sorted(self.states)))
        base = "custom additions and overrides"
        if self.include_builtins:
            base += " plus built-ins"
        return f"{base}; {'; '.join(parts)}" if parts else base


def _harnesses_of(item: InventoryItem) -> frozenset[str]:
    names = {entry.harness for entry in item.availability}
    if item.source.harness:
        names.add(item.source.harness)
    return frozenset(names)


def _searchable_text(item: InventoryItem) -> str:
    pieces = [
        item.display_name,
        item.identity.name,
        item.kind.value,
        item.kind.label,
        item.scope.label,
        item.scope.kind.value,
        item.source.label,
        item.source.locator,
        item.computer,
        item.classification.value,
        *(_harnesses_of(item)),
        *(entry.state.label for entry in item.availability),
        *(prerequisite.name for prerequisite in item.prerequisites),
    ]
    if item.summary:
        pieces.append(item.summary)
    if item.scope.project:
        pieces.append(item.scope.project)
    return " ".join(pieces).lower()


def _query_matches(query: str, item: InventoryItem) -> bool:
    haystack = _searchable_text(item)
    return all(word in haystack for word in query.lower().split())


def _kind_order(kind: SetupKind) -> int:
    return list(SetupKind).index(kind)


def _name_key(item: InventoryItem) -> tuple[str, str]:
    return (item.display_name.lower(), item.identity.key)


def search(
    snapshot: InventorySnapshot, inventory_filter: InventoryFilter
) -> tuple[InventoryItem, ...]:
    """Every matching item, sorted by name — the flat list behind search results."""
    return tuple(sorted(inventory_filter.apply(snapshot.items), key=_name_key))


# ---------------------------------------------------------------------------
# Browse by kind
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KindGroup:
    kind: SetupKind
    items: tuple[InventoryItem, ...]

    @property
    def label(self) -> str:
        return self.kind.label

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def custom_count(self) -> int:
        return sum(1 for item in self.items if item.is_custom)

    @property
    def builtin_count(self) -> int:
        return sum(1 for item in self.items if item.is_builtin)

    @property
    def count_label(self) -> str:
        if self.builtin_count:
            return f"{self.custom_count} custom, {self.builtin_count} built-in"
        return str(self.custom_count)


def browse_by_kind(
    snapshot: InventorySnapshot, inventory_filter: InventoryFilter
) -> tuple[KindGroup, ...]:
    """Items grouped in kind order, each group sorted by name; empty kinds omitted."""
    matching = inventory_filter.apply(snapshot.items)
    groups: list[KindGroup] = []
    for kind in SetupKind:
        items = tuple(sorted((item for item in matching if item.kind is kind), key=_name_key))
        if items:
            groups.append(KindGroup(kind=kind, items=items))
    return tuple(groups)


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceGroup:
    key: str
    label: str
    items: tuple[InventoryItem, ...]

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True, slots=True)
class ScopeGroup:
    """One user-facing scope and the sources that feed it."""

    scope: EffectiveScope
    sources: tuple[SourceGroup, ...]

    @property
    def label(self) -> str:
        return self.scope.label

    @property
    def items(self) -> tuple[InventoryItem, ...]:
        return tuple(item for source in self.sources for item in source.items)

    @property
    def count(self) -> int:
        return len(self.items)


def _scope_order(scope: EffectiveScope) -> tuple[int, str]:
    return (list(ScopeKind).index(scope.kind), scope.label.lower())


def where_it_lives(
    snapshot: InventorySnapshot, inventory_filter: InventoryFilter
) -> tuple[ScopeGroup, ...]:
    """Items grouped by user-facing scope, then by source; never by ownership class."""
    matching = inventory_filter.apply(snapshot.items)
    by_scope: dict[EffectiveScope, dict[str, list[InventoryItem]]] = {}
    labels: dict[str, str] = {}
    for item in matching:
        sources = by_scope.setdefault(item.scope, {})
        sources.setdefault(item.source.key, []).append(item)
        labels.setdefault(item.source.key, item.source.label)
    groups: list[ScopeGroup] = []
    for scope in sorted(by_scope, key=_scope_order):
        sources = tuple(
            SourceGroup(key=key, label=labels[key], items=tuple(sorted(items, key=_name_key)))
            for key, items in sorted(by_scope[scope].items(), key=lambda pair: labels[pair[0]])
        )
        groups.append(ScopeGroup(scope=scope, sources=sources))
    return tuple(groups)


# ---------------------------------------------------------------------------
# Recent changes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecentChanges:
    """Dated items newest first, undated items apart, and what was observed."""

    dated: tuple[InventoryItem, ...]
    undated: tuple[InventoryItem, ...]
    observed: tuple[ObservedChange, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.dated or self.undated or self.observed)


def recent_changes(
    snapshot: InventorySnapshot,
    inventory_filter: InventoryFilter,
    *,
    observed: Iterable[ObservedChange] = (),
    limit: int | None = None,
) -> RecentChanges:
    matching = inventory_filter.apply(snapshot.items)
    dated = sorted(
        (item for item in matching if item.last_changed_at is not None),
        key=lambda item: (item.last_changed_at or snapshot.collected_at, item.identity.key),
        reverse=True,
    )
    undated = sorted((item for item in matching if item.last_changed_at is None), key=_name_key)
    seen = sorted(observed, key=lambda change: change.observed_at, reverse=True)
    if limit is not None:
        dated = dated[:limit]
        seen = seen[:limit]
    return RecentChanges(dated=tuple(dated), undated=tuple(undated), observed=tuple(seen))


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: tuple[T, ...]
    page: int
    page_size: int
    total: int

    @property
    def page_count(self) -> int:
        return max(1, -(-self.total // self.page_size))

    @property
    def has_next(self) -> bool:
        return self.page + 1 < self.page_count

    @property
    def has_previous(self) -> bool:
        return self.page > 0

    @property
    def start(self) -> int:
        return self.page * self.page_size

    @property
    def label(self) -> str:
        noun = "item" if self.total == 1 else "items"
        return f"Page {self.page + 1} of {self.page_count} ({self.total} {noun})"


def paginate[T](entries: Sequence[T], *, page: int, page_size: int = DEFAULT_PAGE_SIZE) -> Page[T]:
    """A deterministic slice; an out-of-range page is clamped, never emptied."""
    if page_size < 1:
        raise ValueError("A page must hold at least one item")
    total = len(entries)
    page_count = max(1, -(-total // page_size))
    page = min(max(page, 0), page_count - 1)
    start = page * page_size
    return Page(
        items=tuple(entries[start : start + page_size]), page=page, page_size=page_size, total=total
    )


# ---------------------------------------------------------------------------
# Compare computers
# ---------------------------------------------------------------------------


def compare_computers(
    local: InventorySnapshot,
    remotes: Mapping[str, InventorySnapshot | None],
    *,
    now: datetime,
    exceptions: Mapping[str, Iterable[DeliberateException]] | None = None,
    local_exceptions: Iterable[DeliberateException] = (),
    inventory_filter: InventoryFilter | None = None,
    max_age: timedelta = DEFAULT_SNAPSHOT_TTL,
) -> tuple[ComputerComparison, ...]:
    """One comparison per remote computer, in name order, filtered like the views."""
    inventory_filter = inventory_filter or InventoryFilter()
    declared = exceptions or {}
    local_declared = tuple(local_exceptions)
    comparisons: list[ComputerComparison] = []
    for name in sorted(remotes):
        computer = normalize_token(name, kind="computer")
        comparison = compare_snapshots(
            local,
            remotes[name],
            now=now,
            computer=computer,
            local_exceptions=local_declared,
            remote_exceptions=tuple(declared.get(name, ())),
            include_builtins=inventory_filter.include_builtins,
            max_age=max_age,
        )
        results = tuple(
            entry
            for entry in comparison.results
            if inventory_filter.matches(entry.local or entry.remote)  # type: ignore[arg-type]
        )
        comparisons.append(
            ComputerComparison(
                computer=comparison.computer,
                freshness=comparison.freshness,
                verified_at=comparison.verified_at,
                results=results,
                source_label=comparison.source_label,
            )
        )
    return tuple(comparisons)


# ---------------------------------------------------------------------------
# Item detail facts
# ---------------------------------------------------------------------------


def format_time(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _availability_line(item: InventoryItem) -> str:
    if not item.availability:
        return "unknown (no harness reported)"
    parts: list[str] = []
    for entry in item.availability:
        text = f"{entry.harness}: {entry.label}"
        if entry.is_verified and entry.evidence:
            text += f" — {entry.evidence} ({format_time(entry.verified_at)})"
        elif entry.detail:
            text += f" — {entry.detail}"
        parts.append(text)
    return "; ".join(parts)


def _prerequisite_line(item: InventoryItem) -> str:
    if not item.prerequisites:
        return "none recorded"
    return "; ".join(
        f"{entry.name}: {entry.state.value} ({entry.kind.value})" for entry in item.prerequisites
    )


def detail_facts(item: InventoryItem) -> tuple[tuple[str, str], ...]:
    """The (label, value) pairs a detail view lists — every unknown says so."""
    last_change = (
        f"{format_time(item.last_changed_at)} (source modification time)"
        if item.last_changed_at is not None
        else "unknown (no source modification time)"
    )
    classification = {
        Classification.CUSTOM: "Custom",
        Classification.OVERRIDDEN_BUILTIN: "Built-in, overridden",
        Classification.BUILTIN: "Built-in (unchanged)",
    }[item.classification]
    facts: list[tuple[str, str]] = [
        ("Kind", item.kind.label),
        ("Classification", classification),
        ("Source", item.source.describe()),
        ("Scope", item.scope.label),
        ("Computer", item.computer),
        ("Availability", _availability_line(item)),
        ("Prerequisites", _prerequisite_line(item)),
        ("Size", item.measurement.size_label),
        ("Tokens", item.measurement.token_label),
        ("Last change", last_change),
        ("Content", str(item.fingerprint) if item.fingerprint else "unknown (no fingerprint)"),
    ]
    if item.summary:
        facts.insert(1, ("Summary", item.summary))
    return tuple(facts)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "InventoryFilter",
    "KindGroup",
    "Page",
    "RecentChanges",
    "ScopeGroup",
    "SourceGroup",
    "browse_by_kind",
    "compare_computers",
    "detail_facts",
    "format_time",
    "paginate",
    "recent_changes",
    "search",
    "where_it_lives",
]
