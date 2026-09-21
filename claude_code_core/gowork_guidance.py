"""Shared planner guidance for every harness, staged, never forked (T29).

The guidance is one narrow supporting skill: the T26 planner and
communication rules, the T27 plan template and check command, on top of the
One Question flow the person already uses. It is written **once**, under the
shared skills directory (``~/.agents/skills/gowork-planning/SKILL.md``), and
every harness reaches that one file:

* the shared instruction file (``~/AGENTS.md``) gets a short routing block
  naming the skill; Claude Code reads it through ``~/.claude/CLAUDE.md``'s
  ``@~/AGENTS.md`` import (the audited setup) or, when that import is absent,
  through the same block appended to ``CLAUDE.md``;
* Codex and DSH read ``AGENTS.md`` in their own homes — a link to the shared
  file when the platform allows one, otherwise a file holding only the block;
* the harness skill directories get a link (symlink, or a directory junction
  on Windows) to the shared skill folder; when neither can be made the
  installer reports the manual step instead of copying.

The installer only stages into the home it is given — there is no default and
the command line refuses to run without ``--home`` — backs up every existing
instruction file before its first change, is idempotent, and its rollback
removes only what its manifest says it owns, keeping edits made afterwards.
The rejected wholesale planning skill (``.planning/gowork-flow/planner-skill-
draft``) is not part of this and is never installed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from claude_code_core.gowork_export import plan_template
from claude_code_core.gowork_prompts import COMMUNICATION_RULES, PLANNER_RULES

SKILL_NAME = "gowork-planning"
BLOCK_START = "<!-- ccdb:gowork-planning start -->"
BLOCK_END = "<!-- ccdb:gowork-planning end -->"
BACKUP_SUFFIX = ".ccdb-backup"
MANIFEST_NAME = ".ccdb-install.json"
Harness = Literal["claude", "codex", "dsh"]
HARNESSES: tuple[Harness, ...] = ("claude", "codex", "dsh")

_IMPORT_RE = re.compile(r"^\s*@(\S+)\s*$", re.MULTILINE)
_SKILL_LINE_RE = re.compile(r"^Skill file: (.+?)\s*$", re.MULTILINE)
# The block plus the blank line the install put before it and the newline after.
_BLOCK_RE = re.compile(
    r"\n?" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.DOTALL
)


class GuidanceError(ValueError):
    """The layout cannot be staged into as asked."""


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GuidanceLayout:
    """Where the guidance and each harness's instruction entry live.

    Every path is explicit; ``for_home`` derives the audited defaults from one
    home directory and the environment it is handed, never the real one.
    """

    home: Path
    claude_home: Path
    codex_home: Path
    dsh_home: Path
    shared_skills: Path
    shared_instructions: Path

    @classmethod
    def for_home(cls, home: Path, *, env: Mapping[str, str] | None = None) -> GuidanceLayout:
        env = env or {}
        home = Path(home)
        codex = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"
        dsh = Path(env["DSH_HOME"]) if env.get("DSH_HOME") else home / ".local/state/ccdb/dsh"
        return cls(
            home=home,
            claude_home=home / ".claude",
            codex_home=codex,
            dsh_home=dsh,
            shared_skills=home / ".agents" / "skills",
            shared_instructions=home / "AGENTS.md",
        )

    @property
    def skill_dir(self) -> Path:
        return self.shared_skills / SKILL_NAME

    @property
    def skill_file(self) -> Path:
        return self.skill_dir / "SKILL.md"

    @property
    def manifest_path(self) -> Path:
        return self.skill_dir / MANIFEST_NAME

    def entry_file(self, harness: Harness) -> Path:
        if harness == "claude":
            return self.claude_home / "CLAUDE.md"
        if harness == "codex":
            return self.codex_home / "AGENTS.md"
        return self.dsh_home / "AGENTS.md"

    def skill_links(self) -> tuple[Path, ...]:
        """Where harness skill discovery should find the shared folder."""
        return (
            self.claude_home / "skills" / SKILL_NAME,
            self.codex_home / "skills" / SKILL_NAME,
        )


# --------------------------------------------------------------------------- #
# Content
# --------------------------------------------------------------------------- #


def render_skill() -> str:
    """The one guidance file. Repo-owned text only; nothing from the rejected draft."""
    description = (
        "Supporting guidance for planning a Go Work build inside an existing "
        "conversation: one short lettered question at a time, settled answers kept, "
        "every agreed outcome turned into a small complete worker task, and the plan "
        "exported in the form the installed runner validates. Use while brainstorming, "
        "answering planning questions or writing build tasks; not for an already scoped "
        "small fix, a factual question, or running an existing plan."
    )
    return "\n".join(
        [
            "---",
            f"name: {SKILL_NAME}",
            f"description: {description}",
            "---",
            "",
            "# Go Work planning (supporting guidance)",
            "",
            "This refines the planning you already do — the one-question skill's short "
            "lettered questions and the project's detailed plan kept on disk. It replaces "
            "nothing and is not a second planning system. The runner enforces dependencies, "
            "ownership and capacity in code; writing the fields below does not start a "
            "build, and a planning answer is never permission to launch.",
            "",
            "## How to work",
            "",
            PLANNER_RULES.rstrip(),
            "",
            "## How to write to the person",
            "",
            COMMUNICATION_RULES.rstrip(),
            "",
            "## What a ready plan contains",
            "",
            "- `Goal:`, `Done when:` and a real standalone `Check:` command; a `Try:` line "
            "when there is something to try.",
            "- `## Decisions`: every settled answer, one line each (workers read these and "
            "never reopen them).",
            "- `## Agreed outcomes`: the requirement ids the person agreed to; every one "
            "must be delivered by at least one task.",
            "- A ```gowork-plan``` manifest: plans (stable id, version, project path, "
            "parent), requirements, and tasks with id, outcome, dependencies, owned "
            "files/resources, required inputs, output, acceptance check and source "
            "requirement. A task in another project that consumes a result names it as a "
            "required input (`<task-id>: what it consumes`). Two tasks that may run "
            "together never own the same file or folder.",
            "- `## Tasks`: one `- [ ]` line per task in dependency order, each with its "
            "`Depends on:` / `Inputs:` / `Files:` / `Result:` / `Verify:` lines, so an "
            "older runner builds the same work one step at a time.",
            "",
            "Before calling a plan ready, check it the way the runner will:",
            "",
            "    python -m claude_code_core.gowork_export check <plan.md>",
            "",
            "It names a dependency cycle, an unsafe owned path, an uncovered outcome, or "
            "what the installed runner cannot build. `python -m claude_code_core."
            "gowork_export template` prints the template below.",
            "",
            "## Plan template",
            "",
            plan_template().rstrip(),
            "",
            "## Where this text lives",
            "",
            "The rules are `claude_code_core/gowork_prompts.py` and the template is "
            "`claude_code_core/gowork_export.py` in claude-code-discord-bridge; this file "
            "is generated from them and re-staged by "
            "`python -m claude_code_core.gowork_guidance stage --home <home>`.",
            "",
        ]
    )


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def guidance_digest() -> str:
    return digest_of(render_skill())


def routing_block(skill_file: Path) -> str:
    """The short rule every harness instruction file carries; the skill holds the rest."""
    return "\n".join(
        [
            BLOCK_START,
            "For project brainstorming, requirements discussion or writing a build plan, "
            "read the Go Work planning guidance below and continue the project's saved "
            "planning state; skip it for an already scoped small fix, a factual question or "
            "running an existing plan.",
            f"Skill file: {skill_file.as_posix()}",
            BLOCK_END,
        ]
    )


# --------------------------------------------------------------------------- #
# Filesystem helpers (each one small, each one testable)
# --------------------------------------------------------------------------- #


def is_link(path: Path) -> bool:
    """A symlink, or a directory junction on Windows."""
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def _symlink(target: Path, link: Path, *, directory: bool) -> None:
    os.symlink(target, link, target_is_directory=directory)


def _junction(target: Path, link: Path, *, directory: bool) -> None:
    if not directory or sys.platform != "win32":
        raise OSError("junctions are for directories on Windows only")
    import _winapi  # noqa: PLC0415 - Windows only

    _winapi.CreateJunction(str(target), str(link))


def _remove_link(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)  # a directory symlink or junction on Windows


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _link_points_at(link: Path, target: Path) -> bool:
    try:
        return link.resolve() == target.resolve()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Actions and reports
# --------------------------------------------------------------------------- #

ActionKind = Literal["write", "link", "block", "backup", "unchanged", "manual", "remove", "restore"]


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    path: Path
    detail: str = ""


@dataclass
class StageReport:
    actions: list[Action] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(a.kind not in ("unchanged", "manual") for a in self.actions)

    @property
    def manual(self) -> list[Action]:
        return [a for a in self.actions if a.kind == "manual"]

    def add(self, kind: ActionKind, path: Path, detail: str = "") -> None:
        self.actions.append(Action(kind, path, detail))


@dataclass(frozen=True, slots=True)
class Resolution:
    """Which skill file a harness reaches, and through which files."""

    skill_path: Path
    digest: str
    via: tuple[str, ...]


# --------------------------------------------------------------------------- #
# Staging
# --------------------------------------------------------------------------- #


class _Stager:
    def __init__(self, layout: GuidanceLayout, *, dry_run: bool) -> None:
        self.layout = layout
        self.dry = dry_run
        self.report = StageReport()
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        path = self.layout.manifest_path
        if path.is_file():
            try:
                return json.loads(_read(path))
            except (OSError, ValueError):
                pass
        return {"skill_file": "", "links": [], "instruction_files": {}, "digest": ""}

    def _ensure_dir(self, path: Path) -> None:
        """Create *path*, remembering every folder that did not exist (rollback removes
        those again when they are left empty)."""
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        path.mkdir(parents=True, exist_ok=True)
        created: list = self.manifest.setdefault("created_dirs", [])
        for folder in missing:
            if str(folder) not in created:
                created.append(str(folder))

    # -- pieces ------------------------------------------------------------ #

    def skill(self) -> None:
        text = render_skill()
        path = self.layout.skill_file
        if path.is_file() and _read(path) == text:
            self.report.add("unchanged", path)
        else:
            self.report.add("write", path, "the shared guidance skill")
            if not self.dry:
                self._ensure_dir(path.parent)
                path.write_text(text, encoding="utf-8")
        self.manifest["skill_file"] = str(path)
        self.manifest["digest"] = digest_of(text)

    def _record_file(self, path: Path, *, created: bool, link: bool = False) -> None:
        files: dict = self.manifest.setdefault("instruction_files", {})
        entry = files.get(str(path))
        if entry is None:
            files[str(path)] = {"created": created, "link": link}

    def upsert_block(self, path: Path) -> None:
        block = routing_block(self.layout.skill_file)
        if not path.exists():
            self.report.add("block", path, "created with the routing rule")
            self._record_file(path, created=True)
            if not self.dry:
                self._ensure_dir(path.parent)
                path.write_text(block + "\n", encoding="utf-8")
            return
        text = _read(path)
        if block in text and text.count(BLOCK_START) == 1:
            self.report.add("unchanged", path)
            self._record_file(path, created=False)
            return
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            self.report.add("backup", backup, f"copy of {path.name} before its first change")
            if not self.dry:
                backup.write_text(text, encoding="utf-8")
        if BLOCK_START in text:
            new_text = _BLOCK_RE.sub(block + "\n", text, count=1)
            new_text = _BLOCK_RE.sub("", new_text)  # any stray duplicate
            detail = "routing rule updated"
        else:
            body = text if text.endswith("\n") or not text else text + "\n"
            new_text = body + "\n" + block + "\n" if body.strip() else block + "\n"
            detail = "routing rule appended"
        self.report.add("block", path, detail)
        self._record_file(path, created=False)
        if not self.dry:
            path.write_text(new_text, encoding="utf-8")

    def _reaches_shared(self, entry: Path) -> bool:
        """Whether *entry* already imports the shared instruction file."""
        if not entry.is_file():
            return False
        for match in _IMPORT_RE.finditer(_read(entry)):
            target = _expand(match.group(1), base=entry.parent, home=self.layout.home)
            if _same(target, self.layout.shared_instructions):
                return True
        return False

    def claude_entry(self) -> None:
        entry = self.layout.entry_file("claude")
        if self._reaches_shared(entry):
            self.report.add("unchanged", entry, "imports the shared instructions")
            return
        self.upsert_block(entry)

    def linked_entry(self, harness: Harness) -> None:
        entry = self.layout.entry_file(harness)
        shared = self.layout.shared_instructions
        if is_link(entry) or entry.is_symlink():
            if _link_points_at(entry, shared):
                self.report.add("unchanged", entry, "links to the shared instructions")
            else:
                self.report.add("manual", entry, "links elsewhere; point it at the shared file")
            return
        if entry.exists():
            self.upsert_block(entry)
            return
        if _same(entry, shared):
            self.report.add("unchanged", entry, "is the shared instructions file")
            return
        try:
            if not self.dry:
                self._ensure_dir(entry.parent)
                _symlink(shared, entry, directory=False)
            detail = f"-> {shared}" + (
                " (or a file with the routing rule when links are unavailable)" if self.dry else ""
            )
            self.report.add("link", entry, detail)
            self._record_file(entry, created=True, link=True)
        except OSError:
            self.upsert_block(entry)

    def skill_link(self, link: Path) -> None:
        target = self.layout.skill_dir
        if is_link(link):
            if _link_points_at(link, target):
                self.report.add("unchanged", link)
            else:
                self.report.add("manual", link, f"links elsewhere; expected {target}")
            return
        if link.exists():
            self.report.add(
                "manual", link, f"a real folder is in the way; link it to {target} yourself"
            )
            return
        if not self.dry:
            self._ensure_dir(link.parent)
            for make in (_symlink, _junction):
                try:
                    make(target, link, directory=True)
                    break
                except OSError:
                    continue
            else:
                self.report.add(
                    "manual",
                    link,
                    f"could not link {link} -> {target} ({SKILL_NAME}); make the link by "
                    "hand or rely on the instruction route",
                )
                return
        self.report.add("link", link, f"-> {target}")
        links: list = self.manifest.setdefault("links", [])
        if str(link) not in links:
            links.append(str(link))

    def write_manifest(self) -> None:
        if self.dry:
            return
        path = self.layout.manifest_path
        self._ensure_dir(path.parent)
        path.write_text(json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8")

    def run(self) -> StageReport:
        if not self.layout.home.is_dir():
            raise GuidanceError(f"home {self.layout.home} is not a directory")
        self.skill()
        self.upsert_block(self.layout.shared_instructions)
        self.claude_entry()
        self.linked_entry("codex")
        self.linked_entry("dsh")
        for link in self.layout.skill_links():
            self.skill_link(link)
        self.write_manifest()
        return self.report


def _expand(reference: str, *, base: Path, home: Path) -> Path:
    if reference.startswith("~/"):
        return home / reference[2:]
    path = Path(reference)
    return path if path.is_absolute() else base / path


def _same(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return False


def plan_guidance(layout: GuidanceLayout) -> StageReport:
    """What ``stage_guidance`` would do, without writing anything."""
    return _Stager(layout, dry_run=True).run()


def stage_guidance(layout: GuidanceLayout) -> StageReport:
    """Stage the guidance into *layout*: idempotent, backed up, nothing outside it."""
    return _Stager(layout, dry_run=False).run()


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #


def rollback_guidance(layout: GuidanceLayout) -> StageReport:
    """Remove what the manifest says is ours; keep every edit made since."""
    report = StageReport()
    manifest_path = layout.manifest_path
    if not manifest_path.is_file():
        report.add("unchanged", layout.skill_dir, "nothing staged here")
        return report
    try:
        manifest = json.loads(_read(manifest_path))
    except (OSError, ValueError) as exc:
        raise GuidanceError(f"unreadable install manifest {manifest_path}: {exc}") from exc

    for raw in manifest.get("links", []):
        link = Path(raw)
        if is_link(link):
            _remove_link(link)
            report.add("remove", link, "skill link")

    for raw, entry in manifest.get("instruction_files", {}).items():
        path = Path(raw)
        if entry.get("link"):
            if is_link(path) or path.is_symlink():
                _remove_link(path)
                report.add("remove", path, "instruction link")
            continue
        if not path.is_file():
            continue
        text = _read(path)
        if BLOCK_START not in text:
            continue
        remainder = _BLOCK_RE.sub("", text)
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if entry.get("created") and not remainder.strip():
            path.unlink()
            report.add("remove", path, "created by the install")
        elif backup.is_file() and _read(backup).rstrip("\n") == remainder.rstrip("\n"):
            path.write_text(_read(backup), encoding="utf-8")
            backup.unlink()
            report.add("restore", path, "matches the backup; backup removed")
        else:
            path.write_text(remainder, encoding="utf-8")
            report.add("restore", path, "routing rule removed; later edits kept")

    skill_file = Path(manifest.get("skill_file") or layout.skill_file)
    if skill_file.is_file():
        skill_file.unlink()
        report.add("remove", skill_file, "the shared guidance skill")
    manifest_path.unlink()
    report.add("remove", manifest_path)
    created_dirs = {Path(raw) for raw in manifest.get("created_dirs", [])} | {layout.skill_dir}
    for folder in sorted(created_dirs, key=lambda p: len(str(p)), reverse=True):
        with contextlib.suppress(OSError):
            folder.rmdir()  # only when empty: anything of the person's stays
    return report


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def resolve_guidance(layout: GuidanceLayout, harness: Harness) -> Resolution | None:
    """Follow *harness*'s instruction entry to the skill file it names, if any."""
    seen: set[Path] = set()

    def follow(path: Path, via: list[str], depth: int) -> Resolution | None:
        if depth > 4 or not path.is_file():
            return None
        key = path.resolve() if path.exists() else path
        if key in seen:
            return None
        seen.add(key)
        text = _read(path)
        chain = [*via, str(path)]
        if BLOCK_START in text:
            match = _SKILL_LINE_RE.search(text)
            if match is not None:
                skill = Path(match.group(1))
                if skill.is_file():
                    skill_text = _read(skill)
                    return Resolution(skill, digest_of(skill_text), (*chain, str(skill)))
        for match in _IMPORT_RE.finditer(text):
            target = _expand(match.group(1), base=path.parent, home=layout.home)
            found = follow(target, chain, depth + 1)
            if found is not None:
                return found
        return None

    return follow(layout.entry_file(harness), [], 0)


# --------------------------------------------------------------------------- #
# Command line
# --------------------------------------------------------------------------- #


def _print_report(report: StageReport) -> None:
    for action in report.actions:
        line = f"  {action.kind:<9} {action.path}"
        if action.detail:
            line += f"  ({action.detail})"
        print(line)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m claude_code_core.gowork_guidance",
        description=(
            "Stage the shared Go Work planning guidance into a home directory for "
            "inspection. There is no default home: pass the one you mean."
        ),
    )
    parser.add_argument("command", choices=("plan", "stage", "rollback", "check"))
    parser.add_argument("--home", required=True, type=Path, help="the home to stage into")
    parser.add_argument("--codex-home", type=Path, help="Codex home (default: <home>/.codex)")
    parser.add_argument(
        "--dsh-home", type=Path, help="DSH home (default: <home>/.local/state/ccdb/dsh)"
    )
    args = parser.parse_args(argv)
    env: dict[str, str] = {}
    if args.codex_home:
        env["CODEX_HOME"] = str(args.codex_home)
    if args.dsh_home:
        env["DSH_HOME"] = str(args.dsh_home)
    layout = GuidanceLayout.for_home(args.home, env=env)
    try:
        if args.command == "check":
            missing = 0
            for harness in HARNESSES:
                found = resolve_guidance(layout, harness)
                if found is None:
                    missing += 1
                    entry = layout.entry_file(harness)
                    print(f"  {harness:<7} does not reach the guidance ({entry})")
                else:
                    stale = "" if found.digest == guidance_digest() else " (stale: re-stage)"
                    print(f"  {harness:<7} -> {found.skill_path}{stale}")
            return 1 if missing else 0
        if args.command == "plan":
            report = plan_guidance(layout)
            print(f"Would stage into {layout.home}:")
        elif args.command == "stage":
            report = stage_guidance(layout)
            print(f"Staged into {layout.home}:")
        else:
            report = rollback_guidance(layout)
            print(f"Rolled back in {layout.home}:")
    except GuidanceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    if report.manual:
        print("Manual steps remain (nothing was copied):")
        for action in report.manual:
            print(f"  - {action.detail}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
