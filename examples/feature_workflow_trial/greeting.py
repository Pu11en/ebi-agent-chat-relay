"""Format greetings for the disposable feature workflow trial."""

from __future__ import annotations


def greet(name: str) -> str:
    """Preserve the name, trimming exterior whitespace and defaulting to friend."""
    return f"Hello, {name.strip() or 'friend'}!"
