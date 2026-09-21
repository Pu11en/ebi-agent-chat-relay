"""Read-only discovery of harness inputs under approved roots (task 2.1).

Discovery answers "what *could* a Discord-launched Claude or Codex read on
this machine?" and nothing more.  It walks the roots it is handed — a home,
a Claude home, a Codex home and optionally one project — and records safe
metadata for every file in a location the harness documentation names:
kind, scope, permissions, exact size, a salted hash and whether the content
matched a secret rule.  Whether a file is *effectively loaded* is a separate
question answered by the collectors in tasks 2.2 and 2.3 from invocation and
session evidence; discovery never claims it.

Three properties are enforced here rather than documented:

* **Never the real home by accident.**  There are no defaults: every root is
  passed in, and a root that does not exist is refused.  Tests hand in a
  fake home; the CLI resolves the real one explicitly.
* **Never outside the roots.**  A symlink is resolved, and a target outside
  the approved roots is excluded with a reason instead of being read.  A
  link into ``/etc`` is not harness configuration.
* **Never content.**  A record carries a hash, a size, names and counts.
  Setting *values* are dropped at parse time; a file that matches a secret
  rule is marked ``redacted`` so later stages know a value was present.

Symlinks and file modes are read from the filesystem, but both can also be
*declared* on :class:`DiscoveryRoots` (``links`` and ``modes``).  Creating a
symlink or a 0600 file needs privileges a Windows test run does not have, so
fixtures declare them; production passes nothing and the real ``os`` answers
(on Windows the answer for a mode is ``unknown``: POSIX bits mean nothing there).
This module reads; it never writes anywhere.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from extensions.harness_audit.models import (
    Harness,
    RedactionStatus,
    SchemaError,
    Scope,
    SizeMeasurement,
    SourceKind,
)
from extensions.harness_audit.redaction import measure, safe_hash, scan

_HOME_DISPLAY = "~"

# Claude Code hook lifecycle events documented by the vendor.  A hook on any
# other event is registered but can never fire (task 3.1 reads this).
CLAUDE_HOOK_EVENTS: frozenset[str] = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "Notification",
        "UserPromptSubmit",
        "Stop",
        "SubagentStop",
        "SessionStart",
        "SessionEnd",
        "PreCompact",
        "PermissionRequest",
    }
)

_FRONTMATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_FRONTMATTER_NAME = re.compile(r"^name:\s*(?P<name>[\w.\-]+)\s*$", re.MULTILINE)
_IMPORT = re.compile(r"(?m)^\s*@[\w./~\\-]+")


class DiscoveryError(SchemaError):
    """The roots are unusable, or a record is malformed."""


# --------------------------------------------------------------------------- #
# Roots
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DiscoveryRoots:
    """Where discovery may look.  Nothing outside these paths is ever read."""

    home: Path
    claude_home: Path
    codex_home: Path
    project_dir: Path | None = None
    known_projects: tuple[str, ...] = ()
    links: Mapping[Path, Path] = field(default_factory=dict)
    modes: Mapping[Path, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("home", "claude_home", "codex_home"):
            path = Path(getattr(self, name))
            if not path.is_dir():
                raise DiscoveryError(f"roots: {name} {str(path)!r} is not a directory")
            object.__setattr__(self, name, path)
        if self.project_dir is not None:
            project = Path(self.project_dir)
            if not project.is_dir():
                raise DiscoveryError(f"roots: project_dir {str(project)!r} is not a directory")
            object.__setattr__(self, "project_dir", project)
        projects = list(self.known_projects)
        if self.project_dir is not None and self.project_dir.name not in projects:
            projects.append(self.project_dir.name)
        object.__setattr__(self, "known_projects", tuple(sorted(set(projects))))
        object.__setattr__(
            self, "links", {Path(link): Path(target) for link, target in self.links.items()}
        )
        object.__setattr__(self, "modes", {Path(path): mode for path, mode in self.modes.items()})

    @property
    def approved_roots(self) -> tuple[Path, ...]:
        roots = [self.claude_home, self.codex_home]
        if self.project_dir is not None:
            roots.append(self.project_dir)
        return tuple(roots)

    @property
    def approved_files(self) -> tuple[Path, ...]:
        """Files directly under the home that a harness reads by name."""
        return (self.home / "AGENTS.md", self.home / "CLAUDE.md", self.home / ".claude.json")

    def display(self, path: Path) -> str:
        """A path as the bundle shows it: ``~``-relative, forward slashes."""
        try:
            return f"{_HOME_DISPLAY}/{path.relative_to(self.home).as_posix()}"
        except ValueError:
            return path.as_posix()

    def is_approved(self, path: Path) -> bool:
        if path in self.approved_files:
            return True
        return any(_is_under(path, root) for root in self.approved_roots)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HookRegistration:
    """A hook as registered in a settings file: event, matcher, command hash."""

    event: str
    matcher: str
    command_hash: str
    command_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "event": self.event,
            "matcher": self.matcher,
            "command_hash": self.command_hash,
            "command_bytes": self.command_bytes,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        return cls(
            event=_req_str(payload, "event"),
            matcher=_req_str(payload, "matcher"),
            command_hash=_req_str(payload, "command_hash"),
            command_bytes=_req_int(payload, "command_bytes"),
        )


@dataclass(frozen=True, slots=True)
class SettingsFacts:
    """The shape of a settings file, with every value dropped.

    ``model`` and ``reasoning_effort`` are kept as names because the audit
    must say which configuration path it inspected; it never changes them.
    """

    keys: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    permission_rules: Mapping[str, int] = field(default_factory=dict)
    hooks: tuple[HookRegistration, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    disabled_mcp_servers: tuple[str, ...] = ()
    model: str = ""
    reasoning_effort: str = ""
    sandbox_mode: str = ""
    approval_policy: str = ""
    parse_error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "permission_rules", dict(sorted(self.permission_rules.items())))

    def to_dict(self) -> dict[str, object]:
        return {
            "keys": list(self.keys),
            "env_names": list(self.env_names),
            "permission_rules": dict(self.permission_rules),
            "hooks": [hook.to_dict() for hook in self.hooks],
            "mcp_servers": list(self.mcp_servers),
            "disabled_mcp_servers": list(self.disabled_mcp_servers),
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "sandbox_mode": self.sandbox_mode,
            "approval_policy": self.approval_policy,
            "parse_error": self.parse_error,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        rules = payload.get("permission_rules", {})
        if not isinstance(rules, Mapping):
            raise DiscoveryError("settings: permission_rules must be an object")
        return cls(
            keys=_str_tuple(payload, "keys"),
            env_names=_str_tuple(payload, "env_names"),
            permission_rules={str(key): int(value) for key, value in rules.items()},  # type: ignore[call-overload]
            hooks=tuple(
                HookRegistration.from_dict(_obj(entry)) for entry in _list(payload, "hooks")
            ),
            mcp_servers=_str_tuple(payload, "mcp_servers"),
            disabled_mcp_servers=_str_tuple(payload, "disabled_mcp_servers"),
            model=_opt_str(payload, "model"),
            reasoning_effort=_opt_str(payload, "reasoning_effort"),
            sandbox_mode=_opt_str(payload, "sandbox_mode"),
            approval_policy=_opt_str(payload, "approval_policy"),
            parse_error=_opt_str(payload, "parse_error"),
        )


@dataclass(frozen=True, slots=True)
class ContentSignals:
    """Counts and names derived from content — never the content itself."""

    lines: int = 0
    frontmatter_bytes: int = 0
    declared_name: str = ""
    referenced_projects: tuple[str, ...] = ()
    import_references: int = 0
    secret_matches: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "lines": self.lines,
            "frontmatter_bytes": self.frontmatter_bytes,
            "declared_name": self.declared_name,
            "referenced_projects": list(self.referenced_projects),
            "import_references": self.import_references,
            "secret_matches": self.secret_matches,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        return cls(
            lines=_req_int(payload, "lines"),
            frontmatter_bytes=_req_int(payload, "frontmatter_bytes"),
            declared_name=_opt_str(payload, "declared_name"),
            referenced_projects=_str_tuple(payload, "referenced_projects"),
            import_references=_req_int(payload, "import_references"),
            secret_matches=_req_int(payload, "secret_matches"),
        )


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    """One file a harness can read, described by safe metadata only."""

    path: str
    resolved_path: str
    harness: Harness | None
    kind: SourceKind
    scope: Scope
    is_symlink: bool
    permissions: str
    size: SizeMeasurement
    content_hash: str
    redaction: RedactionStatus
    signals: ContentSignals
    settings: SettingsFacts | None = None
    read_error: str = ""

    @property
    def file_id(self) -> str:
        harness = self.harness.value if self.harness is not None else "shared"
        return f"{harness}:{self.path}"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "resolved_path": self.resolved_path,
            "harness": None if self.harness is None else self.harness.value,
            "kind": self.kind.value,
            "scope": self.scope.value,
            "is_symlink": self.is_symlink,
            "permissions": self.permissions,
            "size": self.size.to_dict(),
            "content_hash": self.content_hash,
            "redaction": self.redaction.value,
            "signals": self.signals.to_dict(),
            "settings": None if self.settings is None else self.settings.to_dict(),
            "read_error": self.read_error,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Self:
        harness = payload.get("harness")
        settings = payload.get("settings")
        return cls(
            path=_req_str(payload, "path"),
            resolved_path=_req_str(payload, "resolved_path"),
            harness=None if harness is None else Harness(str(harness)),
            kind=SourceKind(_req_str(payload, "kind")),
            scope=Scope(_req_str(payload, "scope")),
            is_symlink=bool(payload.get("is_symlink", False)),
            permissions=_req_str(payload, "permissions"),
            size=SizeMeasurement.from_dict(_obj(payload.get("size"))),
            content_hash=_req_str(payload, "content_hash"),
            redaction=RedactionStatus(_req_str(payload, "redaction")),
            signals=ContentSignals.from_dict(_obj(payload.get("signals"))),
            settings=None if settings is None else SettingsFacts.from_dict(_obj(settings)),
            read_error=_opt_str(payload, "read_error"),
        )


@dataclass(frozen=True, slots=True)
class ExcludedPath:
    """A path discovery refused to read, and why."""

    path: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class TranscriptFile:
    """A session transcript or rollout the collectors may read for metadata."""

    harness: Harness
    path: str
    absolute: Path

    def to_dict(self) -> dict[str, object]:
        return {"harness": self.harness.value, "path": self.path}


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    files: tuple[DiscoveredFile, ...]
    excluded: tuple[ExcludedPath, ...]
    transcripts: tuple[TranscriptFile, ...]
    salted: bool

    def for_harness(self, harness: Harness) -> tuple[DiscoveredFile, ...]:
        """Files this harness reads: its own plus the shared ones."""
        return tuple(entry for entry in self.files if entry.harness in (harness, None))

    def to_dict(self) -> dict[str, object]:
        return {
            "files": [entry.to_dict() for entry in self.files],
            "excluded": [entry.to_dict() for entry in self.excluded],
            "transcripts": [entry.to_dict() for entry in self.transcripts],
            "salted": self.salted,
        }


# --------------------------------------------------------------------------- #
# Layout: where each harness reads, per the pinned vendor guidance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Entry:
    relative: str  # a path or a glob relative to the root
    kind: SourceKind
    scope: Scope
    harness: Harness | None


def _layout(roots: DiscoveryRoots) -> tuple[tuple[Path, _Entry], ...]:
    claude, codex = Harness.CLAUDE, Harness.CODEX
    g, p = Scope.GLOBAL, Scope.PROJECT
    entries: list[tuple[Path, _Entry]] = [
        (roots.home, _Entry("AGENTS.md", SourceKind.GLOBAL_INSTRUCTIONS, g, None)),
        (roots.home, _Entry("CLAUDE.md", SourceKind.GLOBAL_INSTRUCTIONS, g, None)),
        (roots.home, _Entry(".claude.json", SourceKind.CONNECTOR, g, claude)),
        (roots.claude_home, _Entry("CLAUDE.md", SourceKind.GLOBAL_INSTRUCTIONS, g, claude)),
        (roots.claude_home, _Entry("settings.json", SourceKind.HARNESS_SETTING, g, claude)),
        (roots.claude_home, _Entry("settings.local.json", SourceKind.HARNESS_SETTING, g, claude)),
        (roots.claude_home, _Entry("skills/*/SKILL.md", SourceKind.SKILL, g, claude)),
        (roots.claude_home, _Entry("commands/**/*.md", SourceKind.COMMAND, g, claude)),
        (roots.claude_home, _Entry("agents/*.md", SourceKind.TOOL, g, claude)),
        (
            roots.claude_home,
            _Entry("plugins/*/.claude-plugin/plugin.json", SourceKind.PLUGIN, g, claude),
        ),
        (roots.codex_home, _Entry("AGENTS.md", SourceKind.GLOBAL_INSTRUCTIONS, g, codex)),
        (roots.codex_home, _Entry("config.toml", SourceKind.HARNESS_SETTING, g, codex)),
        (roots.codex_home, _Entry("skills/**/SKILL.md", SourceKind.SKILL, g, codex)),
        (roots.codex_home, _Entry("prompts/*.md", SourceKind.COMMAND, g, codex)),
    ]
    project = roots.project_dir
    if project is not None:
        entries.extend(
            [
                (project, _Entry("CLAUDE.md", SourceKind.PROJECT_INSTRUCTIONS, p, claude)),
                (project, _Entry("CLAUDE.local.md", SourceKind.PROJECT_INSTRUCTIONS, p, claude)),
                (project, _Entry(".claude/CLAUDE.md", SourceKind.PROJECT_INSTRUCTIONS, p, claude)),
                (project, _Entry(".claude/settings.json", SourceKind.HARNESS_SETTING, p, claude)),
                (
                    project,
                    _Entry(".claude/settings.local.json", SourceKind.HARNESS_SETTING, p, claude),
                ),
                (project, _Entry(".claude/skills/*/SKILL.md", SourceKind.SKILL, p, claude)),
                (project, _Entry(".claude/commands/**/*.md", SourceKind.COMMAND, p, claude)),
                (project, _Entry(".claude/agents/*.md", SourceKind.TOOL, p, claude)),
                (project, _Entry(".mcp.json", SourceKind.CONNECTOR, p, claude)),
                (project, _Entry("AGENTS.md", SourceKind.PROJECT_INSTRUCTIONS, p, None)),
                (project, _Entry(".codex/config.toml", SourceKind.HARNESS_SETTING, p, codex)),
            ]
        )
    return tuple(entries)


_TRANSCRIPT_GLOBS: tuple[tuple[Harness, str, str], ...] = (
    (Harness.CLAUDE, "claude_home", "projects/*/*.jsonl"),
    (Harness.CODEX, "codex_home", "sessions/**/rollout-*.jsonl"),
)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def discover(roots: DiscoveryRoots, *, salt: str = "") -> DiscoveryResult:
    """Walk the approved roots and describe every harness input found."""
    files: list[DiscoveredFile] = []
    excluded: list[ExcludedPath] = []
    seen: set[tuple[str, Harness | None]] = set()
    for root, entry in _layout(roots):
        for path in _candidates(root, entry.relative, roots):
            key = (roots.display(path), entry.harness)
            if key in seen:
                continue
            seen.add(key)
            record = _describe(path, entry, roots, salt, excluded)
            if record is not None:
                files.append(record)
    transcripts: list[TranscriptFile] = []
    for harness, root_name, pattern in _TRANSCRIPT_GLOBS:
        root: Path = getattr(roots, root_name)
        for path in sorted(root.glob(pattern)):
            if path.is_file() and not path.is_symlink():
                transcripts.append(TranscriptFile(harness, roots.display(path), path))
    return DiscoveryResult(
        files=tuple(sorted(files, key=lambda entry: entry.file_id)),
        excluded=tuple(sorted(excluded, key=lambda entry: entry.path)),
        transcripts=tuple(sorted(transcripts, key=lambda entry: entry.path)),
        salted=bool(salt),
    )


def _candidates(root: Path, relative: str, roots: DiscoveryRoots) -> list[Path]:
    """On-disk matches plus declared links that would match the same pattern."""
    found: set[Path] = set()
    if any(char in relative for char in "*?["):
        found.update(path for path in root.glob(relative) if _is_file_or_link(path, roots))
        for link in roots.links:
            if _is_under(link, root) and _glob_match(link.relative_to(root).as_posix(), relative):
                found.add(link)
    else:
        path = root / relative
        if _is_file_or_link(path, roots):
            found.add(path)
    return sorted(found)


def _is_file_or_link(path: Path, roots: DiscoveryRoots) -> bool:
    return path in roots.links or path.is_file() or path.is_symlink()


def _glob_match(relative: str, pattern: str) -> bool:
    """``Path.match`` has no ``**`` on 3.12, so declared links use this."""
    regex = ""
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            regex += "(?:.*/)?"
            index += 3
            continue
        if char == "*":
            regex += "[^/]*"
        elif char == "?":
            regex += "[^/]"
        else:
            regex += re.escape(char)
        index += 1
    return re.fullmatch(regex, relative) is not None


def _describe(
    path: Path,
    entry: _Entry,
    roots: DiscoveryRoots,
    salt: str,
    excluded: list[ExcludedPath],
) -> DiscoveredFile | None:
    display = roots.display(path)
    is_link = path in roots.links or path.is_symlink()
    try:
        resolved = _resolve(path, roots)
    except DiscoveryError as error:
        excluded.append(ExcludedPath(display, str(error)))
        return None
    if not roots.is_approved(resolved):
        excluded.append(
            ExcludedPath(display, f"resolves outside the approved roots to {resolved.as_posix()}")
        )
        return None
    if not resolved.is_file():
        excluded.append(ExcludedPath(display, "link target is not a regular file"))
        return None

    try:
        raw = resolved.read_bytes()
    except OSError as error:
        return DiscoveredFile(
            path=display,
            resolved_path=roots.display(resolved),
            harness=entry.harness,
            kind=entry.kind,
            scope=entry.scope,
            is_symlink=is_link,
            permissions=_permissions(resolved, roots),
            size=SizeMeasurement(0, 0),
            content_hash="",
            redaction=RedactionStatus.WITHHELD,
            signals=ContentSignals(),
            read_error=f"{type(error).__name__}: could not read",
        )

    text = raw.decode("utf-8", errors="replace")
    notes = scan(text)
    secret_matches = sum(note.occurrences for note in notes)
    settings = _settings_facts(resolved, entry.kind, text, salt)
    return DiscoveredFile(
        path=display,
        resolved_path=roots.display(resolved),
        harness=entry.harness,
        kind=entry.kind,
        scope=entry.scope,
        is_symlink=is_link,
        permissions=_permissions(resolved, roots),
        size=measure(raw, estimate_tokens=True),
        content_hash=safe_hash(raw, salt=salt),
        redaction=RedactionStatus.REDACTED if secret_matches else RedactionStatus.NONE_NEEDED,
        signals=_signals(text, roots.known_projects, secret_matches),
        settings=settings,
    )


def _resolve(path: Path, roots: DiscoveryRoots) -> Path:
    current = path
    for _ in range(16):
        declared = roots.links.get(current)
        if declared is not None:
            current = declared if declared.is_absolute() else current.parent / declared
            continue
        if current.is_symlink():
            try:
                current = current.resolve(strict=True)
            except OSError as error:
                raise DiscoveryError(f"broken link: {type(error).__name__}") from error
            continue
        return current
    raise DiscoveryError("link chain too deep")


def _permissions(path: Path, roots: DiscoveryRoots) -> str:
    """POSIX mode bits as ``0644``, or ``unknown`` when they mean nothing.

    A declared ``None`` says "not inspectable"; on Windows every file reports
    ``0666`` while the real answer lives in ACLs, so the mode is unknown there
    rather than a false world-writable finding on every file.
    """
    if path in roots.modes:
        declared = roots.modes[path]
        return "unknown" if declared is None else f"{declared:04o}"
    if os.name == "nt":
        return "unknown"
    try:
        return f"{stat.S_IMODE(path.stat().st_mode):04o}"
    except OSError:
        return "unknown"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Content-derived facts (names and counts only)
# --------------------------------------------------------------------------- #


def _signals(text: str, known_projects: Iterable[str], secret_matches: int) -> ContentSignals:
    frontmatter = _FRONTMATTER.match(text)
    declared_name = ""
    frontmatter_bytes = 0
    if frontmatter is not None:
        frontmatter_bytes = len(frontmatter.group(0).encode("utf-8"))
        name = _FRONTMATTER_NAME.search(frontmatter.group("body"))
        if name is not None:
            declared_name = name.group("name")
    referenced = tuple(
        sorted(
            project
            for project in known_projects
            if re.search(rf"(?<![\w-]){re.escape(project)}(?![\w-])", text)
        )
    )
    return ContentSignals(
        lines=text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        frontmatter_bytes=frontmatter_bytes,
        declared_name=declared_name,
        referenced_projects=referenced,
        import_references=len(_IMPORT.findall(text)),
        secret_matches=secret_matches,
    )


def _settings_facts(path: Path, kind: SourceKind, text: str, salt: str) -> SettingsFacts | None:
    if kind not in (SourceKind.HARNESS_SETTING, SourceKind.CONNECTOR, SourceKind.PLUGIN):
        return None
    suffix = path.suffix.lower()
    try:
        if suffix == ".toml":
            payload: object = tomllib.loads(text)
        else:
            payload = json.loads(text)
    except (ValueError, tomllib.TOMLDecodeError) as error:
        return SettingsFacts(parse_error=f"{type(error).__name__}: not parseable")
    if not isinstance(payload, Mapping):
        return SettingsFacts(parse_error="top level is not an object")
    return _toml_facts(payload, salt) if suffix == ".toml" else _json_facts(payload, salt)


def _json_facts(payload: Mapping[str, object], salt: str) -> SettingsFacts:
    env = payload.get("env")
    permissions = payload.get("permissions")
    rules: dict[str, int] = {}
    if isinstance(permissions, Mapping):
        for name in ("allow", "ask", "deny"):
            values = permissions.get(name)
            if isinstance(values, list):
                rules[name] = len(values)
    hooks: list[HookRegistration] = []
    raw_hooks = payload.get("hooks")
    if isinstance(raw_hooks, Mapping):
        for event, groups in raw_hooks.items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, Mapping):
                    continue
                matcher = str(group.get("matcher", ""))
                commands = group.get("hooks")
                for command in commands if isinstance(commands, list) else []:
                    if not isinstance(command, Mapping):
                        continue
                    script = str(command.get("command", ""))
                    hooks.append(
                        HookRegistration(
                            str(event),
                            matcher,
                            safe_hash(script, salt=salt),
                            len(script.encode("utf-8")),
                        )
                    )
    servers = payload.get("mcpServers")
    disabled = payload.get("disabledMcpServers")
    return SettingsFacts(
        keys=tuple(sorted(str(key) for key in payload)),
        env_names=tuple(sorted(str(key) for key in env)) if isinstance(env, Mapping) else (),
        permission_rules=rules,
        hooks=tuple(hooks),
        mcp_servers=tuple(sorted(str(key) for key in servers))
        if isinstance(servers, Mapping)
        else (),
        disabled_mcp_servers=tuple(sorted(str(v) for v in disabled))
        if isinstance(disabled, list)
        else (),
        model=str(payload.get("model", "") or ""),
        reasoning_effort=str(payload.get("effort", "") or ""),
    )


def _toml_facts(payload: Mapping[str, object], salt: str) -> SettingsFacts:
    del salt
    servers = payload.get("mcp_servers")
    names: list[str] = []
    disabled: list[str] = []
    if isinstance(servers, Mapping):
        for name, server in servers.items():
            names.append(str(name))
            if isinstance(server, Mapping) and server.get("enabled") is False:
                disabled.append(str(name))
    return SettingsFacts(
        keys=tuple(sorted(str(key) for key in payload)),
        mcp_servers=tuple(sorted(names)),
        disabled_mcp_servers=tuple(sorted(disabled)),
        model=str(payload.get("model", "") or ""),
        reasoning_effort=str(payload.get("model_reasoning_effort", "") or ""),
        sandbox_mode=str(payload.get("sandbox_mode", "") or ""),
        approval_policy=str(payload.get("approval_policy", "") or ""),
    )


# --------------------------------------------------------------------------- #
# Payload helpers
# --------------------------------------------------------------------------- #


def _obj(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise DiscoveryError(f"expected an object, got {type(payload).__name__}")
    return payload


def _req_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise DiscoveryError(f"field {key!r} must be a string")
    return value


def _opt_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DiscoveryError(f"field {key!r} must be a string")
    return value


def _req_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise DiscoveryError(f"field {key!r} must be an integer")
    return value


def _str_tuple(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key, ())
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise DiscoveryError(f"field {key!r} must be a list of strings")
    return tuple(str(entry) for entry in value)


def _list(payload: Mapping[str, object], key: str) -> list[object]:
    value = payload.get(key, ())
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise DiscoveryError(f"field {key!r} must be a list")
    return list(value)
