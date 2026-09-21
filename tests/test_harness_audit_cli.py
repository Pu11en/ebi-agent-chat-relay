"""CLI tests (task 4.3).

``python -m extensions.harness_audit.cli`` wires the read-only audit, the
redacted export/import, the cross-machine compare, and the explicit
quarantine plan / apply / verify / rollback steps.  Every command is run
in-process against fixture homes; the default ``audit`` must leave the
harness directories byte-identical, and no command may change a model,
reasoning-effort, routing or subscription setting.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from extensions.harness_audit import cli
from extensions.harness_audit.cli import main
from extensions.harness_audit.redaction import parse_bundle
from tests.harness_audit_fixtures import (
    FAKE_KEY,
    FAKE_SYSTEM_PROMPT,
    MANIFEST_TODAY,
    claude_invocation,
    codex_invocation,
    fake_home,
    write,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_subprocess_and_fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the audit CLI attempted to start a subprocess")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    monkeypatch.setattr(cli, "_today", lambda: MANIFEST_TODAY)


def tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def machine_fixture(tmp_path: Path, name: str) -> tuple[Path, list[str]]:
    """A fake home plus the argv fragment that points the CLI at it."""
    roots = fake_home(tmp_path / name, with_transcripts=True)
    assert roots.project_dir is not None
    declare = tmp_path / name / "declare.json"
    write(
        declare,
        json.dumps(
            {
                "links": {str(k): str(v) for k, v in roots.links.items()},
                "modes": {str(k): v for k, v in roots.modes.items()},
            }
        ),
    )
    claude_inv = write(
        tmp_path / name / "inv-claude.json", json.dumps(claude_invocation(roots.project_dir))
    )
    codex_inv = write(
        tmp_path / name / "inv-codex.json", json.dumps(codex_invocation(roots.project_dir))
    )
    args = [
        "--home",
        str(roots.home),
        "--project",
        str(roots.project_dir),
        "--declare",
        str(declare),
        "--invocation-claude",
        str(claude_inv),
        "--invocation-codex",
        str(codex_inv),
        "--salt",
        "shared",
        "--known-project",
        "ebi-agent-chat-relay",
    ]
    return roots.home, args


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


def test_audit_is_read_only_and_writes_only_to_the_output_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, args = machine_fixture(tmp_path, "drewai")
    output = tmp_path / "out"
    before = tree(tmp_path)
    code, out, err = run(["audit", "--machine", "drewai", "--output", str(output), *args], capsys)
    assert code == 0, err
    after = tree(tmp_path)
    assert {k: v for k, v in after.items() if not k.startswith("out/")} == before
    assert (output / "bundle-drewai.json").is_file()
    assert (output / "report.txt").is_file() and (output / "report.json").is_file()
    assert (output / "paths-drewai.json").is_file()
    assert "Coverage: PARTIAL (2/4 targets)" in out
    assert "[loaded]" in out and "[installed-only]" in out


def test_audit_bundle_is_redacted_and_names_both_targets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, args = machine_fixture(tmp_path, "drewai")
    output = tmp_path / "out"
    code, _, err = run(["audit", "--machine", "drewai", "--output", str(output), *args], capsys)
    assert code == 0, err
    text = (output / "bundle-drewai.json").read_text(encoding="utf-8")
    bundle = parse_bundle(text)
    assert {inv.target.key for inv in bundle.inventories} == {"drewai/claude", "drewai/codex"}
    assert bundle.findings and bundle.classifications
    for secret in (FAKE_KEY, FAKE_SYSTEM_PROMPT, "Always run the tests", "private prompt text"):
        assert secret not in text
        assert secret not in (output / "report.txt").read_text(encoding="utf-8")


def test_audit_one_harness_marks_the_other_as_a_gap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, args = machine_fixture(tmp_path, "drewai")
    output = tmp_path / "out"
    code, out, _ = run(
        ["audit", "--machine", "drewai", "--harness", "claude", "--output", str(output), *args],
        capsys,
    )
    assert code == 0
    assert "drewai/codex" in out and "coverage gap" in out.lower()
    bundle = parse_bundle((output / "bundle-drewai.json").read_text(encoding="utf-8"))
    assert [inv.target.key for inv in bundle.inventories] == ["drewai/claude"]
    assert [gap.target.key for gap in bundle.coverage_gaps] == ["drewai/codex"]


def test_audit_without_invocation_records_still_runs_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, args = machine_fixture(tmp_path, "drewai")
    trimmed: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("--invocation"):
            skip = True
            continue
        trimmed.append(arg)
    code, out, _ = run(
        ["audit", "--machine", "drewai", "--output", str(tmp_path / "out"), *trimmed], capsys
    )
    assert code == 0
    assert "no invocation record" in out


def test_audit_refuses_a_missing_home_instead_of_guessing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(
        [
            "audit",
            "--machine",
            "drewai",
            "--output",
            str(tmp_path / "out"),
            "--home",
            str(tmp_path / "nope"),
        ],
        capsys,
    )
    assert code != 0
    assert "not a directory" in err


# --------------------------------------------------------------------------- #
# export / import / compare
# --------------------------------------------------------------------------- #


def test_export_import_and_compare_produce_a_complete_four_target_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, drewai_args = machine_fixture(tmp_path, "drewai")
    _, imac_args = machine_fixture(tmp_path, "imac")
    drewai_out, imac_out = tmp_path / "drewai-out", tmp_path / "imac-out"
    assert main(["audit", "--machine", "drewai", "--output", str(drewai_out), *drewai_args]) == 0
    assert main(["audit", "--machine", "imac", "--output", str(imac_out), *imac_args]) == 0
    capsys.readouterr()

    exported = tmp_path / "imac-bundle.json"
    code, _, err = run(
        ["export", "--output", str(imac_out), "--machine", "imac", "--to", str(exported)], capsys
    )
    assert code == 0, err
    assert FAKE_KEY not in exported.read_text(encoding="utf-8")

    code, _, err = run(["import", "--output", str(drewai_out), "--from", str(exported)], capsys)
    assert code == 0, err
    assert (drewai_out / "bundle-imac.json").is_file()

    code, out, err = run(["compare", "--output", str(drewai_out)], capsys)
    assert code == 0, err
    assert "Coverage: COMPLETE (4/4 targets)" in out
    assert "== Parity ==" in out
    report = json.loads((drewai_out / "report.json").read_text(encoding="utf-8"))
    assert report["coverage"]["complete"] is True
    assert {e["target"] for e in report["inventories"]} == {
        "drewai/claude",
        "drewai/codex",
        "imac/claude",
        "imac/codex",
    }


def test_import_rejects_a_bundle_that_still_carries_a_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, args = machine_fixture(tmp_path, "imac")
    out_dir = tmp_path / "imac-out"
    assert main(["audit", "--machine", "imac", "--output", str(out_dir), *args]) == 0
    capsys.readouterr()
    text = (out_dir / "bundle-imac.json").read_text(encoding="utf-8")
    payload = json.loads(text)
    payload["inventories"][0]["items"][0]["label"] = f"api_key={FAKE_KEY}"
    tampered = write(tmp_path / "tampered.json", json.dumps(payload))
    code, _, err = run(
        ["import", "--output", str(tmp_path / "dest"), "--from", str(tampered)], capsys
    )
    assert code != 0
    assert "refusing" in err
    assert not (tmp_path / "dest" / "bundle-imac.json").exists()


def test_compare_with_an_overlay_turns_declared_differences_into_exceptions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, drewai_args = machine_fixture(tmp_path, "drewai")
    home, imac_args = machine_fixture(tmp_path, "imac")
    write(home / ".codex" / "config.toml", 'model = "gpt-5-mini"\n')
    out_dir = tmp_path / "out"
    assert main(["audit", "--machine", "drewai", "--output", str(out_dir), *drewai_args]) == 0
    assert main(["audit", "--machine", "imac", "--output", str(out_dir), *imac_args]) == 0
    overlay = write(
        tmp_path / "imac-overlay.json",
        json.dumps(
            {
                "machine": "imac",
                "exceptions": [
                    {
                        "exception_id": "imac-subscription",
                        "kind": "subscription",
                        "description": "smaller subscription",
                        "preserved_outcome": "same rules load",
                        "covers": ["setting:model", "setting:model_reasoning_effort"],
                    }
                ],
            }
        ),
    )
    capsys.readouterr()
    code, out, err = run(["compare", "--output", str(out_dir), "--overlay", str(overlay)], capsys)
    assert code == 0, err
    assert "imac-subscription" in out
    assert "[exception] setting:model" in out


# --------------------------------------------------------------------------- #
# quarantine plan / apply / verify / rollback
# --------------------------------------------------------------------------- #


def test_quarantine_plan_apply_verify_rollback_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, args = machine_fixture(tmp_path, "drewai")
    out_dir = tmp_path / "out"
    qdir = tmp_path / "quarantine"
    assert main(["audit", "--machine", "drewai", "--output", str(out_dir), *args]) == 0
    baseline = tree(home)
    capsys.readouterr()

    code, out, err = run(
        [
            "quarantine",
            "plan",
            "--output",
            str(out_dir),
            "--machine",
            "drewai",
            "--quarantine-dir",
            str(qdir),
        ],
        capsys,
    )
    assert code == 0, err
    assert tree(home) == baseline, "planning writes nothing into the home"
    manifests = sorted(out_dir.glob("quarantine-*.json"))
    assert len(manifests) == 1
    manifest_path = manifests[0]
    assert "would quarantine" in out

    code, out, err = run(["quarantine", "apply", "--manifest", str(manifest_path)], capsys)
    assert code == 0, err
    applied = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert applied["applied_at"] and applied["entries"]
    for entry in applied["entries"]:
        assert not Path(entry["original_path"]).exists()
        assert Path(entry["disabled_location"]).is_file()

    code, out, err = run(
        [
            "verify",
            "--output",
            str(out_dir),
            "--machine",
            "drewai",
            "--manifest",
            str(manifest_path),
            *args,
        ],
        capsys,
    )
    assert code == 0, err
    verification = json.loads(next(out_dir.glob("verify-*.json")).read_text(encoding="utf-8"))
    assert {t["target"] for t in verification["targets"]} == {"drewai/claude", "drewai/codex"}
    assert all(v["eligible"] is False for v in verification["removal"])
    assert "not classified Remove" in out or "not verified" in out or "failed" in out

    code, out, err = run(["rollback", "--manifest", str(manifest_path)], capsys)
    assert code == 0, err
    assert tree(home) == baseline
    rolled = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert rolled["rolled_back_at"]


def test_quarantine_apply_aborts_on_a_hash_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, args = machine_fixture(tmp_path, "drewai")
    out_dir = tmp_path / "out"
    assert main(["audit", "--machine", "drewai", "--output", str(out_dir), *args]) == 0
    assert (
        main(
            [
                "quarantine",
                "plan",
                "--output",
                str(out_dir),
                "--machine",
                "drewai",
                "--quarantine-dir",
                str(tmp_path / "q"),
            ]
        )
        == 0
    )
    manifest_path = next(out_dir.glob("quarantine-*.json"))
    entry = json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0]
    Path(entry["original_path"]).write_text("changed after planning\n", encoding="utf-8")
    baseline = tree(home)
    capsys.readouterr()
    code, _, err = run(["quarantine", "apply", "--manifest", str(manifest_path)], capsys)
    assert code != 0
    assert "hash" in err
    assert tree(home) == baseline


# --------------------------------------------------------------------------- #
# What the CLI must never do
# --------------------------------------------------------------------------- #


def test_no_command_exposes_a_model_effort_routing_or_subscription_option() -> None:
    parser = cli.build_parser()
    forbidden = ("model", "effort", "reasoning", "provider", "routing", "subscription", "backend")
    for action in cli.iter_actions(parser):
        for option in action.option_strings:
            assert not any(word in option.lower() for word in forbidden), option


def test_every_command_leaves_model_and_effort_settings_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, args = machine_fixture(tmp_path, "drewai")
    out_dir = tmp_path / "out"
    settings = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (home / ".codex" / "config.toml", home / ".claude" / "settings.json")
    }
    assert main(["audit", "--machine", "drewai", "--output", str(out_dir), *args]) == 0
    assert main(["compare", "--output", str(out_dir)]) == 0
    assert (
        main(
            [
                "quarantine",
                "plan",
                "--output",
                str(out_dir),
                "--machine",
                "drewai",
                "--quarantine-dir",
                str(tmp_path / "q"),
            ]
        )
        == 0
    )
    manifest_path = next(out_dir.glob("quarantine-*.json"))
    assert main(["quarantine", "apply", "--manifest", str(manifest_path)]) == 0
    assert (
        main(
            [
                "verify",
                "--output",
                str(out_dir),
                "--machine",
                "drewai",
                "--manifest",
                str(manifest_path),
                *args,
            ]
        )
        == 0
    )
    assert main(["rollback", "--manifest", str(manifest_path)]) == 0
    capsys.readouterr()
    for path, digest in settings.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, path


def test_package_exposes_the_public_api_and_a_module_entry_point() -> None:
    import extensions.harness_audit as package

    for name in (
        "discover",
        "collect_claude",
        "collect_codex",
        "run_rules",
        "classify",
        "check_parity",
    ):
        assert hasattr(package, name), name
    assert callable(cli.main)
    assert 'if __name__ == "__main__"' in Path(cli.__file__).read_text(encoding="utf-8")
