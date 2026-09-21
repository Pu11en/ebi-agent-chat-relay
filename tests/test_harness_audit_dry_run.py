"""Integrated dry run over the checked-in fixtures (task 5.1).

This follows ``docs/harness-configuration-audit.md`` step by step against the
sanitized DrewAI and iMac trees under ``tests/fixtures/harness_audit/`` and
proves two things the document promises: the run produces no filesystem
change outside its output directory, and the combined report covers all four
targets with the iMac's declared differences shown as exceptions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from extensions.harness_audit import cli
from extensions.harness_audit.cli import main
from tests.harness_audit_fixtures import (
    FAKE_KEY,
    FAKE_SYSTEM_PROMPT,
    FIXTURE_ROOT,
    HOME_TOKEN,
    MANIFEST_TODAY,
    materialize_fixture,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    monkeypatch.setattr(cli, "_today", lambda: MANIFEST_TODAY)


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_checked_in_fixtures_carry_no_real_path_and_no_real_secret_shape() -> None:
    for path in FIXTURE_ROOT.rglob("*"):
        if not path.is_file() or path.name == "generate.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "/Users/" not in text and "/home/" not in text and "C:/" not in text, path
        assert "sk-ant-api03-FAKE" in text or "sk-ant-" not in text, path
    assert (FIXTURE_ROOT / "drewai" / "declare.json").read_text(encoding="utf-8").count(HOME_TOKEN)


def test_documented_dry_run_changes_nothing_outside_the_output_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drewai_home, drewai_args = materialize_fixture("drewai", tmp_path)
    imac_home, imac_args = materialize_fixture("imac", tmp_path)
    output = tmp_path / "harness-audit"
    before = tree(tmp_path)

    # Step 1 — audit each machine (read-only).
    assert main(["audit", "--machine", "drewai", "--output", str(output), *drewai_args]) == 0
    imac_output = tmp_path / "harness-audit-imac"
    assert main(["audit", "--machine", "imac", "--output", str(imac_output), *imac_args]) == 0
    # Step 2 — export the iMac bundle, import it beside the DrewAI one.
    exported = tmp_path / "imac-bundle.json"
    assert (
        main(["export", "--output", str(imac_output), "--machine", "imac", "--to", str(exported)])
        == 0
    )
    assert main(["import", "--output", str(output), "--from", str(exported)]) == 0
    # Step 3 — compare with the iMac overlay.
    overlay = tmp_path / "imac" / "overlay.json"
    assert main(["compare", "--output", str(output), "--overlay", str(overlay)]) == 0
    # Step 4 — plan a quarantine (still nothing moves).
    assert (
        main(
            [
                "quarantine",
                "plan",
                "--output",
                str(output),
                "--machine",
                "drewai",
                "--quarantine-dir",
                str(tmp_path / "quarantine"),
            ]
        )
        == 0
    )
    printed = capsys.readouterr().out

    after = tree(tmp_path)
    outside = {
        k: v
        for k, v in after.items()
        if not (
            k.startswith("harness-audit/")
            or k.startswith("harness-audit-imac/")
            or k == "imac-bundle.json"
        )
    }
    assert outside == before, "the dry run touched a file outside its output directories"
    assert not (tmp_path / "quarantine").exists()
    assert tree(drewai_home) == {
        k[len("drewai/home/") :]: v for k, v in before.items() if k.startswith("drewai/home/")
    }
    assert tree(imac_home) == {
        k[len("imac/home/") :]: v for k, v in before.items() if k.startswith("imac/home/")
    }

    report_text = (output / "report.txt").read_text(encoding="utf-8")
    assert "Coverage: COMPLETE (4/4 targets)" in report_text
    assert "imac-subscription" in report_text and "imac-no-github-mcp" in report_text
    assert "[exception] setting:model" in report_text
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert {e["target"] for e in report["inventories"]} == {
        "drewai/claude",
        "drewai/codex",
        "imac/claude",
        "imac/codex",
    }
    for secret in (FAKE_KEY, FAKE_SYSTEM_PROMPT, "Always run the tests", "private prompt text"):
        assert secret not in report_text
        assert secret not in (output / "bundle-drewai.json").read_text(encoding="utf-8")
        assert secret not in (output / "bundle-imac.json").read_text(encoding="utf-8")
    assert "would quarantine" in printed
