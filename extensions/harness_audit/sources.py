"""Pinned official Claude Code and Codex guidance, and a strict loader (task 1.3).

A finding that claims "this file is never loaded" or "the project copy wins"
is a claim about *vendor* behaviour, and the audit is not allowed to settle it
by asking a model or by fetching a page mid-run: results drift, offline audits
break, and nothing about the run stays reproducible.  Vendor behaviour
therefore lives in :data:`DEFAULT_MANIFEST_PATH` — reviewed data in git — and
this module is the gate in front of it.

The gate is closed by default.  A record is usable only when all of it holds:

* **Official.**  The URL is ``https``, carries no credentials or custom port,
  and its host (and, on ``github.com``, its repository owner) belongs to the
  publisher that actually ships the harness.  A blog, a lookalike host or a
  third-party fork is rejected, not downgraded to a weaker citation.
* **Pinned.**  Every entry carries a CLI version range plus a content hash or
  a proposition, so a later reader can tell *what* was checked and *against
  which generation of the CLI*.
* **In review.**  Retrieval cannot be in the future, the review date must come
  after it, and a record past its review date — or a manifest past its own —
  fails the whole load.  Stale vendor guidance is worse than none: it reads
  like evidence.
* **Recognized.**  Unknown keys, unknown harnesses, unknown publishers and
  unknown check names raise rather than being skipped, so a manifest written
  for a newer schema cannot half-load.

Anything short of that raises :class:`ManifestError`; there is no partial
manifest and no "best effort" mode.  Like the rest of the audit's contract
layer this module touches no network and no model, and it changes no harness
setting — it only reads one JSON file that ships beside it.

Provenance of the shipped manifest is recorded in its own ``notes`` field: the
entries were recorded offline from published vendor documentation, so their
propositions are the auditor's restatement of documented behaviour rather than
verbatim quotations, and ``content_hash`` stays empty until a reviewed refresh
fetches the page and fills it in.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Self

from extensions.harness_audit.models import (
    AuditCheck,
    Harness,
    SchemaError,
    VendorCitation,
)

MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = frozenset({1})

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "sources.json"

#: Checks whose verdict depends on documented vendor behaviour.  Every harness
#: must have at least one official source for each of them, or a rule in task
#: 3.1 would have to guess.
VENDOR_BACKED_CHECKS: tuple[AuditCheck, ...] = (
    AuditCheck.SCOPE,
    AuditCheck.LOAD_BEHAVIOR,
    AuditCheck.PRECEDENCE,
    AuditCheck.PERMISSIONS,
    AuditCheck.DEAD_CONFIGURATION,
)

_MANIFEST_KEYS = (
    "schema_version",
    "manifest_version",
    "reviewed_on",
    "review_due_on",
    "notes",
    "sources",
)
_SOURCE_KEYS = (
    "source_id",
    "harness",
    "publisher",
    "title",
    "url",
    "retrieved_on",
    "review_due_on",
    "min_cli_version",
    "max_cli_version",
    "content_hash",
    "proposition",
    "checks",
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^\d{1,6}(\.\d{1,6}){0,3}$")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class ManifestError(SchemaError):
    """The source manifest is malformed, expired, or not official."""


class Publisher(StrEnum):
    """The vendor that actually ships a harness."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"

    @classmethod
    def for_harness(cls, harness: Harness) -> Publisher:
        return _HARNESS_PUBLISHER[harness]


_HARNESS_PUBLISHER: Mapping[Harness, Publisher] = {
    Harness.CLAUDE: Publisher.ANTHROPIC,
    Harness.CODEX: Publisher.OPENAI,
}

#: ``(host, required path prefix)`` pairs each publisher is allowed to speak
#: from.  ``github.com`` is shared, so the owner segment is part of the pair —
#: ``github.com/someone/claude-code-notes`` is not Anthropic.
_OFFICIAL_ORIGINS: Mapping[Publisher, tuple[tuple[str, str], ...]] = {
    Publisher.ANTHROPIC: (
        ("docs.claude.com", "/"),
        ("docs.anthropic.com", "/"),
        ("code.claude.com", "/"),
        ("www.anthropic.com", "/"),
        ("anthropic.com", "/"),
        ("github.com", "/anthropics/"),
    ),
    Publisher.OPENAI: (
        ("developers.openai.com", "/"),
        ("platform.openai.com", "/"),
        ("help.openai.com", "/"),
        ("openai.com", "/"),
        ("github.com", "/openai/"),
    ),
}


# --------------------------------------------------------------------------- #
# Payload helpers — every failure names the file, the record and the field
# --------------------------------------------------------------------------- #


def _mapping(payload: object, where: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ManifestError(f"{where}: expected a JSON object, got {type(payload).__name__}")
    return payload


def _reject_unknown(payload: Mapping[str, object], allowed: Iterable[str], where: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise ManifestError(f"{where}: unknown field(s) {', '.join(unknown)}")


def _req_str(payload: Mapping[str, object], key: str, where: str) -> str:
    if key not in payload:
        raise ManifestError(f"{where}: missing required field {key}")
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: {key} must be a non-empty string")
    return value.strip()


def _opt_str(payload: Mapping[str, object], key: str, where: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ManifestError(f"{where}: {key} must be a string")
    return value.strip()


def _req_int(payload: Mapping[str, object], key: str, where: str) -> int:
    if key not in payload:
        raise ManifestError(f"{where}: missing required field {key}")
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{where}: {key} must be an integer")
    return value


def _req_date(payload: Mapping[str, object], key: str, where: str) -> date:
    text = _req_str(payload, key, where)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ManifestError(f"{where}: {key} must be an ISO date (YYYY-MM-DD)") from exc


def _version(text: str, field_name: str, where: str) -> tuple[int, ...]:
    if not _VERSION.match(text):
        raise ManifestError(f"{where}: {field_name} {text!r} is not a dotted numeric version")
    return tuple(int(part) for part in text.split("."))


def _padded(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    width = max(len(left), len(right))
    return tuple(value + (0,) * (width - len(value)) for value in (left, right))


def _official_url(url: str, publisher: Publisher, where: str) -> str:
    from urllib.parse import urlsplit  # stdlib parsing only; never a request

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise ManifestError(f"{where}: url {url!r} could not be parsed") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        raise ManifestError(f"{where}: url {url!r} must be an official https url")
    if parts.username or parts.password:
        raise ManifestError(f"{where}: url {url!r} must not carry credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise ManifestError(f"{where}: url {url!r} has an invalid port") from exc
    if port is not None:
        raise ManifestError(f"{where}: url {url!r} must not set a port")
    path = parts.path or "/"
    for allowed_host, prefix in _OFFICIAL_ORIGINS[publisher]:
        if host == allowed_host and path.startswith(prefix):
            return url
    raise ManifestError(f"{where}: url {url!r} is not official guidance published by {publisher}")


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OfficialSource:
    """One reviewed vendor document, pinned by date and CLI version range."""

    source_id: str
    harness: Harness
    publisher: Publisher
    title: str
    url: str
    retrieved_on: date
    review_due_on: date
    min_cli_version: str
    max_cli_version: str
    content_hash: str
    proposition: str
    checks: tuple[AuditCheck, ...]

    def is_expired(self, today: date) -> bool:
        """True once ``today`` is past the review date; the pin is then stale."""
        return today > self.review_due_on

    def covers_version(self, version: str) -> bool:
        """True when ``version`` falls in ``[min_cli_version, max_cli_version)``."""
        where = f"source {self.source_id!r}"
        candidate = _version(version, "version", where)
        low, high = _padded(candidate, _version(self.min_cli_version, "min_cli_version", where))
        if low < high:
            return False
        if not self.max_cli_version:
            return True
        low, high = _padded(candidate, _version(self.max_cli_version, "max_cli_version", where))
        return low < high

    def citation(self) -> VendorCitation:
        """The evidence-schema citation a finding records."""
        return VendorCitation(
            source_id=self.source_id,
            url=self.url,
            retrieved_on=self.retrieved_on,
            applies_to_versions=self.version_range,
            content_hash=self.content_hash,
            quoted_proposition=self.proposition,
        )

    @property
    def version_range(self) -> str:
        upper = self.max_cli_version or "*"
        return f">={self.min_cli_version},<{upper}"

    @classmethod
    def from_dict(cls, payload: object, *, origin: str) -> Self:
        where = origin
        data = _mapping(payload, where)
        source_id = _req_str(data, "source_id", where)
        where = f"{origin}: source {source_id!r}"
        _reject_unknown(data, _SOURCE_KEYS, where)
        if not _SOURCE_ID.match(source_id):
            raise ManifestError(f"{where}: source_id must be a lowercase slug")

        harness = _enum(Harness, _req_str(data, "harness", where), "harness", where)
        publisher = _enum(Publisher, _req_str(data, "publisher", where), "publisher", where)
        if publisher is not Publisher.for_harness(harness):
            raise ManifestError(
                f"{where}: publisher {publisher} does not ship the {harness} harness"
            )

        retrieved_on = _req_date(data, "retrieved_on", where)
        review_due_on = _req_date(data, "review_due_on", where)
        if review_due_on <= retrieved_on:
            raise ManifestError(f"{where}: review_due_on must be after retrieved_on")

        min_cli_version = _req_str(data, "min_cli_version", where)
        max_cli_version = _opt_str(data, "max_cli_version", where)
        low = _version(min_cli_version, "min_cli_version", where)
        if max_cli_version:
            high = _version(max_cli_version, "max_cli_version", where)
            padded_low, padded_high = _padded(low, high)
            if padded_low >= padded_high:
                raise ManifestError(f"{where}: max_cli_version must be above min_cli_version")

        content_hash = _opt_str(data, "content_hash", where).lower()
        if content_hash and not _SHA256_HEX.match(content_hash):
            raise ManifestError(f"{where}: content_hash must be a sha256 hex digest")
        proposition = _opt_str(data, "proposition", where)
        if not (content_hash or proposition):
            raise ManifestError(
                f"{where}: unpinned — a source needs a content_hash or a proposition"
            )

        checks = _checks(data.get("checks", ()), where)
        return cls(
            source_id=source_id,
            harness=harness,
            publisher=publisher,
            title=_req_str(data, "title", where),
            url=_official_url(_req_str(data, "url", where), publisher, where),
            retrieved_on=retrieved_on,
            review_due_on=review_due_on,
            min_cli_version=min_cli_version,
            max_cli_version=max_cli_version,
            content_hash=content_hash,
            proposition=proposition,
            checks=checks,
        )


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: str, key: str, where: str) -> EnumT:
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ManifestError(f"{where}: {key} {value!r} is not one of {allowed}") from exc


def _checks(value: object, where: str) -> tuple[AuditCheck, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ManifestError(f"{where}: checks must be a list of check names")
    parsed: list[AuditCheck] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ManifestError(f"{where}: each check must be a string")
        check = _enum(AuditCheck, entry, "check", where)
        if check not in parsed:
            parsed.append(check)
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """The whole reviewed manifest; construction implies it passed every gate."""

    schema_version: int
    manifest_version: int
    reviewed_on: date
    review_due_on: date
    notes: str
    sources: tuple[OfficialSource, ...]
    origin: str = ""

    def get(self, source_id: str) -> OfficialSource:
        """The named source, or :class:`ManifestError` — never ``None``."""
        for source in self.sources:
            if source.source_id == source_id:
                return source
        raise ManifestError(f"{self.origin or 'manifest'}: no official source {source_id!r}")

    def citation(self, source_id: str) -> VendorCitation:
        return self.get(source_id).citation()

    def for_check(self, harness: Harness, check: AuditCheck) -> tuple[OfficialSource, ...]:
        """Every source backing ``check`` on ``harness``, in manifest order."""
        return tuple(
            source
            for source in self.sources
            if source.harness is harness and check in source.checks
        )

    def require(self, harness: Harness, check: AuditCheck) -> OfficialSource:
        """The first backing source, or :class:`ManifestError` if a rule has none."""
        matches = self.for_check(harness, check)
        if not matches:
            raise ManifestError(
                f"{self.origin or 'manifest'}: no official {harness} source covers "
                f"the {check} check"
            )
        return matches[0]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def parse_manifest(
    payload: object,
    *,
    today: date | None = None,
    origin: str = "manifest",
) -> SourceManifest:
    """Validate an already-decoded manifest, failing closed on anything odd."""
    now = today or date.today()
    data = _mapping(payload, origin)
    _reject_unknown(data, _MANIFEST_KEYS, origin)

    schema_version = _req_int(data, "schema_version", origin)
    if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        raise ManifestError(
            f"{origin}: schema_version {schema_version} is not supported "
            f"(known: {sorted(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)})"
        )

    raw_sources = data.get("sources")
    if raw_sources is None:
        raise ManifestError(f"{origin}: missing required field sources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, str):
        raise ManifestError(f"{origin}: sources must be a list")
    if not raw_sources:
        raise ManifestError(f"{origin}: sources must not be empty")

    sources: list[OfficialSource] = []
    seen: set[str] = set()
    for entry in raw_sources:
        source = OfficialSource.from_dict(entry, origin=origin)
        if source.source_id in seen:
            raise ManifestError(f"{origin}: duplicate source_id {source.source_id!r}")
        seen.add(source.source_id)
        if source.retrieved_on > now:
            raise ManifestError(
                f"{origin}: source {source.source_id!r} claims a retrieval date in the future"
            )
        if source.is_expired(now):
            raise ManifestError(
                f"{origin}: source {source.source_id!r} is past its review date "
                f"({source.review_due_on.isoformat()}); refresh it before auditing"
            )
        sources.append(source)

    reviewed_on = _req_date(data, "reviewed_on", origin)
    review_due_on = _req_date(data, "review_due_on", origin)
    if review_due_on <= reviewed_on:
        raise ManifestError(f"{origin}: review_due_on must be after reviewed_on")
    if reviewed_on > now:
        raise ManifestError(f"{origin}: reviewed_on is in the future")
    if now > review_due_on:
        raise ManifestError(
            f"{origin}: the manifest is past its review date "
            f"({review_due_on.isoformat()}); refresh it before auditing"
        )

    return SourceManifest(
        schema_version=schema_version,
        manifest_version=_req_int(data, "manifest_version", origin),
        reviewed_on=reviewed_on,
        review_due_on=review_due_on,
        notes=_opt_str(data, "notes", origin),
        sources=tuple(sources),
        origin=origin,
    )


def load_manifest(path: Path | None = None, *, today: date | None = None) -> SourceManifest:
    """Read and validate the pinned manifest from disk.  No network, ever."""
    manifest_path = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    origin = manifest_path.name or str(manifest_path)
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ManifestError(f"{origin}: source manifest not found at {manifest_path}") from exc
    except OSError as exc:
        raise ManifestError(f"{origin}: source manifest could not be read: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{origin}: source manifest is not valid json: {exc}") from exc
    return parse_manifest(payload, today=today, origin=origin)
