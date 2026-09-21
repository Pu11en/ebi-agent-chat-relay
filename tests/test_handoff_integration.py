"""Three trusted computers, one fake Discord, no model: the handoff protocol end to end.

DrewAI, David and iMac each run their own ``AgentHandoffCog`` against their own
ledger and configuration. They share one in-memory Discord (a guild, the
``agent-handoffs`` channel, its threads, and an origin chat thread) that
delivers every message to every bot that is online — including redeliveries
and messages the recipient missed while it was away. Workers never call a
model: the fake chat records the result sink and the test "finishes" the
turn by calling it.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from claude_code_core.handoffs import protocol as p
from claude_code_core.handoffs.state import HandoffState
from claude_discord.cogs.agent_handoff import AgentHandoffCog
from claude_discord.database.handoff_repo import HandoffRepository
from claude_discord.database.models import init_db
from claude_discord.handoff_authority import RecipientPolicy
from claude_discord.handoff_config import HandoffConfig
from claude_discord.handoff_discord import job_thread_name, parse_event_message
from claude_discord.handoff_executor import HandoffExecutor
from claude_discord.handoff_projects import ApprovedRootResolver

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
GUILD = 1001
HANDOFF_CHANNEL = 2002
CHAT_CHANNEL = 5005
ORIGIN_THREAD = 6006
BOT_IDS = {"drewai": 111, "david": 222, "imac": 333}


# ---------------------------------------------------------------------------
# The fake Discord
# ---------------------------------------------------------------------------


class Guild:
    id = GUILD


class Message:
    def __init__(self, message_id: int, content: str, author_id: int, channel: Any) -> None:
        self.id = message_id
        self.content = content
        self.author = SimpleNamespace(id=author_id, bot=True)
        self.channel = channel
        self.guild = Guild()
        self.webhook_id = None
        self.thread: Thread | None = None
        self._net: Network = channel.net

    async def create_thread(self, *, name: str, **_: object) -> Thread:
        if self.thread is not None:
            raise RuntimeError("thread already exists")
        self.thread = Thread(self._net, self.id, name, parent_id=self.channel.id)
        self._net.threads[self.id] = self.thread
        return self.thread


class Channel:
    def __init__(self, net: Network, channel_id: int) -> None:
        self.net = net
        self.id = channel_id
        self.parent_id = None
        self.guild = Guild()
        self.messages: list[Message] = []

    async def send(self, content: str) -> Message:
        return await self.net.post(self, content)

    def history(self, *, limit: int = 100, after: object = None, oldest_first: bool = True):
        async def _iter():
            for message in self.messages[-limit:]:
                yield message

        return _iter()


class Thread(Channel):
    def __init__(self, net: Network, thread_id: int, name: str, *, parent_id: int) -> None:
        super().__init__(net, thread_id)
        self.name = name
        self.parent_id = parent_id
        self.archived = False

    async def edit(self, **kwargs: object) -> None:
        if "archived" in kwargs:
            self.archived = bool(kwargs["archived"])


@dataclass
class Network:
    """One guild's worth of channels, and who is listening."""

    channels: dict[int, Channel] = field(default_factory=dict)
    threads: dict[int, Thread] = field(default_factory=dict)
    bots: dict[str, AgentBot] = field(default_factory=dict)
    queue: deque[Message] = field(default_factory=deque)
    next_id: int = 10_000
    current_author: int = 0

    def __post_init__(self) -> None:
        self.channels[HANDOFF_CHANNEL] = Channel(self, HANDOFF_CHANNEL)
        self.channels[CHAT_CHANNEL] = Channel(self, CHAT_CHANNEL)
        self.threads[ORIGIN_THREAD] = Thread(self, ORIGIN_THREAD, "origin", parent_id=CHAT_CHANNEL)

    def find(self, channel_id: int) -> Channel | Thread | None:
        return self.channels.get(channel_id) or self.threads.get(channel_id)

    async def post(self, channel: Channel, content: str) -> Message:
        self.next_id += 1
        message = Message(self.next_id, content, self.current_author, channel)
        channel.messages.append(message)
        self.queue.append(message)
        return message

    async def pump(self, *, now: datetime = NOW) -> None:
        """Deliver every queued message to every online bot, until quiet."""
        while self.queue:
            message = self.queue.popleft()
            for bot in list(self.bots.values()):
                if bot.online:
                    await bot.receive(message, now=now)

    def redeliver(self, message: Message) -> None:
        self.queue.append(message)

    @property
    def handoff_channel(self) -> Channel:
        return self.channels[HANDOFF_CHANNEL]

    @property
    def origin_thread(self) -> Thread:
        return self.threads[ORIGIN_THREAD]


# ---------------------------------------------------------------------------
# One bot identity
# ---------------------------------------------------------------------------


class FakeChat:
    """Records worker turns instead of running a model."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.turns: list[dict[str, Any]] = []

    def handoff_capacity_available(self) -> bool:
        return True

    async def run_handoff_turn(self, thread: Any, prompt: str, **kw: Any) -> None:
        self.turns.append({"thread": thread, "prompt": prompt, **kw})


class AgentBot:
    def __init__(self, net: Network, agent_id: str, repo: HandoffRepository, root: Path) -> None:
        self.net = net
        self.agent_id = agent_id
        self.user_id = BOT_IDS[agent_id]
        self.repo = repo
        self.root = root
        self.online = True
        self.cogs: dict[str, Any] = {}
        self.user = SimpleNamespace(id=self.user_id)
        self.chat = FakeChat(self)
        self.cogs["ClaudeChatCog"] = self.chat
        self.cog = self._build_cog()
        self.cogs["AgentHandoffCog"] = self.cog

    def _build_cog(self) -> AgentHandoffCog:
        env = {
            "CCDB_AGENT_ID": self.agent_id,
            "CCDB_HANDOFF_GUILD_ID": str(GUILD),
            "CCDB_HANDOFF_CHANNEL_ID": str(HANDOFF_CHANNEL),
            "CCDB_HANDOFF_AGENTS": ",".join(f"{a}={b}" for a, b in BOT_IDS.items()),
        }
        config = HandoffConfig.from_env(env)
        assert config is not None
        executor = HandoffExecutor(
            repo=self.repo,
            local_agent_id=self.agent_id,
            resolver=ApprovedRootResolver(roots={"drew": (self.root,)}),
            policy=RecipientPolicy(),
            thread_lookup=self.fetch_channel,
        )
        return AgentHandoffCog(
            self,  # type: ignore[arg-type]
            repo=self.repo,
            config=config,
            executor=executor,
            chat=self.chat,
            start_loops=False,
        )

    # discord.py-ish surface the cog uses
    def get_channel(self, channel_id: int) -> Any | None:
        return self.net.find(channel_id)

    async def fetch_channel(self, channel_id: int) -> Any:
        found = self.net.find(channel_id)
        if found is None:
            raise RuntimeError(f"no channel {channel_id}")
        return found

    async def receive(self, message: Message, *, now: datetime) -> None:
        self.net.current_author = self.user_id
        await self.cog.handle_message(message, now=now)

    async def restart(self) -> None:
        """Lose the process (the running turn with it), keep the ledger, come back."""
        self.chat = FakeChat(self)
        self.cogs["ClaudeChatCog"] = self.chat
        self.cog = self._build_cog()
        self.cogs["AgentHandoffCog"] = self.cog
        self.net.current_author = self.user_id
        await self.cog.reconcile_on_reconnect(now=NOW + timedelta(minutes=5))

    async def reconnect(self, *, now: datetime) -> None:
        self.online = True
        self.net.current_author = self.user_id
        await self.cog.reconcile_on_reconnect(now=now)

    async def send(self, task: p.HandoffTask) -> p.HandoffEvent:
        event = p.HandoffEvent(
            event_id=str(uuid.uuid4()),
            kind=p.HandoffEventKind.TASK,
            task_id=task.task_id,
            sender=task.sender,
            recipient=task.recipient,
            sequence=0,
            created_at=task.created_at,
            task=task,
        )
        self.net.current_author = self.user_id
        await self.cog.send_task(event, now=NOW)
        return event

    async def finish_turn(self, text: str, *, index: int = -1) -> None:
        self.net.current_author = self.user_id
        await self.chat.turns[index]["result_sink"](text, None)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[Network]:
    net = Network()
    paths: list[str] = []
    for agent in BOT_IDS:
        fd, path = tempfile.mkstemp(suffix=f"-{agent}.db")
        os.close(fd)
        await init_db(path)
        paths.append(path)
        root = tmp_path / agent / "drewp"
        (root / "main-projects").mkdir(parents=True)
        net.bots[agent] = AgentBot(net, agent, HandoffRepository(path), root)
    try:
        yield net
    finally:
        for path in paths:
            os.unlink(path)


def task_for(
    sender: str,
    recipient: str,
    *,
    goal: str = "List the folders under main-projects and their remotes",
    authority: p.AuthorityScope | None = None,
) -> p.HandoffTask:
    origin = p.ConversationCoordinate(
        guild_id=GUILD, channel_id=CHAT_CHANNEL, thread_id=ORIGIN_THREAD, message_id=77
    )
    return p.HandoffTask(
        task_id=str(uuid.uuid4()),
        sender=sender,
        recipient=recipient,
        origin=origin,
        origin_human_id="drew",
        project=p.ProjectLocator(owner="drew", folder="main-projects"),
        goal=goal,
        authority=authority or p.AuthorityScope(read=True),
        expected_result="Exact paths and a short summary.",
        reply_to=origin,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=6),
    )


def events_in(thread: Thread) -> list[p.HandoffEvent]:
    """The task's story: the starter in the channel, then everything in its thread."""
    starter = next(m for m in thread.net.handoff_channel.messages if m.id == thread.id)
    messages = [starter, *thread.messages]
    return [e for e in (parse_event_message(m.content) for m in messages) if e is not None]


async def run_handoff(world: Network, sender: str, recipient: str, **kw: Any) -> p.HandoffTask:
    task = task_for(sender, recipient, **kw)
    await world.bots[sender].send(task)
    await world.pump()
    return task


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender", "recipient"),
    [("drewai", "david"), ("david", "drewai"), ("imac", "drewai")],
)
async def test_any_trusted_pair_hands_work_and_returns_the_result(
    world: Network, sender: str, recipient: str
) -> None:
    task = await run_handoff(world, sender, recipient)
    recipient_bot = world.bots[recipient]
    sender_bot = world.bots[sender]

    # The recipient stored, acknowledged and started the job in the one thread.
    job = await recipient_bot.repo.get_job(task.task_id, recipient)
    assert job is not None and job.state is HandoffState.RUNNING
    thread = world.threads[world.handoff_channel.messages[0].id]
    assert thread.name == job_thread_name(task.task_id)
    assert len(recipient_bot.chat.turns) == 1
    assert recipient_bot.chat.turns[0]["thread"] is thread
    assert "read-only" in recipient_bot.chat.turns[0]["prompt"].lower()
    # Nobody else started anything.
    for other, bot in world.bots.items():
        if other != recipient:
            assert bot.chat.turns == []

    await recipient_bot.finish_turn("Found youtube-money, craneSignal, realpage.")
    await world.pump()

    kinds = [e.kind for e in events_in(thread)]
    assert kinds == [
        p.HandoffEventKind.TASK,
        p.HandoffEventKind.ACK,
        p.HandoffEventKind.STATE,
        p.HandoffEventKind.RESULT,
    ]
    origin_posts = [m.content for m in world.origin_thread.messages]
    assert any("accepted" in text for text in origin_posts), "the origin bot relayed the ack"
    results = sum("youtube-money" in text for text in origin_posts)
    assert results == 1, "one result, from the recipient"
    # Both ledgers agree, and the sender's ledger never became a second executor.
    mirrored = await sender_bot.repo.get_job(task.task_id, recipient)
    assert mirrored is not None and mirrored.state is HandoffState.COMPLETED
    final = await recipient_bot.repo.get_job(task.task_id, recipient)
    assert final is not None and final.state is HandoffState.COMPLETED
    assert await sender_bot.repo.get_job(task.task_id, sender) is None


@pytest.mark.asyncio
async def test_offline_recipient_recovers_the_job_once_after_reconnecting(world: Network) -> None:
    imac = world.bots["imac"]
    imac.online = False
    task = await run_handoff(world, "drewai", "imac")
    assert await imac.repo.get_job(task.task_id, "imac") is None
    assert imac.chat.turns == []

    await imac.reconnect(now=NOW + timedelta(hours=1))
    await world.pump()
    await imac.reconnect(now=NOW + timedelta(hours=2))  # a second reconnect changes nothing
    await world.pump()

    job = await imac.repo.get_job(task.task_id, "imac")
    assert job is not None and job.state is HandoffState.RUNNING
    assert len(imac.chat.turns) == 1
    thread = world.threads[world.handoff_channel.messages[0].id]
    acks = [e for e in events_in(thread) if e.kind is p.HandoffEventKind.ACK]
    assert len(acks) == 1 and acks[0].sender == "imac"


@pytest.mark.asyncio
async def test_duplicate_delivery_creates_one_job_one_thread_one_execution(world: Network) -> None:
    task = await run_handoff(world, "drewai", "david")
    starter = world.handoff_channel.messages[0]
    world.redeliver(starter)
    world.redeliver(starter)
    await world.pump()

    david = world.bots["david"]
    assert await david.repo.count_tasks() == 1
    assert len(david.chat.turns) == 1
    assert len(await david.repo.list_attempts(task.task_id, "david")) == 1
    assert len(world.threads) == 2  # the origin thread and one job thread
    thread = world.threads[starter.id]
    assert [e.kind for e in events_in(thread)].count(p.HandoffEventKind.ACK) == 1


@pytest.mark.asyncio
async def test_edit_without_authority_blocks_visibly_in_both_places(world: Network) -> None:
    task = await run_handoff(
        world, "drewai", "david", goal="Fix the failing test in youtube-money and commit"
    )
    david = world.bots["david"]
    job = await david.repo.get_job(task.task_id, "david")
    assert job is not None and job.state is HandoffState.BLOCKED
    assert david.chat.turns == []
    thread = world.threads[world.handoff_channel.messages[0].id]
    states = [e for e in events_in(thread) if e.kind is p.HandoffEventKind.STATE]
    assert states[-1].payload["state"] == "blocked"
    assert "edit" in str(states[-1].payload["note"])
    origin_posts = [m.content for m in world.origin_thread.messages]
    assert sum("blocked" in text for text in origin_posts) == 1
    mirrored = await world.bots["drewai"].repo.get_job(task.task_id, "david")
    assert mirrored is not None and mirrored.state is HandoffState.BLOCKED


@pytest.mark.asyncio
async def test_authorized_edit_runs_with_edit_scope_when_policy_allows(world: Network) -> None:
    david = world.bots["david"]
    david.cog.executor._policy = RecipientPolicy(allow_edit=True)  # noqa: SLF001 - test knob
    task = await run_handoff(
        world,
        "drewai",
        "david",
        goal="Fix the failing test in youtube-money and commit",
        authority=p.AuthorityScope(read=True, edit=True, edit_paths=("youtube-money",)),
    )
    job = await david.repo.get_job(task.task_id, "david")
    assert job is not None and job.state is HandoffState.RUNNING
    prompt = david.chat.turns[0]["prompt"]
    assert "youtube-money" in prompt and "do not deploy" in prompt.lower()


@pytest.mark.asyncio
async def test_restart_mid_execution_resumes_once_without_a_second_task(world: Network) -> None:
    task = await run_handoff(world, "drewai", "david")
    david = world.bots["david"]
    assert len(david.chat.turns) == 1

    await david.restart()
    await world.pump()

    job = await david.repo.get_job(task.task_id, "david")
    assert job is not None and job.state is HandoffState.RUNNING and job.attempt == 1
    assert len(david.chat.turns) == 1, "the new process resumed exactly once"
    assert await david.repo.count_tasks() == 1
    attempts = await david.repo.list_attempts(task.task_id, "david")
    assert len(attempts) == 1 and not attempts[0].is_finished
    thread = world.threads[world.handoff_channel.messages[0].id]
    states = [
        str(e.payload["state"]) for e in events_in(thread) if e.kind is p.HandoffEventKind.STATE
    ]
    assert states == ["running", "queued", "running"]

    await david.finish_turn("Done after restart.")
    await world.pump()
    final = await david.repo.get_job(task.task_id, "david")
    assert final is not None and final.state is HandoffState.COMPLETED
    assert sum("Done after restart" in m.content for m in world.origin_thread.messages) == 1


@pytest.mark.asyncio
async def test_final_return_survives_an_unavailable_origin(world: Network) -> None:
    task = await run_handoff(world, "drewai", "david")
    david = world.bots["david"]
    origin = world.threads.pop(ORIGIN_THREAD)  # the origin conversation is unreachable

    await david.finish_turn("Result while the origin is away.")
    await world.pump()
    assert not any("origin is away" in m.content for m in origin.messages)
    job = await david.repo.get_job(task.task_id, "david")
    assert job is not None and job.state is HandoffState.COMPLETED

    world.threads[ORIGIN_THREAD] = origin
    later = datetime.now(UTC) + timedelta(hours=2)
    assert await david.cog.deliver_pending(now=later) == 1
    assert await david.cog.deliver_pending(now=later + timedelta(hours=1)) == 0
    assert sum("origin is away" in m.content for m in origin.messages) == 1
    assert len(await david.repo.list_attempts(task.task_id, "david")) == 1
    mirrored = await world.bots["drewai"].repo.get_job(task.task_id, "david")
    assert mirrored is not None and mirrored.state is HandoffState.COMPLETED


@pytest.mark.asyncio
async def test_a_result_never_starts_work_anywhere(world: Network) -> None:
    task = await run_handoff(world, "drewai", "david")
    await world.bots["david"].finish_turn("Finished.")
    await world.pump()
    for agent, bot in world.bots.items():
        assert await bot.repo.count_tasks() == (1 if agent in ("drewai", "david") else 0)
        assert len(bot.chat.turns) == (1 if agent == "david" else 0)
    thread = world.threads[world.handoff_channel.messages[0].id]
    assert [e.kind for e in events_in(thread)].count(p.HandoffEventKind.TASK) == 1
    assert await world.bots["imac"].repo.get_job(task.task_id, "imac") is None
