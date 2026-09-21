"""Remote snapshot tests (task 3.2): a bounded request/reply and honest freshness.

A trusted computer answers an inventory request with a reply packet; this
side ingests it only when it comes from a computer it trusts and matches the
request it sent.  What it then says about that computer follows the
verification time, not hope: live within the freshness window, stale after
it, unreachable when nothing was ever verified.  Comparison results per item
distinguish matching, differing content, missing on one side, and a
declared deliberate difference.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_code_core.handoffs.protocol import (
    AuthorityScope,
    ConversationCoordinate,
    HandoffTask,
    ProjectLocator,
)
from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_collector import (
    AdapterResult,
    CallableAdapter,
    CollectionContext,
    InventoryCollector,
)
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    ContentFingerprint,
    EffectiveScope,
    Freshness,
    HarnessAvailability,
    InventoryItem,
    InventorySnapshot,
    InventorySource,
    ItemIdentity,
    Prerequisite,
    PrerequisiteKind,
    PrerequisiteState,
    SetupKind,
)
from claude_discord.ai_setup_remote import (
    MAX_REPLY_BYTES,
    ComparisonStatus,
    IngestStatus,
    InventoryPacketError,
    InventoryReply,
    InventoryRequest,
    InventoryRequestOperation,
    compare_snapshots,
    ingest_reply,
    remote_freshness,
    unreachable_snapshot,
)
from claude_discord.database.ai_setup_repo import AISetupRepository

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
LEAKED = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake


def item(
    name: str,
    *,
    computer: str,
    digest: str = "ab" * 32,
    kind: SetupKind = SetupKind.SKILL,
    classification: Classification = Classification.CUSTOM,
    deliberate: str | None = None,
) -> InventoryItem:
    prerequisites: tuple[Prerequisite, ...] = ()
    availability = (
        HarnessAvailability(
            harness="claude", state=AvailabilityState.DISCOVERED, computer=computer
        ),
    )
    if deliberate is not None:
        prerequisites = (
            Prerequisite(
                name=deliberate,
                state=PrerequisiteState.MISSING,
                kind=PrerequisiteKind.SUBSCRIPTION,
                detail="Deliberate difference declared for this computer",
            ),
        )
        availability = (
            HarnessAvailability(
                harness="claude",
                state=AvailabilityState.MISSING_PREREQUISITE,
                computer=computer,
                detail=f"Deliberate difference: {deliberate}",
            ),
        )
    return InventoryItem(
        identity=ItemIdentity(kind=kind, source_key="claude-home", name=name),
        display_name=name,
        source=InventorySource(
            key="claude-home",
            computer=computer,
            label="Claude home",
            locator=f"~/.claude/skills/{name}/SKILL.md",
        ),
        scope=EffectiveScope.shared_profile("drew"),
        classification=classification,
        availability=availability,
        prerequisites=prerequisites,
        fingerprint=ContentFingerprint(digest=digest),
    )


def snapshot(computer: str, *items: InventoryItem, at: datetime = NOW) -> InventorySnapshot:
    return InventorySnapshot(computer=computer, owner="drew", collected_at=at, items=items)


def request(**overrides: object) -> InventoryRequest:
    fields: dict[str, object] = {
        "request_id": "8d1d2b2e-6d6f-4a9a-9d59-1c2a3b4c5d6e",
        "requester": "drewai",
        "computer": "imac",
        "requested_at": NOW,
    }
    fields.update(overrides)
    return InventoryRequest(**fields)  # pyright: ignore[reportArgumentType]


@pytest.fixture
async def repo(tmp_path: Path) -> AISetupRepository:
    repository = AISetupRepository(str(tmp_path / "ai_setup.db"))
    await repository.init_db()
    return repository


# ---------------------------------------------------------------------------
# The packets
# ---------------------------------------------------------------------------


class TestRequestPacket:
    def test_a_request_rides_in_a_handoff_goal(self):
        original = request(include_builtins=True, kinds=(SetupKind.SKILL, SetupKind.HOOK))
        goal = original.to_goal()
        assert goal.startswith("ccdb:inventory-request")
        assert len(goal) <= 1500
        assert InventoryRequest.from_goal(goal) == original
        assert InventoryRequest.from_goal("please look at the inventory") is None

    def test_a_request_is_validated(self):
        with pytest.raises(ValueError):
            request(request_id="not a uuid")
        with pytest.raises(ValueError):
            request(requested_at=NOW.replace(tzinfo=None))
        with pytest.raises(InventoryPacketError):
            InventoryRequest.from_goal("ccdb:inventory-request v1 {not json")

    def test_a_request_expires(self):
        original = request(ttl=timedelta(minutes=10))
        assert not original.is_expired(NOW + timedelta(minutes=5))
        assert original.is_expired(NOW + timedelta(minutes=11))


class TestReplyPacket:
    def test_a_reply_round_trips_with_its_exceptions(self):
        remote = snapshot("imac", item("grilling", computer="imac"))
        declared = (DeliberateException("plugin:", "Plugins need Max"),)
        reply = InventoryReply(
            request_id=request().request_id, snapshot=remote, exceptions=declared
        )
        text = reply.encode()
        assert len(text.encode("utf-8")) <= MAX_REPLY_BYTES
        decoded = InventoryReply.decode(text)
        assert decoded == reply

    def test_a_reply_that_does_not_fit_is_refused_not_split(self):
        many = [item(f"skill-{index}", computer="imac") for index in range(40)]
        reply = InventoryReply(request_id=request().request_id, snapshot=snapshot("imac", *many))
        with pytest.raises(InventoryPacketError):
            reply.encode(max_bytes=4_000)

    def test_a_reply_with_a_secret_is_refused_on_decode(self):
        remote = snapshot("imac", item("grilling", computer="imac"))
        reply = InventoryReply(request_id=request().request_id, snapshot=remote)
        text = reply.encode().replace('"summary":null', f'"summary":"{LEAKED}"', 1)
        with pytest.raises(InventoryPacketError):
            InventoryReply.decode(text)
        with pytest.raises(InventoryPacketError):
            InventoryReply.decode("garbage")
        with pytest.raises(InventoryPacketError):
            InventoryReply.decode("x" * (MAX_REPLY_BYTES + 1))


# ---------------------------------------------------------------------------
# Answering a request locally (the deterministic handoff operation)
# ---------------------------------------------------------------------------


def handoff_task(goal: str) -> HandoffTask:
    coordinate = ConversationCoordinate(guild_id=1, channel_id=2, thread_id=3)
    return HandoffTask(
        task_id="8d1d2b2e-6d6f-4a9a-9d59-1c2a3b4c5d6e",
        sender="drewai",
        recipient="imac",
        origin=coordinate,
        origin_human_id="drew",
        project=ProjectLocator(owner="drew", folder="main-projects"),
        goal=goal,
        authority=AuthorityScope(read=True),
        expected_result="inventory reply",
        reply_to=coordinate,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


class TestRequestOperation:
    async def test_it_answers_only_inventory_requests_and_never_runs_a_model(self):
        def gather(context: CollectionContext) -> AdapterResult:
            return AdapterResult(items=(item("grilling", computer=context.computer),))

        collector = InventoryCollector()
        collector.register(CallableAdapter("fixture", (SetupKind.SKILL,), ("claude-home",), gather))
        operation = InventoryRequestOperation(
            collector=collector,
            context_factory=lambda: CollectionContext(
                computer="imac", owner="drew", collected_at=NOW
            ),
            exceptions=(DeliberateException("plugin:", "Plugins need Max"),),
        )
        assert not operation.matches(handoff_task("audit the skills folder"))
        task = handoff_task(request().to_goal())
        assert operation.matches(task)
        text = await operation.run(task, None)  # type: ignore[arg-type]
        reply = InventoryReply.decode(text)
        assert reply.request_id == task.task_id
        assert reply.snapshot.computer == "imac"
        assert [entry.identity.name for entry in reply.snapshot.items] == ["grilling"]
        assert reply.exceptions[0].reason == "Plugins need Max"

    async def test_a_request_for_another_computer_is_not_answered(self):
        collector = InventoryCollector()
        operation = InventoryRequestOperation(
            collector=collector,
            context_factory=lambda: CollectionContext(
                computer="david", owner="david", collected_at=NOW
            ),
        )
        assert not operation.matches(handoff_task(request(computer="imac").to_goal()))


# ---------------------------------------------------------------------------
# Trusted ingestion and freshness
# ---------------------------------------------------------------------------


class TestIngest:
    async def test_a_trusted_matching_reply_is_stored_as_live(self, repo: AISetupRepository):
        sent = request()
        reply = InventoryReply(
            request_id=sent.request_id,
            snapshot=snapshot("imac", item("grilling", computer="imac")),
            exceptions=(DeliberateException("plugin:", "Plugins need Max"),),
        )
        outcome = await ingest_reply(
            reply.encode(), request=sent, trusted=("imac", "david"), repo=repo, now=NOW
        )
        assert outcome.status is IngestStatus.ACCEPTED
        stored = await repo.load_snapshot("imac")
        assert stored is not None
        assert stored.freshness is Freshness.LIVE
        assert stored.verified_at == NOW
        assert stored.source_label == "trusted reply from imac"
        assert await repo.load_exceptions("imac") == reply.exceptions

    async def test_an_untrusted_or_mismatched_reply_stores_nothing(self, repo: AISetupRepository):
        sent = request()
        reply = InventoryReply(
            request_id=sent.request_id, snapshot=snapshot("imac", item("x", computer="imac"))
        )
        untrusted = await ingest_reply(
            reply.encode(), request=sent, trusted=("david",), repo=repo, now=NOW
        )
        assert untrusted.status is IngestStatus.REJECTED
        assert "trusted" in untrusted.reason
        wrong_request = await ingest_reply(
            reply.encode(),
            request=request(request_id="11111111-2222-4333-8444-555555555555"),
            trusted=("imac",),
            repo=repo,
            now=NOW,
        )
        assert wrong_request.status is IngestStatus.REJECTED
        wrong_computer = await ingest_reply(
            reply.encode(),
            request=request(computer="david"),
            trusted=("imac", "david"),
            repo=repo,
            now=NOW,
        )
        assert wrong_computer.status is IngestStatus.REJECTED
        malformed = await ingest_reply("nope", request=sent, trusted=("imac",), repo=repo, now=NOW)
        assert malformed.status is IngestStatus.REJECTED
        assert await repo.load_snapshot("imac") is None

    async def test_a_reply_from_the_future_or_older_than_stored_is_refused(
        self, repo: AISetupRepository
    ):
        sent = request()
        future = InventoryReply(
            request_id=sent.request_id,
            snapshot=snapshot("imac", at=NOW + timedelta(hours=1)),
        )
        outcome = await ingest_reply(
            future.encode(), request=sent, trusted=("imac",), repo=repo, now=NOW
        )
        assert outcome.status is IngestStatus.REJECTED
        await repo.save_snapshot(snapshot("imac", at=NOW))
        older = InventoryReply(
            request_id=sent.request_id, snapshot=snapshot("imac", at=NOW - timedelta(days=1))
        )
        outcome = await ingest_reply(
            older.encode(), request=sent, trusted=("imac",), repo=repo, now=NOW
        )
        assert outcome.status is IngestStatus.SUPERSEDED

    def test_freshness_follows_the_verification_time(self):
        stored = snapshot("imac", item("grilling", computer="imac"), at=NOW)
        assert remote_freshness(stored, now=NOW + timedelta(hours=1)) is Freshness.LIVE
        aged = remote_freshness(stored, now=NOW + timedelta(days=2))
        assert aged is Freshness.STALE
        assert remote_freshness(None, now=NOW) is Freshness.UNREACHABLE

    def test_an_unreachable_computer_keeps_its_last_snapshot_as_stale(self):
        stored = snapshot("imac", item("grilling", computer="imac"), at=NOW)
        kept = unreachable_snapshot("imac", previous=stored, now=NOW + timedelta(days=2))
        assert kept.freshness is Freshness.STALE
        assert kept.verified_at == NOW
        assert kept.items == stored.items
        nothing = unreachable_snapshot("imac", previous=None, now=NOW)
        assert nothing.freshness is Freshness.UNREACHABLE
        assert nothing.items == ()


# ---------------------------------------------------------------------------
# Comparison outcomes
# ---------------------------------------------------------------------------


class TestCompare:
    def test_every_outcome_is_distinguished(self):
        local = snapshot(
            "drewai",
            item("grilling", computer="drewai"),
            item("baking", computer="drewai", digest="cd" * 32),
            item("roasting", computer="drewai"),
            item("docs", computer="drewai", kind=SetupKind.PLUGIN),
            item("remote", computer="drewai", kind=SetupKind.CONNECTOR),
            item("stock", computer="drewai", classification=Classification.BUILTIN),
        )
        remote = snapshot(
            "imac",
            item("grilling", computer="imac"),
            item("baking", computer="imac", digest="ef" * 32),
            item("smoking", computer="imac"),
            item(
                "remote",
                computer="imac",
                kind=SetupKind.CONNECTOR,
                deliberate="Remote MCP needs the Team plan",
            ),
        )
        comparison = compare_snapshots(
            local,
            remote,
            remote_exceptions=(DeliberateException("plugin:", "Plugins need Max"),),
            now=NOW,
        )
        assert comparison.computer == "imac"
        assert comparison.freshness is Freshness.LIVE
        assert comparison.verified_at == NOW
        by_name = {entry.identity.name: entry for entry in comparison.results}
        assert by_name["grilling"].status is ComparisonStatus.MATCHING
        assert by_name["baking"].status is ComparisonStatus.DIFFERENT
        assert by_name["roasting"].status is ComparisonStatus.MISSING_REMOTE
        assert by_name["smoking"].status is ComparisonStatus.MISSING_LOCAL
        assert by_name["docs"].status is ComparisonStatus.DELIBERATE
        assert "Plugins need Max" in (by_name["docs"].detail or "")
        assert by_name["remote"].status is ComparisonStatus.DELIBERATE
        assert "stock" not in by_name  # built-ins are not compared by default
        assert comparison.counts[ComparisonStatus.MATCHING] == 1
        assert comparison.counts[ComparisonStatus.DELIBERATE] == 2

    def test_a_stale_remote_never_claims_current_parity(self):
        local = snapshot("drewai", item("grilling", computer="drewai"))
        remote = snapshot("imac", item("grilling", computer="imac"), at=NOW - timedelta(days=2))
        comparison = compare_snapshots(local, remote, now=NOW)
        assert comparison.freshness is Freshness.STALE
        assert comparison.verified_at == NOW - timedelta(days=2)
        result = comparison.results[0]
        assert result.status is ComparisonStatus.MATCHING
        assert result.stale is True
        assert "stale" in result.label.lower()

    def test_an_unreachable_remote_reports_every_item_unreachable(self):
        local = snapshot("drewai", item("grilling", computer="drewai"))
        comparison = compare_snapshots(local, None, now=NOW, computer="imac")
        assert comparison.freshness is Freshness.UNREACHABLE
        assert comparison.verified_at is None
        assert [entry.status for entry in comparison.results] == [ComparisonStatus.UNREACHABLE]

    def test_a_missing_fingerprint_is_not_parity(self):
        local = snapshot("drewai", replace(item("grilling", computer="drewai"), fingerprint=None))
        remote = snapshot("imac", item("grilling", computer="imac"))
        comparison = compare_snapshots(local, remote, now=NOW)
        assert comparison.results[0].status is ComparisonStatus.DIFFERENT
        assert "unknown" in (comparison.results[0].detail or "").lower()
