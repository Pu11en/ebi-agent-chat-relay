"""Prompt and config helpers for DrewAI project lookup workers."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_project_lookup_root(*, configured: str | None = None) -> str:
    """Return the directory a project lookup worker is allowed to inspect."""
    root = (
        configured
        or os.getenv("CCDB_PROJECT_LOOKUP_ROOT", "").strip()
        or _first_project_root()
        or "/home/drewp/main-projects"
    )
    path = Path(root).expanduser()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"project lookup root is not a directory: {path}")
    return str(path)


def build_project_lookup_prompt(
    *,
    query: str,
    project_root: str,
    from_agent: str | None = None,
    from_thread: int | None = None,
) -> str:
    """Build the instruction for a cheap/read-only project search worker."""
    origin_bits: list[str] = []
    if from_agent:
        origin_bits.append(f"requesting agent: {from_agent}")
    if from_thread is not None:
        origin_bits.append(f"requesting thread: {from_thread}")
    origin = "\n".join(f"- {bit}" for bit in origin_bits) or "- not provided"
    return (
        "You are DrewAI's project lookup worker.\n\n"
        "Goal: answer the requesting agent by searching Drew's project folders.\n\n"
        f"Project root: `{project_root}`\n\n"
        "Rules:\n"
        "- This task is read-only.\n"
        "- Do not edit, create, delete, move, format, commit, push, or restart anything.\n"
        "- Use efficient local search first, like `rg`, `find`, `git remote -v`, and short "
        "file previews.\n"
        "- Stay inside the project root unless the request explicitly names another allowed path.\n"
        "- Return exact paths, relevant file names, and a short plain-English summary.\n"
        "- If there is a git remote, include it.\n"
        "- If nothing matches, say what you searched and the closest likely folders.\n\n"
        "Origin:\n"
        f"{origin}\n\n"
        "Lookup request:\n"
        f"{query.strip()}\n"
    )


def project_lookup_thread_name(query: str) -> str:
    """Return a compact Discord thread name for a project lookup."""
    cleaned = " ".join(query.split())
    suffix = cleaned[:78] if cleaned else "Project lookup"
    return f"🔎 Project lookup · {suffix}"[:100]


def _first_project_root() -> str | None:
    for raw in os.getenv("CCDB_PROJECT_ROOTS", "").split(","):
        value = raw.strip().strip("'\"")
        if value:
            return value
    return None


__all__ = [
    "build_project_lookup_prompt",
    "project_lookup_thread_name",
    "resolve_project_lookup_root",
]
