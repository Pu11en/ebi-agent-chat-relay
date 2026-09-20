"""Domain model tests for the My AI Setup inventory (OpenSpec task 1.1).

These cover stable identities, every approved kind and scope, the configured /
discovered / verified-loaded / unsupported / stale / unreachable / unknown
states, and the structural guarantee that no raw content or secret value can
enter the model.  Adapters (tasks 2.x) and the collector (task 1.3) are absent
here: nothing in this module touches the filesystem.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime, timedelta

import pytest

from claude_discord import ai_setup_inventory as inventory
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
    SetupKind,
    normalize_token,
    safe_text,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

APPROVED_KINDS = {
    "preference",
    "instruction",
    "memory",
    "skill",
    "tool",
    "plugin",
    "connector",
    "command",
    "hook",
    "harness_setting",
}

APPROVED_SCOPES = {"everywhere", "shared_profile", "computer", "other_profile", "project"}

APPROVED_AVAILABILITY = {
    "configured",
    "discovered",
    "verified_loaded",
    "unsupported",
    "missing_prerequisite",
    "stale",
    "unreachable",
    "unknown",
}


def make_source(
    key: str = "claude-home",
    *,
    computer: str = "drewai",
    locator: str = "~/.claude/skills/grilling",
    harness: str | None = "claude",
    modified_at: datetime | None = None,
) -> InventorySource:
    return InventorySource(
        key=key,
        computer=computer,
        label="Claude home configuration",
        locator=locator,
        harness=harness,
        modified_at=modified_at,
    )


def make_item(
    name: str = "grilling",
    *,
    kind: SetupKind = SetupKind.SKILL,
    computer: str = "drewai",
    classification: Classification = Classification.CUSTOM,
    scope: EffectiveScope | None = None,
    availability: tuple[HarnessAvailability, ...] = (),
    fingerprint: ContentFingerprint | None = None,
    last_changed_at: datetime | None = None,
    measurement: Measurement | None = None,
) -> InventoryItem:
    return InventoryItem(
        identity=ItemIdentity(kind=kind, source_key="claude-home", name=name),
        display_name=name,
        source=make_source(computer=computer),
        scope=scope or EffectiveScope.shared_profile("drew"),
        classification=classification,
        availability=availability,
        fingerprint=fingerprint,
        last_changed_at=last_changed_at,
        measurement=measurement or Measurement.unknown(),
    )


def verified(harness: str = "claude", *, computer: str = "drewai") -> HarnessAvailability:
    return HarnessAvailability(
        harness=harness,
        state=AvailabilityState.VERIFIED_LOADED,
        computer=computer,
        evidence="loader resolved source set",
        verified_at=NOW,
    )


# --- tokens and safe text -------------------------------------------------


def test_normalize_token_is_portable_and_lowercase():
    assert normalize_token("  DrewAI ", kind="computer") == "drewai"
    assert normalize_token("Claude Code", kind="harness") == "claude-code"


def test_normalize_token_rejects_empty():
    with pytest.raises(ValueError, match="harness"):
        normalize_token("   ", kind="harness")


def test_safe_text_rejects_embedded_content():
    with pytest.raises(ValueError, match="single line"):
        safe_text("api_key=abc\nmore config", kind="locator")


def test_safe_text_rejects_overlong_values():
    with pytest.raises(ValueError, match="too long"):
        safe_text("x" * 5000, kind="label", limit=100)


# --- kinds and scopes -----------------------------------------------------


def test_every_approved_kind_exists():
    assert {kind.value for kind in SetupKind} == APPROVED_KINDS


def test_every_kind_has_a_human_label():
    for kind in SetupKind:
        assert kind.label and "_" not in kind.label


def test_every_approved_scope_exists():
    assert {scope.value for scope in ScopeKind} == APPROVED_SCOPES


def test_scope_labels_name_the_user_facing_scopes():
    assert EffectiveScope.everywhere().label == "Everywhere"
    assert EffectiveScope.shared_profile("drew").label == "Shared Drew profile"
    assert EffectiveScope.other_profile("david").label == "David's profile"
    assert "imac" in EffectiveScope.one_computer("imac").label.lower()
    assert "ccdb" in EffectiveScope.one_project("ccdb").label


def test_scope_requires_its_qualifier():
    with pytest.raises(ValueError, match="owner"):
        EffectiveScope(kind=ScopeKind.SHARED_PROFILE)
    with pytest.raises(ValueError, match="computer"):
        EffectiveScope(kind=ScopeKind.COMPUTER)
    with pytest.raises(ValueError, match="project"):
        EffectiveScope(kind=ScopeKind.PROJECT)


def test_internal_ownership_never_reaches_a_user_facing_label():
    mapped = [
        EffectiveScope.for_ownership(OwnershipClass.MEGA_GLOBAL),
        EffectiveScope.for_ownership(
            OwnershipClass.MEGA_GLOBAL, owner="drew", primary_owner="drew"
        ),
        EffectiveScope.for_ownership(OwnershipClass.PROFILE, owner="drew", primary_owner="drew"),
        EffectiveScope.for_ownership(OwnershipClass.PROFILE, owner="david", primary_owner="drew"),
        EffectiveScope.for_ownership(OwnershipClass.MACHINE, computer="imac"),
        EffectiveScope.for_ownership(OwnershipClass.PROJECT, project="ccdb"),
    ]
    assert [scope.kind for scope in mapped] == [
        ScopeKind.EVERYWHERE,
        ScopeKind.SHARED_PROFILE,
        ScopeKind.SHARED_PROFILE,
        ScopeKind.OTHER_PROFILE,
        ScopeKind.COMPUTER,
        ScopeKind.PROJECT,
    ]
    for scope in mapped:
        assert "mega" not in scope.label.lower()


# --- identity -------------------------------------------------------------


def test_identity_key_round_trips():
    identity = ItemIdentity(kind=SetupKind.SKILL, source_key="Claude Home", name="no-ai-slop")
    assert identity.key == "skill:claude-home:no-ai-slop"
    assert ItemIdentity.from_key(identity.key) == identity
    assert str(identity) == identity.key


def test_identity_keeps_name_spelling_and_allows_separators():
    identity = ItemIdentity(kind=SetupKind.COMMAND, source_key="claude-home", name="Deploy:Prod")
    assert identity.name == "Deploy:Prod"
    assert ItemIdentity.from_key(identity.key).name == "Deploy:Prod"


def test_identity_rejects_empty_name():
    with pytest.raises(ValueError, match="name"):
        ItemIdentity(kind=SetupKind.SKILL, source_key="claude-home", name="  ")


def test_identity_is_computer_free_so_computers_can_be_compared():
    here = make_item(computer="drewai")
    there = make_item(computer="imac")
    assert here.identity == there.identity
    assert here.computer != there.computer


def test_malformed_identity_key_is_rejected():
    with pytest.raises(ValueError, match="Malformed"):
        ItemIdentity.from_key("skill:claude-home")


# --- sources and fingerprints --------------------------------------------


def test_source_rejects_a_locator_carrying_configuration():
    with pytest.raises(ValueError, match="single line"):
        make_source(locator="~/.claude/settings.json\nANTHROPIC_API_KEY=sk-live-123")


def test_source_modification_time_must_be_timezone_aware():
    with pytest.raises(ValueError, match="time zone"):
        make_source(modified_at=datetime(2026, 9, 1, 10, 0))


def test_fingerprint_is_one_way_and_comparable():
    first = ContentFingerprint.of_text("secret instructions")
    same = ContentFingerprint.of_text("secret instructions")
    other = ContentFingerprint.of_text("different instructions")
    assert first == same
    assert first.matches(same)
    assert not first.matches(other)
    assert "secret" not in first.digest
    assert first.algorithm == "sha256"
    assert len(first.short) == 12


def test_fingerprint_rejects_a_non_digest_value():
    with pytest.raises(ValueError, match="digest"):
        ContentFingerprint(digest="not-a-digest")


# --- availability states --------------------------------------------------


def test_every_approved_availability_state_exists():
    assert {state.value for state in AvailabilityState} == APPROVED_AVAILABILITY


def test_verified_loaded_requires_deterministic_evidence():
    with pytest.raises(ValueError, match="evidence"):
        HarnessAvailability(harness="claude", state=AvailabilityState.VERIFIED_LOADED)
    with pytest.raises(ValueError, match="evidence"):
        HarnessAvailability(
            harness="claude",
            state=AvailabilityState.VERIFIED_LOADED,
            evidence="loader resolved source set",
        )


def test_a_readable_file_is_only_discovered_not_verified():
    discovered = HarnessAvailability(harness="codex", state=AvailabilityState.DISCOVERED)
    configured = HarnessAvailability(harness="codex", state=AvailabilityState.CONFIGURED)
    assert not discovered.is_verified
    assert not configured.is_verified
    assert verified("codex").is_verified


def test_stale_availability_must_carry_its_last_verification_time():
    with pytest.raises(ValueError, match="verification time"):
        HarnessAvailability(harness="claude", state=AvailabilityState.STALE, computer="imac")
    stale = verified(computer="imac").mark_stale()
    assert stale.state is AvailabilityState.STALE
    assert stale.verified_at == NOW
    assert not stale.is_verified


def test_unreachable_availability_needs_no_prior_verification():
    unreachable = HarnessAvailability(
        harness="claude", state=AvailabilityState.UNREACHABLE, computer="david-pc"
    )
    assert unreachable.verified_at is None
    assert unreachable.state.is_remote_problem


def test_unsupported_item_stays_inventoried():
    item = make_item(
        availability=(
            verified("claude"),
            HarnessAvailability(harness="dsh", state=AvailabilityState.UNSUPPORTED),
        )
    )
    snapshot = InventorySnapshot(computer="drewai", owner="drew", collected_at=NOW, items=(item,))
    assert snapshot.items_by_kind()[SetupKind.SKILL] == (item,)
    assert item.state_for("dsh") is AvailabilityState.UNSUPPORTED
    assert item.verified_harnesses == ("claude",)


def test_unknown_is_the_answer_for_an_unreported_harness():
    item = make_item(availability=(verified("claude"),))
    assert item.state_for("codex") is AvailabilityState.UNKNOWN
    assert item.availability_for("codex") is None
    assert not item.is_verified_on("codex")


def test_duplicate_harness_availability_is_rejected():
    with pytest.raises(ValueError, match="more than once"):
        make_item(availability=(verified("claude"), verified("claude")))


def test_with_availability_replaces_the_same_harness():
    item = make_item(availability=(verified("claude"),))
    updated = item.with_availability(
        HarnessAvailability(harness="claude", state=AvailabilityState.MISSING_PREREQUISITE),
        HarnessAvailability(harness="codex", state=AvailabilityState.DISCOVERED),
    )
    assert updated.state_for("claude") is AvailabilityState.MISSING_PREREQUISITE
    assert updated.state_for("codex") is AvailabilityState.DISCOVERED
    assert item.state_for("claude") is AvailabilityState.VERIFIED_LOADED


# --- prerequisites --------------------------------------------------------


def test_credential_prerequisite_reports_presence_without_the_value():
    present = Prerequisite.credential("GitHub token")
    missing = Prerequisite.credential("Porkbun API key", present=False)
    unknown = Prerequisite.credential("Notion secret", present=None)
    assert present.state is PrerequisiteState.SATISFIED
    assert missing.state is PrerequisiteState.MISSING
    assert unknown.state is PrerequisiteState.UNKNOWN
    assert present.kind is PrerequisiteKind.CREDENTIAL
    assert {f.name for f in fields(Prerequisite)} == {"name", "state", "kind", "detail"}


def test_missing_prerequisites_are_reported_on_the_item():
    item = make_item()
    item = item.with_prerequisites(
        Prerequisite.credential("Porkbun API key", present=False),
        Prerequisite(name="codex CLI", state=PrerequisiteState.SATISFIED),
    )
    assert [p.name for p in item.missing_prerequisites] == ["Porkbun API key"]


# --- measurements ---------------------------------------------------------


def test_unknown_measurement_invents_no_token_count():
    unknown = Measurement.unknown()
    assert unknown.token_count is None
    assert unknown.token_method is MeasurementMethod.UNKNOWN
    assert unknown.token_label == "unknown"
    assert unknown.size_label == "unknown"


def test_unknown_token_method_cannot_carry_a_count():
    with pytest.raises(ValueError, match="unknown"):
        Measurement(token_count=1200, token_method=MeasurementMethod.UNKNOWN)


def test_measured_tokens_must_name_their_tokenizer():
    with pytest.raises(ValueError, match="tokenizer"):
        Measurement(token_count=1200, token_method=MeasurementMethod.MEASURED)
    measured = Measurement.measured(byte_size=4096, token_count=1200, tokenizer="cl100k_base")
    assert "cl100k_base" in measured.token_label
    assert measured.size_label == "4,096 bytes"


def test_estimated_tokens_are_labelled_as_estimates():
    estimated = Measurement.estimated(byte_size=4096, token_count=1024, model="claude-opus-5")
    assert estimated.token_label.startswith("~")
    assert "estimate" in estimated.token_label
    assert estimated.model == "claude-opus-5"


def test_measurement_rejects_negative_sizes():
    with pytest.raises(ValueError, match="negative"):
        Measurement(byte_size=-1)


# --- classification -------------------------------------------------------


def test_custom_and_overridden_items_are_the_default_view():
    assert Classification.CUSTOM.is_custom_view
    assert Classification.OVERRIDDEN_BUILTIN.is_custom_view
    assert not Classification.BUILTIN.is_custom_view


def test_builtins_do_not_change_the_custom_counts():
    custom = make_item("grilling")
    override = make_item("research", classification=Classification.OVERRIDDEN_BUILTIN)
    builtin = make_item("init", classification=Classification.BUILTIN)
    snapshot = InventorySnapshot(
        computer="drewai",
        owner="drew",
        collected_at=NOW,
        items=(custom, override, builtin),
    )
    counts = snapshot.counts_by_classification()
    assert counts[Classification.CUSTOM] == 1
    assert counts[Classification.OVERRIDDEN_BUILTIN] == 1
    assert counts[Classification.BUILTIN] == 1
    assert snapshot.custom_items == (custom, override)
    assert snapshot.builtin_items == (builtin,)
    assert snapshot.items_by_kind()[SetupKind.SKILL] == (custom, override)
    assert len(snapshot.items_by_kind(include_builtins=True)[SetupKind.SKILL]) == 3


# --- items ----------------------------------------------------------------


def test_items_are_immutable():
    item = make_item()
    with pytest.raises(FrozenInstanceError):
        item.display_name = "renamed"  # type: ignore[misc]


def test_no_model_type_can_hold_raw_content_or_a_secret_value():
    forbidden = {
        "content",
        "raw",
        "raw_content",
        "body",
        "text",
        "value",
        "values",
        "secret",
        "secrets",
        "password",
        "credential",
        "credentials",
        "api_key",
        "env",
        "environment",
    }
    checked = 0
    for name in dir(inventory):
        candidate = getattr(inventory, name)
        if not isinstance(candidate, type) or not is_dataclass(candidate):
            continue
        checked += 1
        assert not forbidden & {f.name for f in fields(candidate)}, name
    assert checked >= 8


def test_two_computers_are_aligned_only_on_an_equal_fingerprint():
    digest = ContentFingerprint.of_text("# shared instructions")
    here = make_item(computer="drewai", fingerprint=digest)
    there = make_item(computer="imac", fingerprint=digest)
    different = make_item(computer="imac", fingerprint=ContentFingerprint.of_text("# edited"))
    unknown = make_item(computer="imac")
    assert here.is_aligned_with(there)
    assert not here.is_aligned_with(different)
    assert not here.is_aligned_with(unknown)


def test_item_exposes_its_kind_source_computer_and_scope():
    item = make_item(scope=EffectiveScope.one_project("ccdb"))
    assert item.kind is SetupKind.SKILL
    assert item.computer == "drewai"
    assert item.scope.kind is ScopeKind.PROJECT
    assert item.is_custom


# --- diagnostics ----------------------------------------------------------


def test_diagnostic_message_cannot_smuggle_file_content():
    with pytest.raises(ValueError, match="single line"):
        InventoryDiagnostic(
            source_key="claude-home",
            severity=DiagnosticSeverity.ERROR,
            message="parse failed:\nANTHROPIC_API_KEY=sk-live-123",
        )


def test_item_level_diagnostic_points_at_its_item():
    item = make_item()
    diagnostic = InventoryDiagnostic(
        source_key="claude-home",
        severity=DiagnosticSeverity.ERROR,
        message="skill front matter could not be parsed",
        identity=item.identity,
        occurred_at=NOW,
    )
    assert diagnostic.is_error
    assert diagnostic.affects_item
    snapshot = InventorySnapshot(
        computer="drewai",
        owner="drew",
        collected_at=NOW,
        items=(item,),
        diagnostics=(diagnostic,),
    )
    assert snapshot.diagnostics_for("claude-home") == (diagnostic,)


# --- snapshots ------------------------------------------------------------


def test_snapshot_collection_time_must_be_timezone_aware():
    with pytest.raises(ValueError, match="time zone"):
        InventorySnapshot(computer="drewai", owner="drew", collected_at=datetime(2026, 9, 20))


def test_snapshot_verification_time_defaults_to_collection_time():
    snapshot = InventorySnapshot(computer="drewai", owner="drew", collected_at=NOW)
    assert snapshot.verified_at == NOW
    assert snapshot.freshness is Freshness.LIVE
    assert snapshot.is_current


def test_snapshot_rejects_two_items_with_the_same_identity():
    with pytest.raises(ValueError, match="identity"):
        InventorySnapshot(
            computer="drewai",
            owner="drew",
            collected_at=NOW,
            items=(make_item("grilling"), make_item("grilling")),
        )


def test_an_aged_snapshot_becomes_stale_rather_than_current():
    snapshot = InventorySnapshot(
        computer="imac",
        owner="drew",
        collected_at=NOW,
        items=(make_item(computer="imac"),),
        source_label="trusted handoff from imac",
    )
    fresh = snapshot.aged(NOW + timedelta(minutes=5), max_age=timedelta(hours=1))
    stale = snapshot.aged(NOW + timedelta(hours=9), max_age=timedelta(hours=1))
    assert fresh is snapshot
    assert stale.freshness is Freshness.STALE
    assert not stale.is_current
    assert stale.verified_at == NOW
    assert stale.items == snapshot.items


def test_an_unreachable_snapshot_carries_no_items_to_mistake_for_live_state():
    unreachable = InventorySnapshot(
        computer="david-pc",
        owner="david",
        collected_at=NOW,
        freshness=Freshness.UNREACHABLE,
    )
    assert unreachable.items == ()
    with pytest.raises(ValueError, match="unreachable"):
        InventorySnapshot(
            computer="david-pc",
            owner="david",
            collected_at=NOW,
            freshness=Freshness.UNREACHABLE,
            items=(make_item(computer="david-pc"),),
        )


def test_recent_changes_separates_items_with_unknown_times():
    older = make_item("grilling", last_changed_at=NOW - timedelta(days=3))
    newer = make_item("research", last_changed_at=NOW - timedelta(hours=2))
    undated = make_item("no-ai-slop")
    snapshot = InventorySnapshot(
        computer="drewai",
        owner="drew",
        collected_at=NOW,
        items=(older, newer, undated),
    )
    assert snapshot.recent_changes() == (newer, older)
    assert snapshot.recent_changes(limit=1) == (newer,)
    assert snapshot.undated_items == (undated,)


def test_snapshot_groups_items_by_user_facing_scope():
    everywhere = make_item("agents-md", scope=EffectiveScope.everywhere())
    project = make_item("project-notes", scope=EffectiveScope.one_project("ccdb"))
    snapshot = InventorySnapshot(
        computer="drewai",
        owner="drew",
        collected_at=NOW,
        items=(everywhere, project),
    )
    grouped = snapshot.items_by_scope()
    assert grouped[ScopeKind.EVERYWHERE] == (everywhere,)
    assert grouped[ScopeKind.PROJECT] == (project,)
    assert "mega" not in " ".join(scope.name for scope in grouped).lower()


def test_snapshot_finds_an_item_by_identity():
    item = make_item()
    snapshot = InventorySnapshot(computer="drewai", owner="drew", collected_at=NOW, items=(item,))
    assert snapshot.item(item.identity) is item
    assert snapshot.item(ItemIdentity(SetupKind.HOOK, "claude-home", "missing")) is None
