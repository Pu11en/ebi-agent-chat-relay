"""T11a — a running build is known by its build id, not by its project.

Two manifest plans may run in one project at the same time; starting a plan
that is already running names that build instead of opening a second one;
stopping one build never touches another. Checkbox plans keep the old
one-build-per-project rule (and its switch-on-new-start behaviour).
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.loop_store import LoopStore
from claude_discord.cogs.task_loop import BuildAlreadyRunningError, TaskLoopCog

FIXTURES = Path(__file__).parent / "fixtures"
_PY = sys.executable.replace("\\", "/")  # forward slashes survive shlex on Windows


def _passing_manifest() -> str:
    """The validated plan with acceptance checks that pass on any machine."""
    text = (FIXTURES / "validated-plan.md").read_text(encoding="utf-8")
    return re.sub(r'"acceptance_check": "[^"]*"', f'"acceptance_check": "{_PY} -c pass"', text)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    for project in ("control", "product", "website", "marketing"):
        (tmp_path / project).mkdir()
        (tmp_path / project / "README.md").write_text(f"# {project}\n")
    manifest = _passing_manifest()
    (tmp_path / "PLAN-A.md").write_text(manifest.replace("Launch the business", "Plan A"))
    (tmp_path / "PLAN-B.md").write_text(manifest.replace("Launch the business", "Plan B"))
    (tmp_path / "LEGACY.md").write_text("- [ ] Task 1: a\n- [ ] Task 2: b\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _cog() -> tuple[TaskLoopCog, MagicMock, list[MagicMock]]:
    bot = MagicMock()
    chat = MagicMock()
    threads: list[MagicMock] = []

    async def spawn(*_a, **_k) -> MagicMock:
        thread = MagicMock(spec=discord.Thread)
        thread.id = 1000 + len(threads)
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
        if "Your task (" in prompt:  # a manifest task: leave one committed file behind
            cwd = Path(working_dir)
            (cwd / f"work-{cwd.parent.name[-24:]}.txt").write_text("done\n")
            subprocess.run(["git", "-C", working_dir, "add", "."], capture_output=True)
            subprocess.run(["git", "-C", working_dir, "commit", "-qm", "work"], capture_output=True)
            await result_sink("Built it.\nDONE", None)
            return
        # Tick the first open box of whichever plan the prompt names, like a worker would.
        for plan in sorted(Path(working_dir).glob("*.md")):
            if plan.name in prompt and "- [ ]" in plan.read_text():
                plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
                subprocess.run(
                    ["git", "-C", working_dir, "commit", "-qam", "tick"], capture_output=True
                )
                break
        await result_sink("recap\nDONE", None)

    chat.run_fresh_turn = AsyncMock(side_effect=fresh_turn)
    chat._backend_settings = None
    bot.cogs = {"ClaudeChatCog": chat}
    tmp = Path(tempfile.mkdtemp(prefix="gowork-id-"))
    cog = TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))
    cog._quick_ai = AsyncMock(return_value=None)  # type: ignore[method-assign]  # no real AI
    cog._interview_ai = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return cog, chat, threads


def _channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    return channel


async def _settle(cog: TaskLoopCog, count: int) -> None:
    for _ in range(3000):  # two real builds with git side copies can take a while
        if len(cog.running) == count and all(r.in_review or r.task is None for r in cog.running):
            return
        await asyncio.sleep(0.01)


async def test_two_manifest_plans_run_in_one_project_at_once(repo: Path) -> None:
    cog, _chat, threads = _cog()
    a = await cog.start_loop(_channel(), str(repo / "PLAN-A.md"))
    b = await cog.start_loop(_channel(), str(repo / "PLAN-B.md"))

    assert a is not b and a in threads and b in threads
    assert {r.build_id for r in cog.running} == {f"thread-{a.id}", f"thread-{b.id}"}
    assert {r.repo_dir for r in cog.running} == {repo.resolve()}
    assert sorted(rec.plan_path for rec in cog._store.for_repo(str(repo.resolve()))) == sorted(
        [str(repo / "PLAN-A.md"), str(repo / "PLAN-B.md")]
    )


async def test_starting_a_running_plan_names_that_build_and_opens_nothing(repo: Path) -> None:
    cog, chat, threads = _cog()
    first = await cog.start_loop(_channel(), str(repo / "PLAN-A.md"))
    with pytest.raises(BuildAlreadyRunningError) as info:
        await cog.start_loop(_channel(), str(repo / "PLAN-A.md"))

    assert info.value.thread is first
    assert info.value.build_id == f"thread-{first.id}"
    assert len(cog.running) == 1 and len(cog._store.all()) == 1  # no second build


async def test_stopping_one_build_leaves_the_other_alone(repo: Path) -> None:
    # Both plans' checks fail, so both builds park on their questions instead of ending.
    stuck = re.sub(
        r'"acceptance_check": "[^"]*"',
        f'"acceptance_check": "{_PY} -c exit(1)"',
        _passing_manifest(),
    )
    (repo / "PLAN-A.md").write_text(stuck.replace("Launch the business", "Plan A"))
    (repo / "PLAN-B.md").write_text(stuck.replace("Launch the business", "Plan B"))
    _git(repo, "commit", "-qam", "stuck plans")
    cog, _chat, _threads = _cog()
    a = await cog.start_loop(_channel(), str(repo / "PLAN-A.md"))
    b = await cog.start_loop(_channel(), str(repo / "PLAN-B.md"))
    for _ in range(3000):
        if len(cog.running) == 2 and all(r.parked for r in cog.running):
            break
        await asyncio.sleep(0.01)
    assert {r.build_id for r in cog.running} == {f"thread-{a.id}", f"thread-{b.id}"}

    message = MagicMock()
    message.channel.id = a.id
    message.content = "close"
    assert cog.take_message(message)
    for _ in range(3000):
        if {r.build_id for r in cog.running} == {f"thread-{b.id}"}:
            break
        await asyncio.sleep(0.01)
    assert {r.build_id for r in cog.running} == {f"thread-{b.id}"}
    assert [rec.build_id for rec in cog._store.all()] == [f"thread-{b.id}"]


async def test_checkbox_plans_keep_one_build_per_project(repo: Path) -> None:
    cog, _chat, threads = _cog()
    await cog.start_loop(_channel(), str(repo / "LEGACY.md"))
    with pytest.raises(BuildAlreadyRunningError):
        await cog.start_loop(_channel(), str(repo / "LEGACY.md"))
    (repo / "LEGACY-2.md").write_text("- [ ] Task 1: c\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "second legacy plan")
    with pytest.raises(ValueError, match="already running"):
        await cog.start_loop(_channel(), str(repo / "LEGACY-2.md"))
    assert len(threads) == 1


async def test_a_manifest_plan_may_start_beside_a_checkbox_build(repo: Path) -> None:
    cog, _chat, threads = _cog()
    await cog.start_loop(_channel(), str(repo / "LEGACY.md"))
    await cog.start_loop(_channel(), str(repo / "PLAN-A.md"))
    assert len(cog.running) == 2
