"""The briefing: everything a fresh worker session is allowed to know.

A parallel worker starts in an empty session. It cannot read the planning
thread, cannot ask a follow-up question, and cannot see what its siblings are
doing. So the brief is not a convenience summary — it is the worker's entire
world, and two failures follow from getting it wrong:

* too little, and the worker invents the missing half: a foundation it guessed,
  a check nobody asked for, a file it decided also needed changing;
* too much, and the parent transcript comes with it: half-made decisions,
  abandoned ideas and side requests that were never approved, which a fresh
  session cannot distinguish from its actual task.

This module therefore builds the brief from typed fields only. There is no
free-form "context" input to paste a conversation into, pasted conversation is
rejected rather than trimmed, and every brief carries the same restrictions
verbatim: stay inside the owned paths, never merge, never spawn another worker.

It knows nothing about Git, Discord or the filesystem; it turns records into one
bounded string, and the modules that dispatch and verify work do the rest.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

BRIEFING_VERSION = 1

# Bounds. The brief is read by a model with a finite prompt and by a human
# skimming a Discord thread; an unbounded one silently buries its own task.
MAX_BRIEFING_CHARS = 6000
MAX_GOAL_CHARS = 600
MAX_TITLE_CHARS = 200
MAX_INSTRUCTION_CHARS = 1500
MAX_DECISIONS = 12
MAX_DECISION_CHARS = 400
MAX_OWNED_PATHS = 20
MAX_CHECKS = 10
MAX_PLAN_FILES = 20
MAX_DEPENDENCIES = 20
MAX_FIELD_CHARS = 400

# The result the coordinator verifies. Naming the fields in the brief is what
# makes "the worker forgot to record its commit" a worker bug and not a
# specification gap.
REQUIRED_RESULT_FIELDS: tuple[str, ...] = ("task", "approval_digest", "commit", "worktree", "tests")

# Stated in every brief, unchanged. A restriction that varies per task is one a
# worker can argue itself out of.
RESTRICTIONS: tuple[str, ...] = (
    "Work only inside your own worktree; never edit the main checkout or another worker's files.",
    "Change only the owned paths listed above. Do not expand scope to other files, other tasks, "
    "drive-by fixes, or follow-up work you think is obviously needed; report it instead.",
    "Do not merge, rebase onto, or push into any shared branch, and do not open a pull request. "
    "One integration owner merges every verified commit.",
    "Do not spawn, launch or delegate to additional workers, sessions or background agents.",
    "Do not restart shared services, and publish nothing outside your recorded result.",
)

SECTION_NAMES: tuple[str, ...] = (
    "Build goal",
    "Your task",
    "Approved decisions",
    "Foundation",
    "Owned paths",
    "Restrictions",
    "Checks",
    "Result",
    "Parent",
)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_THREAD_RE = re.compile(r"^\d{5,25}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Chat, not specification. These match how a transcript is shaped — a speaker
# label starting a line, a timestamped message, an explicit transcript wrapper —
# rather than what it talks about, so a decision may still say "the user chose".
_TRANSCRIPT_RE = re.compile(
    r"""(?imx)
    ^\s*(?:user|assistant|human|claude|system|drew|me|you)\s*:      # speaker label
    | ^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*[\w.-]{1,32}\s*:           # [03:12] name:
    | </?\s*transcript\b                                            # wrapper tag
    | ^\s*>\s*[\w.-]{1,32}\s+(?:said|wrote)\b                       # quoted speech
    """
)


class BriefingError(Exception):
    """A briefing cannot be built; no worker may be dispatched with a partial one."""


def _clean(label: str, raw: str, limit: int) -> str:
    """Validate one field: present, bounded, no control bytes, no transcript."""
    value = raw.strip()
    if not value:
        raise BriefingError(f"Briefing {label} is required")
    if _CONTROL_RE.search(value):
        raise BriefingError(f"Briefing {label} contains a control character")
    if len(value) > limit:
        raise BriefingError(f"Briefing {label} is too long: {len(value)} > {limit} characters")
    if _TRANSCRIPT_RE.search(value):
        raise BriefingError(
            f"Briefing {label} looks like a parent transcript; pass approved decisions instead"
        )
    return value


def _clean_all(
    label: str, raw: Sequence[str], limit: int, count: int, *, required: bool
) -> tuple[str, ...]:
    if required and not raw:
        raise BriefingError(f"Briefing {label} is required")
    if len(raw) > count:
        raise BriefingError(f"Briefing {label} has too many entries: {len(raw)} > {count}")
    return tuple(_clean(f"{label} entry", item, limit) for item in raw)


@dataclass(frozen=True, slots=True)
class Decision:
    """One approved decision the worker must preserve, with the ID it was approved under."""

    id: str
    statement: str


@dataclass(frozen=True, slots=True)
class BuildContext:
    """What the whole build is for — the only part shared by every worker."""

    run_id: str
    goal: str
    plan_revision: str
    plan_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    """The one task this worker may do, and the workspace it may do it in."""

    task_id: str
    title: str
    instruction: str
    owned_paths: tuple[str, ...]
    checks: tuple[str, ...]
    worktree: str
    branch: str


@dataclass(frozen=True, slots=True)
class DependencyFoundation:
    """The exact commit this task starts from, and whose work it already contains."""

    commit: str
    repo_path: str
    integrated_tasks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResultDestination:
    """Where the worker records evidence, and what that record must contain."""

    path: str
    approval_digest: str
    required_fields: tuple[str, ...] = REQUIRED_RESULT_FIELDS


@dataclass(frozen=True, slots=True)
class ParentLink:
    """Who owns the plan and who integrates — so the worker reports, not decides."""

    plan_owner_thread: str
    integration_owner_thread: str


@dataclass(frozen=True, slots=True)
class WorkerBriefing:
    """A rendered, bounded brief plus the sections it was built from."""

    version: int
    run_id: str
    task_id: str
    plan_revision: str
    sections: tuple[tuple[str, str], ...]
    text: str
    _index: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def section(self, name: str) -> str:
        try:
            return self._index[name]
        except KeyError as exc:
            raise BriefingError(f"Unknown briefing section: {name}") from exc

    def as_dict(self) -> dict:
        """A durable record of what the worker was told, for restart and audit."""
        return {
            "version": self.version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "plan_revision": self.plan_revision,
            "sections": dict(self.sections),
            "text": self.text,
        }


def _thread(label: str, raw: str) -> str:
    value = raw.strip()
    if not _THREAD_RE.match(value):
        raise BriefingError(f"Briefing {label} thread must be a numeric thread ID: {value!r}")
    return value


def _goal_section(build: BuildContext) -> str:
    goal = _clean("build goal", build.goal, MAX_GOAL_CHARS)
    revision = _clean("plan revision", build.plan_revision, MAX_FIELD_CHARS)
    files = _clean_all(
        "plan file", build.plan_files, MAX_FIELD_CHARS, MAX_PLAN_FILES, required=True
    )
    return (
        f"{goal}\n"
        f"You are one worker in build {_clean('run id', build.run_id, MAX_FIELD_CHARS)}. "
        f"Other workers are building other tasks of this plan right now.\n"
        f"Approved plan revision {revision}. Read these plan files in your worktree and preserve "
        f"their decisions: {', '.join(files)}."
    )


def _task_section(work: WorkerAssignment) -> str:
    return (
        f"Task {_clean('task id', work.task_id, MAX_FIELD_CHARS)}: "
        f"{_clean('task title', work.title, MAX_TITLE_CHARS)}\n"
        f"{_clean('task instruction', work.instruction, MAX_INSTRUCTION_CHARS)}\n"
        "Do this task only. Anything else in the plan belongs to another worker."
    )


def _decisions_section(decisions: Sequence[Decision]) -> str:
    if len(decisions) > MAX_DECISIONS:
        raise BriefingError(
            f"Briefing carries too many decisions: {len(decisions)} > {MAX_DECISIONS}"
        )
    if not decisions:
        return (
            "No approved decision applies beyond the plan files above. "
            "Do not invent one; ask the plan owner through your result instead."
        )
    lines = [
        f"- {_clean('decision id', item.id, MAX_FIELD_CHARS)}: "
        f"{_clean('decision', item.statement, MAX_DECISION_CHARS)}"
        for item in decisions
    ]
    header = "These decisions are already approved. Implement them; do not reopen them."
    return header + "\n" + "\n".join(lines)


def _foundation_section(base: DependencyFoundation, work: WorkerAssignment) -> str:
    commit = base.commit.strip().lower()
    if not _SHA_RE.match(commit):
        raise BriefingError(f"Briefing foundation must be a full 40-character commit: {commit!r}")
    done = _clean_all(
        "dependency", base.integrated_tasks, MAX_FIELD_CHARS, MAX_DEPENDENCIES, required=False
    )
    lineage = (
        f"It already contains the integrated work of task(s) {', '.join(done)}."
        if done
        else "There are no dependency tasks before yours; it is the approved starting point."
    )
    return (
        f"Start from commit {commit} in {_clean('repository', base.repo_path, MAX_FIELD_CHARS)}.\n"
        f"{lineage}\n"
        f"Work only in worktree {_clean('worktree', work.worktree, MAX_FIELD_CHARS)} on branch "
        f"{_clean('branch', work.branch, MAX_FIELD_CHARS)}, with every command naming that "
        "absolute path. Reuse it and preserve partial work if it already exists."
    )


def _paths_section(work: WorkerAssignment) -> str:
    paths = _clean_all(
        "owned path", work.owned_paths, MAX_FIELD_CHARS, MAX_OWNED_PATHS, required=True
    )
    return "You may create or change only these paths:\n" + "\n".join(f"- {p}" for p in paths)


def _checks_section(work: WorkerAssignment) -> str:
    checks = _clean_all("check", work.checks, MAX_FIELD_CHARS, MAX_CHECKS, required=False)
    if not checks:
        return (
            "This task declares no focused check. Run the plan-level check named in the plan "
            "files and record its real command and outcome."
        )
    return (
        "Run these and record the real command and outcome; a briefing is not evidence:\n"
        + "\n".join(f"- {check}" for check in checks)
        + "\nDo not report success until they pass."
    )


def _result_section(where: ResultDestination, work: WorkerAssignment) -> str:
    digest = where.approval_digest.strip().lower()
    if not _DIGEST_RE.match(digest):
        raise BriefingError(f"Briefing approval digest must be a full digest: {digest!r}")
    fields = _clean_all(
        "result field", where.required_fields, MAX_FIELD_CHARS, MAX_CHECKS, required=True
    )
    return (
        f"Commit all owned work locally, leave the worktree clean, and write one atomic JSON "
        f"result to {_clean('result path', where.path, MAX_FIELD_CHARS)} "
        "(create its parent directory if needed).\n"
        f"Required fields: {', '.join(fields)}.\n"
        f"Use task={work.task_id!r} and approval_digest={digest!r}, the full 40-character commit "
        "SHA, your absolute worktree path, and a nonempty list of real check evidence."
    )


def _parent_section(link: ParentLink) -> str:
    return (
        f"The plan owner is thread {_thread('plan owner', link.plan_owner_thread)}; "
        f"the integration owner is thread "
        f"{_thread('integration owner', link.integration_owner_thread)}.\n"
        "They hold the full context of this build; you deliberately do not. If your task is "
        "wrong, blocked or larger than described, record that in your result and stop — do not "
        "work around it."
    )


def build_worker_briefing(
    *,
    build: BuildContext,
    assignment: WorkerAssignment,
    foundation: DependencyFoundation,
    decisions: Sequence[Decision] = (),
    destination: ResultDestination,
    parent: ParentLink,
) -> WorkerBriefing:
    """Render one self-contained brief from typed inputs, or refuse to.

    There is no partial success: a brief missing its foundation, its checks or
    its result destination would send a worker off to guess, so every validation
    failure raises instead of dropping the field.
    """
    bodies = (
        _goal_section(build),
        _task_section(assignment),
        _decisions_section(decisions),
        _foundation_section(foundation, assignment),
        _paths_section(assignment),
        "\n".join(f"- {rule}" for rule in RESTRICTIONS),
        _checks_section(assignment),
        _result_section(destination, assignment),
        _parent_section(parent),
    )
    sections = tuple(zip(SECTION_NAMES, bodies, strict=True))
    header = (
        f"Approved worker briefing {build.run_id.strip()}/{assignment.task_id.strip()} "
        f"(briefing v{BRIEFING_VERSION}). This message is your whole context: the planning "
        "conversation is deliberately not included."
    )
    text = header + "\n\n" + "\n\n".join(f"{name.upper()}\n{body}" for name, body in sections)
    if len(text) > MAX_BRIEFING_CHARS:
        raise BriefingError(
            f"Briefing is too long: {len(text)} > {MAX_BRIEFING_CHARS} characters; "
            "carry fewer decisions rather than truncating the task"
        )
    return WorkerBriefing(
        version=BRIEFING_VERSION,
        run_id=build.run_id.strip(),
        task_id=assignment.task_id.strip(),
        plan_revision=build.plan_revision.strip(),
        sections=sections,
        text=text,
        _index=dict(sections),
    )
