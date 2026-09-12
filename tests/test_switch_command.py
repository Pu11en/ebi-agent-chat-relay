"""/switch sets backend and model in one pick."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from claude_discord.backend_settings import BackendSettings
from tests.test_backend_command_session_clear import (
    _make_cog,
    _make_thread_interaction,
    _new_settings_repo,
)


async def _settings() -> BackendSettings:
    return BackendSettings(
        await _new_settings_repo(),
        env_backend="claude",
        env_model_for_claude="opus",
        env_model_for_codex="",
    )


def _picked_backend(interaction: MagicMock, backend: str) -> MagicMock:
    """Simulate Discord sending the optional ``backend`` option with the focus."""
    interaction.namespace = MagicMock()
    interaction.namespace.backend = backend
    return interaction


async def test_switch_sets_backend_and_model_for_thread() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    await cog.switch_command.callback(cog, interaction, choice="codex|gpt-5.6-sol")
    assert await settings.current_backend(42) == "codex"
    assert await settings.current_model("codex", 42) == "gpt-5.6-sol"
    assert await settings.current_backend(None) == "claude"
    cog._chat_cog.repo.delete.assert_not_awaited()


async def test_switch_rejects_free_text() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    await cog.switch_command.callback(cog, interaction, choice="opus")
    assert await settings.current_backend(42) == "claude"
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_switch_rejects_a_backend_without_a_model_catalog() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    await cog.switch_command.callback(cog, interaction, choice="agui|whatever")
    assert await settings.current_backend(42) == "claude"
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_switching_backend_without_a_model_explains_what_to_do() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    await cog.switch_command.callback(cog, interaction, backend="dsh")
    message = interaction.response.send_message.await_args.args[0]
    assert "dsh" in message
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_autocomplete_lists_every_backend_current_first() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    claude = [("opus", "big"), ("sonnet", "mid")]
    with (
        patch(
            "claude_discord.cogs.backend_command.claude_model_choices",
            AsyncMock(return_value=claude),
        ),
        patch(
            "claude_discord.cogs.backend_command.codex_model_choices",
            return_value=[("gpt-5.6-sol", "x")],
        ),
        patch(
            "claude_discord.cogs.backend_command.dsh_model_choices",
            AsyncMock(return_value=[("deepseek-v4-flash", "y")]),
        ),
    ):
        choices = await cog._switch_autocomplete(interaction, "")
        values = [c.value for c in choices]
        assert values[0] == "claude|opus" and choices[0].name.startswith("✅")
        assert {"codex|gpt-5.6-sol", "dsh|deepseek-v4-flash", "claude|sonnet"} <= set(values)
        filtered = await cog._switch_autocomplete(interaction, "deepseek")
        assert [c.value for c in filtered] == ["dsh|deepseek-v4-flash"]


async def test_autocomplete_does_not_cap_the_threads_backend_at_eight() -> None:
    """The old even-share cap hid models; the thread's own backend is complete."""
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    claude = [(f"claude-model-{i}", "x") for i in range(14)]
    with (
        patch(
            "claude_discord.cogs.backend_command.claude_model_choices",
            AsyncMock(return_value=claude),
        ),
        patch(
            "claude_discord.cogs.backend_command.codex_model_choices",
            return_value=[("gpt-5.6-sol", "x")],
        ),
        patch(
            "claude_discord.cogs.backend_command.dsh_model_choices",
            AsyncMock(return_value=[("deepseek-v4-flash", "y")]),
        ),
    ):
        choices = await cog._switch_autocomplete(interaction, "")
    claude_values = [c.value for c in choices if c.value.startswith("claude|")]
    assert len(claude_values) == 14


async def test_typing_a_backend_name_shows_every_model_it_has() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    dsh = [(f"glm-{i}", "x") for i in range(12)]
    with (
        patch(
            "claude_discord.cogs.backend_command.claude_model_choices",
            AsyncMock(return_value=[("opus", "big")]),
        ),
        patch(
            "claude_discord.cogs.backend_command.codex_model_choices",
            return_value=[("gpt-5.6-sol", "x")],
        ),
        patch(
            "claude_discord.cogs.backend_command.dsh_model_choices",
            AsyncMock(return_value=dsh),
        ),
    ):
        choices = await cog._switch_autocomplete(interaction, "dsh")
    assert len([c for c in choices if c.value.startswith("dsh|")]) == 12


async def test_choosing_a_backend_shows_its_complete_catalog() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _picked_backend(_make_thread_interaction(thread_id=42), "claude")
    claude = [(f"claude-model-{i}", "x") for i in range(15)]
    with (
        patch(
            "claude_discord.cogs.backend_command.claude_model_choices",
            AsyncMock(return_value=claude),
        ),
        patch(
            "claude_discord.cogs.backend_command.codex_model_choices",
            return_value=[("gpt-5.6-sol", "x")],
        ),
        patch(
            "claude_discord.cogs.backend_command.dsh_model_choices",
            AsyncMock(return_value=[("deepseek-v4-flash", "y")]),
        ),
    ):
        choices = await cog._switch_autocomplete(interaction, "")
    values = [c.value for c in choices]
    assert len(values) == 15
    assert all(value.startswith("claude|") for value in values)


async def test_local_models_are_reachable_from_switch() -> None:
    settings = await _settings()
    cog = _make_cog(settings)
    interaction = _make_thread_interaction(thread_id=42)
    with (
        patch(
            "claude_discord.cogs.backend_command.claude_model_choices",
            AsyncMock(return_value=[("opus", "big")]),
        ),
        patch(
            "claude_discord.cogs.backend_command.codex_model_choices",
            return_value=[("gpt-5.6-sol", "x")],
        ),
        patch(
            "claude_discord.cogs.backend_command.dsh_model_choices",
            AsyncMock(return_value=[("deepseek-v4-flash", "y")]),
        ),
    ):
        choices = await cog._switch_autocomplete(interaction, "local")
    assert choices and all(c.value.startswith("local|") for c in choices)
