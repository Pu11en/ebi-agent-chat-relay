"""Feature dispatch contracts exercised against disposable, real Git repositories."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from extensions.feature_workflow.coordinator import Coordinator, WorkflowError, plan_digest


async def git(repo: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await process.communicate()
    assert process.returncode == 0, err.decode()
    return out.decode().strip()


class FakeAPI:
    def __init__(self) -> None:
        self.sessions: list[dict[str, str]] = []
        self.spawns: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.fail_spawn = False

    async def __call__(self, method: str, endpoint: str, body: Any = None) -> dict:
        if method == "GET":
            assert endpoint == "/api/sessions?state=running"
            return {"sessions": self.sessions}
        if endpoint == "/api/spawn":
            self.spawns.append(body)
            if self.fail_spawn:
                raise TimeoutError("response lost after server accepted request")
            return {"thread_id": str(1000 + len(self.spawns))}
        self.messages.append(body)
        return {"status": "delivered"}


async def test_queue_admission_preserves_approval_and_bounds_outstanding_work(setup: tuple) -> None:
    original, api, _ = setup
    coordinator = Coordinator(
        original.repo,
        original.manifest_path,
        original.state_dir,
        api,
        channel_id=30,
        max_running=1,
        queue_ready=True,
    )
    api.sessions = [{"thread_id": str(i), "state": "running"} for i in range(5)]
    await coordinator.tick()
    assert not api.spawns
    await coordinator.approve("Drew approved both trial features", ["alpha", "beta"])
    await coordinator.tick()
    await coordinator.tick()
    assert len(api.spawns) == 1
    assert api.spawns[0]["auto_start"] is True
    assert (await coordinator.status())["tasks"]["beta"]["status"] == "pending"
    await result(coordinator, "alpha")
    await coordinator.tick()
    assert len(api.spawns) == 2
    assert not api.messages  # No stop/interrupt sent to any existing conversation.


@pytest.fixture
async def setup(tmp_path: Path) -> tuple[Coordinator, FakeAPI, dict]:
    repo = tmp_path / "repo"
    repo.mkdir()
    await git(repo, "init", "-b", "main")
    await git(repo, "config", "user.name", "Workflow Test")
    await git(repo, "config", "user.email", "workflow@example.invalid")
    features = []
    for name in ("alpha", "beta"):
        plan = repo / "openspec" / "changes" / name / "proposal.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(f"Feature {name}: isolated demonstration.\n")
        files = [str(plan.relative_to(repo))]
        features.append(
            {
                "id": name,
                "change": name,
                "plan_owner": "10",
                "approved_revision": plan_digest(repo, files),
                "plan_files": files,
            }
        )
    await git(repo, "add", ".")
    await git(repo, "commit", "-m", "foundation")
    foundation = await git(repo, "rev-parse", "HEAD")
    manifest = {
        "schema": 1,
        "run_id": "trial",
        "foundation_sha": foundation,
        "integration_owner": "20",
        "features": features,
        "tasks": [
            {
                "id": name,
                "feature": name,
                "depends_on": [],
                "owned_paths": [f"demo/{name}.txt"],
                "prompt": f"Build {name}.",
            }
            for name in ("alpha", "beta")
        ],
    }
    (repo / "run.json").write_text(json.dumps(manifest))
    await git(repo, "add", "run.json")
    await git(repo, "commit", "-m", "commit manifest")
    api = FakeAPI()
    coordinator = Coordinator(repo, repo / "run.json", tmp_path / "runtime", api, channel_id=30)
    return coordinator, api, manifest


async def change_manifest(coordinator: Coordinator, manifest: dict) -> None:
    coordinator.manifest_path.write_text(json.dumps(manifest))
    await git(coordinator.repo, "add", "run.json")
    await git(coordinator.repo, "commit", "-m", "revise manifest")


async def result(coordinator: Coordinator, task: str, *, path: str | None = None) -> str:
    state = await coordinator.status()
    entry = state["tasks"][task]
    worktree = coordinator.repo.parent / f"wt-{entry['thread_id']}"
    await git(
        coordinator.repo,
        "worktree",
        "add",
        str(worktree),
        "-b",
        f"session/{entry['thread_id']}",
        entry["foundation_sha"],
    )
    changed = worktree / (path or f"demo/{task}.txt")
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("isolated worker result\n")
    await git(worktree, "add", ".")
    await git(worktree, "commit", "-m", f"feat: {task}")
    commit = await git(worktree, "rev-parse", "HEAD")
    payload = {
        "task": task,
        "approval_digest": state["approval_digest"],
        "commit": commit,
        "worktree": str(worktree),
        "tests": ["file content verified"],
    }
    (coordinator.state_dir / "results").mkdir(exist_ok=True)
    (coordinator.state_dir / "results" / f"{task}.json").write_text(json.dumps(payload))
    return commit


async def test_unapproved_work_never_dispatches(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.tick()
    assert not api.spawns
    with pytest.raises(WorkflowError, match="authorization"):
        await coordinator.approve("")


async def test_feature_subset_approval_and_idempotent_recovery(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    restored = Coordinator(
        coordinator.repo, coordinator.manifest_path, coordinator.state_dir, api, channel_id=30
    )
    await restored.tick()
    assert len(api.spawns) == 1
    assert api.spawns[0]["auto_start"] is True
    assert "cwd" not in api.spawns[0]
    assert "session/$DISCORD_THREAD_ID" in api.spawns[0]["prompt"]
    assert str(coordinator.repo) in api.spawns[0]["prompt"]
    assert (await restored.status())["tasks"]["beta"]["status"] == "pending"


async def test_capacity_defers_without_mutating_other_threads(setup: tuple) -> None:
    coordinator, api, _ = setup
    coordinator.queue_ready = False
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    api.sessions = [{"thread_id": str(i), "state": "running"} for i in range(3)]
    await coordinator.tick()
    assert not api.spawns
    api.sessions.pop()
    await coordinator.tick()
    assert len(api.spawns) == 1
    assert not api.messages


async def test_busy_projects_do_not_prevent_default_worker_submission(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    api.sessions = [{"thread_id": str(i), "state": "running"} for i in range(10)]
    await coordinator.tick()
    assert len(api.spawns) == 2
    assert not api.messages


async def test_worker_window_counts_ambiguous_tasks(setup: tuple) -> None:
    coordinator, api, _ = setup
    coordinator.max_running = 1
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    api.fail_spawn = True
    await coordinator.tick()
    await coordinator.tick()
    assert len(api.spawns) == 1


async def test_dirty_or_committed_manifest_drift_blocks_dispatch(setup: tuple) -> None:
    coordinator, api, manifest = setup
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    manifest["tasks"][0]["prompt"] = "Unapproved different work"
    coordinator.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(WorkflowError, match="committed|drift"):
        await coordinator.tick()
    await change_manifest(coordinator, manifest)
    with pytest.raises(WorkflowError, match="drift"):
        await coordinator.tick()
    assert not api.spawns


async def test_plan_drift_blocks_dispatch(setup: tuple) -> None:
    coordinator, api, _ = setup
    plan = coordinator.repo / "openspec/changes/alpha/proposal.md"
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    plan.write_text("Unapproved replacement decision\n")
    with pytest.raises(WorkflowError, match="plan|revision"):
        await coordinator.tick()
    assert not api.spawns


async def test_ambiguous_spawn_requires_reconciliation(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    api.fail_spawn = True
    await coordinator.tick()
    await coordinator.tick()
    assert len(api.spawns) == 1
    assert (await coordinator.status())["tasks"]["alpha"]["status"] == "ambiguous"
    await coordinator.reconcile("alpha", "1001")
    assert (await coordinator.status())["tasks"]["alpha"]["status"] == "dispatched"


async def test_crash_after_intent_does_not_retry(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    state = await coordinator.status()
    state["tasks"]["alpha"]["status"] = "spawning"
    (coordinator.state_dir / "state.json").write_text(json.dumps(state))
    await coordinator.tick()
    assert not api.spawns
    assert (await coordinator.status())["tasks"]["alpha"]["status"] == "ambiguous"


async def test_dependencies_require_verified_integration(setup: tuple) -> None:
    coordinator, api, manifest = setup
    manifest["tasks"][1]["depends_on"] = ["alpha"]
    await change_manifest(coordinator, manifest)
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    await coordinator.tick()
    commit = await result(coordinator, "alpha")
    await coordinator.tick()
    assert len(api.spawns) == 1
    assert (await coordinator.status())["tasks"]["alpha"]["status"] == "verified"
    with pytest.raises(WorkflowError, match="ancestor|integrat|checkout"):
        await coordinator.integrate("alpha", manifest["foundation_sha"])
    await git(coordinator.repo, "merge", "--no-edit", commit)
    integrated = await git(coordinator.repo, "rev-parse", "HEAD")
    await coordinator.integrate("alpha", integrated)
    await coordinator.tick()
    assert len(api.spawns) == 2
    assert (await coordinator.status())["tasks"]["beta"]["foundation_sha"] == integrated


async def test_successful_result_never_respawns_and_handoff_is_durable(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    await result(coordinator, "alpha")
    await coordinator.tick()
    await coordinator.tick()
    assert len(api.spawns) == 1
    assert len(api.messages) == 1
    assert api.messages[0]["mode"] == "queue"
    assert api.messages[0]["from_thread"] == "1001"


@pytest.mark.parametrize("bad", ["ownership", "digest", "ancestry", "worktree", "dirty", "tests"])
async def test_invalid_results_cannot_be_collected(setup: tuple, bad: str) -> None:
    coordinator, _, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    await result(coordinator, "alpha", path="other.txt" if bad == "ownership" else None)
    file = coordinator.state_dir / "results/alpha.json"
    payload = json.loads(file.read_text())
    if bad == "digest":
        payload["approval_digest"] = "0" * 64
    elif bad == "ancestry":
        worktree = Path(payload["worktree"])
        await git(worktree, "checkout", "--orphan", "unrelated")
        await git(worktree, "commit", "-m", "unrelated root")
        payload["commit"] = await git(worktree, "rev-parse", "HEAD")
    elif bad == "worktree":
        payload["worktree"] = str(coordinator.repo)
    elif bad == "dirty":
        (Path(payload["worktree"]) / "uncommitted.txt").write_text("not committed")
    elif bad == "tests":
        payload["tests"] = []
    file.write_text(json.dumps(payload))
    with pytest.raises(WorkflowError):
        await coordinator.collect()
    assert (await coordinator.status())["tasks"]["alpha"]["status"] == "dispatched"


async def test_overlapping_independent_ownership_is_rejected(setup: tuple) -> None:
    coordinator, _, manifest = setup
    manifest["tasks"][1]["owned_paths"] = ["demo/"]
    await change_manifest(coordinator, manifest)
    with pytest.raises(WorkflowError, match="overlap"):
        await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])


async def test_successful_spawn_persists_thread_binding(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    binding = json.loads((coordinator.state_dir / "threads/1001.json").read_text())
    assert binding["task"] == "alpha"
    assert binding["change"] == "alpha"
    assert binding["worktree"] == str(coordinator.repo.parent / "wt-1001")
    assert binding["repo"] == str(coordinator.repo)
    assert str(coordinator.state_dir / "threads") in api.spawns[0]["prompt"]


async def test_watch_exits_after_handoff_without_waiting_for_integration(setup: tuple) -> None:
    coordinator, _, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    await result(coordinator, "alpha")
    state = await asyncio.wait_for(coordinator.watch(15), timeout=2)
    assert state["tasks"]["alpha"]["status"] == "verified"


async def test_lock_rejects_second_coordinator_without_duplicate_dispatch(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    with coordinator._lock(), pytest.raises(WorkflowError, match="lock"):
        await coordinator.tick()
    assert not api.spawns


async def test_cyclic_dependencies_are_rejected(setup: tuple) -> None:
    coordinator, _, manifest = setup
    manifest["tasks"][0]["depends_on"] = ["beta"]
    manifest["tasks"][1]["depends_on"] = ["alpha"]
    await change_manifest(coordinator, manifest)
    with pytest.raises(WorkflowError, match="cycle"):
        await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])


async def test_ambiguous_handoff_is_not_repeated(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    await result(coordinator, "alpha")

    async def failed_handoff(method: str, endpoint: str, body: Any = None) -> dict:
        if method == "POST":
            api.messages.append(body)
            raise TimeoutError("accepted but response lost")
        return await api(method, endpoint, body)

    coordinator.api = failed_handoff
    await coordinator.tick()
    await coordinator.tick()
    assert len(api.messages) == 1
    assert "ambiguous" in (await coordinator.status())["handoffs"].values()


async def test_integration_requires_clean_integration_checkout_head(setup: tuple) -> None:
    coordinator, _, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    commit = await result(coordinator, "alpha")
    await coordinator.collect()
    with pytest.raises(WorkflowError, match="HEAD|checkout"):
        await coordinator.integrate("alpha", commit)
    await git(coordinator.repo, "merge", "--no-edit", commit)
    integrated = await git(coordinator.repo, "rev-parse", "HEAD")
    (coordinator.repo / "demo/alpha.txt").write_text("uncommitted integration edits")
    with pytest.raises(WorkflowError, match="clean"):
        await coordinator.integrate("alpha", integrated)


async def test_expanding_approval_preserves_authorization_history(setup: tuple) -> None:
    coordinator, _, _ = setup
    await coordinator.approve("first authorization", features=["alpha"])
    approval = await coordinator.approve("second authorization", features=["beta"])
    assert [entry["authorization_ref"] for entry in approval["authorizations"]] == [
        "first authorization",
        "second authorization",
    ]


async def test_build_task_cannot_own_approved_plan(setup: tuple) -> None:
    coordinator, _, manifest = setup
    manifest["tasks"][0]["owned_paths"] = ["openspec/"]
    await change_manifest(coordinator, manifest)
    with pytest.raises(WorkflowError, match="plan|manifest"):
        await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])


async def test_watch_waits_for_running_workers_before_completion_handoff(
    setup: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha", "beta"])
    await coordinator.tick()
    await result(coordinator, "alpha")
    slept = []

    async def finish_other_worker(interval: float) -> None:
        slept.append(interval)
        await result(coordinator, "beta")

    monkeypatch.setattr(asyncio, "sleep", finish_other_worker)
    await coordinator.watch()
    assert slept == [15]
    assert len(api.messages) == 1


async def test_result_survives_ebi_clean_worktree_cleanup(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    commit = await result(coordinator, "alpha")
    await git(coordinator.repo, "worktree", "remove", str(coordinator.repo.parent / "wt-1001"))
    await coordinator.tick()
    entry = (await coordinator.status())["tasks"]["alpha"]
    assert entry["status"] == "verified"
    assert entry["result"]["commit"] == commit
    assert len(api.spawns) == 1


@pytest.mark.parametrize("replacement", ["missing", "different-commit"])
async def test_removed_worktree_requires_exact_persisted_worker_branch(
    setup: tuple,
    replacement: str,
) -> None:
    coordinator, _, manifest = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    await result(coordinator, "alpha")
    await git(coordinator.repo, "worktree", "remove", str(coordinator.repo.parent / "wt-1001"))
    if replacement == "missing":
        await git(coordinator.repo, "branch", "-D", "session/1001")
    else:
        await git(coordinator.repo, "branch", "-f", "session/1001", manifest["foundation_sha"])
    with pytest.raises(WorkflowError):
        await coordinator.collect()
    assert (await coordinator.status())["tasks"]["alpha"]["status"] == "dispatched"


async def test_tick_repairs_binding_after_post_spawn_write_gap(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    binding = coordinator.state_dir / "threads/1001.json"
    binding.unlink()
    await coordinator.tick()
    assert json.loads(binding.read_text())["task"] == "alpha"
    assert len(api.spawns) == 1


async def test_ambiguous_dispatch_retains_only_error_class(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    api.fail_spawn = True
    await coordinator.tick()
    entry = (await coordinator.status())["tasks"]["alpha"]
    assert entry["error_class"] == "TimeoutError"
    assert "response lost" not in json.dumps(entry)


async def test_worker_brief_preserves_completed_or_partial_prior_work(setup: tuple) -> None:
    coordinator, api, _ = setup
    await coordinator.approve("Drew authorized trial", features=["alpha"])
    await coordinator.tick()
    prompt = api.spawns[0]["prompt"]
    assert "If the result JSON already exists, do not rebuild" in prompt
    assert "reuse" in prompt
    assert "recreate" in prompt
    assert "existing session branch" in prompt


async def test_approval_never_defaults_to_all_features(setup: tuple) -> None:
    coordinator, api, _ = setup
    with pytest.raises(WorkflowError, match="explicit|feature"):
        await coordinator.approve("Drew authorized only the discussed feature")
    await coordinator.tick()
    assert not api.spawns
