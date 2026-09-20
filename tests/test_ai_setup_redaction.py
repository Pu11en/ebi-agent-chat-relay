"""Adversarial tests for the My AI Setup redaction boundary (OpenSpec task 1.2).

Every test here plants a real secret shape — an API token, a password, a
private key block, an environment value, a connector credential, a raw config
blob — and then proves it cannot reach an inventory item, a diagnostic, a
Discord label or a Setup Agent packet.  The boundary fails closed: anything it
cannot summarize safely is dropped or rejected, never passed through.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claude_discord import ai_setup_redaction as redaction
from claude_discord.ai_setup_inventory import (
    MAX_LOCATOR_LENGTH,
    MAX_MESSAGE_LENGTH,
    AvailabilityState,
    Classification,
    ContentFingerprint,
    DiagnosticSeverity,
    EffectiveScope,
    HarnessAvailability,
    InventoryItem,
    InventorySource,
    ItemIdentity,
    Prerequisite,
    PrerequisiteState,
    SetupKind,
    safe_text,
)
from claude_discord.ai_setup_redaction import (
    REDACTED,
    RedactionError,
    RedactionReason,
    SafeMetadata,
    build_safe_metadata,
    connector_prerequisites,
    contains_secret,
    credential_prerequisite,
    ensure_safe_item,
    environment_prerequisites,
    is_secret_name,
    name_tokens,
    redact_text,
    require_safe_metadata,
    safe_diagnostic,
    safe_fingerprint,
    safe_locator,
    safe_message,
    screen_item,
    secret_reason,
)

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

ANTHROPIC_TOKEN = "sk-ant-api03-Qd4xKp7mN2vR8sT1uW5yZ9bC3eF6hJ0kL4nP7qS2tV5xY8zA1cD4"
GITHUB_TOKEN = "ghp_9fK2mQ7xL4pR8tV1yB5nC3eH6jM0oS2uW4z"
JWT = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkcmV3IiwiaWF0IjoxNzAwMDAwMDAwfQ"
    ".9aVb3Xc7Yd1Ze5Wf2Ug6Th0Si4Rj8Qk"
)
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
GOOGLE_KEY = "AIzaSyD8fRq2mK7pL4xN1vB9cT3eW6yH0jZ5uQ2"
PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdzc2gtcn\n"
    "-----END OPENSSH PRIVATE KEY-----"
)
PASSWORD_URL = "postgres://drew:hunter2swordfish@db.internal:5432/relay"


def _source(locator: str = "~/.agents/skills/grilling/SKILL.md") -> InventorySource:
    return InventorySource(
        key="claude-home",
        computer="drewai",
        label="Shared skills folder",
        locator=locator,
        harness="claude",
        modified_at=NOW,
    )


def _item(**overrides: object) -> InventoryItem:
    defaults: dict[str, object] = {
        "identity": ItemIdentity(kind=SetupKind.SKILL, source_key="claude-home", name="grilling"),
        "display_name": "Grilling",
        "source": _source(),
        "scope": EffectiveScope.everywhere(),
        "classification": Classification.CUSTOM,
    }
    defaults.update(overrides)
    return InventoryItem(**defaults)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------
# Field names that must never carry a value
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "apiKey",
        "ANTHROPIC_API_KEY",
        "password",
        "passwd",
        "passphrase",
        "client_secret",
        "private_key_path",
        "credentials",
        "auth_token",
        "authorization",
        "session_cookie",
        "DISCORD_BOT_TOKEN",
        "refresh-token",
        "env",
        "environment",
        "webhook_url",
    ],
)
def test_secret_field_names_are_recognized(name: str) -> None:
    assert is_secret_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "display_name",
        "locator",
        "scope",
        "harness",
        "keyword",
        "keybindings",
        "tokenizer",
        "model",
        "byte_size",
        "description",
    ],
)
def test_ordinary_field_names_survive(name: str) -> None:
    assert not is_secret_name(name)


def test_name_tokens_split_camel_case_and_separators() -> None:
    assert name_tokens("anthropicApiKey") == ("anthropic", "api", "key")
    assert name_tokens("ANTHROPIC_API_KEY") == ("anthropic", "api", "key")
    assert name_tokens("refresh-token.value") == ("refresh", "token", "value")


def test_token_count_is_rejected_as_a_field_name_by_design() -> None:
    """Counts belong in the typed Measurement, not in free metadata."""
    assert is_secret_name("token_count")


# --------------------------------------------------------------------------
# Values that must never be rendered, stored or attached
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (ANTHROPIC_TOKEN, RedactionReason.SECRET_VALUE),
        (GITHUB_TOKEN, RedactionReason.SECRET_VALUE),
        (JWT, RedactionReason.SECRET_VALUE),
        (AWS_KEY, RedactionReason.SECRET_VALUE),
        (GOOGLE_KEY, RedactionReason.SECRET_VALUE),
        (f"Authorization: Bearer {GITHUB_TOKEN}", RedactionReason.SECRET_VALUE),
        (PRIVATE_KEY, RedactionReason.PRIVATE_KEY),
        (PASSWORD_URL, RedactionReason.SECRET_VALUE),
        ("ANTHROPIC_API_KEY=sk-live-2f9dqzm", RedactionReason.ENVIRONMENT_VALUE),
        ("EDITOR=vim", RedactionReason.ENVIRONMENT_VALUE),
        ('password: "hunter2swordfish"', RedactionReason.SECRET_VALUE),
        ("Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MGFiY2RlZg==", RedactionReason.HIGH_ENTROPY),
    ],
)
def test_unsafe_values_are_detected(value: str, reason: RedactionReason) -> None:
    assert secret_reason(value) is reason
    assert contains_secret(value)


@pytest.mark.parametrize(
    "value",
    [
        "Grilling",
        "~/.agents/skills/grilling/SKILL.md",
        "Shared Drew profile",
        "Loaded by the Claude skill loader",
        "12,480 bytes",
        "sha256:" + "a1b2c3d4e5f6" * 4,
        "https://example.com/docs/skills",
        "session/1551139136931631245",
        "discord-agent-flow-wave-2-20260920",
    ],
)
def test_ordinary_values_are_left_alone(value: str) -> None:
    assert secret_reason(value) is None
    assert redact_text(value) == value


def test_redact_text_keeps_context_and_drops_the_secret() -> None:
    scrubbed = redact_text(f"connector claude-docs uses {ANTHROPIC_TOKEN} to authenticate")
    assert ANTHROPIC_TOKEN not in scrubbed
    assert REDACTED in scrubbed
    assert scrubbed.startswith("connector claude-docs uses")
    assert scrubbed.endswith("to authenticate")


def test_redact_text_keeps_the_variable_name_but_not_its_value() -> None:
    scrubbed = redact_text("ANTHROPIC_API_KEY=sk-live-2f9dqzm4hx")
    assert "ANTHROPIC_API_KEY" in scrubbed
    assert "sk-live-2f9dqzm4hx" not in scrubbed
    assert REDACTED in scrubbed


def test_redact_text_removes_a_whole_private_key_block() -> None:
    scrubbed = redact_text(f"hook installs\n{PRIVATE_KEY}\nthen exits")
    assert "BEGIN OPENSSH PRIVATE KEY" not in scrubbed
    assert "b3BlbnNzaC1rZXktdjEA" not in scrubbed
    assert REDACTED in scrubbed


def test_redact_text_drops_url_credentials_but_keeps_the_host() -> None:
    scrubbed = redact_text(PASSWORD_URL)
    assert "hunter2swordfish" not in scrubbed
    assert "drew:" not in scrubbed
    assert "db.internal" in scrubbed


# --------------------------------------------------------------------------
# Bounded, single-line, secret-free display text
# --------------------------------------------------------------------------


def test_safe_message_collapses_lines_scrubs_and_stays_within_limits() -> None:
    raw = f"adapter failed\n\tGET /v1 with token {GITHUB_TOKEN}\n" + ("very long tail " * 80)
    message = safe_message(raw)
    assert GITHUB_TOKEN not in message
    assert "\n" not in message and "\t" not in message
    assert len(message) <= MAX_MESSAGE_LENGTH
    assert safe_text(message, kind="diagnostic message", limit=MAX_MESSAGE_LENGTH) == message


def test_safe_message_refuses_to_invent_text_for_an_empty_string() -> None:
    with pytest.raises(RedactionError):
        safe_message("   \n  ")


def test_safe_locator_collapses_home_and_drops_query_and_credentials() -> None:
    locator = safe_locator("/home/drew/.claude/settings.json", home="/home/drew")
    assert locator == "~/.claude/settings.json"
    assert safe_locator("https://drew:hunter2@api.internal/v1?api_key=abc123def") == (
        "https://api.internal/v1"
    )


def test_safe_locator_elides_an_over_long_path_instead_of_failing() -> None:
    long_path = "~/" + "/".join(f"deeply-nested-folder-{index}" for index in range(40))
    locator = safe_locator(long_path)
    assert len(locator) <= MAX_LOCATOR_LENGTH
    assert "…" in locator
    assert safe_text(locator, kind="locator", limit=MAX_LOCATOR_LENGTH) == locator


# --------------------------------------------------------------------------
# Safe metadata construction (whitelist in, everything else out)
# --------------------------------------------------------------------------

ALLOWED = ("description", "format", "entry_point", "enabled", "byte_size", "api_key")


def test_build_safe_metadata_keeps_only_allowed_safe_fields() -> None:
    metadata = build_safe_metadata(
        {
            "description": "Grills a plan until it breaks",
            "format": "markdown",
            "enabled": True,
            "byte_size": 12480,
        },
        allowed=ALLOWED,
    )
    assert dict(metadata.fields) == {
        "description": "Grills a plan until it breaks",
        "format": "markdown",
        "enabled": True,
        "byte_size": 12480,
    }
    assert metadata.is_complete
    assert metadata.findings == ()


def test_build_safe_metadata_drops_fields_nobody_allowed() -> None:
    metadata = build_safe_metadata({"raw_config": "anything at all"}, allowed=ALLOWED)
    assert dict(metadata.fields) == {}
    assert [finding.reason for finding in metadata.findings] == [RedactionReason.NOT_ALLOWED]
    assert metadata.rejected_fields == ("raw_config",)
    assert not metadata.is_complete


def test_an_allowlist_cannot_re_admit_a_credential_field() -> None:
    metadata = build_safe_metadata({"api_key": ANTHROPIC_TOKEN}, allowed=ALLOWED)
    assert dict(metadata.fields) == {}
    assert [finding.reason for finding in metadata.findings] == [RedactionReason.SECRET_NAME]
    assert ANTHROPIC_TOKEN not in repr(metadata)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (GITHUB_TOKEN, RedactionReason.SECRET_VALUE),
        (PRIVATE_KEY, RedactionReason.PRIVATE_KEY),
        ("HOME=/home/drew", RedactionReason.ENVIRONMENT_VALUE),
        ("line one\nline two", RedactionReason.UNSAFE_SHAPE),
        ("x" * 400, RedactionReason.TOO_LONG),
    ],
)
def test_unsafe_values_are_dropped_not_rendered(value: str, reason: RedactionReason) -> None:
    metadata = build_safe_metadata({"description": value}, allowed=ALLOWED)
    assert dict(metadata.fields) == {}
    assert [finding.reason for finding in metadata.findings] == [reason]


@pytest.mark.parametrize("value", [b"raw bytes", {"nested": "config"}, ["a", "list"], object()])
def test_raw_unsafe_config_shapes_never_enter_metadata(value: object) -> None:
    metadata = build_safe_metadata({"description": value}, allowed=ALLOWED)
    assert dict(metadata.fields) == {}
    assert [finding.reason for finding in metadata.findings] == [RedactionReason.UNSUPPORTED_TYPE]


def test_absent_values_are_skipped_without_a_complaint() -> None:
    metadata = build_safe_metadata({"description": None}, allowed=ALLOWED)
    assert dict(metadata.fields) == {}
    assert metadata.is_complete


def test_safe_metadata_is_immutable() -> None:
    metadata = build_safe_metadata({"format": "markdown"}, allowed=ALLOWED)
    with pytest.raises(TypeError):
        metadata.fields["format"] = "other"  # pyright: ignore[reportIndexIssue]


def test_require_safe_metadata_fails_closed_on_any_finding() -> None:
    with pytest.raises(RedactionError) as error:
        require_safe_metadata({"api_key": ANTHROPIC_TOKEN}, allowed=ALLOWED)
    assert "api_key" in str(error.value)
    assert ANTHROPIC_TOKEN not in str(error.value)


def test_require_safe_metadata_returns_the_clean_mapping() -> None:
    fields = require_safe_metadata({"format": "markdown"}, allowed=ALLOWED)
    assert dict(fields) == {"format": "markdown"}


def test_metadata_findings_become_safe_diagnostics() -> None:
    metadata = build_safe_metadata({"description": GITHUB_TOKEN}, allowed=ALLOWED)
    diagnostics = metadata.diagnostics("claude-home", computer="drewai", occurred_at=NOW)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.severity is DiagnosticSeverity.WARNING
    assert diagnostic.source_key == "claude-home"
    assert "description" in diagnostic.message
    assert GITHUB_TOKEN not in diagnostic.message
    assert len(diagnostic.message) <= MAX_MESSAGE_LENGTH


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


def test_safe_diagnostic_scrubs_the_message_it_is_given() -> None:
    diagnostic = safe_diagnostic(
        "codex-home",
        DiagnosticSeverity.ERROR,
        f"could not parse config:\nauth_token = {GITHUB_TOKEN}",
        computer="imac",
        occurred_at=NOW,
    )
    assert GITHUB_TOKEN not in diagnostic.message
    assert REDACTED in diagnostic.message
    assert diagnostic.is_error
    assert diagnostic.computer == "imac"


def test_safe_diagnostic_never_raises_on_a_hostile_message() -> None:
    diagnostic = safe_diagnostic(
        "dsh-home",
        DiagnosticSeverity.WARNING,
        PRIVATE_KEY * 20,
    )
    assert "PRIVATE KEY" not in diagnostic.message
    assert len(diagnostic.message) <= MAX_MESSAGE_LENGTH


# --------------------------------------------------------------------------
# Prerequisites: presence, never the value
# --------------------------------------------------------------------------


def test_environment_prerequisites_report_presence_only() -> None:
    environ = {"ANTHROPIC_API_KEY": ANTHROPIC_TOKEN, "EMPTY_TOKEN": "   "}
    prerequisites = environment_prerequisites(
        ["ANTHROPIC_API_KEY", "EMPTY_TOKEN", "MISSING_TOKEN"], environ=environ
    )
    states = {item.name: item.state for item in prerequisites}
    assert states == {
        "ANTHROPIC_API_KEY": PrerequisiteState.SATISFIED,
        "EMPTY_TOKEN": PrerequisiteState.MISSING,
        "MISSING_TOKEN": PrerequisiteState.MISSING,
    }
    assert ANTHROPIC_TOKEN not in repr(prerequisites)


def test_connector_prerequisites_read_presence_out_of_a_raw_config() -> None:
    config = {"api_key": ANTHROPIC_TOKEN, "oauth_token": "", "endpoint": "https://api.example"}
    prerequisites = connector_prerequisites(config, required=["api_key", "oauth_token", "region"])
    states = {item.name: item.state for item in prerequisites}
    assert states == {
        "api_key": PrerequisiteState.SATISFIED,
        "oauth_token": PrerequisiteState.MISSING,
        "region": PrerequisiteState.MISSING,
    }
    assert ANTHROPIC_TOKEN not in repr(prerequisites)


def test_credential_prerequisite_records_an_unknown_check() -> None:
    prerequisite = credential_prerequisite("Keychain entry", present=None)
    assert prerequisite.state is PrerequisiteState.UNKNOWN


# --------------------------------------------------------------------------
# The item boundary: nothing unsafe becomes an inventory item
# --------------------------------------------------------------------------


def test_a_clean_item_passes_the_boundary_unchanged() -> None:
    item = _item(
        summary="Grills a plan until it breaks",
        availability=(
            HarnessAvailability(
                harness="claude",
                state=AvailabilityState.VERIFIED_LOADED,
                evidence="Named in the resolved skill set",
                verified_at=NOW,
            ),
        ),
        prerequisites=(Prerequisite.credential("ANTHROPIC_API_KEY", present=True),),
    )
    assert ensure_safe_item(item) is item


@pytest.mark.parametrize(
    ("field", "overrides"),
    [
        ("summary", {"summary": f"needs {ANTHROPIC_TOKEN} to run"}),
        ("display_name", {"display_name": f"Skill {GITHUB_TOKEN}"}),
        ("source.locator", {"source": _source(locator=PASSWORD_URL)}),
        (
            "availability.evidence",
            {
                "availability": (
                    HarnessAvailability(
                        harness="claude",
                        state=AvailabilityState.VERIFIED_LOADED,
                        evidence=f"Authorization: Bearer {GITHUB_TOKEN}",
                        verified_at=NOW,
                    ),
                )
            },
        ),
        (
            "prerequisites.detail",
            {"prerequisites": (Prerequisite(name="Database", detail=PASSWORD_URL),)},
        ),
    ],
)
def test_an_item_carrying_a_secret_is_rejected(field: str, overrides: dict[str, object]) -> None:
    item = _item(**overrides)
    with pytest.raises(RedactionError) as error:
        ensure_safe_item(item)
    message = str(error.value)
    assert field in message
    assert ANTHROPIC_TOKEN not in message
    assert GITHUB_TOKEN not in message
    assert "hunter2swordfish" not in message


def test_screen_item_lets_collection_continue_with_a_safe_diagnostic() -> None:
    item = _item(summary=f"needs {ANTHROPIC_TOKEN} to run")
    kept, diagnostic = screen_item(item, occurred_at=NOW)
    assert kept is None
    assert diagnostic is not None
    assert diagnostic.is_error
    assert diagnostic.identity == item.identity
    assert diagnostic.source_key == item.identity.source_key
    assert ANTHROPIC_TOKEN not in diagnostic.message


def test_screen_item_passes_a_clean_item_through_without_a_diagnostic() -> None:
    item = _item(summary="Grills a plan until it breaks")
    kept, diagnostic = screen_item(item, occurred_at=NOW)
    assert kept is item
    assert diagnostic is None


# --------------------------------------------------------------------------
# Fingerprints: the only thing raw content may become
# --------------------------------------------------------------------------


def test_safe_fingerprint_reduces_secret_content_to_a_one_way_digest() -> None:
    fingerprint = safe_fingerprint(f"api_key = {ANTHROPIC_TOKEN}\n")
    assert isinstance(fingerprint, ContentFingerprint)
    assert ANTHROPIC_TOKEN not in fingerprint.digest
    assert fingerprint.matches(safe_fingerprint(f"api_key = {ANTHROPIC_TOKEN}\n".encode()))
    assert not fingerprint.matches(safe_fingerprint("api_key = something-else\n"))


# --------------------------------------------------------------------------
# End to end: a hostile source produces an item with no secret anywhere
# --------------------------------------------------------------------------


def test_a_hostile_connector_config_yields_only_safe_facts() -> None:
    raw_config = {
        "name": "claude-docs",
        "description": "Living docs connector",
        "endpoint": "https://docs.example.com/mcp",
        "api_key": ANTHROPIC_TOKEN,
        "oauth": {"client_secret": GITHUB_TOKEN, "refresh_token": JWT},
        "env": {"ANTHROPIC_API_KEY": ANTHROPIC_TOKEN},
        "private_key": PRIVATE_KEY,
        "connection": PASSWORD_URL,
    }
    metadata = build_safe_metadata(raw_config, allowed=("name", "description", "endpoint"))
    prerequisites = connector_prerequisites(raw_config, required=["api_key", "oauth_token"])
    item = _item(
        identity=ItemIdentity(
            kind=SetupKind.CONNECTOR, source_key="claude-home", name="claude-docs"
        ),
        display_name="Claude Docs",
        summary=safe_message(str(metadata.fields["description"])),
        prerequisites=prerequisites,
        fingerprint=safe_fingerprint(repr(raw_config)),
    )
    ensure_safe_item(item)

    rendered = " ".join(
        [
            repr(item),
            repr(metadata),
            repr(prerequisites),
            " ".join(entry.message for entry in metadata.diagnostics("claude-home")),
        ]
    )
    for secret in (ANTHROPIC_TOKEN, GITHUB_TOKEN, JWT, "hunter2swordfish", "b3BlbnNzaC1rZXktdjEA"):
        assert secret not in rendered
    assert dict(metadata.fields) == {
        "name": "claude-docs",
        "description": "Living docs connector",
        "endpoint": "https://docs.example.com/mcp",
    }


def test_module_exports_only_its_public_boundary() -> None:
    assert isinstance(redaction.__all__, list)
    assert set(redaction.__all__) <= set(dir(redaction))
    assert SafeMetadata.__name__ in redaction.__all__
