"""Adapter registry and collector tests for the My AI Setup inventory (task 1.3).

The collector is the join between many small adapters and one snapshot, so
these tests hold it to four promises:

* **One bad source never costs the rest.**  A raising adapter, an item carrying
  a token, an item outside an adapter's declared boundary — each is recorded as
  a per-source diagnostic while every other adapter's items still arrive.
* **Merging is by stable identity, and it never weakens a fact.**  Verified
  loading beats a guess, a missing prerequisite beats a satisfied one, a
  measured token count beats an estimate, and ``unknown`` never overwrites
  anything.
* **The six states stay distinguishable.**  custom, overridden built-in,
  unchanged built-in, discovered, configured and verified-loaded each survive
  collection as themselves.
* **Nothing raw gets through.**  Every adapter's output passes the existing
  redaction boundary before it can reach a snapshot.

Nothing here touches the filesystem or Discord: adapters are in-memory fakes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from claude_discord import ai_setup_collector as collector_module
from claude_discord.ai_setup_collector import (
    AdapterOutcome,
    AdapterRegistry,
    AdapterResult,
    CallableAdapter,
    CollectionContext,
    CollectionResult,
    InventoryCollector,
    SetupAdapter,
    merge_items,
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
    InventorySource,
    ItemIdentity,
    Measurement,
    MeasurementMethod,
    Prerequisite,
    PrerequisiteState,
    SetupKind,
)

LEAKED_TOKEN = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake, used to prove redaction
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
COMPUTER = "drewai"
OWNER = "drew"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def context(**overrides: object) -> CollectionContext:
    defaults: dict[str, object] = {
        "computer": COMPUTER,
        "owner": OWNER,
        "collected_at": NOW,
    }
    defaults.update(overrides)
    return CollectionContext(**defaults)  # pyright: ignore[reportArgumentType]


def source(key: str = "claude-home", *, modified_at: datetime | None = NOW) -> InventorySource:
    return InventorySource(
        key=key,
        computer=COMPUTER,
        label="Claude home",
        locator="~/.claude/skills",
        modified_at=modified_at,
    )


def item(
    name: str = "grilling",
    *,
    kind: SetupKind = SetupKind.SKILL,
    source_key: str = "claude-home",
    classification: Classification = Classification.CUSTOM,
    **overrides: object,
) -> InventoryItem:
    defaults: dict[str, object] = {
        "identity": ItemIdentity(kind=kind, source_key=source_key, name=name),
        "display_name": name,
        "source": source(source_key),
        "scope": EffectiveScope.everywhere(),
        "classification": classification,
    }
    defaults.update(overrides)
    return InventoryItem(**defaults)  # pyright: ignore[reportArgumentType]


def adapter(
    name: str,
    *results: InventoryItem,
    kinds: tuple[SetupKind, ...] = (SetupKind.SKILL,),
    source_keys: tuple[str, ...] = ("claude-home",),
    diagnostics: tuple[object, ...] = (),
    error: BaseException | None = None,
) -> CallableAdapter:
    def gather(_: CollectionContext) -> AdapterResult:
        if error is not None:
            raise error
        return AdapterResult(items=results, diagnostics=diagnostics)  # pyright: ignore[reportArgumentType]

    return CallableAdapter(name=name, kinds=kinds, source_keys=source_keys, gather=gather)


def collect(*adapters: SetupAdapter, ctx: CollectionContext | None = None) -> CollectionResult:
    collector = InventoryCollector()
    for entry in adapters:
        collector.register(entry)
    return collector.collect(ctx or context())


def discovered(harness: str = "claude") -> HarnessAvailability:
    return HarnessAvailability(harness=harness, state=AvailabilityState.DISCOVERED)


def verified(harness: str = "claude", *, at: datetime = NOW) -> HarnessAvailability:
    return HarnessAvailability(
        harness=harness,
        state=AvailabilityState.VERIFIED_LOADED,
        computer=COMPUTER,
        evidence="claude --list-skills resolved source set",
        verified_at=at,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_registers_normalizes_and_lists_adapters() -> None:
    registry = AdapterRegistry()
    registration = registry.register(adapter("Claude Home"))

    assert registration.name == "claude-home"
    assert registry.adapter_names == ("claude-home",)
    assert "claude-home" in registry
    assert len(registry) == 1
    assert registry.get("Claude Home") is registration
    assert registry.kinds == frozenset({SetupKind.SKILL})
    assert registry.source_keys == frozenset({"claude-home"})


def test_registry_rejects_a_duplicate_adapter_name() -> None:
    registry = AdapterRegistry()
    registry.register(adapter("claude-home"))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(adapter("claude-home"))


def test_registry_requires_declared_kinds_and_source_keys() -> None:
    registry = AdapterRegistry()

    with pytest.raises(ValueError, match="at least one setup kind"):
        registry.register(adapter("no-kinds", kinds=()))
    with pytest.raises(ValueError, match="at least one source key"):
        registry.register(adapter("no-sources", source_keys=()))


def test_registry_finds_adapters_by_kind() -> None:
    registry = AdapterRegistry()
    skills = registry.register(adapter("skills", kinds=(SetupKind.SKILL,)))
    hooks = registry.register(
        adapter("hooks", kinds=(SetupKind.HOOK,), source_keys=("claude-settings",))
    )

    assert registry.for_kind(SetupKind.SKILL) == (skills,)
    assert registry.for_kind(SetupKind.HOOK) == (hooks,)
    assert registry.for_kind(SetupKind.CONNECTOR) == ()


def test_callable_adapter_satisfies_the_adapter_protocol() -> None:
    assert isinstance(adapter("claude-home"), SetupAdapter)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def test_context_normalizes_tokens_and_requires_an_aware_time() -> None:
    ctx = context(computer="DrewAI", owner="Drew", harnesses=("Claude", "codex"))

    assert (ctx.computer, ctx.owner) == ("drewai", "drew")
    assert ctx.harnesses == ("claude", "codex")
    assert ctx.supports("Claude") is True
    assert ctx.supports("dsh") is False

    with pytest.raises(ValueError, match="collection time"):
        context(collected_at=datetime(2026, 9, 20, 12, 0))  # noqa: DTZ001 — deliberately naive


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------


def test_collect_puts_the_context_facts_on_a_live_snapshot() -> None:
    result = collect(adapter("claude-home", item()))
    snapshot = result.snapshot

    assert (snapshot.computer, snapshot.owner) == (COMPUTER, OWNER)
    assert snapshot.collected_at == NOW
    assert snapshot.verified_at == NOW
    assert snapshot.freshness is Freshness.LIVE
    assert [entry.identity.name for entry in snapshot.items] == ["grilling"]
    assert result.is_complete is True


def test_collect_with_no_adapters_returns_an_empty_live_snapshot() -> None:
    result = InventoryCollector().collect(context())

    assert result.snapshot.items == ()
    assert result.snapshot.diagnostics == ()
    assert result.outcomes == ()
    assert result.is_complete is True


def test_collect_keeps_adapter_supplied_diagnostics() -> None:
    from claude_discord.ai_setup_redaction import safe_diagnostic

    note = safe_diagnostic("claude-home", DiagnosticSeverity.WARNING, "One folder was unreadable")
    result = collect(adapter("claude-home", item(), diagnostics=(note,)))

    kept = result.snapshot.diagnostics
    assert [(entry.source_key, entry.severity, entry.message) for entry in kept] == [
        (note.source_key, note.severity, note.message)
    ]
    assert kept[0].computer == COMPUTER  # stamped by the run when the adapter left it out
    assert result.has_errors is False
    assert result.is_complete is False


def test_adapter_supplied_diagnostics_are_redacted_before_they_are_stored() -> None:
    """An adapter may hand over a raw error string; it must not reach a snapshot as-is."""
    raw = InventoryDiagnostic(
        source_key="claude-home",
        severity=DiagnosticSeverity.ERROR,
        message=f"connector rejected credential {LEAKED_TOKEN}",
    )
    result = collect(adapter("claude-home", item(), diagnostics=(raw,)))

    stored = result.snapshot.diagnostics
    assert len(stored) == 1
    assert LEAKED_TOKEN not in stored[0].message
    assert "connector rejected credential" in stored[0].message
    assert stored[0].severity is DiagnosticSeverity.ERROR
    assert stored[0].source_key == "claude-home"
    assert stored[0].computer == COMPUTER
    assert stored[0].occurred_at == NOW
    outcome = result.outcome("claude-home")
    assert outcome is not None
    assert LEAKED_TOKEN not in outcome.diagnostics[0].message


def test_adapter_diagnostics_that_are_not_diagnostics_are_reported_not_stored() -> None:
    result = collect(adapter("claude-home", item(), diagnostics=("just a string",)))

    assert all(isinstance(entry, InventoryDiagnostic) for entry in result.snapshot.diagnostics)
    assert any("not an inventory diagnostic" in entry.message for entry in result.diagnostics)


# ---------------------------------------------------------------------------
# One bad source never costs the rest
# ---------------------------------------------------------------------------


def test_a_raising_adapter_does_not_abort_the_collection() -> None:
    result = collect(
        adapter("broken", error=RuntimeError("settings.json is not valid JSON")),
        adapter("claude-home", item()),
    )

    assert [entry.identity.name for entry in result.snapshot.items] == ["grilling"]
    assert result.failed_adapters == ("broken",)
    assert result.has_errors is True

    outcome = result.outcome("broken")
    assert outcome is not None
    assert outcome.failed is True
    assert outcome.reported == 0
    assert [entry.severity for entry in outcome.diagnostics] == [DiagnosticSeverity.ERROR]
    assert "settings.json is not valid JSON" in outcome.diagnostics[0].message
    assert outcome.diagnostics[0].computer == COMPUTER
    assert outcome.diagnostics[0].occurred_at == NOW

    healthy = result.outcome("claude-home")
    assert healthy is not None
    assert (healthy.failed, healthy.reported, healthy.accepted) == (False, 1, 1)


def test_an_adapter_failure_message_is_redacted() -> None:
    result = collect(adapter("broken", error=RuntimeError(f"auth failed for {LEAKED_TOKEN}")))

    message = result.snapshot.diagnostics[0].message
    assert LEAKED_TOKEN not in message
    assert "redacted" in message


def test_an_adapter_returning_the_wrong_type_is_reported_not_raised() -> None:
    broken = CallableAdapter(
        name="broken",
        kinds=(SetupKind.SKILL,),
        source_keys=("claude-home",),
        gather=lambda _: "not a result",  # pyright: ignore[reportArgumentType,reportUnknownLambdaType]
    )

    result = collect(broken, adapter("claude-home", item()))

    assert result.failed_adapters == ("broken",)
    assert len(result.snapshot.items) == 1


def test_an_item_carrying_a_secret_is_withheld_and_the_source_is_reported() -> None:
    result = collect(
        adapter(
            "claude-home",
            item("leaky", summary=f"connector configured with {LEAKED_TOKEN}"),
            item("grilling"),
        )
    )

    assert [entry.identity.name for entry in result.snapshot.items] == ["grilling"]
    outcome = result.outcome("claude-home")
    assert outcome is not None
    assert (outcome.reported, outcome.accepted, outcome.withheld) == (2, 1, 1)

    errors = [entry for entry in result.snapshot.diagnostics if entry.is_error]
    assert len(errors) == 1
    assert LEAKED_TOKEN not in errors[0].message
    assert errors[0].identity is not None
    assert errors[0].identity.name == "leaky"


def test_an_item_outside_the_declared_kinds_is_withheld() -> None:
    result = collect(adapter("claude-home", item("pre-tool-use", kind=SetupKind.HOOK)))

    assert result.snapshot.items == ()
    assert result.has_errors is True
    assert "declared kinds" in result.snapshot.diagnostics[0].message


def test_an_item_outside_the_declared_source_keys_is_withheld() -> None:
    result = collect(adapter("claude-home", item("grilling", source_key="codex-home")))

    assert result.snapshot.items == ()
    assert "declared sources" in result.snapshot.diagnostics[0].message


def test_an_item_collected_from_another_computer_is_withheld() -> None:
    elsewhere = replace(source(), computer="imac")
    result = collect(adapter("claude-home", item("grilling", source=elsewhere)))

    assert result.snapshot.items == ()
    assert "another computer" in result.snapshot.diagnostics[0].message


# ---------------------------------------------------------------------------
# The six states stay distinguishable
# ---------------------------------------------------------------------------


def test_a_custom_item_reported_alone_stays_custom() -> None:
    result = collect(adapter("claude-home", item()))
    only = result.snapshot.items[0]

    assert only.classification is Classification.CUSTOM
    assert result.snapshot.custom_items == (only,)


def test_an_unchanged_builtin_stays_builtin_and_is_hidden_by_default() -> None:
    result = collect(adapter("claude-builtin", item("docs", classification=Classification.BUILTIN)))
    only = result.snapshot.items[0]

    assert only.classification is Classification.BUILTIN
    assert result.snapshot.visible_items() == ()
    assert result.snapshot.visible_items(include_builtins=True) == (only,)
    assert result.snapshot.counts_by_classification()[Classification.CUSTOM] == 0


def test_a_custom_copy_of_a_builtin_identity_becomes_an_overridden_builtin() -> None:
    stock = adapter("claude-builtin", item("docs", classification=Classification.BUILTIN))
    mine = adapter("claude-home", item("docs", classification=Classification.CUSTOM))

    result = collect(stock, mine)

    assert len(result.snapshot.items) == 1
    merged = result.snapshot.items[0]
    assert merged.classification is Classification.OVERRIDDEN_BUILTIN
    assert merged.is_custom is True
    assert result.snapshot.visible_items() == (merged,)


def test_an_adapter_declared_override_is_preserved() -> None:
    result = collect(
        adapter("claude-home", item("docs", classification=Classification.OVERRIDDEN_BUILTIN))
    )

    assert result.snapshot.items[0].classification is Classification.OVERRIDDEN_BUILTIN


def test_discovered_configured_and_verified_loaded_are_kept_apart() -> None:
    entries = (
        HarnessAvailability(harness="claude", state=AvailabilityState.DISCOVERED),
        HarnessAvailability(harness="codex", state=AvailabilityState.CONFIGURED),
        verified("dsh"),
    )
    result = collect(adapter("claude-home", item(availability=entries)))
    merged = result.snapshot.items[0]

    assert merged.state_for("claude") is AvailabilityState.DISCOVERED
    assert merged.state_for("codex") is AvailabilityState.CONFIGURED
    assert merged.state_for("dsh") is AvailabilityState.VERIFIED_LOADED
    assert merged.verified_harnesses == ("dsh",)
    assert merged.state_for("gemini") is AvailabilityState.UNKNOWN


# ---------------------------------------------------------------------------
# Merging never weakens a fact
# ---------------------------------------------------------------------------


def test_verified_loading_beats_a_discovered_guess_for_the_same_harness() -> None:
    guess = adapter(
        "claude-home",
        item(availability=(discovered("claude"),)),
    )
    proof = adapter("claude-loader", item(availability=(verified("claude"),)))

    merged = collect(guess, proof).snapshot.items[0]

    assert merged.is_verified_on("claude") is True
    assert merged.availability_for("claude") is not None
    assert merged.availability_for("claude").verified_at == NOW  # pyright: ignore[reportOptionalMemberAccess]
    assert len(merged.availability) == 1


def test_an_unsupported_harness_is_not_downgraded_by_a_readable_file() -> None:
    unsupported = HarnessAvailability(
        harness="codex", state=AvailabilityState.UNSUPPORTED, detail="Codex has no hook loader"
    )
    result = collect(
        adapter("codex-loader", item(availability=(unsupported,))),
        adapter(
            "claude-home",
            item(
                availability=(
                    HarnessAvailability(harness="codex", state=AvailabilityState.DISCOVERED),
                )
            ),
        ),
    )

    assert result.snapshot.items[0].state_for("codex") is AvailabilityState.UNSUPPORTED


def test_unknown_never_overwrites_a_known_availability() -> None:
    result = collect(
        adapter("claude-loader", item(availability=(verified("claude"),))),
        adapter(
            "claude-home",
            item(availability=(HarnessAvailability(harness="claude"),)),
        ),
    )

    assert result.snapshot.items[0].state_for("claude") is AvailabilityState.VERIFIED_LOADED


def test_the_newer_verification_wins_when_two_sources_agree_on_the_state() -> None:
    later = NOW + timedelta(hours=2)
    result = collect(
        adapter("claude-home", item(availability=(verified("claude"),))),
        adapter("claude-loader", item(availability=(verified("claude", at=later),))),
    )

    assert result.snapshot.items[0].availability_for("claude").verified_at == later  # pyright: ignore[reportOptionalMemberAccess]


def test_a_missing_prerequisite_beats_a_satisfied_one() -> None:
    result = collect(
        adapter(
            "claude-home",
            item(prerequisites=(Prerequisite.credential("GMAIL_TOKEN", present=True),)),
        ),
        adapter(
            "claude-loader",
            item(prerequisites=(Prerequisite.credential("GMAIL_TOKEN", present=False),)),
        ),
    )
    merged = result.snapshot.items[0]

    assert len(merged.prerequisites) == 1
    assert merged.prerequisites[0].state is PrerequisiteState.MISSING
    assert len(merged.missing_prerequisites) == 1


def test_an_unknown_prerequisite_does_not_hide_a_satisfied_one() -> None:
    result = collect(
        adapter(
            "claude-home",
            item(prerequisites=(Prerequisite.credential("GMAIL_TOKEN", present=True),)),
        ),
        adapter(
            "claude-loader",
            item(prerequisites=(Prerequisite.credential("GMAIL_TOKEN", present=None),)),
        ),
    )

    assert result.snapshot.items[0].prerequisites[0].state is PrerequisiteState.SATISFIED


def test_a_measured_token_count_beats_an_estimate_and_sizes_are_kept() -> None:
    estimate = Measurement.estimated(token_count=900)
    measured = Measurement.measured(byte_size=4096, token_count=1024, tokenizer="claude-tokenizer")
    result = collect(
        adapter("claude-home", item(measurement=estimate)),
        adapter("claude-loader", item(measurement=measured)),
    )
    merged = result.snapshot.items[0].measurement

    assert merged.token_method is MeasurementMethod.MEASURED
    assert merged.token_count == 1024
    assert merged.tokenizer == "claude-tokenizer"
    assert merged.byte_size == 4096


def test_an_unknown_measurement_does_not_erase_a_known_one() -> None:
    result = collect(
        adapter("claude-home", item(measurement=Measurement.estimated(token_count=900))),
        adapter("claude-loader", item(measurement=Measurement.unknown())),
    )

    assert result.snapshot.items[0].measurement.token_count == 900


def test_the_newest_known_change_time_wins_and_undated_facts_are_kept() -> None:
    later = NOW + timedelta(days=1)
    result = collect(
        adapter("claude-home", item(last_changed_at=NOW)),
        adapter("claude-loader", item(last_changed_at=None)),
        adapter("claude-audit", item(last_changed_at=later)),
    )

    assert result.snapshot.items[0].last_changed_at == later
    assert result.snapshot.undated_items == ()


def test_the_custom_source_is_preferred_over_the_builtin_one_when_merging() -> None:
    stock_source = InventorySource(
        key="claude-builtin",
        computer=COMPUTER,
        label="Shipped with Claude Code",
        locator="<bundled>",
    )
    stock = adapter(
        "claude-builtin",
        item("docs", classification=Classification.BUILTIN, source=stock_source),
        source_keys=("claude-home",),
    )
    mine = adapter("claude-home", item("docs", summary="My own copy"))

    merged = collect(stock, mine).snapshot.items[0]

    assert merged.source.key == "claude-home"
    assert merged.summary == "My own copy"


def test_two_sources_disagreeing_on_content_is_a_warning_not_a_silent_choice() -> None:
    mine = ContentFingerprint.of_text("my version")
    theirs = ContentFingerprint.of_text("their version")
    result = collect(
        adapter("claude-home", item(fingerprint=mine)),
        adapter("claude-loader", item(fingerprint=theirs)),
    )

    assert result.snapshot.items[0].fingerprint == mine
    warnings = [
        entry
        for entry in result.snapshot.diagnostics
        if entry.severity is DiagnosticSeverity.WARNING
    ]
    assert len(warnings) == 1
    assert "disagree" in warnings[0].message
    assert warnings[0].identity is not None


def test_matching_fingerprints_produce_no_diagnostic() -> None:
    same = ContentFingerprint.of_text("one version")
    result = collect(
        adapter("claude-home", item(fingerprint=same)),
        adapter("claude-loader", item(fingerprint=same)),
    )

    assert result.snapshot.diagnostics == ()
    assert result.snapshot.items[0].fingerprint == same


def test_one_adapter_reporting_an_identity_twice_is_merged_not_fatal() -> None:
    twice = adapter(
        "claude-home",
        item(availability=(discovered("claude"),)),
        item(availability=(verified("claude"),)),
    )

    result = collect(twice)

    assert len(result.snapshot.items) == 1
    assert result.snapshot.items[0].is_verified_on("claude") is True
    outcome = result.outcome("claude-home")
    assert outcome is not None
    assert (outcome.reported, outcome.accepted, outcome.merged) == (2, 2, 1)


def test_merge_items_is_usable_on_its_own_and_keeps_registration_order() -> None:
    merged, diagnostics = merge_items(
        [item("grilling"), item("research"), item("grilling", last_changed_at=NOW)],
        computer=COMPUTER,
        occurred_at=NOW,
    )

    assert [entry.identity.name for entry in merged] == ["grilling", "research"]
    assert merged[0].last_changed_at == NOW
    assert diagnostics == ()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_outcomes_report_per_adapter_counts_in_registration_order() -> None:
    result = collect(
        adapter("claude-home", item("grilling"), item("research")),
        adapter("claude-loader", item("grilling", availability=(verified(),))),
    )

    assert [entry.adapter for entry in result.outcomes] == ["claude-home", "claude-loader"]
    assert [entry.accepted for entry in result.outcomes] == [2, 1]
    assert all(isinstance(entry, AdapterOutcome) for entry in result.outcomes)
    assert result.outcome("missing") is None
    assert len(result.snapshot.items) == 2


def test_result_exposes_items_and_diagnostics_directly() -> None:
    result = collect(adapter("claude-home", item()))

    assert result.items == result.snapshot.items
    assert result.diagnostics == result.snapshot.diagnostics


def test_diagnostics_are_addressable_by_source_key() -> None:
    result = collect(
        adapter("broken", error=RuntimeError("unreadable"), source_keys=("codex-home",)),
        adapter("claude-home", item()),
    )

    assert len(result.snapshot.diagnostics_for("codex-home")) == 1
    assert result.snapshot.diagnostics_for("claude-home") == ()


# ---------------------------------------------------------------------------
# Boundaries this module must not cross yet
# ---------------------------------------------------------------------------


def test_the_collector_reads_no_files_and_knows_nothing_about_discord() -> None:
    from pathlib import Path

    text = Path(collector_module.__file__).read_text(encoding="utf-8")

    for forbidden in ("import discord", "from pathlib", "import os", "open("):
        assert forbidden not in text, f"the collector must not use {forbidden!r} yet"
