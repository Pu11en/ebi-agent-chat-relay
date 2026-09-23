"""The shared setup adapter: instructions, memory and skills from declared roots.

These three kinds live in the same places for every harness — ``CLAUDE.md``
and ``AGENTS.md`` at the top of a home or a project, ``skills/<name>/SKILL.md``,
and a memory directory — so one adapter covers them and each harness adapter
(``harness.py``) only adds what is specific to it.

Two facts shape the availability it reports:

* Claude Code reads ``CLAUDE.md`` and Codex reads ``AGENTS.md``; the other
  file is *unsupported* by the other harness, not silently loaded.
* Codex discovers ``~/.claude/skills`` as well as its own ``~/.codex/skills``
  (measured — see design decision 13 in ``CLAUDE.md``), so a skill in the
  Claude home is *discovered* by both.

"Discovered" is the strongest claim this adapter ever makes: it sees files,
not loaders.  Verified loading comes from runtime evidence handed to the
harness adapters.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from claude_discord.ai_setup_collector import AdapterResult, CollectionContext
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    DiagnosticSeverity,
    HarnessAvailability,
    InventoryDiagnostic,
    InventoryItem,
    ItemIdentity,
    OwnershipClass,
    SetupKind,
)
from claude_discord.ai_setup_redaction import RedactionError, safe_message

from ._files import (
    HarnessLayout,
    SetupRoot,
    SourceError,
    read_file_facts,
    read_text_bounded,
    sorted_children,
)

KNOWN_HARNESSES: tuple[str, ...] = ("claude", "codex")

_FRONTMATTER_DESCRIPTION = re.compile(r"^description:\s*(?P<text>.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class _Found:
    """One file the adapter decided to inventory, before it is read."""

    kind: SetupKind
    name: str
    path: Path
    loaders: frozenset[str]
    unsupported: dict[str, str]
    ownership: OwnershipClass | None = None


def _instruction_files(root: SetupRoot) -> Iterator[_Found]:
    claude_only = {"codex": "Codex reads AGENTS.md, not CLAUDE.md"}
    codex_only = {"claude": "Claude Code reads CLAUDE.md, not AGENTS.md"}
    candidates: list[tuple[str, Path, frozenset[str], dict[str, str], OwnershipClass | None]]
    match root.layout:
        case HarnessLayout.CLAUDE_HOME:
            candidates = [
                ("CLAUDE.md", root.path / "CLAUDE.md", frozenset({"claude"}), claude_only, None),
            ]
        case HarnessLayout.CODEX_HOME:
            candidates = [
                ("AGENTS.md", root.path / "AGENTS.md", frozenset({"codex"}), codex_only, None),
            ]
        case HarnessLayout.PROJECT:
            candidates = [
                ("CLAUDE.md", root.path / "CLAUDE.md", frozenset({"claude"}), claude_only, None),
                (
                    ".claude/CLAUDE.md",
                    root.path / ".claude" / "CLAUDE.md",
                    frozenset({"claude"}),
                    claude_only,
                    None,
                ),
                (
                    "CLAUDE.local.md",
                    root.path / "CLAUDE.local.md",
                    frozenset({"claude"}),
                    claude_only,
                    OwnershipClass.MACHINE,
                ),
                ("AGENTS.md", root.path / "AGENTS.md", frozenset({"codex"}), codex_only, None),
            ]
        case _:
            candidates = []
    for name, path, loaders, unsupported, ownership in candidates:
        if path.is_file() or path.is_symlink():
            yield _Found(SetupKind.INSTRUCTION, name, path, loaders, unsupported, ownership)


def _skill_dirs(root: SetupRoot) -> Iterator[tuple[Path, frozenset[str], dict[str, str]]]:
    match root.layout:
        case HarnessLayout.CLAUDE_HOME:
            yield root.path / "skills", frozenset({"claude", "codex"}), {}
        case HarnessLayout.CODEX_HOME:
            yield (
                root.path / "skills",
                frozenset({"codex"}),
                {"claude": "Claude Code does not read ~/.codex/skills"},
            )
        case HarnessLayout.PROJECT:
            yield root.path / ".claude" / "skills", frozenset({"claude"}), {}
            yield root.path / ".agents" / "skills", frozenset({"codex"}), {}
        case _:
            return


def _skill_files(root: SetupRoot) -> Iterator[_Found]:
    for skills_dir, loaders, unsupported in _skill_dirs(root):
        if not skills_dir.is_dir():
            continue
        for entry in sorted_children(skills_dir):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if skill_file.exists() or skill_file.is_symlink():
                yield _Found(SetupKind.SKILL, entry.name, skill_file, loaders, dict(unsupported))
                continue
            # One nested level (``skills/synced/<name>``) is a container, not a skill.
            for nested in sorted_children(entry):
                nested_file = nested / "SKILL.md"
                if nested.is_dir() and (nested_file.exists() or nested_file.is_symlink()):
                    yield _Found(
                        SetupKind.SKILL,
                        f"{entry.name}/{nested.name}",
                        nested_file,
                        loaders,
                        dict(unsupported),
                    )


def _memory_files(root: SetupRoot) -> Iterator[_Found]:
    match root.layout:
        case HarnessLayout.CLAUDE_HOME:
            projects = root.path / "projects"
            if not projects.is_dir():
                return
            for project_dir in sorted_children(projects):
                memory_dir = project_dir / "memory"
                if not project_dir.is_dir() or not memory_dir.is_dir():
                    continue
                for entry in sorted_children(memory_dir):
                    if entry.suffix == ".md":
                        yield _Found(
                            SetupKind.MEMORY,
                            f"{project_dir.name}/{entry.name}",
                            entry,
                            frozenset({"claude"}),
                            {"codex": "Codex keeps its own memories under ~/.codex"},
                        )
        case HarnessLayout.CODEX_HOME:
            memories = root.path / "memories"
            if not memories.is_dir():
                return
            for entry in sorted_children(memories):
                if entry.suffix == ".md":
                    yield _Found(
                        SetupKind.MEMORY,
                        entry.name,
                        entry,
                        frozenset({"codex"}),
                        {"claude": "Claude Code keeps its own memory under ~/.claude/projects"},
                    )
        case _:
            return


def _found_in(root: SetupRoot) -> Iterator[_Found]:
    yield from _instruction_files(root)
    yield from _memory_files(root)
    yield from _skill_files(root)


def _skill_description(root: SetupRoot, path: Path) -> str | None:
    """The ``description:`` line of a SKILL.md front matter, scrubbed; else nothing."""
    try:
        text = read_text_bounded(root, path, limit=64 * 1024)
    except SourceError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    match = _FRONTMATTER_DESCRIPTION.search(text[3:end])
    if match is None:
        return None
    try:
        return safe_message(match.group("text").strip("\"'"), kind="item summary", limit=120)
    except (RedactionError, ValueError):
        return None


def availability_for(
    context: CollectionContext,
    *,
    loaders: Iterable[str],
    unsupported: dict[str, str],
    state: AvailabilityState = AvailabilityState.DISCOVERED,
    known: Sequence[str] = KNOWN_HARNESSES,
) -> tuple[HarnessAvailability, ...]:
    """Availability rows for the harnesses this run speaks for.

    A harness the run did not declare is left out (it stays ``unknown``); a
    declared one is ``discovered`` when it reads this location, ``unsupported``
    with the reason when it does not, and unreported when nothing is known.
    """
    wanted = context.harnesses or tuple(known)
    loader_set = frozenset(loaders)
    rows: list[HarnessAvailability] = []
    for harness in wanted:
        if harness in loader_set:
            rows.append(
                HarnessAvailability(harness=harness, state=state, computer=context.computer)
            )
        elif harness in unsupported:
            rows.append(
                HarnessAvailability(
                    harness=harness,
                    state=AvailabilityState.UNSUPPORTED,
                    computer=context.computer,
                    detail=unsupported[harness],
                )
            )
    return tuple(rows)


class SharedSetupAdapter:
    """Instructions, memory and skills from every declared root."""

    name = "shared-setup"
    kinds: tuple[SetupKind, ...] = (SetupKind.INSTRUCTION, SetupKind.MEMORY, SetupKind.SKILL)

    def __init__(self, roots: Iterable[SetupRoot]) -> None:
        self.roots: tuple[SetupRoot, ...] = tuple(roots)
        if not self.roots:
            raise ValueError("The shared setup adapter needs at least one declared root")

    @property
    def source_keys(self) -> tuple[str, ...]:
        return tuple(root.key for root in self.roots)

    def collect(self, context: CollectionContext) -> AdapterResult:
        items: list[InventoryItem] = []
        diagnostics: list[InventoryDiagnostic] = []
        for root in self.roots:
            if not root.exists:
                diagnostics.append(
                    context.diagnostic(root.key, DiagnosticSeverity.WARNING, root.missing_message())
                )
                continue
            for found in _found_in(root):
                try:
                    items.append(self._item(root, found, context))
                except SourceError as problem:
                    diagnostics.append(
                        context.diagnostic(root.key, DiagnosticSeverity.WARNING, str(problem))
                    )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))

    def _item(self, root: SetupRoot, found: _Found, context: CollectionContext) -> InventoryItem:
        facts = read_file_facts(root, found.path)
        summary = _skill_description(root, found.path) if found.kind is SetupKind.SKILL else None
        ownership = found.ownership or root.ownership
        return InventoryItem(
            identity=ItemIdentity(kind=found.kind, source_key=root.key, name=found.name),
            display_name=found.name,
            source=root.source_for(found.path, context, modified_at=facts.modified_at),
            scope=root.scope_for(context, ownership=ownership),
            classification=Classification.CUSTOM,
            ownership=ownership,
            availability=availability_for(
                context, loaders=found.loaders, unsupported=found.unsupported
            ),
            measurement=facts.measurement,
            fingerprint=facts.fingerprint,
            last_changed_at=facts.modified_at,
            summary=summary,
        )


__all__ = ["KNOWN_HARNESSES", "SharedSetupAdapter", "availability_for"]
