"""The safety boundary between raw configuration and the My AI Setup inventory.

`ai_setup_inventory` guarantees *shape*: a label is one bounded line, an item
has no field for file content.  This module guarantees *value*: nothing that
looks like a token, a password, a private key, an environment value, a
connector credential or a raw config blob may become an inventory item, a
diagnostic, a Discord label or a Setup Agent packet.

It is deliberately the pessimistic half of the pair, because the cost of the
two mistakes is not symmetric — an over-redacted description is a cosmetic
annoyance, a leaked API key is not.  So it fails closed:

* **Whitelist in, everything else out.**  :func:`build_safe_metadata` keeps
  only the fields an adapter declared safe, and even a declared field is
  dropped when its *name* reads like a credential (``api_key``) or its *value*
  matches a secret shape.  There is no "redact and keep" path for structured
  metadata: an unsafe field is dropped and recorded as a finding, so the
  collector can report a source problem instead of publishing a guess.
* **Raw content may only become a digest.**  :func:`safe_fingerprint` is the
  one function here that accepts file content, and all it returns is a one-way
  :class:`~claude_discord.ai_setup_inventory.ContentFingerprint` used to tell
  two computers' copies apart.
* **Presence, never the value.**  :func:`environment_prerequisites` and
  :func:`connector_prerequisites` answer "is this credential configured?"
  without reading what it is.
* **Nothing unsafe reaches a snapshot.**  :func:`ensure_safe_item` re-checks a
  fully assembled item field by field and raises; :func:`screen_item` is the
  same check in the form task 1.3's collector needs — drop this item, keep
  collecting, record a safe diagnostic.

Free text that must survive (a log line, a locator) goes through
:func:`safe_message` or :func:`safe_locator` instead, which scrub the secret,
collapse the text to one bounded line and never raise on hostile input.

Nothing in this module touches the filesystem; the only ambient state it reads
is ``os.environ``, and only to ask whether a named variable is set.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit, urlunsplit

from claude_discord.ai_setup_inventory import (
    MAX_LABEL_LENGTH,
    MAX_LOCATOR_LENGTH,
    MAX_MESSAGE_LENGTH,
    ContentFingerprint,
    DiagnosticSeverity,
    InventoryDiagnostic,
    InventoryItem,
    ItemIdentity,
    Prerequisite,
    safe_text,
)

REDACTED = "[redacted]"
ELLIPSIS = "…"
MAX_FIELD_NAME_LENGTH = 60

SafeValue = str | bool | int | float


class RedactionError(ValueError):
    """Raised when something unsafe was asked to pass the boundary."""


class RedactionReason(StrEnum):
    """Why one field could not be published."""

    SECRET_NAME = "secret_name"  # noqa: S105 - redaction reason, not a credential
    SECRET_VALUE = "secret_value"  # noqa: S105 - redaction reason, not a credential
    PRIVATE_KEY = "private_key"
    ENVIRONMENT_VALUE = "environment_value"
    HIGH_ENTROPY = "high_entropy"
    NOT_ALLOWED = "not_allowed"
    UNSAFE_SHAPE = "unsafe_shape"
    TOO_LONG = "too_long"
    UNSUPPORTED_TYPE = "unsupported_type"

    @property
    def description(self) -> str:
        """Plain wording for a diagnostic; it never quotes the value."""
        return _REASON_DESCRIPTIONS[self]


_REASON_DESCRIPTIONS: Mapping[RedactionReason, str] = {
    RedactionReason.SECRET_NAME: "the field name identifies a credential",
    RedactionReason.SECRET_VALUE: "the value looks like a secret",
    RedactionReason.PRIVATE_KEY: "the value contains a private key block",
    RedactionReason.ENVIRONMENT_VALUE: "the value is an environment assignment",
    RedactionReason.HIGH_ENTROPY: "the value looks like a random credential",
    RedactionReason.NOT_ALLOWED: "the field is not on the adapter's safe list",
    RedactionReason.UNSAFE_SHAPE: "the value is not a single line of readable text",
    RedactionReason.TOO_LONG: "the value is too long to be a safe summary",
    RedactionReason.UNSUPPORTED_TYPE: "the value is raw configuration, not a safe fact",
}


# ---------------------------------------------------------------------------
# Field names
# ---------------------------------------------------------------------------

# Tokenizing rather than substring matching is what keeps `keyword` and
# `keybindings` out of the credential set while `api_key` stays in it.
_NAME_SPLIT = re.compile(
    r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
)

SECRET_NAME_TOKENS: frozenset[str] = frozenset(
    {
        "auth",
        "authorization",
        "bearer",
        "cert",
        "certificate",
        "cookie",
        "cookies",
        "cred",
        "credential",
        "credentials",
        "creds",
        "dsn",
        "env",
        "environ",
        "environment",
        "key",
        "keypair",
        "keys",
        "license",
        "mfa",
        "otp",
        "passphrase",
        "passwd",
        "password",
        "passwords",
        "pem",
        "pin",
        "private",
        "pwd",
        "salt",
        "secret",
        "secrets",
        "seed",
        "session",
        "sig",
        "signature",
        "token",
        "tokens",
        "webhook",
    }
)


def name_tokens(name: str) -> tuple[str, ...]:
    """Split a field name into lowercase words, camelCase and all."""
    return tuple(part.lower() for part in _NAME_SPLIT.split(name) if part)


def is_secret_name(name: str) -> bool:
    """True when a field's *name* alone disqualifies it from being published.

    Fail-closed on purpose: ``token_count`` is rejected here even though a
    count is harmless, because the inventory carries counts in a typed
    ``Measurement`` and free metadata has no business holding anything
    token-shaped.
    """
    return any(token in SECRET_NAME_TOKENS for token in name_tokens(name))


_ENVIRONMENT_NAME = re.compile(r"\A[A-Z][A-Z0-9_]{2,}\Z")


def _is_environment_name(name: str) -> bool:
    return bool(_ENVIRONMENT_NAME.match(name))


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

_PRIVATE_KEY = re.compile(
    r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?(?:-----END[ A-Z]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_CREDENTIAL_URL = re.compile(r"(?P<scheme>\b[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/:@]+:[^\s/@]*@")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-+/]{8,}={0,2}")
_PROVIDER_TOKEN = re.compile(
    r"""\b(?:
        sk-[A-Za-z0-9_\-]{12,}
      | rk-[A-Za-z0-9_\-]{12,}
      | gh[pousr]_[A-Za-z0-9]{16,}
      | github_pat_[A-Za-z0-9_]{20,}
      | xox[baprs]-[A-Za-z0-9\-]{10,}
      | (?:AKIA|ASIA)[0-9A-Z]{12,}
      | AIza[A-Za-z0-9_\-]{30,}
      | ya29\.[A-Za-z0-9_\-]{10,}
      | glpat-[A-Za-z0-9_\-]{16,}
      | npm_[A-Za-z0-9]{30,}
      | dckr_pat_[A-Za-z0-9_\-]{20,}
      | hf_[A-Za-z0-9]{30,}
      | shpat_[A-Za-z0-9]{28,}
      | SG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}
    )""",
    re.VERBOSE,
)
# A name may sit inside quotes — ``"api_key": "…"`` is how JSON spells an
# assignment — and the quote is kept out of the name so the credential test
# sees ``api_key``, not ``"api_key"``.
_ASSIGNMENT = re.compile(
    r"""(?P<open>["']?)(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)(?P<close>["']?)(?P<gap>\s*[:=]\s*)"""
    r"""(?P<value>"[^"\n]*"|'[^'\n]*'|[^\s,;{}\[\]"']+)""",
)
# ``?api_key=…`` and ``#access_token=…``: a URL's own assignments.  They have to
# be handled before the generic pass, whose ``scheme:`` match would otherwise
# swallow the whole URL as one harmless value.
_URL_PARAMETER = re.compile(r"(?P<lead>[?&#])(?P<name>[A-Za-z0-9_.\-]+)=(?P<value>[^&#\s\"']*)")
# Random-looking candidates only: no path separators, so a long directory name
# is never mistaken for a credential.
_ENTROPY_CANDIDATE = re.compile(r"[A-Za-z0-9+_=\-]{28,}")
_HEX_ONLY = re.compile(r"\A[0-9a-fA-F]+\Z")
_MIN_ENTROPY_BITS = 3.5


def _shannon_entropy(value: str) -> float:
    counts: dict[str, int] = {}
    for character in value:
        counts[character] = counts.get(character, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_random(value: str) -> bool:
    """A long mixed-alphabet, high-entropy run — base64 and friends."""
    if _HEX_ONLY.match(value):
        return False  # A content fingerprint is deliberately safe to show.
    has_digit = any(character.isdigit() for character in value)
    has_lower = any(character.islower() for character in value)
    has_upper = any(character.isupper() for character in value)
    if not (has_digit and has_lower and has_upper):
        return False
    return _shannon_entropy(value) >= _MIN_ENTROPY_BITS


def _assignment_reason(text: str) -> RedactionReason | None:
    """Environment assignments first: their values never leave this module."""
    if any(is_secret_name(match.group("name")) for match in _URL_PARAMETER.finditer(text)):
        return RedactionReason.SECRET_VALUE
    matches = list(_ASSIGNMENT.finditer(text))
    if any(_is_environment_name(match.group("name")) for match in matches):
        return RedactionReason.ENVIRONMENT_VALUE
    if any(is_secret_name(match.group("name")) for match in matches):
        return RedactionReason.SECRET_VALUE
    return None


def secret_reason(value: str) -> RedactionReason | None:
    """Classify a value, or return ``None`` when it is safe to publish."""
    text = str(value)
    if _PRIVATE_KEY.search(text):
        return RedactionReason.PRIVATE_KEY
    assignment = _assignment_reason(text)
    if assignment is not None:
        return assignment
    if _CREDENTIAL_URL.search(text) or _PROVIDER_TOKEN.search(text):
        return RedactionReason.SECRET_VALUE
    if _JWT.search(text) or _BEARER.search(text):
        return RedactionReason.SECRET_VALUE
    if any(_looks_random(match.group(0)) for match in _ENTROPY_CANDIDATE.finditer(text)):
        return RedactionReason.HIGH_ENTROPY
    return None


def contains_secret(value: str) -> bool:
    """True when :func:`secret_reason` found something that must not be shown."""
    return secret_reason(value) is not None


def _redact_assignment(match: re.Match[str]) -> str:
    name = match.group("name")
    if _is_environment_name(name) or is_secret_name(name):
        return f"{match.group('open')}{name}{match.group('close')}{match.group('gap')}{REDACTED}"
    return match.group(0)


def _redact_url_parameter(match: re.Match[str]) -> str:
    name = match.group("name")
    if is_secret_name(name):
        return f"{match.group('lead')}{name}={REDACTED}"
    return match.group(0)


def _redact_entropy(match: re.Match[str]) -> str:
    return REDACTED if _looks_random(match.group(0)) else match.group(0)


def redact_text(value: str) -> str:
    """Replace every secret-looking span with :data:`REDACTED`, keep the rest.

    Used for text that has to survive to stay useful — a log line, an adapter
    error.  Structured metadata does not come through here: an unsafe field is
    dropped outright rather than published with a hole in it.
    """
    text = str(value)
    text = _PRIVATE_KEY.sub(REDACTED, text)
    text = _CREDENTIAL_URL.sub(lambda match: f"{match.group('scheme')}{REDACTED}@", text)
    text = _PROVIDER_TOKEN.sub(REDACTED, text)
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(REDACTED, text)
    text = _URL_PARAMETER.sub(_redact_url_parameter, text)
    text = _ASSIGNMENT.sub(_redact_assignment, text)
    return _ENTROPY_CANDIDATE.sub(_redact_entropy, text)


# ---------------------------------------------------------------------------
# Bounded display text
# ---------------------------------------------------------------------------


def _collapse(value: str) -> str:
    """One line, single spaces — a config file cannot survive this."""
    return " ".join(str(value).split())


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + ELLIPSIS


def _elide(text: str, limit: int) -> str:
    """Keep both ends — for a path, the tail is the informative part."""
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return f"{text[:head]}{ELLIPSIS}{text[-tail:]}"


def safe_message(
    value: str,
    *,
    kind: str = "diagnostic message",
    limit: int = MAX_MESSAGE_LENGTH,
) -> str:
    """Scrub, flatten and bound one line of free text.

    Raises :class:`RedactionError` when nothing readable survives, so a caller
    cannot publish an empty or invented message.
    """
    scrubbed = _collapse(redact_text(_collapse(value)))
    if not scrubbed:
        raise RedactionError(f"An inventory {kind} has no safe text left after redaction")
    return safe_text(_truncate(scrubbed, limit), kind=kind, limit=limit)


def safe_locator(
    value: str,
    *,
    home: str | None = None,
    limit: int = MAX_LOCATOR_LENGTH,
) -> str:
    """Turn a path or URL into a locator that points without disclosing.

    The home directory becomes ``~``, URL credentials and query strings are
    dropped (an ``?api_key=`` is a secret, not a location), any remaining
    secret span is redacted, and an over-long path is elided in the middle
    rather than rejected.
    """
    text = _collapse(value)
    if not text:
        raise RedactionError("An inventory locator has no safe text left after redaction")
    parts = urlsplit(text)
    if parts.scheme and parts.netloc:
        netloc = parts.netloc.rsplit("@", 1)[-1]
        text = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    else:
        root = home if home is not None else os.path.expanduser("~")
        if root and (text == root or text.startswith(f"{root}/")):
            text = f"~{text[len(root) :]}"
    text = _collapse(redact_text(text))
    if not text:
        raise RedactionError("An inventory locator has no safe text left after redaction")
    return safe_text(_elide(text, limit), kind="locator", limit=limit)


def _safe_field_name(name: object) -> str:
    """Even a field *name* is untrusted input; it is scrubbed and bounded."""
    scrubbed = _collapse(redact_text(str(name)))
    return _truncate(scrubbed, MAX_FIELD_NAME_LENGTH) or "(unnamed)"


# ---------------------------------------------------------------------------
# Safe metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RedactionFinding:
    """One field that did not make it through, and why."""

    field: str
    reason: RedactionReason

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _safe_field_name(self.field))

    def describe(self) -> str:
        return f"{self.field}: {self.reason.description}"

    def __str__(self) -> str:
        return self.describe()


@dataclass(frozen=True, slots=True)
class SafeMetadata:
    """Publishable fields plus the record of everything that was dropped."""

    fields: Mapping[str, SafeValue]
    findings: tuple[RedactionFinding, ...] = ()

    @property
    def is_complete(self) -> bool:
        """True when the whole source could be summarized safely."""
        return not self.findings

    @property
    def rejected_fields(self) -> tuple[str, ...]:
        return tuple(finding.field for finding in self.findings)

    def get(self, name: str, default: SafeValue | None = None) -> SafeValue | None:
        return self.fields.get(name, default)

    def describe_findings(self) -> str:
        return "; ".join(finding.describe() for finding in self.findings)

    def diagnostics(
        self,
        source_key: str,
        *,
        computer: str | None = None,
        identity: ItemIdentity | None = None,
        occurred_at: datetime | None = None,
        severity: DiagnosticSeverity = DiagnosticSeverity.WARNING,
    ) -> tuple[InventoryDiagnostic, ...]:
        """Report the drops as data, so collection continues without them."""
        return tuple(
            safe_diagnostic(
                source_key,
                severity,
                finding.describe(),
                computer=computer,
                identity=identity,
                occurred_at=occurred_at,
            )
            for finding in self.findings
        )


def _value_reason(value: object, *, limit: int) -> RedactionReason | None:
    if isinstance(value, bool | int | float):
        return None
    if not isinstance(value, str):
        return RedactionReason.UNSUPPORTED_TYPE
    reason = secret_reason(value)
    if reason is not None:
        return reason
    if value != _collapse(value):
        return RedactionReason.UNSAFE_SHAPE
    if len(value) > limit:
        return RedactionReason.TOO_LONG
    return None


def build_safe_metadata(
    raw: Mapping[str, object],
    *,
    allowed: Iterable[str],
    limit: int = MAX_LABEL_LENGTH,
) -> SafeMetadata:
    """Reduce raw adapter output to the safe facts an adapter declared.

    A field survives only when it is on ``allowed``, its name is not a
    credential name, and its value is a number, a boolean, or one bounded line
    of secret-free text.  Everything else is dropped with a finding — there is
    no partially redacted structured value, because a half-scrubbed config is
    still a config.
    """
    permitted = frozenset(allowed)
    fields: dict[str, SafeValue] = {}
    findings: list[RedactionFinding] = []
    for key, value in raw.items():
        name = str(key)
        if value is None:
            continue  # An absent fact is not a failure.
        if name not in permitted:
            findings.append(RedactionFinding(name, RedactionReason.NOT_ALLOWED))
            continue
        if is_secret_name(name):
            findings.append(RedactionFinding(name, RedactionReason.SECRET_NAME))
            continue
        reason = _value_reason(value, limit=limit)
        if reason is not None:
            findings.append(RedactionFinding(name, reason))
            continue
        fields[name] = value  # pyright: ignore[reportArgumentType]
    return SafeMetadata(fields=MappingProxyType(fields), findings=tuple(findings))


def require_safe_metadata(
    raw: Mapping[str, object],
    *,
    allowed: Iterable[str],
    limit: int = MAX_LABEL_LENGTH,
) -> Mapping[str, SafeValue]:
    """Fail closed: any dropped field aborts this source's summary entirely."""
    metadata = build_safe_metadata(raw, allowed=allowed, limit=limit)
    if not metadata.is_complete:
        raise RedactionError(f"Unsafe inventory metadata — {metadata.describe_findings()}")
    return metadata.fields


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def safe_diagnostic(
    source_key: str,
    severity: DiagnosticSeverity,
    message: str,
    *,
    computer: str | None = None,
    identity: ItemIdentity | None = None,
    occurred_at: datetime | None = None,
) -> InventoryDiagnostic:
    """Build a diagnostic whose message has already been through redaction.

    Never raises on the message: an adapter failure that cannot be described
    still has to be reportable, so an unusable message becomes a placeholder.
    """
    try:
        text = safe_message(message)
    except (RedactionError, ValueError):
        text = "An inventory source failed and its error could not be shown safely"
    return InventoryDiagnostic(
        source_key=source_key,
        severity=severity,
        message=text,
        computer=computer,
        identity=identity,
        occurred_at=occurred_at,
    )


# ---------------------------------------------------------------------------
# Prerequisites — presence, never the value
# ---------------------------------------------------------------------------


def _is_present(value: object) -> bool:
    match value:
        case None:
            return False
        case str():
            return bool(value.strip())
        case bytes() | bytearray() | list() | tuple() | set() | dict():
            return bool(value)
        case _:
            return True


def credential_prerequisite(name: str, *, present: bool | None) -> Prerequisite:
    """Record that a secret is configured, missing, or could not be checked."""
    return Prerequisite.credential(name, present=present)


def environment_prerequisites(
    names: Iterable[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[Prerequisite, ...]:
    """Ask whether each variable is set; the values never leave this call."""
    source = os.environ if environ is None else environ
    return tuple(
        credential_prerequisite(name, present=_is_present(source.get(name))) for name in names
    )


def connector_prerequisites(
    config: Mapping[str, object],
    *,
    required: Iterable[str],
) -> tuple[Prerequisite, ...]:
    """Read presence out of a raw connector config without reading values."""
    return tuple(
        credential_prerequisite(name, present=_is_present(config.get(name))) for name in required
    )


# ---------------------------------------------------------------------------
# The item boundary
# ---------------------------------------------------------------------------


def _item_text_fields(item: InventoryItem) -> Iterator[tuple[str, str]]:
    """Every free-text field an item can carry, with the path to name it."""
    yield "identity.name", item.identity.name
    yield "display_name", item.display_name
    if item.summary is not None:
        yield "summary", item.summary
    yield "source.label", item.source.label
    yield "source.locator", item.source.locator
    if item.scope.display_name is not None:
        yield "scope.display_name", item.scope.display_name
    if item.scope.project is not None:
        yield "scope.project", item.scope.project
    for entry in item.availability:
        if entry.evidence is not None:
            yield "availability.evidence", entry.evidence
        if entry.detail is not None:
            yield "availability.detail", entry.detail
    for prerequisite in item.prerequisites:
        yield "prerequisites.name", prerequisite.name
        if prerequisite.detail is not None:
            yield "prerequisites.detail", prerequisite.detail
    if item.measurement.tokenizer is not None:
        yield "measurement.tokenizer", item.measurement.tokenizer
    if item.measurement.model is not None:
        yield "measurement.model", item.measurement.model


def item_findings(item: InventoryItem) -> tuple[RedactionFinding, ...]:
    """Every unsafe field on an assembled item, named but never quoted."""
    return tuple(
        RedactionFinding(field, reason)
        for field, text in _item_text_fields(item)
        if (reason := secret_reason(text)) is not None
    )


def ensure_safe_item(item: InventoryItem) -> InventoryItem:
    """Return the item, or refuse it — the last check before a snapshot.

    The error names the offending field and the reason; it never repeats the
    value, because the exception text ends up in logs.
    """
    findings = item_findings(item)
    if findings:
        details = "; ".join(finding.describe() for finding in findings)
        raise RedactionError(f"Unsafe inventory item {item.identity.key!r} — {details}")
    return item


def screen_item(
    item: InventoryItem,
    *,
    computer: str | None = None,
    occurred_at: datetime | None = None,
) -> tuple[InventoryItem | None, InventoryDiagnostic | None]:
    """Fail-closed screening in the shape a collector can use.

    Returns the item and no diagnostic when it is safe; otherwise ``None`` and
    an error diagnostic, so one hostile item costs its own source, not the
    whole inventory.
    """
    findings = item_findings(item)
    if not findings:
        return item, None
    details = "; ".join(finding.describe() for finding in findings)
    diagnostic = safe_diagnostic(
        item.identity.source_key,
        DiagnosticSeverity.ERROR,
        f"Item withheld because it carried unsafe values — {details}",
        computer=computer or item.source.computer,
        identity=item.identity,
        occurred_at=occurred_at,
    )
    return None, diagnostic


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------


def safe_fingerprint(content: str | bytes, *, algorithm: str = "sha256") -> ContentFingerprint:
    """The only door raw content may walk through, and it comes out one-way.

    Two computers can be compared for parity without either side's content
    being stored, rendered or attached to an agent session.
    """
    if isinstance(content, str):
        return ContentFingerprint.of_text(content, algorithm=algorithm)
    return ContentFingerprint.of_bytes(bytes(content), algorithm=algorithm)


__all__ = [
    "ELLIPSIS",
    "MAX_FIELD_NAME_LENGTH",
    "REDACTED",
    "SECRET_NAME_TOKENS",
    "RedactionError",
    "RedactionFinding",
    "RedactionReason",
    "SafeMetadata",
    "SafeValue",
    "build_safe_metadata",
    "connector_prerequisites",
    "contains_secret",
    "credential_prerequisite",
    "ensure_safe_item",
    "environment_prerequisites",
    "is_secret_name",
    "item_findings",
    "name_tokens",
    "redact_text",
    "require_safe_metadata",
    "safe_diagnostic",
    "safe_fingerprint",
    "safe_locator",
    "safe_message",
    "screen_item",
    "secret_reason",
]
