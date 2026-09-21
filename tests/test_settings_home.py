"""Settings shows only what this computer supports, and opening it runs no model.

The supported set is plain data (which harnesses are configured, which
subscription features are on), so the filtering can be argued about without
Discord. The view is checked with a fake interaction.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.discord_ui import settings_home
from claude_discord.discord_ui.settings_home import (
    SettingsEntry,
    SettingsHome,
    SettingsHomeView,
    SupportedFeatures,
    default_entries,
)


class TestSupportedFeatures:
    def test_claude_is_always_supported_and_others_need_configuration(self):
        supported = SupportedFeatures.detect(env={}, codex_available=False)
        assert supported.harnesses == frozenset({"claude"})
        assert supported.features == frozenset()

    def test_local_dsh_agui_follow_their_configuration(self):
        env = {
            "CCDB_LOCAL_BASE_URL": "http://127.0.0.1:11434/v1",
            "DSH_ROUTES": "x",
            "CCDB_AGUI_URL": "http://agui",
        }
        supported = SupportedFeatures.detect(env=env, codex_available=True)
        assert supported.harnesses == frozenset({"claude", "codex", "local", "dsh", "agui"})

    def test_explicit_lists_override_detection(self):
        env = {
            "CCDB_SUPPORTED_HARNESSES": "claude, codex",
            "CCDB_SUBSCRIPTION_FEATURES": "max-plan,team",
        }
        supported = SupportedFeatures.detect(env=env, codex_available=False)
        assert supported.harnesses == frozenset({"claude", "codex"})
        assert supported.features == frozenset({"max-plan", "team"})

    def test_supports_requires_every_named_harness_and_feature(self):
        supported = SupportedFeatures(frozenset({"claude"}), frozenset({"team"}))
        assert supported.supports(SettingsEntry("a", "A", "any harness"))
        assert supported.supports(SettingsEntry("b", "B", "", harnesses=frozenset({"claude"})))
        assert not supported.supports(SettingsEntry("c", "C", "", harnesses=frozenset({"local"})))
        assert supported.supports(SettingsEntry("d", "D", "", features=frozenset({"team"})))
        assert not supported.supports(SettingsEntry("e", "E", "", features=frozenset({"pro"})))
        assert supported.supports(
            SettingsEntry("f", "F", "", harnesses=frozenset({"claude", "local"}), any_harness=True)
        )


class TestHome:
    def test_unsupported_entries_are_absent_not_disabled(self):
        supported = SupportedFeatures(frozenset({"claude"}))
        home = SettingsHome(supported)
        keys = {entry.key for entry in home.visible()}
        assert "model" in keys
        assert "ollama" not in keys  # needs the local harness
        assert all(supported.supports(e) for e in home.visible())

    def test_local_runtime_entry_appears_when_local_is_configured(self):
        home = SettingsHome(SupportedFeatures(frozenset({"claude", "local"})))
        assert "ollama" in {entry.key for entry in home.visible()}

    def test_add_is_the_extension_point_and_replaces_by_key(self):
        home = SettingsHome(SupportedFeatures(frozenset({"claude"})))
        opener = AsyncMock()
        home.add(SettingsEntry("ai-setup", "My AI Setup", "What is installed", open=opener))
        assert "My AI Setup" in [entry.label for entry in home.visible()]
        home.add(SettingsEntry("ai-setup", "My AI Setup (v2)", "Again", open=opener))
        labels = [entry.label for entry in home.visible()]
        assert labels.count("My AI Setup (v2)") == 1
        assert "My AI Setup" not in labels

    def test_module_registry_feeds_new_homes(self, monkeypatch):
        monkeypatch.setattr(settings_home, "_REGISTERED", [])
        settings_home.register_entry(SettingsEntry("extra", "Extra", "registered by a cog"))
        home = SettingsHome(SupportedFeatures(frozenset({"claude"})))
        assert "extra" in {entry.key for entry in home.visible()}
        assert "extra" not in {entry.key for entry in default_entries()}


def interaction(user: int = 42):
    item = MagicMock(spec=discord.Interaction)
    item.user = SimpleNamespace(id=user)
    item.response = MagicMock()
    item.response.send_message = AsyncMock()
    item.response.defer = AsyncMock()
    item.followup = MagicMock()
    item.followup.send = AsyncMock()
    return item


class TestView:
    @pytest.fixture
    def home(self):
        home = SettingsHome(SupportedFeatures(frozenset({"claude", "codex"})))
        home.add(SettingsEntry("ai-setup", "My AI Setup", "Inventory", open=AsyncMock()))
        return home

    def test_render_lists_visible_entries_and_buttons_only_for_openers(self, home):
        text, view = home.render("Lenovo")
        assert "Lenovo" in text
        assert "/switch" in text  # hint-only entries are described in the text
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        assert [b.label for b in buttons] == ["My AI Setup"]
        assert isinstance(view, SettingsHomeView)

    async def test_button_calls_the_entry_opener_and_nothing_else(self, home):
        text, view = home.render("Lenovo", user_id=42)
        button = next(c for c in view.children if isinstance(c, discord.ui.Button))
        event = interaction()
        await button.callback(event)
        opener = next(e for e in home.visible() if e.key == "ai-setup").open
        assert opener is not None
        opener.assert_awaited_once_with(event)

    async def test_view_is_personal(self, home):
        _, view = home.render("Lenovo", user_id=42)
        assert await view.interaction_check(interaction(42))
        assert not await view.interaction_check(interaction(7))
