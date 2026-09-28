"""Small filesystem classification shared by the two project pickers."""

from __future__ import annotations

from pathlib import Path


def is_linked_worktree(folder: Path) -> bool:
    """Recognize Git's linked-copy metadata, not folder names or every .git file.

    Submodules and separate-git-dir repositories remain projects. This only
    changes discovery; explicit paths and existing sessions remain usable.
    """
    marker = folder / ".git"
    try:
        if not marker.is_file():
            return False
        with marker.open(encoding="utf-8") as handle:
            line = handle.readline(4096).strip()
        if not line.startswith("gitdir: "):
            return False
        metadata = (folder / line.removeprefix("gitdir: ")).resolve()
        with (metadata / "commondir").open(encoding="utf-8") as handle:
            common = handle.readline(4096).strip()
        with (metadata / "gitdir").open(encoding="utf-8") as handle:
            backlink = handle.readline(4096).strip()
        return bool(common and backlink) and (
            (metadata / common).is_dir() and (metadata / backlink).resolve() == marker.resolve()
        )
    except (OSError, ValueError, RuntimeError):
        return False
