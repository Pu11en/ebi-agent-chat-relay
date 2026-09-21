"""Tests for the Discord side of trusted agent handoffs: rendering, the Cog, reconnect."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claude_code_core.handoffs import protocol as p
from claude_discord.handoff_discord import (
    DISCORD_MESSAGE_LIMIT,
    ensure_job_thread,
    job_thread_name,
    parse_event_message,
    post_task_starter,
    render_event_message,
    render_task_starter,
    short_task_id,
)
from claude_discord.handoff_messages import HandoffEnvelopeError, format_handoff_message

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
GUILD = 1001
CHANNEL = 2002
TASK_ID = "6d9f6ad0-3c6e-4a1e-9f4a-2f2a1c0b7e11"
EVENT_ID = "0f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d42"
EVENT_ID_2 = "1f2a5c31-9b7e-4d2a-8c11-77aa3e5b1d43"


def make_task(
    sender: str = "drewai",
    recipient: str = "david",
    *,
    goal: str = "Find the visual picker skill",
    findings: tuple[str, ...] = (),
    authority: p.AuthorityScope | None = None,
    task_id: str = TASK_ID,
) -> p.HandoffTask:
    origin = p.ConversationCoordinate(guild_id=GUILD, channel_id=5005, thread_id=6006, message_id=7)
    return p.HandoffTask(
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        origin=origin,
        origin_human_id="777",
        project=p.ProjectLocator(owner="drew", folder="main-projects"),
        goal=goal,
        authority=authority or p.AuthorityScope(read=True),
        expected_result="Exact paths and a short summary.",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=6),
        findings=findings,
    )


def make_task_event(task: p.HandoffTask | None = None, event_id: str = EVENT_ID) -> p.HandoffEvent:
    task = task or make_task()
    return p.HandoffEvent(
        event_id=event_id,
        kind=p.HandoffEventKind.TASK,
        task_id=task.task_id,
        sender=task.sender,
        recipient=task.recipient,
        sequence=0,
        created_at=NOW,
        task=task,
    )


def make_event(
    kind: p.HandoffEventKind,
    *,
    sender: str = "david",
    recipient: str = "drewai",
    sequence: int = 1,
    payload: dict[str, str | int | bool] | None = None,
    event_id: str = EVENT_ID_2,
    task_id: str = TASK_ID,
) -> p.HandoffEvent:
    return p.HandoffEvent(
        event_id=event_id,
        kind=kind,
        task_id=task_id,
        sender=sender,
        recipient=recipient,
        sequence=sequence,
        created_at=NOW,
        payload=payload or {},
    )


class TestRendering:
    def test_short_id_and_thread_name(self) -> None:
        assert short_task_id(TASK_ID) == "6d9f6ad0"
        assert job_thread_name(TASK_ID) == "handoff-6d9f6ad0"

    def test_task_starter_is_human_readable_and_parseable(self) -> None:
        event = make_task_event()
        text = render_task_starter(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert "6d9f6ad0" in text
        assert "drewai → david" in text
        assert "drew/main-projects" in text
        assert "Find the visual picker skill" in text
        assert "read-only" in text
        assert parse_event_message(text) == event

    def test_starter_stays_within_discord_bounds_for_a_large_goal(self) -> None:
        goal = "x" * 1000
        event = make_task_event(make_task(goal=goal))
        text = render_task_starter(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert parse_event_message(text) == event

    def test_starter_never_emits_a_transcript(self) -> None:
        # The starter carries the packet fields and nothing else: a packet
        # that would only fit if the human text were dropped is refused.
        findings = tuple("f" * p.MAX_FINDING_CHARS for _ in range(p.MAX_FINDINGS))
        event = make_task_event(make_task(findings=findings))
        with pytest.raises(HandoffEnvelopeError):
            render_task_starter(event)

    @pytest.mark.parametrize(
        ("kind", "payload", "needle"),
        [
            (p.HandoffEventKind.ACK, {}, "accepted"),
            (p.HandoffEventKind.STATE, {"state": "queued", "note": "waiting"}, "queued"),
            (p.HandoffEventKind.STATE, {"state": "blocked", "note": "needs edit"}, "blocked"),
            (p.HandoffEventKind.QUESTION, {"question": "Which folder?"}, "Which folder?"),
            (p.HandoffEventKind.ANSWER, {"answer": "the second one"}, "the second one"),
            (p.HandoffEventKind.RESULT, {"outcome": "completed", "summary": "Found it"}, "Found"),
            (p.HandoffEventKind.RESULT, {"outcome": "failed", "summary": "No such dir"}, "failed"),
        ],
    )
    def test_typed_events_round_trip_with_a_human_line(
        self, kind: p.HandoffEventKind, payload: dict[str, str | int | bool], needle: str
    ) -> None:
        event = make_event(kind, payload=payload)
        text = render_event_message(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert needle in text
        assert parse_event_message(text) == event

    def test_result_summary_is_bounded_in_the_human_line(self) -> None:
        summary = "s" * p.MAX_PAYLOAD_VALUE_CHARS
        event = make_event(
            p.HandoffEventKind.RESULT, payload={"outcome": "completed", "summary": summary}
        )
        text = render_event_message(event)
        assert len(text) <= DISCORD_MESSAGE_LIMIT
        assert parse_event_message(text) == event

    def test_parse_accepts_the_strict_envelope_and_ignores_chatter(self) -> None:
        event = make_task_event()
        assert parse_event_message(format_handoff_message(event)) == event
        assert parse_event_message("hello there") is None
        assert parse_event_message("") is None

    def test_parse_rejects_two_markers_or_a_broken_fence(self) -> None:
        event = make_task_event()
        good = render_event_message(event)
        with pytest.raises(HandoffEnvelopeError):
            parse_event_message(good + "\n" + good)
        with pytest.raises(HandoffEnvelopeError):
            parse_event_message("CCDB_HANDOFF_V1\nnot a fence")


class FakeThread:
    def __init__(self, thread_id: int, name: str, *, archived: bool = False) -> None:
        self.id = thread_id
        self.name = name
        self.archived = archived
        self.parent_id = CHANNEL
        self.sent: list[str] = []
        self.edits: list[dict[str, object]] = []

    async def send(self, content: str) -> SimpleNamespace:
        self.sent.append(content)
        return SimpleNamespace(id=len(self.sent), content=content)

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)
        if kwargs.get("archived") is False:
            self.archived = False


class FakeStarterMessage:
    def __init__(self, message_id: int, *, existing: FakeThread | None = None) -> None:
        self.id = message_id
        self.thread = existing
        self.create_calls = 0

    async def create_thread(self, *, name: str, **_: object) -> FakeThread:
        self.create_calls += 1
        if self.thread is not None:
            raise RuntimeError("thread already exists")
        self.thread = FakeThread(self.id, name)
        return self.thread


class TestJobThread:
    @pytest.mark.asyncio
    async def test_post_task_starter_posts_once_and_opens_one_thread(self) -> None:
        posted: list[str] = []

        async def send(content: str) -> FakeStarterMessage:
            posted.append(content)
            return FakeStarterMessage(4242)

        channel = SimpleNamespace(id=CHANNEL, send=send)
        event = make_task_event()
        starter, thread = await post_task_starter(channel, event)
        assert posted == [render_task_starter(event)]
        assert starter.id == 4242
        assert thread.id == 4242
        assert thread.name == job_thread_name(TASK_ID)

    @pytest.mark.asyncio
    async def test_ensure_job_thread_reuses_an_existing_thread(self) -> None:
        existing = FakeThread(4242, job_thread_name(TASK_ID), archived=True)
        starter = FakeStarterMessage(4242, existing=existing)
        thread = await ensure_job_thread(starter, TASK_ID)
        assert thread is existing
        assert starter.create_calls == 0
        # An archived thread is reopened so the acknowledgement is visible.
        assert existing.archived is False

    @pytest.mark.asyncio
    async def test_ensure_job_thread_falls_back_to_fetch_when_create_races(self) -> None:
        existing = FakeThread(4242, job_thread_name(TASK_ID))
        starter = FakeStarterMessage(4242)
        starter.thread = None

        async def create_thread(**_: object) -> FakeThread:
            raise RuntimeError("already has a thread")

        starter.create_thread = create_thread  # type: ignore[method-assign]
        fetch = AsyncMock(return_value=existing)
        thread = await ensure_job_thread(starter, TASK_ID, fetch_channel=fetch)
        assert thread is existing
        fetch.assert_awaited_once_with(4242)


class TestChatCogHandoffTurn:
    @pytest.mark.asyncio
    async def test_run_handoff_turn_pins_dir_and_harness_then_runs_in_the_thread(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        thread = FakeThread(4242, "handoff-6d9f6ad0")
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append({"seed": seed, "thread": th, "prompt": prompt, **kw})

        settings = SimpleNamespace(set_backend=AsyncMock(), set_model=AsyncMock())
        cog = SimpleNamespace(
            repo=SimpleNamespace(ensure_working_dir=AsyncMock(), get=AsyncMock(return_value=None)),
            _backend_settings=settings,
            _run_claude=_run_claude,
            _session_id_for_current_backend=AsyncMock(),
        )
        sink = AsyncMock()

        await ClaudeChatCog.run_handoff_turn(
            cog,  # type: ignore[arg-type]
            thread,
            "do the thing",
            working_dir="/srv/proj",
            result_sink=sink,
            backend="claude",
            model="haiku",
        )
        await asyncio.sleep(0)

        assert thread.sent and "Handoff turn" in thread.sent[0]
        cog.repo.ensure_working_dir.assert_awaited_once_with(4242, "/srv/proj")
        settings.set_backend.assert_awaited_once_with("claude", thread_id=4242)
        settings.set_model.assert_awaited_once_with("claude", "haiku", thread_id=4242)
        assert len(run_calls) == 1
        assert run_calls[0]["thread"] is thread
        assert run_calls[0]["session_id"] is None
        assert run_calls[0]["result_sink"] is sink
        assert run_calls[0]["lounge"] is False

    @pytest.mark.asyncio
    async def test_run_handoff_turn_resume_uses_the_threads_session(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        thread = FakeThread(5150, "existing")
        run_calls: list[dict[str, object]] = []

        async def _run_claude(seed: object, th: object, prompt: str, **kw: object) -> None:
            run_calls.append(kw)

        record = SimpleNamespace(session_id="abc-123")
        cog = SimpleNamespace(
            repo=SimpleNamespace(
                ensure_working_dir=AsyncMock(), get=AsyncMock(return_value=record)
            ),
            _backend_settings=None,
            _run_claude=_run_claude,
            _session_id_for_current_backend=AsyncMock(return_value="abc-123"),
        )

        await ClaudeChatCog.run_handoff_turn(
            cog,  # type: ignore[arg-type]
            thread,
            "continue",
            working_dir=None,
            result_sink=AsyncMock(),
            resume=True,
        )
        await asyncio.sleep(0)

        assert run_calls[0]["session_id"] == "abc-123"

    def test_capacity_follows_the_concurrency_limit(self) -> None:
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        cog = SimpleNamespace(active_session_count=2, _max_concurrent=3)
        assert ClaudeChatCog.handoff_capacity_available(cog) is True  # type: ignore[arg-type]
        cog.active_session_count = 3
        assert ClaudeChatCog.handoff_capacity_available(cog) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The dedicated listener (task 2.3)
# ---------------------------------------------------------------------------

import os  # noqa: E402
import tempfile  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from pathlib import Path  # noqa: E402

from claude_code_core.handoffs.state import HandoffState  # noqa: E402
from claude_discord.cogs.agent_handoff import AgentHandoffCog  # noqa: E402
from claude_discord.database.handoff_repo import HandoffRepository  # noqa: E402
from claude_discord.database.models import init_db  # noqa: E402
from claude_discord.handoff_authority import RecipientPolicy  # noqa: E402
from claude_discord.handoff_config import HandoffConfig  # noqa: E402
from claude_discord.handoff_executor import HandoffExecutor  # noqa: E402
from claude_discord.handoff_projects import ApprovedRootResolver  # noqa: E402

DREWAI_BOT, IMAC_BOT, DAVID_BOT = 111, 222, 333
CONFIG_ENV = {
    "CCDB_AGENT_ID": "david",
    "CCDB_HANDOFF_GUILD_ID": str(GUILD),
    "CCDB_HANDOFF_CHANNEL_ID": str(CHANNEL),
    "CCDB_HANDOFF_AGENTS": f"drewai={DREWAI_BOT},imac={IMAC_BOT},david={DAVID_BOT}",
}


@pytest.fixture
async def repo() -> AsyncIterator[HandoffRepository]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    try:
        yield HandoffRepository(path)
    finally:
        os.unlink(path)


class FakeGuild:
    def __init__(self, guild_id: int = GUILD) -> None:
        self.id = guild_id


class FakeChannel:
    """The agent-handoffs channel: starters live here, threads hang off them."""

    def __init__(self, channel_id: int = CHANNEL) -> None:
        self.id = channel_id
        self.parent_id = None
        self.guild = FakeGuild()
        self.messages: list[FakeMessage] = []
        self.threads: dict[int, FakeThread] = {}
        self._next_id = 9000

    async def send(self, content: str) -> FakeMessage:
        self._next_id += 1
        message = FakeMessage(
            self._next_id, content, author_id=DAVID_BOT, channel=self, guild=self.guild
        )
        self.messages.append(message)
        return message

    def history(self, *, limit: int = 100, after: object = None, oldest_first: bool = True):
        async def _iter():
            for message in self.messages[-limit:]:
                yield message

        return _iter()


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        content: str,
        *,
        author_id: int,
        channel: object,
        guild: object,
        bot: bool = True,
        webhook_id: int | None = None,
    ) -> None:
        self.id = message_id
        self.content = content
        self.author = SimpleNamespace(id=author_id, bot=bot)
        self.channel = channel
        self.guild = guild
        self.webhook_id = webhook_id
        self.thread: FakeThread | None = None

    async def create_thread(self, *, name: str, **_: object) -> FakeThread:
        if self.thread is not None:
            raise RuntimeError("already has a thread")
        self.thread = FakeThread(self.id, name)
        channel = self.channel
        if isinstance(channel, FakeChannel):
            channel.threads[self.id] = self.thread
        return self.thread


def _bot(channel: FakeChannel) -> SimpleNamespace:
    async def fetch_channel(channel_id: int) -> object:
        found = channel.threads.get(channel_id)
        if found is None:
            raise RuntimeError(f"no channel {channel_id}")
        return found

    def get_channel(channel_id: int) -> object | None:
        if channel_id == channel.id:
            return channel
        return channel.threads.get(channel_id)

    return SimpleNamespace(
        cogs={},
        get_channel=get_channel,
        fetch_channel=fetch_channel,
        user=SimpleNamespace(id=DAVID_BOT),
    )


def _starter(channel: FakeChannel, event: p.HandoffEvent, *, author_id: int = DREWAI_BOT):
    message = FakeMessage(
        4242, render_task_starter(event), author_id=author_id, channel=channel, guild=channel.guild
    )
    channel.messages.append(message)
    return message


def _cog(
    repo: HandoffRepository, channel: FakeChannel, project_root: Path, **kw: object
) -> AgentHandoffCog:
    config = HandoffConfig.from_env(CONFIG_ENV)
    assert config is not None
    bot = _bot(channel)
    executor = HandoffExecutor(
        repo=repo,
        local_agent_id="david",
        resolver=ApprovedRootResolver(roots={"drew": (project_root.parent,)}),
        policy=RecipientPolicy(),
        thread_lookup=bot.fetch_channel,
    )
    return AgentHandoffCog(
        bot,  # type: ignore[arg-type]
        repo=repo,
        config=config,
        executor=executor,
        start_loops=False,
        **kw,  # type: ignore[arg-type]
    )


class TestAgentHandoffCog:
    @pytest.mark.asyncio
    async def test_starter_from_a_trusted_peer_is_stored_acked_and_started(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        root = tmp_path / "drewp" / "main-projects"
        root.mkdir(parents=True)
        channel = FakeChannel()
        cog = _cog(repo, channel, root)
        chat = SimpleNamespace(
            bot=cog.bot,
            handoff_capacity_available=lambda: True,
            run_handoff_turn=AsyncMock(),
        )
        cog.bot.cogs["ClaudeChatCog"] = chat
        starter = _starter(channel, make_task_event())

        receipt = await cog.handle_message(starter, now=NOW)

        assert receipt is not None and receipt.created is True
        job = await repo.get_job(TASK_ID, "david")
        assert job is not None and job.state is HandoffState.RUNNING
        thread = channel.threads[4242]
        assert thread.name == job_thread_name(TASK_ID)
        assert await repo.get_job_thread(TASK_ID, "david") == 4242
        acks = [parse_event_message(t) for t in thread.sent]
        assert acks[0] is not None and acks[0].kind is p.HandoffEventKind.ACK
        chat.run_handoff_turn.assert_awaited_once()
        assert chat.run_handoff_turn.await_args.args[0] is thread

    @pytest.mark.asyncio
    async def test_redelivered_starter_reuses_the_job_and_thread(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        root = tmp_path / "drewp" / "main-projects"
        root.mkdir(parents=True)
        channel = FakeChannel()
        cog = _cog(repo, channel, root)
        chat = SimpleNamespace(
            bot=cog.bot, handoff_capacity_available=lambda: True, run_handoff_turn=AsyncMock()
        )
        cog.bot.cogs["ClaudeChatCog"] = chat
        starter = _starter(channel, make_task_event())

        first = await cog.handle_message(starter, now=NOW)
        second = await cog.handle_message(starter, now=NOW)

        assert first is not None and first.created
        assert second is not None and not second.created
        assert await repo.count_tasks() == 1
        assert len(channel.threads) == 1
        assert chat.run_handoff_turn.await_count == 1
        acks = [
            e
            for e in (parse_event_message(t) for t in channel.threads[4242].sent)
            if e is not None and e.kind is p.HandoffEventKind.ACK
        ]
        assert len(acks) == 1

    @pytest.mark.asyncio
    async def test_ordinary_bot_messages_start_nothing(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        cog = _cog(repo, channel, tmp_path)
        chatter = FakeMessage(
            1, "✅ build passed", author_id=DREWAI_BOT, channel=channel, guild=channel.guild
        )
        broken = FakeMessage(
            2,
            "CCDB_HANDOFF_V1\nnot a packet",
            author_id=DREWAI_BOT,
            channel=channel,
            guild=channel.guild,
        )
        assert await cog.handle_message(chatter) is None
        assert await cog.handle_message(broken) is None
        assert await repo.count_tasks() == 0

    @pytest.mark.asyncio
    async def test_untrusted_senders_and_places_are_refused(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        cog = _cog(repo, channel, tmp_path)
        event = make_task_event()
        elsewhere = FakeChannel(channel_id=CHANNEL + 1)
        cases = [
            _starter(channel, event, author_id=999),  # unmapped bot
            _starter(channel, event, author_id=IMAC_BOT),  # mapped, but not the packet's sender
            FakeMessage(
                5,
                render_task_starter(event),
                author_id=DREWAI_BOT,
                channel=elsewhere,
                guild=channel.guild,
            ),
            FakeMessage(
                6,
                render_task_starter(event),
                author_id=DREWAI_BOT,
                channel=channel,
                guild=channel.guild,
                webhook_id=77,
            ),
            FakeMessage(
                7,
                render_task_starter(event),
                author_id=DREWAI_BOT,
                channel=channel,
                guild=channel.guild,
                bot=False,
            ),
        ]
        for message in cases:
            assert await cog.handle_message(message) is None
        assert await repo.count_tasks() == 0
        assert channel.threads == {}

    @pytest.mark.asyncio
    async def test_a_task_for_another_recipient_is_ignored(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        cog = _cog(repo, channel, tmp_path)
        starter = _starter(channel, make_task_event(make_task("drewai", "imac")))
        assert await cog.handle_message(starter) is None
        assert await repo.count_tasks() == 0

    @pytest.mark.asyncio
    async def test_listener_only_reacts_to_bots_in_the_handoff_scope(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        cog = _cog(repo, channel, tmp_path)
        cog.handle_message = AsyncMock()  # type: ignore[method-assign]
        human = FakeMessage(1, "hi", author_id=5, channel=channel, guild=channel.guild, bot=False)
        outside = FakeMessage(
            2, "x", author_id=DREWAI_BOT, channel=FakeChannel(CHANNEL + 9), guild=channel.guild
        )
        inside = FakeMessage(3, "x", author_id=DREWAI_BOT, channel=channel, guild=channel.guild)
        await cog.on_message(human)  # type: ignore[arg-type]
        await cog.on_message(outside)  # type: ignore[arg-type]
        await cog.on_message(inside)  # type: ignore[arg-type]
        cog.handle_message.assert_awaited_once_with(inside)

    @pytest.mark.asyncio
    async def test_chat_cog_bot_guard_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An ordinary bot message still starts zero chat turns in ClaudeChatCog."""
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        monkeypatch.delenv("CCDB_ALLOWED_CATEGORY_IDS", raising=False)
        monkeypatch.delenv("CCDB_LAUNCHER_CHANNEL_ID", raising=False)
        for key, value in CONFIG_ENV.items():
            monkeypatch.setenv(key, value)
        cog = SimpleNamespace(
            _handoff_repo=None,
            _try_receive_handoff_message=AsyncMock(return_value=False),
            _handle_thread_reply=AsyncMock(),
            _handle_new_conversation=AsyncMock(),
            _handle_mention=AsyncMock(),
            _is_no_mention_scope=lambda channel: True,
            _is_summoned=lambda message: True,
            _allowed_user_ids=None,
            _claimed_by_task_loop=lambda message: False,
        )
        message = FakeMessage(
            1, "✅ build passed", author_id=DREWAI_BOT, channel=FakeChannel(), guild=FakeGuild()
        )
        await ClaudeChatCog.on_message(cog, message)  # type: ignore[arg-type]
        cog._handle_thread_reply.assert_not_awaited()
        cog._handle_new_conversation.assert_not_awaited()
        cog._handle_mention.assert_not_awaited()


# ---------------------------------------------------------------------------
# Reconnect scanning and reconciliation (task 2.4)
# ---------------------------------------------------------------------------

from claude_code_core.handoffs.state import HandoffTrigger, apply  # noqa: E402


def _online_chat(cog: AgentHandoffCog) -> SimpleNamespace:
    chat = SimpleNamespace(
        bot=cog.bot, handoff_capacity_available=lambda: True, run_handoff_turn=AsyncMock()
    )
    cog.bot.cogs["ClaudeChatCog"] = chat
    return chat


class TestReconnect:
    @pytest.mark.asyncio
    async def test_offline_addressed_task_is_discovered_once_after_startup(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        root = tmp_path / "drewp" / "main-projects"
        root.mkdir(parents=True)
        channel = FakeChannel()
        starter = _starter(channel, make_task_event())
        await starter.create_thread(name=job_thread_name(TASK_ID))  # the sender opened it
        cog = _cog(repo, channel, root)
        chat = _online_chat(cog)

        first = await cog.reconcile_on_reconnect(now=NOW)
        second = await cog.reconcile_on_reconnect(now=NOW)

        assert first.discovered == [TASK_ID]
        assert second.discovered == []
        assert await repo.count_tasks() == 1
        job = await repo.get_job(TASK_ID, "david")
        assert job is not None and job.state is HandoffState.RUNNING
        assert chat.run_handoff_turn.await_count == 1
        thread = channel.threads[4242]
        acks = [
            e
            for e in (parse_event_message(t) for t in thread.sent)
            if e is not None and e.kind is p.HandoffEventKind.ACK
        ]
        assert len(acks) == 1

    @pytest.mark.asyncio
    async def test_archived_job_thread_is_fetched_and_reopened(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        root = tmp_path / "drewp" / "main-projects"
        root.mkdir(parents=True)
        channel = FakeChannel()
        starter = _starter(channel, make_task_event())
        archived = FakeThread(4242, job_thread_name(TASK_ID), archived=True)
        channel.threads[4242] = archived  # reachable by fetch, not on the cached message
        starter.thread = None

        async def create_thread(**_: object) -> FakeThread:
            raise RuntimeError("already has a thread")

        starter.create_thread = create_thread  # type: ignore[method-assign]
        cog = _cog(repo, channel, root)
        _online_chat(cog)

        report = await cog.reconcile_on_reconnect(now=NOW)

        assert report.discovered == [TASK_ID]
        assert await repo.get_job_thread(TASK_ID, "david") == 4242
        assert archived.archived is False
        assert any("accepted" in text for text in archived.sent)

    @pytest.mark.asyncio
    async def test_running_job_is_requeued_and_resumed_once(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        root = tmp_path / "drewp" / "main-projects"
        root.mkdir(parents=True)
        channel = FakeChannel()
        starter = _starter(channel, make_task_event())
        await starter.create_thread(name=job_thread_name(TASK_ID))
        task = make_task()
        await repo.record_task(task, now=NOW)
        await repo.set_job_thread(TASK_ID, "david", 4242)
        job = await repo.get_job(TASK_ID, "david")
        assert job is not None
        await repo.claim_attempt(TASK_ID, "david", attempt=1, now=NOW)
        await repo.save_transition(apply(job, HandoffTrigger.START, now=NOW))
        cog = _cog(repo, channel, root)
        chat = _online_chat(cog)

        report = await cog.reconcile_on_reconnect(now=NOW + timedelta(minutes=1))

        assert report.requeued == [TASK_ID]
        assert report.discovered == []
        assert chat.run_handoff_turn.await_count == 1
        assert len(await repo.list_attempts(TASK_ID, "david")) == 1
        job = await repo.get_job(TASK_ID, "david")
        assert job is not None and job.state is HandoffState.RUNNING

    @pytest.mark.asyncio
    async def test_scan_is_bounded_by_the_retention_window(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        seen: dict[str, object] = {}
        original = channel.history

        def history(**kwargs: object):
            seen.update(kwargs)
            return original(**kwargs)  # type: ignore[arg-type]

        channel.history = history  # type: ignore[method-assign]
        cog = _cog(repo, channel, tmp_path)
        _online_chat(cog)

        await cog.reconcile_on_reconnect(now=NOW)

        assert seen["after"] == NOW - cog.config.retention
        assert isinstance(seen["limit"], int) and seen["limit"] <= 500

    @pytest.mark.asyncio
    async def test_on_ready_runs_reconciliation(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        cog = _cog(repo, FakeChannel(), tmp_path)
        cog.reconcile_on_reconnect = AsyncMock()  # type: ignore[method-assign]
        await cog.on_ready()
        cog.reconcile_on_reconnect.assert_awaited_once()


# ---------------------------------------------------------------------------
# Outbox retry delivery (task 4.1)
# ---------------------------------------------------------------------------

from claude_discord.database.handoff_repo import OutboxStatus  # noqa: E402
from claude_discord.handoff_return import record_and_deliver_handoff_result  # noqa: E402


class TestOutboxDelivery:
    @pytest.mark.asyncio
    async def test_unavailable_origin_receives_one_later_result_without_rerunning(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        root = tmp_path / "drewp" / "main-projects"
        root.mkdir(parents=True)
        channel = FakeChannel()
        cog = _cog(repo, channel, root)
        chat = _online_chat(cog)
        starter = _starter(channel, make_task_event())
        await cog.handle_message(starter, now=NOW)
        sink = chat.run_handoff_turn.await_args.kwargs["result_sink"]

        # The origin thread (6006) is not reachable while the worker finishes.
        # (The sink stamps the result with the wall clock, so "later" is real.)
        await sink("Found it under youtube-money.", None)
        later = datetime.now(UTC) + timedelta(hours=1)
        pending = await repo.pending_deliveries(now=later)
        assert len(pending) == 1 and pending[0].status is OutboxStatus.PENDING
        job = await repo.get_job(TASK_ID, "david")
        assert job is not None and job.state is HandoffState.COMPLETED

        # The origin comes back; the next delivery pass reaches it exactly once.
        origin = FakeThread(6006, "origin")
        channel.threads[6006] = origin
        first = await cog.deliver_pending(now=later)
        second = await cog.deliver_pending(now=later + timedelta(hours=1))

        assert first == 1 and second == 0
        assert len(origin.sent) == 1 and "youtube-money" in origin.sent[0]
        assert await repo.pending_deliveries(now=later + timedelta(days=1)) == []
        assert len(await repo.list_attempts(TASK_ID, "david")) == 1, "never rerun"
        assert chat.run_handoff_turn.await_count == 1

    @pytest.mark.asyncio
    async def test_delivery_gives_up_visibly_at_the_bound(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        from claude_discord.database.handoff_repo import MAX_DELIVERY_ATTEMPTS

        channel = FakeChannel()
        cog = _cog(repo, channel, tmp_path)
        task = make_task()
        await repo.record_task(task, now=NOW)
        job = await repo.get_job(TASK_ID, "david")
        assert job is not None
        await repo.save_transition(apply(job, HandoffTrigger.START, now=NOW))
        await record_and_deliver_handoff_result(
            repo=repo,
            bot=cog.bot,
            task_id=TASK_ID,
            local_agent_id="david",
            text="done",
            error=None,
            now=NOW,
        )
        when = NOW
        for _ in range(MAX_DELIVERY_ATTEMPTS + 2):
            when += timedelta(hours=2)
            await cog.deliver_pending(now=when)
        entry = await repo.get_delivery(1)
        assert entry is not None
        assert entry.status is OutboxStatus.ABANDONED
        assert entry.attempts == MAX_DELIVERY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_reconnect_also_flushes_the_outbox(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        cog = _cog(repo, channel, tmp_path)
        _online_chat(cog)
        cog.deliver_pending = AsyncMock(return_value=0)  # type: ignore[method-assign]
        await cog.reconcile_on_reconnect(now=NOW)
        cog.deliver_pending.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delivery_loop_starts_with_the_cog(
        self, repo: HandoffRepository, tmp_path: Path
    ) -> None:
        channel = FakeChannel()
        config = HandoffConfig.from_env(CONFIG_ENV)
        assert config is not None
        cog = AgentHandoffCog(_bot(channel), repo=repo, config=config)  # type: ignore[arg-type]
        try:
            await cog.cog_load()
            assert cog.delivery_loop.is_running()
        finally:
            await cog.cog_unload()
        await asyncio.sleep(0.05)
        assert not cog.delivery_loop.is_running()
