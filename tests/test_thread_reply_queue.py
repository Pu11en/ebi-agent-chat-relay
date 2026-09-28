"""Normal replies queue in order; Stop advances one turn without losing history."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.cogs.run_config import RunConfig


@pytest.mark.parametrize("finish", ["natural", "command", "button", "cancel_admission"])
@pytest.mark.parametrize("initial_session", [None, "abc-0"])
async def test_replies_wait_and_stop_advances_one_turn(
    monkeypatch: pytest.MonkeyPatch, finish: str, initial_session: str | None
) -> None:
    import claude_discord.cogs.claude_chat as chat_mod

    bot = MagicMock()
    bot.user.id = 7
    record = SimpleNamespace(
        session_id=initial_session, working_dir="/project", backend="claude", is_open=True
    )
    repo = MagicMock()
    repo.get = AsyncMock(side_effect=lambda _: record)
    cog = ClaudeChatCog(bot=bot, repo=repo, runner=MagicMock())
    cog._get_dashboard = lambda: None
    cog._get_current_model = AsyncMock(return_value=None)
    cog._get_allowed_tools = AsyncMock(return_value=None)
    cog._get_current_effort = AsyncMock(return_value=None)
    cog._build_prompt_and_images = AsyncMock(side_effect=lambda msg: (msg.content, []))

    names = ("first", "second", "third")
    queued = {name: asyncio.Event() for name in names}
    started = {name: asyncio.Event() for name in names}
    release = {name: asyncio.Event() for name in names}
    configs: dict[str, RunConfig] = {}
    seen: list[tuple[str, str | None]] = []

    class Status:
        def __init__(self, message: discord.Message, **kwargs: object) -> None:
            self.name = message.content

        async def set_queued(self) -> None:
            queued[self.name].set()

    monkeypatch.setattr(chat_mod, "StatusManager", Status)

    async def run(config: RunConfig) -> None:
        name = config.prompt
        configs[name] = config
        seen.append((name, config.session_id))
        config.runner.interrupt = AsyncMock(side_effect=release[name].set)
        if finish == "cancel_admission" and name == "first":
            assert config.stop_view is not None
            config.stop_view.set_queued_task(asyncio.current_task())
        started[name].set()
        try:
            await release[name].wait()
        finally:
            record.session_id = f"abc-{names.index(name) + 1}"

    monkeypatch.setattr(chat_mod, "run_claude_with_config", run)
    cog._build_runner_for_thread = AsyncMock(side_effect=lambda **kwargs: MagicMock())
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    thread.parent_id = 999
    thread.owner_id = bot.user.id
    thread.send = AsyncMock()

    def message(name: str) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.channel = thread
        msg.content = name
        msg.attachments = []
        msg.author = SimpleNamespace(id=12, bot=False)
        return msg

    async def stop(name: str) -> None:
        if finish == "command":
            assert await cog.stop_turn(thread.id)
        else:
            interaction = MagicMock(spec=discord.Interaction)
            interaction.channel = thread
            interaction.response = AsyncMock()
            interaction.followup = AsyncMock()
            view = configs[name].stop_view
            assert view is not None
            await view.stop_button.callback(interaction)

    tasks = [asyncio.create_task(cog._handle_thread_reply(message("first")))]
    try:
        await asyncio.wait_for(started["first"].wait(), 1)
        for name in names[1:]:
            tasks.append(asyncio.create_task(cog._handle_thread_reply(message(name))))
            await asyncio.wait_for(queued[name].wait(), 1)
        assert seen == [("first", initial_session)]
        configs["first"].runner.interrupt.assert_not_awaited()

        if finish == "natural":
            release["first"].set()
        else:
            await stop("first")
        await asyncio.wait_for(started["second"].wait(), 1)
        assert seen == [("first", initial_session), ("second", "abc-1")]
        configs["second"].runner.interrupt.assert_not_awaited()

        if finish == "natural":
            release["second"].set()
        else:
            await stop("second")
        await asyncio.wait_for(started["third"].wait(), 1)
        assert seen[-1] == ("third", "abc-2")
        release["third"].set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
        assert results[1:] == [None, None]
        if finish == "cancel_admission":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert results[0] is None
        assert not cog._active_runners
        assert not cog._active_tasks
    finally:
        for event in release.values():
            event.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)


async def test_mention_queues_instead_of_interrupting() -> None:
    cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())
    cog.repo.get = AsyncMock(return_value=None)
    cog._run_claude = AsyncMock()
    cog._thread_context_days = 0
    cog._build_prompt_and_images = AsyncMock(return_value=("follow up", []))
    message = MagicMock(spec=discord.Message)
    message.channel = MagicMock(spec=discord.TextChannel)
    message.channel.id = 42

    await cog._handle_mention(message)

    assert cog._run_claude.call_args.kwargs.get("interrupt_existing", False) is False


async def test_cancelling_waiting_reply_does_not_cancel_current_work() -> None:
    cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    active = asyncio.create_task(asyncio.Event().wait())
    cog._active_runners[42] = MagicMock()
    cog._active_tasks[42] = active
    waiting = asyncio.create_task(cog._evict_active_run(thread, interrupt=False, notice=""))
    try:
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not active.done()
    finally:
        active.cancel()
        await asyncio.gather(active, return_exceptions=True)
