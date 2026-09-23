"""The feature-workflow file lock must work on every OS the bot runs on.

The coordinator and run-state ledger were written against ``fcntl.flock``, which
does not exist on Windows; David's machine runs the bot there. These tests pin
the two behaviours both call sites rely on: an exclusive lock refuses a second
non-blocking taker, and releasing it lets the next taker in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from extensions.feature_workflow import _filelock


def test_nonblocking_second_lock_raises_blocking_io_error(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with path.open("a") as first, path.open("a") as second:
        _filelock.lock(first, blocking=False)
        try:
            with pytest.raises(BlockingIOError):
                _filelock.lock(second, blocking=False)
        finally:
            _filelock.unlock(first)


def test_unlock_lets_the_next_taker_in(tmp_path: Path) -> None:
    path = tmp_path / "lock"
    with path.open("a") as first, path.open("a") as second:
        _filelock.lock(first, blocking=False)
        _filelock.unlock(first)
        _filelock.lock(second, blocking=False)
        _filelock.unlock(second)
