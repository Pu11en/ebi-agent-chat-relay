"""Natural-language triggers for cross-agent handoff requests."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_LOOKUP_QUERY_CHARS = 500

_DREWAI_NAME_RE = r"drew\s*ai"
_SPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[\s.?!,;:]+$")
_LOOKUP_PATTERNS = [
    re.compile(
        rf"\buse\s+{_DREWAI_NAME_RE}\s+to\s+find(?:\s+info\s+(?:on|about))?\s+(?P<query>.+)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\bask\s+{_DREWAI_NAME_RE}\s+to\s+search\s+drew'?s\s+projects\s+for\s+"
        r"(?P<query>.+)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\bask\s+{_DREWAI_NAME_RE}\s+to\s+look\s+in\s+drew'?s\s+projects\s+for\s+"
        r"(?P<query>.+)",
        re.IGNORECASE | re.DOTALL,
    ),
]


@dataclass(frozen=True)
class DrewAILookupTrigger:
    """A parsed request for DrewAI to search Drew's main project folders."""

    query: str
    agent_id: str = "drewai"
    intent: str = "project_lookup"


def parse_drewai_lookup_trigger(text: str) -> DrewAILookupTrigger | None:
    """Return a DrewAI project lookup request parsed from ordinary Discord text."""
    normalized = _SPACE_RE.sub(" ", text.strip())
    if not normalized:
        return None

    for pattern in _LOOKUP_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue

        query = _clean_query(match.group("query"))
        if not query:
            return None
        return DrewAILookupTrigger(query=query[:MAX_LOOKUP_QUERY_CHARS])

    return None


def _clean_query(query: str) -> str:
    return _TRAILING_PUNCTUATION_RE.sub("", _SPACE_RE.sub(" ", query.strip()))


__all__ = [
    "DrewAILookupTrigger",
    "MAX_LOOKUP_QUERY_CHARS",
    "parse_drewai_lookup_trigger",
]
