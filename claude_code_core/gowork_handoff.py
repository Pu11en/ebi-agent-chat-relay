"""The compact, persisted handoff a Go Work worker starts from (T12).

A worker begins with no memory. What it needs is small and known: which
attempt it is, the assignment (outcome, what it owns, what it may rely on,
what it must produce, how that is checked), the build's goal, the decisions
the plan already saved, and the evidence behind every input it consumes.
That is written to one JSON file per attempt before the worker starts, so a
restart that delivers the same attempt twice reads the same handoff back —
and the chat that led here is never part of it.

Relation to the bot-to-bot handoff contract (``claude_code_core/handoffs/``):
that packet carries a job *between agents* (sender, recipient, authority,
project locator, reply coordinates) and travels through Discord as a
``CCDB_HANDOFF_V1`` envelope. A worker handoff stays on this host, inside one
build, for one attempt; it follows the same rules — bounded fields, no
transcript, stable identity — but is a different object and is never posted
as an envelope.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from claude_code_core.gowork_state import BuildState, TaskStatus

MAX_FIELD_CHARS = 2000
_DECISIONS_HEADING = re.compile(r"^#{1,6}\s*.*decision", re.I)
_HEADING = re.compile(r"^#{1,6}\s")
_ITEM = re.compile(r"^\s*[-*]\s+(?!\[[ x]\])(.+?)\s*$")


def plan_decisions(plan_text: str) -> tuple[str, ...]:
    """The list items under the plan's *Decisions* heading, in order."""
    decisions: list[str] = []
    inside = False
    for line in plan_text.splitlines():
        if _HEADING.match(line):
            inside = bool(_DECISIONS_HEADING.match(line))
            continue
        if inside and (m := _ITEM.match(line)):
            decisions.append(m.group(1)[:MAX_FIELD_CHARS])
    return tuple(decisions)


def _goal(plan_text: str) -> str:
    for line in plan_text.splitlines():
        m = re.match(r"^\s*\**goal\**\s*:\s*(.+)$", line, re.I)
        if m:
            return m.group(1).strip("* ")[:MAX_FIELD_CHARS]
    return ""


@dataclass(frozen=True, slots=True)
class WorkerHandoff:
    attempt_id: str
    task_id: str
    plan_id: str
    plan_version: int
    project_dir: str
    goal: str
    outcome: str
    owned_files: tuple[str, ...]
    owned_resources: tuple[str, ...]
    required_inputs: tuple[str, ...]
    input_evidence: tuple[str, ...]
    expected_output: str
    acceptance_check: str
    source_requirement: str
    decisions: tuple[str, ...]
    created_at: str
    #: Why the previous attempt was sent back (T18): the repair worker fixes this first.
    previous_failure: str | None = None
    attempt: int = 1
    #: Set when the plan changed after earlier work (T19): adjust, don't restart.
    rework_reason: str | None = None
    previous_commit: str | None = None

    def to_json(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "project_dir": self.project_dir,
            "goal": self.goal,
            "outcome": self.outcome,
            "owned_files": list(self.owned_files),
            "owned_resources": list(self.owned_resources),
            "required_inputs": list(self.required_inputs),
            "input_evidence": list(self.input_evidence),
            "expected_output": self.expected_output,
            "acceptance_check": self.acceptance_check,
            "source_requirement": self.source_requirement,
            "decisions": list(self.decisions),
            "created_at": self.created_at,
            "previous_failure": self.previous_failure,
            "attempt": self.attempt,
            "rework_reason": self.rework_reason,
            "previous_commit": self.previous_commit,
        }

    @classmethod
    def from_json(cls, value: dict) -> WorkerHandoff:
        return cls(
            attempt_id=str(value["attempt_id"]),
            task_id=str(value["task_id"]),
            plan_id=str(value["plan_id"]),
            plan_version=int(value["plan_version"]),
            project_dir=str(value.get("project_dir", "")),
            goal=str(value.get("goal", "")),
            outcome=str(value["outcome"]),
            owned_files=tuple(value.get("owned_files") or ()),
            owned_resources=tuple(value.get("owned_resources") or ()),
            required_inputs=tuple(value.get("required_inputs") or ()),
            input_evidence=tuple(value.get("input_evidence") or ()),
            expected_output=str(value.get("expected_output", "")),
            acceptance_check=str(value.get("acceptance_check", "")),
            source_requirement=str(value.get("source_requirement", "")),
            decisions=tuple(value.get("decisions") or ()),
            created_at=str(value.get("created_at", "")),
            previous_failure=value.get("previous_failure"),
            attempt=int(value.get("attempt", 1)),
            rework_reason=value.get("rework_reason"),
            previous_commit=value.get("previous_commit"),
        )


def build_handoff(state: BuildState, task_id: str, *, plan_text: str) -> WorkerHandoff:
    """The handoff for *task_id*'s current attempt, which must be running."""
    record = state[task_id]
    if record.status is not TaskStatus.RUNNING:
        raise ValueError(
            f"task '{task_id}' is {record.status.value}, not running; begin the attempt first"
        )
    assignment = state.tree.task(task_id)
    plan = state.tree.get(assignment.plan_id)
    evidence: list[str] = []
    for dependency in assignment.dependencies:
        done = state[dependency]
        checks = ", ".join(done.checks) if done.checks else "no check output recorded"
        evidence.append(f"{dependency}: accepted at {done.result_commit} — {checks}")
    return WorkerHandoff(
        attempt_id=record.attempt_id,
        task_id=task_id,
        plan_id=assignment.plan_id,
        plan_version=record.plan_version,
        project_dir=str(plan.project_path),
        goal=_goal(plan_text),
        outcome=assignment.outcome[:MAX_FIELD_CHARS],
        owned_files=assignment.owned_files,
        owned_resources=assignment.owned_resources,
        required_inputs=assignment.required_inputs,
        input_evidence=tuple(evidence),
        expected_output=assignment.output[:MAX_FIELD_CHARS],
        acceptance_check=assignment.acceptance_check[:MAX_FIELD_CHARS],
        source_requirement=assignment.source_requirement,
        decisions=plan_decisions(plan_text),
        created_at=_dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        previous_failure=record.previous_failure,
        attempt=record.attempt,
        rework_reason=record.rework_reason,
        previous_commit=record.previous_commit,
    )


def handoff_path(directory: Path, attempt_id: str) -> Path:
    return directory / (re.sub(r"[^A-Za-z0-9._-]+", "_", attempt_id) + ".json")


def persist_handoff(directory: Path, handoff: WorkerHandoff) -> Path:
    """Write the handoff once per attempt; a second delivery returns the first file."""
    path = handoff_path(directory, handoff.attempt_id)
    if path.is_file():
        return path
    directory.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(handoff.to_json(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_handoff(directory: Path, attempt_id: str) -> WorkerHandoff | None:
    path = handoff_path(directory, attempt_id)
    if not path.is_file():
        return None
    return WorkerHandoff.from_json(json.loads(path.read_text(encoding="utf-8")))


def render_worker_prompt(handoff: WorkerHandoff, *, cwd: Path, handoff_path: Path) -> str:
    """The worker's whole briefing, from the handoff and nothing else."""
    parts = [
        "[gowork build worker — for this worker only] You are one of several workers "
        "building tasks of a plan at the same time, each in its own copy. You start "
        "with no memory; everything you need is below (also saved at "
        f"{handoff_path}).",
        "",
        f"Attempt: {handoff.attempt_id}",
    ]
    if handoff.goal:
        parts.append(f"The goal of this whole build: {handoff.goal}")
    parts += [
        "",
        f"Your task ({handoff.task_id}): {handoff.outcome}",
        f"Work only in this folder: {cwd}",
        "You may change only these files/folders: " + ", ".join(handoff.owned_files),
    ]
    if handoff.owned_resources:
        parts.append("Resources you own: " + ", ".join(handoff.owned_resources))
    if handoff.required_inputs:
        parts.append("Inputs you can rely on: " + "; ".join(handoff.required_inputs))
    if handoff.input_evidence:
        parts.append("Evidence behind those inputs: " + "; ".join(handoff.input_evidence))
    if handoff.decisions:
        parts += ["", "Decisions already made (follow them, don't reopen them):"]
        parts += [f"- {d}" for d in handoff.decisions]
    if handoff.rework_reason:
        parts += [
            "",
            f"This is a rework (attempt {handoff.attempt}): {handoff.rework_reason}.",
            "Earlier work for this task is already in the copy"
            + (f" (commit {handoff.previous_commit})" if handoff.previous_commit else "")
            + "; adjust it to the changed plan rather than starting over.",
        ]
    if handoff.previous_failure:
        parts += [
            "",
            f"This is attempt {handoff.attempt}. The previous attempt was sent back because: "
            f"{handoff.previous_failure}",
            "Fix that first; this is the last automatic try before a person is asked.",
        ]
    parts += [
        "",
        f"Expected output: {handoff.expected_output}",
        f"Acceptance check — run it and make it pass: {handoff.acceptance_check}",
        f"This delivers agreed outcome {handoff.source_requirement}.",
        "Other tasks are being built right now by others: don't do them, and don't edit "
        "the plan file (the bot records results).",
        "Commit your work with git. Leave no uncommitted changes.",
        "",
        "Never push, deploy, delete data, spend money or use new API keys. If the task "
        "needs any of that, don't do it: end with STUCK and say why.",
        "",
        "Finish with one or two plain sentences on what you did, for a non-technical "
        "reader. The very last line must be exactly one of:",
        "DONE — the task is finished, checked and committed",
        "STUCK: <plain reason> — you cannot finish it this way",
    ]
    return "\n".join(parts)
