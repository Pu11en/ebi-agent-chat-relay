"""Optional per-instance category boundary, independent of Discord admin rights."""

from __future__ import annotations

import os
from typing import Any

import discord


def category_allowed(channel: Any) -> bool:
    """An unset boundary preserves existing consumers; configured boundaries fail closed."""
    raw = os.getenv("CCDB_ALLOWED_CATEGORY_IDS", "").strip()
    if not raw:
        return True
    allowed = {int(value.strip()) for value in raw.split(",") if value.strip().isdigit()}
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return getattr(channel, "category_id", None) in allowed


def install_category_check(bot: Any) -> None:
    """Compose with the consumer's existing command check, including autocomplete."""
    if not os.getenv("CCDB_ALLOWED_CATEGORY_IDS", "").strip():
        return
    previous = bot.tree.interaction_check

    async def check(interaction: discord.Interaction) -> bool:
        if not category_allowed(interaction.channel):
            if interaction.type == discord.InteractionType.autocomplete:
                await interaction.response.autocomplete([])
            else:
                await interaction.response.send_message(
                    "This bot belongs to another computer category. "
                    "Use this category's own bot or its control-center buttons.",
                    ephemeral=True,
                )
            return False
        return await previous(interaction)

    bot.tree.interaction_check = check
