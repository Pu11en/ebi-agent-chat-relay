"""T11b — the Discord cog builds a manifest plan through side copies and the ledger."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.loop_store import LoopStore
from claude_discord.cogs.task_loop import TaskLoopCog

FIXTURES = Path(__file__).parent / "fixtures"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    for project in ("control", "product", "website", "marketing"):
        (tmp_path / project).mkdir()
        (tmp_path / project / "README.md").write_text(f"# {project}\n")
    (tmp_path / "PLAN.md").write_text((FIXTURES / "validated-plan.md").read_text(encoding="utf-8"))
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _cog() -> tuple[TaskLoopCog, MagicMock, list[MagicMock], list[tuple[str, float]]]:
    bot = MagicMock()
    chat = MagicMock()
    threads: list[MagicMock] = []
    worked: list[tuple[str, float]] = []

    async def spawn(*_a, **_k) -> MagicMock:
        thread = MagicMock(spec=discord.Thread)
        thread.id = 2000 + len(threads)
        thread.mention = f"<#{thread.id}>"
        thread.send = AsyncMock(return_value=MagicMock())
        thread.delete = AsyncMock()
        threads.append(thread)
        return thread

    chat.spawn_session = AsyncMock(side_effect=spawn)

    async def fresh_turn(seed, thread, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
        if "checking finished work" in prompt or "agree the goal" in prompt:
            await result_sink("PASS: fine\nDONE", None)
            return
        cwd = Path(working_dir)
        loop = asyncio.get_running_loop()
        worked.append((cwd.name, loop.time()))
        await asyncio.sleep(0.6)  # long enough for siblings to overlap despite git setup
        (cwd / f"work-{cwd.parent.name[-24:]}.txt").write_text("done\n")  # one file per task
        _git(cwd, "add", ".")
        _git(cwd, "commit", "-qm", f"work in {cwd.name}")
        await result_sink(f"Built {cwd.name}.\nDONE", None)

    chat.run_fresh_turn = AsyncMock(side_effect=fresh_turn)
    chat._backend_settings = None
    bot.cogs = {"ClaudeChatCog": chat}
    tmp = Path(tempfile.mkdtemp(prefix="gowork-mf-"))
    cog = TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))
    return cog, chat, threads, worked


async def test_manifest_build_runs_projects_together_and_accepts_every_task(repo: Path) -> None:
    cog, chat, threads, worked = _cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()

    worker = await cog.start_loop(channel, str(repo / "PLAN.md"))
    for _ in range(1000):
        if cog.running and cog.running[0].in_review:
            break
        await asyncio.sleep(0.01)
    running = cog.running[0]
    assert running.in_review, "the build should be waiting for the verdict"

    ledger = json.loads(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}.json").read_text()
    )
    assert {t["status"] for t in ledger["tasks"].values()} == {"accepted"}
    assert all(t["result_commit"] for t in ledger["tasks"].values())

    # Every task's work landed in the build's copy, inside its own project folder.
    copy = running.copy
    assert copy is not None
    assert len(list((copy.path / "product").glob("work-*.txt"))) == 1
    assert len(list((copy.path / "website").glob("work-*.txt"))) == 2
    assert len(list((copy.path / "marketing").glob("work-*.txt"))) == 1
    assert not list((repo / "product").glob("work-*.txt"))  # the project itself is untouched

    # product and marketing ran at the same time; the website page waited for product.
    starts = dict(worked[:3])
    assert set(starts) == {"product", "website", "marketing"}
    assert max(starts.values()) - min(starts.values()) < 0.6  # all three were running at once
    assert worked[3][0] == "website" and worked[3][1] > starts["product"] + 0.6
    assert len(threads) == 1 + 4  # the build thread plus one worker thread per task
    assert chat.run_fresh_turn.await_count >= 4

    # T12: every attempt's handoff was written before its worker started, and the
    # worker's prompt was rendered from it (attempt id, ownership, evidence).
    handoffs = sorted(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}" / "handoffs").glob("*.json")
    )
    assert [h.name for h in handoffs] == sorted(
        f"thread-{worker.id}_{task}_1.json" for task in ledger["tasks"]
    )
    prompts = [c.args[2] for c in chat.run_fresh_turn.call_args_list if "Your task (" in c.args[2]]
    page = next(p for p in prompts if "website.catalog-page" in p)
    assert "Attempt: thread-" in page and "src/pages/catalog.tsx" in page
    assert "product.catalog-api: accepted at " in page  # evidence behind its input


async def test_worker_threads_are_archived_after_the_save_never_deleted_and_retried(
    repo: Path,
) -> None:
    """T14: archive follows the durable save; a failed archive is retried, nothing is deleted."""
    cog, chat, threads, _worked = _cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    archived_after: dict[int, str] = {}

    def make_edit(thread: MagicMock):  # noqa: ANN202
        async def edit(**kwargs: object) -> None:
            if kwargs.get("archived"):
                ledger = json.loads(
                    (
                        cog._store.path.with_name("builds") / f"thread-{threads[0].id}.json"
                    ).read_text()
                )
                task = next(t for t in ledger["tasks"].values() if t.get("thread_id") == thread.id)
                if thread.id == threads[2].id and thread.id not in archived_after:
                    archived_after[thread.id] = "failed once"
                    raise discord.HTTPException(MagicMock(status=500), "boom")
                archived_after[thread.id] = task["status"]  # what was on disk at archive time

        return edit

    original_spawn = chat.spawn_session.side_effect

    async def spawn(*a, **k):  # noqa: ANN001, ANN002, ANN003
        thread = await original_spawn(*a, **k)
        thread.edit = AsyncMock(side_effect=make_edit(thread))
        return thread

    chat.spawn_session = AsyncMock(side_effect=spawn)
    cog.bot.get_channel = MagicMock(
        side_effect=lambda cid: next((t for t in threads if t.id == cid), None)
    )

    worker = await cog.start_loop(channel, str(repo / "PLAN.md"))
    for _ in range(1000):
        if cog.running and cog.running[0].in_review:
            break
        await asyncio.sleep(0.01)
    running = cog.running[0]

    assert set(archived_after.values()) == {"accepted", "failed once"}  # saved before archived
    # The build's own thread is only ever archived by the finish flow (with its reason),
    # never by the per-task archive; the planning channel is never touched.
    assert all(
        c.kwargs.get("reason") == "go-work finished"
        for c in worker.edit.call_args_list
        if c.kwargs.get("archived")
    )
    assert not hasattr(channel, "edit") or not channel.edit.called
    for thread in threads:
        thread.delete.assert_not_called()

    ledger = json.loads(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}.json").read_text()
    )
    pending = [t for t in ledger["tasks"].values() if not t["archived"]]
    assert len(pending) == 1 and pending[0]["thread_id"] == threads[2].id

    assert await cog.retry_archives(running) == 1  # what a restart would do
    ledger = json.loads(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}.json").read_text()
    )
    assert all(t["archived"] for t in ledger["tasks"].values())
    assert chat.run_fresh_turn.await_count == 4  # four workers, none rerun by the retry
