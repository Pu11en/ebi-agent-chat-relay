"""Harness adapter tests (task 2.2): Claude, Codex and DSH from fixture homes.

Each adapter reads one declared root and reports commands, hooks, plugins,
connectors and harness settings as safe metadata.  The tests hold them to the
spec's honesty rules: a file is *discovered* or *configured*, never verified
without loader evidence; a harness that is not configured on this computer is
*unsupported*; a declared subscription or machine exception is a *deliberate*
difference; and no credential value — settings ``env``, hook command flags,
connector ``env`` — ever reaches an item or a diagnostic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claude_discord.ai_setup_adapters import (
    ClaudeHarnessAdapter,
    CodexHarnessAdapter,
    DeliberateException,
    DshHarnessAdapter,
    HarnessProfile,
    LoaderEvidence,
    SetupRoot,
    claude_home_root,
    codex_home_root,
    dsh_config_root,
    load_exceptions,
    project_root,
)
from claude_discord.ai_setup_collector import CollectionContext, InventoryCollector, SetupAdapter
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    InventoryItem,
    PrerequisiteKind,
    PrerequisiteState,
    ScopeKind,
    SetupKind,
)

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
LEAKED = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake, used to prove redaction
HOOK_TOKEN = "ghp_abcdefghijklmnopqrstuv0123456789"  # noqa: S105 — a fake


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def context(**overrides: object) -> CollectionContext:
    defaults: dict[str, object] = {
        "computer": "drewai",
        "owner": "drew",
        "collected_at": NOW,
        "harnesses": ("claude", "codex", "dsh"),
    }
    defaults.update(overrides)
    return CollectionContext(**defaults)  # pyright: ignore[reportArgumentType]


def collect(*adapters: SetupAdapter, ctx: CollectionContext | None = None):
    collector = InventoryCollector()
    for adapter in adapters:
        collector.register(adapter)
    return collector.collect(ctx or context())


def by_name(items: tuple[InventoryItem, ...], kind: SetupKind, name: str) -> InventoryItem:
    for item in items:
        if item.kind is kind and item.identity.name == name:
            return item
    keys = [i.identity.key for i in items]
    raise AssertionError(f"no {kind.value} item named {name!r} in {keys}")


def all_text(result) -> str:
    return repr(result.snapshot.items) + repr(result.snapshot.diagnostics)


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    home = tmp_path / "home" / ".claude"
    write(
        home / "settings.json",
        json.dumps(
            {
                "model": "opus",
                "permissions": {"allow": ["Bash(uv:*)"]},
                "env": {"ANTHROPIC_API_KEY": LEAKED, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8000"},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": f"guard.py --token={HOOK_TOKEN}"}
                            ],
                        }
                    ],
                    "Stop": [{"hooks": [{"type": "command", "command": "notify.sh"}]}],
                },
                "enabledPlugins": {"docs@claude-plugins-official": True},
            }
        ),
    )
    write(home / "settings.local.json", json.dumps({"model": "sonnet"}))
    write(home / "commands" / "verify.md", "Run the verification pipeline.\n")
    write(
        home
        / "plugins"
        / "marketplaces"
        / "claude-plugins-official"
        / "plugins"
        / "docs"
        / "plugin.json",
        json.dumps({"name": "docs"}),
    )
    write(
        tmp_path / "home" / ".claude.json",
        json.dumps(
            {
                "oauthAccount": {"accessToken": LEAKED},
                "mcpServers": {
                    "claude-docs": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "docs-mcp", f"--key={LEAKED}"],
                        "env": {"DOCS_TOKEN": LEAKED, "DOCS_REGION": ""},
                    },
                    "remote": {"type": "http", "url": f"https://mcp.example.com/?token={LEAKED}"},
                },
            }
        ),
    )
    return home


def claude_adapter(home: Path, tmp_path: Path, **kwargs: object) -> ClaudeHarnessAdapter:
    root = claude_home_root(home, owner="drew", home=tmp_path / "home")
    return ClaudeHarnessAdapter(root, **kwargs)  # pyright: ignore[reportArgumentType]


class TestClaudeAdapter:
    def test_declares_its_boundary(self, claude_home: Path, tmp_path: Path):
        adapter = claude_adapter(claude_home, tmp_path)
        assert adapter.name == "claude-harness"
        assert set(adapter.kinds) == {
            SetupKind.COMMAND,
            SetupKind.HOOK,
            SetupKind.PLUGIN,
            SetupKind.CONNECTOR,
            SetupKind.HARNESS_SETTING,
        }
        assert list(adapter.source_keys) == ["claude-home"]

    def test_settings_become_safe_setting_items(self, claude_home: Path, tmp_path: Path):
        result = collect(claude_adapter(claude_home, tmp_path))
        items = result.snapshot.items

        model = by_name(items, SetupKind.HARNESS_SETTING, "model")
        assert model.summary == "opus"
        assert model.classification is Classification.CUSTOM
        assert model.source.locator == "~/.claude/settings.json"
        assert model.state_for("claude") is AvailabilityState.CONFIGURED
        assert by_name(items, SetupKind.HARNESS_SETTING, "permissions").summary == "1 entry"
        env = by_name(items, SetupKind.HARNESS_SETTING, "env")
        assert env.summary == "2 variables set"
        assert LEAKED not in all_text(result)
        assert "ANTHROPIC_API_KEY" not in all_text(result)

    def test_local_settings_are_one_computer(self, claude_home: Path, tmp_path: Path):
        result = collect(claude_adapter(claude_home, tmp_path))
        local = by_name(result.snapshot.items, SetupKind.HARNESS_SETTING, "local/model")
        assert local.scope.kind is ScopeKind.COMPUTER
        assert local.source.locator == "~/.claude/settings.local.json"
        assert local.summary == "sonnet"

    def test_hooks_are_listed_per_event_with_redacted_commands(
        self, claude_home: Path, tmp_path: Path
    ):
        result = collect(claude_adapter(claude_home, tmp_path))
        hook = by_name(result.snapshot.items, SetupKind.HOOK, "PreToolUse/Bash")
        assert hook.summary is not None
        assert "guard.py" in hook.summary
        assert HOOK_TOKEN not in all_text(result)
        stop = by_name(result.snapshot.items, SetupKind.HOOK, "Stop")
        assert stop.summary == "notify.sh"
        assert stop.state_for("claude") is AvailabilityState.CONFIGURED

    def test_commands_are_discovered(self, claude_home: Path, tmp_path: Path):
        result = collect(claude_adapter(claude_home, tmp_path))
        command = by_name(result.snapshot.items, SetupKind.COMMAND, "verify")
        assert command.source.locator == "~/.claude/commands/verify.md"
        assert command.state_for("claude") is AvailabilityState.DISCOVERED
        assert command.fingerprint is not None

    def test_enabled_marketplace_plugin_is_an_overridden_builtin(
        self, claude_home: Path, tmp_path: Path
    ):
        result = collect(claude_adapter(claude_home, tmp_path))
        plugin = by_name(result.snapshot.items, SetupKind.PLUGIN, "docs@claude-plugins-official")
        assert plugin.classification is Classification.OVERRIDDEN_BUILTIN
        assert plugin.state_for("claude") is AvailabilityState.CONFIGURED

    def test_a_marketplace_plugin_that_is_not_enabled_is_a_builtin(
        self, claude_home: Path, tmp_path: Path
    ):
        write(
            claude_home
            / "plugins"
            / "marketplaces"
            / "claude-plugins-official"
            / "plugins"
            / "x"
            / "plugin.json",
            "{}",
        )
        result = collect(claude_adapter(claude_home, tmp_path))
        plugin = by_name(result.snapshot.items, SetupKind.PLUGIN, "x@claude-plugins-official")
        assert plugin.classification is Classification.BUILTIN
        assert plugin not in result.snapshot.custom_items

    def test_connectors_report_credential_presence_not_values(
        self, claude_home: Path, tmp_path: Path
    ):
        result = collect(claude_adapter(claude_home, tmp_path))
        docs = by_name(result.snapshot.items, SetupKind.CONNECTOR, "claude-docs")
        assert docs.summary == "stdio: npx"
        assert docs.source.locator == "~/.claude.json"
        states = {p.name: p.state for p in docs.prerequisites}
        assert states == {
            "DOCS_TOKEN": PrerequisiteState.SATISFIED,
            "DOCS_REGION": PrerequisiteState.MISSING,
        }
        assert all(p.kind is PrerequisiteKind.CREDENTIAL for p in docs.prerequisites)
        assert docs.state_for("claude") is AvailabilityState.MISSING_PREREQUISITE
        remote = by_name(result.snapshot.items, SetupKind.CONNECTOR, "remote")
        assert remote.summary == "http: mcp.example.com"
        assert LEAKED not in all_text(result)
        assert "oauthAccount" not in all_text(result)

    def test_project_settings_and_connectors_are_project_scoped(self, tmp_path: Path):
        project = tmp_path / "work" / "ccdb"
        write(project / ".claude" / "settings.json", json.dumps({"model": "haiku"}))
        write(project / ".mcp.json", json.dumps({"mcpServers": {"gh": {"command": "gh-mcp"}}}))
        write(project / ".claude" / "commands" / "ship.md", "Ship it.\n")
        root = project_root(project, name="ccdb")
        result = collect(ClaudeHarnessAdapter(root))
        items = result.snapshot.items
        assert by_name(items, SetupKind.HARNESS_SETTING, "model").scope.kind is ScopeKind.PROJECT
        assert by_name(items, SetupKind.CONNECTOR, "gh").scope.project == "ccdb"
        assert by_name(items, SetupKind.COMMAND, "ship").source.key == "project-ccdb"

    def test_malformed_settings_cost_one_diagnostic_not_the_rest(
        self, claude_home: Path, tmp_path: Path
    ):
        write(claude_home / "settings.json", "{not json")
        result = collect(claude_adapter(claude_home, tmp_path))
        assert result.failed_adapters == ()
        assert by_name(result.snapshot.items, SetupKind.COMMAND, "verify")
        diagnostics = result.snapshot.diagnostics_for("claude-home")
        assert any("settings.json" in d.message for d in diagnostics)

    def test_a_missing_home_is_reported_not_raised(self, tmp_path: Path):
        adapter = ClaudeHarnessAdapter(claude_home_root(tmp_path / "none", owner="drew"))
        result = collect(adapter)
        assert result.failed_adapters == ()
        assert result.snapshot.items == ()
        assert result.snapshot.diagnostics_for("claude-home")


# ---------------------------------------------------------------------------
# Evidence, profiles and deliberate exceptions
# ---------------------------------------------------------------------------


class TestEvidenceAndProfiles:
    def test_loader_evidence_upgrades_discovered_to_verified(
        self, claude_home: Path, tmp_path: Path
    ):
        evidence = LoaderEvidence(
            harness="claude",
            verified_at=NOW,
            loaded=frozenset({"command:claude-home:verify"}),
            evidence="claude --print listed the command",
        )
        result = collect(claude_adapter(claude_home, tmp_path, evidence=(evidence,)))
        command = by_name(result.snapshot.items, SetupKind.COMMAND, "verify")
        assert command.is_verified_on("claude")
        assert command.availability_for("claude").verified_at == NOW  # type: ignore[union-attr]
        model = by_name(result.snapshot.items, SetupKind.HARNESS_SETTING, "model")
        assert not model.is_verified_on("claude")

    def test_evidence_for_another_harness_does_not_verify(self, claude_home: Path, tmp_path: Path):
        evidence = LoaderEvidence(
            harness="codex",
            verified_at=NOW,
            loaded=frozenset({"command:claude-home:verify"}),
            evidence="codex rollout",
        )
        result = collect(claude_adapter(claude_home, tmp_path, evidence=(evidence,)))
        command = by_name(result.snapshot.items, SetupKind.COMMAND, "verify")
        assert not command.has_verified_availability

    def test_an_unconfigured_harness_marks_everything_unsupported(
        self, claude_home: Path, tmp_path: Path
    ):
        profile = HarnessProfile(harness="claude", configured=False)
        result = collect(claude_adapter(claude_home, tmp_path, profile=profile))
        command = by_name(result.snapshot.items, SetupKind.COMMAND, "verify")
        entry = command.availability_for("claude")
        assert entry is not None
        assert entry.state is AvailabilityState.UNSUPPORTED
        assert "not configured on this computer" in (entry.detail or "")

    def test_a_deliberate_exception_is_a_missing_prerequisite_with_its_reason(
        self, claude_home: Path, tmp_path: Path
    ):
        profile = HarnessProfile(
            harness="claude",
            exceptions=(
                DeliberateException(
                    item="connector:claude-home:remote",
                    reason="Remote MCP needs the Team plan",
                    kind=PrerequisiteKind.SUBSCRIPTION,
                ),
            ),
        )
        result = collect(claude_adapter(claude_home, tmp_path, profile=profile))
        remote = by_name(result.snapshot.items, SetupKind.CONNECTOR, "remote")
        entry = remote.availability_for("claude")
        assert entry is not None
        assert entry.state is AvailabilityState.MISSING_PREREQUISITE
        assert "deliberate" in (entry.detail or "").lower()
        exception = [p for p in remote.prerequisites if p.kind is PrerequisiteKind.SUBSCRIPTION]
        assert exception and exception[0].is_missing
        assert exception[0].name == "Remote MCP needs the Team plan"
        assert exception[0].detail is not None and "deliberate" in exception[0].detail.lower()

    def test_exceptions_load_from_a_json_file_and_match_by_prefix(self, tmp_path: Path):
        path = write(
            tmp_path / "exceptions.json",
            json.dumps(
                [
                    {"item": "connector:claude-home:", "reason": "No MCP on the iMac"},
                    {"item": "plugin:", "reason": "Plugins need Max", "kind": "subscription"},
                ]
            ),
        )
        loaded = load_exceptions(path)
        assert [entry.item for entry in loaded] == ["connector:claude-home:", "plugin:"]
        assert loaded[0].kind is PrerequisiteKind.OTHER
        assert loaded[1].kind is PrerequisiteKind.SUBSCRIPTION
        assert loaded[0].applies_to("connector:claude-home:remote")
        assert not loaded[0].applies_to("plugin:claude-home:x")

    def test_a_malformed_exceptions_file_is_empty_not_fatal(self, tmp_path: Path):
        assert load_exceptions(write(tmp_path / "bad.json", "[{}]")) == ()
        assert load_exceptions(tmp_path / "missing.json") == ()


# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / "home" / ".codex"
    write(
        home / "config.toml",
        "\n".join(
            [
                'model = "gpt-5-codex"',
                'model_reasoning_effort = "high"',
                "[features]",
                "web_search_request = true",
                "[mcp_servers.docs]",
                'command = "npx"',
                'args = ["-y", "docs-mcp"]',
                f'env = {{ DOCS_TOKEN = "{LEAKED}" }}',
                "[mcp_servers.remote]",
                'url = "https://mcp.example.com/sse"',
                'bearer_token_env_var = "REMOTE_TOKEN"',
                "[hooks]",
                f'before_run = "hook.sh --token {HOOK_TOKEN}"',
                "[profiles.fast]",
                'model = "gpt-5-mini"',
            ]
        )
        + "\n",
    )
    write(home / "prompts" / "review.md", "Review the diff.\n")
    return home


def codex_adapter(home: Path, tmp_path: Path, **kwargs: object) -> CodexHarnessAdapter:
    root = codex_home_root(home, owner="drew", home=tmp_path / "home")
    return CodexHarnessAdapter(root, **kwargs)  # pyright: ignore[reportArgumentType]


class TestCodexAdapter:
    def test_declares_its_boundary(self, codex_home: Path, tmp_path: Path):
        adapter = codex_adapter(codex_home, tmp_path)
        assert adapter.name == "codex-harness"
        assert list(adapter.source_keys) == ["codex-home"]

    def test_config_scalars_tables_and_prompts(self, codex_home: Path, tmp_path: Path):
        result = collect(codex_adapter(codex_home, tmp_path))
        items = result.snapshot.items
        assert result.failed_adapters == ()
        assert by_name(items, SetupKind.HARNESS_SETTING, "model").summary == "gpt-5-codex"
        assert by_name(items, SetupKind.HARNESS_SETTING, "model_reasoning_effort").summary == "high"
        features = by_name(items, SetupKind.HARNESS_SETTING, "features")
        assert features.summary == "1 entry"
        assert by_name(items, SetupKind.HARNESS_SETTING, "profiles.fast").summary == "1 entry"
        prompt = by_name(items, SetupKind.COMMAND, "review")
        assert prompt.source.locator == "~/.codex/prompts/review.md"
        assert prompt.state_for("codex") is AvailabilityState.DISCOVERED
        assert prompt.state_for("claude") is AvailabilityState.UNSUPPORTED

    def test_connectors_and_hooks_never_carry_values(self, codex_home: Path, tmp_path: Path):
        result = collect(codex_adapter(codex_home, tmp_path))
        docs = by_name(result.snapshot.items, SetupKind.CONNECTOR, "docs")
        assert docs.summary == "stdio: npx"
        assert {p.name: p.state for p in docs.prerequisites} == {
            "DOCS_TOKEN": PrerequisiteState.SATISFIED
        }
        remote = by_name(result.snapshot.items, SetupKind.CONNECTOR, "remote")
        assert remote.summary == "http: mcp.example.com"
        assert {p.name: p.state for p in remote.prerequisites} == {
            "REMOTE_TOKEN": PrerequisiteState.MISSING
        }
        hook = by_name(result.snapshot.items, SetupKind.HOOK, "before_run")
        assert hook.summary is not None and "hook.sh" in hook.summary
        assert LEAKED not in all_text(result)
        assert HOOK_TOKEN not in all_text(result)

    def test_remote_connector_prerequisite_reads_the_environment_given(
        self, codex_home: Path, tmp_path: Path
    ):
        adapter = codex_adapter(codex_home, tmp_path, environ={"REMOTE_TOKEN": "remote-value-7731"})
        result = collect(adapter)
        remote = by_name(result.snapshot.items, SetupKind.CONNECTOR, "remote")
        assert remote.prerequisites[0].is_satisfied
        assert "remote-value-7731" not in all_text(result)

    def test_malformed_toml_is_one_diagnostic(self, codex_home: Path, tmp_path: Path):
        write(codex_home / "config.toml", "model = \n")
        result = collect(codex_adapter(codex_home, tmp_path))
        assert result.failed_adapters == ()
        assert by_name(result.snapshot.items, SetupKind.COMMAND, "review")
        assert any("config.toml" in d.message for d in result.snapshot.diagnostics)


# ---------------------------------------------------------------------------
# DSH
# ---------------------------------------------------------------------------


class TestDshAdapter:
    def test_routes_report_credential_presence_and_the_patch_file(self, tmp_path: Path):
        config = tmp_path / "home" / ".config" / "ccdb" / "dsh"
        write(config / "providers.patch.yml", f"routes:\n  zai:\n    apiKey: {LEAKED}\n")
        root = dsh_config_root(config, owner="drew", home=tmp_path / "home")
        adapter = DshHarnessAdapter(
            root, environ={"DEEPSEEK_API_KEY": "x", "ZAI_BASE_URL": "https://alt.example"}
        )
        assert adapter.name == "dsh-harness"
        result = collect(adapter)
        items = result.snapshot.items

        deepseek = by_name(items, SetupKind.CONNECTOR, "deepseek-official")
        assert deepseek.prerequisites[0].name == "DEEPSEEK_API_KEY"
        assert deepseek.prerequisites[0].is_satisfied
        assert deepseek.state_for("dsh") is AvailabilityState.CONFIGURED
        zai = by_name(items, SetupKind.CONNECTOR, "zai")
        assert zai.prerequisites[0].is_missing
        assert zai.state_for("dsh") is AvailabilityState.MISSING_PREREQUISITE
        override = by_name(items, SetupKind.HARNESS_SETTING, "ZAI_BASE_URL")
        assert override.summary == "set"
        assert override.scope.kind is ScopeKind.COMPUTER
        patch = by_name(items, SetupKind.HARNESS_SETTING, "providers.patch.yml")
        assert patch.source.locator == "~/.config/ccdb/dsh/providers.patch.yml"
        assert patch.fingerprint is not None
        assert LEAKED not in all_text(result)
        assert "https://alt.example" not in all_text(result)

    def test_without_the_config_directory_routes_are_still_inventoried(self, tmp_path: Path):
        root = dsh_config_root(tmp_path / "none", owner="drew")
        result = collect(DshHarnessAdapter(root, environ={}))
        zai = by_name(result.snapshot.items, SetupKind.CONNECTOR, "zai")
        assert zai.prerequisites[0].is_missing
        assert result.failed_adapters == ()


def test_every_harness_adapter_is_a_setup_adapter(tmp_path: Path):
    root: SetupRoot = claude_home_root(tmp_path, owner="drew")
    assert isinstance(ClaudeHarnessAdapter(root), SetupAdapter)
    assert isinstance(CodexHarnessAdapter(codex_home_root(tmp_path, owner="drew")), SetupAdapter)
    assert isinstance(DshHarnessAdapter(dsh_config_root(tmp_path, owner="drew")), SetupAdapter)
