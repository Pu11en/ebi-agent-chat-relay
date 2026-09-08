"""Regression tests for the self-healing service health check."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "health-check.sh"


def _command(path: Path, name: str, body: str) -> None:
    command = path / name
    command.write_text(f"#!/bin/bash\n{body}\n")
    command.chmod(0o755)


def _run(tmp_path: Path, response: str, curl_status: int = 0) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "systemctl-calls"
    _command(bin_dir, "curl", f"printf '%s' '{response}'\nexit {curl_status}")
    _command(bin_dir, "systemctl", f'printf "%s\\n" "$*" >> "{calls}"')
    return subprocess.run(
        [str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CCDB_HEALTH_RETRIES": "1",
            "CCDB_HEALTH_RETRY_DELAY": "0",
            "CCDB_SERVICE": "ebi-agent-chat-relay.service",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_healthy_service_is_left_running(tmp_path: Path) -> None:
    result = _run(tmp_path, '{"status":"ok"}')

    assert result.returncode == 0
    assert not (tmp_path / "systemctl-calls").exists()


def test_unhealthy_or_unreachable_service_is_restarted(tmp_path: Path) -> None:
    result = _run(tmp_path, '{"status":"error"}', curl_status=7)

    assert result.returncode == 0
    assert (tmp_path / "systemctl-calls").read_text() == (
        "--user restart ebi-agent-chat-relay.service\n"
    )
    assert "restarting ebi-agent-chat-relay.service" in result.stderr


def test_non_health_json_is_treated_as_unhealthy(tmp_path: Path) -> None:
    result = _run(tmp_path, '{"unrelated":true}')

    assert result.returncode == 0
    assert (tmp_path / "systemctl-calls").exists()


def test_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & 0o111
