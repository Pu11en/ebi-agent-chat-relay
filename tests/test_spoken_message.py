"""Tests for a message the user said out loud (voice → an existing thread).

Covers:
- build_spoken_prompt framing (the user's own words, transcribed)
- ApiServer POST /api/threads/{thread_id}/spoken
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.spoken import MAX_SPOKEN_TEXT_CHARS, build_spoken_prompt

OWNER = 488763953397235712
STRANGER = 999999999999999999
THREAD = 222

# ---------------------------------------------------------------------------
# Prompt framing
# ---------------------------------------------------------------------------


def test_spoken_prompt_says_the_words_are_the_humans_own() -> None:
    prompt = build_spoken_prompt(text="push the branch")

    assert "push the branch" in prompt
    assert "NOT a relay" in prompt
    assert "SPOKEN BY YOUR HUMAN" in prompt


def test_spoken_prompt_warns_that_transcription_mishears_identifiers() -> None:
    prompt = build_spoken_prompt(text="run make verify")

    assert "speech recognition" in prompt
    assert "misheard" in prompt


def test_spoken_prompt_names_the_surface_it_came_from() -> None:
    assert "live voice transcript" in build_spoken_prompt(text="hi", source="voice")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def cog() -> MagicMock:
    c = MagicMock()
    c.deliver_relayed_message = AsyncMock()
    return c


@pytest.fixture
def thread() -> MagicMock:
    t = MagicMock(spec=discord.Thread)
    t.id = THREAD
    return t


@pytest.fixture
def bot(cog: MagicMock, thread: MagicMock) -> MagicMock:
    b = MagicMock()
    b.cogs = {"ClaudeChatCog": cog}
    b.get_channel.return_value = thread
    b.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    b.owner_id = OWNER
    return b


@pytest.fixture
async def api_client(db_path: str, bot: MagicMock) -> TestClient:
    notif_repo = NotificationRepository(db_path)
    await notif_repo.init_db()
    api = ApiServer(repo=notif_repo, bot=bot, host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()


async def test_spoken_message_is_delivered_unwrapped_and_queued(
    api_client: TestClient, cog: MagicMock, thread: MagicMock
) -> None:
    resp = await api_client.post(
        f"/api/threads/{THREAD}/spoken",
        json={"text": "check whether the domain verified", "speaker_id": str(OWNER)},
    )

    assert resp.status == 202
    body = await resp.json()
    assert body["mode"] == "queue"
    assert body["source"] == "voice"

    await asyncio.sleep(0)  # let the background delivery task run
    cog.deliver_relayed_message.assert_awaited_once()
    args, kwargs = cog.deliver_relayed_message.call_args
    assert args[0] is thread
    assert "check whether the domain verified" in args[1]
    assert "ANOTHER CLAUDE SESSION" not in args[1]
    assert kwargs["interrupt"] is False


async def test_interrupt_mode_is_passed_through(api_client: TestClient, cog: MagicMock) -> None:
    resp = await api_client.post(
        f"/api/threads/{THREAD}/spoken",
        json={
            "text": "stop, that is the wrong file",
            "speaker_id": str(OWNER),
            "mode": "interrupt",
        },
    )

    assert resp.status == 202
    await asyncio.sleep(0)
    assert cog.deliver_relayed_message.call_args.kwargs["interrupt"] is True


async def test_someone_else_speaking_in_the_room_cannot_drive_a_session(
    api_client: TestClient, cog: MagicMock
) -> None:
    resp = await api_client.post(
        f"/api/threads/{THREAD}/spoken",
        json={"text": "delete the database", "speaker_id": str(STRANGER)},
    )

    assert resp.status == 403
    await asyncio.sleep(0)
    cog.deliver_relayed_message.assert_not_awaited()


async def test_a_configured_owner_makes_the_speaker_mandatory(
    api_client: TestClient, cog: MagicMock
) -> None:
    resp = await api_client.post(f"/api/threads/{THREAD}/spoken", json={"text": "do the thing"})

    assert resp.status == 403
    await asyncio.sleep(0)
    cog.deliver_relayed_message.assert_not_awaited()


async def test_without_a_configured_owner_the_control_plane_secret_is_the_gate(
    db_path: str, bot: MagicMock, cog: MagicMock
) -> None:
    bot.owner_id = None
    notif_repo = NotificationRepository(db_path)
    await notif_repo.init_db()
    api = ApiServer(repo=notif_repo, bot=bot, host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        resp = await client.post(f"/api/threads/{THREAD}/spoken", json={"text": "do the thing"})
        assert resp.status == 202
        await asyncio.sleep(0)
        cog.deliver_relayed_message.assert_awaited_once()
    finally:
        await client.close()


async def test_speaking_is_not_rate_limited_the_way_agent_relay_is(
    api_client: TestClient, cog: MagicMock
) -> None:
    """A person mid-sentence must not hit the two-agents-looping brake."""
    for _ in range(4):
        resp = await api_client.post(
            f"/api/threads/{THREAD}/spoken",
            json={"text": "and one more thing", "speaker_id": str(OWNER)},
        )
        assert resp.status == 202

    await asyncio.sleep(0)
    assert cog.deliver_relayed_message.await_count == 4


@pytest.mark.parametrize(
    "payload",
    [
        {"speaker_id": str(OWNER)},
        {"text": "   ", "speaker_id": str(OWNER)},
        {"text": "hi", "speaker_id": str(OWNER), "mode": "shout"},
        {"text": "hi", "speaker_id": str(OWNER), "source": "telepathy"},
        {"text": "x" * (MAX_SPOKEN_TEXT_CHARS + 1), "speaker_id": str(OWNER)},
    ],
)
async def test_spoken_rejects_bad_input(api_client: TestClient, payload: dict) -> None:
    resp = await api_client.post(f"/api/threads/{THREAD}/spoken", json=payload)
    assert resp.status == 400


async def test_spoken_to_unknown_thread_returns_404(api_client: TestClient, bot: MagicMock) -> None:
    bot.get_channel.return_value = None
    resp = await api_client.post(
        f"/api/threads/{THREAD}/spoken", json={"text": "hi", "speaker_id": str(OWNER)}
    )
    assert resp.status == 404


async def test_spoken_to_non_thread_channel_is_rejected(
    api_client: TestClient, bot: MagicMock
) -> None:
    bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)
    resp = await api_client.post(
        f"/api/threads/{THREAD}/spoken", json={"text": "hi", "speaker_id": str(OWNER)}
    )
    assert resp.status == 400
