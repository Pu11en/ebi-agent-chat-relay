"""Redaction-boundary tests for the professional harness audit (task 1.2).

The audit reads personal configuration on two computers and then hands a
bundle to the other one, so this module's job is narrow and absolute: a
credential, a cookie, a private key, an environment value or a private prompt
body must never reach a serialized bundle, while the metadata the audit
actually reasons about — identifiers, sizes, permissions and hashes — must
survive intact and still be useful.

The tests below therefore pin four properties:

* **Secrets die at the boundary.**  Every fixture secret is checked against the
  final serialized bytes, not against an intermediate object.
* **Safe metadata survives.**  A redactor that deletes everything would pass a
  leak test and be useless, so the same bundle is checked for the identifiers,
  sizes, permissions and hashes the rules need.
* **Redaction is idempotent.**  Redacted text scans clean, so the fail-closed
  check at serialization time cannot fire on its own output.
* **It fails closed.**  A bundle assembled without redaction refuses to
  serialize rather than emitting a best-effort payload.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from extensions.harness_audit.models import (
    SCHEMA_VERSION,
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
    Scope,
    Severity,
    SizeMeasurement,
    SourceKind,
    TokenCountKind,
    VendorCitation,
    to_json,
)
from extensions.harness_audit.redaction import (
    SECRET_PLACEHOLDER,
    WITHHELD_PLACEHOLDER,
    PrivateBody,
    RedactedBundle,
    RedactionError,
    RedactionNote,
    assert_no_secrets,
    build_redacted_bundle,
    measure,
    parse_bundle,
    redact_classification,
    redact_coverage_gap,
    redact_environment,
    redact_finding,
    redact_inventory,
    redact_item,
    redact_machine_exception,
    redact_text,
    safe_hash,
    scan,
    serialize_bundle,
    withhold_body,
)

COLLECTED_AT = datetime(2026, 9, 20, 2, 30, tzinfo=UTC)
DREWAI_CLAUDE = AuditTarget(Machine.DREWAI, Harness.CLAUDE)
DREWAI_CODEX = AuditTarget(Machine.DREWAI, Harness.CODEX)

# Fake credentials. None of these is a real secret; they exist so the tests can
# assert that these exact strings never appear in a serialized bundle.
FAKE_API_KEY = "sk-ant-api03-ZZZZfakefakefakefake1234567890"
FAKE_GITHUB_TOKEN = "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.c2lnbmF0dXJlZmFrZQ"
FAKE_COOKIE = "session_id=QWERTYfake123; theme=dark"
FAKE_ENV_SECRET = "hunter2hunter2hunter2"
FAKE_PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----"
)
FAKE_PRIVATE_BODY = "Drew's private prompt body that must never leave this machine."

ALL_FAKE_SECRETS = (
    FAKE_API_KEY,
    FAKE_GITHUB_TOKEN,
    FAKE_JWT,
    "QWERTYfake123",
    FAKE_ENV_SECRET,
    "MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefake",
    FAKE_PRIVATE_BODY,
)


def loaded_evidence(detail: str, *, reference: str = "~/.claude/CLAUDE.md") -> EvidenceRecord:
    return EvidenceRecord(
        level=EvidenceLevel.LOADED,
        method="resolved-symlink",
        detail=detail,
        source_reference=reference,
    )


def secret_item() -> InventoryItem:
    """An item whose every free-text field carries a different fixture secret."""
    return InventoryItem(
        item_id="claude-global-agents-md",
        target=DREWAI_CLAUDE,
        kind=SourceKind.GLOBAL_INSTRUCTIONS,
        label=f"global AGENTS.md (api_key={FAKE_API_KEY})",
        sources=(
            ItemSource(
                reference=f"https://drew:{FAKE_ENV_SECRET}@example.invalid/agents.md",
                scope=Scope.GLOBAL,
                evidence=loaded_evidence(f"Authorization: Bearer {FAKE_JWT}"),
                precedence=1,
            ),
        ),
        evidence=loaded_evidence(f"Cookie: {FAKE_COOKIE}"),
        size=SizeMeasurement(
            byte_size=4096,
            characters=4000,
            token_count=1000,
            token_count_kind=TokenCountKind.ESTIMATED,
            token_method="characters/4 heuristic",
        ),
        permissions="-rw-------",
        content_hash="sha256:" + "ab" * 32,
        effective_behavior="",
        unrecognized={"future_field": FAKE_GITHUB_TOKEN},
    )


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #


def test_safe_hash_is_stable_and_salted() -> None:
    first = safe_hash(FAKE_PRIVATE_BODY)
    assert first == safe_hash(FAKE_PRIVATE_BODY.encode())
    assert first.startswith("sha256:")
    assert FAKE_PRIVATE_BODY not in first
    assert safe_hash(FAKE_PRIVATE_BODY, salt="drewai") != first
    assert safe_hash(FAKE_PRIVATE_BODY, salt="drewai") == safe_hash(
        FAKE_PRIVATE_BODY, salt="drewai"
    )


def test_measure_reports_exact_size_and_labels_any_token_estimate() -> None:
    exact = measure("héllo")
    assert exact.characters == 5
    assert exact.byte_size == 6
    assert exact.token_count_kind is TokenCountKind.UNKNOWN
    assert exact.token_count is None

    estimated = measure("x" * 400, estimate_tokens=True)
    assert estimated.token_count_kind is TokenCountKind.ESTIMATED
    assert estimated.token_count == 100
    assert "not provider billing data" in estimated.label


def test_withhold_body_keeps_size_and_hash_but_never_the_body() -> None:
    body = withhold_body(FAKE_PRIVATE_BODY, field="claude.system_prompt", salt="drewai")
    assert body.redaction is RedactionStatus.WITHHELD
    assert body.size.characters == len(FAKE_PRIVATE_BODY)
    assert body.content_hash == safe_hash(FAKE_PRIVATE_BODY, salt="drewai")
    assert FAKE_PRIVATE_BODY not in to_json(body)
    assert PrivateBody.from_dict(body.to_dict()) == body


def test_withhold_body_refuses_to_claim_nothing_was_hidden() -> None:
    with pytest.raises(RedactionError):
        PrivateBody(
            field="claude.system_prompt",
            size=measure(FAKE_PRIVATE_BODY),
            content_hash=safe_hash(FAKE_PRIVATE_BODY),
            redaction=RedactionStatus.NONE_NEEDED,
        )


# --------------------------------------------------------------------------- #
# Text rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"ANTHROPIC_API_KEY={FAKE_API_KEY}", FAKE_API_KEY),
        (f'"client_secret": "{FAKE_ENV_SECRET}"', FAKE_ENV_SECRET),
        (f"password: {FAKE_ENV_SECRET}", FAKE_ENV_SECRET),
        (f"Authorization: Bearer {FAKE_JWT}", FAKE_JWT),
        (f"Cookie: {FAKE_COOKIE}", "QWERTYfake123"),
        (f"Set-Cookie: {FAKE_COOKIE}", "QWERTYfake123"),
        (f"git remote https://drew:{FAKE_ENV_SECRET}@example.invalid/x.git", FAKE_ENV_SECRET),
        (f"the token is {FAKE_GITHUB_TOKEN} somewhere", FAKE_GITHUB_TOKEN),
        (f"bare key {FAKE_API_KEY} in prose", FAKE_API_KEY),
        (f"a jwt {FAKE_JWT} in prose", FAKE_JWT),
        (f"identity file:\n{FAKE_PRIVATE_KEY}\n", "MIIEowIBAAKCAQEA"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----\nMIIEowIBAAKCtruncated", "MIIEowIBAAKCtruncated"),
    ],
)
def test_redact_text_removes_every_known_secret_shape(text: str, secret: str) -> None:
    result = redact_text(text, field="detail")
    assert secret not in result.text
    assert SECRET_PLACEHOLDER in result.text
    assert not result.is_clean
    assert all(note.field == "detail" for note in result.notes)


@pytest.mark.parametrize(
    "text",
    [
        "/home/drewp/.claude/CLAUDE.md",
        "-rw------- 4096 bytes / 4000 characters",
        "sha256:" + "ab" * 32,
        '"token_count": 1000, "token_count_kind": "estimated"',
        '"token_method": "characters/4 heuristic"',
        "scope mismatch: project rule loaded globally",
        "precedence 1 beats precedence 2",
    ],
)
def test_redact_text_leaves_safe_audit_metadata_alone(text: str) -> None:
    result = redact_text(text, field="detail")
    assert result.text == text
    assert result.is_clean
    assert scan(text) == ()


def test_redaction_is_idempotent_so_the_fail_closed_check_cannot_self_trigger() -> None:
    text = (
        f"Cookie: {FAKE_COOKIE}\nANTHROPIC_API_KEY={FAKE_API_KEY}\n"
        f"Authorization: Bearer {FAKE_JWT}\n{FAKE_PRIVATE_KEY}\n"
    )
    once = redact_text(text, field="detail").text
    twice = redact_text(once, field="detail")
    assert twice.text == once
    assert twice.is_clean
    assert scan(once) == ()


def test_scan_counts_occurrences_and_names_its_rule() -> None:
    notes = scan(f"a={FAKE_API_KEY} b={FAKE_GITHUB_TOKEN}", field="summary")
    assert notes
    assert sum(note.occurrences for note in notes) == 2
    assert all(note.status is RedactionStatus.REDACTED for note in notes)
    assert all(note.rule for note in notes)
    assert RedactionNote.from_dict(notes[0].to_dict()) == notes[0]


def test_assert_no_secrets_refuses_text_that_still_carries_one() -> None:
    assert_no_secrets(f"api_key={SECRET_PLACEHOLDER}", field="bundle")
    with pytest.raises(RedactionError, match="bundle"):
        assert_no_secrets(f"api_key={FAKE_API_KEY}", field="bundle")


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #


def test_redact_environment_keeps_names_and_never_a_value() -> None:
    result = redact_environment(
        {"ANTHROPIC_API_KEY": FAKE_API_KEY, "CCDB_API_SECRET": FAKE_ENV_SECRET, "EDITOR": "vim"},
        field="claude.environment",
    )
    assert set(result.record) == {"ANTHROPIC_API_KEY", "CCDB_API_SECRET", "EDITOR"}
    assert set(result.record.values()) == {SECRET_PLACEHOLDER}
    assert not result.is_clean
    serialized = to_json(RedactionNote("x", "y", RedactionStatus.REDACTED))
    assert FAKE_API_KEY not in serialized
    for secret in (FAKE_API_KEY, FAKE_ENV_SECRET, "vim"):
        assert secret not in str(result.record)


def test_redact_environment_withholds_a_name_that_is_itself_a_secret() -> None:
    result = redact_environment({FAKE_GITHUB_TOKEN: "1"}, field="codex.environment")
    assert FAKE_GITHUB_TOKEN not in str(result.record)
    assert any(note.status is RedactionStatus.WITHHELD for note in result.notes)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


def test_redact_item_strips_secrets_and_keeps_the_metadata_rules_need() -> None:
    result = redact_item(secret_item(), salt="drewai")
    item = result.record
    serialized = to_json(item)
    for secret in (FAKE_API_KEY, FAKE_JWT, FAKE_GITHUB_TOKEN, FAKE_ENV_SECRET, "QWERTYfake123"):
        assert secret not in serialized

    assert item.item_id == "claude-global-agents-md"
    assert item.kind is SourceKind.GLOBAL_INSTRUCTIONS
    assert item.permissions == "-rw-------"
    assert item.content_hash == "sha256:" + "ab" * 32
    assert item.size is not None
    assert item.size.byte_size == 4096
    assert item.size.token_method == "characters/4 heuristic"
    assert item.sources[0].scope is Scope.GLOBAL
    assert item.sources[0].precedence == 1
    assert item.evidence.level is EvidenceLevel.LOADED
    assert item.redaction is RedactionStatus.WITHHELD
    assert item.unrecognized == {"future_field": WITHHELD_PLACEHOLDER}
    assert result.notes


def test_redact_item_replaces_a_content_hash_that_is_not_a_hash() -> None:
    original = secret_item()
    leaky = InventoryItem(
        item_id=original.item_id,
        target=original.target,
        kind=original.kind,
        label="global AGENTS.md",
        sources=original.sources,
        evidence=original.evidence,
        permissions="-rw-------",
        content_hash=FAKE_PRIVATE_BODY,
    )
    item = redact_item(leaky, salt="drewai").record
    assert item.content_hash == safe_hash(FAKE_PRIVATE_BODY, salt="drewai")
    assert FAKE_PRIVATE_BODY not in to_json(item)


def test_redact_item_leaves_an_already_clean_item_untouched() -> None:
    clean = InventoryItem(
        item_id="claude-skill-research",
        target=DREWAI_CLAUDE,
        kind=SourceKind.SKILL,
        label="research skill",
        sources=(
            ItemSource(
                reference="~/.agents/skills/research",
                scope=Scope.GLOBAL,
                evidence=loaded_evidence("listed in the session skill manifest"),
            ),
        ),
        evidence=loaded_evidence("listed in the session skill manifest"),
        permissions="drwx------",
    )
    result = redact_item(clean)
    assert result.record == clean
    assert result.is_clean


def test_redact_inventory_preserves_identity_and_cleans_every_item() -> None:
    inventory = HarnessInventory(
        target=DREWAI_CLAUDE, collected_at=COLLECTED_AT, items=(secret_item(),)
    )
    result = redact_inventory(inventory, salt="drewai")
    assert result.record.target == DREWAI_CLAUDE
    assert result.record.collected_at == COLLECTED_AT
    assert len(result.record.items) == 1
    serialized = to_json(result.record)
    for secret in ALL_FAKE_SECRETS:
        assert secret not in serialized


def test_redact_finding_classification_exception_and_gap() -> None:
    finding = Finding(
        finding_id="dup-agents-md",
        check=AuditCheck.DUPLICATION,
        outcome=CheckOutcome.FAIL,
        severity=Severity.MEDIUM,
        summary=f"two copies, one holds api_key={FAKE_API_KEY}",
        item_ids=("claude-global-agents-md", "codex-global-agents-md"),
        targets=(DREWAI_CLAUDE, DREWAI_CODEX),
        evidence=(loaded_evidence(f"Cookie: {FAKE_COOKIE}"),),
        vendor_sources=(
            VendorCitation(
                source_id="claude-code-memory",
                url=f"https://drew:{FAKE_ENV_SECRET}@docs.claude.com/memory",
                retrieved_on=date(2026, 9, 20),
                quoted_proposition="CLAUDE.md is read at session start",
            ),
        ),
        comparison_method="normalized sha256",
        duplicated_bytes=4096,
    )
    redacted_finding = redact_finding(finding).record
    serialized = to_json(redacted_finding)
    assert FAKE_API_KEY not in serialized
    assert FAKE_ENV_SECRET not in serialized
    assert "QWERTYfake123" not in serialized
    assert redacted_finding.duplicated_bytes == 4096
    assert redacted_finding.comparison_method == "normalized sha256"
    assert redacted_finding.vendor_sources[0].url.startswith("https://")

    record = ClassificationRecord(
        item_id="claude-global-agents-md",
        classification=Classification.FIX,
        reason=f"holds a live credential {FAKE_GITHUB_TOKEN}",
        evidence=(loaded_evidence("read at session start"),),
        affected_targets=(DREWAI_CLAUDE,),
        proposed_scope=Scope.GLOBAL,
        risk="none; the file stays in place",
        reversible_action="move the value to the keychain and restore from git if needed",
    )
    redacted_record = redact_classification(record).record
    assert FAKE_GITHUB_TOKEN not in to_json(redacted_record)
    assert redacted_record.classification is Classification.FIX

    exception = MachineException(
        exception_id="imac-no-opus",
        machine=Machine.IMAC,
        kind=ExceptionKind.SUBSCRIPTION,
        description=f"subscription differs; login token {FAKE_JWT}",
        preserved_outcome="same skills load on both machines",
        evidence=loaded_evidence("read from the CLI config"),
    )
    redacted_exception = redact_machine_exception(exception).record
    assert FAKE_JWT not in to_json(redacted_exception)
    assert redacted_exception.machine is Machine.IMAC

    gap = CoverageGap(
        target=AuditTarget(Machine.IMAC, Harness.CODEX),
        reason=f"machine offline; last seen with cookie {FAKE_COOKIE}",
        missing_evidence=(f"rollout metadata (api_key={FAKE_API_KEY})",),
        observed_at=COLLECTED_AT,
    )
    redacted_gap = redact_coverage_gap(gap).record
    assert "QWERTYfake123" not in to_json(redacted_gap)
    assert FAKE_API_KEY not in to_json(redacted_gap)
    assert redacted_gap.claims_full_coverage is False


# --------------------------------------------------------------------------- #
# Bundles
# --------------------------------------------------------------------------- #


def sample_bundle() -> RedactedBundle:
    inventory = HarnessInventory(
        target=DREWAI_CLAUDE, collected_at=COLLECTED_AT, items=(secret_item(),)
    )
    gap = CoverageGap(
        target=DREWAI_CODEX,
        reason="codex config unreadable",
        missing_evidence=("native config",),
        observed_at=COLLECTED_AT,
    )
    return build_redacted_bundle(
        machine=Machine.DREWAI,
        created_at=COLLECTED_AT,
        inventories=(inventory,),
        coverage_gaps=(gap,),
        private_bodies=(withhold_body(FAKE_PRIVATE_BODY, field="bot.system_prompt"),),
        environment={"ANTHROPIC_API_KEY": FAKE_API_KEY, "CCDB_API_SECRET": FAKE_ENV_SECRET},
        salt="drewai",
    )


def test_serialized_bundle_contains_no_fixture_secret() -> None:
    serialized = serialize_bundle(sample_bundle())
    for secret in ALL_FAKE_SECRETS:
        assert secret not in serialized


def test_serialized_bundle_still_carries_usable_audit_metadata() -> None:
    serialized = serialize_bundle(sample_bundle())
    assert "claude-global-agents-md" in serialized
    assert "drewai" in serialized
    assert "-rw-------" in serialized
    assert "4096" in serialized
    assert "ab" * 32 in serialized
    assert "ANTHROPIC_API_KEY" in serialized
    assert "codex config unreadable" in serialized
    assert str(len(FAKE_PRIVATE_BODY)) in serialized


def test_bundle_round_trips_deterministically() -> None:
    bundle = sample_bundle()
    serialized = serialize_bundle(bundle)
    assert serialize_bundle(sample_bundle()) == serialized
    assert parse_bundle(serialized) == bundle
    assert serialize_bundle(parse_bundle(serialized)) == serialized


def test_bundle_rejects_an_unsupported_or_unknown_payload() -> None:
    payload = parse_bundle(serialize_bundle(sample_bundle())).to_dict()
    with pytest.raises(RedactionError):
        RedactedBundle.from_dict({**payload, "surprise": 1})
    with pytest.raises(RedactionError):
        RedactedBundle.from_dict({**payload, "schema_version": SCHEMA_VERSION + 1})


def test_bundle_rejects_evidence_from_another_machine() -> None:
    inventory = HarnessInventory(
        target=AuditTarget(Machine.IMAC, Harness.CLAUDE), collected_at=COLLECTED_AT, items=()
    )
    with pytest.raises(RedactionError, match="imac"):
        RedactedBundle(machine=Machine.DREWAI, created_at=COLLECTED_AT, inventories=(inventory,))


def test_serializing_an_unredacted_bundle_fails_closed() -> None:
    inventory = HarnessInventory(
        target=DREWAI_CLAUDE, collected_at=COLLECTED_AT, items=(secret_item(),)
    )
    leaky = RedactedBundle(
        machine=Machine.DREWAI, created_at=COLLECTED_AT, inventories=(inventory,)
    )
    with pytest.raises(RedactionError):
        serialize_bundle(leaky)


def test_build_redacted_bundle_records_what_it_removed() -> None:
    bundle = sample_bundle()
    assert bundle.notes
    assert any(note.status is RedactionStatus.WITHHELD for note in bundle.notes)
    assert all(note.occurrences >= 1 for note in bundle.notes)
    for note in bundle.notes:
        for secret in ALL_FAKE_SECRETS:
            assert secret not in note.field
            assert secret not in note.rule
