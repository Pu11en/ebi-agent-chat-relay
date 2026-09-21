"""Linked child planning threads, tested against a fake Discord and a fake ledger.

The adapter is the only piece of the split feature that touches Discord, so its
tests pin what crosses that boundary and nothing else:

* a child thread is created once per split identity, with intent recorded
  before the spawn and the compact handoff — every required field, no
  transcript — as its first message;
* a repeated request, or a restart after creation, finds the same child and
  never spawns a second thread; an interrupted creation is reconciled by the
  spawn's correlation identity, never retried blind;
* a changed decision reaches only the children that carry it, as one bounded
  message naming the decision, its source and both revisions;
* an acknowledgement is recorded idempotently and only for the right child;
* completion is reported to the parent exactly once;
* the parent status lists every child with link, goal, state, dependencies and
  blocker, and two children never see each other's messages.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from extensions.feature_workflow.planning_discord import (
    MAX_CHILD_BRIEF_CHARS,
    ChildPlanningThreads,
    PlanningDiscordError,
    render_child_briefing,
    render_decision_update,
    render_parent_status,
)
from extensions.feature_workflow.planning_models import (
    REQUIRED_HANDOFF_FIELDS,
    ChildHandoff,
    ChildRecord,
    DecisionAck,
    DependencyKind,
    LockedDecision,
    OwnedScope,
    ParentLink,
    PlanDependency,
    PlanningState,
    ProjectTarget,
    SplitIdentity,
)

PARENT = 1550757693784989707
CHILD_A = 1551148966035333194
CHILD_B = 1551148966035333195
NOW = "2026-09-21T10:00:00+00:00"
LATER = "2026-09-21T10:05:00+00:00"


class FakeLedger:
    """The narrow persistence the adapter needs; the real one is the registry."""

    def __init__(self) -> None:
        self.records: dict[str, ChildRecord] = {}
        self.puts: list[ChildRecord] = []

    async def get(self, identity: SplitIdentity) -> ChildRecord | None:
        return self.records.get(identity.key)

    async def put(self, record: ChildRecord) -> None:
        self.records[record.identity.key] = record
        self.puts.append(record)

    async def children_of(self, parent_thread_id: int) -> tuple[ChildRecord, ...]:
        return tuple(
            record
            for record in self.records.values()
            if record.parent_thread_id == parent_thread_id
        )


class FakeTransport:
    """A Discord that remembers what was asked of it."""

    def __init__(self, thread_ids: Iterable[int] = (CHILD_A, CHILD_B)) -> None:
        self.pending = list(thread_ids)
        self.spawned: list[dict[str, object]] = []
        self.posts: list[tuple[int, str]] = []
        self.known: dict[str, int] = {}
        self.fail_spawn = False
        self.fail_lookup = False

    async def spawn_thread(
        self, *, parent_thread_id: int, name: str, prompt: str, correlation_id: str
    ) -> int:
        if self.fail_spawn:
            raise ConnectionError("relay timed out")
        thread_id = self.pending.pop(0)
        self.spawned.append(
            {
                "parent_thread_id": parent_thread_id,
                "name": name,
                "prompt": prompt,
                "correlation_id": correlation_id,
                "thread_id": thread_id,
            }
        )
        self.known[correlation_id] = thread_id
        return thread_id

    async def find_thread(self, correlation_id: str) -> int | None:
        if self.fail_lookup:
            raise ConnectionError("relay unavailable")
        return self.known.get(correlation_id)

    async def post(self, thread_id: int, text: str) -> None:
        self.posts.append((thread_id, text))

    def posts_to(self, thread_id: int) -> list[str]:
        return [text for target, text in self.posts if target == thread_id]


def parent_link() -> ParentLink:
    return ParentLink(
        thread_id=PARENT,
        url=f"https://discord.com/channels/1/{PARENT}",
        plan_owner="parent-planner",
    )


def decision(
    decision_id: str = "d1", revision: str = "r1", statement: str | None = None
) -> LockedDecision:
    return LockedDecision(
        id=decision_id,
        statement=statement or "Texas only; other states need a paying client first",
        revision=revision,
        source="parent-thread",
    )


def handoff(goal: str, *, decisions: tuple[LockedDecision, ...] = (decision(),)) -> ChildHandoff:
    return ChildHandoff(
        goal=goal,
        target=ProjectTarget(project="realpage", computer="asset"),
        decisions=decisions,
        dependencies=(
            PlanDependency(
                on=f"{PARENT}:lead-table",
                kind=DependencyKind.ORDERING,
                reason="reads the lead table",
            ),
        ),
        ownership=(OwnedScope(project="realpage", paths=("digest/weekly.py",), owner="child"),),
        restrictions=("planning is read-only", "never merge"),
        authority="plan only; the parent thread dispatches the build",
        expected_output="one implementation-ready plan with a Check command",
        parent=parent_link(),
    )


@pytest.fixture
def ledger() -> FakeLedger:
    return FakeLedger()


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def threads(ledger: FakeLedger, transport: FakeTransport) -> ChildPlanningThreads:
    return ChildPlanningThreads(ledger=ledger, transport=transport)


class TestBriefing:
    def test_briefing_carries_every_required_field_and_no_transcript(self) -> None:
        text = render_child_briefing(handoff("Send a weekly email digest of new leads"))
        lowered = text.lower()
        for field in REQUIRED_HANDOFF_FIELDS:
            assert field.replace("_", " ") in lowered, field
        assert "Send a weekly email digest of new leads" in text
        assert "realpage" in text and "asset" in text
        assert "d1" in text and "r1" in text and "Texas only" in text
        assert "digest/weekly.py" in text
        assert f"https://discord.com/channels/1/{PARENT}" in text
        assert "transcript" not in lowered.replace("no transcript", "")
        assert len(text) <= MAX_CHILD_BRIEF_CHARS

    def test_briefing_states_the_child_owns_one_plan_and_reports_once(self) -> None:
        text = render_child_briefing(handoff("Send a weekly email digest of new leads"))
        assert "exactly one" in text
        assert "once" in text
        assert "another child" in text


class TestOpenChild:
    async def test_creates_one_linked_thread_with_the_compact_handoff(
        self, threads: ChildPlanningThreads, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        assert record.thread_id == CHILD_A
        assert record.state is PlanningState.PLANNING
        assert record.url == f"https://discord.com/channels/1/{CHILD_A}"
        assert len(transport.spawned) == 1
        spawn = transport.spawned[0]
        assert spawn["parent_thread_id"] == PARENT
        assert spawn["correlation_id"] == record.identity.key
        assert spawn["prompt"] == render_child_briefing(record.handoff)
        assert "Send a weekly email digest" in str(spawn["name"])
        # The parent learns the link once.
        parent_posts = transport.posts_to(PARENT)
        assert len(parent_posts) == 1 and str(CHILD_A) in parent_posts[0]

    async def test_intent_is_recorded_before_the_spawn(
        self, threads: ChildPlanningThreads, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        states = [record.state for record in ledger.puts]
        assert states[0] is PlanningState.CREATING
        assert states[0].value == "creating" and ledger.puts[0].thread_id is None
        assert states[-1] is PlanningState.PLANNING

    async def test_same_request_twice_is_one_child(
        self, threads: ChildPlanningThreads, transport: FakeTransport
    ) -> None:
        first = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        second = await threads.open_child(handoff("  send a WEEKLY email digest "), now=LATER)
        assert second.identity == first.identity
        assert second.thread_id == first.thread_id
        assert len(transport.spawned) == 1
        assert len(transport.posts_to(PARENT)) == 1

    async def test_restart_restores_the_link_without_a_replacement_thread(
        self, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        before = ChildPlanningThreads(ledger=ledger, transport=transport)
        created = await before.open_child(handoff("Send a weekly email digest"), now=NOW)
        # A new process, same ledger.
        after = ChildPlanningThreads(ledger=ledger, transport=transport)
        restored = await after.open_child(handoff("Send a weekly email digest"), now=LATER)
        assert restored.thread_id == created.thread_id
        assert len(transport.spawned) == 1

    async def test_interrupted_creation_is_reconciled_by_correlation_not_retried(
        self, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        threads = ChildPlanningThreads(ledger=ledger, transport=transport)
        transport.fail_spawn = True
        with pytest.raises(PlanningDiscordError):
            await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        identity = SplitIdentity.from_goal(PARENT, "Send a weekly email digest")
        stuck = await ledger.get(identity)
        assert stuck is not None and stuck.state is PlanningState.CREATING
        assert stuck.thread_id is None

        # Discord did create the thread; only the answer was lost.
        transport.fail_spawn = False
        transport.known[identity.key] = CHILD_B
        recovered = await threads.open_child(handoff("Send a weekly email digest"), now=LATER)
        assert recovered.thread_id == CHILD_B
        assert transport.spawned == []

    async def test_unresolvable_interruption_blocks_instead_of_spawning(
        self, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        threads = ChildPlanningThreads(ledger=ledger, transport=transport)
        transport.fail_spawn = True
        with pytest.raises(PlanningDiscordError):
            await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        transport.fail_spawn = False
        transport.fail_lookup = True
        blocked = await threads.open_child(handoff("Send a weekly email digest"), now=LATER)
        assert blocked.state is PlanningState.BLOCKED
        assert blocked.blocker and "reconcile" in blocked.blocker
        assert transport.spawned == []

    async def test_lookup_that_proves_absence_creates_the_child_once(
        self, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        threads = ChildPlanningThreads(ledger=ledger, transport=transport)
        transport.fail_spawn = True
        with pytest.raises(PlanningDiscordError):
            await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        transport.fail_spawn = False
        recovered = await threads.open_child(handoff("Send a weekly email digest"), now=LATER)
        assert recovered.thread_id == CHILD_A
        assert len(transport.spawned) == 1

    async def test_two_children_get_two_threads_and_two_parent_links(
        self, threads: ChildPlanningThreads, transport: FakeTransport
    ) -> None:
        first = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        second = await threads.open_child(handoff("Add a permit map view"), now=NOW)
        assert first.thread_id != second.thread_id
        assert first.identity != second.identity
        assert len(transport.posts_to(PARENT)) == 2


class TestDecisionUpdates:
    async def test_changed_decision_reaches_only_children_that_carry_it(
        self, threads: ChildPlanningThreads, transport: FakeTransport
    ) -> None:
        digest = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        other = await threads.open_child(
            handoff("Add a permit map view", decisions=(decision(decision_id="d2"),)), now=NOW
        )
        changed = decision(revision="r2", statement="Texas and Oklahoma from October")
        updated = await threads.update_decision(PARENT, changed, now=LATER)

        assert [record.identity for record in updated] == [digest.identity]
        assert transport.posts_to(other.thread_id or 0) == [] or all(
            "d1" not in text for text in transport.posts_to(other.thread_id or 0)
        )
        posts = transport.posts_to(CHILD_A)
        assert len(posts) == 1
        assert posts[0] == render_decision_update(decision(), changed)
        assert "d1" in posts[0] and "r1" in posts[0] and "r2" in posts[0]
        assert "parent-thread" in posts[0]
        assert "Texas and Oklahoma from October" in posts[0]
        assert len(posts[0]) <= 2000

    async def test_updated_decision_is_stored_on_the_child_record(
        self, threads: ChildPlanningThreads, ledger: FakeLedger
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        changed = decision(revision="r2", statement="Texas and Oklahoma from October")
        await threads.update_decision(PARENT, changed, now=LATER)
        stored = await ledger.get(record.identity)
        assert stored is not None
        assert stored.handoff.decision("d1") == changed
        assert not stored.has_acknowledged("d1", "r2")

    async def test_same_revision_is_not_resent(
        self, threads: ChildPlanningThreads, transport: FakeTransport
    ) -> None:
        await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        assert await threads.update_decision(PARENT, decision(), now=LATER) == ()
        assert transport.posts_to(CHILD_A) == []

    async def test_finished_children_are_left_alone(
        self, threads: ChildPlanningThreads, ledger: FakeLedger, transport: FakeTransport
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        await ledger.put(record.with_state(PlanningState.INTEGRATED, at=LATER))
        changed = decision(revision="r2")
        assert await threads.update_decision(PARENT, changed, now=LATER) == ()
        assert transport.posts_to(CHILD_A) == []


class TestAcknowledgements:
    async def test_acknowledgement_is_recorded_once(
        self, threads: ChildPlanningThreads, ledger: FakeLedger
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        ack = DecisionAck(
            decision_id="d1", revision="r1", child_thread_id=CHILD_A, acknowledged_at=LATER
        )
        first = await threads.acknowledge(record.identity, ack)
        second = await threads.acknowledge(record.identity, ack)
        assert first.has_acknowledged("d1", "r1")
        assert second == first
        stored = await ledger.get(record.identity)
        assert stored is not None and len(stored.acks) == 1

    async def test_acknowledgement_from_the_wrong_child_is_refused(
        self, threads: ChildPlanningThreads
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        wrong = DecisionAck(
            decision_id="d1", revision="r1", child_thread_id=CHILD_B, acknowledged_at=LATER
        )
        with pytest.raises(PlanningDiscordError):
            await threads.acknowledge(record.identity, wrong)

    async def test_unknown_child_is_refused(self, threads: ChildPlanningThreads) -> None:
        ack = DecisionAck(
            decision_id="d1", revision="r1", child_thread_id=CHILD_A, acknowledged_at=LATER
        )
        with pytest.raises(PlanningDiscordError):
            await threads.acknowledge(SplitIdentity.from_goal(PARENT, "never opened"), ack)


class TestCompletion:
    async def test_plan_ready_is_reported_to_the_parent_exactly_once(
        self, threads: ChildPlanningThreads, transport: FakeTransport, ledger: FakeLedger
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        before = len(transport.posts_to(PARENT))
        first = await threads.report_plan_ready(
            record.identity, plan_revision="abc123", next_boundary="digest/weekly.py", now=LATER
        )
        second = await threads.report_plan_ready(
            record.identity, plan_revision="abc123", next_boundary="digest/weekly.py", now=LATER
        )
        assert first is True and second is False
        posts = transport.posts_to(PARENT)[before:]
        assert len(posts) == 1
        assert "abc123" in posts[0] and "digest/weekly.py" in posts[0]
        assert str(CHILD_A) in posts[0]
        stored = await ledger.get(record.identity)
        assert stored is not None
        assert stored.state is PlanningState.PLAN_READY
        assert stored.plan_revision == "abc123"

    async def test_a_new_revision_is_reported_again(
        self, threads: ChildPlanningThreads, transport: FakeTransport
    ) -> None:
        record = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        await threads.report_plan_ready(
            record.identity, plan_revision="abc123", next_boundary="digest/", now=LATER
        )
        again = await threads.report_plan_ready(
            record.identity, plan_revision="def456", next_boundary="digest/", now=LATER
        )
        assert again is True

    async def test_completion_of_one_child_does_not_touch_the_other(
        self, threads: ChildPlanningThreads, transport: FakeTransport, ledger: FakeLedger
    ) -> None:
        first = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        second = await threads.open_child(handoff("Add a permit map view"), now=NOW)
        await threads.report_plan_ready(
            first.identity, plan_revision="abc123", next_boundary="digest/", now=LATER
        )
        other = await ledger.get(second.identity)
        assert other is not None and other.state is PlanningState.PLANNING
        assert transport.posts_to(second.thread_id or 0) == []


class TestParentStatus:
    async def test_status_lists_every_child_with_link_state_dependencies_and_blocker(
        self, threads: ChildPlanningThreads, ledger: FakeLedger
    ) -> None:
        first = await threads.open_child(handoff("Send a weekly email digest"), now=NOW)
        second = await threads.open_child(handoff("Add a permit map view"), now=NOW)
        await ledger.put(
            second.with_state(
                PlanningState.BLOCKED, at=LATER, blocker="waiting on the map tile licence"
            )
        )
        text = await threads.parent_status(PARENT)
        assert first.url and first.url in text
        assert second.url and second.url in text
        assert "Send a weekly email digest" in text and "Add a permit map view" in text
        assert "planning" in text and "blocked" in text
        assert f"{PARENT}:lead-table" in text
        assert "waiting on the map tile licence" in text
        assert "2 children" in text or "2 child" in text

    async def test_status_for_a_parent_without_children(
        self, threads: ChildPlanningThreads
    ) -> None:
        text = await threads.parent_status(PARENT)
        assert "no child" in text.lower()

    def test_render_uses_the_summary_only(self) -> None:
        record = ChildRecord(
            identity=SplitIdentity.from_goal(PARENT, "Send a weekly email digest"),
            handoff=handoff("Send a weekly email digest"),
            state=PlanningState.PLANNING,
            created_at=NOW,
            updated_at=NOW,
            thread_id=CHILD_A,
        )
        text = render_parent_status(PARENT, (record,))
        assert "Texas only" not in text  # decisions are the child's, not the dashboard's
        assert len(text) <= 2000
