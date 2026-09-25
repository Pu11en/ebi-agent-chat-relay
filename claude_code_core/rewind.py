"""JSONL session rewind utilities.

Claude Code stores conversation history as JSONL files under:
    ~/.claude/projects/{project_dir}/{session_id}.jsonl

where ``project_dir`` is derived from the working directory by replacing
``/`` and ``_`` with ``-``.

Rewinding means truncating the JSONL at a selected user turn, removing
that turn and everything after it so that ``--resume session_id`` picks
up from the preceding conversation state.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Max label length for Discord select options
_MAX_LABEL_LEN = 80


@dataclass(frozen=True)
class TurnEntry:
    """A single user turn extracted from a session JSONL."""

    line_index: int  # 0-based line index in the JSONL file
    uuid: str
    timestamp: str | None
    text: str  # Truncated preview of the user message


def _cwd_to_project_dir(working_dir: str) -> str:
    """Convert a working directory path to Claude's project directory name.

    Claude Code uses the working directory with ``/`` and ``_`` replaced by ``-``:
        /home/ebi/foo_bar  ->  -home-ebi-foo-bar
    """
    return working_dir.replace("/", "-").replace("_", "-")


def find_session_jsonl(session_id: str, working_dir: str | None) -> Path | None:
    """Locate the JSONL file for a given session.

    Tries the expected path first (derived from ``working_dir``), then falls
    back to a glob search across all project directories so that sessions
    whose working directory differs from what was recorded still work.

    Returns ``None`` when the file cannot be found.
    """
    if working_dir:
        project_dir = _cwd_to_project_dir(working_dir)
        candidate = _CLAUDE_PROJECTS_DIR / project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate

    # Fallback: search all project subdirectories
    for jsonl in _CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"):
        return jsonl

    return None


def parse_user_turns(jsonl_path: Path, *, max_turns: int = 25) -> list[TurnEntry]:
    """Parse user turns from a session JSONL file.

    Reads every line of the file (conversation histories are typically small
    relative to memory) and collects entries where ``type == "user"`` and
    the content is a real user message (not internal meta messages or
    XML-prefixed control content).

    Returns up to ``max_turns`` entries (the most recent ones), preserving
    chronological order so the select menu reads oldest-to-newest.
    """
    try:
        with open(jsonl_path) as f:
            lines = f.readlines()
    except OSError:
        logger.warning("Cannot read JSONL for rewind: %s", jsonl_path)
        return []

    turns: list[TurnEntry] = []
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            continue

        if data.get("type") != "user":
            continue
        if data.get("isMeta"):
            continue

        content = _extract_text(data.get("message", {}).get("content", ""))
        if not content or content.startswith("<"):
            continue

        turns.append(
            TurnEntry(
                line_index=i,
                uuid=data.get("uuid", ""),
                timestamp=data.get("timestamp"),
                text=content[:_MAX_LABEL_LEN],
            )
        )

    # Return only the most recent max_turns (Discord Select allows max 25 options)
    return turns[-max_turns:]


def truncate_jsonl_at_line(jsonl_path: Path, line_index: int) -> bool:
    """Truncate the JSONL so that ``line_index`` and everything after is removed.

    Keeps lines 0 .. line_index - 1.  Effectively rewinds the conversation to
    the state just before the user message at ``line_index`` was sent.

    Returns ``True`` on success, ``False`` on I/O error.
    """
    try:
        with open(jsonl_path) as f:
            lines = f.readlines()
        with open(jsonl_path, "w") as f:
            f.writelines(lines[:line_index])
        logger.info(
            "Rewound JSONL %s: truncated %d -> %d lines",
            jsonl_path.name,
            len(lines),
            line_index,
        )
        return True
    except OSError:
        logger.error("Failed to truncate JSONL: %s", jsonl_path, exc_info=True)
        return False


def _extract_text(content: object) -> str:
    """Extract plain text from a message content field.

    The ``content`` field can be a bare string or a list of content blocks
    (OpenAI-style). Only ``text`` blocks are included.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
            elif isinstance(block, str):
                parts.append(block.strip())
        return " ".join(p for p in parts if p)
    return ""


def copy_session_jsonl(session_id: str, working_dir: str | None) -> str | None:
    """Copy a session transcript to a fresh session id and return that id.

    A fork has to be a *separate* conversation, and sharing the parent's
    session id does not make one: ``--resume <id>`` appends to the transcript
    named by that id, so two threads holding the same id interleave their turns
    into one file and each one reads the other's messages back as its own
    history. Measured on 2026-09-25 against a live pair of threads — 688 lines,
    two unrelated conversations, the user asking mid-thread why he was being
    told about the other one.

    ``--resume --fork-session`` is meant to prevent exactly that and is passed,
    but the same measurement shows the forked turn landing in the parent's file
    anyway, so the separation cannot rest on the flag being honoured. Copying
    the transcript ourselves makes the fork independent from its first turn:
    every ``sessionId`` field is rewritten to the new id, so the CLI resumes a
    file that agrees with its own name.

    Returns ``None`` when the transcript cannot be found or copied — the caller
    then falls back to sharing the id, which is no worse than before.
    """
    source = find_session_jsonl(session_id, working_dir)
    if source is None:
        logger.info("No transcript for session %s — fork shares the parent id", session_id)
        return None

    new_id = str(uuid.uuid4())
    destination = source.with_name(f"{new_id}.jsonl")
    try:
        with source.open() as src, destination.open("w") as dst:
            for line in src:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    dst.write(line + "\n")
                    continue
                if isinstance(entry, dict) and entry.get("sessionId"):
                    entry["sessionId"] = new_id
                dst.write(json.dumps(entry) + "\n")
    except OSError:
        logger.error("Failed to copy transcript %s for a fork", source, exc_info=True)
        destination.unlink(missing_ok=True)
        return None

    logger.info("Forked transcript %s -> %s", source.name, destination.name)
    return new_id
