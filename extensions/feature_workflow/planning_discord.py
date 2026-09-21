"""Linked child planning threads in Discord: the only file of the split that posts.

A planning conversation that grows a second build gets a second thread. This
adapter creates that thread, gives it its compact handoff as the first message,
keeps its decisions current, and tells the parent thread what its children are
doing. Everything it knows comes from two narrow interfaces so the policy and
the records stay testable without Discord:

* a ``ChildLedger`` — the durable registry keyed by split identity (the
  registry module owns persistence; this file only reads and writes records);
* a ``PlanningTransport`` — spawn a thread under a parent, look one up by its
  correlation identity, post a message. The shipped implementation talks to
  Ebi's localhost API; tests use a fake.

The rules the adapter enforces, rather than merely documents:

*Intent first, identity always.* The record is written as ``creating`` before
the spawn, and the spawn carries the split identity as its correlation ID. A
crash between the two leaves a record with no thread; the next request looks
the identity up instead of spawning again. Found → attached. Provably absent →
created once. Unknown → blocked for a person, never retried blind.

*A child hears only what binds it.* A decision update goes to the children
whose handoff carries that decision ID at an older revision — one bounded
message naming the decision, its source and both revisions — and is stored on
the record so a restart knows the child was told.

*The parent hears about completion once.* ``report_plan_ready`` posts to the
parent when the child's revision changes and not otherwise, so a retried call
after a crash does not repeat the report.

No transcript is ever copied; the handoff is rendered from the record.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol

from extensions.feature_workflow.planning_models import (
    TERMINAL_PLANNING_STATES,
    ChildHandoff,
    ChildRecord,
    DecisionAck,
    LockedDecision,
    PlanningModelError,
    PlanningState,
    SplitIdentity,
    summarize_children,
)

# A child's first message must fit in one spawn prompt a person can still read.
# The models bound every field; this is the belt to their braces.
MAX_CHILD_BRIEF_CHARS = 16000
MAX_THREAD_NAME_CHARS = 100
# Discord's message limit; status and updates are single messages by design.
MAX_MESSAGE_CHARS = 2000


class PlanningDiscordError(Exception):
    """The adapter could not act safely; the ledger says what was recorded."""


class ChildLedger(Protocol):
    """What the adapter needs from the durable registry, and nothing more."""

    async def get(self, identity: SplitIdentity) -> ChildRecord | None: ...

    async def put(self, record: ChildRecord) -> None: ...

    async def children_of(self, parent_thread_id: int) -> tuple[ChildRecord, ...]: ...


class PlanningTransport(Protocol):
    """What the adapter needs from Discord, through Ebi."""

    async def spawn_thread(
        self, *, parent_thread_id: int, name: str, prompt: str, correlation_id: str
    ) -> int: ...

    async def find_thread(self, correlation_id: str) -> int | None: ...

    async def post(self, thread_id: int, text: str) -> None: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- rendering ---------------------------------------------------------------


def render_child_briefing(handoff: ChildHandoff) -> str:
    """The child's first message: every required field, only its own decisions."""
    lines = [
        f"Child planning thread for parent {handoff.parent.url} "
        f"(plan owner {handoff.parent.plan_owner}).",
        "You own exactly one build plan. Plan it from this brief; the parent thread's "
        "conversation is not yours to read or replay (no transcript is provided). "
        "Never edit another child's plan. When the plan is implementation-ready, report "
        "its approved revision and next executable boundary to the parent once.",
        "",
        f"Goal: {handoff.goal}",
        f"Target: project {handoff.target.project} on computer {handoff.target.computer}"
        + (f" at {handoff.target.repo_path}" if handoff.target.repo_path else ""),
        "",
        "Decisions (locked; each carries the revision that set it):",
    ]
    if handoff.decisions:
        lines.extend(
            f"- {item.id} [{item.revision}, {item.source}]: {item.statement}"
            for item in handoff.decisions
        )
    else:
        lines.append("- none bind this build")
    lines += ["", "Dependencies:"]
    if handoff.dependencies:
        lines.extend(
            f"- {item.on} ({item.kind.value}): {item.reason}" for item in handoff.dependencies
        )
    else:
        lines.append("- none")
    lines += ["", "Ownership (writable scope this build may plan changes to):"]
    if handoff.ownership:
        lines.extend(
            f"- {scope.project}: {', '.join(scope.paths)} "
            f"({'writable' if scope.writable else 'read-only'}, owner {scope.owner})"
            for scope in handoff.ownership
        )
    else:
        lines.append("- none declared; planning is read-only")
    lines += ["", "Restrictions:"]
    lines.extend(f"- {item}" for item in handoff.restrictions)
    lines += [
        "",
        f"Authority: {handoff.authority}",
        f"Expected output: {handoff.expected_output}",
        f"Parent: thread {handoff.parent.thread_id} — {handoff.parent.url}",
    ]
    text = "\n".join(lines)
    if len(text) > MAX_CHILD_BRIEF_CHARS:
        raise PlanningDiscordError(
            f"child briefing is {len(text)} characters (limit {MAX_CHILD_BRIEF_CHARS})"
        )
    return text


def render_decision_update(previous: LockedDecision, current: LockedDecision) -> str:
    """One bounded message: which decision moved, from where, and by whom."""
    text = (
        f"Decision update from the parent ({current.source}): {current.id} changed "
        f"{previous.revision} → {current.revision}.\n"
        f"Now: {current.statement}\n"
        f"Was: {previous.statement}\n"
        f"Acknowledge decision {current.id} at revision {current.revision} when your plan "
        "reflects it."
    )
    return _clip(text, MAX_MESSAGE_CHARS)


def render_parent_status(parent_thread_id: int, records: Iterable[ChildRecord]) -> str:
    """The parent's dashboard: one line per child, from summaries only."""
    summary = summarize_children(parent_thread_id, records)
    if not summary.children:
        return "No child planning threads."
    count = len(summary.children)
    lines = [f"{count} child planning thread{'s' if count != 1 else ''}:"]
    for row in summary.children:
        link = row.url or "(thread not created yet)"
        line = f"- {row.state.value} · {_clip(row.goal, 120)} · {link}"
        if row.dependencies:
            line += f" · depends on {', '.join(row.dependencies)}"
        if row.blocker:
            line += f" · blocker: {_clip(row.blocker, 160)}"
        lines.append(line)
    return _clip("\n".join(lines), MAX_MESSAGE_CHARS)


def _thread_name(handoff: ChildHandoff) -> str:
    return _clip(f"plan · {handoff.goal}", MAX_THREAD_NAME_CHARS)


# ---- the adapter -------------------------------------------------------------


class ChildPlanningThreads:
    """Create, update and summarize linked child planning threads."""

    def __init__(self, *, ledger: ChildLedger, transport: PlanningTransport) -> None:
        self._ledger = ledger
        self._transport = transport

    async def _require(self, identity: SplitIdentity) -> ChildRecord:
        record = await self._ledger.get(identity)
        if record is None:
            raise PlanningDiscordError(f"no child planning record for {identity.key}")
        return record

    async def open_child(self, handoff: ChildHandoff, *, now: str | None = None) -> ChildRecord:
        """Create the child for this handoff, or return the one that already exists.

        Idempotent on the split identity (parent thread + goal). A record with
        a thread is returned as is; a record still ``creating`` is reconciled
        through the transport's correlation lookup before anything is spawned.
        """
        if not isinstance(handoff, ChildHandoff):
            raise PlanningDiscordError("open_child expects a ChildHandoff")
        at = now or _now()
        identity = SplitIdentity.from_goal(handoff.parent.thread_id, handoff.goal)
        record = await self._ledger.get(identity)
        if record is not None and record.thread_id is not None:
            return record
        if record is None:
            record = ChildRecord(
                identity=identity,
                handoff=handoff,
                state=PlanningState.CREATING,
                created_at=at,
                updated_at=at,
            )
            await self._ledger.put(record)  # intent, before the side effect
        elif record.state is PlanningState.CREATING:
            # A spawn went out and its answer never came back. Ask, don't retry.
            try:
                found = await self._transport.find_thread(identity.key)
            except Exception as exc:
                blocked = record.with_state(
                    PlanningState.BLOCKED,
                    at=at,
                    blocker=(
                        "child thread creation was interrupted and the lookup by "
                        f"correlation {identity.key} failed ({type(exc).__name__}); "
                        "reconcile by hand before retrying"
                    ),
                )
                await self._ledger.put(blocked)
                return blocked
            if found is not None:
                return await self._attach(record, found, at)
        elif record.state is PlanningState.BLOCKED:
            return record
        else:
            record = replace(record, state=PlanningState.CREATING, updated_at=at)
            await self._ledger.put(record)

        try:
            thread_id = await self._transport.spawn_thread(
                parent_thread_id=identity.parent_thread_id,
                name=_thread_name(record.handoff),
                prompt=render_child_briefing(record.handoff),
                correlation_id=identity.key,
            )
        except Exception as exc:
            # The record stays `creating` with no thread: the next call reconciles.
            raise PlanningDiscordError(
                f"child thread creation for {identity.key} is uncertain: {exc}"
            ) from exc
        return await self._attach(record, thread_id, at)

    async def _attach(self, record: ChildRecord, thread_id: int, at: str) -> ChildRecord:
        try:
            with_thread = record.with_thread(thread_id, at=at)
        except PlanningModelError as exc:
            raise PlanningDiscordError(str(exc)) from exc
        await self._ledger.put(with_thread)
        planning = with_thread.with_state(PlanningState.PLANNING, at=at)
        await self._ledger.put(planning)
        await self._transport.post(
            planning.parent_thread_id,
            _clip(
                f"Child planning thread opened for '{planning.handoff.goal}': {planning.url} "
                f"(thread {thread_id}). It plans one build and reports back here.",
                MAX_MESSAGE_CHARS,
            ),
        )
        return planning

    async def update_decision(
        self, parent_thread_id: int, decision: LockedDecision, *, now: str | None = None
    ) -> tuple[ChildRecord, ...]:
        """Send one bounded update to every unfinished child that carries this decision."""
        if not isinstance(decision, LockedDecision):
            raise PlanningDiscordError("update_decision expects a LockedDecision")
        at = now or _now()
        updated: list[ChildRecord] = []
        for record in await self._ledger.children_of(parent_thread_id):
            if record.state in TERMINAL_PLANNING_STATES or record.thread_id is None:
                continue
            previous = record.handoff.decision(decision.id)
            if previous is None or previous.revision == decision.revision:
                continue
            decisions = tuple(
                decision if item.id == decision.id else item for item in record.handoff.decisions
            )
            handoff = replace(record.handoff, decisions=decisions)
            changed = replace(record, handoff=handoff, updated_at=at)
            await self._ledger.put(changed)
            await self._transport.post(record.thread_id, render_decision_update(previous, decision))
            updated.append(changed)
        return tuple(updated)

    async def acknowledge(self, identity: SplitIdentity, ack: DecisionAck) -> ChildRecord:
        """Record a child's acknowledgement; repeating it changes nothing."""
        record = await self._require(identity)
        try:
            acknowledged = record.acknowledge(ack)
        except PlanningModelError as exc:
            raise PlanningDiscordError(str(exc)) from exc
        if acknowledged is not record:
            await self._ledger.put(acknowledged)
        return acknowledged

    async def report_plan_ready(
        self,
        identity: SplitIdentity,
        *,
        plan_revision: str,
        next_boundary: str,
        now: str | None = None,
    ) -> bool:
        """Tell the parent the child's plan is ready. True when a report was posted.

        The same revision reported twice posts once: the record already says
        ``plan-ready`` at that revision, so the retry is a no-op.
        """
        record = await self._require(identity)
        if record.thread_id is None:
            raise PlanningDiscordError(f"child {identity.key} has no thread yet")
        if record.state is PlanningState.PLAN_READY and record.plan_revision == plan_revision:
            return False
        at = now or _now()
        try:
            ready = record.with_state(PlanningState.PLAN_READY, at=at, plan_revision=plan_revision)
        except PlanningModelError as exc:
            raise PlanningDiscordError(str(exc)) from exc
        await self._ledger.put(ready)
        await self._transport.post(
            ready.parent_thread_id,
            _clip(
                f"Child plan ready: '{ready.handoff.goal}' (thread {ready.thread_id}, "
                f"{ready.url}) approved revision {plan_revision}; next executable boundary: "
                f"{next_boundary}.",
                MAX_MESSAGE_CHARS,
            ),
        )
        return True

    async def parent_status(self, parent_thread_id: int) -> str:
        return render_parent_status(
            parent_thread_id, await self._ledger.children_of(parent_thread_id)
        )


class ApiPlanningTransport:
    """The shipped transport: Ebi's localhost control plane.

    ``api`` is an async callable ``(method, endpoint, body) -> dict`` such as
    ``coordinator.EbiAPI``. The spawn carries ``parent_thread_id`` and
    ``correlation_id`` so the server records the link and answers the lookup
    after a crash (see ``/api/correlations/<id>``).
    """

    def __init__(self, api: Any, *, channel_id: int | None = None) -> None:
        self._api = api
        self._channel_id = channel_id

    async def spawn_thread(
        self, *, parent_thread_id: int, name: str, prompt: str, correlation_id: str
    ) -> int:
        body: dict[str, Any] = {
            "prompt": prompt,
            "thread_name": name,
            "auto_start": True,
            "parent_thread_id": parent_thread_id,
            "correlation_id": correlation_id,
        }
        if self._channel_id is not None:
            body["channel_id"] = self._channel_id
        response = await self._api("POST", "/api/spawn", body)
        thread_id = str(response.get("thread_id", ""))
        if not thread_id.isdigit():
            raise PlanningDiscordError("spawn returned an invalid thread ID")
        return int(thread_id)

    async def find_thread(self, correlation_id: str) -> int | None:
        try:
            response = await self._api("GET", f"/api/correlations/{correlation_id}", None)
        except Exception as exc:
            if "404" in str(exc):
                return None
            raise
        thread_id = str(response.get("thread_id", ""))
        return int(thread_id) if thread_id.isdigit() else None

    async def post(self, thread_id: int, text: str) -> None:
        await self._api(
            "POST",
            f"/api/threads/{thread_id}/message",
            {"text": text, "mode": "queue", "hop": 0},
        )
