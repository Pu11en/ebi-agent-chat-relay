"""Every thread we create must ask Discord for the longest visibility window.

A thread that Discord auto-archives drops out of the channel's thread list. The
window is fixed at creation time, so a call site that forgets the keyword silently
inherits discord.py's default and the conversation disappears from the sidebar
while the user still considers it open. This is an architecture test: it fails
when a *new* ``create_thread`` call site forgets, which is the way this
regresses.

The second half is the reverse rule: a thread that has been put away (archived,
locked, or its session closed) is left alone by every post nobody asked for,
because Discord un-archives a thread the moment anything lands in it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.session_repo import LifecycleState, SessionRecord
from claude_discord.thread_policy import THREAD_AUTO_ARCHIVE_MINUTES, may_post_unsolicited

_REPO = Path(__file__).parent.parent
PACKAGE_DIR = _REPO / "claude_discord"
# EbiBot's Cogs create threads too, and a thread that vanishes is just as wrong
# there — the rule is about Discord's behaviour, not about which package we are in.
_SCANNED_ROOTS = (PACKAGE_DIR, _REPO / "examples" / "ebibot" / "cogs")

# The only values Discord accepts, in minutes.
_ALLOWED_WINDOWS = (60, 1440, 4320, 10080)


def _create_thread_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_thread"
    ]


class TestThreadAutoArchiveWindow:
    def test_constant_is_discords_maximum(self) -> None:
        assert THREAD_AUTO_ARCHIVE_MINUTES in _ALLOWED_WINDOWS
        assert max(_ALLOWED_WINDOWS) == THREAD_AUTO_ARCHIVE_MINUTES

    def test_every_call_site_passes_the_constant(self) -> None:
        violations = []
        for path in sorted(p for root in _SCANNED_ROOTS for p in root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for call in _create_thread_calls(tree):
                kwarg = next((k for k in call.keywords if k.arg == "auto_archive_duration"), None)
                rel = path.relative_to(_REPO)
                if kwarg is None:
                    violations.append(f"  {rel}:{call.lineno}: no auto_archive_duration")
                elif not (
                    isinstance(kwarg.value, ast.Name)
                    and kwarg.value.id == "THREAD_AUTO_ARCHIVE_MINUTES"
                ):
                    violations.append(
                        f"  {rel}:{call.lineno}: auto_archive_duration is not the shared constant"
                    )

        assert not violations, (
            "create_thread() call sites must pass "
            "auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES "
            "(from claude_discord.thread_policy), or the thread vanishes from the "
            "channel's thread list while the user still considers it open:\n"
            + "\n".join(violations)
        )

    def test_the_scan_actually_finds_call_sites(self) -> None:
        """Guard against the scan silently matching nothing and passing forever."""
        found = sum(
            len(_create_thread_calls(ast.parse(p.read_text(encoding="utf-8"))))
            for root in _SCANNED_ROOTS
            for p in root.rglob("*.py")
        )
        assert found >= 8, f"expected the package to still create threads, found {found}"


def _thread(thread_id: int = 1, *, archived: bool = False, locked: bool = False) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.archived = archived
    thread.locked = locked
    return thread


def _row(state: LifecycleState) -> SessionRecord:
    return SessionRecord(
        thread_id=1,
        session_id="native-id",
        working_dir=None,
        model=None,
        origin="discord",
        summary=None,
        created_at="2026-09-29T08:00:00",
        last_used_at="2026-09-29T08:00:00",
        lifecycle_state=state.value,
    )


def _repo(record: SessionRecord | None) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=record)
    return repo


class TestMayPostUnsolicited:
    """An unprompted post (a reminder, a restart line, a report) must never reopen a thread."""

    async def test_an_open_live_thread_may_be_posted_to(self) -> None:
        assert await may_post_unsolicited(_thread(), _repo(_row(LifecycleState.OPEN))) is True

    async def test_a_thread_with_no_session_row_may_be_posted_to(self) -> None:
        assert await may_post_unsolicited(_thread(), _repo(None)) is True

    async def test_without_a_store_only_discord_is_asked(self) -> None:
        assert await may_post_unsolicited(_thread()) is True
        assert await may_post_unsolicited(_thread(archived=True)) is False

    async def test_an_archived_thread_is_left_alone(self) -> None:
        repo = _repo(_row(LifecycleState.OPEN))
        assert await may_post_unsolicited(_thread(archived=True), repo) is False
        repo.get.assert_not_awaited()  # Discord's own word settles it; no store read

    async def test_a_locked_thread_is_left_alone(self) -> None:
        assert await may_post_unsolicited(_thread(locked=True), _repo(None)) is False

    async def test_a_missing_thread_is_left_alone(self) -> None:
        assert await may_post_unsolicited(None, _repo(None)) is False

    @pytest.mark.parametrize("state", [LifecycleState.CLOSED, LifecycleState.CLOSING])
    async def test_a_thread_whose_session_is_put_away_is_left_alone(
        self, state: LifecycleState
    ) -> None:
        """The close is on its way to archiving the thread; a post now would race it."""
        assert await may_post_unsolicited(_thread(), _repo(_row(state))) is False

    async def test_a_channel_that_cannot_be_archived_is_always_fair_game(self) -> None:
        """A build's starting place is often a plain channel; it has no archive to undo."""
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 7
        repo = _repo(_row(LifecycleState.CLOSED))
        assert await may_post_unsolicited(channel, repo) is True
        repo.get.assert_not_awaited()

    async def test_a_store_that_cannot_answer_does_not_silence_the_post(self) -> None:
        """Discord's flags were checked first and are the stronger signal."""
        repo = MagicMock()
        repo.get = AsyncMock(side_effect=OSError("database is locked"))
        assert await may_post_unsolicited(_thread(), repo) is True
