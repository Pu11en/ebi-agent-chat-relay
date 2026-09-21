"""The catalog ↔ trusted-handoff contract (OpenSpec shared-project-catalog task 4.2).

Origin side: a ``RemoteTargetResolution`` becomes one compact, owner-qualified
task packet for the destination computer. Reply side: the recipient's result
event is ingested as a timestamped remote snapshot that never carries a path a
local caller could bind. Recipient side: the catalog implements the handoff
``ProjectResolver`` so an inbound locator resolves inside the approved roots.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_code_core.handoffs import protocol as p
from claude_code_core.handoffs.state import HandoffState
from claude_discord.catalog_config import CatalogConfig
from claude_discord.catalog_handoff import (
    REPLY_KEY_CATALOG,
    REPLY_KEY_VERIFIED_AT,
    RemoteCatalogReply,
    build_catalog_lookup_task,
    build_catalog_reply_payload,
    catalog_project_resolver,
    parse_catalog_reply,
    resolution_for_state,
    resolution_from_reply,
)
from claude_discord.catalog_service import ProjectCatalogService, resolution_to_dict
from claude_discord.handoff_projects import ProjectResolutionError
from claude_discord.project_catalog import (
    Availability,
    RemoteTargetResolution,
    ResolutionKind,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
ORIGIN = p.ConversationCoordinate(guild_id=10, channel_id=20, thread_id=30, message_id=40)
IDS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")


def _remote(*terms: str) -> RemoteTargetResolution:
    return RemoteTargetResolution(owner="drew", computer="drewai", requested_terms=terms)


# --- origin: remote target → compact task packet -----------------------------


def test_remote_target_becomes_an_owner_qualified_read_only_task() -> None:
    event = build_catalog_lookup_task(
        _remote("alpha"),
        origin=ORIGIN,
        origin_human_id="777",
        sender_agent_id="david",
        now=NOW,
        id_factory=lambda: IDS,
    )
    assert event.kind is p.HandoffEventKind.TASK
    task = event.task
    assert task is not None
    assert (task.sender, task.recipient) == ("david", "drewai")
    assert task.project == p.ProjectLocator(owner="drew", folder="alpha")
    assert task.authority.read is True and task.authority.edit is False
    assert "alpha" in task.goal and "drew" in task.goal
    assert "path" in task.expected_result.lower()  # the contract says: no paths
    assert task.reply_to == ORIGIN and task.origin == ORIGIN
    assert task.created_at == NOW and task.expires_at > NOW
    # The packet is bounded and round-trips through the strict envelope.
    text = p.encode_event(event)
    assert p.decode_event(text) == event


def test_a_listing_request_uses_the_lookup_folder_not_a_guess() -> None:
    event = build_catalog_lookup_task(
        _remote(),
        origin=ORIGIN,
        origin_human_id="777",
        sender_agent_id="david",
        env={"CCDB_CATALOG_LOOKUP_FOLDER": "projects"},
        id_factory=lambda: IDS,
    )
    assert event.task is not None and event.task.project.folder == "projects"
    event = build_catalog_lookup_task(
        _remote(), origin=ORIGIN, origin_human_id="777", sender_agent_id="david", env={}
    )
    assert event.task is not None and event.task.project.folder == "main-projects"


def test_only_a_remote_target_can_become_a_handoff_and_never_to_itself(tmp_path: Path) -> None:
    (tmp_path / "alpha").mkdir()
    config = CatalogConfig.from_env(
        {
            "CCDB_PROJECT_ROOTS": str(tmp_path),
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
        }
    )
    local = ProjectCatalogService(config, None).resolver.resolve("alpha")
    assert local.kind is ResolutionKind.LOCAL_AVAILABLE
    with pytest.raises(TypeError):
        build_catalog_lookup_task(local, origin=ORIGIN, origin_human_id="7", sender_agent_id="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_catalog_lookup_task(
            _remote("alpha"), origin=ORIGIN, origin_human_id="7", sender_agent_id="drewai"
        )
    # A term that is not a folder name cannot smuggle a path into the locator.
    event = build_catalog_lookup_task(
        _remote("../etc"), origin=ORIGIN, origin_human_id="7", sender_agent_id="david", env={}
    )
    assert event.task is not None and event.task.project.folder == "main-projects"
    assert "../etc" in event.task.goal


# --- reply: recipient payload ↔ remote snapshot ----------------------------


async def test_recipient_reply_payload_is_bounded_and_path_free(tmp_path: Path) -> None:
    for name in ("alpha", "beta"):
        (tmp_path / name).mkdir()
    config = CatalogConfig.from_env(
        {
            "CCDB_PROJECT_ROOTS": str(tmp_path),
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
        }
    )
    service = ProjectCatalogService(config, None, cache_ttl=0)
    snapshot = await service.search("")
    payload = build_catalog_reply_payload(snapshot, verified_at=NOW)
    assert payload["outcome"] == "completed"
    assert str(tmp_path) not in json.dumps(payload)
    listed = json.loads(str(payload[REPLY_KEY_CATALOG]))
    assert [item["name"] for item in listed] == ["alpha", "beta"]
    assert all("path" not in item for item in listed)
    assert payload[REPLY_KEY_VERIFIED_AT] == NOW.isoformat()
    # The payload fits the protocol's per-value bound even for a huge catalog.
    big = build_catalog_reply_payload(
        [_fake_project(f"project-with-a-long-name-{i:03d}") for i in range(400)],
        verified_at=NOW,
    )
    assert len(str(big[REPLY_KEY_CATALOG])) <= p.MAX_PAYLOAD_VALUE_CHARS
    assert big["truncated"] is True
    event = p.HandoffEvent(
        event_id=IDS[1],
        kind=p.HandoffEventKind.RESULT,
        task_id=IDS[0],
        sender="drewai",
        recipient="david",
        sequence=3,
        created_at=NOW,
        payload=big,
    )
    p.decode_event(p.encode_event(event))


def _fake_project(name: str):
    from pathlib import PurePosixPath

    from claude_discord.project_catalog import CatalogProject, ProjectIdentity

    return CatalogProject(
        identity=ProjectIdentity("drew", "drewai", "main", name),
        path=PurePosixPath("/srv/main") / name,
    )


def _result_event(payload: dict, *, sender: str = "drewai") -> p.HandoffEvent:
    return p.HandoffEvent(
        event_id=IDS[1],
        kind=p.HandoffEventKind.RESULT,
        task_id=IDS[0],
        sender=sender,
        recipient="david",
        sequence=2,
        created_at=NOW,
        payload=payload,
    )


def test_a_reply_is_ingested_with_its_verification_time_and_no_usable_path() -> None:
    payload = {
        "outcome": "completed",
        "summary": "2 projects",
        REPLY_KEY_CATALOG: json.dumps(
            [
                {"name": "alpha", "root": "main", "availability": "available", "path": "/x"},
                {"name": "beta", "root": "main", "availability": "missing"},
                {"name": "../up", "root": "main", "availability": "available"},
            ]
        ),
        REPLY_KEY_VERIFIED_AT: NOW.isoformat(),
    }
    reply = parse_catalog_reply(_result_event(payload), target=_remote("alpha"))
    assert isinstance(reply, RemoteCatalogReply)
    assert reply.source == "drewai" and reply.verified_at == NOW
    assert [(r.name, r.availability) for r in reply.projects] == [
        ("alpha", Availability.AVAILABLE),
        ("beta", Availability.MISSING),
    ]
    assert all(not hasattr(r, "path") for r in reply.projects)
    assert all(r.working_directory is None for r in reply.projects)
    assert reply.is_locally_verified is False
    resolved = resolution_from_reply(_remote("alpha"), reply)
    assert resolved.kind is ResolutionKind.REMOTE_TARGET
    assert resolved.working_directory is None
    assert resolved.availability is Availability.AVAILABLE
    assert resolved.verified_at == NOW and resolved.source == "drewai"
    assert resolved.queued is False
    assert "path" not in resolution_to_dict(resolved)
    assert resolution_to_dict(resolved)["locally_verified"] is False


def test_a_reply_from_another_computer_or_a_non_result_is_refused() -> None:
    payload = {"outcome": "completed", "summary": "ok", REPLY_KEY_CATALOG: "[]"}
    with pytest.raises(ValueError):
        parse_catalog_reply(_result_event(payload, sender="imac"), target=_remote("alpha"))
    ack = p.HandoffEvent(
        event_id=IDS[1],
        kind=p.HandoffEventKind.ACK,
        task_id=IDS[0],
        sender="drewai",
        recipient="david",
        sequence=1,
        created_at=NOW,
    )
    with pytest.raises(ValueError):
        parse_catalog_reply(ack, target=_remote("alpha"))


def test_a_failed_or_malformed_reply_is_unavailable_never_a_local_fallback() -> None:
    failed = parse_catalog_reply(
        _result_event({"outcome": "failed", "summary": "offline"}), target=_remote("alpha")
    )
    assert failed.outcome == "failed" and failed.projects == ()
    resolved = resolution_from_reply(_remote("alpha"), failed)
    assert resolved.availability is Availability.UNKNOWN and resolved.working_directory is None
    garbage = parse_catalog_reply(
        _result_event({"outcome": "completed", "summary": "x", REPLY_KEY_CATALOG: "{not json"}),
        target=_remote("alpha"),
    )
    assert garbage.projects == () and garbage.verified_at == NOW


@pytest.mark.parametrize(
    "state, queued, availability",
    [
        (HandoffState.ACCEPTED, True, Availability.UNKNOWN),
        (HandoffState.QUEUED, True, Availability.UNKNOWN),
        (HandoffState.RUNNING, True, Availability.UNKNOWN),
        (HandoffState.BLOCKED, True, Availability.UNKNOWN),
        ("failed", False, Availability.UNKNOWN),
    ],
)
def test_handoff_state_is_surfaced_as_queue_state_not_as_a_local_answer(
    state: HandoffState | str, queued: bool, availability: Availability
) -> None:
    resolved = resolution_for_state(_remote("alpha"), state, now=NOW)
    assert resolved.queued is queued
    assert resolved.availability is availability
    assert resolved.working_directory is None
    assert resolved.source is not None and resolved.source.startswith("handoff:")
    payload = resolution_to_dict(resolved)
    assert payload["queued"] is queued and "path" not in payload


# --- recipient: the catalog as the handoff ProjectResolver ------------------


def _catalog(tmp_path: Path) -> ProjectCatalogService:
    main = tmp_path / "main"
    work = tmp_path / "work"
    for name in ("alpha", "beta"):
        (main / name).mkdir(parents=True)
    (work / "alpha").mkdir(parents=True)
    (main / "notes.txt").write_text("x", encoding="utf-8")
    config = CatalogConfig.from_env(
        {
            "CCDB_PROJECT_ROOTS": f"{main},{work}",
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
        }
    )
    return ProjectCatalogService(config, None, cache_ttl=0)


def test_catalog_resolver_resolves_locators_inside_the_approved_roots(tmp_path: Path) -> None:
    resolver = catalog_project_resolver(_catalog(tmp_path), env={})
    resolved = resolver.resolve(p.ProjectLocator(owner="drew", folder="beta"))
    assert resolved.path == (tmp_path / "main" / "beta").resolve()
    assert resolved.root == (tmp_path / "main").resolve()
    # A root key qualifies a same-named project; the bare key is the root itself.
    resolved = resolver.resolve(p.ProjectLocator(owner="drew", folder="work/alpha"))
    assert resolved.path == (tmp_path / "work" / "alpha").resolve()
    resolved = resolver.resolve(p.ProjectLocator(owner="drew", folder="work"))
    assert resolved.path == (tmp_path / "work").resolve()
    with pytest.raises(ProjectResolutionError):
        resolver.resolve(p.ProjectLocator(owner="drew", folder="alpha"))  # two: say which
    with pytest.raises(ProjectResolutionError):
        resolver.resolve(p.ProjectLocator(owner="drew", folder="notes.txt"))
    with pytest.raises(ProjectResolutionError):
        resolver.resolve(p.ProjectLocator(owner="drew", folder="main/missing"))
    with pytest.raises(ProjectResolutionError):
        resolver.resolve(p.ProjectLocator(owner="david", folder="beta"))


def test_catalog_resolver_keeps_the_configured_owner_roots_and_pins(tmp_path: Path) -> None:
    other = tmp_path / "david-root"
    (other / "gamma").mkdir(parents=True)
    resolver = catalog_project_resolver(
        _catalog(tmp_path),
        env={"CCDB_HANDOFF_PROJECT_ROOTS": f"david={other}"},
        fallback_lookup_root=str(tmp_path / "main"),
    )
    assert (
        resolver.resolve(p.ProjectLocator(owner="david", folder="gamma")).path
        == (other / "gamma").resolve()
    )
    pinned = resolver.resolve(p.ProjectLocator(owner="drew", folder="main-projects"))
    assert pinned.path == (tmp_path / "main").resolve()


def test_executor_and_cog_accept_the_catalog_resolver() -> None:
    from unittest.mock import MagicMock

    from claude_discord.cogs.agent_handoff import AgentHandoffCog
    from claude_discord.handoff_config import HandoffConfig
    from claude_discord.handoff_executor import build_handoff_executor

    resolver = MagicMock()
    executor = build_handoff_executor(repo=MagicMock(), local_agent_id="drewai", resolver=resolver)
    assert executor._resolver is resolver
    config = HandoffConfig.from_env(
        {
            "CCDB_AGENT_ID": "drewai",
            "CCDB_HANDOFF_GUILD_ID": "10",
            "CCDB_HANDOFF_CHANNEL_ID": "20",
            "CCDB_HANDOFF_AGENTS": "david=123",
        }
    )
    assert config is not None
    cog = AgentHandoffCog(
        MagicMock(), repo=MagicMock(), config=config, project_resolver=resolver, start_loops=False
    )
    assert cog.executor._resolver is resolver
