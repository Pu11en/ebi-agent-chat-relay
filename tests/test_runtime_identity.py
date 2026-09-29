"""Health must report what is running, not what is checked out now (task 5.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from claude_discord.ext.api_server import ApiServer
from claude_discord.runtime_identity import capture_identity, disk_revision


def fake_git(state: dict[str, str]):
    def run(argv: list[str]) -> str:
        if "rev-parse" in argv:
            if state["head"] == "fail":
                raise subprocess.CalledProcessError(128, argv)
            return state["head"] + "\n"
        return state.get("status", "")

    return run


async def test_checkout_change_after_start_does_not_change_running_identity() -> None:
    state = {"head": "a" * 40}
    git = fake_git(state)
    boot = capture_identity(Path("/src"), git=git)
    repo = MagicMock()
    repo.get_pending = AsyncMock(return_value=[])
    server = ApiServer(repo=repo, bot=MagicMock())
    server.runtime_identity = boot
    server.disk_revision = lambda: disk_revision(Path("/src"), git=git)
    state["head"] = "b" * 40  # someone checks out another revision later

    async with TestClient(TestServer(server.app)) as client:
        body = await (await client.get("/api/health")).json()

    assert body["runtime"]["commit"] == "a" * 40
    assert body["runtime"]["dirty"] is False
    assert body["runtime"]["started_at"] == boot.started_at
    assert body["disk_commit"] == "b" * 40
    assert body["running_matches_disk"] is False


async def test_unavailable_git_is_labeled_unknown() -> None:
    git = fake_git({"head": "fail"})
    boot = capture_identity(Path("/installed"), git=git)
    assert (boot.commit, boot.dirty) == ("unknown", None)
    assert await disk_revision(Path("/installed"), git=git) == "unknown"


def test_dirty_tree_is_reported() -> None:
    boot = capture_identity(Path("/src"), git=fake_git({"head": "c" * 40, "status": " M x.py\n"}))
    assert boot.dirty is True
