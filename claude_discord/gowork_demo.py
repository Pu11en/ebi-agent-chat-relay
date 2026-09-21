"""The offline Go Work practice run (T31): ``python -m claude_discord.gowork_demo``.

Everything real is real: :class:`TaskLoopCog`, the core loop, the task
ledger, side copies in a temporary git repository, the admission controller
and the capacity policy. Everything that would touch the outside is a fake:
workers (they write one file, commit it and wait at a gate the script opens),
the host probe (memory readings the script sets), and Discord (threads and
messages are recorded objects). No model is called, no bot process runs, and
nothing outside the temporary folder is read or written.

The script walks two plans in one repository through the behaviours the
upgrade promised, in a fixed order, and checks each one as it happens. It
exits 0 only when every check passed, else 1 with the first failure named.
The output is one line per behaviour: what was seen and why it matters.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from claude_code_core.gowork_admission import AdmissionController
from claude_code_core.gowork_capacity import CapacityPolicy
from claude_code_core.gowork_export import (
    PlanExport,
    PlanHeader,
    RuntimeSupport,
    export_plan,
    plan_tree_from_manifest,
    write_plan,
)
from claude_code_core.gowork_resources import ResourceSnapshot
from claude_code_core.loop_store import LoopStore
from claude_code_core.types import ImageData, StreamEvent
from claude_code_core.work_copy import commit_all
from claude_discord.cogs import _run_helper as helper
from claude_discord.cogs.run_config import RunConfig
from claude_discord.cogs.task_loop import TaskLoopCog

COMMAND = "uv run python -m claude_discord.gowork_demo"

#: Every behaviour the run must observe, in the order it looks for them.
BEHAVIOURS: tuple[str, ...] = (
    "two plans in several projects",
    "dependency wait",
    "one repair",
    "fair turns",
    "more than ten adaptive admissions",
    "pressure pauses starts",
    "mid-build plan change",
    "direct-reply blocker",
    "archive before a sibling finishes",
    "restart keeps finished work",
    "automatic checked completion",
)

LIMITATIONS: tuple[str, ...] = (
    "This is a practice run on this computer's Python and git, not the live bot: no bot "
    "process, no Discord, no model was involved, so it proves the coordinator and its "
    "adapters, not a deployment.",
    "Workers are fakes that commit one file each; what a real worker writes, and whether a "
    "model follows its handoff, is not measured here.",
    "The mid-build edit is applied to the build's own copy of the plan (what the running "
    "loop reads); the route from the planning thread's plan into a manifest build's copy "
    "carries checkbox changes only.",
    "The quick AI (step grouping and the hard-step picker) is stubbed to 'no answer': it is a "
    "live `claude -p` call, and the manifest path does not group by model anyway.",
    "Host readings are set by the script; a real host's memory, swap and load are read by "
    "HostProbe (see tests/gowork_upgrade/test_resources.py).",
)

_PY = sys.executable.replace("\\", "/")
_POLL = 0.02
_TIMEOUT = 90.0

A_TASKS = ("product.catalog-api", "website.catalog-page", "website.page-styles", "marketing.post")
B_TASKS = tuple(f"content.page-{n:02d}" for n in range(1, 10))

_GATE_CHECK = """\
import pathlib, sys
p = pathlib.Path(sys.argv[1]); fail_times = int(sys.argv[2])
n = int(p.read_text()) + 1 if p.exists() else 1
p.parent.mkdir(parents=True, exist_ok=True); p.write_text(str(n))
sys.exit(1 if n <= fail_times else 0)
"""


class DemoError(AssertionError):
    """A behaviour the upgrade promised was not observed."""


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeProbe:
    """Host readings the script sets: healthy by default, critical on request."""

    def __init__(self) -> None:
        self.available_mb = 64000.0

    def sample(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            memory_total_mb=64000.0,
            memory_available_mb=self.available_mb,
            swap_used_mb=0.0,
            cpu_count=16,
            cpu_load=1.0,
            disk_free_mb=500000.0,
        )


class _FakeRunner:
    """A SessionBackend whose 'session' is one coroutine; it never spawns anything."""

    command = "fake-worker"
    model = "none"
    permission_mode = "default"
    api_port: int | None = None
    timeout_seconds = 60
    dangerously_skip_permissions = False
    allowed_tools: list[str] | None = None

    def __init__(self, work: Callable[[], Any]) -> None:
        self.working_dir: str | None = None
        self.append_system_prompt: str | None = None
        self.images: list[ImageData] | None = None
        self._work = work

    def clone(self, **_changes: object) -> _FakeRunner:
        return self

    async def run(
        self, prompt: str, session_id: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        await self._work()
        return
        yield  # pragma: no cover - makes this an async generator

    async def interrupt(self) -> None:
        pass

    async def kill(self) -> None:
        pass

    async def inject_tool_result(self, request_id: str, data: dict) -> None:
        pass

    def _build_env(self) -> dict[str, str]:
        return {}

    def describe_api(self) -> str:
        return "fake worker (no process)"


@dataclass
class FakeWorkers:
    """Fake ClaudeChatCog: worker turns go through the real admission path."""

    controller: AdmissionController
    threads: list[Any] = field(default_factory=list)
    started: list[tuple[str, int]] = field(default_factory=list)
    committed: set[tuple[str, int]] = field(default_factory=set)
    finished: set[tuple[str, int]] = field(default_factory=set)
    max_running: int = 0
    _gates: dict[tuple[str, int], asyncio.Event] = field(default_factory=dict)
    _backend_settings: Any = None

    def gate(self, task_id: str, attempt: int) -> asyncio.Event:
        return self._gates.setdefault((task_id, attempt), asyncio.Event())

    def open(self, task_id: str, attempt: int = 1) -> None:
        self.gate(task_id, attempt).set()

    def attempts_started(self, task_id: str) -> int:
        return sum(1 for t, _n in self.started if t == task_id)

    async def spawn_session(self, _parent: Any, *_a: Any, **_k: Any) -> Any:
        thread = MagicMock(spec=discord.Thread)
        thread.id = 2000 + len(self.threads)
        thread.mention = f"<#{thread.id}>"
        thread.parent = None
        thread.send = AsyncMock(return_value=MagicMock())
        thread.edit = AsyncMock()
        thread.delete = AsyncMock()
        self.threads.append(thread)
        return thread

    def thread(self, thread_id: int) -> Any | None:
        return next((t for t in self.threads if t.id == thread_id), None)

    def archived(self, thread_id: int) -> bool:
        thread = self.thread(thread_id)
        return thread is not None and any(
            c.kwargs.get("archived") for c in thread.edit.call_args_list
        )

    async def run_fresh_turn(  # noqa: PLR0913
        self,
        _seed: Any,
        thread: Any,
        prompt: str,
        *,
        working_dir: str | None,
        result_sink: Callable[[str | None, str | None], Any],
        slot_kind: str = "task",
        slot_build_id: str = "",
        slot_unblocks: int = 0,
    ) -> None:
        match = re.search(r"Attempt: (\S+)", prompt)
        if "Your task (" not in prompt or match is None or working_dir is None:
            await result_sink("PASS: fine\nDONE", None)  # the build's own checks
            return
        _build, task_id, number = match.group(1).rsplit(":", 2)
        attempt = int(number)
        cwd = Path(working_dir)

        async def work() -> None:
            self.started.append((task_id, attempt))
            self.max_running = max(self.max_running, self.controller.snapshot().held_tasks)
            (cwd / f"work-{task_id}-a{attempt}.txt").write_text(f"done at {time.time()}\n")
            await commit_all(cwd, f"{task_id} attempt {attempt}")
            self.committed.add((task_id, attempt))
            await self.gate(task_id, attempt).wait()
            self.finished.add((task_id, attempt))

        config = RunConfig(
            thread=thread,
            runner=_FakeRunner(work),
            prompt=prompt,
            slim_context=True,
            slot_kind=slot_kind,
            slot_build_id=slot_build_id,
            slot_unblocks=slot_unblocks,
        )
        await helper.run_claude_with_config(config)
        if (task_id, attempt) in self.finished:
            await result_sink(f"Built {task_id}.\nDONE", None)
        else:
            await result_sink(None, "the worker did not finish")


# --------------------------------------------------------------------------- #
# The repository and its two plans
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _check(root: Path, name: str, fail_times: int) -> str:
    return f"{_PY} {root.as_posix()}/gate_check.py {root.as_posix()}/counts/{name} {fail_times}"


def _launch_manifest(root: Path) -> dict:
    return {
        "schema_version": 1,
        "plans": [
            {"id": "launch", "version": 1, "project_path": "control"},
            {"id": "product", "version": 2, "parent_id": "launch", "project_path": "product"},
            {"id": "website", "version": 4, "parent_id": "launch", "project_path": "website"},
            {"id": "marketing", "version": 1, "parent_id": "launch", "project_path": "marketing"},
        ],
        "requirements": [
            {"id": "REQ-CATALOG", "outcome": "The product catalog contract is published"},
            {"id": "REQ-WEBSITE", "outcome": "The website shows the catalog"},
            {"id": "REQ-POST", "outcome": "A launch announcement is ready"},
        ],
        "tasks": [
            {
                "id": "product.catalog-api",
                "plan_id": "product",
                "plan_version": 2,
                "outcome": "Publish the checked product catalog contract",
                "dependencies": [],
                "owned_files": ["src/catalog.py"],
                "owned_resources": ["catalog-schema"],
                "required_inputs": ["REQ-CATALOG: the agreed fields"],
                "output": "A versioned catalog contract",
                "acceptance_check": _check(root, "api", 1),  # fails once: the one repair
                "source_requirement": "REQ-CATALOG",
            },
            {
                "id": "website.catalog-page",
                "plan_id": "website",
                "plan_version": 4,
                "outcome": "Show the checked product catalog on the website",
                "dependencies": ["product.catalog-api"],
                "owned_files": ["src/pages/catalog.tsx"],
                "owned_resources": [],
                "required_inputs": ["product.catalog-api: the versioned contract"],
                "output": "A catalog page backed by the accepted contract",
                "acceptance_check": f"{_PY} -c pass",
                "source_requirement": "REQ-WEBSITE",
            },
            {
                "id": "website.page-styles",
                "plan_id": "website",
                "plan_version": 4,
                "outcome": "Give every website page the agreed launch styling",
                "dependencies": [],
                "owned_files": ["src/styles/"],
                "owned_resources": [],
                "required_inputs": ["REQ-WEBSITE: the launch look"],
                "output": "Launch styling applied",
                "acceptance_check": f"{_PY} -c pass",
                "source_requirement": "REQ-WEBSITE",
            },
            {
                "id": "marketing.post",
                "plan_id": "marketing",
                "plan_version": 1,
                "outcome": "Write the launch announcement",
                "dependencies": [],
                "owned_files": ["posts/launch.md"],
                "owned_resources": [],
                "required_inputs": ["REQ-POST: the launch date"],
                "output": "A reviewed launch announcement",
                "acceptance_check": _check(root, "post", 2),  # fails twice: a person decides
                "source_requirement": "REQ-POST",
            },
        ],
    }


def _content_manifest() -> dict:
    return {
        "schema_version": 1,
        "plans": [{"id": "content", "version": 1, "project_path": "docs"}],
        "requirements": [{"id": "REQ-DOCS", "outcome": "Every help page is written"}],
        "tasks": [
            {
                "id": task_id,
                "plan_id": "content",
                "plan_version": 1,
                "outcome": f"Write help page {task_id[-2:]}",
                "dependencies": [],
                "owned_files": [f"pages/{task_id[-2:]}.md"],
                "owned_resources": [],
                "required_inputs": ["REQ-DOCS: the page outline"],
                "output": f"Help page {task_id[-2:]} written and checked",
                "acceptance_check": f"{_PY} -c pass",
                "source_requirement": "REQ-DOCS",
            }
            for task_id in B_TASKS
        ],
    }


def _write_plan(repo: Path, name: str, manifest: dict, title: str, goal: str) -> Path:
    tree = plan_tree_from_manifest(manifest, base_dir=repo)
    header = PlanHeader(title=title, goal=goal, check=f"{_PY} -c pass")
    export = export_plan(
        tree, header, runtime=RuntimeSupport(manifest_dispatch=True), relative_to=repo
    )
    # No checkbox lines: the manifest path dispatches from the ledger, and a checkbox
    # list would ask the (stubbed) quick AI for a grouping it never uses.
    text = export.text.split("## Tasks", 1)[0]
    return write_plan(repo / name, PlanExport(text, export.format, export.notes))


def _make_repo(root: Path) -> Path:
    repo = root / "shop"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "demo@example.invalid")
    _git(repo, "config", "user.name", "gowork demo")
    _git(repo, "config", "core.autocrlf", "false")
    for project in ("control", "product", "website", "marketing", "docs"):
        (repo / project).mkdir()
        (repo / project / "README.md").write_text(f"# {project}\n")
    (root / "gate_check.py").write_text(_GATE_CHECK)
    _write_plan(
        repo,
        "PLAN-launch.md",
        _launch_manifest(root),
        "Launch the shop",
        "Customers can browse the catalog and hear about the launch",
    )
    _write_plan(
        repo, "PLAN-content.md", _content_manifest(), "Help pages", "Every help page exists"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


class _Demo:
    def __init__(self, root: Path, say: Callable[[str], None]) -> None:
        self.root = root
        self.say = say
        self.repo = _make_repo(root)
        self.controller = AdmissionController(root / "admission.json", capacity=4, review_reserve=1)
        self.policy = CapacityPolicy(start=4, ceiling=32, growth_step=4, cooldown_ticks=1)
        self.probe = FakeProbe()
        self.workers = FakeWorkers(self.controller)
        self.posted: list[Any] = []
        self.channel = self._channel()
        self.bot = MagicMock()
        self.bot.cogs = {"ClaudeChatCog": self.workers}
        self.bot.get_channel = MagicMock(side_effect=self._get_channel)
        self.cog = self._cog()

    # -- wiring --------------------------------------------------------------

    def _channel(self) -> Any:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1

        async def send(text: str = "", **_k: Any) -> Any:
            message = MagicMock()
            message.id = 5000 + len(self.posted)
            message.content = text
            self.posted.append(message)
            return message

        channel.send = AsyncMock(side_effect=send)
        return channel

    def _get_channel(self, channel_id: int) -> Any:
        return self.channel if channel_id == 1 else self.workers.thread(channel_id)

    def _cog(self) -> TaskLoopCog:
        cog = TaskLoopCog(
            self.bot,
            allowed_user_ids={1},
            work_root=self.root / "copies",
            store=LoopStore(self.root / "state" / "loops.json"),
        )
        # The quick AI is a live `claude -p`; the demo answers "no answer" (see LIMITATIONS).
        cog._quick_ai = AsyncMock(return_value=None)  # type: ignore[method-assign]
        return cog

    def ledger(self, build_id: str) -> dict:
        path = self.root / "state" / "builds" / f"{build_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))["tasks"]

    def status(self, build_id: str, task_id: str) -> str:
        return self.ledger(build_id)[task_id]["status"]

    async def shutdown(self) -> None:
        """Cancel every build the demo still has running.

        A failed check leaves the fake workers mid-build; their driver tasks
        must not outlive the demo, or the event loop that ran it (CI on Linux
        3.12) waits for them forever at close.
        """
        drivers = [r.task for r in self.cog.running if r.task is not None]
        for driver in drivers:
            driver.cancel()
        if drivers:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.gather(*drivers, return_exceptions=True), 10)

    async def until(self, what: str, condition: Callable[[], bool]) -> None:
        deadline = time.monotonic() + _TIMEOUT
        while not condition():
            if time.monotonic() > deadline:
                raise DemoError(f"{what}: not seen within {_TIMEOUT:.0f} s")
            await asyncio.sleep(_POLL)

    def check(self, behaviour: str, ok: bool, why: str) -> None:
        if not ok:
            raise DemoError(f"{behaviour} — {why}")
        self.say(f"✓ {behaviour}: {why}")

    def questions(self) -> list[Any]:
        return [m for m in self.posted if str(m.content).startswith("❓")]

    def reply(self, ref: int | None, text: str, author: int = 1) -> bool:
        message = MagicMock()
        message.id = 9000 + len(self.posted)
        message.channel.id = 1
        message.channel.send = AsyncMock()
        message.content = text
        message.author.id = author
        message.reference = MagicMock(message_id=ref) if ref else None
        return self.cog.take_message(message)

    # -- the phases ------------------------------------------------------------

    async def run(self) -> None:
        helper.configure_adaptive_limit(
            controller=self.controller,
            policy=self.policy,
            probe=self.probe,
            friction_path=self.root / "state" / "gowork-friction.jsonl",
        )
        a_thread = await self.cog.start_loop(self.channel, str(self.repo / "PLAN-launch.md"))
        a = f"thread-{a_thread.id}"
        await self.until(
            "launch's first workers",
            lambda: sum(1 for t, _n in self.workers.started if t in A_TASKS) == 3,
        )
        b_thread = await self.cog.start_loop(self.channel, str(self.repo / "PLAN-content.md"))
        b = f"thread-{b_thread.id}"
        await self.until("content queued", lambda: self.controller.snapshot().waiting >= 4)
        self.check(
            BEHAVIOURS[0],
            len(self.cog.running) == 2 and self.controller.snapshot().held_tasks == 3,
            "launch (control/product/website/marketing) and content (docs) build side by side "
            "in one repository; capacity 4 with 1 slot kept for reviews, so launch holds 3 "
            f"and content waits with {self.controller.snapshot().waiting}",
        )

        # Dependency wait + the one repair.
        page = "website.catalog-page"
        api = "product.catalog-api"
        self.workers.open(api, 1)
        await self.until("api's repair", lambda: self.ledger(a)[api]["attempt"] == 2)
        self.check(
            BEHAVIOURS[1],
            self.status(a, page) == "pending" and self.workers.attempts_started(page) == 0,
            "the catalog page has not started: its input (the catalog contract) is not "
            "accepted yet, while the styling and the announcement run",
        )
        record = self.ledger(a)[api]
        self.check(
            BEHAVIOURS[2],
            record["attempt"] == 2 and record["lineage_repairs"] == 1,
            "the contract's acceptance check failed once, so exactly one repair attempt "
            f"started, told why ({record['previous_failure'][:60]}…)",
        )

        # Fair turns: the slot api's first attempt freed went to the waiting build.
        await self.until("content's turn", lambda: self.controller.fairness(b).admitted >= 1)
        saved = json.loads((self.root / "admission.json").read_text(encoding="utf-8"))
        self.check(
            BEHAVIOURS[3],
            self.status(a, page) == "pending" and {a, b} <= set(saved["builds"]),
            "the first freed slot went to the waiting content build although launch still "
            "had work; both builds' turns are on disk in admission.json",
        )

        # Adaptive capacity: three healthy ticks, one finished worker, and the loop tops up.
        for _ in range(3):
            helper.tick_capacity()
        self.workers.open(B_TASKS[0], 1)
        await self.until(
            "eleven workers at once",
            lambda: self.workers.max_running >= 11 or self.controller.snapshot().held_tasks >= 11,
        )
        self.check(
            BEHAVIOURS[4],
            helper.session_limit() == 16 and self.workers.max_running >= 11,
            f"capacity grew 4 → {helper.session_limit()} on a healthy host and "
            f"{self.workers.max_running} workers ran at once — past the old fixed ten",
        )

        # Pressure: a critical reading pauses starts; recovery lifts it again.
        self.probe.available_mb = 900.0
        decision = helper.tick_capacity()
        paused = decision is not None and decision.pause_starts
        self.probe.available_mb = 64000.0
        helper.tick_capacity()
        recovered = helper.tick_capacity()
        self.check(
            BEHAVIOURS[5],
            paused and recovered is not None and not recovered.pause_starts,
            f"900 MB free paused new starts ({decision.reason if decision else ''}); "
            "healthy readings resumed growth after the cooldown",
        )

        # Archive: the first content page's thread closed while its siblings still run.
        b1 = self.ledger(b)[B_TASKS[0]]
        b2 = self.ledger(b)[B_TASKS[1]]
        self.check(
            BEHAVIOURS[8],
            b1["status"] == "accepted"
            and b1["result_commit"]
            and self.workers.archived(b1["thread_id"])
            and b2["status"] == "running"
            and not self.workers.archived(b2["thread_id"]),
            "help page 01's thread was archived (never deleted) right after its result was "
            "saved, while page 02's thread stays open with its worker",
        )

        # Mid-build change: website goes to v5 while its styling task is running.
        running_a = next(r for r in self.cog.running if r.build_id == a)
        assert running_a.copy is not None
        plan_path = running_a.copy.plan_path
        text = plan_path.read_text(encoding="utf-8")
        text = re.sub(r'("id": "website",\s*"version": )4', r"\g<1>5", text)
        text = re.sub(r'("plan_id": "website",\s*"plan_version": )4', r"\g<1>5", text)
        plan_path.write_text(text, encoding="utf-8")
        await commit_all(running_a.copy.path, "the person changed the website plan")
        self.workers.open(api, 2)
        await self.until("the page starts", lambda: self.workers.attempts_started(page) == 1)
        styles = "website.page-styles"
        self.workers.open(styles, 1)
        await self.until("styling rework", lambda: self.workers.attempts_started(styles) == 2)
        record = self.ledger(a)[styles]
        self.check(
            BEHAVIOURS[6],
            record["attempt"] == 2
            and "version 5" in (record["rework_reason"] or "")
            and record["previous_commit"]
            and record["lineage_repairs"] == 0
            and self.status(a, api) == "accepted",
            "the styling task finished at v4 and was kept, then a rework (not a repair) "
            "started at v5 knowing the earlier commit; the accepted contract was untouched",
        )

        # Direct-reply blocker: the announcement fails twice, one question, one reply.
        post = "marketing.post"
        self.workers.open(post, 1)
        await self.until("post's repair", lambda: self.workers.attempts_started(post) == 2)
        self.workers.open(post, 2)
        await self.until("one question", lambda: len(self.questions()) == 1)
        question = self.questions()[0]
        ignored = (
            self.reply(None, "retry"),  # not a reply to the question
            self.reply(question.id, "retry", author=99),  # not an authorized person
        )
        await asyncio.sleep(0.05)
        before = self.workers.attempts_started(post)
        self.reply(question.id, "retry")
        self.workers.open(styles, 2)  # a running build picks the retry up on its next turn
        await self.until("post's third attempt", lambda: self.workers.attempts_started(post) == 3)
        self.check(
            BEHAVIOURS[7],
            not any(ignored)
            and before == 2
            and "launch announcement" in question.content
            and self.cog._blockers.by_message(question.id).answer == "retry",  # type: ignore[union-attr]
            "after its one repair the announcement asked one question; a non-reply and a "
            "stranger's reply changed nothing; a direct 'retry' reply started attempt 3",
        )
        self.workers.open(post, 3)
        await self.until("post accepted", lambda: self.status(a, post) == "accepted")
        await self.until("styles accepted", lambda: self.status(a, styles) == "accepted")

        # Restart: every worker still running has committed; the bot 'dies' and comes back.
        await self.until(
            "in-flight work committed",
            lambda: set(self.workers.started) <= self.workers.committed | self.workers.finished,
        )
        in_flight = [
            t for t, r in {**self.ledger(a), **self.ledger(b)}.items() if r["status"] == "running"
        ]
        starts_before = list(self.workers.started)
        drivers = [r.task for r in self.cog.running if r.task is not None]
        for driver in drivers:
            driver.cancel()
        await asyncio.gather(*drivers, return_exceptions=True)
        await self.until("old bot gone", lambda: not self.cog.running)
        self.cog = self._cog()
        resumed = await self.cog.resume_all()
        await self.until("both builds finished", lambda: not self.cog.running)
        salvaged = [t for t in in_flight if {**self.ledger(a), **self.ledger(b)}[t]["accepted"]]
        self.check(
            BEHAVIOURS[9],
            resumed == 2
            and len(in_flight) >= 2
            and salvaged == in_flight
            and self.workers.started == starts_before,
            f"{len(in_flight)} workers were mid-flight ({page} and {len(in_flight) - 1} help "
            "pages); after the restart their committed work was merged, checked and accepted "
            "with no worker rerun",
        )

        # Automatic checked completion: both builds landed without a 'looks good'.
        all_a, all_b = self.ledger(a), self.ledger(b)
        landed = {
            "product": len(list((self.repo / "product").glob("work-*.txt"))),
            "website": len(list((self.repo / "website").glob("work-*.txt"))),
            "marketing": len(list((self.repo / "marketing").glob("work-*.txt"))),
            "docs": len(list((self.repo / "docs").glob("work-*.txt"))),
        }
        kept = [m for m in self.posted if "Kept" in str(m.content)]
        worker_threads = [t for t in self.workers.threads if t.id not in (a_thread.id, b_thread.id)]
        self.check(
            BEHAVIOURS[10],
            all(r["status"] == "accepted" for r in {**all_a, **all_b}.values())
            and landed == {"product": 2, "website": 3, "marketing": 3, "docs": 9}
            and len(kept) == 2
            and _git(self.repo, "remote").strip() == ""
            and all(not t.delete.called for t in worker_threads)
            and all(self.workers.archived(t.id) for t in worker_threads),
            "every task is accepted with checks, both builds were combined into the project "
            f"on this computer ({sum(landed.values())} files, failed attempts' evidence "
            "included) with no reply and no remote; every worker thread is archived, none "
            "deleted",
        )


async def run_demo(root: Path | None = None, *, say: Callable[[str], None] = print) -> int:
    """Run the practice build in *root* (a fresh temporary folder by default).

    Returns the exit status: 0 when every behaviour was observed, 1 otherwise.
    """
    made_root = root is None
    root = Path(tempfile.mkdtemp(prefix="gowork-demo-")) if root is None else Path(root)
    say(f"Go Work practice run in {root} (nothing outside it is touched)")
    started = time.monotonic()
    code = 0
    demo: _Demo | None = None
    try:
        demo = _Demo(root, say)
        await demo.run()
    except DemoError as exc:
        say(f"✗ {exc}")
        code = 1
    except Exception as exc:  # noqa: BLE001 - the demo reports, it does not crash
        say(f"✗ the demo itself failed: {type(exc).__name__}: {exc}")
        code = 1
    finally:
        if demo is not None:
            await demo.shutdown()
        helper.configure_adaptive_limit(controller=None, policy=None, probe=None)
    say(
        f"{'All behaviours observed' if code == 0 else 'Stopped at the first failure'} "
        f"in {time.monotonic() - started:.0f} s."
    )
    say("Pending limitations:")
    for line in LIMITATIONS:
        say(f"- {line}")
    if made_root and code == 0:
        shutil.rmtree(root, ignore_errors=True)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m claude_discord.gowork_demo",
        description="Offline Go Work practice run: real coordinator, fake workers/host/Discord.",
    )
    parser.add_argument("--root", type=Path, help="run here instead of a temporary folder")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)
    for name in ("claude_discord", "claude_code_core"):
        logging.getLogger(name).setLevel(logging.ERROR)
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(run_demo(args.root))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
