"""An invalid spawn request is rejected before anything is created."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.models import init_db
from claude_discord.database.repository import SessionRepository


async def test_automatic_spawn_without_prompt_creates_no_thread(tmp_path) -> None:
    path = str(tmp_path / "sessions.db")
    await init_db(path)
    repo = SessionRepository(path)
    cog = ClaudeChatCog(bot=MagicMock(), repo=repo, runner=MagicMock())
    thread = MagicMock(id=555, send=AsyncMock(), edit=AsyncMock())
    thread.name = "unnamed"
    channel = MagicMock(create_thread=AsyncMock(return_value=thread))
    with pytest.raises(ValueError, match="needs a prompt"):
        await cog.spawn_session(channel, "", auto_start=True, working_dir=str(tmp_path))
    channel.create_thread.assert_not_awaited()
