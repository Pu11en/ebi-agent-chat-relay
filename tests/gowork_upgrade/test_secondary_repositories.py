"""E2 — the cog confines secondary repositories and never fast-forwards one unchecked.

A manifest may name another repository. Before the build copies it, runs workers
in it or moves its branch, that repository must sit beneath an approved root
(``CCDB_PROJECT_ROOTS``, else the parent folder of the master plan's repository).
On completion it is only fast-forwarded when its plan declares a ``check`` and
that check passes on the merged result; otherwise the copy stays on its branch
and the person is told to merge by hand.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from test_manifest_build_cog import _channel, _cog, _git

_PY = sys.executable.replace("\\", "/")


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text(f"# {path.name}\n")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


def _two_repo_plan(other: Path, *, check: str | None = None) -> str:
    second: dict[str, object] = {
        "id": "other",
        "version": 1,
        "parent_id": "launch",
        "project_path": other.as_posix(),
    }
    if check is not None:
        second["check"] = check
    manifest = {
        "schema_version": 1,
        "plans": [{"id": "launch", "version": 1, "project_path": "control"}, second],
        "requirements": [
            {"id": "REQ-A", "outcome": "control has its note"},
            {"id": "REQ-B", "outcome": "the other repository has its note"},
        ],
        "tasks": [
            {
                "id": "control.note",
                "plan_id": "launch",
                "plan_version": 1,
                "outcome": "Write the control note",
                "dependencies": [],
                "owned_files": ["work-*.txt"],
                "owned_resources": [],
                "required_inputs": ["REQ-A"],
                "output": "a note",
                "acceptance_check": f"{_PY} -c pass",
                "source_requirement": "REQ-A",
            },
            {
                "id": "other.note",
                "plan_id": "other",
                "plan_version": 1,
                "outcome": "Write the other note",
                "dependencies": [],
                "owned_files": ["work-*.txt"],
                "owned_resources": [],
                "required_inputs": ["REQ-B"],
                "output": "a note",
                "acceptance_check": f"{_PY} -c pass",
                "source_requirement": "REQ-B",
            },
        ],
    }
    return "# Two repositories\n\n```gowork-plan\n" + json.dumps(manifest, indent=1) + "\n```\n"


def _main_repo(root: Path, plan: str) -> Path:
    main = _repo(root / "main")
    (main / "control").mkdir()
    (main / "control" / "README.md").write_text("# control\n")
    (main / "PLAN.md").write_text(plan)
    _git(main, "add", ".")
    _git(main, "commit", "-qm", "plan")
    return main


def _worktrees(repo: Path) -> list[str]:
    return [
        ln
        for ln in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if ln.startswith("worktree ")
    ]


def _sent(*mocks: MagicMock) -> str:
    return "\n".join(str(c.args[0]) for m in mocks for c in m.send.await_args_list if c.args)


async def _settle(cog, channel, plan: Path):  # noqa: ANN001, ANN202
    import asyncio

    worker = await cog.start_loop(channel, str(plan))
    for _ in range(2500):
        if not cog.running:
            break
        r = cog.running[0]
        if r.in_review or r.waiting_for_person or r.parked:
            break
        await asyncio.sleep(0.01)
    return worker


@pytest.fixture(autouse=True)
def _no_configured_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCDB_PROJECT_ROOTS", raising=False)


async def test_a_repository_outside_the_approved_area_is_refused_before_any_copy(
    tmp_path: Path,
) -> None:
    elsewhere = _repo(Path(tempfile.mkdtemp(prefix="gowork-outside-")) / "secret")
    main = _main_repo(tmp_path, _two_repo_plan(elsewhere))
    cog, chat, threads, worked = _cog()
    channel = _channel()

    await _settle(cog, channel, main / "PLAN.md")

    assert (
        _worktrees(elsewhere) == [f"worktree {elsewhere.resolve().as_posix()}"]
        or len(_worktrees(elsewhere)) == 1
    ), "no copy of the unapproved repository was made"
    assert worked == [], "no worker ran anywhere"
    text = _sent(channel, *threads)
    assert "can't be built as written" in text
    assert "plan 'other'" in text and "approved project roots" in text


async def test_configured_roots_replace_the_parent_folder_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _repo(tmp_path / "other")  # a sibling, which the default would allow
    approved = tmp_path / "approved"
    approved.mkdir()
    monkeypatch.setenv("CCDB_PROJECT_ROOTS", str(approved))
    main = _main_repo(tmp_path, _two_repo_plan(other))
    cog, _chat, threads, worked = _cog()
    channel = _channel()

    await _settle(cog, channel, main / "PLAN.md")

    assert len(_worktrees(other)) == 1
    assert worked == []
    assert "approved project roots" in _sent(channel, *threads)


async def test_a_sibling_repository_without_a_check_is_left_on_its_branch(
    tmp_path: Path,
) -> None:
    other = _repo(tmp_path / "other")
    before = _git(other, "rev-parse", "HEAD").strip()
    main = _main_repo(tmp_path, _two_repo_plan(other))
    cog, _chat, threads, worked = _cog()
    channel = _channel()

    worker = await _settle(cog, channel, main / "PLAN.md")

    assert not cog.running, "the build finished on its own"
    ledger = json.loads(
        (cog._store.path.with_name("builds") / f"thread-{worker.id}.json").read_text()
    )
    assert {t["status"] for t in ledger["tasks"].values()} == {"accepted"}
    assert ledger.get("integrated")
    # The master repository landed; the other repository's branch did not move.
    assert len(list((main / "control").glob("work-*.txt"))) == 1
    assert _git(other, "rev-parse", "HEAD").strip() == before
    assert list(other.glob("work-*.txt")) == []
    # Its copy is still there, on its gowork branch, for the person to merge by hand.
    kept = _worktrees(other)
    assert len(kept) == 2
    branches = _git(other, "branch", "--list", "--format=%(refname:short)", "gowork/*").split()
    assert len(branches) == 1
    text = _sent(channel, *threads)
    assert f"left on branch `{branches[0]}` in {other.resolve().as_posix()}" in text.replace(
        "\\", "/"
    )
    assert "no check defined, merge by hand" in text
    assert len(worked) == 2 and "control" in [w[0] for w in worked]  # both tasks ran


async def test_a_sibling_repository_with_a_passing_check_is_integrated(tmp_path: Path) -> None:
    other = _repo(tmp_path / "other")
    before = _git(other, "rev-parse", "HEAD").strip()
    main = _main_repo(tmp_path, _two_repo_plan(other, check=f"{_PY} -c pass"))
    cog, _chat, _threads, _worked = _cog()
    channel = _channel()

    await _settle(cog, channel, main / "PLAN.md")

    assert not cog.running
    assert _git(other, "rev-parse", "HEAD").strip() != before
    assert len(list(other.glob("work-*.txt"))) == 1
    assert len(_worktrees(other)) == 1, "the copy was removed after landing"
    assert _git(other, "branch", "--list", "gowork/*").strip() == ""
    assert "merge by hand" not in _sent(channel)


async def test_a_sibling_repository_whose_check_fails_keeps_the_build(tmp_path: Path) -> None:
    other = _repo(tmp_path / "other")
    before = _git(other, "rev-parse", "HEAD").strip()
    main = _main_repo(tmp_path, _two_repo_plan(other, check=f"{_PY} -c 'raise SystemExit(3)'"))
    cog, _chat, threads, _worked = _cog()
    channel = _channel()

    await _settle(cog, channel, main / "PLAN.md")

    assert _git(other, "rev-parse", "HEAD").strip() == before
    assert len(_worktrees(other)) == 2
    text = _sent(channel, *threads)
    assert "couldn't add" in text and "fails its check" in text
    assert list((main / "control").glob("work-*.txt")) == [], "the master waits too"
    subprocess.run(["git", "-C", str(main), "status", "--porcelain"], check=True)
