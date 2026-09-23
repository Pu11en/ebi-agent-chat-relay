"""Close and reopen a session, without destroying it.

Stopping a turn and closing a session are different acts: `/stop` interrupts
the model and leaves everything else alone, while closing wraps the work up,
marks the record closed and archives the conversation so it can be found and
resumed later. The distinction only holds if closing never deletes — not the
session row, not the working folder, not the thread — and that is what this
service exists to guarantee in one place rather than in every frontend.

Three deliberate constraints shape it:

* **Nothing is destroyed.** The service can archive and unarchive. It has no
  path to delete or lock, so no caller can discover one.
* **Authority is inherited, never minted.** Every close carries a typed
  :class:`CloseAuthorization` naming a person or a preauthorized workflow. A
  model's own sense that it has finished is not representable here, so an
  agent closing a session is always traceable to someone who allowed it.
* **A running turn is waited out, not killed.** A close asked for mid-turn is
  written down as `closing` before anything is acknowledged, so the wrap-up
  still happens once the turn ends — even if the bot restarts in between, via
  :meth:`SessionLifecycleService.reconcile_pending_closes`.

The service talks to the outside world through small protocols instead of
Discord objects, so Teams, the API, or a test can drive the same lifecycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from claude_code_core.session_repo import (
    CloseAuthority,
    SessionRecord,
    SessionRepository,
)

from .backend_settings import session_is_resumable

logger = logging.getLogger(__name__)


class CloseAuthorityError(PermissionError):
    """A close was attempted without inherited authority.

    Raised rather than returned: an unauthorized close is a programming error
    at the call site, not an outcome a user should see reported back.
    """


@dataclass(frozen=True, slots=True)
class CloseAuthorization:
    """Who allowed this close, in a form that cannot be faked.

    ``actor`` is the audit string for whoever carries the authority — a user
    id for the two human sources, the operator of a workflow otherwise. The
    constructors below are the intended entry points; constructing the
    dataclass directly is still validated so a stray string cannot slip in.
    """

    source: CloseAuthority
    actor: str
    workflow_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, CloseAuthority):
            raise CloseAuthorityError(
                f"Close authority must be a CloseAuthority, got {self.source!r}. "
                "A model deciding it has finished is not an authority."
            )
        if not self.actor or not self.actor.strip():
            raise CloseAuthorityError("A close authority must name the actor it came from.")
        if self.source is CloseAuthority.WORKFLOW_CLOSE_ON_DONE:
            if not self.workflow_id or not self.workflow_id.strip():
                raise CloseAuthorityError("A workflow close must name the workflow that grants it.")
        elif self.workflow_id is not None:
            raise CloseAuthorityError(
                "Only a close-on-done workflow authority carries a workflow id."
            )

    @classmethod
    def from_interaction(cls, actor: str | int) -> CloseAuthorization:
        """A person pressed Close or ran `/close` themselves."""
        return cls(source=CloseAuthority.DIRECT_INTERACTION, actor=str(actor))

    @classmethod
    def from_user_instruction(cls, actor: str | int) -> CloseAuthorization:
        """The current request explicitly told the agent to close when done."""
        return cls(source=CloseAuthority.USER_INSTRUCTION, actor=str(actor))

    @classmethod
    def from_workflow(cls, workflow_id: str, *, close_on_done: bool) -> CloseAuthorization:
        """A preauthorized workflow finished with close-on-done enabled.

        ``close_on_done`` is required and checked here so the caller cannot
        pass its own configuration flag through unexamined.
        """
        if not close_on_done:
            raise CloseAuthorityError(
                f"Workflow {workflow_id!r} is not configured to close on done."
            )
        return cls(
            source=CloseAuthority.WORKFLOW_CLOSE_ON_DONE,
            actor=f"workflow:{workflow_id.strip()}",
            workflow_id=workflow_id.strip(),
        )

    @property
    def is_human(self) -> bool:
        """Did a person ask for this close, directly or in their request?"""
        return self.source in (
            CloseAuthority.DIRECT_INTERACTION,
            CloseAuthority.USER_INSTRUCTION,
        )

    @property
    def is_workflow(self) -> bool:
        return self.source is CloseAuthority.WORKFLOW_CLOSE_ON_DONE


class CloseState(Enum):
    """What a close request actually did."""

    #: The wrap-up was written and the conversation archived.
    CLOSED = "closed"
    #: A turn is still running; the request is stored and will finish later.
    PENDING = "pending"
    #: The session was already closed; nothing was rewritten.
    ALREADY_CLOSED = "already_closed"
    #: Completion was asked for but no close had been requested.
    NOT_REQUESTED = "not_requested"
    #: No session is bound to this thread.
    NO_SESSION = "no_session"


class ReopenState(Enum):
    REOPENED = "reopened"
    ALREADY_OPEN = "already_open"
    NO_SESSION = "no_session"


@dataclass(frozen=True, slots=True)
class CloseOutcome:
    """The result of a close, phrased so a frontend can report it verbatim."""

    state: CloseState
    record: SessionRecord | None = None
    wrap_up: str | None = None
    archived: bool = False

    @property
    def is_closed(self) -> bool:
        return self.state is CloseState.CLOSED

    @property
    def is_pending(self) -> bool:
        return self.state is CloseState.PENDING


@dataclass(frozen=True, slots=True)
class ReopenOutcome:
    """The result of a reopen, including whether the old session id survives.

    ``resume_session_id`` is ``None`` when the harness in play cannot resume
    the stored id. Handing it over anyway is the failure mode this field
    exists to prevent: the record keeps its id, the caller just must not pass
    it to an incompatible CLI.
    """

    state: ReopenState
    record: SessionRecord | None = None
    unarchived: bool = False
    requires_fresh_session: bool = False
    resume_session_id: str | None = None

    @property
    def is_reopened(self) -> bool:
        return self.state is ReopenState.REOPENED


class ConversationSurface(Protocol):
    """The frontend's archive box. Note what it cannot do: lock, or delete."""

    async def archive(self, thread_id: int) -> bool: ...

    async def unarchive(self, thread_id: int) -> bool: ...


class TurnActivity(Protocol):
    """Whether a model turn is in flight, and how to wait one out."""

    async def is_active(self, thread_id: int) -> bool: ...

    async def wait_until_idle(self, thread_id: int) -> None: ...


class WrapUpWriter(Protocol):
    """Writes the closing summary. May return ``None`` when it cannot."""

    async def summarize(self, record: SessionRecord) -> str | None: ...


def deterministic_wrap_up(record: SessionRecord) -> str:
    """The summary used when no model-assisted wrap-up can be produced.

    Built only from stored fields, so it always exists and always says
    something true. A close must never fail for want of a sentence.
    """
    parts = [f"Closed without a model wrap-up (session `{record.session_id or 'unknown'}`)."]
    if record.summary and record.summary.strip():
        parts.append(record.summary.strip())
    parts.append(f"Folder: {record.working_dir or 'none bound'}.")
    if record.backend:
        parts.append(f"Backend: {record.backend}.")
    if record.model:
        parts.append(f"Model: {record.model}.")
    parts.append(f"Last active {record.last_used_at}.")
    return " ".join(parts)


class SessionLifecycleService:
    """Frontend-neutral close and reopen over :class:`SessionRepository`.

    Every optional collaborator has a safe absence: no surface means nothing
    is archived, no turn tracker means sessions are treated as idle, and no
    wrap-up writer means the deterministic summary is always used.
    """

    def __init__(
        self,
        repo: SessionRepository,
        *,
        surface: ConversationSurface | None = None,
        turns: TurnActivity | None = None,
        wrap_up_writer: WrapUpWriter | None = None,
    ) -> None:
        self.repo = repo
        self.surface = surface
        self.turns = turns
        self.wrap_up_writer = wrap_up_writer

    async def close(self, thread_id: int, authorization: CloseAuthorization) -> CloseOutcome:
        """Close ``thread_id``'s session, waiting out any running turn.

        Idle sessions close immediately. A session with a turn in flight gets
        a durable `closing` marker and comes back ``PENDING`` — the caller
        acknowledges that, and :meth:`complete_pending_close` finishes it when
        the turn ends. Closing an already-closed session reports its state
        without touching the stored wrap-up.
        """
        _require_authority(authorization)

        record = await self.repo.get(thread_id)
        if record is None:
            return CloseOutcome(state=CloseState.NO_SESSION)
        if record.is_closed:
            return CloseOutcome(
                state=CloseState.ALREADY_CLOSED,
                record=record,
                wrap_up=record.wrap_up,
            )

        # Persist the request before doing anything else: a crash between here
        # and the wrap-up must leave evidence that a close was asked for.
        requested = await self.repo.request_close(thread_id, authorization.source)
        if requested is None:  # pragma: no cover - the row was removed underneath us
            return CloseOutcome(state=CloseState.NO_SESSION)

        if await self._is_active(thread_id):
            logger.info("Close requested for thread %s while a turn is running", thread_id)
            return CloseOutcome(state=CloseState.PENDING, record=requested)

        return await self._finalize(requested)

    async def close_when_idle(
        self, thread_id: int, authorization: CloseAuthorization
    ) -> CloseOutcome:
        """Close, blocking until any running turn finishes on its own.

        For callers that can afford to wait (a workflow closing itself down).
        Interactive paths should use :meth:`close` and let the run-finalization
        hook complete the pending close instead of holding an interaction open.
        """
        outcome = await self.close(thread_id, authorization)
        if not outcome.is_pending:
            return outcome
        if self.turns is not None:
            await self.turns.wait_until_idle(thread_id)
        return await self.complete_pending_close(thread_id)

    async def complete_pending_close(self, thread_id: int) -> CloseOutcome:
        """Finish a close that was requested while a turn was running.

        Called from run finalization and from startup reconciliation, so it is
        idempotent in both directions: nothing requested is ``NOT_REQUESTED``,
        already finished is ``ALREADY_CLOSED``, and a turn that is somehow
        still running stays ``PENDING`` for the next attempt.
        """
        record = await self.repo.get(thread_id)
        if record is None:
            return CloseOutcome(state=CloseState.NO_SESSION)
        if record.is_closed:
            return CloseOutcome(
                state=CloseState.ALREADY_CLOSED,
                record=record,
                wrap_up=record.wrap_up,
            )
        if not record.close_pending:
            return CloseOutcome(state=CloseState.NOT_REQUESTED, record=record)
        if await self._is_active(thread_id):
            return CloseOutcome(state=CloseState.PENDING, record=record)
        return await self._finalize(record)

    async def reconcile_pending_closes(self, limit: int = 50) -> list[CloseOutcome]:
        """Finish every close interrupted by a restart.

        Run on startup: each stored `closing` row is a turn that ended without
        its wrap-up, and leaving it there would strand the session between
        states forever.
        """
        pending = await self.repo.list_pending_closes(limit=limit)
        return [await self.complete_pending_close(record.thread_id) for record in pending]

    async def reopen(self, thread_id: int, *, backend: str | None = None) -> ReopenOutcome:
        """Return a closed session to open and unarchive its conversation.

        Pass ``backend`` when the harness may have changed while the session
        was closed: the outcome then says whether the stored session id can
        still be resumed, rather than letting an incompatible id reach a CLI.
        """
        record = await self.repo.get(thread_id)
        if record is None:
            return ReopenOutcome(state=ReopenState.NO_SESSION)
        if record.is_open:
            needs_fresh, resume_id = _resume_plan(record, backend)
            return ReopenOutcome(
                state=ReopenState.ALREADY_OPEN,
                record=record,
                requires_fresh_session=needs_fresh,
                resume_session_id=resume_id,
            )

        reopened = await self.repo.reopen(thread_id)
        if reopened is None:  # pragma: no cover - the row was removed underneath us
            return ReopenOutcome(state=ReopenState.NO_SESSION)

        unarchived = False
        if self.surface is not None:
            unarchived = bool(await self.surface.unarchive(thread_id))

        needs_fresh, resume_id = _resume_plan(reopened, backend)
        return ReopenOutcome(
            state=ReopenState.REOPENED,
            record=reopened,
            unarchived=unarchived,
            requires_fresh_session=needs_fresh,
            resume_session_id=resume_id,
        )

    async def _finalize(self, record: SessionRecord) -> CloseOutcome:
        """Write the wrap-up, mark the record closed, archive the thread."""
        wrap_up = await self._write_wrap_up(record)
        closed = await self.repo.mark_closed(record.thread_id, wrap_up)
        archived = False
        if self.surface is not None:
            archived = bool(await self.surface.archive(record.thread_id))
        logger.info("Closed session for thread %s (archived=%s)", record.thread_id, archived)
        return CloseOutcome(
            state=CloseState.CLOSED,
            record=closed,
            wrap_up=(closed.wrap_up if closed is not None else wrap_up),
            archived=archived,
        )

    async def _write_wrap_up(self, record: SessionRecord) -> str:
        """Ask the writer for a summary, falling back rather than failing."""
        if self.wrap_up_writer is None:
            return deterministic_wrap_up(record)
        try:
            written = await self.wrap_up_writer.summarize(record)
        except Exception:
            logger.warning(
                "Wrap-up failed for thread %s; closing with a stored summary",
                record.thread_id,
                exc_info=True,
            )
            return deterministic_wrap_up(record)
        if written is None or not written.strip():
            return deterministic_wrap_up(record)
        return written

    async def _is_active(self, thread_id: int) -> bool:
        if self.turns is None:
            return False
        return bool(await self.turns.is_active(thread_id))


def close_outcome_text(outcome: CloseOutcome) -> str:
    """One plain sentence per close outcome, safe for any frontend to send verbatim."""
    if outcome.state is CloseState.CLOSED:
        where = " and archived" if outcome.archived else ""
        text = f"Session closed{where}. Reopen it any time from **Sessions**."
        if outcome.wrap_up:
            text += f"\n> {outcome.wrap_up[:900]}"
        return text
    if outcome.state is CloseState.PENDING:
        return (
            "A turn is still running. The session will wrap up and close by itself "
            "when it finishes; nothing was interrupted."
        )
    if outcome.state is CloseState.ALREADY_CLOSED:
        return "This session is already closed. Reopen it from **Sessions** to continue."
    if outcome.state is CloseState.NOT_REQUESTED:
        return "No close was requested for this session; nothing changed."
    return "No session is bound to this thread, so there is nothing to close."


def _require_authority(authorization: CloseAuthorization) -> None:
    """Fail closed on anything that is not a typed authorization."""
    if not isinstance(authorization, CloseAuthorization):
        raise CloseAuthorityError(
            "Closing a session needs a CloseAuthorization naming a person or a "
            f"preauthorized workflow, got {authorization!r}."
        )


def _resume_plan(record: SessionRecord, backend: str | None) -> tuple[bool, str | None]:
    """Whether ``backend`` needs a fresh session, and the id it may resume."""
    if backend is None or session_is_resumable(record.backend, backend):
        return False, record.session_id
    return True, None
