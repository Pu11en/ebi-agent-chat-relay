"""Create or clone a project folder from a Discord action, beneath approved roots only.

A button in a chat client can now alter disk state, so the rules here are
strict on purpose and enforced before anything touches the filesystem:

* The destination is one plain folder name beneath a root the instance
  configured (``CCDB_PROJECT_ROOTS``). Path separators, ``..``, absolute
  paths, hidden names, and Windows reserved names are refused, and the result
  is checked to still lie beneath the root after resolution.
* An existing folder is never reused or overwritten; the collision check is
  case-insensitive so Windows and macOS agree with Linux.
* Clone is an argument vector — ``git clone -- <url> <dest>`` — with no shell,
  a ``--`` before the URL so a location can never become a flag, and an
  environment stripped of the bot's secrets.
* A failed clone removes only the folder this attempt created. Anything that
  existed before the attempt is untouched, and nothing is started on failure.

Nothing in this module starts a model turn; it returns a folder the launcher
binds an idle thread to.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Seconds a clone may run before it is killed and cleaned up.
CLONE_TIMEOUT_SECONDS = 600

_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}
_NAME_MAX = 80
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_GITHUB_SHORT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}")
_HTTPS_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::\d+)?/[A-Za-z0-9_./-]+")

#: Runs an argument vector in a working directory and returns the exit code.
#: Injected so tests never spawn git; the default is :func:`run_git`.
CloneRunner = Callable[[Sequence[str], Path], Awaitable[int]]


def configured_root_list(
    env: Mapping[str, str] | None = None, *, fallback: str | None = None
) -> list[str]:
    """The raw ``CCDB_PROJECT_ROOTS`` entries (comma, newline or path-separator list).

    The one parser for every consumer of the setting — Create/Clone, the
    shared catalog and the lookup root — so they cannot disagree about what
    was configured.  Entries are returned as written; callers decide whether a
    missing folder is dropped (Create) or reported (the catalog).
    """
    source = os.environ if env is None else env
    raw = source.get("CCDB_PROJECT_ROOTS", "")
    separators = r"[,\n]|" + re.escape(os.pathsep)
    candidates = [part.strip() for part in re.split(separators, raw) if part.strip()]
    if not candidates and fallback:
        candidates = [fallback]
    return candidates


class ProjectCreationError(ValueError):
    """A creation request was refused or failed; the message is safe to show."""


@dataclass(frozen=True, slots=True)
class ProjectRoots:
    """The folders a Discord action may create projects beneath."""

    paths: tuple[Path, ...]

    @classmethod
    def from_paths(cls, candidates: Iterable[str | Path]) -> ProjectRoots:
        """Keep the candidates that are real, absolute folders."""
        kept: list[Path] = []
        for raw in candidates:
            text = str(raw).strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                logger.debug("Ignoring relative project root %r", text)
                continue
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                logger.debug("Ignoring unavailable project root %r", text)
                continue
            if resolved.is_dir() and resolved not in kept:
                kept.append(resolved)
        return cls(tuple(kept))

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, fallback: str | None = None
    ) -> ProjectRoots:
        """Read ``CCDB_PROJECT_ROOTS`` (comma or path-separator list), else ``fallback``."""
        return cls.from_paths(configured_root_list(env, fallback=fallback))

    def choose(self, root: str | Path | None) -> Path:
        """The approved root a request names, or the first one when it names none."""
        if not self.paths:
            raise ProjectCreationError(
                "No approved project root is configured on this computer (set CCDB_PROJECT_ROOTS)."
            )
        if root is None:
            return self.paths[0]
        try:
            wanted = Path(root).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise ProjectCreationError("That project root is not available.") from exc
        for approved in self.paths:
            if wanted == approved:
                return approved
        raise ProjectCreationError("Projects can only be created beneath an approved root.")


def validate_name(name: str) -> str:
    """One plain folder name: no separators, traversal, hidden or reserved names."""
    name = (name or "").strip()
    if (
        not name
        or len(name) > _NAME_MAX
        or name in {".", ".."}
        or name.startswith((".", "-"))
        or name.endswith((".", " "))
        or _BAD_NAME_CHARS.search(name)
        or name.split(".")[0].upper() in _RESERVED
    ):
        raise ProjectCreationError(
            "Choose a plain folder name: letters, digits, dots, dashes or underscores, "
            "no slashes, and not starting with a dot or dash."
        )
    return name


def resolve_destination(roots: ProjectRoots, root: str | Path | None, name: str) -> Path:
    """The folder a request may create: validated, beneath ``root``, and not yet present."""
    approved = roots.choose(root)
    clean = validate_name(name)
    destination = approved / clean
    # Belt and braces: even a name that passed validation must resolve beneath the root.
    try:
        resolved_parent = destination.parent.resolve()
    except (OSError, RuntimeError) as exc:
        raise ProjectCreationError("That project root is not available.") from exc
    if resolved_parent != approved:
        raise ProjectCreationError("Projects can only be created beneath an approved root.")
    try:
        for existing in approved.iterdir():
            if existing.name.casefold() == clean.casefold():
                raise ProjectCreationError(
                    "That folder name already exists. Choose another name, or open the "
                    "existing folder from Browse."
                )
    except OSError as exc:
        raise ProjectCreationError("That project root cannot be read right now.") from exc
    return destination


def parse_repository(value: str) -> tuple[str, str]:
    """A supported repository location and the folder name it implies.

    Accepts ``owner/repo`` (GitHub) or an ``https://`` URL. SSH, ``file://``,
    local paths and anything that could be read as a flag are refused: the
    location is passed to ``git`` after ``--`` anyway, but a refusal here is
    the message the user sees.
    """
    text = (value or "").strip()
    if not text or text.startswith("-") or any(ch.isspace() for ch in text):
        raise ProjectCreationError("Use a GitHub owner/repo or an https:// repository link.")
    if text.startswith("https://"):
        candidate = text.removesuffix("/")
        if not _HTTPS_URL.fullmatch(candidate):
            raise ProjectCreationError("Use a GitHub owner/repo or an https:// repository link.")
        url = candidate if candidate.endswith(".git") else f"{candidate}.git"
        name = url.rsplit("/", 1)[-1].removesuffix(".git")
    else:
        short = text.removesuffix(".git")
        if not _GITHUB_SHORT.fullmatch(short):
            raise ProjectCreationError("Use a GitHub owner/repo or an https:// repository link.")
        url = f"https://github.com/{short}.git"
        name = short.split("/", 1)[1]
    if name in {".", ".."} or not name:
        raise ProjectCreationError("Use a GitHub owner/repo or an https:// repository link.")
    return url, name


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """The clone's environment: no bot secrets, and git may not prompt."""
    source = dict(os.environ if env is None else env)
    for key in list(source):
        if key.startswith(("DISCORD_", "CCDB_")):
            source.pop(key)
    source.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    return source


async def run_git(argv: Sequence[str], cwd: Path) -> int:
    """Run ``argv`` with no shell and return its exit code; kill it on timeout."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        env=child_env(),
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout=CLONE_TIMEOUT_SECONDS)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise


async def create_project(roots: ProjectRoots, root: str | Path | None, name: str) -> Path:
    """Create one empty folder beneath an approved root and return it."""
    destination = resolve_destination(roots, root, name)
    try:
        await asyncio.to_thread(destination.mkdir)  # exclusive: never reuse a folder
    except FileExistsError as exc:
        raise ProjectCreationError("That folder name already exists.") from exc
    except OSError as exc:
        raise ProjectCreationError("The folder could not be created on this computer.") from exc
    return destination


async def clone_project(
    roots: ProjectRoots,
    root: str | Path | None,
    repository: str,
    *,
    name: str | None = None,
    runner: CloneRunner | None = None,
    git_command: str = "git",
) -> Path:
    """Clone ``repository`` into a new folder beneath an approved root.

    The destination is created first (exclusively, so this attempt owns it)
    and removed again if the clone fails for any reason. A pre-existing folder
    is never touched, and no session is started for a failed clone.
    """
    url, implied = parse_repository(repository)
    destination = await create_project(roots, root, name if name else implied)
    argv = [git_command, "clone", "--", url, str(destination)]
    run = runner if runner is not None else run_git
    try:
        code = await run(argv, destination.parent)
    except TimeoutError:
        _discard(destination)
        raise ProjectCreationError(
            "Cloning timed out. Nothing was started; check the repository and try again."
        ) from None
    except asyncio.CancelledError:
        _discard(destination)
        raise
    except Exception as exc:
        _discard(destination)
        logger.warning("Clone of %s failed before git returned", url, exc_info=True)
        raise ProjectCreationError(
            "Cloning failed on this computer. Nothing was started; try again."
        ) from exc
    if code != 0:
        _discard(destination)
        raise ProjectCreationError(
            "Cloning failed. Check the repository link and this computer's git access, "
            "then try again. Nothing was started."
        )
    return destination


def _discard(destination: Path) -> None:
    """Remove the folder this attempt created; never anything else."""
    shutil.rmtree(destination, ignore_errors=True)
