"""What a Go Work build tells the person, drawn from its ledger (T22).

Short, and complete on its own: the project and its goal first, then what
changed or failed and why it matters, in the plan's own words for each task
(its outcome, never a bare id). Everything here is read from saved evidence;
nothing is invented, and a build with stuck tasks reports issues, not success.
"""

from __future__ import annotations

from claude_code_core.gowork_state import BuildState, TaskAttempt, TaskStatus

MAX_REASON_CHARS = 220
MAX_ITEMS = 12


def _outcome(state: BuildState, task_id: str) -> str:
    try:
        return state.tree.task(task_id).outcome
    except KeyError:
        return task_id


def _delivers(state: BuildState, task_id: str) -> str:
    try:
        requirement = state.tree.task(task_id).source_requirement
    except KeyError:
        return ""
    for item in state.tree.requirements:
        if item.requirement_id == requirement:
            return item.outcome
    return ""


def _short(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= MAX_REASON_CHARS else text[: MAX_REASON_CHARS - 1] + "…"


def _header(project: str, goal: str | None) -> str:
    return f"**{project}** — {goal.strip()}" if goal else f"**{project}**"


def _plain_check(check: str) -> str:
    """A saved check line in plain words (a review verdict reads as a sentence)."""
    if check.startswith("review (approve)"):
        return "a second AI approved it"
    if check.startswith("review (changes)"):
        return "a second AI sent it back: " + check.split(":", 1)[-1].strip()
    if check.startswith("review (none)"):
        return "the review could not run"
    return check


def render_progress(state: BuildState, *, project: str, goal: str | None) -> str:
    """Where the build stands right now."""
    records = state.records
    done = [r for r in records if r.accepted]
    running = [r for r in records if r.status is TaskStatus.RUNNING]
    stuck = [r for r in records if r.status is TaskStatus.BLOCKED]
    waiting = [r for r in records if r.status in (TaskStatus.PENDING, TaskStatus.FINISHED)]
    lines = [_header(project, goal), f"{len(done)} of {len(records)} done."]
    for r in done[-MAX_ITEMS:]:
        lines.append(f"✅ {_outcome(state, r.task_id)}")
    for r in running[:MAX_ITEMS]:
        lines.append(f"⏳ {_outcome(state, r.task_id)} — being built now")
    for r in stuck[:MAX_ITEMS]:
        lines.append(f"🛑 {_outcome(state, r.task_id)} — {_short(r.reason)}")
    if waiting:
        names = ", ".join(_outcome(state, r.task_id) for r in waiting[:MAX_ITEMS])
        lines.append(f"⬜ waiting: {names}")
    return "\n".join(lines)


def _worth_knowing(state: BuildState, record: TaskAttempt) -> list[str]:
    notes: list[str] = []
    name = _outcome(state, record.task_id)
    if record.lineage_repairs:
        why = f" ({_short(record.previous_failure)})" if record.previous_failure else ""
        notes.append(f"{name} needed one repair{why}")
    if record.rework_reason:
        notes.append(f"{name} was reworked after the plan changed ({_short(record.rework_reason)})")
    if record.attempt > 1 and not record.lineage_repairs and not record.rework_reason:
        notes.append(f"{name} took {record.attempt} attempts")
    return notes


def render_completion(state: BuildState, *, project: str, goal: str | None) -> str:
    """The end of the build: what got done and why it matters — or what is stuck."""
    records = state.records
    done = [r for r in records if r.accepted]
    stuck = [r for r in records if r.status is TaskStatus.BLOCKED]
    lines = [_header(project, goal)]
    if stuck:
        lines.append(f"🛑 {len(stuck)} task{'s are' if len(stuck) != 1 else ' is'} stuck:")
        for r in stuck[:MAX_ITEMS]:
            lines.append(f"• {_outcome(state, r.task_id)} — {_short(r.reason)}")
        if done:
            lines.append(f"Done and checked so far ({len(done)}):")
            for r in done[:MAX_ITEMS]:
                lines.append(f"• {_outcome(state, r.task_id)}")
        left = [r for r in records if not r.accepted and r.status is not TaskStatus.BLOCKED]
        if left:
            names = ", ".join(_outcome(state, r.task_id) for r in left[:MAX_ITEMS])
            lines.append(f"Still waiting on the stuck ones: {names}")
        return "\n".join(lines)
    lines.append(f"🏁 All {len(records)} tasks are done and checked.")
    for r in done[:MAX_ITEMS]:
        delivers = _delivers(state, r.task_id)
        line = f"• {_outcome(state, r.task_id)}"
        if delivers:
            line += f" — delivers {delivers}"
        checks = [_plain_check(c) for c in r.checks if not c.startswith("worker ran")]
        if checks:
            line += f" (checked by: {'; '.join(checks)})"
        lines.append(line)
    notes: list[str] = []
    for r in done:
        notes += _worth_knowing(state, r)
    if notes:
        lines += ["Worth knowing:", *[f"• {n}" for n in notes[:MAX_ITEMS]]]
    return "\n".join(lines)


def render_blocker_question(
    state: BuildState, task_id: str, *, project: str, goal: str | None
) -> str:
    """One question, one decision: what is stuck, why, and what to reply."""
    record = state[task_id]
    return (
        f"❓ {_header(project, goal)}\n"
        f"**{_outcome(state, task_id)}** is stuck: {_short(record.reason)}\n"
        "What should I do? Reply to this message with **retry**, **skip**, or tell me what to "
        "change."
    )
