"""Whitespace-based word counting for the disposable workflow trial."""

from __future__ import annotations


def count_words(text: str) -> int:
    """Count whitespace-separated tokens, preserving punctuation within tokens."""
    return len(text.split())
