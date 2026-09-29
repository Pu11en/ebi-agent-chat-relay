"""The live check is profile-driven, read-only and honest about skips (task 5.3)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from claude_discord.consistency_check import ConsistencyReport

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "live-check.py"
spec = importlib.util.spec_from_file_location("live_check", SCRIPT)
assert spec is not None and spec.loader is not None
live_check = importlib.util.module_from_spec(spec)
sys.modules["live_check"] = live_check
spec.loader.exec_module(live_check)

COMMIT = "a" * 40


def healthy(**overrides: object) -> object:
    report = ConsistencyReport(available=True, coverage="all 3 session rows")
    report.untagged_workers = [3]  # legitimately untagged internal worker
    values = {
        "health": {"status": "ok", "runtime": {"commit": COMMIT}},
        "disk_commit": COMMIT,
        "report": report,
        "units": {},
    }
    values.update(overrides)
    return live_check.Observed(**values)


def marks(results: list[tuple[str, str, str]]) -> dict[str, str]:
    return {name: mark for mark, name, _detail in results}


def test_default_profile_has_no_personal_units_and_skips_unconfigured_checks() -> None:
    profile = live_check.load_profile({})
    assert (profile.name, profile.bot_unit, profile.jester_unit, profile.voice_unit) == (
        "none",
        None,
        None,
        None,
    )
    results = marks(live_check.evaluate(profile, healthy()))
    assert results["bot service is running"] == "SKIP"
    assert results["nothing is failing over and over"] == "SKIP"
    assert results["internal workers hold no tags"] == "PASS"
    assert "FAIL" not in results.values()
    assert not any("voice" in name or "Jester" in name for name in results)


def test_jester_profile_checks_its_unit_and_read_only_snapshot() -> None:
    profile = live_check.load_profile(
        {"CCDB_LIVE_CHECK_PROFILE": "jester", "JESTER_UNIT": "jester.service"}
    )
    seen = healthy(units={"jester.service": "active"}, jester_sessions={"sessions": []})
    results = marks(live_check.evaluate(profile, seen))
    assert results["Jester service is running"] == "PASS"
    assert results["Jester's read-only session snapshot answers"] == "PASS"
    assert not any("voice decision" in name for name in results)


def test_legacy_voice_profile_keeps_its_drift_and_parser_checks() -> None:
    profile = live_check.load_profile(
        {
            "CCDB_LIVE_CHECK_PROFILE": "legacy-voice",
            "CCDB_VOICE_UNIT": "voice.service",
            "CCDB_VOICE_RUNTIME": "/runtime",
        }
    )
    probe = {"expected": "zoro", "exact": "zoro", "misheard": "zoro", "tidy": True, "notTidy": True}
    seen = healthy(
        units={"voice.service": "active"}, voice_digests=("abc", "def"), voice_probe=probe
    )
    results = marks(live_check.evaluate(profile, seen))
    assert results["deployed voice code is the committed voice code"] == "FAIL"
    assert results["a misheard tag still reaches its own thread"] == "PASS"


def test_running_revision_drift_and_unknown_identity() -> None:
    profile = live_check.load_profile({})
    drifted = marks(live_check.evaluate(profile, healthy(disk_commit="b" * 40)))
    assert drifted["running code is the checked-out code"] == "FAIL"
    unknown = healthy(health={"status": "ok", "runtime": {"commit": "unknown"}})
    assert marks(live_check.evaluate(profile, unknown))["running code is the checked-out code"] == (
        "SKIP"
    )


def test_unreadable_store_is_skipped_not_passed() -> None:
    report = ConsistencyReport(available=False, coverage="unavailable: no database")
    results = marks(live_check.evaluate(live_check.load_profile({}), healthy(report=report)))
    assert results["session store is consistent"] == "SKIP"


def test_observation_never_lists_sessions_through_the_allocating_api(tmp_path: Path) -> None:
    paths: list[str] = []

    def read(_profile: object, path: str) -> dict[str, object]:
        paths.append(path)
        return {}

    profile = live_check.load_profile(
        {"CCDB_LIVE_CHECK_PROFILE": "jester", "CCDB_LIVE_CHECK_DB": str(tmp_path / "none.db")}
    )
    live_check.observe(profile, read=read)
    assert paths == ["/api/health", "/api/jester/sessions?limit=100"]
    assert not any(path.startswith("/api/sessions") for path in paths)


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(SystemExit):
        live_check.load_profile({"CCDB_LIVE_CHECK_PROFILE": "drews-machine"})
