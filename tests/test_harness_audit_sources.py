"""Contract tests for the pinned official-source manifest (task 1.3).

Rules that encode *vendor* behaviour — load order, scope, precedence,
permissions — must cite official guidance with a pin, and the audit runs
offline.  Those two facts put every interesting property in the loader rather
than in a web request, so these tests pin:

* the shipped manifest really covers both harnesses and every vendor-backed
  check, and each record survives the evidence schema's own citation rules;
* anything unpinned, expired, malformed, unknown or not published by the
  vendor is rejected outright — never downgraded to a warning; and
* loading never opens a socket.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from extensions.harness_audit.models import AuditCheck, Harness, SchemaError, VendorCitation
from extensions.harness_audit.sources import (
    DEFAULT_MANIFEST_PATH,
    MANIFEST_SCHEMA_VERSION,
    VENDOR_BACKED_CHECKS,
    ManifestError,
    OfficialSource,
    Publisher,
    SourceManifest,
    load_manifest,
    parse_manifest,
)

# The shipped manifest is reviewed data, so the tests read its own review dates
# instead of hard-coding a "now" that would rot the suite on a refresh.
TODAY = date(2026, 9, 20)


def shipped() -> SourceManifest:
    return load_manifest(today=TODAY)


def valid_payload() -> dict[str, Any]:
    """A minimal manifest that must load, used as the base for mutations."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_version": 1,
        "reviewed_on": "2026-09-20",
        "review_due_on": "2026-12-19",
        "notes": "fixture manifest",
        "sources": [
            {
                "source_id": "claude-code-memory",
                "harness": "claude",
                "publisher": "anthropic",
                "title": "Claude Code memory",
                "url": "https://docs.claude.com/en/docs/claude-code/memory",
                "retrieved_on": "2026-09-20",
                "review_due_on": "2026-12-19",
                "min_cli_version": "2.0.0",
                "max_cli_version": "",
                "content_hash": "",
                "proposition": "CLAUDE.md files load from user, project and local scopes.",
                "checks": ["load-behavior", "precedence"],
            },
            {
                "source_id": "codex-config",
                "harness": "codex",
                "publisher": "openai",
                "title": "Codex CLI configuration",
                "url": "https://developers.openai.com/codex/config/",
                "retrieved_on": "2026-09-20",
                "review_due_on": "2026-12-19",
                "min_cli_version": "0.140.0",
                "max_cli_version": "1.0.0",
                "content_hash": "a" * 64,
                "proposition": "config.toml is read from CODEX_HOME.",
                "checks": ["load-behavior"],
            },
        ],
    }


def mutated(**changes: Any) -> dict[str, Any]:
    payload = valid_payload()
    payload.update(changes)
    return payload


def with_source(index: int = 0, **changes: Any) -> dict[str, Any]:
    payload = valid_payload()
    source = copy.deepcopy(payload["sources"][index])
    source.update(changes)
    payload["sources"][index] = source
    return payload


def parse(payload: Mapping[str, Any], *, today: date = TODAY) -> SourceManifest:
    return parse_manifest(payload, today=today, origin="fixture")


# --------------------------------------------------------------------------- #
# The shipped manifest
# --------------------------------------------------------------------------- #


def test_shipped_manifest_file_exists_and_loads() -> None:
    assert DEFAULT_MANIFEST_PATH.is_file()
    manifest = shipped()
    assert manifest.schema_version == MANIFEST_SCHEMA_VERSION
    assert manifest.sources


def test_shipped_manifest_covers_both_harnesses() -> None:
    manifest = shipped()
    covered = {source.harness for source in manifest.sources}
    assert covered == {Harness.CLAUDE, Harness.CODEX}


def test_shipped_manifest_covers_every_vendor_backed_check_on_every_harness() -> None:
    manifest = shipped()
    for harness in Harness:
        for check in VENDOR_BACKED_CHECKS:
            assert manifest.for_check(harness, check), f"{harness.value} has no source for {check}"


def test_shipped_sources_are_official_https_urls() -> None:
    for source in shipped().sources:
        assert source.url.startswith("https://")
        assert source.publisher is Publisher.for_harness(source.harness)


def test_shipped_sources_are_pinned_and_unexpired() -> None:
    for source in shipped().sources:
        assert source.content_hash or source.proposition
        assert not source.is_expired(TODAY)
        assert source.review_due_on > source.retrieved_on


def test_shipped_source_ids_are_unique() -> None:
    ids = [source.source_id for source in shipped().sources]
    assert len(ids) == len(set(ids))


def test_shipped_sources_produce_valid_evidence_citations() -> None:
    for source in shipped().sources:
        citation = source.citation()
        assert isinstance(citation, VendorCitation)
        assert citation.source_id == source.source_id
        # The evidence schema round trips it without complaint.
        assert VendorCitation.from_dict(citation.to_dict()) == citation


def test_shipped_manifest_json_is_a_plain_reviewable_object() -> None:
    raw = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert isinstance(raw["sources"], list)


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


def test_get_returns_the_named_source() -> None:
    manifest = parse(valid_payload())
    source = manifest.get("codex-config")
    assert isinstance(source, OfficialSource)
    assert source.harness is Harness.CODEX


def test_unknown_source_id_fails_closed() -> None:
    manifest = parse(valid_payload())
    with pytest.raises(ManifestError, match="not-a-source"):
        manifest.get("not-a-source")


def test_for_check_filters_by_harness_and_check() -> None:
    manifest = parse(valid_payload())
    assert [s.source_id for s in manifest.for_check(Harness.CLAUDE, AuditCheck.PRECEDENCE)] == [
        "claude-code-memory"
    ]
    assert manifest.for_check(Harness.CODEX, AuditCheck.PRECEDENCE) == ()


def test_require_raises_when_no_source_backs_the_check() -> None:
    manifest = parse(valid_payload())
    assert manifest.require(Harness.CLAUDE, AuditCheck.LOAD_BEHAVIOR).source_id
    with pytest.raises(ManifestError, match="permissions"):
        manifest.require(Harness.CODEX, AuditCheck.PERMISSIONS)


def test_covers_version_respects_the_pinned_range() -> None:
    manifest = parse(valid_payload())
    claude = manifest.get("claude-code-memory")
    codex = manifest.get("codex-config")
    assert claude.covers_version("2.0.0")
    assert claude.covers_version("9.1.2")
    assert not claude.covers_version("1.9.9")
    assert codex.covers_version("0.147.0")
    assert not codex.covers_version("1.0.0")  # max is exclusive
    assert not codex.covers_version("0.1.0")


def test_covers_version_rejects_a_malformed_version() -> None:
    claude = parse(valid_payload()).get("claude-code-memory")
    with pytest.raises(ManifestError, match="version"):
        claude.covers_version("2.0.0-beta")


def test_manifest_records_are_frozen() -> None:
    source = parse(valid_payload()).get("codex-config")
    with pytest.raises((AttributeError, TypeError)):
        source.url = "https://example.com"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Expired records fail closed
# --------------------------------------------------------------------------- #


def test_expired_source_fails_closed() -> None:
    with pytest.raises(ManifestError, match="claude-code-memory"):
        parse(valid_payload(), today=date(2026, 12, 20))


def test_source_is_valid_on_its_review_due_date() -> None:
    assert parse(valid_payload(), today=date(2026, 12, 19)).sources


def test_expired_manifest_fails_closed_even_when_sources_are_fresh() -> None:
    payload = valid_payload()
    payload["review_due_on"] = "2026-09-19"
    for source in payload["sources"]:
        source["review_due_on"] = "2027-12-19"
    with pytest.raises(ManifestError, match="review"):
        parse(payload)


def test_review_due_on_before_retrieval_fails_closed() -> None:
    with pytest.raises(ManifestError, match="review_due_on"):
        parse(with_source(review_due_on="2026-09-20"))


def test_retrieval_in_the_future_fails_closed() -> None:
    with pytest.raises(ManifestError, match="future"):
        parse(with_source(retrieved_on="2026-09-21", review_due_on="2027-01-01"))


# --------------------------------------------------------------------------- #
# Non-official records fail closed
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "http://docs.claude.com/en/docs/claude-code/memory",  # not https
        "https://medium.com/@someone/claude-code-tips",  # a blog
        "https://docs.claude.com.evil.example/en/docs",  # lookalike host
        "https://github.com/someone/claude-code-notes",  # unapproved repo owner
        "https://user:pw@docs.claude.com/en/docs",  # credentials in the url
        "https://docs.claude.com:8443/en/docs",  # non-default port
        "docs.claude.com/en/docs",  # not a url at all
    ],
)
def test_non_official_url_fails_closed(url: str) -> None:
    with pytest.raises(ManifestError, match="official|url"):
        parse(with_source(url=url))


def test_openai_repo_under_github_is_official_for_codex() -> None:
    payload = with_source(1, url="https://github.com/openai/codex/blob/main/docs/config.md")
    assert parse(payload).get("codex-config").url.startswith("https://github.com/openai/")


def test_publisher_that_does_not_own_the_harness_fails_closed() -> None:
    with pytest.raises(ManifestError, match="publisher"):
        parse(with_source(publisher="openai"))


def test_publisher_host_mismatch_fails_closed() -> None:
    with pytest.raises(ManifestError, match="official"):
        parse(with_source(url="https://developers.openai.com/codex/config/"))


def test_unknown_publisher_fails_closed() -> None:
    with pytest.raises(ManifestError, match="publisher"):
        parse(with_source(publisher="acme"))


def test_unknown_harness_fails_closed() -> None:
    with pytest.raises(ManifestError, match="harness"):
        parse(with_source(harness="gemini"))


def test_unknown_check_fails_closed() -> None:
    with pytest.raises(ManifestError, match="check"):
        parse(with_source(checks=["vibes"]))


# --------------------------------------------------------------------------- #
# Malformed records fail closed
# --------------------------------------------------------------------------- #


def test_unpinned_source_fails_closed() -> None:
    with pytest.raises(ManifestError, match="pin|content_hash|proposition"):
        parse(with_source(content_hash="", proposition="   "))


@pytest.mark.parametrize("content_hash", ["abc123", "z" * 64, "A" * 63])
def test_malformed_content_hash_fails_closed(content_hash: str) -> None:
    with pytest.raises(ManifestError, match="content_hash"):
        parse(with_source(content_hash=content_hash))


def test_uppercase_content_hash_is_normalized() -> None:
    manifest = parse(with_source(1, content_hash="A" * 64))
    assert manifest.get("codex-config").content_hash == "a" * 64


def test_inverted_version_range_fails_closed() -> None:
    with pytest.raises(ManifestError, match="version"):
        parse(with_source(min_cli_version="3.0.0", max_cli_version="2.0.0"))


@pytest.mark.parametrize("version", ["two", "2.0.0-rc1", "2..0", "-1.0", ""])
def test_malformed_min_version_fails_closed(version: str) -> None:
    with pytest.raises(ManifestError, match="version"):
        parse(with_source(min_cli_version=version))


def test_unknown_top_level_key_fails_closed() -> None:
    with pytest.raises(ManifestError, match="surprise"):
        parse(mutated(surprise="new field"))


def test_unknown_source_key_fails_closed() -> None:
    with pytest.raises(ManifestError, match="surprise"):
        parse(with_source(surprise="new field"))


@pytest.mark.parametrize(
    "key",
    ["source_id", "harness", "publisher", "title", "url", "retrieved_on", "review_due_on"],
)
def test_missing_required_source_key_fails_closed(key: str) -> None:
    payload = valid_payload()
    del payload["sources"][0][key]
    with pytest.raises(ManifestError, match=key):
        parse(payload)


@pytest.mark.parametrize("key", ["schema_version", "manifest_version", "reviewed_on", "sources"])
def test_missing_required_top_level_key_fails_closed(key: str) -> None:
    payload = valid_payload()
    del payload[key]
    with pytest.raises(ManifestError, match=key):
        parse(payload)


def test_duplicate_source_id_fails_closed() -> None:
    payload = valid_payload()
    payload["sources"][1]["source_id"] = "claude-code-memory"
    with pytest.raises(ManifestError, match="duplicate"):
        parse(payload)


def test_empty_source_list_fails_closed() -> None:
    with pytest.raises(ManifestError, match="sources"):
        parse(mutated(sources=[]))


def test_empty_title_fails_closed() -> None:
    with pytest.raises(ManifestError, match="title"):
        parse(with_source(title="   "))


def test_unsupported_schema_version_fails_closed() -> None:
    with pytest.raises(ManifestError, match="schema_version"):
        parse(mutated(schema_version=MANIFEST_SCHEMA_VERSION + 1))


def test_non_object_manifest_fails_closed() -> None:
    with pytest.raises(ManifestError, match="object"):
        parse_manifest(["not", "a", "manifest"], today=TODAY, origin="fixture")  # type: ignore[arg-type]


def test_non_object_source_fails_closed() -> None:
    with pytest.raises(ManifestError, match="object"):
        parse(mutated(sources=["https://docs.claude.com/en/docs/claude-code/memory"]))


def test_manifest_error_is_a_schema_error() -> None:
    assert issubclass(ManifestError, SchemaError)


# --------------------------------------------------------------------------- #
# Loading from disk
# --------------------------------------------------------------------------- #


def test_malformed_json_on_disk_fails_closed(tmp_path: Path) -> None:
    broken = tmp_path / "sources.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="json|parse"):
        load_manifest(broken, today=TODAY)


def test_missing_manifest_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found|missing"):
        load_manifest(tmp_path / "absent.json", today=TODAY)


def test_load_names_the_offending_file(tmp_path: Path) -> None:
    bad = tmp_path / "sources.json"
    payload = with_source(url="https://medium.com/@someone/tips")
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match="sources.json"):
        load_manifest(bad, today=TODAY)


def test_loading_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the source manifest must never touch the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert load_manifest(today=TODAY).sources
