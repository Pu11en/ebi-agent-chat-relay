"""T11b — the Discord cog builds a manifest plan through side copies and the ledger."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.loop_store import LoopStore
from claude_discord.cogs.task_loop import TaskLoopCog

FIXTURES = Path(__file__).parent / "fixtures"
_PY = sys.executable.replace("\\", "/")  # forward slashes survive shlex on Windows


def _passing_manifest() -> str:
    """The validated plan with acceptance checks that pass on any machine."""
    text = (FIXTURES / "validated-plan.md").read_text(encoding="utf-8")
    return re.sub(r'"acceptance_check": "[^"]*"', f'"acceptance_check": "{_PY} -c pass"', text)


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
    (tmp_path / "PLAN.md").write_text(_passing_manifest())
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


async def test_combined_check_failure_blocks_the_task_and_its_dependents(repo: Path) -> None:
    """T15: the merge lands, the combined check fails, the commit is kept, dependents wait."""
    failing = re.sub(
        r'"acceptance_check": "[^"]*"',
        f'"acceptance_check": "{_PY} -c exit(1)"',
        _passing_manifest(),
        count=1,  # only product.catalog-api's check fails
    )
    (repo / "PLAN.md").write_text(failing)
    _git(repo, "commit", "-qam", "failing check")
    cog, _chat, threads, _worked = _cog()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()

    worker = await cog.start_loop(channel, str(repo / "PLAN.md"))
    for _ in range(1000):
        if cog.running and (cog.running[0].in_review or cog.running[0].waiting_for_person):
            break
        await asyncio.sleep(0.01)

    ledger = json.loads(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}.json").read_text()
    )
    api = ledger["tasks"]["product.catalog-api"]
    assert api["status"] == "blocked" and "combined check failed" in api["reason"]
    assert api["result_commit"]  # kept for repair
    assert ledger["tasks"]["website.catalog-page"]["status"] == "pending"
    assert {
        ledger["tasks"][t]["status"] for t in ("website.page-styles", "marketing.launch-post")
    } == {"accepted"}


async def test_a_clash_keeps_the_workers_branch(repo: Path) -> None:
    """T15: a worker that edits outside its files and clashes loses nothing — both versions stay."""
    cog, chat, threads, _worked = _cog()
    original = chat.run_fresh_turn.side_effect

    async def clashing(seed, thread, prompt, *, working_dir, result_sink, **slot):  # noqa: ANN001
        if "Your task (" in prompt:
            cwd = Path(working_dir)
            (cwd.parent / "control" / "README.md").write_text(f"edited by {cwd.name}\n")
            _git(cwd.parent, "commit", "-qam", f"{cwd.name} edits control")
            await asyncio.sleep(0.2)
            await result_sink("Edited the shared file.\nDONE", None)
            return
        await original(
            seed, thread, prompt, working_dir=working_dir, result_sink=result_sink, **slot
        )

    chat.run_fresh_turn = AsyncMock(side_effect=clashing)
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    await cog.start_loop(channel, str(repo / "PLAN.md"))
    for _ in range(1000):
        if cog.running and (cog.running[0].in_review or cog.running[0].waiting_for_person):
            break
        await asyncio.sleep(0.01)
    running = cog.running[0]
    assert running.copy is not None

    ledger = json.loads(
        (
            cog._store.path.with_name("builds") / f"thread-{running.worker_thread_id}.json"
        ).read_text()
    )
    blocked = [t for t in ledger["tasks"].values() if t["status"] == "blocked"]
    assert blocked, "the later editors of the same file must clash"
    for task in blocked:
        assert "both versions kept" in task["reason"]
        branch = task["reason"].split("branch ")[-1].rstrip(")")
        assert branch in _git(running.copy.path, "branch", "--list", branch)  # still there
    assert _git(running.copy.path, "status", "--porcelain").strip() == ""  # the copy is clean


def _plan_with_mode(repo: Path, text: str | None = None) -> None:
    (repo / "PLAN.md").write_text(text or _passing_manifest())
    _git(repo, "commit", "-qam", "plan")


async def _run_until_settled(cog: TaskLoopCog, channel: MagicMock, plan: Path):  # noqa: ANN202
    worker = await cog.start_loop(channel, str(plan), mode=getattr(cog, "_test_mode", None))
    for _ in range(1500):
        if cog.running and (cog.running[0].in_review or cog.running[0].waiting_for_person):
            break
        await asyncio.sleep(0.01)
    return worker, json.loads(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}.json").read_text()
    )


def _channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    return channel


async def test_balanced_ordinary_tasks_need_no_review(repo: Path) -> None:
    """T16: an ordinary balanced task is accepted on its checks alone."""
    cog, chat, _threads, _worked = _cog()
    cog._is_hard = AsyncMock(return_value=False)  # type: ignore[method-assign]
    _worker, ledger = await _run_until_settled(cog, _channel(), repo / "PLAN.md")
    assert {t["status"] for t in ledger["tasks"].values()} == {"accepted"}
    assert not any("reviewing" in str(c.args[2]) for c in chat.run_fresh_turn.call_args_list)


async def test_a_hard_task_cannot_be_accepted_without_a_reviewer(repo: Path) -> None:
    """T16: required review with no reviewer AI is 'blocked', never approval."""
    cog, _chat, _threads, _worked = _cog()
    cog._is_hard = AsyncMock(side_effect=lambda _r, step: "catalog contract" in step)  # type: ignore[method-assign]
    _worker, ledger = await _run_until_settled(cog, _channel(), repo / "PLAN.md")
    api = ledger["tasks"]["product.catalog-api"]
    assert api["status"] == "blocked" and "review" in api["reason"] and api["result_commit"]
    assert ledger["tasks"]["marketing.launch-post"]["status"] == "accepted"


async def test_a_reviewer_verdict_decides_and_a_broken_review_blocks(repo: Path) -> None:
    """T16: CHANGES blocks, APPROVE accepts, no verdict blocks."""
    cog, chat, _threads, _worked = _cog()
    cog._test_mode = "careful"  # type: ignore[attr-defined]
    cog._reviewer_for = AsyncMock(return_value=("claude", "opus"))  # type: ignore[method-assign]
    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value="claude")
    settings.current_model = AsyncMock(return_value="sonnet")
    settings.set_backend = AsyncMock()
    settings.set_model = AsyncMock()
    chat._backend_settings = settings
    original = chat.run_fresh_turn.side_effect
    verdicts = {
        "catalog contract": "CHANGES: the schema lacks a version field",
        "Write the launch announcement": "",
        "catalog on the website": "APPROVE",
        "launch styling": "APPROVE",
    }

    async def turn(seed, thread, prompt, *, working_dir, result_sink, **slot):  # noqa: ANN001
        if "review" in prompt.lower() and "APPROVE" in prompt:
            step = next((ln for ln in prompt.splitlines() if ln.startswith("The step: ")), "")
            for key, verdict in verdicts.items():
                if key in step:
                    await result_sink(verdict or None, None if verdict else "reviewer crashed")
                    return
        await original(
            seed, thread, prompt, working_dir=working_dir, result_sink=result_sink, **slot
        )

    chat.run_fresh_turn = AsyncMock(side_effect=turn)
    _worker, ledger = await _run_until_settled(cog, _channel(), repo / "PLAN.md")
    tasks = ledger["tasks"]
    assert tasks["product.catalog-api"]["status"] == "blocked"
    assert "schema lacks a version field" in tasks["product.catalog-api"]["reason"]
    assert tasks["marketing.launch-post"]["status"] == "blocked"
    assert "review" in tasks["marketing.launch-post"]["reason"]
    assert tasks["website.page-styles"]["status"] == "accepted"
    assert any("review" in c for c in tasks["website.page-styles"]["checks"])


async def test_a_task_without_a_runnable_check_is_not_accepted(repo: Path) -> None:
    """T16: every task needs real check evidence."""
    _plan_with_mode(
        repo,
        re.sub(
            r'"acceptance_check": "[^"]*"',
            '"acceptance_check": "none"',
            _passing_manifest(),
            count=1,
        ),
    )
    cog, _chat, _threads, _worked = _cog()
    cog._is_hard = AsyncMock(return_value=False)  # type: ignore[method-assign]
    _worker, ledger = await _run_until_settled(cog, _channel(), repo / "PLAN.md")
    api = ledger["tasks"]["product.catalog-api"]
    assert api["status"] == "blocked" and "no runnable check" in api["reason"]


async def test_a_restart_keeps_finished_work_and_never_reruns_or_reassigns(repo: Path) -> None:
    """T17: restart after merge, after a worker's commit, and mid-work: nothing lost, nothing
    rerun, the uncertain one waits for a person."""
    from claude_code_core.gowork_plan import load_plan_tree
    from claude_code_core.gowork_state import open_build_state
    from claude_code_core.loop_store import LoopRecord
    from claude_code_core.work_copy import create_side_copy, create_work_copy, side_copy_for

    cog, chat, threads, _worked = _cog()
    cog._is_hard = AsyncMock(return_value=False)  # type: ignore[method-assign]
    copy = await create_work_copy(repo, repo / "PLAN.md", root=cog._work_root)
    build_id = "thread-9000"
    state = open_build_state(
        cog._store.path.with_name("builds") / f"{build_id}.json",
        load_plan_tree(copy.plan_path),
        build_id=build_id,
    )
    base = _git(copy.path, "rev-parse", "HEAD").strip()
    # 1. product: merged into the copy, then the bot died before the ledger was written
    state.begin("product.catalog-api")
    state.note_thread(
        "product.catalog-api",
        state["product.catalog-api"].attempt_id,
        thread_id=1,
        base_commit=base,
    )
    side = await create_side_copy(copy, "product.catalog-api")
    (side.path / "product" / "work-merged.txt").write_text("done\n")
    _git(side.path, "add", ".")
    _git(side.path, "commit", "-qm", "product work")
    _git(copy.path, "merge", "--no-edit", side.branch)
    # 2. marketing: the worker committed, the merge never happened
    state.begin("marketing.launch-post")
    side2 = await create_side_copy(copy, "marketing.launch-post")
    state.note_thread(
        "marketing.launch-post",
        state["marketing.launch-post"].attempt_id,
        thread_id=2,
        base_commit=_git(side2.path, "rev-parse", "HEAD").strip(),
    )
    (side2.path / "marketing" / "work-committed.txt").write_text("done\n")
    _git(side2.path, "add", ".")
    _git(side2.path, "commit", "-qm", "marketing work")
    # 3. styles: the worker was mid-flight with nothing saved
    state.begin("website.page-styles")
    side3 = await create_side_copy(copy, "website.page-styles")
    state.note_thread(
        "website.page-styles",
        state["website.page-styles"].attempt_id,
        thread_id=3,
        base_commit=_git(side3.path, "rev-parse", "HEAD").strip(),
    )

    thread = MagicMock(spec=discord.Thread)
    thread.id = 9000
    thread.mention = "<#9000>"
    thread.send = AsyncMock(return_value=MagicMock())
    thread.delete = AsyncMock()
    report = MagicMock()
    report.id = 1
    report.send = AsyncMock()
    cog.bot.get_channel = MagicMock(side_effect=lambda cid: thread if cid == 9000 else report)
    cog._store.save(
        LoopRecord(
            repo_dir=str(repo.resolve()),
            plan_path=str(repo / "PLAN.md"),
            copy_path=str(copy.path),
            copy_plan=str(copy.plan_path),
            branch=copy.branch,
            worker_thread_id=9000,
            report_channel_id=1,
        )
    )

    assert await cog.resume_all() == 1
    for _ in range(1500):
        running = cog.running[0] if cog.running else None
        if running and (running.in_review or running.waiting_for_person):
            break
        await asyncio.sleep(0.01)

    ledger = json.loads((cog._store.path.with_name("builds") / f"{build_id}.json").read_text())
    tasks = ledger["tasks"]
    assert tasks["product.catalog-api"]["status"] == "accepted"  # salvaged, not rerun
    assert tasks["marketing.launch-post"]["status"] == "accepted"  # merged now, not rerun
    assert tasks["website.page-styles"]["status"] == "blocked"
    assert "restart" in tasks["website.page-styles"]["reason"]
    assert tasks["website.catalog-page"]["status"] == "accepted"  # the rest carried on
    assert (copy.path / "marketing" / "work-committed.txt").exists()
    assert not (side_copy_for(copy, "product.catalog-api").path).exists()
    worked_prompts = [
        c.args[2] for c in chat.run_fresh_turn.call_args_list if "Your task (" in c.args[2]
    ]
    assert (
        len(worked_prompts) == 1 and "website.catalog-page" in worked_prompts[0]
    )  # only the page ran
