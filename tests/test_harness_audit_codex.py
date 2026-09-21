"""Codex effective-input collector tests (task 2.3).

Codex reads ``config.toml`` and ``AGENTS.md`` from ``CODEX_HOME``, the project
``AGENTS.md``, skills from both ``~/.codex/skills`` and ``~/.claude/skills``
(measured, see CLAUDE.md decision 13), and it writes a rollout whose
``session_meta`` records the cwd, the CLI version and the instructions it
loaded.  The collector reads that metadata — never a message — and earns its
evidence levels from it.  It records model and reasoning settings only to say
which path it inspected; it never changes them.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from extensions.harness_audit.codex_collector import (
    CodexInvocation,
    collect_codex,
    read_codex_rollouts,
)
from extensions.harness_audit.discovery import discover
from extensions.harness_audit.models import (
    AuditTarget,
    EvidenceLevel,
    Harness,
    InventoryItem,
    Machine,
    Scope,
    SourceKind,
    to_json,
)
from extensions.harness_audit.redaction import build_redacted_bundle, serialize_bundle
from tests.harness_audit_fixtures import (
    FAKE_NUMERIC_PASSWORD,
    FAKE_SYSTEM_PROMPT,
    GLOBAL_RULES,
    add_codex_rollout,
    codex_invocation,
    fake_home,
    manifest,
    write,
)

COLLECTED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
TARGET = AuditTarget(Machine.DREWAI, Harness.CODEX)


@pytest.fixture(autouse=True)
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("collector attempted to start a subprocess")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)


def item(items: tuple[InventoryItem, ...], suffix: str) -> InventoryItem:
    for entry in items:
        if entry.item_id.endswith(suffix):
            return entry
    raise AssertionError(f"no item ends with {suffix!r}; have {[i.item_id for i in items]}")


def collect(
    tmp_path: Path, *, rollouts: bool, invocation: bool = True, extra: tuple[str, ...] = ()
):
    roots = fake_home(tmp_path, with_transcripts=rollouts)
    assert roots.project_dir is not None
    record = CodexInvocation.from_dict(codex_invocation(roots.project_dir, *extra))
    return collect_codex(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record if invocation else None,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )


# --------------------------------------------------------------------------- #
# Evidence levels
# --------------------------------------------------------------------------- #


def test_rollout_instructions_hash_proves_the_global_agents_file_loaded(tmp_path: Path) -> None:
    result = collect(tmp_path, rollouts=True)
    items = result.inventory.items
    agents = item(items, "~/.codex/AGENTS.md")
    assert agents.evidence.level is EvidenceLevel.LOADED
    assert agents.evidence.method == "rollout-instructions-hash-match"
    assert [source.reference for source in agents.sources] == ["~/.codex/AGENTS.md", "~/AGENTS.md"]
    assert item(items, "~/projects/relay/AGENTS.md").evidence.level is EvidenceLevel.LOADED
    assert item(items, "~/.codex/config.toml").evidence.level is EvidenceLevel.LOADED


def test_skills_from_both_homes_are_loaded_when_the_rollout_names_them(tmp_path: Path) -> None:
    result = collect(tmp_path, rollouts=True)
    items = result.inventory.items
    release = item(items, "~/.codex/skills/release/SKILL.md")
    deploy = item(items, "~/.claude/skills/deploy/SKILL.md")
    assert release.evidence.level is EvidenceLevel.LOADED
    assert deploy.evidence.level is EvidenceLevel.LOADED
    assert deploy.item_id.startswith("codex/skill/")
    assert "rollout" in release.evidence.method


def test_without_a_rollout_everything_on_disk_is_configured(tmp_path: Path) -> None:
    result = collect(tmp_path, rollouts=False)
    items = result.inventory.items
    assert item(items, "~/.codex/AGENTS.md").evidence.level is EvidenceLevel.CONFIGURED
    assert (
        item(items, "~/.codex/skills/release/SKILL.md").evidence.level is EvidenceLevel.CONFIGURED
    )
    assert item(items, "~/.codex/config.toml").evidence.level is EvidenceLevel.CONFIGURED


def test_rollout_that_does_not_name_a_skill_leaves_it_configured(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=False)
    assert roots.project_dir is not None
    add_codex_rollout(
        roots.home, roots.project_dir, version="0.147.0", instructions=GLOBAL_RULES, skill_names=()
    )
    record = CodexInvocation.from_dict(codex_invocation(roots.project_dir))
    result = collect_codex(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    skill = item(result.inventory.items, "~/.codex/skills/release/SKILL.md")
    assert skill.evidence.level is EvidenceLevel.CONFIGURED
    assert item(result.inventory.items, "~/.codex/AGENTS.md").evidence.level is EvidenceLevel.LOADED


def test_prompts_and_disabled_servers_are_installed_only(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    assert roots.project_dir is not None
    write(roots.codex_home / "prompts" / "review.md", "Review the diff.\n")
    write(
        roots.codex_home / "config.toml",
        'model = "gpt-5-codex"\nmodel_reasoning_effort = "high"\n'
        '[mcp_servers.github]\ncommand = "gh-mcp"\n'
        '[mcp_servers.old]\ncommand = "old-mcp"\nenabled = false\n',
    )
    record = CodexInvocation.from_dict(codex_invocation(roots.project_dir))
    result = collect_codex(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    items = result.inventory.items
    assert item(items, "~/.codex/prompts/review.md").evidence.level is EvidenceLevel.INSTALLED_ONLY
    assert item(items, "config.toml#old").evidence.level is EvidenceLevel.INSTALLED_ONLY
    assert item(items, "config.toml#github").evidence.level is EvidenceLevel.CONFIGURED


def test_unparseable_config_and_uncovered_version_are_unknown(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=False)
    assert roots.project_dir is not None
    write(roots.codex_home / "config.toml", "model = [unterminated")
    add_codex_rollout(roots.home, roots.project_dir, version="0.100.0", instructions=GLOBAL_RULES)
    record = CodexInvocation.from_dict(codex_invocation(roots.project_dir))
    record = CodexInvocation(record.argv, record.cwd, record.environment_names, cli_version="")
    result = collect_codex(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    items = result.inventory.items
    config = item(items, "~/.codex/config.toml")
    assert config.evidence.level is EvidenceLevel.UNKNOWN
    assert config.evidence.missing_evidence
    agents = item(items, "~/.codex/AGENTS.md")
    assert agents.evidence.level is EvidenceLevel.UNKNOWN
    assert any("0.100.0" in missing for missing in agents.evidence.missing_evidence)


# --------------------------------------------------------------------------- #
# Settings, overrides, bot additions — recorded, never changed
# --------------------------------------------------------------------------- #


def test_model_and_reasoning_settings_are_recorded_and_left_untouched(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    assert roots.project_dir is not None
    config_path = roots.codex_home / "config.toml"
    before = hashlib.sha256(config_path.read_bytes()).hexdigest()
    record = CodexInvocation.from_dict(codex_invocation(roots.project_dir))
    result = collect_codex(
        discover(roots, salt="t"),
        machine=Machine.DREWAI,
        invocation=record,
        manifest=manifest(),
        collected_at=COLLECTED_AT,
        salt="t",
    )
    assert hashlib.sha256(config_path.read_bytes()).hexdigest() == before

    effort = item(result.inventory.items, "harness-setting/model_reasoning_effort")
    assert effort.kind is SourceKind.HARNESS_SETTING
    assert "excluded from cleanup" in effort.effective_behavior
    references = [source.reference for source in effort.sources]
    assert references == ["invocation -c model_reasoning_effort", "~/.codex/config.toml"]
    assert effort.effective_source.scope is Scope.SESSION
    assert "medium" in effort.label and "high" in effort.label

    model = item(result.inventory.items, "harness-setting/model")
    assert "gpt-5-codex" in model.label
    assert "excluded from cleanup" in model.effective_behavior

    sandbox = item(result.inventory.items, "harness-setting/sandbox_mode")
    assert "workspace-write" in sandbox.label


def test_developer_instructions_override_is_a_withheld_bot_addition(tmp_path: Path) -> None:
    result = collect(
        tmp_path, rollouts=False, extra=("-c", f"developer_instructions={FAKE_SYSTEM_PROMPT}")
    )
    addition = item(result.inventory.items, "bot-addition/developer_instructions")
    assert addition.kind is SourceKind.BOT_ADDITION
    assert addition.evidence.level is EvidenceLevel.LOADED
    assert addition.size is not None and addition.size.byte_size == len(FAKE_SYSTEM_PROMPT.encode())
    assert [body.field for body in result.private_bodies] == [
        "codex/bot-addition/developer_instructions"
    ]
    assert FAKE_SYSTEM_PROMPT not in to_json(result.inventory)


def test_unknown_override_values_are_withheld(tmp_path: Path) -> None:
    result = collect(
        tmp_path, rollouts=False, extra=("-c", "openai_api_key=sk-FAKEFAKEFAKEFAKEFAKE1")
    )
    override = item(result.inventory.items, "harness-setting/openai_api_key")
    assert "sk-FAKE" not in override.label
    assert "sk-FAKE" not in to_json(result.inventory)


def test_environment_names_are_kept_and_values_never_exist(tmp_path: Path) -> None:
    result = collect(tmp_path, rollouts=False)
    assert (
        item(result.inventory.items, "environment/CODEX_HOME").evidence.level
        is EvidenceLevel.LOADED
    )
    assert result.environment == {
        "CODEX_HOME": "[redacted]",
        "OPENAI_API_KEY": "[redacted]",
        "PATH": "[redacted]",
    }


def test_without_an_invocation_nothing_is_loaded(tmp_path: Path) -> None:
    result = collect(tmp_path, rollouts=True, invocation=False)
    assert all(entry.evidence.level is not EvidenceLevel.LOADED for entry in result.inventory.items)
    assert "invocation" in " ".join(result.notes).lower()


def test_result_serializes_into_a_bundle_without_any_fixture_secret(tmp_path: Path) -> None:
    result = collect(
        tmp_path, rollouts=True, extra=("-c", f"developer_instructions={FAKE_SYSTEM_PROMPT}")
    )
    bundle = build_redacted_bundle(
        machine=Machine.DREWAI,
        created_at=COLLECTED_AT,
        inventories=(result.inventory,),
        private_bodies=result.private_bodies,
        environment=result.environment,
    )
    serialized = serialize_bundle(bundle)
    assert FAKE_NUMERIC_PASSWORD not in serialized
    assert FAKE_SYSTEM_PROMPT not in serialized
    assert "Always run the tests" not in serialized
    assert result.inventory.target == TARGET


def test_read_codex_rollouts_keeps_metadata_and_hashes_the_instructions(tmp_path: Path) -> None:
    roots = fake_home(tmp_path, with_transcripts=True)
    rollouts = read_codex_rollouts(discover(roots, salt="t").transcripts, salt="t")
    assert len(rollouts) == 1
    rollout = rollouts[0]
    assert rollout.version == "0.147.0"
    assert rollout.cwd.endswith("projects/relay")
    assert rollout.instructions_hash.startswith("sha256:")
    assert set(rollout.skill_names) == {"release", "deploy"}
    assert "Always run the tests" not in to_json(rollout)
