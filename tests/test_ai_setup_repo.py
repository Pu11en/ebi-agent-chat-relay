"""Snapshot persistence tests (task 3.1): safe facts in, the same facts out, no secrets.

The repository stores what the inventory model already guarantees is safe —
fingerprints, measurements, availability, prerequisites, diagnostics,
exceptions and verification times — and refuses anything the redactor would.
The tests read the database file back as raw text to prove that a token never
lands in it, whichever field tried to carry it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_codec import (
    CodecError,
    item_from_dict,
    item_to_dict,
    snapshot_from_dict,
    snapshot_from_json,
    snapshot_to_dict,
    snapshot_to_json,
)
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
    OwnershipClass,
    Prerequisite,
    PrerequisiteKind,
    PrerequisiteState,
    SetupKind,
)
from claude_discord.ai_setup_redaction import RedactionError
from claude_discord.database.ai_setup_repo import AISetupRepository, ObservedChange

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
EARLIER = NOW - timedelta(days=3)
LEAKED = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake, used to prove redaction


def skill(
    name: str = "grilling",
    *,
    computer: str = "drewai",
    digest: str = "ab" * 32,
    changed: datetime | None = EARLIER,
    classification: Classification = Classification.CUSTOM,
) -> InventoryItem:
    return InventoryItem(
        identity=ItemIdentity(kind=SetupKind.SKILL, source_key="claude-home", name=name),
        display_name=name,
        source=InventorySource(
            key="claude-home",
            computer=computer,
            label="Claude home",
            locator=f"~/.claude/skills/{name}/SKILL.md",
            harness="claude",
            modified_at=changed,
        ),
        scope=EffectiveScope.shared_profile("drew"),
        classification=classification,
        ownership=OwnershipClass.MEGA_GLOBAL,
        availability=(
            HarnessAvailability(
                harness="claude",
                state=AvailabilityState.VERIFIED_LOADED,
                computer=computer,
                evidence="claude --print listed the skill",
                verified_at=NOW,
            ),
            HarnessAvailability(
                harness="codex",
                state=AvailabilityState.UNSUPPORTED,
                computer=computer,
                detail="Codex does not read this location",
            ),
        ),
        prerequisites=(
            Prerequisite(
                name="GRILL_TOKEN",
                state=PrerequisiteState.MISSING,
                kind=PrerequisiteKind.CREDENTIAL,
            ),
        ),
        measurement=Measurement.estimated(byte_size=1200, character_count=1180, token_count=295),
        fingerprint=ContentFingerprint(digest=digest),
        last_changed_at=changed,
        summary="How to grill",
    )


def snapshot(
    *items: InventoryItem, computer: str = "drewai", **overrides: object
) -> InventorySnapshot:
    fields: dict[str, object] = {
        "computer": computer,
        "owner": "drew",
        "collected_at": NOW,
        "items": tuple(items),
        "diagnostics": (
            InventoryDiagnostic(
                source_key="codex-home",
                severity=DiagnosticSeverity.WARNING,
                message="The Codex home root is not present on this computer (~/.codex)",
                computer=computer,
                occurred_at=NOW,
            ),
        ),
        "source_label": "local collection",
    }
    fields.update(overrides)
    return InventorySnapshot(**fields)  # pyright: ignore[reportArgumentType]


class TestCodec:
    def test_an_item_round_trips_through_a_plain_dict(self):
        item = skill()
        data = item_to_dict(item)
        assert data["identity"] == "skill:claude-home:grilling"
        assert data["fingerprint"] == {"algorithm": "sha256", "digest": "ab" * 32}
        assert item_from_dict(data) == item

    def test_a_snapshot_round_trips_through_json(self):
        original = snapshot(skill(), skill("baking", digest="cd" * 32, changed=None))
        text = snapshot_to_json(original)
        assert snapshot_from_json(text) == original
        assert snapshot_from_dict(snapshot_to_dict(original)) == original

    def test_decoding_rejects_a_secret_and_a_wrong_shape(self):
        data = item_to_dict(skill())
        data["summary"] = f"key {LEAKED}"
        with pytest.raises(RedactionError):
            item_from_dict(data)
        with pytest.raises(CodecError):
            item_from_dict({"identity": "skill:claude-home:grilling"})
        with pytest.raises(CodecError):
            snapshot_from_json("[not a snapshot]")
        with pytest.raises(CodecError):
            snapshot_from_json('{"computer": "x"}')

    def test_no_field_can_carry_raw_content(self):
        """The dict has exactly the item's safe fields and nothing else."""
        data = item_to_dict(skill())
        assert set(data) == {
            "identity",
            "display_name",
            "source",
            "scope",
            "classification",
            "ownership",
            "availability",
            "prerequisites",
            "measurement",
            "fingerprint",
            "last_changed_at",
            "summary",
        }


@pytest.fixture
async def repo(tmp_path: Path) -> AISetupRepository:
    repository = AISetupRepository(str(tmp_path / "ai_setup.db"))
    await repository.init_db()
    return repository


def raw_database_text(path: str) -> str:
    """Every stored text and blob, so a leak is caught wherever it hid."""
    connection = sqlite3.connect(path)
    try:
        chunks: list[str] = []
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        ]
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}"):  # noqa: S608 — test only
                chunks.append(repr(row))
        return "\n".join(chunks)
    finally:
        connection.close()


class TestSnapshots:
    async def test_a_snapshot_round_trips(self, repo: AISetupRepository):
        original = snapshot(skill(), skill("baking", digest="cd" * 32, changed=None))
        await repo.save_snapshot(original)
        loaded = await repo.load_snapshot("drewai")
        assert loaded == original
        assert await repo.list_computers() == ("drewai",)

    async def test_an_unknown_computer_is_none(self, repo: AISetupRepository):
        assert await repo.load_snapshot("imac") is None
        assert await repo.list_computers() == ()

    async def test_one_snapshot_per_computer_the_newest_wins(self, repo: AISetupRepository):
        await repo.save_snapshot(snapshot(skill(), skill("baking", digest="cd" * 32)))
        later = snapshot(skill(digest="ef" * 32), collected_at=NOW + timedelta(hours=1))
        await repo.save_snapshot(later)
        loaded = await repo.load_snapshot("drewai")
        assert loaded is not None
        assert [item.identity.name for item in loaded.items] == ["grilling"]
        assert loaded.collected_at == NOW + timedelta(hours=1)
        assert loaded.items[0].fingerprint == ContentFingerprint(digest="ef" * 32)

    async def test_an_older_snapshot_never_replaces_a_newer_one(self, repo: AISetupRepository):
        newest = snapshot(skill(digest="ef" * 32), collected_at=NOW + timedelta(hours=1))
        await repo.save_snapshot(newest)
        stored = await repo.save_snapshot(snapshot(skill()))
        assert stored is False
        loaded = await repo.load_snapshot("drewai")
        assert loaded == newest

    async def test_remote_snapshots_keep_their_freshness_and_source(self, repo: AISetupRepository):
        remote = snapshot(
            skill(computer="imac"),
            computer="imac",
            freshness=Freshness.STALE,
            verified_at=EARLIER,
            source_label="trusted handoff from imac",
        )
        await repo.save_snapshot(remote)
        loaded = await repo.load_snapshot("imac")
        assert loaded == remote
        assert loaded.freshness is Freshness.STALE
        assert loaded.verified_at == EARLIER
        assert loaded.source_label == "trusted handoff from imac"

    async def test_observed_changes_are_recorded_when_a_fingerprint_moves(
        self, repo: AISetupRepository
    ):
        await repo.save_snapshot(snapshot(skill(), skill("baking", digest="cd" * 32)))
        later = snapshot(
            skill(digest="ef" * 32),
            skill("roasting", digest="12" * 32),
            collected_at=NOW + timedelta(hours=1),
        )
        await repo.save_snapshot(later)
        changes = await repo.recent_changes("drewai")
        by_key = {change.identity.key: change for change in changes}
        assert isinstance(changes[0], ObservedChange)
        assert by_key["skill:claude-home:grilling"].kind == "changed"
        assert by_key["skill:claude-home:grilling"].previous == ContentFingerprint(digest="ab" * 32)
        assert by_key["skill:claude-home:grilling"].current == ContentFingerprint(digest="ef" * 32)
        assert by_key["skill:claude-home:roasting"].kind == "added"
        assert by_key["skill:claude-home:baking"].kind == "removed"
        assert all(change.observed_at == NOW + timedelta(hours=1) for change in changes)
        assert await repo.recent_changes("drewai", limit=1) == changes[:1]

    async def test_an_unchanged_snapshot_records_no_change(self, repo: AISetupRepository):
        await repo.save_snapshot(snapshot(skill()))
        await repo.save_snapshot(snapshot(skill(), collected_at=NOW + timedelta(hours=1)))
        assert await repo.recent_changes("drewai") == ()


class TestNoSecretsStored:
    async def test_an_item_carrying_a_secret_is_refused_before_any_write(
        self, repo: AISetupRepository, tmp_path: Path
    ):
        hostile = replace(skill(), summary=f"token {LEAKED}")
        with pytest.raises(RedactionError):
            await repo.save_snapshot(snapshot(hostile))
        assert await repo.load_snapshot("drewai") is None
        assert LEAKED not in raw_database_text(repo.db_path)

    async def test_a_diagnostic_carrying_a_secret_is_scrubbed(self, repo: AISetupRepository):
        diagnostic = InventoryDiagnostic(
            source_key="claude-home",
            severity=DiagnosticSeverity.ERROR,
            message=f"settings.json could not be read: api_key={LEAKED}",
            computer="drewai",
            occurred_at=NOW,
        )
        await repo.save_snapshot(replace(snapshot(skill()), diagnostics=(diagnostic,)))
        loaded = await repo.load_snapshot("drewai")
        assert loaded is not None
        assert LEAKED not in loaded.diagnostics[0].message
        assert "[redacted]" in loaded.diagnostics[0].message
        assert LEAKED not in raw_database_text(repo.db_path)

    async def test_the_database_holds_only_safe_facts(self, repo: AISetupRepository):
        await repo.save_snapshot(snapshot(skill()))
        text = raw_database_text(repo.db_path)
        assert "ab" * 32 in text  # the one-way fingerprint is the parity fact
        assert "GRILL_TOKEN" in text  # a prerequisite is named, never valued
        assert "How to grill" in text
        assert LEAKED not in text


class TestExceptions:
    async def test_declared_exceptions_round_trip_per_computer(self, repo: AISetupRepository):
        declared = (
            DeliberateException("connector:claude-home:remote", "Remote MCP needs the Team plan"),
            DeliberateException("plugin:", "Plugins need Max", kind=PrerequisiteKind.SUBSCRIPTION),
        )
        await repo.save_exceptions("imac", declared)
        assert await repo.load_exceptions("imac") == declared
        assert await repo.load_exceptions("drewai") == ()
        await repo.save_exceptions("imac", declared[:1])
        assert await repo.load_exceptions("imac") == declared[:1]
