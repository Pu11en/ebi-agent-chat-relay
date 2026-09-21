"""Exclusive advisory file lock that works on POSIX and Windows."""

from __future__ import annotations

import sys
from typing import IO

if sys.platform == "win32":
    import msvcrt

    def lock(stream: IO, *, blocking: bool = True) -> None:
        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), mode, 1)
        except OSError as exc:
            # msvcrt raises a bare OSError (EACCES/EDEADLK); callers expect BlockingIOError.
            raise BlockingIOError(str(exc)) from exc

    def unlock(stream: IO) -> None:
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock(stream: IO, *, blocking: bool = True) -> None:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(stream, flags)

    def unlock(stream: IO) -> None:
        fcntl.flock(stream, fcntl.LOCK_UN)
