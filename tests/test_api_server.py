"""Tests for ApiServer REST API extension."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.repository import SessionRecord, SessionRepository
from claude_discord.ext.api_server import ApiServer, build_jester_session_snapshot
from claude_discord.thread_policy import THREAD_AUTO_ARCHIVE_MINUTES


@pytest.fixture
async def repo() -> NotificationRepository:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = NotificationRepository(path)
    await repo.init_db()
    yield repo
    os.unlink(path)


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    b.get_channel.return_value = channel
    return b


@pytest.fixture
async def client(repo: NotificationRepository, bot: MagicMock) -> TestClient:
    api = ApiServer(
        repo=repo,
        bot=bot,
        default_channel_id=12345,
        host="127.0.0.1",
        port=0,
    )
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
async def auth_client(repo: NotificationRepository, bot: MagicMock) -> TestClient:
    api = ApiServer(
        repo=repo,
        bot=bot,
        default_channel_id=12345,
        api_secret="test-secret-123",
    )
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: TestClient) -> None:
        resp = await client.get("/api/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    @pytest.mark.asyncio
    async def test_health_reports_no_overdue_when_clean(self, client: TestClient) -> None:
        resp = await client.get("/api/health")
        assert (await resp.json())["overdue_notifications"] == 0

    @pytest.mark.asyncio
    async def test_health_counts_overdue_notifications(
        self, client: TestClient, repo: NotificationRepository
    ) -> None:
        """A notification long past its time is the symptom of a dead dispatcher.

        The previous outage was invisible precisely because every endpoint kept
        answering normally while nothing was delivered, so the health check now
        reports the backlog instead of a bare "ok".
        """
        await repo.create(message="取り残された", scheduled_at="2020-01-01T09:00:00")
        await repo.create(message="まだ先", scheduled_at="2099-01-01T09:00:00")

        data = await (await client.get("/api/health")).json()

        assert data["overdue_notifications"] == 1
        assert data["status"] == "degraded"


class TestNotify:
    @pytest.mark.asyncio
    async def test_notify_sends_message(self, client: TestClient, bot: MagicMock) -> None:
        resp = await client.post("/api/notify", json={"message": "Hello!"})
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "sent"
        bot.get_channel.assert_called_with(12345)

    @pytest.mark.asyncio
    async def test_notify_missing_message(self, client: TestClient) -> None:
        resp = await client.post("/api/notify", json={})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_notify_invalid_json(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/notify",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_notify_text_format(self, client: TestClient, bot: MagicMock) -> None:
        channel = bot.get_channel.return_value
        resp = await client.post("/api/notify", json={"message": "Hello text!", "format": "text"})
        assert resp.status == 200
        channel.send.assert_called_once_with("Hello text!")

    @pytest.mark.asyncio
    async def test_notify_no_channel(self, repo: NotificationRepository) -> None:
        bot = MagicMock()
        api = ApiServer(repo=repo, bot=bot, default_channel_id=None)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/notify", json={"message": "test"})
            assert resp.status == 400
        finally:
            await client.close()


class TestNotifyPoll:
    """Tests for poll parameter in /api/notify."""

    @pytest.mark.asyncio
    async def test_notify_with_poll(self, client: TestClient, bot: MagicMock) -> None:
        """Poll object is constructed and passed to channel.send()."""
        channel = bot.get_channel.return_value
        resp = await client.post(
            "/api/notify",
            json={
                "message": "投票してね",
                "poll": {
                    "question": "好きな言語は？",
                    "answers": ["Python", "Go", "Rust"],
                    "duration_hours": 24,
                },
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "sent"
        call_kwargs = channel.send.call_args.kwargs
        assert "poll" in call_kwargs
        poll = call_kwargs["poll"]
        # discord.py may store question as str or PollMedia depending on version
        q = poll.question
        assert (q.text if hasattr(q, "text") else q) == "好きな言語は？"
        assert len(poll.answers) == 3
        assert poll.duration.total_seconds() == 24 * 3600

    @pytest.mark.asyncio
    async def test_notify_poll_with_multiselect(self, client: TestClient, bot: MagicMock) -> None:
        """allow_multiselect flag is passed through."""
        channel = bot.get_channel.return_value
        resp = await client.post(
            "/api/notify",
            json={
                "message": "複数選択OK",
                "poll": {
                    "question": "好きな食べ物は？",
                    "answers": ["寿司", "ラーメン", "カレー"],
                    "duration_hours": 48,
                    "allow_multiselect": True,
                },
            },
        )
        assert resp.status == 200
        poll = channel.send.call_args.kwargs["poll"]
        assert poll.multiple is True

    @pytest.mark.asyncio
    async def test_notify_poll_default_duration(self, client: TestClient, bot: MagicMock) -> None:
        """Default duration is 24 hours when not specified."""
        channel = bot.get_channel.return_value
        resp = await client.post(
            "/api/notify",
            json={
                "message": "デフォルト期間テスト",
                "poll": {
                    "question": "テスト？",
                    "answers": ["はい", "いいえ"],
                },
            },
        )
        assert resp.status == 200
        poll = channel.send.call_args.kwargs["poll"]
        assert poll.duration.total_seconds() == 24 * 3600

    @pytest.mark.asyncio
    async def test_notify_poll_missing_question(self, client: TestClient) -> None:
        """Poll without question returns 400."""
        resp = await client.post(
            "/api/notify",
            json={
                "message": "テスト",
                "poll": {"answers": ["A", "B"]},
            },
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_notify_poll_missing_answers(self, client: TestClient) -> None:
        """Poll without answers returns 400."""
        resp = await client.post(
            "/api/notify",
            json={
                "message": "テスト",
                "poll": {"question": "テスト？"},
            },
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_notify_poll_too_few_answers(self, client: TestClient) -> None:
        """Poll with fewer than 2 answers returns 400."""
        resp = await client.post(
            "/api/notify",
            json={
                "message": "テスト",
                "poll": {"question": "テスト？", "answers": ["ひとつだけ"]},
            },
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_notify_poll_with_emoji_answers(self, client: TestClient, bot: MagicMock) -> None:
        """Answers with emoji objects are supported."""
        channel = bot.get_channel.return_value
        resp = await client.post(
            "/api/notify",
            json={
                "message": "絵文字付き",
                "poll": {
                    "question": "どれがいい？",
                    "answers": [
                        {"text": "Python", "emoji": "🐍"},
                        {"text": "Go", "emoji": "🐹"},
                    ],
                    "duration_hours": 24,
                },
            },
        )
        assert resp.status == 200
        poll = channel.send.call_args.kwargs["poll"]
        assert len(poll.answers) == 2


class TestNotifyThread:
    """Tests for thread_name parameter in /api/notify."""

    @pytest.fixture
    def bot_with_thread(self) -> MagicMock:
        """Bot mock whose channel supports create_thread().

        Simulates ThreadWithMessage (NamedTuple with .thread attribute)
        returned by TextChannel.create_thread() in discord.py v2.
        """
        b = MagicMock()
        channel = MagicMock()
        channel.send = AsyncMock()
        thread = MagicMock(spec=["id", "name", "send"])
        thread.id = 111222333
        thread.name = "PR Review"
        thread.send = AsyncMock()
        # Wrap in ThreadWithMessage-like object
        thread_with_msg = MagicMock(spec=["thread", "message"])
        thread_with_msg.thread = thread
        channel.create_thread = AsyncMock(return_value=thread_with_msg)
        b.get_channel.return_value = channel
        return b

    @pytest.fixture
    async def thread_client(
        self, repo: NotificationRepository, bot_with_thread: MagicMock
    ) -> TestClient:
        api = ApiServer(
            repo=repo,
            bot=bot_with_thread,
            default_channel_id=12345,
        )
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_notify_thread_creates_thread_and_sends_text(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """When thread_name is given, creates a thread and sends message as text."""
        channel = bot_with_thread.get_channel.return_value
        thread = channel.create_thread.return_value.thread
        resp = await thread_client.post(
            "/api/notify",
            json={
                "message": "PR #42 needs review",
                "thread_name": "PR Review",
                "format": "text",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "sent"
        assert data["thread_id"] == "111222333"
        channel.create_thread.assert_called_once_with(
            name="PR Review", auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
        )
        thread.send.assert_called_once_with("PR #42 needs review")
        # Channel.send should NOT be called — message goes to thread
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_thread_with_embed(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """When thread_name + embed format, embed goes to thread."""
        channel = bot_with_thread.get_channel.return_value
        thread = channel.create_thread.return_value.thread
        resp = await thread_client.post(
            "/api/notify",
            json={
                "message": "Summary here",
                "thread_name": "Summary Thread",
                "format": "embed",
            },
        )
        assert resp.status == 200
        channel.create_thread.assert_called_once_with(
            name="Summary Thread", auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
        )
        call_kwargs = thread.send.call_args.kwargs
        assert "embed" in call_kwargs
        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_thread_default_format_is_text(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """When thread_name is given without format, default to text (not embed)."""
        channel = bot_with_thread.get_channel.return_value
        thread = channel.create_thread.return_value.thread
        resp = await thread_client.post(
            "/api/notify",
            json={
                "message": "Auto text",
                "thread_name": "Auto Thread",
            },
        )
        assert resp.status == 200
        thread.send.assert_called_once_with("Auto text")

    @pytest.mark.asyncio
    async def test_notify_without_thread_name_sends_to_channel(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """Without thread_name, behaves as before — sends to channel."""
        channel = bot_with_thread.get_channel.return_value
        resp = await thread_client.post(
            "/api/notify",
            json={"message": "Channel message", "format": "text"},
        )
        assert resp.status == 200
        channel.send.assert_called_once_with("Channel message")
        channel.create_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_thread_returns_thread_id(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """Response includes thread_id when a thread is created."""
        resp = await thread_client.post(
            "/api/notify",
            json={"message": "test", "thread_name": "Test"},
        )
        data = await resp.json()
        assert data["thread_id"] == "111222333"
        assert data["thread_name"] == "PR Review"

    @pytest.mark.asyncio
    async def test_notify_blank_thread_name_sends_to_channel(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """Whitespace-only thread_name is treated as absent, avoiding Discord 400s."""
        channel = bot_with_thread.get_channel.return_value
        resp = await thread_client.post(
            "/api/notify",
            json={"message": "No thread please", "thread_name": "   "},
        )
        assert resp.status == 200
        channel.create_thread.assert_not_called()
        channel.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_thread_name_is_trimmed_and_limited_to_discord_max(
        self, thread_client: TestClient, bot_with_thread: MagicMock
    ) -> None:
        """Thread names are normalized before passing them to Discord."""
        channel = bot_with_thread.get_channel.return_value
        raw_name = f"  {'a' * 120}  "
        resp = await thread_client.post(
            "/api/notify",
            json={"message": "Long title", "thread_name": raw_name},
        )
        assert resp.status == 200
        channel.create_thread.assert_called_once_with(
            name="a" * 100, auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES
        )


class TestSchedule:
    @pytest.mark.asyncio
    async def test_schedule_creates_notification(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/schedule",
            json={
                "message": "Reminder",
                "scheduled_at": "2026-01-01T09:00:00",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "scheduled"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_schedule_missing_message(self, client: TestClient) -> None:
        resp = await client.post("/api/schedule", json={"scheduled_at": "2026-01-01T09:00:00"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_schedule_missing_time(self, client: TestClient) -> None:
        resp = await client.post("/api/schedule", json={"message": "test"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_schedule_invalid_time(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/schedule",
            json={
                "message": "test",
                "scheduled_at": "not-a-date",
            },
        )
        assert resp.status == 400


class TestListScheduled:
    @pytest.mark.asyncio
    async def test_list_empty(self, client: TestClient) -> None:
        resp = await client.get("/api/scheduled")
        assert resp.status == 200
        data = await resp.json()
        assert data["notifications"] == []

    @pytest.mark.asyncio
    async def test_list_after_schedule(self, client: TestClient) -> None:
        await client.post(
            "/api/schedule",
            json={
                "message": "test",
                "scheduled_at": "2026-01-01T09:00:00",
            },
        )
        resp = await client.get("/api/scheduled")
        data = await resp.json()
        assert len(data["notifications"]) == 1


class TestCancelScheduled:
    @pytest.mark.asyncio
    async def test_cancel_existing(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/schedule",
            json={
                "message": "test",
                "scheduled_at": "2026-01-01T09:00:00",
            },
        )
        nid = (await resp.json())["id"]
        resp = await client.delete(f"/api/scheduled/{nid}")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, client: TestClient) -> None:
        resp = await client.delete("/api/scheduled/99999")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_cancel_invalid_id(self, client: TestClient) -> None:
        resp = await client.delete("/api/scheduled/abc")
        assert resp.status == 400


class TestAuthentication:
    @pytest.mark.asyncio
    async def test_health_bypasses_auth(self, auth_client: TestClient) -> None:
        resp = await auth_client.get("/api/health")
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_missing_auth_header(self, auth_client: TestClient) -> None:
        resp = await auth_client.post("/api/notify", json={"message": "test"})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_invalid_token(self, auth_client: TestClient) -> None:
        resp = await auth_client.post(
            "/api/notify",
            json={"message": "test"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_valid_token(self, auth_client: TestClient, bot: MagicMock) -> None:
        resp = await auth_client.post(
            "/api/notify",
            json={"message": "test"},
            headers={"Authorization": "Bearer test-secret-123"},
        )
        assert resp.status == 200

    @pytest.mark.asyncio
    async def test_token_prefix_is_rejected(self, auth_client: TestClient) -> None:
        """正しいトークンの前方一致（短い部分文字列）でも 401 になること。

        素朴な `==` 比較ではなく `hmac.compare_digest` を使う前提のテスト。
        長さの異なる文字列でも安全に拒否されることを確認する。
        """
        resp = await auth_client.post(
            "/api/notify",
            json={"message": "test"},
            headers={"Authorization": "Bearer test-secret-12"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_token_longer_is_rejected(self, auth_client: TestClient) -> None:
        resp = await auth_client.post(
            "/api/notify",
            json={"message": "test"},
            headers={"Authorization": "Bearer test-secret-123-extra"},
        )
        assert resp.status == 401


@pytest.mark.asyncio
async def test_invalid_model_cannot_partially_switch_thread_backend(
    repo: NotificationRepository, bot: MagicMock
) -> None:
    api = ApiServer(repo=repo, bot=bot)
    settings = MagicMock()
    settings.set_backend = AsyncMock()
    settings.set_model = AsyncMock()
    api.backend_settings = settings
    client = TestClient(TestServer(api.app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/threads/1554145503506333736/runtime",
            json={"backend": "codex", "model": " "},
        )
        assert response.status == 400
        settings.set_backend.assert_not_awaited()
        settings.set_model.assert_not_awaited()
    finally:
        await client.close()


class TestSpawn:
    """Tests for POST /api/spawn — programmatic Claude session creation."""

    @pytest.fixture
    def mock_cog(self) -> MagicMock:
        """Mock ClaudeChatCog with a spawn_session that returns a fake thread."""
        thread = MagicMock()
        thread.id = 999888777
        thread.name = "Test thread"
        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=thread)
        return cog

    @pytest.fixture
    def bot_with_text_channel(self) -> MagicMock:
        """Bot mock whose get_channel() returns a discord.TextChannel spec mock."""
        import discord

        b = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        b.get_channel.return_value = channel
        return b

    @pytest.fixture
    async def spawn_client(
        self,
        repo: NotificationRepository,
        bot_with_text_channel: MagicMock,
        mock_cog: MagicMock,
    ) -> TestClient:
        """ApiServer client with ClaudeChatCog pre-loaded in bot.cogs."""
        bot_with_text_channel.cogs = {"ClaudeChatCog": mock_cog}
        api = ApiServer(repo=repo, bot=bot_with_text_channel, default_channel_id=12345)
        api.backend_settings = MagicMock()
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_spawn_forwards_user_id_as_invite(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        """A caller-supplied user_id must reach spawn_session, not be dropped."""
        resp = await spawn_client.post(
            "/api/spawn",
            json={"prompt": "Check the backlog", "user_id": 418192003549888523},
        )
        assert resp.status == 201
        assert mock_cog.spawn_session.await_args.kwargs["invite_user_id"] == 418192003549888523

    @pytest.mark.asyncio
    async def test_spawn_without_user_id_invites_nobody(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post("/api/spawn", json={"prompt": "Check the backlog"})
        assert mock_cog.spawn_session.await_args.kwargs["invite_user_id"] is None

    @pytest.mark.asyncio
    async def test_spawn_rejects_non_numeric_user_id(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await spawn_client.post("/api/spawn", json={"prompt": "Hello", "user_id": "@ebi"})
        assert resp.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_rejects_non_positive_user_id(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await spawn_client.post("/api/spawn", json={"prompt": "Hello", "user_id": 0})
        assert resp.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_returns_201_with_thread_info(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await spawn_client.post("/api/spawn", json={"prompt": "Do something useful"})
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "spawned"
        assert data["thread_id"] == "999888777"
        assert data["thread_name"] == "Test thread"

    @pytest.mark.asyncio
    async def test_spawn_uses_single_thread_tagger_so_live_tags_are_not_stolen(
        self, spawn_client: TestClient
    ) -> None:
        with patch(
            "claude_discord.ext.api_server.VoiceTagger.tag_thread",
            new_callable=AsyncMock,
            return_value=None,
        ) as tag_thread:
            resp = await spawn_client.post("/api/spawn", json={"prompt": "Check the backlog"})
        assert resp.status == 201
        assert (await resp.json())["voice_label"] is None
        tag_thread.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_spawn_passes_prompt_to_cog(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post("/api/spawn", json={"prompt": "Organise Todoist inbox"})
        mock_cog.spawn_session.assert_called_once()
        _channel, prompt = mock_cog.spawn_session.call_args.args
        assert prompt == "Organise Todoist inbox"

    @pytest.mark.asyncio
    async def test_spawn_passes_thread_name_when_given(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post(
            "/api/spawn",
            json={"prompt": "Long prompt", "thread_name": "Custom title"},
        )
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs.get("thread_name") == "Custom title"

    @pytest.mark.asyncio
    async def test_spawn_forwards_working_directory(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post(
            "/api/spawn",
            json={"prompt": "Inspect project", "working_dir": "/home/user/project"},
        )

        assert mock_cog.spawn_session.await_args.kwargs["working_dir"] == "/home/user/project"

    @pytest.mark.asyncio
    async def test_spawn_sets_requested_backend_and_model_before_first_turn(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        response = await spawn_client.post(
            "/api/spawn",
            json={"prompt": "Inspect project", "backend": "codex", "model": "auto"},
        )
        assert response.status == 201
        kwargs = mock_cog.spawn_session.await_args.kwargs
        assert kwargs["backend"] == "codex"
        assert kwargs["model"] == "auto"
        assert kwargs["auto_start"] is True

    @pytest.mark.asyncio
    async def test_spawn_refuses_model_without_backend(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        response = await spawn_client.post(
            "/api/spawn", json={"prompt": "Inspect project", "model": "auto"}
        )
        assert response.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_rejects_non_string_working_directory(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        response = await spawn_client.post(
            "/api/spawn",
            json={"prompt": "Inspect project", "working_dir": {"path": "/tmp"}},
        )

        assert response.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_thread_name_defaults_to_none(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post("/api/spawn", json={"prompt": "Some prompt"})
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs.get("thread_name") is None

    @pytest.mark.asyncio
    async def test_spawn_missing_prompt_returns_400(self, spawn_client: TestClient) -> None:
        resp = await spawn_client.post("/api/spawn", json={})
        assert resp.status == 400
        data = await resp.json()
        assert "prompt" in data["error"]

    @pytest.mark.asyncio
    async def test_spawn_empty_prompt_returns_400(self, spawn_client: TestClient) -> None:
        resp = await spawn_client.post("/api/spawn", json={"prompt": "   "})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_spawn_without_cog_returns_503(
        self, repo: NotificationRepository, bot: MagicMock
    ) -> None:
        bot.cogs = {}  # No ClaudeChatCog loaded
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/spawn", json={"prompt": "Hello"})
            assert resp.status == 503
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_spawn_no_channel_returns_400(
        self, repo: NotificationRepository, mock_cog: MagicMock
    ) -> None:
        bot = MagicMock()
        bot.cogs = {"ClaudeChatCog": mock_cog}
        # No default_channel_id, no channel_id in body
        api = ApiServer(repo=repo, bot=bot, default_channel_id=None)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/spawn", json={"prompt": "Hello"})
            assert resp.status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_spawn_auto_start_defaults_to_true(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post("/api/spawn", json={"prompt": "Hello"})
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs.get("auto_start") is True

    @pytest.mark.asyncio
    async def test_spawn_accepts_payload_over_1mb(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        """A >1MB attachment body must not be 413'd (aiohttp's default body limit
        is 1MB; ApiServer raises client_max_size for base64 payloads)."""
        import base64

        blob = b"\x00" * (2 * 1024 * 1024)  # 2 MB → ~2.7 MB base64
        resp = await spawn_client.post(
            "/api/spawn",
            json={
                "prompt": "big attachment",
                "attachments": [{"filename": "big.bin", "data": base64.b64encode(blob).decode()}],
            },
        )
        assert resp.status == 201
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs["attachments"][0][1] == blob

    @pytest.mark.asyncio
    async def test_spawn_decodes_attachments_and_passes_to_cog(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        import base64

        blob = b"%PDF-1.4 hello"
        resp = await spawn_client.post(
            "/api/spawn",
            json={
                "prompt": "Issue with attachment",
                "attachments": [{"filename": "spec.pdf", "data": base64.b64encode(blob).decode()}],
            },
        )
        assert resp.status == 201
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs.get("attachments") == [("spec.pdf", blob)]

    @pytest.mark.asyncio
    async def test_spawn_without_attachments_passes_none(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post("/api/spawn", json={"prompt": "No files"})
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs.get("attachments") is None

    @pytest.mark.asyncio
    async def test_spawn_invalid_base64_attachment_returns_400(
        self, spawn_client: TestClient
    ) -> None:
        resp = await spawn_client.post(
            "/api/spawn",
            json={
                "prompt": "Bad file",
                "attachments": [{"filename": "x.bin", "data": "not!!base64!!"}],
            },
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_spawn_attachments_must_be_a_list(self, spawn_client: TestClient) -> None:
        resp = await spawn_client.post(
            "/api/spawn",
            json={"prompt": "x", "attachments": {"filename": "a"}},
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_spawn_sanitizes_attachment_filename(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        import base64

        await spawn_client.post(
            "/api/spawn",
            json={
                "prompt": "traversal",
                "attachments": [
                    {"filename": "../../etc/passwd", "data": base64.b64encode(b"x").decode()}
                ],
            },
        )
        kwargs = mock_cog.spawn_session.call_args.kwargs
        name = kwargs["attachments"][0][0]
        assert "/" not in name and ".." not in name

    @pytest.mark.asyncio
    async def test_spawn_auto_start_false_passed_to_cog(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        await spawn_client.post(
            "/api/spawn",
            json={"prompt": "Notify only", "auto_start": False},
        )
        kwargs = mock_cog.spawn_session.call_args.kwargs
        assert kwargs.get("auto_start") is False

    @pytest.mark.asyncio
    async def test_explicit_empty_spawn_creates_a_thread_without_a_task(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await spawn_client.post(
            "/api/spawn",
            json={
                "empty": True,
                "thread_name": "Jobs",
                "auto_start": False,
                "working_dir": "/projects/jobs",
                "backend": "codex",
            },
        )
        assert resp.status == 201
        assert mock_cog.spawn_session.await_args.args[1] == ""
        assert mock_cog.spawn_session.await_args.kwargs["auto_start"] is False

    @pytest.mark.asyncio
    async def test_empty_spawn_requires_a_title_and_no_automatic_task(
        self, spawn_client: TestClient, mock_cog: MagicMock
    ) -> None:
        for body in (
            {"empty": True},
            {"empty": True, "thread_name": "Jobs"},
            {"empty": True, "thread_name": "Jobs", "auto_start": False, "prompt": "run this"},
        ):
            resp = await spawn_client.post("/api/spawn", json=body)
            assert resp.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_invalid_json_returns_400(self, spawn_client: TestClient) -> None:
        resp = await spawn_client.post(
            "/api/spawn",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400


class TestSpawnMetadata:
    """Generic correlation/parent metadata on /api/spawn (parallel-gowork 5.1).

    A coordinator that records "spawning" before the request and then loses the
    answer needs one thing from the control plane: a way to ask "did the spawn
    with *this* identity happen?" without spawning again. The fields are
    generic — any caller may set them — and optional, so existing consumers
    see no change.
    """

    PARENT = 1550757693784989707
    CORRELATION = "1550757693784989707:weekly-digest-1a2b3c4d"

    @pytest.fixture
    def mock_cog(self) -> MagicMock:
        thread = MagicMock()
        thread.id = 999888777
        thread.name = "Test thread"
        thread.edit = AsyncMock()
        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=thread)
        return cog

    @pytest.fixture
    async def settings_repo(self):
        from claude_discord.database.models import init_db
        from claude_discord.database.settings_repo import SettingsRepository

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await init_db(path)
        yield SettingsRepository(path)
        os.unlink(path)

    @pytest.fixture
    async def meta_client(
        self, repo: NotificationRepository, mock_cog: MagicMock, settings_repo
    ) -> TestClient:
        import discord

        bot = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        bot.get_channel.return_value = channel
        bot.cogs = {"ClaudeChatCog": mock_cog}
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        api.settings_repo = settings_repo
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()

    @pytest.fixture
    async def bare_client(self, repo: NotificationRepository, mock_cog: MagicMock) -> TestClient:
        """No settings repo wired: the fields are accepted and echoed, not stored."""
        import discord

        bot = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        bot.get_channel.return_value = channel
        bot.cogs = {"ClaudeChatCog": mock_cog}
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_spawn_without_metadata_is_unchanged(self, meta_client: TestClient) -> None:
        resp = await meta_client.post("/api/spawn", json={"prompt": "Hello"})
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "spawned"
        assert data["parent_thread_id"] is None
        assert data["correlation_id"] is None

    @pytest.mark.asyncio
    async def test_spawn_echoes_and_records_metadata(
        self, meta_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await meta_client.post(
            "/api/spawn",
            json={
                "prompt": "Plan the digest",
                "parent_thread_id": self.PARENT,
                "correlation_id": self.CORRELATION,
            },
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["thread_id"] == "999888777"
        assert data["parent_thread_id"] == str(self.PARENT)
        assert data["correlation_id"] == self.CORRELATION
        mock_cog.spawn_session.assert_awaited_once()

        found = await meta_client.get(f"/api/correlations/{self.CORRELATION}")
        assert found.status == 200
        body = await found.json()
        assert body["thread_id"] == "999888777"
        assert body["parent_thread_id"] == str(self.PARENT)
        assert body["correlation_id"] == self.CORRELATION

        meta = await meta_client.get("/api/threads/999888777/metadata")
        assert meta.status == 200
        assert (await meta.json())["parent_thread_id"] == str(self.PARENT)

    @pytest.mark.asyncio
    async def test_repeated_correlation_returns_the_existing_thread_without_spawning(
        self, meta_client: TestClient, mock_cog: MagicMock
    ) -> None:
        body = {"prompt": "Plan the digest", "correlation_id": self.CORRELATION}
        first = await meta_client.post("/api/spawn", json=body)
        assert first.status == 201
        second = await meta_client.post("/api/spawn", json=body)
        assert second.status == 200
        data = await second.json()
        assert data["status"] == "existing"
        assert data["thread_id"] == "999888777"
        assert data["correlation_id"] == self.CORRELATION
        assert mock_cog.spawn_session.await_count == 1

    @pytest.mark.asyncio
    async def test_unknown_correlation_is_404(self, meta_client: TestClient) -> None:
        resp = await meta_client.get("/api/correlations/never-spawned")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_thread_without_metadata_is_404(self, meta_client: TestClient) -> None:
        resp = await meta_client.get("/api/threads/424242/metadata")
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_lookup_without_a_store_is_503_not_absent(self, bare_client: TestClient) -> None:
        """'Not stored anywhere' must not read as 'provably never spawned'."""
        resp = await bare_client.get(f"/api/correlations/{self.CORRELATION}")
        assert resp.status == 503

    @pytest.mark.asyncio
    async def test_bare_server_still_accepts_and_echoes_the_fields(
        self, bare_client: TestClient
    ) -> None:
        resp = await bare_client.post(
            "/api/spawn",
            json={"prompt": "Hello", "parent_thread_id": self.PARENT, "correlation_id": "abc"},
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["parent_thread_id"] == str(self.PARENT)
        assert data["correlation_id"] == "abc"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["abc", 0, -5, 1.5, True])
    async def test_invalid_parent_thread_id_is_400(
        self, meta_client: TestClient, mock_cog: MagicMock, value: object
    ) -> None:
        resp = await meta_client.post(
            "/api/spawn", json={"prompt": "Hello", "parent_thread_id": value}
        )
        assert resp.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["", " ", "has space", "a/b", "x" * 121, 7, "../etc"])
    async def test_invalid_correlation_id_is_400(
        self, meta_client: TestClient, mock_cog: MagicMock, value: object
    ) -> None:
        resp = await meta_client.post(
            "/api/spawn", json={"prompt": "Hello", "correlation_id": value}
        )
        assert resp.status == 400
        mock_cog.spawn_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_metadata_routes_are_not_on_the_external_listener(
        self, repo: NotificationRepository
    ) -> None:
        api = ApiServer(repo=repo, bot=MagicMock(), default_channel_id=12345)
        external = {resource.canonical for resource in api.external_app.router.resources()}
        assert "/api/correlations/{correlation_id}" not in external
        assert "/api/threads/{thread_id}/metadata" not in external
        internal = {resource.canonical for resource in api.app.router.resources()}
        assert "/api/correlations/{correlation_id}" in internal
        assert "/api/threads/{thread_id}/metadata" in internal


class TestMarkResume:
    """Tests for POST /api/mark-resume endpoint."""

    @pytest.fixture
    async def resume_client(self, repo: NotificationRepository, bot: MagicMock) -> TestClient:
        import os
        import tempfile

        from claude_discord.database.models import init_db as _init
        from claude_discord.database.resume_repo import PendingResumeRepository

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await _init(path)
        resume_repo = PendingResumeRepository(path)

        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345, resume_repo=resume_repo)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_mark_resume_returns_201(self, resume_client: TestClient) -> None:
        resp = await resume_client.post("/api/mark-resume", json={"thread_id": 123456789})
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "marked"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_mark_resume_with_all_fields(self, resume_client: TestClient) -> None:
        resp = await resume_client.post(
            "/api/mark-resume",
            json={
                "thread_id": 987654321,
                "session_id": "abc-123",
                "reason": "self_restart",
                "resume_prompt": "Please continue the previous task.",
            },
        )
        assert resp.status == 201

    @pytest.mark.asyncio
    async def test_mark_resume_missing_thread_id_returns_400(
        self, resume_client: TestClient
    ) -> None:
        resp = await resume_client.post("/api/mark-resume", json={})
        assert resp.status == 400
        data = await resp.json()
        assert "thread_id" in data["error"]

    @pytest.mark.asyncio
    async def test_mark_resume_invalid_thread_id_returns_400(
        self, resume_client: TestClient
    ) -> None:
        resp = await resume_client.post("/api/mark-resume", json={"thread_id": "not-a-number"})
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_mark_resume_without_repo_returns_503(
        self, repo: NotificationRepository, bot: MagicMock
    ) -> None:
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)  # no resume_repo
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/mark-resume", json={"thread_id": 111})
            assert resp.status == 503
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_mark_resume_invalid_json_returns_400(self, resume_client: TestClient) -> None:
        resp = await resume_client.post(
            "/api/mark-resume",
            data=b"bad",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400


class TestSessionSnapshot:
    """Tests for the side-effect-free Jester session snapshot."""

    def _record(
        self,
        thread_id: int,
        *,
        session_id: str = "session",
        working_dir: str | None = "/home/drewp/main-projects/repo",
        last_used_at: str = "2026-09-28 10:00:00",
        lifecycle_state: str = "open",
    ) -> SessionRecord:
        return SessionRecord(
            thread_id=thread_id,
            session_id=session_id,
            working_dir=working_dir,
            model=None,
            origin="discord",
            summary=None,
            created_at="2026-09-28 09:00:00",
            last_used_at=last_used_at,
            lifecycle_state=lifecycle_state,
        )

    @staticmethod
    def _session_repo(records: list[SessionRecord]) -> MagicMock:
        """A session store whose ``list_all`` filters and caps like the real one."""

        async def list_all(
            limit: int = 50, origin: str | None = None, *, lifecycle_state: object = None
        ) -> list[SessionRecord]:
            state = getattr(lifecycle_state, "value", lifecycle_state)
            rows = [r for r in records if state is None or r.lifecycle_state == state]
            rows.sort(key=lambda r: r.last_used_at, reverse=True)
            return rows[:limit]

        session_repo = MagicMock()
        session_repo.list_all = AsyncMock(side_effect=list_all)
        return session_repo

    @staticmethod
    def _thread(name: str, *, archived: bool = False) -> MagicMock:
        thread = MagicMock()
        thread.name = name
        thread.archived = archived
        thread.edit = AsyncMock()
        return thread

    @staticmethod
    def _bot(cached: dict[int, MagicMock] | None = None) -> MagicMock:
        """A bot whose channel cache holds exactly ``cached`` and fetches nothing."""
        bot = MagicMock()
        bot.cogs = {}
        bot.session_registry = None
        bot.guilds = []
        known = cached or {}
        bot.get_channel.side_effect = lambda thread_id: known.get(thread_id)
        bot.fetch_channel = AsyncMock()
        return bot

    @staticmethod
    def _api(
        repo: NotificationRepository,
        *,
        bot: MagicMock,
        session_repo: object,
        settings_repo: MagicMock | None = None,
    ) -> ApiServer:
        api = ApiServer(
            repo=repo,
            bot=bot,
            default_channel_id=12345,
            session_repo=session_repo,  # type: ignore[arg-type]
        )
        api.settings_repo = settings_repo
        return api

    @staticmethod
    @contextlib.asynccontextmanager
    async def _serving(api: ApiServer) -> AsyncIterator[TestClient]:
        client = TestClient(TestServer(api.app))
        await client.start_server()
        try:
            yield client
        finally:
            await client.close()

    @pytest.fixture
    async def session_db(self) -> AsyncIterator[str]:
        from claude_discord.database.models import init_db

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await init_db(path)
        yield path
        os.unlink(path)

    @staticmethod
    async def _seed(path: str, *, open_indexes: set[int], total: int = 150) -> None:
        """Write ``total`` rows in one transaction; row ``i`` is newer than row ``i - 1``."""
        base = datetime(2026, 9, 1, 0, 0, 0)
        rows = [
            (
                1000 + i,
                f"sess-{i}",
                (base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"),
                "open" if i in open_indexes else "closed",
            )
            for i in range(total)
        ]
        async with aiosqlite.connect(path) as db:
            await db.executemany(
                "INSERT INTO sessions (thread_id, session_id, last_used_at, lifecycle_state)"
                " VALUES (?, ?, ?, ?)",
                rows,
            )
            await db.commit()

    def test_snapshot_helper_maps_tagged_row_with_exact_large_thread_id(self) -> None:
        thread_id = 1554146845415055445

        sessions = build_jester_session_snapshot(
            records=[self._record(thread_id)],
            active=[self._record(42)],
            running_thread_ids={thread_id, 42},
            thread_names={thread_id: "[zoro] repo session"},
            voice_labels={thread_id: "zoro"},
        )

        assert sessions == [
            {
                "thread_id": "1554146845415055445",
                "tag": "zoro",
                "name": "[zoro] repo session",
                "project": "/home/drewp/main-projects/repo",
            }
        ]

    def test_snapshot_helper_does_not_modify_input_rows(self) -> None:
        thread_id = 1554146845415055445
        record = self._record(
            thread_id,
            session_id="sess-exact",
            working_dir="/home/drewp/main-projects/unchanged",
        )
        before = asdict(record)

        sessions = build_jester_session_snapshot(
            records=[record],
            active=[],
            running_thread_ids=set(),
            thread_names={thread_id: "[zoro] unchanged"},
            voice_labels={thread_id: "zoro"},
        )

        assert asdict(record) == before
        assert sessions[0] == {
            "thread_id": "1554146845415055445",
            "tag": "zoro",
            "name": "[zoro] unchanged",
            "project": "/home/drewp/main-projects/unchanged",
        }

    @pytest.mark.asyncio
    async def test_snapshot_route_needs_a_session_store(self, client: TestClient) -> None:
        assert (await client.get("/api/sessions/snapshot")).status == 404
        assert (await client.get("/api/jester/sessions")).status == 503

    @pytest.mark.asyncio
    async def test_repeated_snapshot_reads_do_not_mint_tags_or_rename_threads(self, repo) -> None:
        thread_id = 1554146845415055445
        record = self._record(thread_id)
        before = asdict(record)
        session_repo = self._session_repo([record])
        settings_repo = MagicMock()
        settings_repo.get_all = AsyncMock(return_value={f"voice_label:{thread_id}": "franky"})
        channel = self._thread("[franky] project work")
        bot = self._bot({thread_id: channel})
        api = self._api(repo, bot=bot, session_repo=session_repo, settings_repo=settings_repo)
        async with self._serving(api) as client:
            with patch.object(api, "_apply_voice_labels", new_callable=AsyncMock) as tagger:
                first = await (await client.get("/api/jester/sessions")).json()
                second = await (await client.get("/api/jester/sessions")).json()
        assert first["sessions"] == second["sessions"]
        assert first["open_count"] == second["open_count"] == 1
        assert first["sessions"][0]["thread_id"] == str(thread_id)
        assert first["sessions"][0]["tag"] == "franky"
        assert "frankie" in first["sessions"][0]["aliases"]
        assert first["sessions"][0]["state"] == "history"
        assert first["sessions"][0]["visible"] is True
        assert asdict(record) == before
        tagger.assert_not_awaited()
        # The read touches nothing but the two listings: no row, setting or title changes.
        assert {call[0] for call in session_repo.mock_calls} == {"list_all"}
        assert {call[0] for call in settings_repo.mock_calls} == {"get_all"}
        assert channel.mock_calls == []
        bot.fetch_channel.assert_not_awaited()
        assert settings_repo.get_all.await_count == 2

    @pytest.mark.asyncio
    async def test_snapshot_says_how_many_spoken_tag_words_exist(self, repo) -> None:
        """Jester's "why has this thread no tag?" answer needs the size of the pool."""
        thread_id = 1554146845415055445
        session_repo = self._session_repo([self._record(thread_id)])
        settings_repo = MagicMock()
        settings_repo.get_all = AsyncMock(return_value={})
        bot = self._bot({thread_id: self._thread("project work")})
        api = self._api(repo, bot=bot, session_repo=session_repo, settings_repo=settings_repo)
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()
        assert body["tag_words"] == 10
        assert {"sessions", "open_count", "discord_active_threads", "generated_at"} <= set(body)

    @pytest.mark.asyncio
    async def test_every_open_row_is_listed_and_closed_rows_are_not(
        self, repo: NotificationRepository, session_db: str
    ) -> None:
        # 150 rows, three open; the oldest open row (1000) lies beyond any newest-100 page.
        await self._seed(session_db, open_indexes={0, 75, 149})
        bot = self._bot()
        bot.fetch_channel = AsyncMock(side_effect=lambda tid: self._thread(f"thread {tid}"))
        api = self._api(repo, bot=bot, session_repo=SessionRepository(session_db))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        rows = body["sessions"]
        assert {row["thread_id"] for row in rows} == {"1000", "1075", "1149"}
        assert [row["closed"] for row in rows] == [False, False, False]
        assert body["open_count"] == 3
        assert {row["name"] for row in rows} == {"thread 1000", "thread 1075", "thread 1149"}
        assert [row["visible"] for row in rows] == [True, True, True]
        assert {call.args[0] for call in bot.fetch_channel.await_args_list} == {1000, 1075, 1149}
        assert datetime.fromisoformat(body["generated_at"]).tzinfo is not None

    @pytest.mark.asyncio
    async def test_include_closed_adds_the_newest_closed_rows_flagged_closed(
        self, repo: NotificationRepository, session_db: str
    ) -> None:
        await self._seed(session_db, open_indexes={0, 75, 149})
        bot = self._bot()
        bot.fetch_channel = AsyncMock(side_effect=lambda tid: self._thread(f"thread {tid}"))
        api = self._api(repo, bot=bot, session_repo=SessionRepository(session_db))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions?include_closed=1")).json()
            capped = await (
                await client.get("/api/jester/sessions?include_closed=1&limit=10")
            ).json()
            assert (await client.get("/api/jester/sessions?include_closed=maybe")).status == 400

        open_rows = [row for row in body["sessions"] if not row["closed"]]
        closed_rows = [row for row in body["sessions"] if row["closed"]]
        assert {row["thread_id"] for row in open_rows} == {"1000", "1075", "1149"}
        assert len(closed_rows) == 100
        closed_ids = {row["thread_id"] for row in closed_rows}
        assert "1148" in closed_ids
        assert "1001" not in closed_ids  # the newest hundred, not the oldest
        assert all(row["tag"] is None and row["aliases"] == [] for row in closed_rows)
        assert body["open_count"] == 3
        assert sum(row["closed"] for row in capped["sessions"]) == 10
        # Closed rows are never fetched, and the second read is served from the cache.
        assert {call.args[0] for call in bot.fetch_channel.await_args_list} == {1000, 1075, 1149}
        assert bot.fetch_channel.await_count == 3

    @pytest.mark.asyncio
    async def test_uncached_open_row_is_named_by_one_fetch_then_remembered(self, repo) -> None:
        bot = self._bot()
        bot.fetch_channel = AsyncMock(return_value=self._thread("[zoro] repo session"))
        api = self._api(repo, bot=bot, session_repo=self._session_repo([self._record(4242)]))
        async with self._serving(api) as client:
            first = await (await client.get("/api/jester/sessions")).json()
            second = await (await client.get("/api/jester/sessions")).json()

        assert first["sessions"][0]["name"] == "[zoro] repo session"
        assert first["sessions"][0]["visible"] is True
        assert second["sessions"] == first["sessions"]
        bot.fetch_channel.assert_awaited_once_with(4242)

    @pytest.mark.asyncio
    async def test_archived_cached_thread_is_not_visible(self, repo) -> None:
        bot = self._bot({4242: self._thread("[zoro] parked", archived=True)})
        api = self._api(repo, bot=bot, session_repo=self._session_repo([self._record(4242)]))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        assert body["sessions"][0]["name"] == "[zoro] parked"
        assert body["sessions"][0]["visible"] is False
        bot.fetch_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gone_thread_is_not_visible_and_is_not_asked_again(self, repo) -> None:
        bot = self._bot()
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        api = self._api(repo, bot=bot, session_repo=self._session_repo([self._record(4242)]))
        async with self._serving(api) as client:
            first = await (await client.get("/api/jester/sessions")).json()
            second = await (await client.get("/api/jester/sessions")).json()

        assert first["sessions"][0]["visible"] is False
        assert first["sessions"][0]["name"] is None
        assert second["sessions"] == first["sessions"]
        bot.fetch_channel.assert_awaited_once_with(4242)

    @pytest.mark.asyncio
    async def test_fetches_are_capped_and_the_newest_threads_are_resolved_first(self, repo) -> None:
        records = [
            self._record(1000 + i, last_used_at=f"2026-09-28 10:{i:02d}:00") for i in range(30)
        ]
        bot = self._bot()
        bot.fetch_channel = AsyncMock(side_effect=lambda tid: self._thread(f"thread {tid}"))
        api = self._api(repo, bot=bot, session_repo=self._session_repo(records))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        assert bot.fetch_channel.await_count == 25
        unknown = [row for row in body["sessions"] if row["visible"] is None]
        # The five oldest wait for the next read; their names are unknown, not invented.
        assert {row["thread_id"] for row in unknown} == {str(1000 + i) for i in range(5)}
        assert all(row["name"] is None for row in unknown)
        assert body["open_count"] == 30

    @pytest.mark.asyncio
    async def test_a_failing_fetch_leaves_visibility_unknown_without_failing_the_read(
        self, repo
    ) -> None:
        records = [
            self._record(1000 + i, last_used_at=f"2026-09-28 10:{i:02d}:00") for i in range(3)
        ]
        bot = self._bot()
        bot.fetch_channel = AsyncMock(side_effect=RuntimeError("discord unreachable"))
        api = self._api(repo, bot=bot, session_repo=self._session_repo(records))
        async with self._serving(api) as client:
            resp = await client.get("/api/jester/sessions")
            assert resp.status == 200
            body = await resp.json()

        assert [row["visible"] for row in body["sessions"]] == [None, None, None]
        assert [row["name"] for row in body["sessions"]] == [None, None, None]
        # One failure ends fetching for this read rather than repeating it per row.
        assert bot.fetch_channel.await_count == 1

    _BOT_ID = 1546642963709427832
    _OTHER_BOT_ID = 1550644558176460961
    _OWNER_ID = 424242

    @staticmethod
    def _owned_thread(
        thread_id: int,
        name: str,
        *,
        owner_id: int | None,
        channel: str | None = "control-center",
        archived: bool = False,
    ) -> MagicMock:
        thread = MagicMock()
        thread.id = thread_id
        thread.name = name
        thread.archived = archived
        thread.owner_id = owner_id
        thread.parent = None if channel is None else MagicMock()
        if thread.parent is not None:
            thread.parent.name = channel
        return thread

    def _guild_bot(self, threads: list[MagicMock], *, names: dict[int, str]) -> MagicMock:
        """A bot user ``_BOT_ID`` whose cache holds ``threads`` and knows ``names``."""
        guild = MagicMock()
        guild.threads = threads

        def member(user_id: int) -> MagicMock | None:
            if user_id not in names:
                return None
            found = MagicMock()
            found.display_name = names[user_id]
            return found

        guild.get_member.side_effect = member
        bot = self._bot({t.id: t for t in threads})
        bot.user.id = self._BOT_ID
        bot.guilds = [guild]
        bot.get_user.return_value = None
        return bot

    @pytest.mark.asyncio
    async def test_discord_active_threads_counts_only_unarchived_cached_threads(self, repo) -> None:
        guild_threads = [
            self._owned_thread(1, "a", owner_id=self._BOT_ID),
            self._owned_thread(2, "b", owner_id=self._BOT_ID, archived=True),
            self._owned_thread(3, "c", owner_id=self._BOT_ID),
        ]
        bot = self._guild_bot(guild_threads, names={})
        api = self._api(repo, bot=bot, session_repo=self._session_repo([]))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        assert body["discord_active_threads"] == 2
        assert body["sessions"] == []
        assert body["open_count"] == 0
        assert body["other_threads"] == []

    @pytest.mark.asyncio
    async def test_other_bots_and_hand_made_threads_are_listed_not_counted(self, repo) -> None:
        guild_threads = [
            self._owned_thread(100, "[zoro] podlox", owner_id=self._BOT_ID),
            self._owned_thread(101, "cranesignal", owner_id=self._OTHER_BOT_ID, channel="workers"),
            self._owned_thread(102, "youtube-money", owner_id=self._OTHER_BOT_ID),
            self._owned_thread(103, "hand made", owner_id=self._OWNER_ID),
            self._owned_thread(104, "old foreign", owner_id=self._OTHER_BOT_ID, archived=True),
        ]
        bot = self._guild_bot(guild_threads, names={self._OTHER_BOT_ID: "david"})
        api = self._api(repo, bot=bot, session_repo=self._session_repo([self._record(100)]))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        assert body["open_count"] == 1
        assert body["discord_active_threads"] == 1
        assert body["other_threads"] == [
            {
                "thread_id": "103",
                "name": "hand made",
                "owner_id": str(self._OWNER_ID),
                "owner_name": None,
                "channel": "control-center",
            },
            {
                "thread_id": "102",
                "name": "youtube-money",
                "owner_id": str(self._OTHER_BOT_ID),
                "owner_name": "david",
                "channel": "control-center",
            },
            {
                "thread_id": "101",
                "name": "cranesignal",
                "owner_id": str(self._OTHER_BOT_ID),
                "owner_name": "david",
                "channel": "workers",
            },
        ]
        # Reading other threads is cache-only.
        bot.fetch_channel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_hand_made_thread_with_a_session_row_counts_as_ebis_own(self, repo) -> None:
        guild_threads = [self._owned_thread(200, "hand made", owner_id=self._OWNER_ID)]
        bot = self._guild_bot(guild_threads, names={})
        api = self._api(repo, bot=bot, session_repo=self._session_repo([self._record(200)]))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        assert body["open_count"] == body["discord_active_threads"] == 1
        assert body["other_threads"] == []

    @pytest.mark.asyncio
    async def test_other_threads_are_capped_at_25_newest_first(self, repo) -> None:
        guild_threads = [
            self._owned_thread(300 + i, f"t{i}", owner_id=self._OTHER_BOT_ID, channel=None)
            for i in range(30)
        ]
        bot = self._guild_bot(guild_threads, names={})
        user = MagicMock()
        user.display_name = "I mac codex"
        bot.get_user.side_effect = lambda uid: user if uid == self._OTHER_BOT_ID else None
        api = self._api(repo, bot=bot, session_repo=self._session_repo([]))
        async with self._serving(api) as client:
            body = await (await client.get("/api/jester/sessions")).json()

        others = body["other_threads"]
        assert body["discord_active_threads"] == 0
        assert len(others) == 25
        assert [o["thread_id"] for o in others] == [str(329 - i) for i in range(25)]
        assert others[0]["owner_name"] == "I mac codex"
        assert others[0]["channel"] is None

    @pytest.mark.asyncio
    async def test_old_sessions_endpoint_keeps_numeric_thread_ids(self) -> None:
        from claude_discord.database.lounge_repo import LoungeRepository
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await init_db(path)
        notif_repo = NotificationRepository(path)
        await notif_repo.init_db()
        bot = MagicMock()
        bot.session_registry = None
        bot.cogs = {}
        bot.get_channel.return_value = None
        api = ApiServer(
            repo=notif_repo,
            bot=bot,
            default_channel_id=12345,
            session_repo=SessionRepository(path),
            lounge_repo=LoungeRepository(path),
        )
        client = TestClient(TestServer(api.app))
        await client.start_server()
        try:
            await SessionRepository(path).save(thread_id=444, session_id="sess-444")

            resp = await client.get("/api/sessions")

            assert resp.status == 200
            session = (await resp.json())["sessions"][0]
            assert session["thread_id"] == 444
        finally:
            await client.close()
            os.unlink(path)


class TestIngest:
    """Tests for POST /api/ingest — authenticated external spawn with attachments."""

    INGEST_TOKEN = "ingest-secret-xyz"
    AUTH = {"Authorization": f"Bearer {INGEST_TOKEN}"}

    @pytest.fixture
    def mock_cog(self) -> MagicMock:
        thread = MagicMock()
        thread.id = 111222333
        thread.name = "Ingested thread"
        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=thread)
        return cog

    @pytest.fixture
    def bot_with_text_channel(self, mock_cog: MagicMock) -> MagicMock:
        import discord

        b = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        b.get_channel.return_value = channel
        b.cogs = {"ClaudeChatCog": mock_cog}
        return b

    @pytest.fixture
    async def ingest_client(
        self,
        repo: NotificationRepository,
        bot_with_text_channel: MagicMock,
        tmp_path,
    ) -> TestClient:
        api = ApiServer(
            repo=repo,
            bot=bot_with_text_channel,
            default_channel_id=12345,
            ingest_token=self.INGEST_TOKEN,
            working_dir=str(tmp_path),
        )
        api._ingest_tmp = str(tmp_path)  # expose for assertions
        server = TestServer(api.app)
        client = TestClient(server)
        client._api = api  # type: ignore[attr-defined]
        await client.start_server()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_ingest_disabled_without_token(
        self, repo: NotificationRepository, bot_with_text_channel: MagicMock
    ) -> None:
        api = ApiServer(repo=repo, bot=bot_with_text_channel, default_channel_id=12345)
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/ingest", json={"content": "hi"}, headers=self.AUTH)
            assert resp.status == 503
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ingest_runs_in_the_directory_that_owns_its_saved_files(
        self,
        ingest_client: TestClient,
        mock_cog: MagicMock,
        tmp_path,
    ) -> None:
        response = await ingest_client.post(
            "/api/ingest",
            json={"content": "Inspect the imported material"},
            headers=self.AUTH,
        )

        assert response.status == 201
        assert mock_cog.spawn_session.await_args.kwargs["working_dir"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_ingest_missing_auth_returns_401(self, ingest_client: TestClient) -> None:
        resp = await ingest_client.post("/api/ingest", json={"content": "hi"})
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_ingest_wrong_token_returns_401(self, ingest_client: TestClient) -> None:
        resp = await ingest_client.post(
            "/api/ingest",
            json={"content": "hi"},
            headers={"Authorization": "Bearer nope"},
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_ingest_returns_201_with_thread_info(self, ingest_client: TestClient) -> None:
        resp = await ingest_client.post(
            "/api/ingest", json={"content": "Teams thread body"}, headers=self.AUTH
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "spawned"
        assert data["thread_id"] == "111222333"
        assert data["attachments_saved"] == 0

    @pytest.mark.asyncio
    async def test_ingest_missing_content_returns_400(self, ingest_client: TestClient) -> None:
        resp = await ingest_client.post("/api/ingest", json={}, headers=self.AUTH)
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_ingest_accepts_prompt_alias(
        self, ingest_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await ingest_client.post(
            "/api/ingest", json={"prompt": "Via alias"}, headers=self.AUTH
        )
        assert resp.status == 201
        _channel, prompt = mock_cog.spawn_session.call_args.args
        assert prompt == "Via alias"

    @pytest.mark.asyncio
    async def test_ingest_saves_attachment_and_references_path(
        self, ingest_client: TestClient, mock_cog: MagicMock, tmp_path
    ) -> None:
        import base64

        payload = base64.b64encode(b"hello file").decode()
        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "See attached",
                "attachments": [{"filename": "report.txt", "data": payload}],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["attachments_saved"] == 1

        # File written under {working_dir}/ingest/**/report.txt with correct bytes
        matches = list(tmp_path.glob("ingest/*/report.txt"))
        assert len(matches) == 1
        assert matches[0].read_bytes() == b"hello file"

        # Saved path is referenced in the prompt passed to spawn_session
        _channel, prompt = mock_cog.spawn_session.call_args.args
        assert "report.txt" in prompt
        assert str(matches[0]) in prompt

    @pytest.mark.asyncio
    async def test_ingest_rejects_path_traversal_filename(
        self, ingest_client: TestClient, tmp_path
    ) -> None:
        import base64

        payload = base64.b64encode(b"x").decode()
        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "evil",
                "attachments": [{"filename": "../../etc/passwd", "data": payload}],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        # Nothing written outside the ingest dir; basename sanitised to "passwd"
        assert list(tmp_path.glob("ingest/*/passwd"))
        assert not list(tmp_path.glob("**/etc/passwd"))

    @pytest.mark.asyncio
    async def test_ingest_invalid_base64_returns_400(self, ingest_client: TestClient) -> None:
        resp = await ingest_client.post(
            "/api/ingest",
            json={"content": "x", "attachments": [{"filename": "a.bin", "data": "!!!notb64"}]},
            headers=self.AUTH,
        )
        assert resp.status == 400

    @staticmethod
    def _make_zip(members: dict[str, bytes]) -> str:
        """Build an in-memory zip from {arcname: bytes} and return base64."""
        import base64
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, blob in members.items():
                zf.writestr(name, blob)
        return base64.b64encode(buf.getvalue()).decode()

    @pytest.mark.asyncio
    async def test_ingest_extracts_zip_bundle_and_lists_extracted_files(
        self, ingest_client: TestClient, mock_cog: MagicMock, tmp_path
    ) -> None:
        zip_b64 = self._make_zip({"a.txt": b"alpha", "docs/b.md": b"# beta"})
        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "See bundle",
                "attachments": [{"filename": "bundle.zip", "data": zip_b64}],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201

        # Zip is expanded on disk; its members exist with correct bytes.
        a = list(tmp_path.glob("ingest/*/**/a.txt"))
        b = list(tmp_path.glob("ingest/*/**/b.md"))
        assert len(a) == 1 and a[0].read_bytes() == b"alpha"
        assert len(b) == 1 and b[0].read_bytes() == b"# beta"

        # The zip archive itself is removed after extraction.
        assert not list(tmp_path.glob("ingest/*/bundle.zip"))

        # Prompt references the extracted files (paths only), not the zip name.
        _channel, prompt = mock_cog.spawn_session.call_args.args
        assert "a.txt" in prompt
        assert "b.md" in prompt
        assert "bundle.zip" not in prompt

    @pytest.mark.asyncio
    async def test_ingest_zip_extraction_blocks_zip_slip(
        self, ingest_client: TestClient, tmp_path
    ) -> None:
        zip_b64 = self._make_zip({"../../evil.txt": b"pwned", "ok.txt": b"safe"})
        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "evil zip",
                "attachments": [{"filename": "bundle.zip", "data": zip_b64}],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        # Nothing escapes the ingest dir.
        assert not list(tmp_path.glob("**/evil.txt"))

    @pytest.mark.asyncio
    async def test_two_attachments_with_the_same_name_both_survive(
        self, ingest_client: TestClient, tmp_path
    ) -> None:
        # Teams names every pasted screenshot "image.png". Writing both to the
        # same path left one file where two were sent, and the saved count still
        # said 2 — a loss indistinguishable from success.
        import base64

        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "two shots",
                "attachments": [
                    {"filename": "image.png", "data": base64.b64encode(b"first").decode()},
                    {"filename": "image.png", "data": base64.b64encode(b"second").decode()},
                ],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        files = sorted(p.read_bytes() for p in tmp_path.glob("ingest/*/image*.png"))
        assert files == [b"first", b"second"]

    @pytest.mark.asyncio
    async def test_zip_members_with_the_same_name_both_survive(
        self, ingest_client: TestClient, tmp_path
    ) -> None:
        import base64
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("image.png", b"first")
            zf.writestr("image.png", b"second")
        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "dup members",
                "attachments": [
                    {
                        "filename": "b.zip",
                        "data": base64.b64encode(buf.getvalue()).decode(),
                    }
                ],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        files = sorted(p.read_bytes() for p in tmp_path.glob("ingest/*/**/image*.png"))
        assert files == [b"first", b"second"]

    def test_unique_path_refuses_a_path_outside_the_ingest_root(
        self, repo: NotificationRepository, bot_with_text_channel: MagicMock, tmp_path
    ) -> None:
        # Containment is re-established at the filesystem call itself, not only
        # by the basename sanitiser far upstream.
        from pathlib import Path

        api = ApiServer(repo=repo, bot=bot_with_text_channel, working_dir=str(tmp_path))
        assert api._unique_path(Path("/etc/passwd")) is None
        assert api._unique_path(tmp_path / "ingest" / ".." / ".." / "escape.txt") is None
        inside = api._unique_path(tmp_path / "ingest" / "req" / "ok.txt")
        assert inside is not None and str(inside).startswith(str((tmp_path / "ingest").resolve()))

    def test_unique_path_walks_past_existing_collisions(
        self, repo: NotificationRepository, bot_with_text_channel: MagicMock, tmp_path
    ) -> None:
        # The disambiguation loop is what stops a same-named attachment from
        # overwriting an earlier one, so it has to keep stepping past every name
        # already taken — not just the first.
        api = ApiServer(repo=repo, bot=bot_with_text_channel, working_dir=str(tmp_path))
        dest = tmp_path / "ingest" / "req"
        dest.mkdir(parents=True)
        (dest / "image.png").write_bytes(b"a")
        (dest / "image_2.png").write_bytes(b"b")
        got = api._unique_path(dest / "image.png")
        assert got is not None and got.name == "image_3.png"

    def test_unique_path_refuses_rather_than_inventing_a_name_when_exhausted(
        self, repo: NotificationRepository, bot_with_text_channel: MagicMock, tmp_path, monkeypatch
    ) -> None:
        # Every candidate taken → refuse (the caller turns this into a 400)
        # rather than fall back to a random name nothing else can predict.
        api = ApiServer(repo=repo, bot=bot_with_text_channel, working_dir=str(tmp_path))
        (tmp_path / "ingest").mkdir()
        monkeypatch.setattr(os.path, "exists", lambda _p: True)
        assert api._unique_path(tmp_path / "ingest" / "image.png") is None

    def test_contained_path_rejects_a_sibling_with_the_root_as_a_name_prefix(
        self, repo: NotificationRepository, bot_with_text_channel: MagicMock, tmp_path
    ) -> None:
        # "/…/ingest-evil" starts with "/…/ingest" as a *string* but is not
        # inside it. The guard compares against root + os.sep for this reason.
        api = ApiServer(repo=repo, bot=bot_with_text_channel, working_dir=str(tmp_path))
        (tmp_path / "ingest").mkdir()
        sibling = tmp_path / "ingest-evil"
        sibling.mkdir()
        assert api._contained_path(sibling / "loot.txt") is None
        assert api._contained_path(tmp_path / "ingest" / "ok.txt") is not None

    @pytest.mark.asyncio
    async def test_manifest_shortfall_warns_the_session_and_the_caller(
        self, ingest_client: TestClient, mock_cog: MagicMock, tmp_path
    ) -> None:
        import base64

        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "See attached",
                "attachments": [
                    {"filename": "shot.png", "data": base64.b64encode(b"png").decode()}
                ],
                "attachments_manifest": [
                    {"name": "shot.png", "message": "返信 1"},
                    {
                        "name": "MEHJdebug.log",
                        "status": "linked",
                        "message": "返信 2",
                        "reason": "SharePoint download failed",
                    },
                ],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        data = await resp.json()

        # The caller — the only party that can re-send — is told what is missing.
        assert data["attachments"]["verified"] is True
        assert data["attachments"]["complete"] is False
        assert data["attachments"]["not_delivered"][0]["name"] == "MEHJdebug.log"

        # The session is told before it is asked to do anything.
        _channel, prompt = mock_cog.spawn_session.call_args.args
        assert "MEHJdebug.log" in prompt
        assert "返信 2" in prompt
        assert prompt.index("⚠️") < prompt.index("See attached")

        # And the ledger is written next to the files.
        report = list(tmp_path.glob("ingest/*/ATTACHMENTS-REPORT.md"))
        assert len(report) == 1
        assert "MEHJdebug.log" in report[0].read_text(encoding="utf-8")

    @pytest.mark.asyncio
    async def test_complete_manifest_adds_no_warning(
        self, ingest_client: TestClient, mock_cog: MagicMock
    ) -> None:
        import base64

        resp = await ingest_client.post(
            "/api/ingest",
            json={
                "content": "See attached",
                "attachments": [
                    {"filename": "shot.png", "data": base64.b64encode(b"png").decode()}
                ],
                "attachments_manifest": [{"name": "shot.png", "message": "返信 1"}],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        assert (await resp.json())["attachments"]["complete"] is True
        _channel, prompt = mock_cog.spawn_session.call_args.args
        assert "⚠️" not in prompt

    @pytest.mark.asyncio
    async def test_ingest_without_a_manifest_still_works_unverified(
        self, ingest_client: TestClient
    ) -> None:
        # Zero-Config: an older client that knows nothing about manifests must
        # keep working, and must not be reported as verified-complete.
        resp = await ingest_client.post(
            "/api/ingest", json={"content": "no manifest"}, headers=self.AUTH
        )
        assert resp.status == 201
        attachments = (await resp.json())["attachments"]
        assert attachments["verified"] is False
        assert attachments["complete"] is None

    @pytest.mark.asyncio
    async def test_malformed_manifest_returns_400(self, ingest_client: TestClient) -> None:
        resp = await ingest_client.post(
            "/api/ingest",
            json={"content": "x", "attachments_manifest": "not-a-list"},
            headers=self.AUTH,
        )
        assert resp.status == 400

    @pytest.mark.asyncio
    async def test_strict_mode_refuses_a_lossy_ingest(
        self,
        repo: NotificationRepository,
        bot_with_text_channel: MagicMock,
        mock_cog: MagicMock,
        tmp_path,
    ) -> None:
        api = ApiServer(
            repo=repo,
            bot=bot_with_text_channel,
            default_channel_id=12345,
            ingest_token=self.INGEST_TOKEN,
            working_dir=str(tmp_path),
            ingest_require_complete=True,
        )
        client = TestClient(TestServer(api.app))
        await client.start_server()
        try:
            resp = await client.post(
                "/api/ingest",
                json={
                    "content": "x",
                    "attachments_manifest": [{"name": "gone.log", "status": "failed"}],
                },
                headers=self.AUTH,
            )
            assert resp.status == 409
            assert (await resp.json())["attachments"]["not_delivered"][0]["name"] == "gone.log"
            # No session was started on partial evidence.
            mock_cog.spawn_session.assert_not_called()
        finally:
            await client.close()


class TestIngestResult:
    """Tests for /api/ingest result capture + GET /api/ingest/{result_id}."""

    INGEST_TOKEN = "ingest-secret-xyz"
    AUTH = {"Authorization": f"Bearer {INGEST_TOKEN}"}

    @pytest.fixture
    def mock_cog(self) -> MagicMock:
        thread = MagicMock()
        thread.id = 111222333
        thread.name = "Ingested thread"
        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=thread)
        return cog

    @pytest.fixture
    def bot_with_text_channel(self, mock_cog: MagicMock) -> MagicMock:
        import discord

        b = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        b.get_channel.return_value = channel
        b.cogs = {"ClaudeChatCog": mock_cog}
        return b

    @pytest.fixture
    async def ingest_repo(self):
        from claude_discord.database.ingest_repo import IngestResultRepository

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        r = IngestResultRepository(path)
        await r.init_db()
        yield r
        os.unlink(path)

    @pytest.fixture
    async def result_client(
        self,
        repo: NotificationRepository,
        bot_with_text_channel: MagicMock,
        ingest_repo,
        tmp_path,
    ) -> TestClient:
        api = ApiServer(
            repo=repo,
            bot=bot_with_text_channel,
            default_channel_id=12345,
            ingest_token=self.INGEST_TOKEN,
            working_dir=str(tmp_path),
            ingest_repo=ingest_repo,
        )
        server = TestServer(api.app)
        client = TestClient(server)
        client._api = api  # type: ignore[attr-defined]
        await client.start_server()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_post_returns_result_id_when_repo_configured(
        self, result_client: TestClient
    ) -> None:
        resp = await result_client.post("/api/ingest", json={"content": "hello"}, headers=self.AUTH)
        assert resp.status == 201
        data = await resp.json()
        assert "result_id" in data
        assert len(data["result_id"]) > 0

    @pytest.mark.asyncio
    async def test_get_result_running_then_done(
        self, result_client: TestClient, ingest_repo
    ) -> None:
        resp = await result_client.post("/api/ingest", json={"content": "hello"}, headers=self.AUTH)
        result_id = (await resp.json())["result_id"]

        # Immediately after spawn, the result is still being produced.
        poll = await result_client.get(f"/api/ingest/{result_id}", headers=self.AUTH)
        assert poll.status == 200
        running = await poll.json()
        assert running["status"] == "running"
        assert running["thread_id"] == "111222333"

        # Simulate the session finishing and firing the result sink.
        await ingest_repo.set_result(result_id, "the generated answer")

        poll2 = await result_client.get(f"/api/ingest/{result_id}", headers=self.AUTH)
        done = await poll2.json()
        assert done["status"] == "done"
        assert done["result"] == "the generated answer"

    @pytest.mark.asyncio
    async def test_sink_passed_to_spawn_writes_result(
        self, result_client: TestClient, mock_cog: MagicMock, ingest_repo
    ) -> None:
        resp = await result_client.post("/api/ingest", json={"content": "hello"}, headers=self.AUTH)
        result_id = (await resp.json())["result_id"]

        # The handler must pass a result_sink to spawn_session.
        sink = mock_cog.spawn_session.call_args.kwargs["result_sink"]
        assert sink is not None

        # Invoking the sink (as the real session would) persists the answer.
        await sink("answer via sink", None)
        rec = await ingest_repo.get(result_id)
        assert rec["status"] == "done"
        assert rec["result"] == "answer via sink"

        # Error path routes to set_error.
        await sink(None, "kaboom")
        rec2 = await ingest_repo.get(result_id)
        assert rec2["status"] == "error"
        assert rec2["error"] == "kaboom"

    @pytest.mark.asyncio
    async def test_sink_attaches_answer_markdown_to_thread(
        self, result_client: TestClient, mock_cog: MagicMock, ingest_repo
    ) -> None:
        resp = await result_client.post("/api/ingest", json={"content": "hello"}, headers=self.AUTH)
        result_id = (await resp.json())["result_id"]
        sink = mock_cog.spawn_session.call_args.kwargs["result_sink"]

        with patch(
            "claude_discord.ext.api_server.send_file_blobs",
            new_callable=AsyncMock,
        ) as send_file_blobs:
            await sink("answer via sink", None)

        rec = await ingest_repo.get(result_id)
        assert rec["result"] == "answer via sink"
        send_file_blobs.assert_awaited_once()
        thread, blobs = send_file_blobs.await_args.args[:2]
        assert thread.id == 111222333
        assert blobs == [("ccdb-answer.md", b"answer via sink")]

    @pytest.mark.asyncio
    async def test_sink_does_not_attach_on_error(
        self, result_client: TestClient, mock_cog: MagicMock
    ) -> None:
        resp = await result_client.post("/api/ingest", json={"content": "hello"}, headers=self.AUTH)
        assert resp.status == 201
        sink = mock_cog.spawn_session.call_args.kwargs["result_sink"]

        with patch(
            "claude_discord.ext.api_server.send_file_blobs",
            new_callable=AsyncMock,
        ) as send_file_blobs:
            await sink(None, "kaboom")

        send_file_blobs.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_unknown_result_404(self, result_client: TestClient) -> None:
        resp = await result_client.get("/api/ingest/does-not-exist", headers=self.AUTH)
        assert resp.status == 404

    @pytest.mark.asyncio
    async def test_get_result_requires_auth(self, result_client: TestClient) -> None:
        resp = await result_client.get("/api/ingest/anything")
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_get_result_wrong_token_401(self, result_client: TestClient) -> None:
        resp = await result_client.get(
            "/api/ingest/anything", headers={"Authorization": "Bearer nope"}
        )
        assert resp.status == 401

    @pytest.mark.asyncio
    async def test_no_result_id_without_repo(
        self,
        repo: NotificationRepository,
        bot_with_text_channel: MagicMock,
        tmp_path,
    ) -> None:
        # No ingest_repo wired → endpoint still works, just no result retrieval.
        api = ApiServer(
            repo=repo,
            bot=bot_with_text_channel,
            default_channel_id=12345,
            ingest_token=self.INGEST_TOKEN,
            working_dir=str(tmp_path),
        )
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/ingest", json={"content": "hello"}, headers=self.AUTH)
            data = await resp.json()
            assert "result_id" not in data
            # GET returns 503 when retrieval isn't configured.
            poll = await client.get("/api/ingest/whatever", headers=self.AUTH)
            assert poll.status == 503
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_ingest_pings_and_joins_owner(
        self, repo: NotificationRepository, ingest_repo, tmp_path
    ) -> None:
        """An ingest session auto-joins + @mentions the bot owner on start, and
        pings them again on completion so a long-running result is delivered
        asynchronously over Discord (no foreground poller needed)."""
        import discord

        thread = MagicMock()
        thread.id = 111222333
        thread.name = "MEHJ thread"
        thread.send = AsyncMock()
        thread.add_user = AsyncMock()

        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=thread)

        bot = MagicMock()
        bot.owner_id = 999  # configured owner → should be mentioned/added
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        bot.get_channel.return_value = channel
        bot.cogs = {"ClaudeChatCog": cog}

        api = ApiServer(
            repo=repo,
            bot=bot,
            default_channel_id=12345,
            ingest_token=self.INGEST_TOKEN,
            working_dir=str(tmp_path),
            ingest_repo=ingest_repo,
        )
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/ingest", json={"content": "hi"}, headers=self.AUTH)
            assert resp.status == 201

            # Owner auto-joined the thread.
            thread.add_user.assert_awaited()

            # Start ping mentions the owner.
            contents = [c.kwargs.get("content", "") for c in thread.send.call_args_list]
            assert any("<@999>" in s and "開始" in s for s in contents)

            # Completion: invoking the captured sink pings the owner again.
            sink = cog.spawn_session.call_args.kwargs["result_sink"]
            await sink("the answer", None)
            contents = [c.kwargs.get("content", "") for c in thread.send.call_args_list]
            assert any("<@999>" in s and "回答ができました" in s for s in contents)
        finally:
            await client.close()


class TestBinaryAttachmentMisdetectedAsZip:
    """A binary attachment must survive being mistaken for a zip archive.

    ``zipfile.is_zipfile()`` looks for the end-of-central-directory signature
    (``PK\\x05\\x06``) near the end of the file — it does not require the file to
    *start* like a zip. Any binary can contain those four bytes by chance, and
    Windows event logs (.evtx), memory dumps and packet captures are exactly the
    kind of large opaque blobs where that happens.

    When it did, the expander treated the file as an archive, "extracted" its
    zero members, and then deleted the original — destroying the attachment and
    reporting ``attachments_saved: 0``. Reproduced against the live endpoint
    with a real 1.1 MB Admin.evtx before this was fixed.
    """

    INGEST_TOKEN = "ingest-secret-xyz"
    AUTH = {"Authorization": f"Bearer {INGEST_TOKEN}"}

    @pytest.fixture
    def mock_cog(self) -> MagicMock:
        thread = MagicMock()
        thread.id = 111222333
        thread.name = "Ingested thread"
        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=thread)
        return cog

    @pytest.fixture
    def bot_with_text_channel(self, mock_cog: MagicMock) -> MagicMock:
        import discord

        b = MagicMock()
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        b.get_channel.return_value = channel
        b.cogs = {"ClaudeChatCog": mock_cog}
        return b

    @pytest.fixture
    async def client(
        self, repo: NotificationRepository, bot_with_text_channel: MagicMock, tmp_path
    ) -> TestClient:
        api = ApiServer(
            repo=repo,
            bot=bot_with_text_channel,
            default_channel_id=12345,
            ingest_token=self.INGEST_TOKEN,
            working_dir=str(tmp_path),
        )
        c = TestClient(TestServer(api.app))
        await c.start_server()
        yield c
        await c.close()

    @staticmethod
    def _evtx_containing_eocd() -> bytes:
        """A .evtx-shaped blob ending in a well-formed, empty EOCD record.

        Built deterministically rather than by scribbling the signature into
        random bytes: ``is_zipfile`` also validates the trailing comment-length
        field, so a random blob only trips it some of the time and the test
        would be flaky. This is the worst realistic case — a binary that any
        zip reader agrees is an archive containing nothing.
        """
        import struct

        eocd = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0, 0, 0, 0, 0)
        return b"ElfFile\x00" + bytes(200_000) + eocd

    @pytest.mark.asyncio
    async def test_binary_that_looks_like_a_zip_is_not_deleted(
        self, client: TestClient, tmp_path
    ) -> None:
        import base64
        import hashlib

        raw = self._evtx_containing_eocd()
        resp = await client.post(
            "/api/ingest",
            json={
                "content": "see log",
                "attachments": [{"filename": "Admin.evtx", "data": base64.b64encode(raw).decode()}],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        assert (await resp.json())["attachments_saved"] == 1

        found = list(tmp_path.glob("ingest/*/Admin.evtx"))
        assert len(found) == 1, "the attachment must still exist on disk"
        assert hashlib.sha256(found[0].read_bytes()).hexdigest() == (
            hashlib.sha256(raw).hexdigest()
        ), "and must be byte-for-byte intact"

    @pytest.mark.asyncio
    async def test_a_genuinely_empty_zip_is_kept_rather_than_deleted(
        self, client: TestClient, tmp_path
    ) -> None:
        # Nothing to replace it with, so removing it would leave the session
        # with strictly less than it was sent.
        import base64
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w"):
            pass
        resp = await client.post(
            "/api/ingest",
            json={
                "content": "empty bundle",
                "attachments": [
                    {"filename": "b.zip", "data": base64.b64encode(buf.getvalue()).decode()}
                ],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        assert list(tmp_path.glob("ingest/*/b.zip"))

    @pytest.mark.asyncio
    async def test_a_real_bundle_is_still_expanded_and_the_zip_removed(
        self, client: TestClient, tmp_path
    ) -> None:
        # The behaviour that must not regress.
        import base64
        import io
        import zipfile as zf

        buf = io.BytesIO()
        with zf.ZipFile(buf, "w") as z:
            z.writestr("Admin.evtx", b"ElfFile\x00payload")
        resp = await client.post(
            "/api/ingest",
            json={
                "content": "bundle",
                "attachments": [
                    {
                        "filename": "teams-attachments.zip",
                        "data": base64.b64encode(buf.getvalue()).decode(),
                    }
                ],
            },
            headers=self.AUTH,
        )
        assert resp.status == 201
        member = list(tmp_path.glob("ingest/*/**/Admin.evtx"))
        assert len(member) == 1
        assert member[0].read_bytes() == b"ElfFile\x00payload"
        assert not list(tmp_path.glob("ingest/*/teams-attachments.zip"))


class TestCloseSession:
    """POST /api/threads/{id}/close — a session ends itself when told to.

    The authority is the person who asked in the thread, passed as ``actor``;
    the service refuses anything else, so "the model decided it was done" is
    still not closeable.
    """

    @pytest.fixture
    def lifecycle(self) -> MagicMock:
        from claude_discord.session_lifecycle import CloseOutcome, CloseState

        service = MagicMock()
        service.close = AsyncMock(
            return_value=CloseOutcome(state=CloseState.PENDING, record=MagicMock())
        )
        return service

    @pytest.fixture
    async def closing_client(self, repo, bot: MagicMock, lifecycle: MagicMock) -> TestClient:
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        api.lifecycle = lifecycle
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()

    async def test_close_carries_the_user_instruction_authority(self, closing_client, lifecycle):
        from claude_code_core.session_repo import CloseAuthority

        response = await closing_client.post(
            "/api/threads/555/close", json={"actor": 488763953397235712}
        )
        assert response.status == 200
        body = await response.json()
        assert body["state"] == "pending"
        thread_id, authorization = lifecycle.close.await_args.args
        assert thread_id == 555
        assert authorization.source is CloseAuthority.USER_INSTRUCTION
        assert authorization.actor == "488763953397235712"

    async def test_a_close_without_an_actor_is_refused(self, closing_client, lifecycle):
        response = await closing_client.post("/api/threads/555/close", json={})
        assert response.status == 400
        lifecycle.close.assert_not_awaited()

    async def test_an_unknown_thread_is_a_404(self, closing_client, lifecycle):
        from claude_discord.session_lifecycle import CloseOutcome, CloseState

        lifecycle.close.return_value = CloseOutcome(state=CloseState.NO_SESSION)
        response = await closing_client.post("/api/threads/555/close", json={"actor": "42"})
        assert response.status == 404

    async def test_without_the_service_the_endpoint_says_so(self, client):
        response = await client.post("/api/threads/555/close", json={"actor": "42"})
        assert response.status == 503


class TestProjectLookupWorkerClose:
    """The lookup worker is a helper thread: its row closes, and it never wakes a closed origin."""

    @pytest.mark.asyncio
    async def test_result_skips_archived_requester_and_closes_worker_row(
        self, repo: NotificationRepository, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from claude_discord.session_lifecycle import CloseState, SessionLifecycleService

        monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(tmp_path))
        worker_thread = MagicMock()
        worker_thread.id = 444
        worker_thread.name = "lookup"
        worker_thread.edit = AsyncMock()
        origin_thread = MagicMock()
        origin_thread.id = 111
        origin_thread.archived = True
        origin_thread.send = AsyncMock()
        cog = MagicMock()
        cog.spawn_session = AsyncMock(return_value=worker_thread)
        channel = MagicMock(spec=discord.TextChannel)
        bot = MagicMock()
        bot.cogs = {"ClaudeChatCog": cog}
        bot.get_channel.side_effect = lambda cid: {12345: channel, 111: origin_thread}.get(cid)
        lifecycle = MagicMock(spec=SessionLifecycleService)
        lifecycle.close = AsyncMock(
            return_value=SimpleNamespace(
                state=CloseState.CLOSED,
                record=SimpleNamespace(is_closed=True, archive_pending=False),
                archived=True,
            )
        )
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        api.lifecycle = lifecycle
        client = TestClient(TestServer(api.app))
        await client.start_server()
        try:
            resp = await client.post(
                "/api/project-lookup", json={"text": "find it", "from_thread": 111}
            )
            assert resp.status == 201
            sink = cog.spawn_session.await_args.kwargs["result_sink"]
            await sink("Found it.", None)
        finally:
            await client.close()

        origin_thread.send.assert_not_awaited()
        lifecycle.close.assert_awaited_once()
        closed_id, authorization = lifecycle.close.await_args.args
        assert closed_id == 444
        assert authorization.is_workflow
        worker_thread.edit.assert_not_awaited()
