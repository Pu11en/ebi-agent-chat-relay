"""Wire protocol for trusted cross-computer agent handoffs.

Surface-neutral by design: this module knows nothing about Discord, the session
database, or any particular harness. It defines the bounded, versioned values a
sender puts on the wire and the validation a recipient applies *before* anything
is stored or executed.

The rules encoded here are the loop- and blast-radius bounds from the
`trusted-agent-handoffs` design:

* only a ``task`` event can create work; every other kind is commentary,
* every event carries a UUID and a monotonic sequence inside its task,
* a project is an ``owner`` plus a relative ``folder`` — never a remote absolute
  path, never a pronoun whose owner cannot be proven,
* authority is a typed scope, so prose like "full permissions" cannot smuggle in
  a capability,
* every field and the whole packet are size-bounded, and serialization accepts
  only JSON scalars in known keys.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, NoReturn
from urllib.parse import unquote

PROTOCOL_VERSION = "v1"
SUPPORTED_VERSIONS = frozenset({PROTOCOL_VERSION})

# Bounds. They exist so a handoff stays a compact job packet rather than a
# forwarded conversation, and so one malformed message cannot exhaust a
# recipient's storage or Discord budget.
MAX_PACKET_BYTES = 6000
MAX_AGENT_ID_CHARS = 64
MAX_OWNER_CHARS = 64
MAX_FOLDER_CHARS = 200
MAX_GOAL_CHARS = 1500
MAX_EXPECTED_RESULT_CHARS = 800
MAX_FINDINGS = 10
MAX_FINDING_CHARS = 500
MAX_EDIT_PATHS = 20
MAX_HUMAN_ID_CHARS = 64
MAX_PAYLOAD_KEYS = 12
MAX_PAYLOAD_KEY_CHARS = 32
MAX_PAYLOAD_VALUE_CHARS = 1500
MAX_SEQUENCE = 10_000

_MAX_SNOWFLAKE = 2**63 - 1

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_FOLDER_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._ -]+$")
_PAYLOAD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# An owner has to be provable from the recorded human origin. These words change
# meaning depending on who is speaking, so they are never a project owner.
AMBIGUOUS_OWNER_TERMS = frozenset(
    {
        "me",
        "my",
        "mine",
        "myself",
        "we",
        "us",
        "our",
        "ours",
        "you",
        "your",
        "yours",
        "he",
        "him",
        "his",
        "she",
        "her",
        "hers",
        "they",
        "them",
        "their",
        "theirs",
        "it",
        "its",
        "user",
        "the user",
        "someone",
        "somebody",
        "anyone",
        "everyone",
        "self",
        "local",
        "here",
        "this computer",
    }
)

RESULT_OUTCOMES = frozenset({"completed", "failed"})


class HandoffProtocolError(ValueError):
    """Base class for every rejection of untrusted handoff input."""


class HandoffValidationError(HandoffProtocolError):
    """A packet or event is missing, malformed, ambiguous, or unsafe."""


class HandoffVersionError(HandoffProtocolError):
    """A packet or event declares a protocol version this build cannot honour."""


class HandoffSizeError(HandoffProtocolError):
    """A serialized packet exceeds its byte bound."""


class HandoffEventKind(Enum):
    """The five things agents say to each other, plus the task that starts it."""

    TASK = "task"
    ACK = "ack"
    STATE = "state"
    QUESTION = "question"
    ANSWER = "answer"
    RESULT = "result"

    @property
    def creates_work(self) -> bool:
        """Only a task event may schedule execution — acks and results never do."""
        return self is HandoffEventKind.TASK

    @property
    def is_terminal(self) -> bool:
        """A result event carries the terminal outcome of a logical task."""
        return self is HandoffEventKind.RESULT


class HandoffCapability(Enum):
    """Exceptional capabilities that must be granted explicitly, never inferred."""

    DESTRUCTIVE = "destructive"
    DEPLOYMENT = "deployment"
    PAID_PROVIDER = "paid_provider"
    EXTERNAL_MESSAGE = "external_message"
    PERMISSION_CHANGE = "permission_change"


def _fail(message: str) -> NoReturn:
    raise HandoffValidationError(message)


def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        _fail(f"{name} must be a string, got {type(value).__name__}")
    return value


def _clean_text(value: object, name: str, max_chars: int, *, allow_empty: bool = False) -> str:
    text = _require_str(value, name).strip()
    if not text and not allow_empty:
        _fail(f"{name} must not be empty")
    if len(text) > max_chars:
        _fail(f"{name} exceeds {max_chars} characters")
    if any(ch == "\x00" or (ord(ch) < 0x20 and ch not in "\n\t") for ch in text):
        _fail(f"{name} contains control characters")
    return text


def validate_uuid(value: object) -> str:
    """Return the canonical lowercase UUID string, or reject it.

    Only the plain 8-4-4-4-12 form is accepted: brace and ``urn:`` spellings of
    the same id would otherwise defeat deduplication by string equality.
    """
    text = _require_str(value, "id").strip().lower()
    if not _UUID_RE.match(text):
        _fail(f"not a canonical UUID: {text!r}")
    try:
        uuid.UUID(text)
    except ValueError:
        _fail(f"not a valid UUID: {text!r}")
    return text


def validate_agent_id(value: object) -> str:
    """Normalise a stable agent id (``DrewAI`` -> ``drewai``)."""
    text = _require_str(value, "agent id").strip().lower()
    if not text or len(text) > MAX_AGENT_ID_CHARS or not _AGENT_ID_RE.match(text):
        _fail(f"invalid agent id: {value!r}")
    return text


def validate_snowflake(value: object, name: str) -> int:
    """Validate a positive platform id (guild/channel/thread/message)."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{name} must be an integer id")
    if value <= 0 or value > _MAX_SNOWFLAKE:
        _fail(f"{name} is out of range")
    return int(value)


def validate_relative_folder(value: object, name: str = "folder") -> str:
    """Validate a relative, traversal-free project folder locator.

    Absolute paths are rejected outright: another computer's absolute path means
    nothing here, and accepting one would read outside the approved roots.
    """
    text = _require_str(value, name).strip()
    if not text:
        _fail(f"{name} must not be empty")
    if len(text) > MAX_FOLDER_CHARS:
        _fail(f"{name} exceeds {MAX_FOLDER_CHARS} characters")
    if "\x00" in text or "\\" in text:
        _fail(f"{name} contains an illegal character")
    if "://" in text:
        _fail(f"{name} must not be a URL")
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", text):
        _fail(f"{name} must be relative to an approved project root, not absolute")
    # Percent-encoding would otherwise hide "../" from the segment check below.
    for candidate in (text, unquote(text)):
        if candidate.startswith(("/", "~")) or "\\" in candidate:
            _fail(f"{name} resolves to an absolute or escaped path")
        segments = candidate.split("/")
        if any(seg in ("", ".", "..") for seg in segments):
            _fail(f"{name} contains an empty or traversing path segment")
    if any(not _FOLDER_SEGMENT_RE.match(seg) or seg != seg.strip() for seg in text.split("/")):
        _fail(f"{name} contains an unsupported path segment")
    return text


def validate_monotonic_sequence(previous: int | None, candidate: object) -> int:
    """Enforce bounded, strictly increasing event sequences within one task."""
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        _fail("sequence must be an integer")
    if candidate < 0 or candidate > MAX_SEQUENCE:
        _fail(f"sequence must be between 0 and {MAX_SEQUENCE}")
    if previous is None:
        if candidate != 0:
            _fail("the first event of a task must have sequence 0")
    elif candidate <= previous:
        _fail(f"sequence {candidate} does not follow {previous}")
    return int(candidate)


def next_sequence(previous: int | None) -> int:
    """Return the next legal sequence number after ``previous``."""
    if previous is None:
        return 0
    return validate_monotonic_sequence(previous, previous + 1)


def _validate_version(value: object) -> str:
    text = _require_str(value, "version").strip()
    if text not in SUPPORTED_VERSIONS:
        raise HandoffVersionError(f"unsupported protocol version: {text!r}")
    return text


def _validate_timestamp(value: object, name: str) -> datetime:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            _fail(f"{name} is not an ISO-8601 timestamp")
    if not isinstance(value, datetime):
        _fail(f"{name} must be a timestamp")
    if value.tzinfo is None:
        _fail(f"{name} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_keys(raw: object, required: set[str], optional: set[str], name: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        _fail(f"{name} must be a JSON object")
    keys = set(raw)
    if not all(isinstance(k, str) for k in keys):
        _fail(f"{name} has non-string keys")
    missing = required - keys
    if missing:
        _fail(f"{name} is missing required field(s): {', '.join(sorted(missing))}")
    unknown = keys - required - optional
    if unknown:
        _fail(f"{name} has unsupported field(s): {', '.join(sorted(unknown))}")
    return dict(raw)


@dataclass(frozen=True)
class ProjectLocator:
    """An explicit project owner plus a relative folder on the recipient."""

    owner: str
    folder: str

    def __post_init__(self) -> None:
        owner = _require_str(self.owner, "owner").strip().lower()
        if owner in AMBIGUOUS_OWNER_TERMS or owner.rstrip("s") in AMBIGUOUS_OWNER_TERMS:
            _fail(f"project owner {self.owner!r} is ambiguous; name the person")
        if not owner or len(owner) > MAX_OWNER_CHARS or not _OWNER_RE.match(owner):
            _fail(f"invalid project owner: {self.owner!r}")
        if any(word in AMBIGUOUS_OWNER_TERMS for word in re.split(r"[^a-z]+", owner) if word):
            _fail(f"project owner {self.owner!r} is ambiguous; name the person")
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "folder", validate_relative_folder(self.folder))

    def to_dict(self) -> dict[str, Any]:
        return {"owner": self.owner, "folder": self.folder}

    @classmethod
    def from_dict(cls, raw: object) -> ProjectLocator:
        data = _require_keys(raw, {"owner", "folder"}, set(), "project")
        return cls(owner=data["owner"], folder=data["folder"])


@dataclass(frozen=True)
class AuthorityScope:
    """The typed authority a handoff inherits from the original human request."""

    read: bool
    edit: bool = False
    edit_paths: tuple[str, ...] = ()
    capabilities: frozenset[HandoffCapability] = frozenset()

    def __post_init__(self) -> None:
        for name in ("read", "edit"):
            if not isinstance(getattr(self, name), bool):
                _fail(f"{name} authority must be a boolean")
        if not self.read and not self.edit:
            _fail("an authority scope must grant at least read")
        paths = tuple(self.edit_paths)
        if paths and not self.edit:
            _fail("edit_paths require edit authority")
        if len(paths) > MAX_EDIT_PATHS:
            _fail(f"at most {MAX_EDIT_PATHS} edit paths are allowed")
        paths = tuple(validate_relative_folder(path, "edit path") for path in paths)
        caps = frozenset(self.capabilities)
        if any(not isinstance(cap, HandoffCapability) for cap in caps):
            _fail("capabilities must be HandoffCapability members")
        object.__setattr__(self, "edit_paths", paths)
        object.__setattr__(self, "capabilities", caps)

    @property
    def is_read_only(self) -> bool:
        return not self.edit and not self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "read": self.read,
            "edit": self.edit,
            "edit_paths": list(self.edit_paths),
            "capabilities": sorted(cap.value for cap in self.capabilities),
        }

    @classmethod
    def from_dict(cls, raw: object) -> AuthorityScope:
        data = _require_keys(raw, {"read", "edit"}, {"edit_paths", "capabilities"}, "authority")
        paths = data.get("edit_paths") or []
        if not isinstance(paths, list):
            _fail("edit_paths must be a list")
        caps_raw = data.get("capabilities") or []
        if not isinstance(caps_raw, list):
            _fail("capabilities must be a list")
        caps = set()
        for value in caps_raw:
            text = _require_str(value, "capability")
            try:
                caps.add(HandoffCapability(text))
            except ValueError:
                _fail(f"unknown capability: {text!r}")
        return cls(
            read=data["read"],
            edit=data["edit"],
            edit_paths=tuple(_require_str(path, "edit path") for path in paths),
            capabilities=frozenset(caps),
        )


@dataclass(frozen=True)
class ConversationCoordinate:
    """Where a conversation lives: the origin of a task and where results return."""

    guild_id: int
    channel_id: int
    thread_id: int | None = None
    message_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "guild_id", validate_snowflake(self.guild_id, "guild_id"))
        object.__setattr__(self, "channel_id", validate_snowflake(self.channel_id, "channel_id"))
        for name in ("thread_id", "message_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, validate_snowflake(value, name))

    def to_dict(self) -> dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(cls, raw: object) -> ConversationCoordinate:
        data = _require_keys(
            raw, {"guild_id", "channel_id"}, {"thread_id", "message_id"}, "coordinate"
        )
        return cls(
            guild_id=data["guild_id"],
            channel_id=data["channel_id"],
            thread_id=data.get("thread_id"),
            message_id=data.get("message_id"),
        )


@dataclass(frozen=True)
class HandoffTask:
    """A compact, bounded job packet — never a forwarded conversation."""

    task_id: str
    sender: str
    recipient: str
    origin: ConversationCoordinate
    origin_human_id: str
    project: ProjectLocator
    goal: str
    authority: AuthorityScope
    expected_result: str
    reply_to: ConversationCoordinate
    created_at: datetime
    expires_at: datetime
    findings: tuple[str, ...] = ()
    version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "version", _validate_version(self.version))
        set_(self, "task_id", validate_uuid(self.task_id))
        set_(self, "sender", validate_agent_id(self.sender))
        set_(self, "recipient", validate_agent_id(self.recipient))
        if self.sender == self.recipient:
            _fail("a handoff needs two different agents")
        for name in ("origin", "reply_to"):
            if not isinstance(getattr(self, name), ConversationCoordinate):
                _fail(f"{name} must be a ConversationCoordinate")
        if not isinstance(self.project, ProjectLocator):
            _fail("project must be a ProjectLocator")
        if not isinstance(self.authority, AuthorityScope):
            _fail("authority must be an AuthorityScope")
        set_(
            self,
            "origin_human_id",
            _clean_text(self.origin_human_id, "origin_human_id", MAX_HUMAN_ID_CHARS),
        )
        set_(self, "goal", _clean_text(self.goal, "goal", MAX_GOAL_CHARS))
        set_(
            self,
            "expected_result",
            _clean_text(self.expected_result, "expected_result", MAX_EXPECTED_RESULT_CHARS),
        )
        findings = tuple(self.findings)
        if len(findings) > MAX_FINDINGS:
            _fail(f"at most {MAX_FINDINGS} findings may be attached")
        set_(
            self,
            "findings",
            tuple(_clean_text(item, "finding", MAX_FINDING_CHARS) for item in findings),
        )
        set_(self, "created_at", _validate_timestamp(self.created_at, "created_at"))
        set_(self, "expires_at", _validate_timestamp(self.expires_at, "expires_at"))
        if self.expires_at <= self.created_at:
            _fail("expires_at must be after created_at")

    def is_expired(self, now: datetime) -> bool:
        return _validate_timestamp(now, "now") >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_id": self.task_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "origin": self.origin.to_dict(),
            "origin_human_id": self.origin_human_id,
            "project": self.project.to_dict(),
            "goal": self.goal,
            "authority": self.authority.to_dict(),
            "findings": list(self.findings),
            "expected_result": self.expected_result,
            "reply_to": self.reply_to.to_dict(),
            "created_at": _iso(self.created_at),
            "expires_at": _iso(self.expires_at),
        }

    @classmethod
    def from_dict(cls, raw: object) -> HandoffTask:
        required = {
            "version",
            "task_id",
            "sender",
            "recipient",
            "origin",
            "origin_human_id",
            "project",
            "goal",
            "authority",
            "expected_result",
            "reply_to",
            "created_at",
            "expires_at",
        }
        data = _require_keys(raw, required, {"findings"}, "task")
        _validate_version(data["version"])
        findings = data.get("findings") or []
        if not isinstance(findings, list):
            _fail("findings must be a list")
        return cls(
            version=data["version"],
            task_id=data["task_id"],
            sender=data["sender"],
            recipient=data["recipient"],
            origin=ConversationCoordinate.from_dict(data["origin"]),
            origin_human_id=data["origin_human_id"],
            project=ProjectLocator.from_dict(data["project"]),
            goal=data["goal"],
            authority=AuthorityScope.from_dict(data["authority"]),
            findings=tuple(findings),
            expected_result=data["expected_result"],
            reply_to=ConversationCoordinate.from_dict(data["reply_to"]),
            created_at=data["created_at"],
            expires_at=data["expires_at"],
        )


def _validate_payload(kind: HandoffEventKind, raw: object) -> dict[str, str | int | bool]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        _fail("payload must be a JSON object")
    if len(raw) > MAX_PAYLOAD_KEYS:
        _fail(f"payload has more than {MAX_PAYLOAD_KEYS} keys")
    payload: dict[str, str | int | bool] = {}
    for key, value in raw.items():
        name = _require_str(key, "payload key")
        if len(name) > MAX_PAYLOAD_KEY_CHARS or not _PAYLOAD_KEY_RE.match(name):
            _fail(f"unsupported payload key: {key!r}")
        if isinstance(value, bool):
            payload[name] = value
        elif isinstance(value, int):
            if abs(value) > _MAX_SNOWFLAKE:
                _fail(f"payload value for {name!r} is out of range")
            payload[name] = value
        elif isinstance(value, str):
            payload[name] = _clean_text(value, f"payload.{name}", MAX_PAYLOAD_VALUE_CHARS)
        else:
            _fail(f"payload value for {name!r} must be a string, integer, or boolean")

    if kind is HandoffEventKind.STATE and not payload.get("state"):
        _fail("a state event must name its state")
    if kind is HandoffEventKind.QUESTION and not payload.get("question"):
        _fail("a question event must carry its question")
    if kind is HandoffEventKind.ANSWER and not payload.get("answer"):
        _fail("an answer event must carry its answer")
    if kind is HandoffEventKind.RESULT:
        outcome = payload.get("outcome")
        if outcome not in RESULT_OUTCOMES:
            _fail(f"a result event needs outcome in {sorted(RESULT_OUTCOMES)}")
        if not payload.get("summary"):
            _fail("a result event must carry a bounded summary")
    return payload


@dataclass(frozen=True)
class HandoffEvent:
    """One protocol message about one logical task."""

    event_id: str
    kind: HandoffEventKind
    task_id: str
    sender: str
    recipient: str
    sequence: int
    created_at: datetime
    task: HandoffTask | None = None
    payload: dict[str, str | int | bool] = field(default_factory=dict)
    version: str = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        set_ = object.__setattr__
        set_(self, "version", _validate_version(self.version))
        if not isinstance(self.kind, HandoffEventKind):
            _fail(f"unknown event kind: {self.kind!r}")
        set_(self, "event_id", validate_uuid(self.event_id))
        set_(self, "task_id", validate_uuid(self.task_id))
        set_(self, "sender", validate_agent_id(self.sender))
        set_(self, "recipient", validate_agent_id(self.recipient))
        if self.sender == self.recipient:
            _fail("an event needs two different agents")
        set_(self, "created_at", _validate_timestamp(self.created_at, "created_at"))
        # A task event opens the chain at 0; everything else answers it.
        set_(
            self,
            "sequence",
            validate_monotonic_sequence(None if self.kind.creates_work else 0, self.sequence),
        )
        if self.kind.creates_work:
            if not isinstance(self.task, HandoffTask):
                _fail("a task event must carry its task packet")
            task = self.task
            if task.version != self.version:
                raise HandoffVersionError("event and task protocol versions disagree")
            if (task.task_id, task.sender, task.recipient) != (
                self.task_id,
                self.sender,
                self.recipient,
            ):
                _fail("task packet does not match its event envelope")
        elif self.task is not None:
            _fail(f"a {self.kind.value} event must not carry a task packet")
        set_(self, "payload", _validate_payload(self.kind, self.payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "task_id": self.task_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "sequence": self.sequence,
            "created_at": _iso(self.created_at),
            "payload": dict(self.payload),
            "task": self.task.to_dict() if self.task is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: object) -> HandoffEvent:
        required = {
            "version",
            "event_id",
            "kind",
            "task_id",
            "sender",
            "recipient",
            "sequence",
            "created_at",
        }
        data = _require_keys(raw, required, {"payload", "task"}, "event")
        _validate_version(data["version"])
        kind_value = _require_str(data["kind"], "kind")
        try:
            kind = HandoffEventKind(kind_value)
        except ValueError:
            _fail(f"unknown event kind: {kind_value!r}")
        task_raw = data.get("task")
        return cls(
            version=data["version"],
            event_id=data["event_id"],
            kind=kind,
            task_id=data["task_id"],
            sender=data["sender"],
            recipient=data["recipient"],
            sequence=data["sequence"],
            created_at=data["created_at"],
            task=HandoffTask.from_dict(task_raw) if task_raw is not None else None,
            payload=data.get("payload") or {},
        )


def encode_event(event: HandoffEvent, *, max_bytes: int = MAX_PACKET_BYTES) -> str:
    """Serialize an event to bounded, deterministic JSON."""
    if not isinstance(event, HandoffEvent):
        _fail("only a HandoffEvent can be encoded")
    text = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        raise HandoffSizeError(f"handoff packet is {size} bytes, limit is {max_bytes}")
    return text


def decode_event(text: object, *, max_bytes: int = MAX_PACKET_BYTES) -> HandoffEvent:
    """Parse and fully validate an event received from another agent."""
    raw_text = _require_str(text, "packet")
    size = len(raw_text.encode("utf-8"))
    if size > max_bytes:
        raise HandoffSizeError(f"handoff packet is {size} bytes, limit is {max_bytes}")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise HandoffValidationError(f"packet is not valid JSON: {exc.msg}") from exc
    return HandoffEvent.from_dict(parsed)
