"""`/session`: Fork, Rewind, Compact, Clear, Context and Goal in one ephemeral view.

Six separate top-level commands were the main source of slash clutter; one
command that opens a row of buttons keeps them one keystroke away without
listing them everywhere. The view owns no behaviour: every button calls
exactly one method of the :class:`SessionActionService` it was given, which
is the same service the legacy slash commands call, so the two cannot drift.

Only actions that discard conversation state confirm first. Clear asks
explicitly; Rewind's turn picker is already a choice the user makes on
purpose; Context is read-only and just answers.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import CoroutineType
from typing import Any, Protocol

import discord

ACTION_LABELS: tuple[str, ...] = ("Fork", "Rewind", "Compact", "Clear", "Context", "Goal")


class SessionActionService(Protocol):
    """What `/session` can do to the session in the interaction's thread."""

    async def fork(self, interaction: discord.Interaction) -> None: ...

    async def rewind(self, interaction: discord.Interaction) -> None: ...

    async def compact(self, interaction: discord.Interaction) -> None: ...

    async def clear(self, interaction: discord.Interaction) -> None: ...

    async def context(self, interaction: discord.Interaction) -> None: ...

    async def goal(self, interaction: discord.Interaction, condition: str | None) -> None: ...


class ConfirmView(discord.ui.View):
    """One destructive button plus Cancel; the action runs at most once."""

    def __init__(
        self,
        action: Callable[[discord.Interaction], Awaitable[None]],
        *,
        user_id: int,
        label: str,
    ) -> None:
        super().__init__(timeout=120)
        self.action = action
        self.user_id = user_id
        self.used = False

        go: discord.ui.Button[ConfirmView] = discord.ui.Button(
            label=label, style=discord.ButtonStyle.danger
        )

        async def confirm(interaction: discord.Interaction) -> None:
            if self.used:
                await interaction.response.send_message(
                    "Already done. Open `/session` again for another action.", ephemeral=True
                )
                return
            self.used = True
            self.stop()
            await self.action(interaction)

        go.callback = confirm
        self.add_item(go)

        cancel: discord.ui.Button[ConfirmView] = discord.ui.Button(
            label="Cancel", style=discord.ButtonStyle.secondary
        )

        async def keep(interaction: discord.Interaction) -> None:
            self.used = True
            self.stop()
            await interaction.response.edit_message(content="Nothing was changed.", view=None)

        cancel.callback = keep
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return _same_user(interaction, self.user_id)


class GoalModal(discord.ui.Modal, title="Session goal"):
    condition = discord.ui.TextInput(
        label="Completion condition (blank = show, 'clear' = remove)",
        required=False,
        max_length=400,
    )

    def __init__(self, service: SessionActionService) -> None:
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.condition).strip()
        await self.service.goal(interaction, text or None)


class SessionActionsView(discord.ui.View):
    """The six session actions; personal and short-lived."""

    def __init__(self, service: SessionActionService, *, user_id: int) -> None:
        super().__init__(timeout=300)
        self.service = service
        self.user_id = user_id
        handlers: dict[str, Callable[[discord.Interaction], Awaitable[None]]] = {
            "Fork": service.fork,
            "Rewind": service.rewind,
            "Compact": service.compact,
            "Clear": self._confirm_clear,
            "Context": service.context,
            "Goal": self._ask_goal,
        }
        for label in ACTION_LABELS:
            danger = label == "Clear"
            button: discord.ui.Button[SessionActionsView] = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary,
            )
            button.callback = _bind(handlers[label])
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return _same_user(interaction, self.user_id)

    async def _confirm_clear(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "**Clear** forgets this thread's conversation so the next message starts a "
            "fresh session in the same folder. Working files and this thread are kept. "
            "Continue?",
            view=ConfirmView(self.service.clear, user_id=self.user_id, label="Clear this session"),
            ephemeral=True,
        )

    async def _ask_goal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(GoalModal(self.service))


class _ButtonCallback(Protocol):
    def __call__(self, interaction: discord.Interaction) -> CoroutineType[Any, Any, None]: ...


def _bind(handler: Callable[[discord.Interaction], Awaitable[None]]) -> _ButtonCallback:
    async def callback(interaction: discord.Interaction) -> None:
        await handler(interaction)

    return callback


def _same_user(interaction: discord.Interaction, user_id: int) -> bool:
    return interaction.user.id == user_id
