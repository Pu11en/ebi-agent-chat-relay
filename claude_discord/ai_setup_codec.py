"""JSON shape for inventory snapshots — the same one on disk and on the wire.

The repository (``database/ai_setup_repo.py``) stores items as this JSON and
the remote inventory reply (``ai_setup_remote.py``) carries it between
computers, so there is exactly one place that knows how an
:class:`~claude_discord.ai_setup_inventory.InventoryItem` is spelled.

Decoding is as strict as constructing: every value goes back through the
dataclass validators (one bounded line per text field, timezone-aware times,
a hex digest for a fingerprint) and every decoded item through
:func:`~claude_discord.ai_setup_redaction.ensure_safe_item`, so a snapshot
written by another computer — or tampered with on the way — cannot smuggle a
credential into this one's database or Discord view.  Unknown keys are
ignored, which is what lets a newer sender talk to an older receiver.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    ContentFingerprint,
    DiagnosticSeverity,
    EffectiveScope,
    Freshness,
    HarnessAvailability,
    InventoryDiagnostic,
    InventoryItem,
    InventorySnapshot,
    InventorySource,
    ItemIdentity,
    Measurement,
    MeasurementMethod,
    OwnershipClass,
    Prerequisite,
    PrerequisiteKind,
    PrerequisiteState,
    ScopeKind,
)
from claude_discord.ai_setup_redaction import RedactionError, ensure_safe_item

#: Bumped when a decoder could no longer read what an older encoder wrote.
CODEC_VERSION = 1


class CodecError(ValueError):
    """The data is not an inventory snapshot in the shape this module writes."""


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def source_to_dict(source: InventorySource) -> dict[str, Any]:
    return {
        "key": source.key,
        "computer": source.computer,
        "label": source.label,
        "locator": source.locator,
        "harness": source.harness,
        "modified_at": _time(source.modified_at),
    }


def scope_to_dict(scope: EffectiveScope) -> dict[str, Any]:
    return {
        "kind": scope.kind.value,
        "owner": scope.owner,
        "computer": scope.computer,
        "project": scope.project,
        "display_name": scope.display_name,
    }


def availability_to_dict(entry: HarnessAvailability) -> dict[str, Any]:
    return {
        "harness": entry.harness,
        "state": entry.state.value,
        "computer": entry.computer,
        "evidence": entry.evidence,
        "verified_at": _time(entry.verified_at),
        "detail": entry.detail,
    }


def prerequisite_to_dict(entry: Prerequisite) -> dict[str, Any]:
    return {
        "name": entry.name,
        "state": entry.state.value,
        "kind": entry.kind.value,
        "detail": entry.detail,
    }


def measurement_to_dict(measurement: Measurement) -> dict[str, Any]:
    return {
        "byte_size": measurement.byte_size,
        "character_count": measurement.character_count,
        "token_count": measurement.token_count,
        "token_method": measurement.token_method.value,
        "tokenizer": measurement.tokenizer,
        "model": measurement.model,
    }


def fingerprint_to_dict(fingerprint: ContentFingerprint | None) -> dict[str, str] | None:
    if fingerprint is None:
        return None
    return {"algorithm": fingerprint.algorithm, "digest": fingerprint.digest}


def item_to_dict(item: InventoryItem) -> dict[str, Any]:
    return {
        "identity": item.identity.key,
        "display_name": item.display_name,
        "source": source_to_dict(item.source),
        "scope": scope_to_dict(item.scope),
        "classification": item.classification.value,
        "ownership": item.ownership.value if item.ownership is not None else None,
        "availability": [availability_to_dict(entry) for entry in item.availability],
        "prerequisites": [prerequisite_to_dict(entry) for entry in item.prerequisites],
        "measurement": measurement_to_dict(item.measurement),
        "fingerprint": fingerprint_to_dict(item.fingerprint),
        "last_changed_at": _time(item.last_changed_at),
        "summary": item.summary,
    }


def diagnostic_to_dict(diagnostic: InventoryDiagnostic) -> dict[str, Any]:
    return {
        "source_key": diagnostic.source_key,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
        "computer": diagnostic.computer,
        "identity": diagnostic.identity.key if diagnostic.identity is not None else None,
        "occurred_at": _time(diagnostic.occurred_at),
    }


def snapshot_to_dict(snapshot: InventorySnapshot) -> dict[str, Any]:
    return {
        "version": CODEC_VERSION,
        "computer": snapshot.computer,
        "owner": snapshot.owner,
        "collected_at": _time(snapshot.collected_at),
        "verified_at": _time(snapshot.verified_at),
        "freshness": snapshot.freshness.value,
        "source_label": snapshot.source_label,
        "items": [item_to_dict(item) for item in snapshot.items],
        "diagnostics": [diagnostic_to_dict(entry) for entry in snapshot.diagnostics],
    }


def snapshot_to_json(snapshot: InventorySnapshot) -> str:
    return json.dumps(snapshot_to_dict(snapshot), separators=(",", ":"), sort_keys=True)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CodecError(f"An inventory {what} must be an object, not {type(value).__name__}")
    return value  # pyright: ignore[reportUnknownVariableType]


def _text(data: Mapping[str, Any], key: str, *, required: bool = False) -> str | None:
    value = data.get(key)
    if value is None:
        if required:
            raise CodecError(f"An inventory record is missing {key!r}")
        return None
    if not isinstance(value, str):
        raise CodecError(f"Inventory field {key!r} must be text")
    return value


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = _text(data, key, required=True)
    assert value is not None
    return value


def _integer(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodecError(f"Inventory field {key!r} must be an integer")
    return value


def _when(data: Mapping[str, Any], key: str) -> datetime | None:
    text = _text(data, key)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise CodecError(f"Inventory field {key!r} is not an ISO 8601 time") from error


def _list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise CodecError(f"Inventory field {key!r} must be a list")
    return value  # pyright: ignore[reportUnknownVariableType]


def _build[T](factory: Callable[..., T], **fields: Any) -> T:
    """Construct a domain value, turning its validation error into a codec error."""
    try:
        return factory(**fields)
    except RedactionError:
        raise
    except (ValueError, TypeError) as error:
        name = getattr(factory, "__name__", "value")
        raise CodecError(f"Invalid inventory {name}: {error}") from error


def source_from_dict(data: object) -> InventorySource:
    record = _mapping(data, "source")
    return _build(
        InventorySource,
        key=_required_text(record, "key"),
        computer=_required_text(record, "computer"),
        label=_required_text(record, "label"),
        locator=_required_text(record, "locator"),
        harness=_text(record, "harness"),
        modified_at=_when(record, "modified_at"),
    )


def scope_from_dict(data: object) -> EffectiveScope:
    record = _mapping(data, "scope")
    return _build(
        EffectiveScope,
        kind=_build(ScopeKind, value=_required_text(record, "kind")),
        owner=_text(record, "owner"),
        computer=_text(record, "computer"),
        project=_text(record, "project"),
        display_name=_text(record, "display_name"),
    )


def availability_from_dict(data: object) -> HarnessAvailability:
    record = _mapping(data, "availability")
    return _build(
        HarnessAvailability,
        harness=_required_text(record, "harness"),
        state=_build(AvailabilityState, value=_required_text(record, "state")),
        computer=_text(record, "computer"),
        evidence=_text(record, "evidence"),
        verified_at=_when(record, "verified_at"),
        detail=_text(record, "detail"),
    )


def prerequisite_from_dict(data: object) -> Prerequisite:
    record = _mapping(data, "prerequisite")
    return _build(
        Prerequisite,
        name=_required_text(record, "name"),
        state=_build(PrerequisiteState, value=_required_text(record, "state")),
        kind=_build(PrerequisiteKind, value=_required_text(record, "kind")),
        detail=_text(record, "detail"),
    )


def measurement_from_dict(data: object) -> Measurement:
    if data is None:
        return Measurement.unknown()
    record = _mapping(data, "measurement")
    method = _text(record, "token_method") or MeasurementMethod.UNKNOWN.value
    return _build(
        Measurement,
        byte_size=_integer(record, "byte_size"),
        character_count=_integer(record, "character_count"),
        token_count=_integer(record, "token_count"),
        token_method=_build(MeasurementMethod, value=method),
        tokenizer=_text(record, "tokenizer"),
        model=_text(record, "model"),
    )


def fingerprint_from_dict(data: object) -> ContentFingerprint | None:
    if data is None:
        return None
    record = _mapping(data, "fingerprint")
    return _build(
        ContentFingerprint,
        digest=_required_text(record, "digest"),
        algorithm=_text(record, "algorithm") or "sha256",
    )


def item_from_dict(data: object) -> InventoryItem:
    """Rebuild one item and refuse it unless the redactor would publish it."""
    record = _mapping(data, "item")
    identity = _build(ItemIdentity.from_key, key=_required_text(record, "identity"))
    ownership_name = _text(record, "ownership")
    item = _build(
        InventoryItem,
        identity=identity,
        display_name=_required_text(record, "display_name"),
        source=source_from_dict(record.get("source")),
        scope=scope_from_dict(record.get("scope")),
        classification=_build(Classification, value=_required_text(record, "classification")),
        ownership=_build(OwnershipClass, value=ownership_name) if ownership_name else None,
        availability=tuple(availability_from_dict(e) for e in _list(record, "availability")),
        prerequisites=tuple(prerequisite_from_dict(e) for e in _list(record, "prerequisites")),
        measurement=measurement_from_dict(record.get("measurement")),
        fingerprint=fingerprint_from_dict(record.get("fingerprint")),
        last_changed_at=_when(record, "last_changed_at"),
        summary=_text(record, "summary"),
    )
    return ensure_safe_item(item)


def diagnostic_from_dict(data: object) -> InventoryDiagnostic:
    record = _mapping(data, "diagnostic")
    identity_key = _text(record, "identity")
    return _build(
        InventoryDiagnostic,
        source_key=_required_text(record, "source_key"),
        severity=_build(DiagnosticSeverity, value=_required_text(record, "severity")),
        message=_required_text(record, "message"),
        computer=_text(record, "computer"),
        identity=_build(ItemIdentity.from_key, key=identity_key) if identity_key else None,
        occurred_at=_when(record, "occurred_at"),
    )


def snapshot_from_dict(data: object) -> InventorySnapshot:
    record = _mapping(data, "snapshot")
    version = record.get("version", CODEC_VERSION)
    if not isinstance(version, int) or version > CODEC_VERSION:
        raise CodecError(f"Inventory snapshot version {version!r} is newer than this codec")
    collected_at = _when(record, "collected_at")
    if collected_at is None:
        raise CodecError("An inventory snapshot is missing 'collected_at'")
    freshness = _text(record, "freshness") or Freshness.LIVE.value
    return _build(
        InventorySnapshot,
        computer=_required_text(record, "computer"),
        owner=_required_text(record, "owner"),
        collected_at=collected_at,
        items=tuple(item_from_dict(entry) for entry in _list(record, "items")),
        diagnostics=tuple(diagnostic_from_dict(e) for e in _list(record, "diagnostics")),
        freshness=_build(Freshness, value=freshness),
        verified_at=_when(record, "verified_at"),
        source_label=_text(record, "source_label"),
    )


def snapshot_from_json(text: str | bytes) -> InventorySnapshot:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise CodecError("Inventory snapshot text is not valid JSON") from error
    return snapshot_from_dict(data)


__all__ = [
    "CODEC_VERSION",
    "CodecError",
    "availability_from_dict",
    "availability_to_dict",
    "diagnostic_from_dict",
    "diagnostic_to_dict",
    "fingerprint_from_dict",
    "fingerprint_to_dict",
    "item_from_dict",
    "item_to_dict",
    "measurement_from_dict",
    "measurement_to_dict",
    "prerequisite_from_dict",
    "prerequisite_to_dict",
    "scope_from_dict",
    "scope_to_dict",
    "snapshot_from_dict",
    "snapshot_from_json",
    "snapshot_to_dict",
    "snapshot_to_json",
    "source_from_dict",
    "source_to_dict",
]
