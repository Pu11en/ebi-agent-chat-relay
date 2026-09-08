"""Integration tests for the systemd pre-start boundary."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pre_start_finds_uv_in_user_local_bin_with_systemd_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in ("pre-start.sh", "deploy-checkout.sh"):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, scripts / name)

    subprocess.run(["git", "init", "-q", "-b", "test", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True
    )
    (repo / "tracked").write_text("fixture\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)

    site_packages = repo / ".venv" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)

    home = tmp_path / "home"
    uv = home / ".local" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$HOME/uv-call"\n')
    uv.chmod(0o755)

    env = os.environ.copy()
    env.update({"HOME": str(home), "PATH": "/usr/bin:/bin"})
    env.pop("CCDB_UV_BIN", None)
    result = subprocess.run(
        ["bash", str(scripts / "pre-start.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (home / "uv-call").read_text().strip() == "sync"
