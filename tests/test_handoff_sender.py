"""Tests for posting DrewAI lookup handoffs into Discord."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.handoffs import protocol as p
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.handoff_messages import parse_handoff_message
from claude_discord.handoff_sender import (
    build_project_lookup_handoff_event,
    send_project_lookup_handoff,
)
from claude_discord.handoff_triggers import DrewAILookupTrigger

NOW = datetime(2026, 9, 20, 22, 0, 0, tzinfo=UTC)
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"


def _thread_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=444,
        content="use DrewAI to find info on the Pinterest visual picker process",
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=333, parent_id=222, send=AsyncMock()),
        author=SimpleNamespace(id=555, bot=False),
        type=discord.MessageType.default,
    )


def _channel_message() -> SimpleNamespace:
    return SimpleNamespace(
        id=444,
        content="use DrewAI to find info on the Realpage folder",
        guild=SimpleNamespace(id=111),
        channel=SimpleNamespace(id=222, parent_id=None),
        author=SimpleNamespace(id=555, bot=False),
        type=discord.MessageType.default,
    )


def _ids() -> tuple[str, str]:
    return TASK_ID, EVENT_ID


def test_build_project_lookup_handoff_event_uses_thread_origin_and_read_only_scope() -> None:
    event = build_project_lookup_handoff_event(
        DrewAILookupTrigger("the Pinterest visual picker process"),
        origin_message=_thread_message(),
        sender_agent_id="david",
        now=NOW,
        id_factory=_ids,
    )

    assert event.kind is p.HandoffEventKind.TASK
    assert event.sender == "david"
    assert event.recipient == "drewai"
    assert event.sequence == 0
    assert event.task is not None
    assert event.task.origin == p.ConversationCoordinate(
        guild_id=111,
        channel_id=222,
        thread_id=333,
        message_id=444,
    )
    assert event.task.reply_to == event.task.origin
    assert event.task.origin_human_id == "555"
    assert event.task.project == p.ProjectLocator(owner="drew", folder="main-projects")
    assert event.task.authority == p.AuthorityScope(read=True)
    assert "Pinterest visual picker process" in event.task.goal
    assert event.task.expected_result == "Exact paths, relevant files, and a short summary."
    assert event.task.expires_at == NOW + timedelta(hours=6)


def test_build_project_lookup_handoff_event_handles_channel_origin_without_thread() -> None:
    event = build_project_lookup_handoff_event(
        DrewAILookupTrigger("the Realpage folder"),
        origin_message=_channel_message(),
        now=NOW,
        id_factory=_ids,
    )

    assert event.task is not None
    assert event.task.origin == p.ConversationCoordinate(
        guild_id=111,
        channel_id=222,
        thread_id=None,
        message_id=444,
    )


@pytest.mark.asyncio
async def test_send_project_lookup_handoff_posts_parseable_discord_envelope() -> None:
    destination = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))

    sent = await send_project_lookup_handoff(
        DrewAILookupTrigger("keyword rules for Pinterest sheets"),
        origin_message=_thread_message(),
        destination=destination,
        sender_agent_id="david",
        now=NOW,
        id_factory=_ids,
    )

    destination.send.assert_awaited_once()
    sent_text = destination.send.await_args.args[0]
    parsed = parse_handoff_message(sent_text)

    assert parsed == sent.event
    assert sent.sent_message.id == 999
    assert sent.event.task is not None
    assert "keyword rules for Pinterest sheets" in sent.event.task.goal


def test_build_project_lookup_handoff_event_requires_guild_context() -> None:
    message = _thread_message()
    message.guild = None

    with pytest.raises(ValueError, match="guild"):
        build_project_lookup_handoff_event(
            DrewAILookupTrigger("anything"),
            origin_message=message,
            now=NOW,
            id_factory=_ids,
        )


@pytest.mark.asyncio
async def test_chat_cog_sends_natural_drewai_lookup_to_configured_agent_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CCDB_AGENT_ROUTES", "drewai=777|drew,drew ai")
    monkeypatch.setenv("CCDB_AGENT_ID", "david")
    target = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=999)))
    bot = MagicMock()
    bot.get_channel.return_value = target
    cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
    message = _thread_message()

    handled = await cog._try_send_drewai_lookup_handoff(message)

    assert handled is True
    target.send.assert_awaited_once()
    parsed = parse_handoff_message(target.send.await_args.args[0])
    assert parsed is not None
    assert parsed.sender == "david"
    assert parsed.recipient == "drewai"
    assert parsed.task is not None
    assert "Pinterest visual picker process" in parsed.task.goal
    message.channel.send.assert_awaited_once()
    assert "DrewAI" in message.channel.send.await_args.args[0]


@pytest.mark.asyncio
async def test_chat_cog_sends_natural_drewai_lookup_to_remote_project_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def remote_handler(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.json_response(
            {
                "status": "spawned",
                "agent_id": "drewai",
                "thread_id": "999",
                "thread_name": "🔎 Project lookup · Pinterest",
            },
            status=201,
        )

    remote_app = web.Application()
    remote_app.router.add_post("/api/project-lookup", remote_handler)
    remote_server = TestServer(remote_app)
    remote_client = TestClient(remote_server)
    await remote_client.start_server()

    try:
        monkeypatch.setenv("DREWAI_RELAY_SECRET", "secret-value")
        monkeypatch.setenv("CCDB_AGENT_ID", "david")
        monkeypatch.setenv(
            "CCDB_AGENT_ROUTES",
            (
                '{"drewai": {"url": "'
                f"{remote_client.make_url('/api/project-lookup')}"
                '", "secret_env": "DREWAI_RELAY_SECRET", "aliases": ["drew"]}}'
            ),
        )
        bot = MagicMock()
        bot.get_channel.return_value = None
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
        message = _thread_message()

        handled = await cog._try_send_drewai_lookup_handoff(message)

        assert handled is True
        assert seen["auth"] == "Bearer secret-value"
        assert seen["body"] == {
            "text": "the Pinterest visual picker process",
            "from_agent": "david",
            "from_thread": 333,
            "channel_id": 222,
        }
        message.channel.send.assert_awaited_once()
        assert "DrewAI" in message.channel.send.await_args.args[0]
        assert "999" in message.channel.send.await_args.args[0]
    finally:
        await remote_client.close()


@pytest.mark.asyncio
async def test_chat_message_handoff_short_circuits_normal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
    cog = SimpleNamespace(
        _allowed_user_ids={555},
        _claimed_by_task_loop=lambda message: False,
        _ensure_thread_members=AsyncMock(),
        _try_send_drewai_lookup_handoff=AsyncMock(return_value=True),
        _is_no_mention_scope=lambda channel: True,
        _handle_thread_reply=AsyncMock(),
    )
    message = _thread_message()

    await ClaudeChatCog.on_message(cog, message)

    cog._try_send_drewai_lookup_handoff.assert_awaited_once_with(message)
    cog._handle_thread_reply.assert_not_awaited()
