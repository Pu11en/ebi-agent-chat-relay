"""The live check is profile-driven, read-only and honest about skips (task 5.3, step A4)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
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
# Spelled in two halves so this file never trips the rule it tests.
ALLOCATING_LISTING = "/api/" + "sessions"


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


@pytest.mark.parametrize("profile_name", ["none", "jester"])
def test_observation_never_lists_sessions_through_the_allocating_api(
    tmp_path: Path, profile_name: str
) -> None:
    paths: list[str] = []

    def read(_profile: object, path: str) -> dict[str, object]:
        paths.append(path)
        return {}

    profile = live_check.load_profile(
        {
            "CCDB_LIVE_CHECK_PROFILE": profile_name,
            "CCDB_LIVE_CHECK_DB": str(tmp_path / "none.db"),
            "JESTER_TURN_LOG": str(tmp_path / "turns.jsonl"),
        }
    )
    seen = live_check.observe(profile, read=read)
    # Open rows are all listed by the snapshot; ``limit`` only pages closed rows.
    assert paths == ["/api/health", "/api/jester/sessions"]
    assert not any(path.startswith(ALLOCATING_LISTING) for path in paths)
    assert seen.jester_turns is None  # no Jester unit: "since the unit started" is unknown


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(SystemExit):
        live_check.load_profile({"CCDB_LIVE_CHECK_PROFILE": "drews-machine"})


def snapshot(*rows: dict[str, object], **counts: object) -> dict[str, object]:
    """A /api/jester/sessions body as the running bot shapes it (step A2)."""
    body: dict[str, object] = {
        "sessions": list(rows),
        "open_count": sum(1 for r in rows if not r.get("closed")),
        "discord_active_threads": sum(1 for r in rows if not r.get("closed")),
        "generated_at": "2026-09-29T19:00:00+00:00",
    }
    body.update(counts)
    return body


def row(thread_id: str, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "thread_id": thread_id,
        "tag": "zoro",
        "name": f"[zoro] thread {thread_id}",
        "closed": False,
        "visible": True,
    }
    base.update(fields)
    return base


@pytest.mark.parametrize("profile_name", ["none", "jester"])
def test_an_open_session_hidden_in_discord_fails(profile_name: str) -> None:
    profile = live_check.load_profile({"CCDB_LIVE_CHECK_PROFILE": profile_name})
    hidden = snapshot(row("1"), row("2", name="[nami] ghost", tag="nami", visible=False))
    results = live_check.evaluate(profile, healthy(jester_sessions=hidden))
    mark, detail = next(
        (m, d) for m, n, d in results if n == "open sessions are visible in Discord"
    )
    assert mark == "FAIL"
    assert "open session not visible in Discord" in detail
    assert "[nami] ghost" in detail

    shown = snapshot(row("1"), row("2", closed=True, tag=None, visible=False))
    assert (
        marks(live_check.evaluate(profile, healthy(jester_sessions=shown)))[
            "open sessions are visible in Discord"
        ]
        == "PASS"
    )


def test_open_count_must_equal_discords_active_thread_count() -> None:
    profile = live_check.load_profile({})
    name = "open sessions match Discord's active threads"
    off = snapshot(row("1"), row("2"), row("3"), discord_active_threads=2)
    mark, detail = next(
        (m, d)
        for m, n, d in live_check.evaluate(profile, healthy(jester_sessions=off))
        if n == name
    )
    assert mark == "FAIL"
    assert "open_count 3" in detail and "discord_active_threads 2" in detail
    same = snapshot(row("1"), row("2"))
    assert marks(live_check.evaluate(profile, healthy(jester_sessions=same)))[name] == "PASS"


def test_snapshot_without_visibility_or_counts_is_skipped_not_passed() -> None:
    profile = live_check.load_profile({})
    older = {"sessions": [{"thread_id": "1", "tag": "zoro", "closed": False}]}
    results = marks(live_check.evaluate(profile, healthy(jester_sessions=older)))
    assert results["open sessions are visible in Discord"] == "SKIP"
    assert results["open sessions match Discord's active threads"] == "SKIP"
    unread = marks(live_check.evaluate(profile, healthy(jester_sessions=None)))
    assert unread["open sessions are visible in Discord"] == "SKIP"
    assert unread["open sessions match Discord's active threads"] == "SKIP"


def turn(
    heard: str, owner: str | None = None, at: str = "2026-09-29T19:00:00.000Z"
) -> dict[str, object]:
    record: dict[str, object] = {"at": at, "type": "turn", "heardText": heard}
    if owner is not None:
        record["ownerText"] = owner
    return record


def test_jester_must_not_open_with_a_backchannel() -> None:
    profile = live_check.load_profile({"CCDB_LIVE_CHECK_PROFILE": "jester"})
    name = "Jester never opens with a backchannel"
    bad = [turn("I'm here."), turn("Mm-hmm. Two threads are open.")]
    mark, detail = next(
        (m, d) for m, n, d in live_check.evaluate(profile, healthy(jester_turns=bad)) if n == name
    )
    assert mark == "FAIL"
    assert "Mm-hmm" in detail
    good = [
        turn("Two threads are open."),
        turn("Hmm is not how I start, but hmm mid-sentence is fine."),
    ]
    assert marks(live_check.evaluate(profile, healthy(jester_turns=good)))[name] == "FAIL"
    clean = [turn("Two threads are open."), turn("")]
    assert marks(live_check.evaluate(profile, healthy(jester_turns=clean)))[name] == "PASS"
    assert marks(live_check.evaluate(profile, healthy(jester_turns=None)))[name] == "SKIP"


def test_jester_must_not_answer_a_backchannel_only_owner_turn() -> None:
    profile = live_check.load_profile({"CCDB_LIVE_CHECK_PROFILE": "jester"})
    name = "Jester does not answer backchannel-only turns"
    answered = [turn("Sure, what would you like?", owner="Mm-hmm.")]
    mark, detail = next(
        (m, d)
        for m, n, d in live_check.evaluate(profile, healthy(jester_turns=answered))
        if n == name
    )
    assert mark == "FAIL"
    assert "Mm-hmm" in detail
    fine = [
        turn("Two threads are open.", owner="what's open?"),
        turn("Zoro is idle.", owner="uh huh, and zoro?"),
    ]
    assert marks(live_check.evaluate(profile, healthy(jester_turns=fine)))[name] == "PASS"
    # Today's log carries only Jester's words, so there is nothing to judge yet.
    no_owner_words = [turn("Two threads are open.")]
    assert marks(live_check.evaluate(profile, healthy(jester_turns=no_owner_words)))[name] == "SKIP"
    assert marks(live_check.evaluate(profile, healthy(jester_turns=None)))[name] == "SKIP"


def test_default_profile_has_no_jester_hygiene_lines() -> None:
    results = marks(
        live_check.evaluate(live_check.load_profile({}), healthy(jester_turns=[turn("Mm-hmm.")]))
    )
    assert not any("Jester" in name for name in results)


@pytest.mark.parametrize(
    ("text", "opens", "only"),
    [
        ("Mm-hmm. I'm here.", True, False),
        ("Mm hmm", True, True),
        ("Hmmm, let me look.", True, False),
        ("uh huh", True, True),
        ("Okay.", False, True),
        ("Yeah, sure.", False, True),
        ("okay let's go", False, False),
        ("I'm here. Mm-hmm.", False, False),
        ("", False, False),
        ("huh?", False, False),
    ],
)
def test_backchannel_helpers_follow_jesters_definitions(text: str, opens: bool, only: bool) -> None:
    assert live_check.opens_with_backchannel(text) is opens
    assert live_check.is_backchannel_only(text) is only


def test_turns_since_keeps_only_jester_turns_after_the_unit_started(tmp_path: Path) -> None:
    log = tmp_path / "turns.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps(turn("old", at="2026-09-29T11:59:00.000Z")),
                "not json at all",
                json.dumps({"at": "2026-09-29T12:00:30.000Z", "type": "barge_in", "heard": "x"}),
                json.dumps(turn("new", at="2026-09-29T12:01:00.000Z")),
            ]
        )
        + "\n"
    )
    # systemd reports the unit start in local time, without a zone; the log is UTC.
    started = datetime(2026, 9, 29, 12, 0, tzinfo=UTC).astimezone().replace(tzinfo=None)
    since = live_check.turns_since(log, started)
    assert since is not None
    assert [t["heardText"] for t in since] == ["new"]
    assert live_check.turns_since(log, None) is None
    assert live_check.turns_since(tmp_path / "missing.jsonl", started) is None


def test_profile_reads_the_turn_log_path_from_the_environment(tmp_path: Path) -> None:
    profile = live_check.load_profile({"JESTER_TURN_LOG": str(tmp_path / "t.jsonl")})
    assert profile.jester_turn_log == tmp_path / "t.jsonl"
    assert live_check.load_profile({}).jester_turn_log.name == "turns.jsonl"


def test_scripts_naming_the_allocating_listing_are_reported(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "ok.sh").write_text('curl "$API/api/jester/sessions"\n')
    (scripts / "bad.sh").write_text(f'x=1\ncurl "$API{ALLOCATING_LISTING}"\n')
    (scripts / "bad.py").write_text(f'URL = "{ALLOCATING_LISTING}?limit=5"\n')
    (scripts / "notes.md").write_text(f"{ALLOCATING_LISTING} is not a script\n")
    assert live_check.scripts_naming_session_listing(scripts) == ("bad.py:1", "bad.sh:2")
    assert live_check.scripts_naming_session_listing(tmp_path / "none") is None

    profile = live_check.load_profile({})
    name = "no script calls the tag-minting session listing"
    assert (
        marks(live_check.evaluate(profile, healthy(sessions_api_references=("bad.sh:2",))))[name]
        == "FAIL"
    )
    assert marks(live_check.evaluate(profile, healthy(sessions_api_references=())))[name] == "PASS"
    assert (
        marks(live_check.evaluate(profile, healthy(sessions_api_references=None)))[name] == "SKIP"
    )


def test_no_repo_script_calls_the_allocating_listing() -> None:
    assert live_check.scripts_naming_session_listing(SCRIPT.parent) == ()
