"""Where the shared catalog meets the trusted handoff boundary.

Three contracts, one file, no Discord:

* **Origin** — :func:`build_catalog_lookup_task` turns a
  :class:`RemoteTargetResolution` (the resolver's "that lives on Drew's
  computer" answer) into one compact, read-only task packet addressed to that
  computer. The packet carries the owner and a relative locator, never a path:
  the same folder name can mean something unrelated here, and a remote path
  would bypass the destination's own permissions.
* **Reply** — :func:`build_catalog_reply_payload` is what the recipient puts in
  its result event (bounded JSON, no paths), and :func:`parse_catalog_reply`
  ingests it as a :class:`RemoteCatalogReply` stamped with the sender and the
  remote verification time. :func:`resolution_for_state` reports the handoff's
  queue state while nothing has come back, so an offline computer is *visibly
  queued* rather than silently replaced by a local same-named folder.
* **Recipient** — :func:`catalog_project_resolver` is the handoff
  ``ProjectResolver`` backed by this computer's approved roots, so an inbound
  locator resolves exactly as New session would.

Nothing here can produce a locally usable path from remote data:
:class:`RemoteProject` has no ``path`` attribute at all.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from claude_code_core.handoffs import protocol as p
from claude_code_core.handoffs.state import HandoffState

from .handoff_projects import (
    ApprovedRootResolver,
    ProjectResolutionError,
    ResolvedProject,
    build_project_resolver,
)
from .project_catalog import (
    MAX_QUERY_LIMIT,
    Availability,
    CatalogProject,
    CatalogSnapshot,
    RemoteTargetResolution,
    ResolutionResult,
    normalize_token,
    validate_child_name,
)

if TYPE_CHECKING:
    from .catalog_service import ProjectCatalogService

logger = logging.getLogger(__name__)

DEFAULT_HANDOFF_TTL = timedelta(hours=6)
ENV_LOOKUP_FOLDER = "CCDB_CATALOG_LOOKUP_FOLDER"
#: The locator folder a *listing* request names when no project term was given.
#: Matches the narrow project-lookup slice's pinned ``drew/main-projects`` so a
#: recipient that has not adopted the catalog resolver still answers.
DEFAULT_LOOKUP_FOLDER = "main-projects"

REPLY_KEY_CATALOG = "catalog"
REPLY_KEY_VERIFIED_AT = "verified_at"
REPLY_KEY_TRUNCATED = "truncated"

EXPECTED_REPLY_CONTRACT = (
    "Reply with a result event whose payload carries "
    f"{REPLY_KEY_CATALOG}=<JSON list of {{name, root, availability}}>, "
    f"{REPLY_KEY_VERIFIED_AT}=<ISO 8601 UTC time the folders were checked> and "
    f"{REPLY_KEY_TRUNCATED}=<true when the list was cut>. No paths: a path from "
    "another computer is not usable here."
)


# ---------------------------------------------------------------------------
# Origin: remote target -> task packet
# ---------------------------------------------------------------------------


def _lookup_folder(env: Mapping[str, str] | None) -> str:
    source: Mapping[str, str] = os.environ if env is None else env
    configured = (source.get(ENV_LOOKUP_FOLDER) or "").strip()
    return configured or DEFAULT_LOOKUP_FOLDER


def _folder_for(
    target: RemoteTargetResolution, explicit: str | None, env: Mapping[str, str] | None
) -> str:
    if explicit:
        return p.validate_relative_folder(explicit)
    terms = [term for term in target.requested_terms if term.strip()]
    if len(terms) == 1:
        try:
            validate_child_name(terms[0])
            return p.validate_relative_folder(terms[0])
        except (p.HandoffProtocolError, ValueError):
            pass  # a term that is not a folder name stays in the goal only
    return p.validate_relative_folder(_lookup_folder(env))


def build_catalog_lookup_task(
    target: ResolutionResult,
    *,
    origin: p.ConversationCoordinate,
    origin_human_id: str,
    sender_agent_id: str,
    folder: str | None = None,
    reply_to: p.ConversationCoordinate | None = None,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_HANDOFF_TTL,
    env: Mapping[str, str] | None = None,
    id_factory: Callable[[], tuple[str, str]] | None = None,
) -> p.HandoffEvent:
    """One compact, owner-qualified, read-only task for the target's computer.

    Only a :class:`RemoteTargetResolution` can become a handoff — a local
    result is bound locally and an ambiguous one is asked about, never sent.
    The recipient is the trusted computer's token (which doubles as its handoff
    agent id); the locator is the single requested project name when there is
    one, otherwise the configured lookup folder for a listing.
    """
    if not isinstance(target, RemoteTargetResolution):
        raise TypeError("only a remote target resolution can be handed off")
    recipient = p.validate_agent_id(target.computer)
    sender = p.validate_agent_id(sender_agent_id)
    if sender == recipient:
        raise ValueError("a remote target cannot be this computer")
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    task_id, event_id = (
        id_factory()
        if id_factory is not None
        else (
            str(uuid.uuid4()),
            str(uuid.uuid4()),
        )
    )
    terms = " ".join(term for term in target.requested_terms if term.strip())
    what = f"projects matching {terms!r}" if terms else "the project list"
    task = p.HandoffTask(
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        origin=origin,
        origin_human_id=origin_human_id,
        project=p.ProjectLocator(owner=target.owner, folder=_folder_for(target, folder, env)),
        goal=f"Catalog lookup on {target.owner}/{target.computer}: report {what} "
        "from the approved project roots (names, roots, availability only).",
        authority=p.AuthorityScope(read=True),
        expected_result=EXPECTED_REPLY_CONTRACT,
        reply_to=reply_to or origin,
        created_at=created_at,
        expires_at=created_at + ttl,
    )
    return p.HandoffEvent(
        event_id=event_id,
        kind=p.HandoffEventKind.TASK,
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        sequence=0,
        created_at=created_at,
        task=task,
    )


# ---------------------------------------------------------------------------
# Reply: recipient payload <-> remote snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RemoteProject:
    """A project as another computer reported it — deliberately without a path."""

    owner: str
    computer: str
    root: str
    name: str
    availability: Availability = Availability.UNKNOWN

    @property
    def key(self) -> str:
        return ":".join((self.owner, self.computer, self.root, self.name))

    @property
    def working_directory(self) -> None:
        """Always ``None``: a remote project is never a local working directory."""
        return None


@dataclass(frozen=True, slots=True)
class RemoteCatalogReply:
    """What a trusted computer answered, stamped with who and when."""

    owner: str
    computer: str
    source: str
    verified_at: datetime
    outcome: str
    projects: tuple[RemoteProject, ...] = ()
    truncated: bool = False
    summary: str = ""

    @property
    def is_locally_verified(self) -> bool:
        return False


def _project_item(project: CatalogProject) -> dict[str, str]:
    return {
        "name": project.identity.name,
        "root": project.identity.root_key,
        "availability": project.availability.value,
    }


def build_catalog_reply_payload(
    projects: CatalogSnapshot | Iterable[CatalogProject],
    *,
    verified_at: datetime,
    summary: str | None = None,
    outcome: str = "completed",
) -> dict[str, str | int | bool]:
    """The recipient's result payload: bounded JSON, names and roots, no paths."""
    found = list(projects.projects if isinstance(projects, CatalogSnapshot) else projects)
    truncated = isinstance(projects, CatalogSnapshot) and projects.truncated
    items = [_project_item(project) for project in found[:MAX_QUERY_LIMIT]]
    truncated = truncated or len(found) > MAX_QUERY_LIMIT
    encoded = json.dumps(items, separators=(",", ":"))
    while len(encoded) > p.MAX_PAYLOAD_VALUE_CHARS and items:
        items.pop()
        truncated = True
        encoded = json.dumps(items, separators=(",", ":"))
    stamp = verified_at.astimezone(UTC).isoformat()
    return {
        "outcome": outcome,
        "summary": summary or f"{len(items)} project(s) listed at {stamp}",
        REPLY_KEY_CATALOG: encoded,
        REPLY_KEY_VERIFIED_AT: stamp,
        REPLY_KEY_TRUNCATED: truncated,
    }


def _parse_items(raw: object, *, owner: str, computer: str) -> tuple[RemoteProject, ...]:
    if not isinstance(raw, str) or not raw.strip():
        return ()
    try:
        decoded = json.loads(raw)
    except ValueError:
        logger.warning("remote catalog reply from %s carried unreadable JSON", computer)
        return ()
    if not isinstance(decoded, list):
        return ()
    projects: list[RemoteProject] = []
    for item in decoded[:MAX_QUERY_LIMIT]:
        if not isinstance(item, dict):
            continue
        try:
            name = validate_child_name(str(item.get("name", "")))
            root = normalize_token(str(item.get("root", "")), kind="root key")
            availability = Availability(str(item.get("availability", "unknown")))
        except ValueError:
            continue  # never surface an entry the catalog's own rules would refuse
        projects.append(
            RemoteProject(
                owner=owner, computer=computer, root=root, name=name, availability=availability
            )
        )
    return tuple(projects)


def _parse_stamp(value: object, fallback: datetime) -> datetime:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback


def parse_catalog_reply(
    event: p.HandoffEvent, *, target: RemoteTargetResolution
) -> RemoteCatalogReply:
    """Ingest the recipient's result as a timestamped, path-free remote snapshot.

    The event must be a *result* and must come from the computer the target
    named — a reply from anyone else is refused, never merged.
    """
    if event.kind is not p.HandoffEventKind.RESULT:
        raise ValueError("only a result event carries a catalog reply")
    if event.sender != target.computer:
        raise ValueError(
            f"catalog reply came from {event.sender!r}, not the target {target.computer!r}"
        )
    payload = event.payload
    outcome = str(payload.get("outcome", "failed"))
    projects = (
        _parse_items(payload.get(REPLY_KEY_CATALOG), owner=target.owner, computer=target.computer)
        if outcome == "completed"
        else ()
    )
    return RemoteCatalogReply(
        owner=target.owner,
        computer=target.computer,
        source=event.sender,
        verified_at=_parse_stamp(payload.get(REPLY_KEY_VERIFIED_AT), event.created_at),
        outcome=outcome,
        projects=projects,
        truncated=bool(payload.get(REPLY_KEY_TRUNCATED, False)),
        summary=str(payload.get("summary", "")),
    )


def resolution_from_reply(
    target: RemoteTargetResolution, reply: RemoteCatalogReply
) -> RemoteTargetResolution:
    """The target, updated with what the remote computer reported."""
    if reply.outcome != "completed":
        availability = Availability.UNKNOWN
    elif any(project.availability.is_available for project in reply.projects):
        availability = Availability.AVAILABLE
    else:
        availability = Availability.MISSING
    return RemoteTargetResolution(
        owner=target.owner,
        computer=target.computer,
        requested_terms=target.requested_terms,
        availability=availability,
        verified_at=reply.verified_at,
        queued=False,
        source=reply.source,
    )


_WAITING_STATES = frozenset(
    {HandoffState.ACCEPTED, HandoffState.QUEUED, HandoffState.RUNNING, HandoffState.BLOCKED}
)


def resolution_for_state(
    target: RemoteTargetResolution, state: HandoffState | str, *, now: datetime | None = None
) -> RemoteTargetResolution:
    """Surface the handoff's queue state on the target while no reply exists.

    Accepted, queued, running and blocked are all "still waiting on that
    computer" — ``queued`` is True and availability stays unknown. A failed
    job is reported unavailable. In no case does a local folder stand in.
    """
    parsed = HandoffState.parse(state)
    return RemoteTargetResolution(
        owner=target.owner,
        computer=target.computer,
        requested_terms=target.requested_terms,
        availability=Availability.UNKNOWN,
        verified_at=(now or datetime.now(UTC)).astimezone(UTC),
        queued=parsed in _WAITING_STATES,
        source=f"handoff:{parsed.value}",
    )


# ---------------------------------------------------------------------------
# Recipient: the catalog as the handoff ProjectResolver
# ---------------------------------------------------------------------------


class CatalogRootResolver(ApprovedRootResolver):
    """Approved-root resolution that also understands ``root-key/name`` locators.

    A one-segment folder is looked up under every root of the owner (two
    matches is an error asking for the root key, never a guess); a root key on
    its own is that root (a listing job); ``key/name`` is one exact folder.
    Every hit is still checked to exist, be a directory and resolve inside its
    root — the same checks the handoff resolver has always made.
    """

    def __init__(
        self,
        roots: Mapping[str, tuple[Path, ...]],
        pinned: Mapping[p.ProjectLocator, Path],
        keyed: Mapping[str, Mapping[str, Path]],
    ) -> None:
        super().__init__(roots=roots, pinned=pinned)
        object.__setattr__(self, "_keyed", {owner: dict(v) for owner, v in keyed.items()})

    def resolve(self, locator: p.ProjectLocator) -> ResolvedProject:
        if not isinstance(locator, p.ProjectLocator):
            raise ProjectResolutionError("only a validated ProjectLocator can be resolved")
        keyed: Mapping[str, Path] = getattr(self, "_keyed", {}).get(locator.owner, {})
        if locator in self.pinned or not keyed:
            return super().resolve(locator)
        segments = locator.folder.split("/")
        if len(segments) == 2 and segments[0] in keyed:
            root = keyed[segments[0]]
            return self._check(locator, root / segments[1], root)
        if len(segments) == 1:
            if segments[0] in keyed:
                root = keyed[segments[0]]
                return self._check(locator, root, root)
            hits = [root for root in keyed.values() if (root / segments[0]).is_dir()]
            if len(hits) > 1:
                keys = ", ".join(f"{key}/{segments[0]}" for key, r in keyed.items() if r in hits)
                raise ProjectResolutionError(
                    f"{locator.folder!r} exists under more than one approved root; "
                    f"say which: {keys}"
                )
        return super().resolve(locator)


def catalog_project_resolver(
    catalog: ProjectCatalogService,
    *,
    env: Mapping[str, str] | None = None,
    fallback_lookup_root: str | None = None,
) -> CatalogRootResolver:
    """The handoff resolver this computer runs with once it has a catalog.

    The catalog's approved roots become the local owner's roots; the roots and
    pins ``build_project_resolver`` reads from the environment are kept, so a
    hand-configured owner or the legacy ``drew/main-projects`` pin still works.
    """
    base = build_project_resolver(env=env, fallback_lookup_root=fallback_lookup_root)
    owner = catalog.config.machine.owner
    roots: dict[str, tuple[Path, ...]] = {k: tuple(v) for k, v in base.roots.items()}
    catalog_paths = tuple(Path(str(root.path)) for root in catalog.roots)
    roots[owner] = catalog_paths + tuple(
        path for path in roots.get(owner, ()) if path not in catalog_paths
    )
    keyed = {owner: {root.key: Path(str(root.path)) for root in catalog.roots}}
    return CatalogRootResolver(roots=roots, pinned=dict(base.pinned), keyed=keyed)


__all__ = [
    "DEFAULT_HANDOFF_TTL",
    "DEFAULT_LOOKUP_FOLDER",
    "ENV_LOOKUP_FOLDER",
    "EXPECTED_REPLY_CONTRACT",
    "REPLY_KEY_CATALOG",
    "REPLY_KEY_TRUNCATED",
    "REPLY_KEY_VERIFIED_AT",
    "CatalogRootResolver",
    "RemoteCatalogReply",
    "RemoteProject",
    "build_catalog_lookup_task",
    "build_catalog_reply_payload",
    "catalog_project_resolver",
    "parse_catalog_reply",
    "resolution_for_state",
    "resolution_from_reply",
]
