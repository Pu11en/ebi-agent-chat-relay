"""Run the offline Go Work regression suite, including tests added by this build."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tests = set()
    for pattern in (
        "test_task_loop*.py",
        "test_gowork*.py",
        "test_loop_store.py",
        "test_work_copy.py",
    ):
        tests.update(root.joinpath("tests").glob(pattern))
    added = root / "tests" / "gowork_upgrade"
    if added.is_dir():
        tests.add(added)
    if not tests:
        print("No Go Work tests found", file=sys.stderr)
        return 1
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", *map(str, sorted(tests)), "-q"],
            cwd=root,
            timeout=300,  # real git worktrees in the cog tests are slow on Windows
            check=False,
        ).returncode
    except subprocess.TimeoutExpired:
        print("Go Work checks exceeded 300 seconds", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
