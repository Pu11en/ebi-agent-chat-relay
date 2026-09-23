"""Contract tests for the harness-audit evidence schema (task 1.1).

Every later collector, rule and report speaks this vocabulary, so the tests
here pin the three properties the rest of the audit depends on:

* an inventory item is only *effective* when the evidence says ``loaded``;
* serialization round trips byte-for-byte and refuses a payload whose shape is
  not recognized; and
* explicit ``unknown`` evidence survives every hop instead of quietly turning
  into a pass, a failure, or a deletion.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from extensions.harness_audit.models import (
    APPROVED_TARGETS,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    AuditCheck,
    AuditTarget,
    CheckOutcome,
    Classification,
    ClassificationRecord,
    CoverageGap,
    EvidenceLevel,
    EvidenceRecord,
    ExceptionKind,
    Finding,
    Harness,
    HarnessInventory,
    InventoryItem,
    ItemSource,
    Machine,
    MachineException,
    RedactionStatus,
    SchemaError,
    Scope,
    Severity,
    SizeMeasurement,
    SourceKind,
    TokenCountKind,
    VendorCitation,
    from_json,
    to_json,
)

COLLECTED_AT = datetime(2026, 9, 20, 2, 30, tzinfo=UTC)
DREWAI_CLAUDE = AuditTarget(Machine.DREWAI, Harness.CLAUDE)
IMAC_CODEX = AuditTarget(Machine.IMAC, Harness.CODEX)


def loaded_evidence(detail: str = "read from resolved ~/AGENTS.md symlink") -> EvidenceRecord:
    return EvidenceRecord(
        level=EvidenceLevel.LOADED,
        method="resolved-symlink",
        detail=detail,
        source_reference="~/.claude/CLAUDE.md",
    )


def unknown_evidence() -> EvidenceRecord:
    return EvidenceRecord(
        level=EvidenceLevel.UNKNOWN,
        method="cli-version-unrecognized",
        detail="codex 0.900.0 writes an unfamiliar config layout",
        missing_evidence=("effective load order",),
    )


def sample_item(item_id: str = "claude-global-agents-md") -> InventoryItem:
    return InventoryItem(
        item_id=item_id,
        target=DREWAI_CLAUDE,
        kind=SourceKind.GLOBAL_INSTRUCTIONS,
        label="global AGENTS.md",
        sources=(
            ItemSource(
                reference="~/.claude/CLAUDE.md",
                scope=Scope.GLOBAL,
                evidence=loaded_evidence(),
                precedence=1,
            ),
        ),
        evidence=loaded_evidence(),
        size=SizeMeasurement(
            byte_size=8192,
            characters=8110,
            token_count=2100,
            token_count_kind=TokenCountKind.ESTIMATED,
            token_method="chars/4 heuristic",
        ),
        permissions="0644",
        content_hash="sha256:" + "a" * 64,
    )


class TestTargets:
    def test_four_approved_targets(self) -> None:
        assert len(APPROVED_TARGETS) == 4
        assert set(APPROVED_TARGETS) == {
            AuditTarget(machine, harness) for machine in Machine for harness in Harness
        }

    def test_key_round_trips(self) -> None:
        assert DREWAI_CLAUDE.key == "drewai/claude"
        assert AuditTarget.from_key("imac/codex") == IMAC_CODEX

    def test_unknown_target_key_is_rejected(self) -> None:
        with pytest.raises(SchemaError):
            AuditTarget.from_key("thinkpad/claude")
        with pytest.raises(SchemaError):
            AuditTarget.from_key("drewai")


class TestEvidenceLevels:
    def test_only_loaded_is_effective(self) -> None:
        assert EvidenceLevel.LOADED.is_effective
        for level in (
            EvidenceLevel.CONFIGURED,
            EvidenceLevel.INSTALLED_ONLY,
            EvidenceLevel.UNKNOWN,
        ):
            assert not level.is_effective

    def test_unknown_evidence_must_name_what_is_missing(self) -> None:
        with pytest.raises(SchemaError):
            EvidenceRecord(
                level=EvidenceLevel.UNKNOWN,
                method="cli-version-unrecognized",
                detail="unfamiliar layout",
            )

    def test_known_evidence_cannot_carry_missing_evidence(self) -> None:
        with pytest.raises(SchemaError):
            EvidenceRecord(
                level=EvidenceLevel.LOADED,
                method="argv",
                detail="present on the command line",
                missing_evidence=("load order",),
            )

    def test_unknown_evidence_survives_serialization(self) -> None:
        record = unknown_evidence()
        restored = EvidenceRecord.from_dict(record.to_dict())
        assert restored == record
        assert restored.level is EvidenceLevel.UNKNOWN
        assert restored.missing_evidence == ("effective load order",)

    def test_method_and_detail_are_required(self) -> None:
        with pytest.raises(SchemaError):
            EvidenceRecord(level=EvidenceLevel.LOADED, method=" ", detail="x")
        with pytest.raises(SchemaError):
            EvidenceRecord(level=EvidenceLevel.LOADED, method="argv", detail="")


class TestSizeMeasurement:
    def test_exact_size_is_kept_and_estimate_is_labeled(self) -> None:
        size = sample_item().size
        assert size is not None
        assert size.byte_size == 8192
        assert size.characters == 8110
        assert "estimated" in size.label
        assert "not provider billing data" in size.label

    def test_token_count_requires_a_named_method(self) -> None:
        with pytest.raises(SchemaError):
            SizeMeasurement(
                byte_size=10,
                characters=10,
                token_count=3,
                token_count_kind=TokenCountKind.ESTIMATED,
                token_method="",
            )

    def test_unknown_token_kind_forbids_a_count(self) -> None:
        with pytest.raises(SchemaError):
            SizeMeasurement(byte_size=10, characters=10, token_count=3)

    def test_unknown_token_kind_round_trips(self) -> None:
        size = SizeMeasurement(byte_size=10, characters=10)
        assert SizeMeasurement.from_dict(size.to_dict()) == size
        assert size.token_count is None

    def test_negative_sizes_are_rejected(self) -> None:
        with pytest.raises(SchemaError):
            SizeMeasurement(byte_size=-1, characters=0)


class TestInventoryItem:
    def test_round_trip_is_deterministic(self) -> None:
        item = sample_item()
        payload = to_json(item)
        restored = from_json(InventoryItem, payload)
        assert restored == item
        assert to_json(restored) == payload

    def test_loaded_item_is_effective(self) -> None:
        assert sample_item().is_effective

    def test_installed_only_item_is_not_effective(self) -> None:
        evidence = EvidenceRecord(
            level=EvidenceLevel.INSTALLED_ONLY,
            method="directory-listing",
            detail="skill folder exists; no target loads it",
            source_reference="~/.agents/skills/grilling",
        )
        item = InventoryItem(
            item_id="skill-grilling",
            target=DREWAI_CLAUDE,
            kind=SourceKind.SKILL,
            label="grilling skill",
            sources=(
                ItemSource(
                    reference="~/.agents/skills/grilling",
                    scope=Scope.GLOBAL,
                    evidence=evidence,
                ),
            ),
            evidence=evidence,
        )
        assert not item.is_effective
        assert InventoryItem.from_dict(item.to_dict()).evidence.level is (
            EvidenceLevel.INSTALLED_ONLY
        )

    def test_builtin_material_requires_loaded_evidence(self) -> None:
        evidence = EvidenceRecord(
            level=EvidenceLevel.CONFIGURED,
            method="settings-file",
            detail="listed in settings.json",
        )
        with pytest.raises(SchemaError):
            InventoryItem(
                item_id="builtin-tool-bash",
                target=DREWAI_CLAUDE,
                kind=SourceKind.TOOL,
                label="Bash tool",
                sources=(
                    ItemSource(reference="builtin:bash", scope=Scope.HARNESS, evidence=evidence),
                ),
                evidence=evidence,
                is_builtin=True,
            )

    def test_multiple_sources_keep_scope_precedence_and_effective_behavior(self) -> None:
        item = InventoryItem(
            item_id="safety-rules",
            target=DREWAI_CLAUDE,
            kind=SourceKind.GLOBAL_INSTRUCTIONS,
            label="safety rules",
            sources=(
                ItemSource(
                    reference="~/AGENTS.md",
                    scope=Scope.GLOBAL,
                    evidence=loaded_evidence(),
                    precedence=2,
                ),
                ItemSource(
                    reference="project/AGENTS.md",
                    scope=Scope.PROJECT,
                    evidence=loaded_evidence("project file read at session start"),
                    precedence=1,
                ),
                ItemSource(
                    reference="bot:append-system-prompt",
                    scope=Scope.SESSION,
                    evidence=unknown_evidence(),
                ),
            ),
            evidence=loaded_evidence(),
            effective_behavior="project copy wins; the bot prompt's precedence is unknown",
        )
        assert item.effective_source.reference == "project/AGENTS.md"
        assert item.scope is Scope.PROJECT
        assert [source.precedence for source in item.sources] == [2, 1, None]
        assert InventoryItem.from_dict(item.to_dict()) == item

    def test_several_sources_require_stated_effective_behavior(self) -> None:
        with pytest.raises(SchemaError):
            InventoryItem(
                item_id="safety-rules",
                target=DREWAI_CLAUDE,
                kind=SourceKind.GLOBAL_INSTRUCTIONS,
                label="safety rules",
                sources=(
                    ItemSource(
                        reference="~/AGENTS.md", scope=Scope.GLOBAL, evidence=loaded_evidence()
                    ),
                    ItemSource(
                        reference="project/AGENTS.md",
                        scope=Scope.PROJECT,
                        evidence=loaded_evidence(),
                    ),
                ),
                evidence=loaded_evidence(),
            )

    def test_item_needs_at_least_one_source(self) -> None:
        with pytest.raises(SchemaError):
            InventoryItem(
                item_id="orphan",
                target=DREWAI_CLAUDE,
                kind=SourceKind.HOOK,
                label="orphan hook",
                sources=(),
                evidence=loaded_evidence(),
            )

    def test_unknown_field_is_rejected(self) -> None:
        payload = sample_item().to_dict()
        payload["secret_value"] = "sk-live-123"
        with pytest.raises(SchemaError, match="secret_value"):
            InventoryItem.from_dict(payload)

    def test_missing_required_field_is_rejected(self) -> None:
        payload = sample_item().to_dict()
        del payload["evidence"]
        with pytest.raises(SchemaError, match="evidence"):
            InventoryItem.from_dict(payload)

    def test_unrecognized_collector_data_is_preserved_explicitly(self) -> None:
        item = InventoryItem(
            item_id="codex-native-setting",
            target=IMAC_CODEX,
            kind=SourceKind.HARNESS_SETTING,
            label="codex config.toml",
            sources=(
                ItemSource(
                    reference="~/.codex/config.toml",
                    scope=Scope.HARNESS,
                    evidence=loaded_evidence("parsed by the codex CLI at startup"),
                ),
            ),
            evidence=loaded_evidence("parsed by the codex CLI at startup"),
            redaction=RedactionStatus.REDACTED,
            unrecognized={"future_key": "value kept verbatim for a newer CLI"},
        )
        restored = InventoryItem.from_dict(item.to_dict())
        assert restored.unrecognized["future_key"] == "value kept verbatim for a newer CLI"
        assert restored == item

    def test_unrecognized_bucket_is_immutable(self) -> None:
        item = sample_item()
        with pytest.raises(TypeError):
            item.unrecognized["injected"] = "no"  # type: ignore[index]


class TestHarnessInventory:
    def _inventory(self) -> HarnessInventory:
        return HarnessInventory(
            target=DREWAI_CLAUDE,
            collected_at=COLLECTED_AT,
            items=(sample_item("zz-last"), sample_item("aa-first")),
        )

    def test_inventory_is_versioned(self) -> None:
        inventory = self._inventory()
        assert inventory.schema_version == SCHEMA_VERSION
        assert SCHEMA_VERSION in SUPPORTED_SCHEMA_VERSIONS
        assert inventory.to_dict()["schema_version"] == SCHEMA_VERSION

    def test_unsupported_schema_version_is_rejected(self) -> None:
        payload = self._inventory().to_dict()
        payload["schema_version"] = 999
        with pytest.raises(SchemaError, match="schema_version"):
            HarnessInventory.from_dict(payload)

    def test_items_are_ordered_deterministically(self) -> None:
        inventory = self._inventory()
        assert [item.item_id for item in inventory.items] == ["aa-first", "zz-last"]
        assert to_json(inventory) == to_json(from_json(HarnessInventory, to_json(inventory)))

    def test_duplicate_item_ids_are_rejected(self) -> None:
        with pytest.raises(SchemaError, match="duplicate"):
            HarnessInventory(
                target=DREWAI_CLAUDE,
                collected_at=COLLECTED_AT,
                items=(sample_item(), sample_item()),
            )

    def test_items_must_belong_to_the_inventory_target(self) -> None:
        stray = InventoryItem(
            item_id="stray",
            target=IMAC_CODEX,
            kind=SourceKind.SKILL,
            label="stray",
            sources=(ItemSource(reference="x", scope=Scope.GLOBAL, evidence=loaded_evidence()),),
            evidence=loaded_evidence(),
        )
        with pytest.raises(SchemaError, match="target"):
            HarnessInventory(target=DREWAI_CLAUDE, collected_at=COLLECTED_AT, items=(stray,))

    def test_effective_items_exclude_installed_only(self) -> None:
        configured = EvidenceRecord(
            level=EvidenceLevel.CONFIGURED,
            method="settings-file",
            detail="declared but never observed loading",
        )
        item = InventoryItem(
            item_id="bb-configured",
            target=DREWAI_CLAUDE,
            kind=SourceKind.HOOK,
            label="hook",
            sources=(
                ItemSource(
                    reference="~/.claude/settings.json", scope=Scope.HARNESS, evidence=configured
                ),
            ),
            evidence=configured,
        )
        inventory = HarnessInventory(
            target=DREWAI_CLAUDE,
            collected_at=COLLECTED_AT,
            items=(sample_item("aa-first"), item),
        )
        assert [i.item_id for i in inventory.effective_items] == ["aa-first"]

    def test_collected_at_must_be_timezone_aware(self) -> None:
        with pytest.raises(SchemaError, match="time zone"):
            HarnessInventory(
                target=DREWAI_CLAUDE,
                collected_at=datetime(2026, 9, 20, 2, 30),  # noqa: DTZ001
                items=(),
            )


class TestVendorCitation:
    def _citation(self) -> VendorCitation:
        return VendorCitation(
            source_id="claude-code-memory",
            url="https://docs.claude.com/en/docs/claude-code/memory",
            retrieved_on=date(2026, 9, 20),
            applies_to_versions=">=2.0.0",
            quoted_proposition="CLAUDE.md files load from the project tree upward.",
        )

    def test_round_trip(self) -> None:
        citation = self._citation()
        assert VendorCitation.from_dict(citation.to_dict()) == citation

    def test_citation_needs_pinned_content(self) -> None:
        with pytest.raises(SchemaError):
            VendorCitation(
                source_id="claude-code-memory",
                url="https://docs.claude.com/en/docs/claude-code/memory",
                retrieved_on=date(2026, 9, 20),
            )

    def test_non_official_scheme_is_rejected(self) -> None:
        with pytest.raises(SchemaError):
            VendorCitation(
                source_id="blog",
                url="http://example.com/blog",
                retrieved_on=date(2026, 9, 20),
                content_hash="sha256:" + "b" * 64,
            )


class TestFinding:
    def _duplicate_finding(self) -> Finding:
        return Finding(
            finding_id="dup-agents-md",
            check=AuditCheck.DUPLICATION,
            outcome=CheckOutcome.FAIL,
            severity=Severity.MEDIUM,
            summary="the same safety rule is maintained in two loaded files",
            item_ids=("claude-global-agents-md", "codex-global-agents-md"),
            targets=(IMAC_CODEX, DREWAI_CLAUDE),
            evidence=(loaded_evidence(), loaded_evidence("codex reads the same bytes")),
            comparison_method="normalized-content-sha256",
            duplicated_bytes=8192,
        )

    def test_round_trip_and_sorted_targets(self) -> None:
        finding = self._duplicate_finding()
        assert [target.key for target in finding.targets] == ["drewai/claude", "imac/codex"]
        payload = to_json(finding)
        assert to_json(from_json(Finding, payload)) == payload

    def test_duplication_finding_needs_comparison_method_and_two_items(self) -> None:
        with pytest.raises(SchemaError, match="comparison"):
            Finding(
                finding_id="dup",
                check=AuditCheck.DUPLICATION,
                outcome=CheckOutcome.FAIL,
                severity=Severity.LOW,
                summary="duplicate",
                item_ids=("a", "b"),
                targets=(DREWAI_CLAUDE,),
                evidence=(loaded_evidence(),),
            )
        with pytest.raises(SchemaError, match="two"):
            Finding(
                finding_id="dup",
                check=AuditCheck.DUPLICATION,
                outcome=CheckOutcome.FAIL,
                severity=Severity.LOW,
                summary="duplicate",
                item_ids=("a",),
                targets=(DREWAI_CLAUDE,),
                evidence=(loaded_evidence(),),
                comparison_method="normalized-content-sha256",
            )

    def test_finding_requires_evidence(self) -> None:
        with pytest.raises(SchemaError, match="evidence"):
            Finding(
                finding_id="empty",
                check=AuditCheck.PERMISSIONS,
                outcome=CheckOutcome.FAIL,
                severity=Severity.HIGH,
                summary="world-writable global file",
                item_ids=("a",),
                targets=(DREWAI_CLAUDE,),
                evidence=(),
            )

    def test_vendor_dependent_finding_requires_a_citation(self) -> None:
        vendor_evidence = EvidenceRecord(
            level=EvidenceLevel.LOADED,
            method="load-order-comparison",
            detail="project file overrides the global file",
            depends_on_vendor_behavior=True,
        )
        with pytest.raises(SchemaError, match="vendor"):
            Finding(
                finding_id="precedence",
                check=AuditCheck.PRECEDENCE,
                outcome=CheckOutcome.FAIL,
                severity=Severity.MEDIUM,
                summary="global rule is shadowed",
                item_ids=("a",),
                targets=(DREWAI_CLAUDE,),
                evidence=(vendor_evidence,),
            )
        finding = Finding(
            finding_id="precedence",
            check=AuditCheck.PRECEDENCE,
            outcome=CheckOutcome.FAIL,
            severity=Severity.MEDIUM,
            summary="global rule is shadowed",
            item_ids=("a",),
            targets=(DREWAI_CLAUDE,),
            evidence=(vendor_evidence,),
            vendor_sources=(
                VendorCitation(
                    source_id="claude-code-memory",
                    url="https://docs.claude.com/en/docs/claude-code/memory",
                    retrieved_on=date(2026, 9, 20),
                    content_hash="sha256:" + "c" * 64,
                ),
            ),
        )
        assert Finding.from_dict(finding.to_dict()) == finding

    def test_unknown_outcome_is_preserved_with_its_missing_evidence(self) -> None:
        finding = Finding(
            finding_id="load-unknown",
            check=AuditCheck.LOAD_BEHAVIOR,
            outcome=CheckOutcome.UNKNOWN,
            severity=Severity.INFO,
            summary="cannot tell whether the hook runs for Discord sessions",
            item_ids=("hook-x",),
            targets=(IMAC_CODEX,),
            evidence=(unknown_evidence(),),
            missing_evidence=("a session rollout from this machine",),
        )
        restored = Finding.from_dict(finding.to_dict())
        assert restored.outcome is CheckOutcome.UNKNOWN
        assert restored.missing_evidence == ("a session rollout from this machine",)

    def test_unknown_outcome_must_say_what_is_missing(self) -> None:
        with pytest.raises(SchemaError, match="missing_evidence"):
            Finding(
                finding_id="load-unknown",
                check=AuditCheck.LOAD_BEHAVIOR,
                outcome=CheckOutcome.UNKNOWN,
                severity=Severity.INFO,
                summary="cannot tell",
                item_ids=("hook-x",),
                targets=(IMAC_CODEX,),
                evidence=(unknown_evidence(),),
            )

    def test_settled_outcome_cannot_claim_missing_evidence(self) -> None:
        with pytest.raises(SchemaError, match="missing_evidence"):
            Finding(
                finding_id="perm",
                check=AuditCheck.PERMISSIONS,
                outcome=CheckOutcome.PASS,
                severity=Severity.INFO,
                summary="permissions are fine",
                item_ids=("a",),
                targets=(DREWAI_CLAUDE,),
                evidence=(loaded_evidence(),),
                missing_evidence=("something",),
            )


class TestClassification:
    def test_exactly_five_classifications(self) -> None:
        assert [c.value for c in Classification] == [
            "keep",
            "fix",
            "move-to-project",
            "load-only-when-needed",
            "remove",
        ]
        assert [c for c in Classification if c.is_destructive] == [Classification.REMOVE]

    def _record(self, **overrides: object) -> ClassificationRecord:
        defaults: dict[str, object] = {
            "item_id": "claude-global-agents-md",
            "classification": Classification.KEEP,
            "reason": "portable rules that both harnesses load",
            "evidence": (loaded_evidence(),),
            "affected_targets": (DREWAI_CLAUDE,),
            "proposed_scope": Scope.GLOBAL,
            "risk": "none; the file stays where it is",
            "reversible_action": "no change; re-run the audit after any edit",
        }
        defaults.update(overrides)
        return ClassificationRecord(**defaults)  # type: ignore[arg-type]

    def test_round_trip(self) -> None:
        record = self._record()
        payload = to_json(record)
        assert to_json(from_json(ClassificationRecord, payload)) == payload

    def test_remove_is_impossible_with_unknown_evidence(self) -> None:
        with pytest.raises(SchemaError, match="[Rr]emove"):
            self._record(
                classification=Classification.REMOVE,
                evidence=(loaded_evidence(), unknown_evidence()),
                uncertainty="the codex load path could not be read",
            )

    def test_unknown_evidence_must_record_its_uncertainty(self) -> None:
        with pytest.raises(SchemaError, match="uncertainty"):
            self._record(classification=Classification.FIX, evidence=(unknown_evidence(),))

    def test_keep_with_unknown_evidence_is_allowed(self) -> None:
        record = self._record(
            classification=Classification.KEEP,
            evidence=(unknown_evidence(),),
            uncertainty="cannot prove the hook is loaded on the iMac",
        )
        assert record.classification is Classification.KEEP
        assert ClassificationRecord.from_dict(record.to_dict()) == record

    def test_load_on_demand_needs_an_activation_boundary(self) -> None:
        with pytest.raises(SchemaError, match="activation"):
            self._record(classification=Classification.LOAD_ON_DEMAND)
        record = self._record(
            classification=Classification.LOAD_ON_DEMAND,
            activation_boundary="only when a LinkedIn skill is invoked",
        )
        assert record.activation_boundary == "only when a LinkedIn skill is invoked"

    def test_move_to_project_must_propose_project_scope(self) -> None:
        with pytest.raises(SchemaError, match="project"):
            self._record(classification=Classification.MOVE_TO_PROJECT, proposed_scope=Scope.GLOBAL)

    def test_reversible_action_and_affected_targets_are_required(self) -> None:
        with pytest.raises(SchemaError, match="reversible"):
            self._record(reversible_action="  ")
        with pytest.raises(SchemaError, match="affected_targets"):
            self._record(affected_targets=())


class TestMachineException:
    def _exception(self) -> MachineException:
        return MachineException(
            exception_id="imac-no-opus-5",
            machine=Machine.IMAC,
            kind=ExceptionKind.MODEL_AVAILABILITY,
            description="the iMac subscription cannot select Opus 5",
            preserved_outcome="both machines still answer with the same rules and skills",
            evidence=loaded_evidence("model catalog read from the local CLI cache"),
            harness=Harness.CODEX,
        )

    def test_round_trip_and_not_a_parity_failure(self) -> None:
        exception = self._exception()
        assert not exception.is_parity_failure
        assert MachineException.from_dict(exception.to_dict()) == exception

    def test_exception_may_apply_to_both_harnesses(self) -> None:
        exception = MachineException(
            exception_id="imac-macos-paths",
            machine=Machine.IMAC,
            kind=ExceptionKind.OPERATING_SYSTEM,
            description="macOS home paths differ",
            preserved_outcome="the same canonical files load under a different absolute path",
            evidence=loaded_evidence("resolved path recorded by the collector"),
        )
        assert exception.harness is None
        assert MachineException.from_dict(exception.to_dict()).harness is None

    def test_preserved_outcome_is_required(self) -> None:
        with pytest.raises(SchemaError, match="preserved_outcome"):
            MachineException(
                exception_id="imac-no-opus-5",
                machine=Machine.IMAC,
                kind=ExceptionKind.SUBSCRIPTION,
                description="different plan",
                preserved_outcome="",
                evidence=loaded_evidence(),
            )

    def test_unknown_field_is_rejected(self) -> None:
        payload = self._exception().to_dict()
        payload["machine_token"] = "imac"
        with pytest.raises(SchemaError, match="machine_token"):
            MachineException.from_dict(payload)


class TestCoverageGap:
    def _gap(self) -> CoverageGap:
        return CoverageGap(
            target=IMAC_CODEX,
            reason="the iMac was offline during collection",
            missing_evidence=("native config", "session rollout metadata"),
            observed_at=COLLECTED_AT,
        )

    def test_round_trip_and_named_target(self) -> None:
        gap = self._gap()
        assert gap.target.key == "imac/codex"
        assert not gap.claims_full_coverage
        assert CoverageGap.from_dict(gap.to_dict()) == gap

    def test_gap_must_name_the_missing_evidence(self) -> None:
        with pytest.raises(SchemaError, match="missing_evidence"):
            CoverageGap(
                target=IMAC_CODEX,
                reason="offline",
                missing_evidence=(),
                observed_at=COLLECTED_AT,
            )

    def test_observed_at_must_be_timezone_aware(self) -> None:
        with pytest.raises(SchemaError, match="time zone"):
            CoverageGap(
                target=IMAC_CODEX,
                reason="offline",
                missing_evidence=("native config",),
                observed_at=datetime(2026, 9, 20),  # noqa: DTZ001
            )


class TestSerializationHelpers:
    def test_json_is_stable_across_repeated_dumps(self) -> None:
        item = sample_item()
        assert to_json(item) == to_json(item)

    def test_json_key_order_follows_the_schema_not_insertion(self) -> None:
        payload = sample_item().to_dict()
        shuffled = dict(reversed(list(payload.items())))
        assert to_json(InventoryItem.from_dict(shuffled)) == to_json(sample_item())

    def test_from_json_rejects_a_non_object_payload(self) -> None:
        with pytest.raises(SchemaError):
            from_json(InventoryItem, "[]")

    def test_wrong_type_for_a_field_is_rejected(self) -> None:
        payload = sample_item().to_dict()
        payload["label"] = 12
        with pytest.raises(SchemaError, match="label"):
            InventoryItem.from_dict(payload)

    def test_enum_value_outside_the_schema_is_rejected(self) -> None:
        payload = sample_item().to_dict()
        payload["kind"] = "telepathy"
        with pytest.raises(SchemaError, match="kind"):
            InventoryItem.from_dict(payload)
