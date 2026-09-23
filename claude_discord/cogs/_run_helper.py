"""Shared helper for running Claude Code CLI and streaming results to a Discord thread.

Both ClaudeChatCog and SkillCommandCog need to run Claude and post results.
This module is the thin orchestration layer that:
1. Builds ephemeral system context (lounge + concurrency notice) via --append-system-prompt
2. Delegates event processing to EventProcessor
3. Handles AskUserQuestion flow (recursive resume)

Primary API:
    run_claude_with_config(config: RunConfig) -> str | None

Legacy shim:
    run_claude_in_thread(thread, runner, repo, prompt, session_id, ...) -> str | None
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import discord

from claude_code_core.capacity_policy import (
    FallbackTarget,
    RecoveryPhase,
    RecoveryStatus,
    parse_fallback_chain,
)
from claude_code_core.frontend import ActivitySpec, Notice, NoticeLevel, StatusKind
from claude_code_core.gowork_admission import AdmissionController, Reservation, SlotKind
from claude_code_core.gowork_capacity import CapacityDecision, CapacityPolicy
from claude_code_core.gowork_friction import FrictionEvent, append_friction
from claude_code_core.gowork_resources import WorkerPeaks
from claude_code_core.task_loop import MAX_PARALLEL

from ..capacity_recovery import (
    AttemptResult,
    AttemptTarget,
    CapacityRecoveryCoordinator,
    TurnResult,
    TurnSubmission,
)
from ..catalog_query import build_catalog_hint
from ..discord_ui.ask_handler import collect_ask_answers
from ..discord_ui.embeds import error_embed, timeout_embed
from ..lounge import build_lounge_prompt
from ..pr_completion_gate import GitHubPrCompletionGate, build_completion_prompt
from .event_processor import EventProcessor, _backend_name_from_runner
from .run_config import RunConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global session slot limiter
# ---------------------------------------------------------------------------
_global_semaphore: asyncio.Semaphore | None = None
_max_concurrent: int = 10
_pr_completion_gate: GitHubPrCompletionGate | None = None
# The adaptive path (no explicit limit): one admission controller for every
# build, a policy that sizes it from measured resources, and the probe it reads.
_global_admission: AdmissionController | None = None
_capacity_policy: CapacityPolicy | None = None
_resource_probe: Any = None
_worker_peaks: WorkerPeaks = WorkerPeaks()
_friction_path: Path | None = None
# Model-capacity recovery: one coordinator for every run, the computer-wide
# fallback chain (CCDB_CAPACITY_FALLBACK) and the factory that builds a runner
# for a fallback target. None means "one attempt, as before".
_capacity_coordinator: CapacityRecoveryCoordinator | None = None
_capacity_fallback_chain: tuple[FallbackTarget, ...] = ()
_capacity_backend_factory: Any = None


def configure_capacity_recovery(
    coordinator: CapacityRecoveryCoordinator | None,
    *,
    fallback_chain: tuple[FallbackTarget, ...] | None = None,
    backend_factory: Any = None,
) -> None:
    """Install the shared recovery coordinator for every run in this process.

    ``fallback_chain`` defaults to ``CCDB_CAPACITY_FALLBACK`` (``"codex:gpt-5.5,
    claude"``) with computer authority; a run's own ``RunConfig.fallback_chain``
    wins when set. Without a ``backend_factory`` no switch can be performed, so
    the chain is ignored and the turn only waits.
    """
    global _capacity_coordinator, _capacity_fallback_chain, _capacity_backend_factory  # noqa: PLW0603
    _capacity_coordinator = coordinator
    _capacity_fallback_chain = (
        parse_fallback_chain(os.environ.get("CCDB_CAPACITY_FALLBACK"), authority="computer")
        if fallback_chain is None
        else tuple(fallback_chain)
    )
    _capacity_backend_factory = backend_factory


def capacity_coordinator() -> CapacityRecoveryCoordinator | None:
    """The installed coordinator, for cogs that drive their own attempts."""
    return _capacity_coordinator


def capacity_fallback_chain() -> tuple[FallbackTarget, ...]:
    """The computer-wide fallback chain captured at configuration time."""
    return _capacity_fallback_chain


def configure_session_limit(max_concurrent: int) -> None:
    """Set an explicit process-wide concurrent session limit (a fixed semaphore).

    Called from ``setup_bridge()`` when the operator configured a number.  Every
    ``run_claude_with_config()`` call — whichever Cog makes it — honours it, and
    the adaptive path is switched off.
    """
    global _global_semaphore, _max_concurrent  # noqa: PLW0603
    if type(max_concurrent) is not int or max_concurrent < 1:
        raise ValueError("Session capacity must be a positive integer")
    _max_concurrent = max_concurrent
    _global_semaphore = asyncio.Semaphore(max_concurrent)


def configure_adaptive_limit(
    *,
    controller: AdmissionController | None,
    policy: CapacityPolicy | None,
    probe: Any = None,
    friction_path: Path | None = None,
) -> None:
    """Use measured capacity instead of a fixed number (``None`` for all switches it off).

    The controller admits runs, the policy resizes it on every ``tick_capacity()``
    from the probe's snapshot, and an explicit ``configure_session_limit()`` still
    wins because the fixed semaphore is checked first.
    """
    global _global_admission, _capacity_policy, _resource_probe, _friction_path  # noqa: PLW0603
    _global_admission = controller
    _capacity_policy = policy
    _resource_probe = probe
    _friction_path = friction_path
    if controller is not None and policy is not None:
        controller.set_capacity(policy.capacity)


def tick_capacity() -> CapacityDecision | None:
    """Re-measure the host and resize the admission controller (adaptive path only)."""
    if _global_admission is None or _capacity_policy is None or _resource_probe is None:
        return None
    held = _global_admission.snapshot().held
    decision = _capacity_policy.decide(_resource_probe.sample(), _worker_peaks, held=held)
    _global_admission.set_capacity(decision.capacity)
    if decision.pause_starts and _friction_path is not None:
        append_friction(
            _friction_path,
            FrictionEvent(
                kind="capacity",
                build_id="host",
                task_id="",
                attempt_id="",
                plan_id="",
                plan_version=0,
                detail=decision.reason,
            ),
        )
    return decision


async def run_capacity_ticks(interval_seconds: float) -> None:
    """Background loop for ``setup_bridge()``: keep the adaptive capacity current."""
    while True:
        try:
            decision = tick_capacity()
            if decision is not None and decision.pause_starts:
                logger.info("gowork capacity: %s", decision.reason)
        except Exception:  # never let a measurement error stop the loop
            logger.warning("capacity tick failed", exc_info=True)
        await asyncio.sleep(interval_seconds)


def session_limit() -> int | None:
    """Process-wide capacity right now, or None for unconfigured embedded use."""
    if _global_semaphore is not None:
        return _max_concurrent
    if _global_admission is not None:
        return _global_admission.snapshot().capacity
    return None


def parallel_limit() -> int:
    """How many Go Work steps may run side by side (the loop's old fixed ten)."""
    return session_limit() or MAX_PARALLEL


def configure_pr_completion_gate(owner: str | None) -> None:
    """Enable the owner-PR completion gate, or disable it with an empty owner."""
    global _pr_completion_gate  # noqa: PLW0603
    _pr_completion_gate = GitHubPrCompletionGate(owner) if owner else None


# ---------------------------------------------------------------------------
# ScheduleWakeup → one-shot scheduled task bridge
# ---------------------------------------------------------------------------
# Harness-driven models (e.g. /loop dynamic pacing) call a ScheduleWakeup tool
# expecting to be re-invoked after a delay.  In claude -p mode no harness
# exists, so ccdb honours the request by registering a one-shot task in the
# SQLite scheduler that resumes the session in the same thread.
_wakeup_task_repo = None

# Bounds mirror the harness runtime clamp for ScheduleWakeup.delaySeconds.
_WAKEUP_MIN_DELAY_SECONDS = 60
_WAKEUP_MAX_DELAY_SECONDS = 3600

# Sentinel emitted for autonomous loops; meaningless outside the harness.
_AUTONOMOUS_LOOP_SENTINEL = "<<autonomous-loop-dynamic>>"
_AUTONOMOUS_LOOP_PROMPT = (
    "Continue your autonomous /loop iteration based on the previous context. "
    "If the loop's goal is complete, summarize and stop scheduling wakeups."
)


def configure_wakeup_scheduler(task_repo) -> None:
    """Set the process-wide TaskRepository used to honour ScheduleWakeup calls.

    Called once from ``setup_bridge()`` when the scheduler is enabled.  When
    unset, ScheduleWakeup tool calls are logged and ignored.
    """
    global _wakeup_task_repo  # noqa: PLW0603
    _wakeup_task_repo = task_repo


# Max characters for tool result display (re-exported for backward compat).
TOOL_RESULT_MAX_CHARS = 3000

# Injected via --append-system-prompt after context compaction to prevent
# Claude from auto-executing "pending tasks" from the compacted summary.
_POST_COMPACT_GUARDRAIL = (
    "⚠️ POST-COMPACT GUARDRAIL (MANDATORY): Context was just compacted. "
    "You MUST follow these rules:\n"
    "1. Do NOT automatically execute any external actions "
    "(posting to Teams/Slack/Discord/email, calling external APIs, creating resources, etc.) "
    "based on 'in progress' or 'pending' tasks in the compacted context summary.\n"
    "2. Treat every such pending task as needing fresh authorization from the user.\n"
    "3. Respond ONLY to what is explicitly requested in the user's current message.\n"
    "4. If relevant, briefly mention what you were doing before compaction.\n"
    "These rules override any implied continuation in the compacted summary."
)

_TIMEOUT_PATTERN = re.compile(r"Timed out after (\d+) seconds")


def _make_error_embed(error: str) -> discord.Embed:
    """Return a timeout_embed for timeout errors, error_embed otherwise."""
    m = _TIMEOUT_PATTERN.match(error)
    if m:
        return timeout_embed(int(m.group(1)))
    return error_embed(error)


def _truncate_result(content: str) -> str:
    """Truncate tool result content for display (re-exported for backward compat)."""
    if len(content) <= TOOL_RESULT_MAX_CHARS:
        return content
    return content[:TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"


async def _build_system_context(config: RunConfig) -> str | None:
    """Build ephemeral system context from AI Lounge and concurrency notice.

    Returns a string to inject as backend-specific developer/system instructions, or
    None if no context is available. Keeping it separate from the user message prevents
    this ephemeral metadata from accumulating in session history, which would otherwise
    cause "Prompt is too long" errors over long conversations.
    """
    parts: list[str] = []

    # Layer 3: AI Lounge context (recent messages + invitation).
    if config.lounge_repo is not None:
        try:
            recent = await config.lounge_repo.get_recent(limit=10)
            lounge_context = build_lounge_prompt(
                recent, current_thread_id=config.surface.thread_key
            )
            parts.append(lounge_context)
            logger.debug("Lounge context built (%d recent message(s))", len(recent))
        except Exception:
            logger.warning("Failed to fetch lounge context — skipping", exc_info=True)

    # Layer 1 + 2: Register session and build concurrency notice.
    if config.registry is not None:
        config.registry.register(
            config.surface.thread_key, config.prompt[:100], config.runner.working_dir
        )
        others = config.registry.list_others(config.surface.thread_key)
        if not config.slim_context:
            notice = config.registry.build_concurrency_notice(config.surface.thread_key)
            parts.append(notice)
        logger.info(
            "Concurrency notice built for thread %d (%d other active session(s), dir=%s)",
            config.surface.thread_key,
            len(others),
            config.runner.working_dir or "(default)",
        )
    else:
        logger.debug(
            "No session registry — concurrency notice skipped for thread %d",
            config.surface.thread_key,
        )

    # File delivery marker: always injected so Claude knows the per-thread
    # marker name, even when it discovers the mechanism from session history
    # or CLAUDE.md rather than from an explicit "send me the file" request.
    from .event_processor import _attachment_marker_name

    wd = config.runner.working_dir or "your current working directory"
    marker = _attachment_marker_name(config.surface.thread_key)
    if config.slim_context:
        parts.append(
            "To send the person a file, append its absolute path to "
            f"{wd}/{marker} (one per line); the bot attaches it when you finish."
        )
    else:
        parts.append(
            "## File Delivery\n"
            "When you need to send files to Discord, use your Bash tool to append "
            "each file's ABSOLUTE path (one path per line, UTF-8) to:\n"
            f"  {wd}/{marker}\n"
            f"Example: `echo /absolute/path/to/file >> {wd}/{marker}`\n"
            "The bot will attach those files when this session ends.\n"
            "When local instructions require Discord attachment for a substantial "
            "written deliverable, save the final text as a Markdown file and append "
            "that file path here. Otherwise, only include files the user explicitly "
            "asked to receive.\n\n"
            "## Show documents as cards (the user never opens files)\n"
            "The user only reads Discord messages. Never mention a file, path or "
            "plan name as if they had read it. Whenever they need a document's "
            "content (a plan, summary, review, status, research), write a "
            "plain-English Markdown version for them and append its path to the "
            "file above: every `.md` listed there is shown inline in the thread as "
            "colored cards, one card per `## ` section.\n"
            "Card rules: keep every point and detail of the source; no jargon and "
            "no assumed context; start with `# Title`; use `## ` sections (about 3 "
            "to 6), `### ` sub-headings, short bullets, bold for key facts and "
            "emoji markers (✅ done, ⏳ now, ⬜ next, ⚠️ warning); no tables. Your "
            "chat reply (the cards appear right after it) gives a short summary "
            "and asks the next question."
        )

    # Project catalog: only the invocation hint, never the projects. The hint
    # points at the control plane, so it is worth injecting only when one is
    # reachable; a slim (gowork) briefing works in its own fixed copy and has
    # no project to choose.
    if not config.slim_context and getattr(config.runner, "api_port", None) is not None:
        parts.append(build_catalog_hint())

    # Post-compact guardrail: prevent auto-execution of "pending tasks" from summary.
    if config.post_compact_rerun:
        parts.append(_POST_COMPACT_GUARDRAIL)
        logger.info("Post-compact guardrail injected for thread %d", config.surface.thread_key)

    return "\n\n".join(parts) if parts else None


def _merge_system_context(base: str | None, built: str | None) -> str | None:
    """Prepend the runner's standing instruction to the per-turn context.

    ``clone(append_system_prompt=...)`` replaces rather than merges, and the
    per-turn context is never None on a real chat turn (the file-delivery
    marker is unconditional), so an operator's ``APPEND_SYSTEM_PROMPT`` would
    otherwise never reach the model. When there is no built context the result
    is None — nothing is cloned, and the runner already carries its own
    standing instruction.
    """
    if not built or not isinstance(base, str) or not base.strip():
        return built
    return f"{base.strip()}\n\n{built}"


async def _cleanup_session_worktree(config: RunConfig) -> None:
    """Remove the session worktree for this thread if it is clean.

    Runs git operations in a thread pool to avoid blocking the event loop.
    Logs the outcome but never raises — cleanup failures are non-fatal.
    """
    import asyncio

    assert config.worktree_manager is not None  # caller ensures this

    try:
        result = await asyncio.to_thread(
            config.worktree_manager.cleanup_for_thread,
            config.surface.thread_key,
        )
        if result.removed:
            logger.info(
                "Cleaned up session worktree for thread %d: %s",
                config.surface.thread_key,
                result.path,
            )
        elif result.reason == "worktree directory does not exist":
            # Normal case — Claude didn't create a worktree
            pass
        else:
            logger.warning(
                "Could not clean up worktree for thread %d (%s): %s",
                config.surface.thread_key,
                result.path,
                result.reason,
            )
            # Explain the safety decision without encouraging destructive cleanup.
            if "uncommitted changes" in result.reason:
                with contextlib.suppress(Exception):
                    await config.surface.send_notice(
                        Notice(
                            level=NoticeLevel.WARNING,
                            title="Worktree preserved",
                            body=(
                                f"`{result.path}` contains local changes or ignored files, "
                                "so it was kept safely inside its project. No immediate action "
                                "is required. Review those files before choosing to merge or "
                                "clean up the worktree."
                            ),
                        )
                    )
    except Exception:
        logger.exception(
            "Unexpected error during worktree cleanup for thread %d", config.surface.thread_key
        )


async def _schedule_wakeup(config: RunConfig, wakeup: dict) -> None:
    """Register a one-shot scheduled task for a ScheduleWakeup request.

    The task resumes the session in the same thread after the requested delay
    (clamped to the harness range).  Failures are logged and reported to the
    thread but never break the just-finished session.
    """
    repo = _wakeup_task_repo
    if repo is None:
        logger.warning(
            "ScheduleWakeup requested in thread %d but scheduler is disabled — ignoring",
            config.surface.thread_key,
        )
        return

    try:
        delay = int(wakeup.get("delaySeconds", 0))
    except (TypeError, ValueError):
        delay = 0
    delay = max(_WAKEUP_MIN_DELAY_SECONDS, min(_WAKEUP_MAX_DELAY_SECONDS, delay))

    prompt = str(wakeup.get("prompt") or "").strip()
    if not prompt or prompt == _AUTONOMOUS_LOOP_SENTINEL:
        prompt = _AUTONOMOUS_LOOP_PROMPT
    reason = str(wakeup.get("reason") or "").strip()

    name = f"wakeup-thread-{config.surface.thread_key}"
    channel_id = getattr(config.thread, "parent_id", None) or config.surface.thread_key

    try:
        # Replace any previous wakeup for this thread — last call wins.
        await repo.delete_by_name(name)
        await repo.create(
            name=name,
            prompt=prompt,
            interval_seconds=delay,
            channel_id=channel_id,
            working_dir=getattr(config.runner, "working_dir", None),
            run_immediately=False,
            thread_id=config.surface.thread_key,
            one_shot=True,
        )
    except Exception:
        logger.exception("Failed to schedule wakeup task for thread %d", config.surface.thread_key)
        with contextlib.suppress(Exception):
            await config.surface.send_notice(
                Notice(level=NoticeLevel.WARNING, body="Wakeup could not be scheduled")
            )
        return

    label = f"⏰ Wakeup scheduled in {delay}s"
    if reason:
        label += f" — {reason}"
    logger.info("Wakeup scheduled for thread %d in %ds", config.surface.thread_key, delay)
    with contextlib.suppress(discord.HTTPException):
        await config.surface.send_notice(Notice(level=NoticeLevel.SUBTLE, body=label))


async def _get_pr_completion_prompt(
    config: RunConfig,
    *,
    session_id: str | None,
    final_error: str | None,
) -> str | None:
    """Return one automatic continuation prompt for owner PRs left open.

    GitHub availability must not turn a successful model response into a failed
    Discord turn, so lookup failures are visible but fail open. The rerun flag
    provides a hard one-continuation limit when a PR is genuinely blocked.
    """
    gate = _pr_completion_gate
    if (
        gate is None
        or config.pr_completion_gate_rerun
        or session_id is None
        or final_error is not None
    ):
        return None

    try:
        prs = await gate.find_for_thread(config.surface.thread_key)
    except Exception:
        logger.warning(
            "PR completion gate unavailable for thread %d",
            config.surface.thread_key,
            exc_info=True,
        )
        with contextlib.suppress(Exception):
            await config.surface.send_notice(
                Notice(
                    level=NoticeLevel.WARNING,
                    title="PR completion gate unavailable",
                    body=(
                        "GitHub could not be checked; this turn is being returned "
                        "without enforcement."
                    ),
                )
            )
        return None

    if not prs:
        return None

    with contextlib.suppress(Exception):
        await config.surface.send_notice(
            Notice(
                level=NoticeLevel.WARNING,
                title="Open owner PR detected — continuing",
                body=(
                    f"{len(prs)} non-draft PR(s) from session/{config.surface.thread_key} "
                    "are still open. The same agent will finish or report a concrete blocker."
                ),
            )
        )
    return build_completion_prompt(prs)


async def run_claude_with_config(config: RunConfig) -> str | None:
    """Execute Claude Code CLI and stream results to a Discord thread.

    This is the primary entry point. All Cogs should create a RunConfig and
    pass it here, rather than using the legacy run_claude_in_thread() shim.

    Returns:
        The final session_id, or None if the run failed.
    """
    working_dir = getattr(config.runner, "working_dir", None)
    if config.repo is not None and isinstance(working_dir, str) and working_dir:
        record = await config.repo.ensure_working_dir(
            thread_id=config.surface.thread_key,
            working_dir=working_dir,
            origin=config.session_origin,
        )
        if record.working_dir:
            config.runner.working_dir = record.working_dir

    system_context = _merge_system_context(
        getattr(config.runner, "append_system_prompt", None),
        await _build_system_context(config),
    )
    runner = (
        config.runner.clone(append_system_prompt=system_context)
        if system_context
        else config.runner
    )
    # Set the per-invocation images directly: they are resolved after the
    # runner was built, so clone() (which carries the old runner's images)
    # cannot be the channel for them.
    if config.images:
        runner.images = config.images

    # Keep stop_view in sync with the runner that will own the live subprocess.
    # When system_context is present a fresh clone is created above, making the
    # original config.runner a "dead" runner with no process.  Without this
    # update the Stop button would send SIGINT to that dead runner and have no
    # effect.  See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/174
    if runner is not config.runner:
        if config.stop_view is not None:
            config.stop_view.update_runner(runner)

        # Update config.runner to point to the clone so that EventProcessor
        # calls interrupt() on the runner that actually owns the subprocess.
        # Without this, compact_boundary and AskUserQuestion interrupt the
        # original (process-less) runner — a no-op that leaves Claude running
        # invisibly.  See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/306
        config = replace(config, runner=runner)

    coordinator = _capacity_coordinator if config.capacity_recovery else None
    if coordinator is None:
        config, processor, failure = await _run_one_attempt(config)
        turn: TurnResult | None = None
    else:
        config, recovered, failure, turn = await _run_recovered_turn(config, coordinator)
        if recovered is None:
            # The turn was already taken (a duplicate resume): nothing ran here.
            return config.session_id
        processor = recovered
    if failure is not None:
        await _emit_result_sink(config, None, failure)
        return processor.session_id
    if turn is not None and not turn.accepted and turn.kind != "duplicate":
        # Recovery stopped: exhausted, needs the user, or ambiguous. The status
        # line already said why; the sink gets the last error so callers can
        # tell a failed turn from an empty answer.
        await _emit_result_sink(config, None, processor.final_error or "capacity recovery stopped")
        return processor.session_id
    return await _finish_turn(config, processor)


async def _run_one_attempt(
    config: RunConfig,
) -> tuple[RunConfig, EventProcessor, str | None]:
    """One complete backend attempt under relay admission.

    Returns the (possibly replaced) config, the processor that saw the stream,
    and the exception text when the run raised — the raise is already reported
    to the surface here, so callers only route it to the result sink.
    """
    runner = config.runner
    processor = EventProcessor(config)
    failure: str | None = None

    # --- Session slot limiter: an explicit semaphore, else adaptive admission ---
    sem = _global_semaphore
    admission = _global_admission if sem is None else None
    reservation: Reservation | None = None
    acquired = False
    try:
        if config.registry is not None:
            config.registry.update(config.surface.thread_key, execution_state="queued")
        if config.stop_view is not None:
            config.stop_view.set_queued_task(asyncio.current_task())
            await config.stop_view.set_label("Queued — waiting for capacity")
        if config.status is not None:
            await config.status.set_queued()
        if sem is not None and sem.locked():
            with contextlib.suppress(Exception):
                await config.surface.send_notice(
                    Notice(
                        level=NoticeLevel.SUBTLE,
                        body=f"⏳ Waiting for capacity ({_max_concurrent} max sessions running)",
                    )
                )
        if sem is not None:
            await sem.acquire()
            acquired = True
        elif admission is not None:
            kind: SlotKind = config.slot_kind if config.slot_kind in ("task", "review") else "chat"  # type: ignore[assignment]
            reservation = admission.reserve(
                kind,
                config.slot_build_id or f"thread-{config.surface.thread_key}",
                unblocks=config.slot_unblocks,
            )
            if kind != "chat" and not admission.has_room(kind):
                with contextlib.suppress(Exception):
                    snapshot = admission.snapshot()
                    await config.surface.send_notice(
                        Notice(
                            level=NoticeLevel.SUBTLE,
                            body=(
                                f"⏳ Waiting for capacity ({snapshot.held} of "
                                f"{snapshot.capacity} slots in use)"
                            ),
                        )
                    )
            await reservation.acquire()
            acquired = True
        if config.registry is not None:
            config.registry.update(config.surface.thread_key, execution_state="running")
        if config.stop_view is not None:
            await config.stop_view.set_label("⏺ Session running")
        if config.status is not None:
            await config.status.set_thinking()
        if config.stop_view is not None:
            config.stop_view.set_queued_task(None)
        async for event in runner.run(config.prompt, session_id=config.session_id):
            if processor.should_drain and not event.is_complete:
                continue
            await processor.process(event)
    except Exception as exc:
        logger.exception("Error running Claude CLI for thread %d", config.surface.thread_key)
        with contextlib.suppress(Exception):
            detail = f"{type(exc).__name__}: {exc}"
            await config.surface.send_notice(
                Notice(
                    level=NoticeLevel.ERROR,
                    title="Error",
                    body=f"An unexpected error occurred.\n```\n{detail}\n```",
                )
            )
        if config.status:
            with contextlib.suppress(Exception):
                await config.status.set_error()
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        if sem is not None and acquired:
            sem.release()
        if reservation is not None:
            reservation.release()
        if config.stop_view is not None:
            config.stop_view.set_queued_task(None)
        await processor.finalize()
        if config.registry is not None:
            config.registry.unregister(config.surface.thread_key)
        if config.worktree_manager is not None and (
            (sem is None and admission is None) or acquired
        ):
            await _cleanup_session_worktree(config)
    return config, processor, failure


async def _run_recovered_turn(
    config: RunConfig, coordinator: CapacityRecoveryCoordinator
) -> tuple[RunConfig, EventProcessor | None, str | None, TurnResult]:
    """Run the attempt through the coordinator: wait, switch, or stop per policy."""
    backend = _backend_name_from_runner(config.runner)
    model = getattr(config.runner, "model", None)
    submission = TurnSubmission(
        turn_key=config.recovery_turn_key
        or f"{config.surface.frontend}:{config.surface.thread_key}:{uuid.uuid4().hex}",
        frontend=config.surface.frontend,
        thread_id=int(config.surface.thread_key),
        session_id=config.session_id,
        prompt=config.prompt,
        backend=backend,
        model=model if isinstance(model, str) else None,
        fallback_chain=_usable_chain(config.fallback_chain or _capacity_fallback_chain),
    )
    presenter = _RecoveryPresenter(config)
    last: dict[str, Any] = {}
    attempt_config = replace(config, recovery_presents_errors=True)

    async def attempt(target: AttemptTarget) -> AttemptResult:
        cfg = attempt_config
        if (target.backend, target.model) != (submission.backend, submission.model):
            runner = _capacity_backend_factory.build(
                backend=target.backend, model=target.model, thread_id=submission.thread_id
            )
            cfg = replace(cfg, runner=runner, session_id=None)
            if cfg.stop_view is not None:
                cfg.stop_view.update_runner(runner)
        else:
            cfg = replace(cfg, session_id=target.session_id)
        cfg, processor, failure = await _run_one_attempt(cfg)
        last.update(config=cfg, processor=processor, failure=failure)
        return AttemptResult(
            text=processor.final_assistant_text or None,
            error=processor.final_error or failure,
            delivered=processor.assistant_text_sent,
            session_id=processor.session_id,
        )

    turn = await coordinator.run_turn(
        submission,
        attempt,
        on_status=presenter,
        on_switch=presenter.switch,
        claim_token=config.recovery_claim_token,
    )
    await presenter.close(turn)
    if "processor" not in last:
        return config, None, None, turn
    return last["config"], last["processor"], last["failure"], turn


def _usable_chain(chain: tuple[FallbackTarget, ...]) -> tuple[FallbackTarget, ...]:
    """A chain is only usable when a factory can build its targets."""
    return chain if _capacity_backend_factory is not None else ()


class _RecoveryPresenter:
    """The one live recovery status for a turn: opened once, edited after."""

    _TERMINAL = {
        RecoveryPhase.EXHAUSTED,
        RecoveryPhase.AUTHENTICATION,
        RecoveryPhase.QUOTA,
        RecoveryPhase.AMBIGUOUS,
    }

    def __init__(self, config: RunConfig) -> None:
        self._config = config
        self._handle: Any = None
        self._open = False
        # Each attempt unregisters the session when it ends; remember how it was
        # registered so the waiting turn stays visible (as "recovering") between
        # attempts instead of vanishing from the dashboard and the API.
        self._registered: tuple[str, str | None] | None = None
        if config.registry is not None:
            for session in config.registry.list_active():
                if session.thread_id == config.surface.thread_key:
                    self._registered = (session.description, session.working_dir)

    async def __call__(self, status: RecoveryStatus) -> None:
        if status.phase is RecoveryPhase.PERMANENT:
            return  # the processor's error embed already carries the diagnostic
        if status.phase is RecoveryPhase.ACCEPTED:
            await self._finish(status.line, ok=True)
            return
        config = self._config
        if config.registry is not None:
            if self._registered is not None:
                config.registry.register(config.surface.thread_key, *self._registered)
            config.registry.update(config.surface.thread_key, execution_state="recovering")
        if config.stop_view is not None:
            with contextlib.suppress(Exception):
                await config.stop_view.set_label("⏳ Waiting for model capacity")
        with contextlib.suppress(Exception):
            if self._handle is None:
                self._handle = await config.surface.open_activity(
                    ActivitySpec(kind="todo", title="Model capacity", detail=status.line)
                )
                self._open = True
            else:
                await self._handle.update(status.line)
        if status.phase in self._TERMINAL:
            await self._finish(status.line, ok=False)
            with contextlib.suppress(Exception):
                await config.surface.set_status(StatusKind.ERROR)

    async def switch(self, target: FallbackTarget) -> None:
        # The FALLBACK status line already announced the switch.
        logger.info(
            "capacity recovery thread=%s switching to %s (%s authority)",
            self._config.surface.thread_key,
            target.label,
            target.authority,
        )

    async def close(self, turn: TurnResult) -> None:
        if self._open and turn.status is not None:
            await self._finish(turn.status.line, ok=turn.accepted)

    async def _finish(self, line: str, *, ok: bool) -> None:
        if not self._open or self._handle is None:
            return
        self._open = False
        with contextlib.suppress(Exception):
            await self._handle.complete(line, ok=ok)


async def _finish_turn(config: RunConfig, processor: EventProcessor) -> str | None:
    """Everything that follows an accepted attempt: reruns, asks, the sink."""
    # After compact_boundary, rerun with a guardrail to prevent Claude from
    # auto-executing "pending tasks" from the compacted context summary.
    if processor.compact_occurred:
        session_id = processor.session_id or config.session_id
        logger.info(
            "Compact detected for session %s — rerunning with post-compact guardrail", session_id
        )
        guardrail_config = replace(config, session_id=session_id, post_compact_rerun=True)
        return await run_claude_with_config(guardrail_config)

    # Honour a ScheduleWakeup tool call by registering a one-shot scheduled
    # task that resumes this session after the requested delay.  Done before
    # the AskUserQuestion flow — a wakeup and a pending ask are mutually
    # exclusive in practice (the ask interrupts the turn).
    if processor.pending_wakeup is not None:
        await _schedule_wakeup(config, processor.pending_wakeup)

    # After the stream ends, handle pending AskUserQuestion by showing Discord
    # UI and resuming the session with the user's answer.
    if processor.pending_ask and processor.session_id:
        answer_prompt = await collect_ask_answers(
            config.thread,
            processor.pending_ask,
            processor.session_id,
            ask_repo=config.ask_repo,
            notify_user_id=config.notify_user_id,
        )
        if answer_prompt:
            logger.info(
                "Resuming session %s after AskUserQuestion answer",
                processor.session_id,
            )
            return await run_claude_with_config(config.with_prompt(answer_prompt))

    # A PR created by this Discord thread is an intermediate artifact, not a
    # terminal outcome. Resume the same agent once so green owner PRs are
    # merged and verified instead of being delegated back to the user.
    session_id = processor.session_id or config.session_id
    completion_prompt = await _get_pr_completion_prompt(
        config,
        session_id=session_id,
        final_error=processor.final_error,
    )
    if completion_prompt is not None:
        completion_config = replace(
            config,
            prompt=completion_prompt,
            session_id=session_id,
            pr_completion_gate_rerun=True,
        )
        return await run_claude_with_config(completion_config)

    # Terminal path. The compact/ask reruns above delegate to a nested
    # run_claude_with_config call, which reaches its own terminal return — so the
    # sink fires exactly once, from the outermost completed run. An in-stream
    # RESULT error (e.g. API 400/429) is reported as an error, not an empty
    # "done", so the caller can tell a failure from an empty answer.
    await _emit_result_sink(config, processor.final_assistant_text, processor.final_error)
    return processor.session_id


async def _emit_result_sink(config: RunConfig, text: str | None, error: str | None) -> None:
    """Invoke config.result_sink once with the run's terminal outcome.

    A sink failure must never break the Claude run, so all exceptions are
    suppressed and logged.
    """
    if config.result_sink is None:
        return
    try:
        await config.result_sink(text, error)
    except Exception:
        logger.exception("result_sink callback failed for thread %d", config.surface.thread_key)


async def run_claude_in_thread(
    thread: discord.Thread | discord.TextChannel,
    runner,
    repo,
    prompt: str,
    session_id: str | None,
    status=None,
    registry=None,
    ask_repo=None,
    lounge_repo=None,
) -> str | None:
    """Backward-compatible shim. Prefer run_claude_with_config() for new code."""
    config = RunConfig(
        thread=thread,
        runner=runner,
        prompt=prompt,
        session_id=session_id,
        repo=repo,
        status=status,
        registry=registry,
        ask_repo=ask_repo,
        lounge_repo=lounge_repo,
    )
    return await run_claude_with_config(config)
