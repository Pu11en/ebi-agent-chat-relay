"""Prompts used to resume interrupted agent turns after a bot restart."""

from __future__ import annotations


def build_restart_resume_prompt(*, after_upgrade: bool = False) -> str:
    """Preserve the authority already present in the saved conversation."""
    restart = (
        "The bot restarted after a package upgrade." if after_upgrade else "The bot restarted."
    )
    return (
        f"{restart} Use the saved conversation to report where work was interrupted, then "
        "continue the authorized task. Do not ask the user to repeat or reconfirm instructions "
        "already in the conversation. If required context is genuinely missing, explain the exact "
        "missing decision before asking one question."
    )
