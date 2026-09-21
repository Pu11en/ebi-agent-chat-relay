"""Remote inventory: the request/reply packet, trusted ingestion, and comparison.

*Compare computers* needs the iMac's inventory on DrewAI, and the design rules
out reading the iMac's files from here — its paths and permissions belong to
it.  So the iMac collects its own snapshot and sends the safe result.  This
module owns what crosses between them and what is believed on arrival; the
trusted-handoff build owns the transport.

* :class:`InventoryRequest` is small enough to ride in a handoff task's goal
  (``ccdb:inventory-request v1 {…}``), so a request is an ordinary trusted
  task with read-only authority.
* :class:`InventoryRequestOperation` is the handoff executor's
  ``DeterministicOperation`` for it: no model, one local collection, the
  reply as text.  It answers only requests addressed to *this* computer.
* :class:`InventoryReply` is the snapshot plus the computer's declared
  exceptions, in the codec's JSON, bounded by :data:`MAX_REPLY_BYTES` and
  refused rather than split when it does not fit — a reply that arrives in
  pieces cannot be verified as one.
* :func:`ingest_reply` believes a reply only from a computer on the trusted
  list, only for the request that was sent, only when its collection time is
  not in the future, and never over a newer stored snapshot.
* Freshness follows the verification time.  :func:`remote_freshness` says
  *live* inside the window and *stale* after it; :func:`unreachable_snapshot`
  keeps the last snapshot as stale when a computer stops answering and
  produces an empty *unreachable* one when nothing was ever verified.
* :func:`compare_snapshots` distinguishes matching content, differing
  content, missing on either side, a declared deliberate difference, and an
  unreachable computer; a stale remote is compared but every result says so.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_codec import CodecError, snapshot_from_dict, snapshot_to_dict
from claude_discord.ai_setup_collector import CollectionContext, InventoryCollector
from claude_discord.ai_setup_inventory import (
    DEFAULT_SNAPSHOT_TTL,
    AvailabilityState,
    Freshness,
    InventoryItem,
    InventorySnapshot,
    ItemIdentity,
    PrerequisiteKind,
    SetupKind,
    normalize_token,
)
from claude_discord.ai_setup_redaction import RedactionError
from claude_discord.database.ai_setup_repo import AISetupRepository

#: The largest reply this side will encode or accept.
MAX_REPLY_BYTES = 512 * 1024
#: How long a request stays answerable.
DEFAULT_REQUEST_TTL = timedelta(hours=1)
#: A remote clock may run a little ahead; further ahead than this is refused.
MAX_CLOCK_SKEW = timedelta(minutes=5)
REQUEST_MARKER = "ccdb:inventory-request"
REQUEST_VERSION = 1
REPLY_VERSION = 1

_REQUEST_RE = re.compile(
    rf"^\s*{re.escape(REQUEST_MARKER)}\s+v(?P<version>\d+)\s+(?P<body>\{{.*\}})\s*$", re.DOTALL
)


class InventoryPacketError(ValueError):
    """A request or reply is not a packet this module wrote, or does not fit."""


def _require_aware(value: datetime, what: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"An inventory {what} must carry a time zone")
    return value


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InventoryRequest:
    """Ask one trusted computer for its safe inventory."""

    request_id: str
    requester: str
    computer: str
    requested_at: datetime
    kinds: tuple[SetupKind, ...] = ()
    include_builtins: bool = False
    ttl: timedelta = DEFAULT_REQUEST_TTL

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "request_id", str(uuid.UUID(str(self.request_id))))
        except ValueError as error:
            raise ValueError("An inventory request id must be a UUID") from error
        object.__setattr__(self, "requester", normalize_token(self.requester, kind="computer"))
        object.__setattr__(self, "computer", normalize_token(self.computer, kind="computer"))
        object.__setattr__(self, "requested_at", _require_aware(self.requested_at, "request time"))
        object.__setattr__(self, "kinds", tuple(SetupKind(kind) for kind in self.kinds))
        if self.ttl <= timedelta(0):
            raise ValueError("An inventory request must stay answerable for some time")

    @classmethod
    def create(
        cls,
        *,
        requester: str,
        computer: str,
        now: datetime,
        kinds: Iterable[SetupKind] = (),
        include_builtins: bool = False,
    ) -> InventoryRequest:
        return cls(
            request_id=str(uuid.uuid4()),
            requester=requester,
            computer=computer,
            requested_at=now,
            kinds=tuple(kinds),
            include_builtins=include_builtins,
        )

    @property
    def expires_at(self) -> datetime:
        return self.requested_at + self.ttl

    def is_expired(self, now: datetime) -> bool:
        return now > self.expires_at

    def to_goal(self) -> str:
        """The handoff task goal that carries this request (well under 1,500 chars)."""
        body = {
            "id": self.request_id,
            "from": self.requester,
            "to": self.computer,
            "at": self.requested_at.isoformat(),
            "ttl": int(self.ttl.total_seconds()),
            "kinds": [kind.value for kind in self.kinds],
            "builtins": self.include_builtins,
        }
        return f"{REQUEST_MARKER} v{REQUEST_VERSION} {json.dumps(body, separators=(',', ':'))}"

    @classmethod
    def from_goal(cls, goal: str) -> InventoryRequest | None:
        """Parse a goal; ``None`` for an ordinary goal, an error for a broken packet."""
        if REQUEST_MARKER not in goal:
            return None
        match = _REQUEST_RE.match(goal)
        if match is None:
            raise InventoryPacketError("The inventory request marker is present but malformed")
        if int(match.group("version")) > REQUEST_VERSION:
            raise InventoryPacketError("The inventory request is newer than this reader")
        try:
            body = json.loads(match.group("body"))
        except ValueError as error:
            raise InventoryPacketError("The inventory request body is not JSON") from error
        if not isinstance(body, dict):
            raise InventoryPacketError("The inventory request body must be an object")
        try:
            return cls(
                request_id=str(body.get("id", "")),
                requester=str(body.get("from", "")),
                computer=str(body.get("to", "")),
                requested_at=datetime.fromisoformat(str(body.get("at", ""))),
                kinds=tuple(SetupKind(kind) for kind in body.get("kinds", [])),
                include_builtins=bool(body.get("builtins", False)),
                ttl=timedelta(seconds=int(body.get("ttl", DEFAULT_REQUEST_TTL.total_seconds()))),
            )
        except (ValueError, TypeError) as error:
            raise InventoryPacketError(f"The inventory request is invalid: {error}") from error


# ---------------------------------------------------------------------------
# Reply
# ---------------------------------------------------------------------------


def _exception_to_dict(entry: DeliberateException) -> dict[str, str]:
    return {"item": entry.item, "reason": entry.reason, "kind": entry.kind.value}


def _exception_from_dict(data: object) -> DeliberateException:
    if not isinstance(data, Mapping):
        raise InventoryPacketError("A declared exception must be an object")
    try:
        return DeliberateException(
            item=str(data.get("item", "")),
            reason=str(data.get("reason", "")),
            kind=PrerequisiteKind(str(data.get("kind", PrerequisiteKind.OTHER.value))),
        )
    except ValueError as error:
        raise InventoryPacketError(f"A declared exception is invalid: {error}") from error


@dataclass(frozen=True, slots=True)
class InventoryReply:
    """One computer's answer: its snapshot and its declared exceptions."""

    request_id: str
    snapshot: InventorySnapshot
    exceptions: tuple[DeliberateException, ...] = ()

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "request_id", str(uuid.UUID(str(self.request_id))))
        except ValueError as error:
            raise ValueError("An inventory reply must name the request it answers") from error
        object.__setattr__(self, "exceptions", tuple(self.exceptions))

    @property
    def computer(self) -> str:
        return self.snapshot.computer

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": REPLY_VERSION,
            "request_id": self.request_id,
            "snapshot": snapshot_to_dict(self.snapshot),
            "exceptions": [_exception_to_dict(entry) for entry in self.exceptions],
        }

    def encode(self, *, max_bytes: int = MAX_REPLY_BYTES) -> str:
        """The reply as compact JSON; refused, not split, when it does not fit."""
        text = json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)
        size = len(text.encode("utf-8"))
        if size > max_bytes:
            raise InventoryPacketError(
                f"The inventory reply is {size:,} bytes, over the {max_bytes:,}-byte bound"
            )
        return text

    @classmethod
    def decode(cls, text: str | bytes, *, max_bytes: int = MAX_REPLY_BYTES) -> InventoryReply:
        """Rebuild a reply, refusing a bad shape, an oversize packet or a secret."""
        raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if len(raw) > max_bytes:
            raise InventoryPacketError("The inventory reply exceeds the accepted size")
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise InventoryPacketError("The inventory reply is not JSON") from error
        if not isinstance(data, Mapping):
            raise InventoryPacketError("The inventory reply must be an object")
        version = data.get("version", REPLY_VERSION)
        if not isinstance(version, int) or version > REPLY_VERSION:
            raise InventoryPacketError("The inventory reply is newer than this reader")
        exceptions_raw = data.get("exceptions", [])
        if not isinstance(exceptions_raw, list):
            raise InventoryPacketError("The inventory reply's exceptions must be a list")
        try:
            return cls(
                request_id=str(data.get("request_id", "")),
                snapshot=snapshot_from_dict(data.get("snapshot")),
                exceptions=tuple(_exception_from_dict(entry) for entry in exceptions_raw),
            )
        except RedactionError as error:
            raise InventoryPacketError(
                f"The inventory reply carried an unsafe item: {error}"
            ) from error
        except (CodecError, ValueError) as error:
            raise InventoryPacketError(f"The inventory reply is invalid: {error}") from error


# ---------------------------------------------------------------------------
# Answering a request on the computer it is addressed to
# ---------------------------------------------------------------------------


class InventoryRequestOperation:
    """The handoff executor's deterministic operation for inventory requests.

    ``matches`` is true only for a goal that parses as a request addressed to
    the computer ``context_factory`` speaks for; ``run`` collects locally and
    returns the encoded reply.  No model is involved at any point.
    """

    def __init__(
        self,
        *,
        collector: InventoryCollector,
        context_factory: Callable[[], CollectionContext],
        exceptions: Iterable[DeliberateException]
        | Callable[[], Iterable[DeliberateException]] = (),
    ) -> None:
        self._collector = collector
        self._context_factory = context_factory
        self._exceptions = exceptions

    def _request_for_me(self, goal: str) -> InventoryRequest | None:
        try:
            request = InventoryRequest.from_goal(goal)
        except InventoryPacketError:
            return None
        if request is None:
            return None
        return request if request.computer == self._context_factory().computer else None

    def matches(self, task: Any) -> bool:
        return self._request_for_me(str(getattr(task, "goal", ""))) is not None

    async def run(self, task: Any, project: Any) -> str:
        request = self._request_for_me(str(getattr(task, "goal", "")))
        if request is None:
            raise InventoryPacketError("This task is not an inventory request for this computer")
        context = self._context_factory()
        result = self._collector.collect(context)
        snapshot = result.snapshot
        if request.kinds or not request.include_builtins:
            wanted = frozenset(request.kinds)
            items = tuple(
                item
                for item in snapshot.visible_items(include_builtins=request.include_builtins)
                if not wanted or item.kind in wanted
            )
            snapshot = replace(snapshot, items=items)
        source = self._exceptions
        exceptions = tuple(source() if callable(source) else source)
        return InventoryReply(
            request_id=request.request_id, snapshot=snapshot, exceptions=exceptions
        ).encode()


# ---------------------------------------------------------------------------
# Trusted ingestion
# ---------------------------------------------------------------------------


class IngestStatus(StrEnum):
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"  # a newer snapshot for that computer was already held
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    status: IngestStatus
    computer: str | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status is IngestStatus.ACCEPTED


async def ingest_reply(
    text: str | bytes,
    *,
    request: InventoryRequest | None,
    trusted: Collection[str],
    repo: AISetupRepository,
    now: datetime,
) -> IngestOutcome:
    """Believe a reply only when its origin and its request check out, then store it."""
    try:
        reply = InventoryReply.decode(text)
    except InventoryPacketError as error:
        return IngestOutcome(IngestStatus.REJECTED, reason=str(error))
    computer = reply.computer
    trusted_set = {normalize_token(name, kind="computer") for name in trusted}
    if computer not in trusted_set:
        return IngestOutcome(
            IngestStatus.REJECTED, computer, f"{computer} is not a trusted computer"
        )
    if request is not None:
        if reply.request_id != request.request_id:
            return IngestOutcome(
                IngestStatus.REJECTED, computer, "The reply answers a different request"
            )
        if computer != request.computer:
            return IngestOutcome(
                IngestStatus.REJECTED,
                computer,
                f"The request was for {request.computer}, the reply is from {computer}",
            )
    if reply.snapshot.collected_at > now + MAX_CLOCK_SKEW:
        return IngestOutcome(
            IngestStatus.REJECTED, computer, "The reply's collection time is in the future"
        )
    stored = replace(
        reply.snapshot,
        freshness=Freshness.LIVE,
        verified_at=reply.snapshot.collected_at,
        source_label=f"trusted reply from {computer}",
    )
    if not await repo.save_snapshot(stored):
        return IngestOutcome(
            IngestStatus.SUPERSEDED, computer, "A newer snapshot for this computer is held"
        )
    await repo.save_exceptions(computer, reply.exceptions)
    return IngestOutcome(IngestStatus.ACCEPTED, computer, "stored")


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def remote_freshness(
    snapshot: InventorySnapshot | None,
    *,
    now: datetime,
    max_age: timedelta = DEFAULT_SNAPSHOT_TTL,
) -> Freshness:
    """What a stored remote snapshot may still be called at ``now``."""
    if snapshot is None:
        return Freshness.UNREACHABLE
    if snapshot.freshness is Freshness.UNREACHABLE:
        return Freshness.UNREACHABLE
    return Freshness.STALE if snapshot.is_stale_at(now, max_age=max_age) else Freshness.LIVE


def unreachable_snapshot(
    computer: str,
    *,
    previous: InventorySnapshot | None,
    now: datetime,
    owner: str = "unknown",
) -> InventorySnapshot:
    """A computer that did not answer: its last snapshot as stale, or nothing."""
    if previous is not None:
        return previous.with_freshness(Freshness.STALE)
    return InventorySnapshot(
        computer=computer,
        owner=owner,
        collected_at=now,
        freshness=Freshness.UNREACHABLE,
        source_label="no verified snapshot",
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


class ComparisonStatus(StrEnum):
    MATCHING = "matching"
    DIFFERENT = "different"
    MISSING_REMOTE = "missing_remote"
    MISSING_LOCAL = "missing_local"
    DELIBERATE = "deliberate"
    UNREACHABLE = "unreachable"

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS: Mapping[ComparisonStatus, str] = {
    ComparisonStatus.MATCHING: "Aligned",
    ComparisonStatus.DIFFERENT: "Different content",
    ComparisonStatus.MISSING_REMOTE: "Missing there",
    ComparisonStatus.MISSING_LOCAL: "Only there",
    ComparisonStatus.DELIBERATE: "Deliberate difference",
    ComparisonStatus.UNREACHABLE: "Unreachable",
}


@dataclass(frozen=True, slots=True)
class ItemComparison:
    identity: ItemIdentity
    status: ComparisonStatus
    local: InventoryItem | None = None
    remote: InventoryItem | None = None
    detail: str | None = None
    stale: bool = False

    @property
    def display_name(self) -> str:
        item = self.local or self.remote
        return item.display_name if item is not None else self.identity.name

    @property
    def label(self) -> str:
        text = self.status.label
        return f"{text} (stale)" if self.stale else text


@dataclass(frozen=True, slots=True)
class ComputerComparison:
    """One remote computer against the local snapshot."""

    computer: str
    freshness: Freshness
    verified_at: datetime | None
    results: tuple[ItemComparison, ...] = ()
    source_label: str | None = None

    @property
    def counts(self) -> Mapping[ComparisonStatus, int]:
        counts = {status: 0 for status in ComparisonStatus}
        for entry in self.results:
            counts[entry.status] += 1
        return counts

    @property
    def is_current(self) -> bool:
        return self.freshness is Freshness.LIVE

    def with_status(self, *statuses: ComparisonStatus) -> tuple[ItemComparison, ...]:
        wanted = frozenset(statuses)
        return tuple(entry for entry in self.results if entry.status in wanted)


def _deliberate_reason(
    key: str,
    item: InventoryItem | None,
    exceptions: Iterable[DeliberateException],
) -> str | None:
    for exception in exceptions:
        if exception.applies_to(key):
            return exception.reason
    if item is not None:
        for prerequisite in item.prerequisites:
            if prerequisite.detail and "deliberate" in prerequisite.detail.lower():
                return prerequisite.name
        for entry in item.availability:
            if (
                entry.state is AvailabilityState.MISSING_PREREQUISITE
                and entry.detail
                and "deliberate" in entry.detail.lower()
            ):
                return entry.detail
    return None


def compare_snapshots(
    local: InventorySnapshot,
    remote: InventorySnapshot | None,
    *,
    now: datetime,
    computer: str | None = None,
    local_exceptions: Iterable[DeliberateException] = (),
    remote_exceptions: Iterable[DeliberateException] = (),
    include_builtins: bool = False,
    max_age: timedelta = DEFAULT_SNAPSHOT_TTL,
) -> ComputerComparison:
    """Compare custom item identities and content between two computers."""
    local_exceptions = tuple(local_exceptions)
    remote_exceptions = tuple(remote_exceptions)
    freshness = remote_freshness(remote, now=now, max_age=max_age)
    local_items = {
        item.identity: item for item in local.visible_items(include_builtins=include_builtins)
    }
    if remote is None or freshness is Freshness.UNREACHABLE:
        name = computer or (remote.computer if remote is not None else "unknown")
        return ComputerComparison(
            computer=normalize_token(name, kind="computer"),
            freshness=Freshness.UNREACHABLE,
            verified_at=None,
            results=tuple(
                ItemComparison(
                    identity=identity,
                    status=ComparisonStatus.UNREACHABLE,
                    local=item,
                    detail=f"{name} has no verified snapshot",
                )
                for identity, item in local_items.items()
            ),
        )
    stale = freshness is Freshness.STALE
    remote_items = {
        item.identity: item for item in remote.visible_items(include_builtins=include_builtins)
    }
    results: list[ItemComparison] = []
    for identity in [*local_items, *(key for key in remote_items if key not in local_items)]:
        mine = local_items.get(identity)
        theirs = remote_items.get(identity)
        key = identity.key
        remote_reason = _deliberate_reason(key, theirs, remote_exceptions)
        local_reason = _deliberate_reason(key, mine, local_exceptions)
        if mine is not None and theirs is None:
            if remote_reason is not None:
                status, detail = ComparisonStatus.DELIBERATE, f"{remote.computer}: {remote_reason}"
            else:
                status, detail = ComparisonStatus.MISSING_REMOTE, f"Not on {remote.computer}"
        elif mine is None and theirs is not None:
            if local_reason is not None:
                status, detail = ComparisonStatus.DELIBERATE, f"{local.computer}: {local_reason}"
            else:
                status, detail = ComparisonStatus.MISSING_LOCAL, f"Only on {remote.computer}"
        else:
            assert mine is not None and theirs is not None
            if remote_reason is not None or local_reason is not None:
                reason = remote_reason or local_reason
                status, detail = ComparisonStatus.DELIBERATE, str(reason)
            elif mine.fingerprint is None or theirs.fingerprint is None:
                status = ComparisonStatus.DIFFERENT
                detail = "Content fingerprint unknown on one side; not counted as aligned"
            elif mine.is_aligned_with(theirs):
                status, detail = ComparisonStatus.MATCHING, "Same content fingerprint"
            else:
                status = ComparisonStatus.DIFFERENT
                detail = f"{mine.fingerprint} here vs {theirs.fingerprint} on {remote.computer}"
        results.append(
            ItemComparison(
                identity=identity,
                status=status,
                local=mine,
                remote=theirs,
                detail=detail,
                stale=stale,
            )
        )
    return ComputerComparison(
        computer=remote.computer,
        freshness=freshness,
        verified_at=remote.verified_at or remote.collected_at,
        results=tuple(results),
        source_label=remote.source_label,
    )


__all__ = [
    "DEFAULT_REQUEST_TTL",
    "MAX_CLOCK_SKEW",
    "MAX_REPLY_BYTES",
    "REQUEST_MARKER",
    "ComparisonStatus",
    "ComputerComparison",
    "IngestOutcome",
    "IngestStatus",
    "InventoryPacketError",
    "InventoryReply",
    "InventoryRequest",
    "InventoryRequestOperation",
    "ItemComparison",
    "compare_snapshots",
    "ingest_reply",
    "remote_freshness",
    "unreachable_snapshot",
]
