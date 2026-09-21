"""MyAISetupCog tests (tasks 4.3 and 4.4): a Settings entry, an ephemeral view, a safe handoff.

The Cog registers itself as a Settings entry and opens Browse by kind
without a model turn; every inventory read goes to fixture homes handed in
through the collector, never to the real home. Ask Setup Agent creates one
normal session through ``spawn_session`` with a bounded packet of safe
facts and the user's question, and changes nothing itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.ai_setup_agent import MAX_PACKET_CHARS, build_setup_agent_packet
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    EffectiveScope,
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
from claude_discord.ai_setup_local import LocalRoots, build_local_collector
from claude_discord.cogs.my_ai_setup import ENTRY_KEY, MyAISetupCog
from claude_discord.database.ai_setup_repo import AISetupRepository
from claude_discord.discord_ui import settings_home
from claude_discord.discord_ui.my_ai_setup import ItemDetailView, MyAISetupView
from claude_discord.discord_ui.settings_home import SettingsHome, SupportedFeatures

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
LEAKED = "sk-ant-api03-ZZaabbccddeeff112233"  # noqa: S105 — a fake
HOOK_TOKEN = "ghp_abcdefghijklmnopqrstuv0123456789"  # noqa: S105 — a fake


@pytest.fixture
def fixture_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    claude = home / ".claude"
    (claude / "skills" / "grilling").mkdir(parents=True)
    (claude / "skills" / "grilling" / "SKILL.md").write_text(
        "---\ndescription: How to grill\n---\nGrill things.\n", encoding="utf-8"
    )
    (claude / "CLAUDE.md").write_text("Be brief.\n", encoding="utf-8")
    (claude / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "env": {"ANTHROPIC_API_KEY": LEAKED},
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": f"n --t={HOOK_TOKEN}"}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "npx", "env": {"DOCS_TOKEN": LEAKED}}}}),
        encoding="utf-8",
    )
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-5-codex"\n', encoding="utf-8")
    (home / "cogs").mkdir()
    (home / "cogs" / "reminder.py").write_text("async def setup(b, r, c): pass\n", encoding="utf-8")
    return home


def roots_for(home: Path) -> LocalRoots:
    return LocalRoots(
        home=home,
        claude_home=home / ".claude",
        codex_home=home / ".codex",
        dsh_config=home / ".config" / "ccdb" / "dsh",
        cogs_dir=home / "cogs",
        project=None,
        owner="drew",
    )


class TestLocalCollector:
    def test_builds_one_adapter_per_declared_root(self, fixture_home: Path):
        collector = build_local_collector(roots_for(fixture_home), environ={})
        assert collector.registry.adapter_names == (
            "shared-setup",
            "claude-harness",
            "codex-harness",
            "dsh-harness",
            "discord-extensions",
        )

    def test_roots_can_be_read_from_the_environment(self, fixture_home: Path):
        env = {
            "CODEX_HOME": str(fixture_home / "elsewhere" / "codex"),
            "CUSTOM_COGS_DIR": str(fixture_home / "cogs"),
            "CCDB_AI_SETUP_OWNER": "Drew",
        }
        roots = LocalRoots.from_env(env, home=fixture_home, working_dir=str(fixture_home / "proj"))
        assert roots.claude_home == fixture_home / ".claude"
        assert roots.codex_home == fixture_home / "elsewhere" / "codex"
        assert roots.cogs_dir == fixture_home / "cogs"
        assert roots.project == fixture_home / "proj"
        assert roots.owner == "Drew"


def interaction(user: int = 42, *, guild: int | None = 10, thread: bool = False):
    event = MagicMock(spec=discord.Interaction)
    event.user = SimpleNamespace(id=user, display_name="drew")
    event.guild_id = guild
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 500
    channel.category_id = None
    if thread:
        parent = channel
        channel = MagicMock(spec=discord.Thread)
        channel.id = 600
        channel.parent = parent
        channel.parent_id = 500
    event.channel = channel
    event.channel_id = channel.id
    event.response = MagicMock()
    event.response.is_done = MagicMock(return_value=False)
    event.response.send_message = AsyncMock()
    event.response.defer = AsyncMock()
    event.response.send_modal = AsyncMock()
    event.response.edit_message = AsyncMock()
    event.followup = MagicMock()
    event.followup.send = AsyncMock()
    event.edit_original_response = AsyncMock()
    return event


@pytest.fixture
async def repo(tmp_path: Path) -> AISetupRepository:
    repository = AISetupRepository(str(tmp_path / "ai_setup.db"))
    await repository.init_db()
    return repository


@pytest.fixture
def chat() -> MagicMock:
    cog = MagicMock()
    cog.spawn_session = AsyncMock()
    cog.spawn_session.return_value = SimpleNamespace(id=777, mention="<#777>")
    cog._run_claude = AsyncMock()
    cog._allowed_user_ids = {42}
    return cog


@pytest.fixture
def cog(fixture_home: Path, repo: AISetupRepository, chat: MagicMock, monkeypatch) -> MyAISetupCog:
    monkeypatch.setattr(settings_home, "_REGISTERED", [])
    monkeypatch.setenv("CCDB_COMPUTER_NAME", "DrewAI")
    bot = MagicMock()
    bot.user = SimpleNamespace(display_name="EbiBot")
    collector = build_local_collector(roots_for(fixture_home), environ={})
    home = SettingsHome(SupportedFeatures(frozenset({"claude"})))
    return MyAISetupCog(
        bot,
        repo=repo,
        collector=collector,
        chat=chat,
        allowed_user_ids={42},
        owner="drew",
        settings_home=home,
        clock=lambda: NOW,
    )


class TestSettingsEntry:
    async def test_cog_load_registers_one_settings_entry_without_owning_settings(
        self, cog: MyAISetupCog
    ):
        await cog.cog_load()
        registered = [entry for entry in settings_home._REGISTERED if entry.key == ENTRY_KEY]
        assert len(registered) == 1
        assert registered[0].label == "My AI Setup"
        assert registered[0].open is not None
        assert cog.settings_home is not None
        assert [entry.key for entry in cog.settings_home.visible() if entry.key == ENTRY_KEY]
        # No slash command of its own — Settings owns the navigation.
        assert not hasattr(cog, "ai_setup_command")
        await cog.cog_load()  # idempotent: still one entry
        assert sum(1 for entry in settings_home._REGISTERED if entry.key == ENTRY_KEY) == 1

    async def test_the_settings_button_opens_browse_by_kind(self, cog: MyAISetupCog):
        home = SettingsHome(SupportedFeatures(frozenset({"claude"})), entries=[cog.entry])
        _, view = home.render("DrewAI", user_id=42)
        button = next(child for child in view.children if isinstance(child, discord.ui.Button))
        event = interaction()
        await button.callback(event)
        event.response.defer.assert_awaited_once_with(ephemeral=True)
        kwargs = event.followup.send.await_args.kwargs
        text = event.followup.send.await_args.args[0]
        assert kwargs["ephemeral"] is True
        assert "Browse by kind" in text
        assert "grilling" in text and "CLAUDE.md" in text
        assert isinstance(kwargs["view"], MyAISetupView)


class TestOpen:
    async def test_open_spends_no_model_tokens_and_reads_only_fixture_homes(
        self, cog: MyAISetupCog, chat: MagicMock, repo: AISetupRepository
    ):
        event = interaction()
        await cog.open(event)
        chat.spawn_session.assert_not_awaited()
        chat._run_claude.assert_not_awaited()
        text = event.followup.send.await_args.args[0]
        assert LEAKED not in text and HOOK_TOKEN not in text
        assert "reminder" in text or "Commands" in text or "Plugins" in text
        stored = await repo.load_snapshot("drewai")
        assert stored is not None and stored.items

    async def test_an_unauthorized_user_sees_no_inventory(self, cog: MyAISetupCog):
        event = interaction(user=7)
        await cog.open(event)
        event.followup.send.assert_not_awaited()
        message = event.response.send_message.await_args.args[0]
        assert "authorized" in message.lower()
        assert "grilling" not in message
        outside = interaction(guild=None)
        await cog.open(outside)
        outside.followup.send.assert_not_awaited()

    async def test_refresh_recollects_and_edits_the_same_message(self, cog: MyAISetupCog):
        event = interaction()
        await cog.open(event)
        view = event.followup.send.await_args.kwargs["view"]
        refresh = next(
            child
            for child in view.children
            if isinstance(child, discord.ui.Button) and child.label == "Refresh"
        )
        assert not refresh.disabled
        again = interaction()
        await refresh.callback(again)
        again.response.edit_message.assert_awaited_once()
        assert "Browse by kind" in again.response.edit_message.await_args.kwargs["content"]


def skill_item() -> InventoryItem:
    return InventoryItem(
        identity=ItemIdentity(kind=SetupKind.SKILL, source_key="claude-home", name="grilling"),
        display_name="grilling",
        source=InventorySource(
            key="claude-home",
            computer="drewai",
            label="Claude home",
            locator="~/.claude/skills/grilling/SKILL.md",
            harness="claude",
            modified_at=NOW,
        ),
        scope=EffectiveScope.shared_profile("drew"),
        availability=(
            HarnessAvailability(
                harness="claude", state=AvailabilityState.DISCOVERED, computer="drewai"
            ),
        ),
        prerequisites=(
            Prerequisite(
                name="GRILL_TOKEN",
                state=PrerequisiteState.MISSING,
                kind=PrerequisiteKind.CREDENTIAL,
            ),
        ),
        last_changed_at=NOW,
        summary="How to grill",
    )


class TestPacket:
    def test_the_packet_carries_safe_facts_the_question_and_the_rules(self):
        packet = build_setup_agent_packet(
            skill_item(), question="Why is this skill not loading on Codex?", computer="DrewAI"
        )
        assert len(packet) <= MAX_PACKET_CHARS
        assert "skill:claude-home:grilling" in packet
        assert "~/.claude/skills/grilling/SKILL.md" in packet
        assert "Shared Drew profile" in packet
        assert "claude: Discovered" in packet
        assert "GRILL_TOKEN: missing (credential)" in packet
        assert "Why is this skill not loading on Codex?" in packet
        assert "Do not edit" in packet
        assert "Mega" not in packet and "ownership" not in packet.lower()

    def test_a_pasted_secret_in_the_question_is_redacted_and_the_packet_stays_bounded(self):
        packet = build_setup_agent_packet(
            skill_item(), question=f"my key is {LEAKED} " + "x" * 5000, computer="DrewAI"
        )
        assert LEAKED not in packet
        assert "[redacted]" in packet
        assert len(packet) <= MAX_PACKET_CHARS


class TestAskSetupAgent:
    async def test_creates_one_session_with_the_safe_packet_and_edits_nothing(
        self, cog: MyAISetupCog, chat: MagicMock, fixture_home: Path
    ):
        before = {path: path.read_bytes() for path in fixture_home.rglob("*") if path.is_file()}
        event = interaction()
        await cog.open(event)
        view = event.followup.send.await_args.kwargs["view"]
        picker = next(child for child in view.children if isinstance(child, discord.ui.Select))
        chosen = next(option for option in picker.options if option.label == "grilling")
        picker._values = [chosen.value]
        detail_event = interaction()
        await picker.callback(detail_event)
        detail = detail_event.response.edit_message.await_args.kwargs["view"]
        assert isinstance(detail, ItemDetailView)
        ask = next(
            child
            for child in detail.children
            if isinstance(child, discord.ui.Button) and child.label == "Ask Setup Agent"
        )
        assert not ask.disabled
        modal_event = interaction()
        await ask.callback(modal_event)
        modal = modal_event.response.send_modal.await_args.args[0]
        modal.question._value = "Is this skill loaded by Codex too?"
        submit = interaction()
        await modal.on_submit(submit)

        chat.spawn_session.assert_awaited_once()
        kwargs = chat.spawn_session.await_args.kwargs
        assert kwargs["thread_name"] == "Setup: grilling"
        assert kwargs["invite_user_id"] == 42
        prompt = kwargs["prompt"]
        assert "Is this skill loaded by Codex too?" in prompt
        assert "~/.claude/skills/grilling/SKILL.md" in prompt
        assert "How to grill" in prompt
        assert LEAKED not in prompt and HOOK_TOKEN not in prompt
        assert "Grill things" not in prompt  # no raw file content
        assert "Do not edit" in prompt
        assert "<#777>" in submit.followup.send.await_args.args[0]
        after = {path: path.read_bytes() for path in fixture_home.rglob("*") if path.is_file()}
        assert after == before

    async def test_from_a_thread_the_session_opens_in_the_parent_channel(
        self, cog: MyAISetupCog, chat: MagicMock
    ):
        event = interaction(thread=True)
        await cog.ask_setup_agent(event, skill_item(), "hello")
        channel = chat.spawn_session.await_args.args[0]
        assert channel.id == 500

    async def test_without_a_chat_cog_the_handoff_is_declined(
        self, fixture_home: Path, repo: AISetupRepository, monkeypatch
    ):
        monkeypatch.setattr(settings_home, "_REGISTERED", [])
        bot = MagicMock()
        bot.user = SimpleNamespace(display_name="EbiBot")
        cog = MyAISetupCog(
            bot,
            repo=repo,
            collector=build_local_collector(roots_for(fixture_home), environ={}),
            chat=None,
            owner="drew",
            clock=lambda: NOW,
        )
        event = interaction()
        await cog.open(event)
        view = event.followup.send.await_args.kwargs["view"]
        assert view.ctx.agent is None
        await cog.ask_setup_agent(event, skill_item(), "hello")
        assert "not available" in event.response.send_message.await_args.args[0].lower()


class TestSetupWiring:
    async def test_setup_bridge_registers_the_cog_with_the_live_settings_home(
        self, tmp_path: Path, monkeypatch
    ):
        from claude_discord.setup import setup_bridge

        monkeypatch.setattr(settings_home, "_REGISTERED", [])
        # Declared roots come from a fixture home, never the real one.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        monkeypatch.setenv("CUSTOM_COGS_DIR", str(tmp_path / "home" / "cogs"))
        monkeypatch.setenv("CCDB_AI_SETUP_TRUSTED_COMPUTERS", "imac, david")
        bot = MagicMock()
        bot.channel_id = 123
        bot.user = SimpleNamespace(display_name="EbiBot")
        bot.add_cog = AsyncMock()
        bot.cogs = {}
        bot.wait_until_ready = AsyncMock()
        runner = MagicMock()
        runner.model = "sonnet"
        runner.working_dir = str(tmp_path / "proj")
        runner.api_port = None
        components = await setup_bridge(
            bot,
            runner,
            claude_channel_id=123,
            session_db_path=str(tmp_path / "sessions.db"),
            enable_scheduler=False,
            worktree_base_dir=str(tmp_path / "worktrees"),
        )
        added = [call.args[0] for call in bot.add_cog.await_args_list]
        cogs = [cog for cog in added if isinstance(cog, MyAISetupCog)]
        assert len(cogs) == 1
        cog = cogs[0]
        assert cog.trusted_computers == ("imac", "david")
        assert cog.collector.registry.adapter_names == (
            "shared-setup",
            "claude-harness",
            "claude-project-proj",
            "codex-harness",
            "dsh-harness",
            "discord-extensions",
        )
        assert components.settings_home is not None
        await cog.cog_load()
        assert any(entry.key == ENTRY_KEY for entry in components.settings_home.visible())
        # No inventory was collected merely by starting the bot.
        assert await cog.repo.load_snapshot(cog.computer()) is None


class TestAskSetupAgentComparisons:
    async def test_the_packet_names_how_stored_remote_snapshots_compare(
        self, cog: MyAISetupCog, chat: MagicMock, repo: AISetupRepository
    ):
        mine = skill_item()
        await repo.save_snapshot(
            InventorySnapshot(computer="drewai", owner="drew", collected_at=NOW, items=(mine,))
        )
        theirs = InventoryItem(
            **{
                **{f.name: getattr(mine, f.name) for f in mine.__dataclass_fields__.values()},
                "source": InventorySource(
                    key="claude-home",
                    computer="imac",
                    label="Claude home",
                    locator="~/.claude/skills/grilling/SKILL.md",
                ),
                "availability": (),
            }
        )
        await repo.save_snapshot(
            InventorySnapshot(computer="imac", owner="drew", collected_at=NOW, items=(theirs,))
        )
        event = interaction()
        await cog.ask_setup_agent(event, mine, "Is the iMac copy the same?")
        prompt = chat.spawn_session.await_args.kwargs["prompt"]
        assert "Other computers:" in prompt
        assert "- imac: Different content" in prompt  # no fingerprint on either → not parity
        assert "Is the iMac copy the same?" in prompt


class TestLogsAndStorageStaySafe:
    async def test_logs_and_the_database_carry_no_secret_after_an_open(
        self, cog: MyAISetupCog, repo: AISetupRepository, caplog, tmp_path: Path
    ):
        import logging
        import sqlite3

        caplog.set_level(logging.DEBUG)
        event = interaction()
        await cog.open(event)
        assert LEAKED not in caplog.text and HOOK_TOKEN not in caplog.text
        connection = sqlite3.connect(repo.db_path)
        try:
            dump = "\n".join(connection.iterdump())
        finally:
            connection.close()
        assert LEAKED not in dump and HOOK_TOKEN not in dump
        assert "ANTHROPIC_API_KEY" not in dump
        assert "grilling" in dump  # the safe facts did land
