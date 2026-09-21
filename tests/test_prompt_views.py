"""Prompts are bound to the people allowed to talk to the bot.

A prompt's buttons are public in the thread, but the answer runs a model turn
(a permission grant, a plan approval, a fresh session). With
``CLAUDE_ALLOWED_USER_IDS`` set, a member who may only *read* the thread must
not be able to press "Allow" on someone else's session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.frontend import Choice, ChoicePrompt, FormField, FormPrompt
from claude_discord.discord_ui.prompt_views import ChoiceView, FormModal


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.message = None
    return interaction


def _choice_prompt() -> ChoicePrompt:
    return ChoicePrompt(
        question="Run rm -rf?",
        choices=(Choice(value="allow", label="Allow"), Choice(value="deny", label="Deny")),
        default_on_timeout="deny",
        timeout_seconds=5,
    )


def _form_prompt() -> FormPrompt:
    return FormPrompt(
        title="Details",
        fields=(FormField(key="name", label="Name", kind="text"),),
        timeout_seconds=5,
    )


class TestChoiceViewInteractionCheck:
    async def test_an_allowed_user_passes(self) -> None:
        view = ChoiceView(_choice_prompt(), allowed_user_ids=frozenset({1, 2}))
        interaction = _interaction(2)
        assert await view.interaction_check(interaction) is True
        interaction.response.send_message.assert_not_awaited()

    async def test_a_stranger_is_refused_ephemerally(self) -> None:
        view = ChoiceView(_choice_prompt(), allowed_user_ids=frozenset({1, 2}))
        interaction = _interaction(99)
        assert await view.interaction_check(interaction) is False
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert not view._future.done()  # the prompt is still waiting for its owner

    async def test_a_strangers_click_does_not_answer(self) -> None:
        """The check is what discord.py consults before the button callback."""
        view = ChoiceView(_choice_prompt(), allowed_user_ids=frozenset({1}))
        interaction = _interaction(99)
        button: Any = view.children[0]
        if await view.interaction_check(interaction):
            await button.callback(interaction)
        assert not view._future.done()

    async def test_no_allowlist_keeps_todays_behaviour(self) -> None:
        view = ChoiceView(_choice_prompt())
        interaction = _interaction(99)
        assert await view.interaction_check(interaction) is True
        interaction.response.send_message.assert_not_awaited()

    async def test_an_empty_allowlist_refuses_everyone(self) -> None:
        """Empty is not None: nobody was allowed, so nobody may answer."""
        view = ChoiceView(_choice_prompt(), allowed_user_ids=frozenset())
        assert await view.interaction_check(_interaction(1)) is False

    async def test_refusal_survives_a_failed_ephemeral_reply(self) -> None:
        view = ChoiceView(_choice_prompt(), allowed_user_ids=frozenset({1}))
        interaction = _interaction(99)
        interaction.response.send_message = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "nope")
        )
        assert await view.interaction_check(interaction) is False


class TestFormModalInteractionCheck:
    async def test_a_stranger_cannot_submit(self) -> None:
        modal = FormModal(_form_prompt(), allowed_user_ids=frozenset({1}))
        interaction = _interaction(99)
        assert await modal.interaction_check(interaction) is False
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    async def test_an_allowed_user_can_submit(self) -> None:
        modal = FormModal(_form_prompt(), allowed_user_ids=frozenset({1}))
        assert await modal.interaction_check(_interaction(1)) is True

    async def test_no_allowlist_keeps_todays_behaviour(self) -> None:
        modal = FormModal(_form_prompt())
        assert await modal.interaction_check(_interaction(99)) is True


@pytest.mark.parametrize("ids", [frozenset({1}), None])
async def test_views_expose_their_allowlist(ids: frozenset[int] | None) -> None:
    assert ChoiceView(_choice_prompt(), allowed_user_ids=ids).allowed_user_ids == ids
    assert FormModal(_form_prompt(), allowed_user_ids=ids).allowed_user_ids == ids
