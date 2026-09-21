"""A busy moment in the sessions database must wait, not kill the reply."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import aiosqlite

from claude_code_core import session_repo as sr


async def test_every_connection_waits_long_enough(tmp_path: Path) -> None:
    from claude_code_core.models import init_db

    db = tmp_path / "s.db"
    await init_db(str(db))
    seen: list[float] = []
    real = aiosqlite.connect

    def spy(path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(kwargs.get("timeout", 5.0))
        return real(path, *args, **kwargs)

    with patch.object(sr.aiosqlite, "connect", side_effect=spy):
        repo = sr.SessionRepository(str(db))
        await repo.save(1, "abc-123")
        await repo.get(1)
    assert seen and min(seen) >= 30
