"""Durable, opt-in feature dispatch through Ebi's existing localhost control plane.

Run with Python 3.12; no bot imports, extra dependencies, or resident AI turn.
The approved manifest is immutable. Runtime approvals and results live separately.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import tempfile
import urllib.request
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any


class WorkflowError(Exception):
    """A workflow precondition failed; no automatic recovery is authorized."""


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise WorkflowError("Expected a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise WorkflowError("Path must stay inside the repository")
    return value


def _file(repo: Path, name: str) -> Path:
    path = repo / _relative(name)
    if not path.resolve().is_relative_to(repo.resolve()):
        raise WorkflowError("Path escapes the repository")
    return path


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plan_digest(repo: Path, paths: list[str]) -> str:
    """Stable approval revision: canonical path-to-content-hash mapping."""
    return _digest(
        {name: hashlib.sha256(_file(repo, name).read_bytes()).hexdigest() for name in paths}
    )


async def _git(repo: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode:
        raise WorkflowError(f"Git validation failed: {args[0]}")
    return stdout.decode()


async def _ancestor(repo: Path, ancestor: str, commit: str) -> None:
    if not all(re.fullmatch(r"[a-f0-9]{40}", sha) for sha in (ancestor, commit)):
        raise WorkflowError("Expected full commit SHA")
    try:
        await _git(repo, "merge-base", "--is-ancestor", ancestor, commit)
    except WorkflowError as exc:
        raise WorkflowError("Required commit is not an ancestor of the integration/result") from exc


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class EbiAPI:
    def __init__(self, origin: str) -> None:
        self.origin = origin.rstrip("/")
        if not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost|\[::1\]):\d+", self.origin):
            raise WorkflowError("Use Ebi's trusted localhost HTTP origin")

    async def __call__(self, method: str, endpoint: str, body: Any = None) -> dict:
        def request() -> dict:
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request(
                self.origin + endpoint,
                data=data,
                method=method,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)

        return await asyncio.to_thread(request)


class Coordinator:
    def __init__(
        self,
        repo: Path,
        manifest: Path,
        state_dir: Path,
        api: Any,
        *,
        channel_id: int | None = None,
        max_running: int = 3,
        worktree_root: Path | None = None,
        queue_ready: bool = False,
    ) -> None:
        self.repo = repo.resolve()
        self.manifest_path = manifest.resolve()
        self.state_dir = state_dir.resolve()
        self.api, self.channel_id, self.max_running = api, channel_id, max_running
        self.queue_ready = queue_ready
        self.worktree_root = (worktree_root or self.repo.parent).resolve()
        if max_running < 1 or (channel_id is not None and channel_id < 1):
            raise WorkflowError("Capacity and channel ID must be positive")
        if self.state_dir.is_relative_to(self.repo):
            raise WorkflowError("Runtime state must live outside the repository")

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "lock").open("a") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WorkflowError("Another coordinator holds the run lock") from exc
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _save(self, state: dict) -> None:
        _atomic(self.state_dir / "state.json", state)

    def _bind(self, manifest: dict, task: dict, entry: dict) -> None:
        feature = next(f for f in manifest["features"] if f["id"] == task["feature"])
        _atomic(
            self.state_dir / "threads" / f"{entry['thread_id']}.json",
            {
                "repo": str(self.repo),
                "manifest": str(self.manifest_path),
                "state_dir": str(self.state_dir),
                "task": task["id"],
                "feature": feature["id"],
                "change": feature["change"],
                "plan_owner": feature["plan_owner"],
                "integration_owner": manifest["integration_owner"],
                "worktree": str(self.worktree_root / f"wt-{entry['thread_id']}"),
            },
        )

    async def _load(self) -> tuple[dict, dict, dict | None]:
        relative = self.manifest_path.relative_to(self.repo).as_posix()
        raw = self.manifest_path.read_text()
        if raw != await _git(self.repo, "show", f"HEAD:{relative}"):
            raise WorkflowError("Manifest must be committed and unchanged")
        manifest = json.loads(raw)
        if manifest.get("schema") != 1:
            raise WorkflowError("Unsupported manifest schema")
        for name in (
            manifest["run_id"],
            *(x["id"] for x in manifest["tasks"]),
            *(x["id"] for x in manifest["features"]),
        ):
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
                raise WorkflowError("Run, task and feature IDs must be short slugs")
        await _ancestor(self.repo, manifest["foundation_sha"], manifest["foundation_sha"])
        if not str(manifest["integration_owner"]).isdigit():
            raise WorkflowError("Integration owner must be a Discord thread ID")
        features = {f["id"]: f for f in manifest["features"]}
        tasks = {t["id"]: t for t in manifest["tasks"]}
        if len(features) != len(manifest["features"]) or len(tasks) != len(manifest["tasks"]):
            raise WorkflowError("Feature/task IDs must be unique")
        for feature in features.values():
            change = feature["change"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", change):
                raise WorkflowError("OpenSpec change must be a short slug")
            if not str(feature["plan_owner"]).isdigit():
                raise WorkflowError("Plan owner must be a Discord thread ID")
            files = feature["plan_files"]
            root = self.repo / "openspec" / "changes" / change
            actual = {p.relative_to(self.repo).as_posix() for p in root.rglob("*") if p.is_file()}
            if not files or set(files) != actual:
                raise WorkflowError("plan_files must cover the complete OpenSpec change")
            if plan_digest(self.repo, files) != feature["approved_revision"]:
                raise WorkflowError("OpenSpec plan revision drift")
            for path in files:
                if _file(self.repo, path).read_text() != await _git(
                    self.repo, "show", f"{manifest['foundation_sha']}:{path}"
                ):
                    raise WorkflowError("Approved plan must be present in the foundation")
        ancestors: dict[str, set[str]] = {}

        def dependencies(task_id: str, visiting: set[str]) -> set[str]:
            if task_id in visiting or task_id not in tasks:
                raise WorkflowError("Task dependencies contain a cycle or unknown task")
            if task_id not in ancestors:
                ancestors[task_id] = set()
                for dependency in tasks[task_id]["depends_on"]:
                    ancestors[task_id] |= {dependency} | dependencies(
                        dependency, visiting | {task_id}
                    )
            return ancestors[task_id]

        for task in tasks.values():
            dependencies(task["id"], set())
            if (
                task["feature"] not in features
                or not task["owned_paths"]
                or not task["prompt"].strip()
            ):
                raise WorkflowError("Tasks require a feature, owned paths and a prompt")
            for path in task["owned_paths"]:
                _relative(path)
                protected = [relative, *(p for f in features.values() for p in f["plan_files"])]
                if any(path == p or (path.endswith("/") and p.startswith(path)) for p in protected):
                    raise WorkflowError("Build ownership cannot include approved plans or manifest")
            for other in tasks.values():
                if task is other or other["id"] in ancestors[task["id"]]:
                    continue
                if task["id"] in dependencies(other["id"], set()):
                    continue
                if any(
                    a == b
                    or (a.endswith("/") and b.startswith(a))
                    or (b.endswith("/") and a.startswith(b))
                    for a in task["owned_paths"]
                    for b in other["owned_paths"]
                ):
                    raise WorkflowError("Independent tasks have overlapping file ownership")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        approval_path = self.state_dir / "approval.json"
        approval = json.loads(approval_path.read_text()) if approval_path.exists() else None
        if approval and approval["digest"] != digest:
            raise WorkflowError("Approved manifest drift; create a new run")
        state_path = self.state_dir / "state.json"
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "approval_digest": digest,
                "foundation_sha": manifest["foundation_sha"],
                "tasks": {name: {"status": "pending"} for name in tasks},
                "handoffs": {},
            }
        )
        if state["approval_digest"] != digest:
            raise WorkflowError("Run manifest drift; use a new state directory")
        return manifest, state, approval

    async def approve(self, authorization_ref: str, features: list[str] | None = None) -> dict:
        if not authorization_ref.strip():
            raise WorkflowError("An explicit user authorization reference is required")
        if not features:
            raise WorkflowError("Explicit feature selection is required for approval")
        with self._lock():
            manifest, state, prior = await self._load()
            selected = set(features)
            if not selected or not selected <= {f["id"] for f in manifest["features"]}:
                raise WorkflowError("Unknown or empty feature approval")
            approval = {
                "digest": state["approval_digest"],
                "features": sorted(selected | set(prior["features"] if prior else [])),
                "authorization_ref": authorization_ref,
                "authorizations": [
                    *(prior.get("authorizations", []) if prior else []),
                    {"authorization_ref": authorization_ref, "features": sorted(selected)},
                ],
            }
            self._save(state)
            _atomic(self.state_dir / "approval.json", approval)
            return approval

    async def status(self) -> dict:
        with self._lock():
            path = self.state_dir / "state.json"
            if path.exists():
                return json.loads(path.read_text())
            _, state, _ = await self._load()
            return state

    def _brief(self, manifest: dict, task: dict, state: dict) -> str:
        feature = next(f for f in manifest["features"] if f["id"] == task["feature"])
        base = state["tasks"][task["id"]]["foundation_sha"]
        worktree = shlex.quote(str(self.worktree_root)) + '/wt-"$DISCORD_THREAD_ID"'
        command = (
            f"git -C {shlex.quote(str(self.repo))} worktree add {worktree} "
            f'-b "session/$DISCORD_THREAD_ID" {base}'
        )
        recreate = (
            f"git -C {shlex.quote(str(self.repo))} worktree add {worktree} "
            '"session/$DISCORD_THREAD_ID"'
        )
        result = self.state_dir / "results" / f"{task['id']}.json"
        backup = manifest.get("backup_remote")
        return (
            f"Approved feature worker {manifest['run_id']}/{task['id']}. "
            f"Plan owner thread {feature['plan_owner']}; "
            f"integration owner {manifest['integration_owner']}.\n"
            f"Your durable thread binding is {self.state_dir}/threads/$DISCORD_THREAD_ID.json "
            "(written immediately after thread creation); use it when resuming this feature.\n"
            f"Read repository instructions at {self.repo}. "
            f"First check {result}. If the result JSON already exists, do not rebuild; "
            "read its evidence, report the existing result, "
            "and stop without recreating a worktree. "
            "If the expected worktree exists, verify its repository, session branch and foundation "
            "ancestry, then reuse it and preserve any partial work. If the worktree was removed "
            "but the existing session branch remains, verify its foundation ancestry and recreate "
            f"the worktree from that branch:\n{recreate}\n"
            "Only when neither the worktree nor session branch exists, create the required "
            f"isolated worktree from the approved foundation:\n{command}\n"
            "Work ONLY inside that absolute worktree, with every command explicitly using it. "
            "Never edit the main checkout or another worker's files; do not restart services.\n"
            f"Read these approved OpenSpec plan files in your worktree: {feature['plan_files']}. "
            f"Plan revision {feature['approved_revision']}; "
            "preserve these decisions and plan files.\n"
            f"Own ONLY these paths: {task['owned_paths']}. {task['prompt']}\n"
            "Run relevant checks, commit all owned work, and leave a clean worktree. "
            + (
                f"Push your branch to configured backup remote {backup!r}. "
                if backup
                else "Push the branch to an existing appropriate backup remote if configured. "
            )
            + f"Write atomic JSON result to {result}; create its parent directory if needed. "
            f"Required fields: task={task['id']!r}, approval_digest={state['approval_digest']!r}, "
            "commit=<full 40-character commit SHA>, worktree=<absolute worktree path>, "
            "tests=<nonempty array of actual check commands and outcomes>. "
            "Do not mark success until checks pass. Include evidence in your final Discord reply. "
            "The external coordinator verifies Git evidence and wakes the integration owner; "
            "do not merge your work or launch extra workers."
        )

    async def _collect(self, manifest: dict, state: dict) -> None:
        for task in manifest["tasks"]:
            entry = state["tasks"][task["id"]]
            path = self.state_dir / "results" / f"{task['id']}.json"
            if entry["status"] != "dispatched" or not path.exists():
                continue
            result = json.loads(path.read_text())
            if (
                result["task"] != task["id"]
                or result["approval_digest"] != state["approval_digest"]
            ):
                raise WorkflowError("Result task or approval digest mismatch")
            if (
                not isinstance(result["tests"], list)
                or not result["tests"]
                or not all(isinstance(test, str) and test.strip() for test in result["tests"])
            ):
                raise WorkflowError("Result needs actual test evidence")
            expected = self.worktree_root / f"wt-{entry['thread_id']}"
            worktree = Path(result["worktree"])
            if worktree != expected or worktree.resolve() != expected:
                raise WorkflowError("Result worktree does not match dispatched worker")
            commit = result["commit"]
            await _ancestor(self.repo, entry["foundation_sha"], commit)
            branch = f"refs/heads/session/{entry['thread_id']}"
            if (await _git(self.repo, "rev-parse", "--verify", branch)).strip() != commit:
                raise WorkflowError("Result commit is not the persisted worker branch HEAD")
            # Ebi normally removes clean worktrees at turn end but preserves session branches.
            if worktree.exists():
                common = await _git(
                    self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
                )
                if (
                    await _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
                    != common
                ):
                    raise WorkflowError("Result worktree belongs to another repository")
                if await _git(worktree, "status", "--porcelain"):
                    raise WorkflowError("Result worktree must be clean")
                if (await _git(worktree, "symbolic-ref", "HEAD")).strip() != branch:
                    raise WorkflowError("Result worktree is not on its expected session branch")
                if (await _git(worktree, "rev-parse", "HEAD")).strip() != commit:
                    raise WorkflowError("Result commit is not the worker HEAD")
            changed = (
                (
                    await _git(
                        self.repo,
                        "diff",
                        "--name-only",
                        "-z",
                        "--no-renames",
                        entry["foundation_sha"],
                        commit,
                        "--",
                    )
                )
                .strip("\0")
                .split("\0")
            )
            if not all(
                path
                and any(
                    path == owner or (owner.endswith("/") and path.startswith(owner))
                    for owner in task["owned_paths"]
                )
                for path in changed
            ):
                raise WorkflowError("Result changes violate owned paths or contain no work")
            entry.update(status="verified", result=result)
            self._save(state)

    async def collect(self) -> dict:
        with self._lock():
            manifest, state, approval = await self._load()
            if approval:
                await self._collect(manifest, state)
            return state

    async def integrate(self, task_id: str, commit: str) -> dict:
        with self._lock():
            _, state, _ = await self._load()
            entry = state["tasks"][task_id]
            if entry["status"] not in {"verified", "integrated"}:
                raise WorkflowError("Only a verified task can be integrated")
            if (await _git(self.repo, "rev-parse", "HEAD")).strip() != commit:
                raise WorkflowError("Integration commit must be the integration checkout HEAD")
            if await _git(self.repo, "status", "--porcelain", "--untracked-files=no"):
                raise WorkflowError("Integration checkout must be clean")
            await _ancestor(self.repo, state["foundation_sha"], commit)
            await _ancestor(self.repo, entry["result"]["commit"], commit)
            entry.update(status="integrated", integration_commit=commit)
            state["foundation_sha"] = commit
            self._save(state)
            return state

    async def reconcile(self, task_id: str, thread_id: str) -> dict:
        if not str(thread_id).isdigit():
            raise WorkflowError("Reconciliation requires the actual Discord thread ID")
        with self._lock():
            manifest, state, _ = await self._load()
            entry = state["tasks"][task_id]
            if entry["status"] not in {"spawning", "ambiguous"}:
                raise WorkflowError("Only ambiguous dispatches need reconciliation")
            entry.update(status="dispatched", thread_id=str(thread_id))
            self._save(state)
            self._bind(manifest, next(t for t in manifest["tasks"] if t["id"] == task_id), entry)
            return state

    async def _handoff(self, manifest: dict, state: dict) -> None:
        if any(
            e["status"] in {"dispatched", "spawning", "ambiguous"} for e in state["tasks"].values()
        ):
            return
        ready = sorted(name for name, e in state["tasks"].items() if e["status"] == "verified")
        if not ready:
            return
        key = _digest(ready)
        if key in state["handoffs"]:
            return
        state["handoffs"][key] = "sending"
        self._save(state)
        source = state["tasks"][ready[-1]]["thread_id"]
        try:
            await self.api(
                "POST",
                f"/api/threads/{manifest['integration_owner']}/message",
                {
                    "from_thread": source,
                    "mode": "queue",
                    "hop": 0,
                    "text": f"External feature coordinator: {manifest['run_id']} "
                    "has verified results "
                    f"for {', '.join(ready)}. Read {self.state_dir / 'state.json'} and the "
                    "committed manifest, inspect worker evidence, integrate in your isolated "
                    "worktree, then record each integration with the coordinator CLI. "
                    "Other conversations must remain running. No worker action is requested.",
                },
            )
            state["handoffs"][key] = "sent"
        except Exception as error:
            state["handoffs"][key] = "ambiguous"
            state.setdefault("handoff_errors", {})[key] = type(error).__name__
        self._save(state)

    async def tick(self) -> dict:
        with self._lock():
            manifest, state, approval = await self._load()
            for task in manifest["tasks"]:
                entry = state["tasks"][task["id"]]
                if entry["status"] == "spawning":
                    entry.update(status="ambiguous", error_class="ProcessInterrupted")
                if "thread_id" in entry:
                    binding = self.state_dir / "threads" / f"{entry['thread_id']}.json"
                    if not binding.exists():
                        self._bind(manifest, task, entry)
            self._save(state)
            if not approval:
                return state
            await self._collect(manifest, state)
            sessions = await self.api("GET", "/api/sessions?state=running")
            if self.queue_ready:
                # Ebi's semaphore controls actual execution. Bound this run's
                # outstanding queue without waiting for the whole server to idle.
                occupied = sum(
                    entry["status"] in {"dispatched", "spawning", "ambiguous"}
                    for entry in state["tasks"].values()
                )
            else:
                occupied = len(sessions["sessions"])
            capacity = max(0, self.max_running - occupied)
            for task in manifest["tasks"]:
                entry = state["tasks"][task["id"]]
                if (
                    capacity < 1
                    or entry["status"] != "pending"
                    or task["feature"] not in approval["features"]
                ):
                    continue
                if any(state["tasks"][dep]["status"] != "integrated" for dep in task["depends_on"]):
                    continue
                entry.update(status="spawning", foundation_sha=state["foundation_sha"])
                self._save(state)  # Persist intent BEFORE the non-idempotent remote operation.
                body = {
                    "prompt": self._brief(manifest, task, state),
                    "auto_start": True,
                    "thread_name": f"{manifest['run_id']} · {task['id']}",
                }
                if self.channel_id is not None:
                    body["channel_id"] = self.channel_id
                try:
                    response = await self.api("POST", "/api/spawn", body)
                    thread_id = str(response["thread_id"])
                    if not thread_id.isdigit():
                        raise WorkflowError("Spawn returned an invalid thread ID")
                    entry.update(status="dispatched", thread_id=thread_id)
                except Exception as error:
                    entry.update(status="ambiguous", error_class=type(error).__name__)
                self._save(state)
                if entry["status"] == "dispatched":
                    self._bind(manifest, task, entry)
                capacity -= 1
            await self._handoff(manifest, state)
            return state

    async def watch(self, interval: float = 15) -> dict:
        """Yield the run at an integration frontier, ambiguity, or completion."""
        if interval < 1:
            raise WorkflowError("Watch interval must be at least one second")
        while True:
            state = await self.tick()
            statuses = {entry["status"] for entry in state["tasks"].values()}
            active = statuses & {"dispatched", "spawning"}
            if (
                "ambiguous" in statuses
                or ("verified" in statuses and not active)
                or statuses <= {"integrated"}
            ):
                return state
            if not (self.state_dir / "approval.json").exists():
                return state
            approved = json.loads((self.state_dir / "approval.json").read_text())["features"]
            manifest = json.loads(self.manifest_path.read_text())
            if all(
                state["tasks"][task["id"]]["status"] == "integrated"
                for task in manifest["tasks"]
                if task["feature"] in approved
            ):
                return state
            await asyncio.sleep(interval)


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument(
        "--api-url", default=os.environ.get("CCDB_API_URL", "http://127.0.0.1:8080")
    )
    parser.add_argument("--channel-id", type=int)
    parser.add_argument("--max-running", type=int, default=3)
    parser.add_argument("--worktree-root", type=Path)
    parser.add_argument(
        "--queue-ready",
        action="store_true",
        help="Queue approved ready tasks; Ebi retains its global execution limit.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    approve = sub.add_parser("approve")
    approve.add_argument("--authorization-ref", required=True)
    approve.add_argument("--feature", action="append", required=True)
    for name in ("tick", "status", "collect"):
        sub.add_parser(name)
    for name, field in (("integrate", "commit"), ("reconcile", "thread-id")):
        command = sub.add_parser(name)
        command.add_argument("--task", required=True)
        command.add_argument(f"--{field}", required=True)
    digest = sub.add_parser("plan-digest")
    digest.add_argument("--feature", required=True)
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    manifest = args.manifest if args.manifest.is_absolute() else args.repo / args.manifest
    coordinator = Coordinator(
        args.repo,
        manifest,
        args.state_dir,
        EbiAPI(args.api_url),
        channel_id=args.channel_id,
        max_running=args.max_running,
        worktree_root=args.worktree_root,
        queue_ready=args.queue_ready,
    )
    if args.command == "plan-digest":
        feature = next(
            f for f in json.loads(manifest.read_text())["features"] if f["id"] == args.feature
        )
        print(plan_digest(args.repo, feature["plan_files"]))
        return
    if args.command == "watch":
        output = await coordinator.watch(args.interval)
    elif args.command == "approve":
        output = await coordinator.approve(args.authorization_ref, args.feature)
    elif args.command == "integrate":
        output = await coordinator.integrate(args.task, args.commit)
    elif args.command == "reconcile":
        output = await coordinator.reconcile(args.task, args.thread_id)
    else:
        output = await getattr(coordinator, args.command)()
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except (WorkflowError, OSError, KeyError, ValueError) as error:
        raise SystemExit(f"Feature workflow stopped: {error}") from None
