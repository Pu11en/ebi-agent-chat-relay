#!/usr/bin/env python3
"""Does the running system actually work? Ask it, don't ask the tests.

Every bug found on 2026-09-26 was found by Drew hitting it. 6,029 unit tests were
green the whole time. They were green because they check the code sitting still,
and every one of those bugs lived somewhere a unit test cannot reach:

* the deployed artefact differed from the committed source (the speech worker)
* a refactor dropped a field the *other* process reads (the tag mishearings)
* Discord's real limit is metered differently from how the code paced itself
* live state had accumulated (25 of 26 tags held by closed sessions)

So this talks to the running bot, reads the real database, and prints one line
per check. It is strictly read-only: it never calls ``/api/sessions`` (listing
there can allocate tags), opens the database read-only, and starts no model.

What to check comes from the environment, so no machine's paths are built in:

  CCDB_LIVE_CHECK_PROFILE  none (default) | jester | legacy-voice
  CCDB_API_URL             control API, default http://127.0.0.1:9876
  CCDB_LIVE_CHECK_DB       session database, default <repo>/data/sessions.db
  CCDB_BOT_UNIT            systemd --user unit of the bot (optional)
  CCDB_BOT_LOG             bot log file to scan for repeated errors (optional)
  JESTER_UNIT              jester profile: Jester's systemd --user unit
  CCDB_VOICE_UNIT          legacy-voice profile: the older voice service unit
  CCDB_VOICE_RUNTIME       legacy-voice profile: its deployed extension directory

A check that is not configured, or whose source cannot be read, is SKIP — never
PASS. Exit code is 1 if anything failed, so it can gate a deploy.

Usage:  uv run python scripts/live-check.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from claude_discord.consistency_check import (  # noqa: E402
    ConsistencyReport,
    inspect_database,
    read_only_uri,
)
from claude_discord.voice_labels import aliases_for  # noqa: E402

PROFILES = ("none", "jester", "legacy-voice")
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass(frozen=True)
class Profile:
    name: str
    api: str
    db: Path
    secret: str = ""
    bot_unit: str | None = None
    bot_log: Path | None = None
    jester_unit: str | None = None
    voice_unit: str | None = None
    voice_runtime: Path | None = None


def load_profile(env: Mapping[str, str]) -> Profile:
    name = env.get("CCDB_LIVE_CHECK_PROFILE", "none").strip() or "none"
    if name not in PROFILES:
        raise SystemExit(f"CCDB_LIVE_CHECK_PROFILE must be one of {PROFILES}, not {name!r}")

    def path(key: str) -> Path | None:
        return Path(env[key]) if env.get(key) else None

    return Profile(
        name=name,
        api=env.get("CCDB_API_URL", "http://127.0.0.1:9876"),
        db=path("CCDB_LIVE_CHECK_DB") or REPO / "data" / "sessions.db",
        secret=env.get("CCDB_API_SECRET", ""),
        bot_unit=env.get("CCDB_BOT_UNIT") or None,
        bot_log=path("CCDB_BOT_LOG"),
        jester_unit=env.get("JESTER_UNIT") or None,
        voice_unit=env.get("CCDB_VOICE_UNIT") or None,
        voice_runtime=path("CCDB_VOICE_RUNTIME"),
    )


@dataclass(frozen=True)
class Observed:
    """Everything read from the live system; ``None`` means it could not be read."""

    health: dict[str, object] | None
    disk_commit: str | None
    report: ConsistencyReport
    units: Mapping[str, str | None]
    jester_sessions: object | None = None
    voice_digests: tuple[str, str] | None = None
    voice_probe: dict[str, object] | None = None
    repeated_errors: dict[str, int] | None = None
    edit_rejections: int | None = None


Result = tuple[str, str, str]


def evaluate(profile: Profile, seen: Observed) -> list[Result]:
    out: list[Result] = []

    def check(ok: bool | None, name: str, detail: str = "") -> None:
        out.append((SKIP if ok is None else PASS if ok else FAIL, name, detail))

    def unit(name: str | None, label: str) -> None:
        if name is None:
            check(None, f"{label} service is running", "unit not configured")
        else:
            state = seen.units.get(name)
            check(
                None if state is None else state == "active",
                f"{label} service is running",
                f"{name} is {state}",
            )

    unit(profile.bot_unit, "bot")
    check(seen.health is not None, "bot answers its API")
    runtime = (seen.health or {}).get("runtime")
    running = runtime.get("commit") if isinstance(runtime, dict) else None
    if not running or running == "unknown" or not seen.disk_commit:
        check(None, "running code is the checked-out code", "running or disk revision unknown")
    else:
        check(
            running == seen.disk_commit,
            "running code is the checked-out code",
            f"running {running} vs disk {seen.disk_commit}",
        )

    report = seen.report
    if not report.available:
        check(None, "session store is consistent", report.coverage)
    else:
        check(
            not report.closed_holding_tags,
            "no closed session is holding a tag",
            f"{report.closed_holding_tags}",
        )
        check(
            not report.duplicate_tags,
            "no two threads answer to the same tag",
            f"{report.duplicate_tags}",
        )
        check(
            not report.workers_holding_tags,
            "internal workers hold no tags",
            f"{report.workers_holding_tags}",
        )
        check(
            not (report.untagged_user_sessions and report.free_tags),
            "open user sessions are tagged while tags are free",
            f"untagged {report.untagged_user_sessions}, {report.free_tags} free",
        )

    if profile.name == "jester":
        unit(profile.jester_unit, "Jester")
        check(isinstance(seen.jester_sessions, dict), "Jester's read-only session snapshot answers")
    if profile.name == "legacy-voice":
        unit(profile.voice_unit, "voice")
        if seen.voice_digests is None:
            check(None, "deployed voice code is the committed voice code", "runtime not configured")
        else:
            repo_digest, live_digest = seen.voice_digests
            check(
                repo_digest == live_digest,
                "deployed voice code is the committed voice code",
                f"repo {repo_digest} vs live {live_digest}",
            )
        probe = seen.voice_probe
        if probe is None:
            check(None, "the voice decision code runs", "no tagged session to probe")
        elif "error" in probe:
            check(False, "the voice decision code runs", str(probe["error"])[:200])
        else:
            check(
                probe.get("exact") == probe.get("expected"),
                "saying a tag reaches its own thread",
                f"{probe.get('exact')} != {probe.get('expected')}",
            )
            check(
                probe.get("misheard") in (probe.get("expected"), "<none>"),
                "a misheard tag still reaches its own thread",
                f"{probe.get('misheard')} != {probe.get('expected')}",
            )
            check(bool(probe.get("tidy")), '"close everything I am not using" is understood')
            check(bool(probe.get("notTidy")), "talking about closing does not close anything")

    if seen.repeated_errors is None:
        check(None, "nothing is failing over and over", "bot log not configured")
    else:
        check(
            not seen.repeated_errors,
            "nothing is failing over and over",
            "; ".join(f"{v}x{k}" for k, v in seen.repeated_errors.items()),
        )
    if seen.edit_rejections is not None:
        check(
            seen.edit_rejections == 0,
            "Discord is not rejecting message updates",
            f"{seen.edit_rejections} rejections",
        )
    return out


# --------------------------------------------------------------------------
# Reading the live system. Every call here is a read.
# --------------------------------------------------------------------------


def run(*args: str) -> str | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return None


def api(profile: Profile, path: str) -> object | None:
    req = urllib.request.Request(f"{profile.api}{path}")
    if profile.secret:
        req.add_header("Authorization", f"Bearer {profile.secret}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def tree_digest(root: Path) -> str:
    """One hash over a directory's source, so drift is a single comparison."""
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or "node_modules" in path.parts:
            continue
        h.update(str(path.relative_to(root)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def unit_started_at(unit: str) -> datetime | None:
    raw = run("systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestamp") or ""
    value = raw.partition("=")[2].strip()
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def log_since(log: Path, started: datetime | None) -> list[str] | None:
    """Lines logged after *started*, or None when the log cannot be read.

    Tracks the last timestamp seen rather than filtering line by line: a
    traceback's continuation lines carry no timestamp, and comparing them as
    strings lets nineteen days of old ones through — which is exactly the mistake
    that produced a false alarm about 131 tracebacks.
    """
    if started is None or not log.exists():
        return None
    stamp = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    cutoff = started.strftime("%Y-%m-%d %H:%M:%S")
    out: list[str] = []
    current = ""
    with log.open(errors="replace") as handle:
        for line in handle:
            m = stamp.match(line)
            if m:
                current = m.group(1)
            if current >= cutoff:
                out.append(line.rstrip("\n"))
    return out


def voice_probe(report_db: Path) -> dict[str, object] | None:
    """Drive the older voice integration's parser with one real stored tag."""
    import sqlite3

    if not report_db.is_file():
        return None
    try:
        with closing(sqlite3.connect(read_only_uri(report_db), uri=True)) as conn:
            row = conn.execute(
                "SELECT s.value FROM settings s JOIN sessions x ON s.key = 'voice_label:' || "
                "x.thread_id WHERE x.lifecycle_state != 'closed' LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    tag = row[0]
    tags = [{"label": tag, "aliases": list(aliases_for(tag))}]
    command = REPO / "extensions/voice_transcripts/src/control/command.mjs"
    probe = f"""
import {{ parseByTag, parseTidyUp }} from "{command}";
const tags = {json.dumps(tags)};
const out = {{ expected: tags[0].label }};
out.exact = parseByTag(`${{tags[0].label}} run make verify`, tags)?.target ?? null;
const alias = tags[0].aliases[0];
out.misheard = alias ? (parseByTag(`${{alias}} run make verify`, tags)?.target ?? null) : "<none>";
out.tidy = !!parseTidyUp("close everything I'm not using");
out.notTidy = !parseTidyUp("I should close everything I'm not using at some point");
console.log(JSON.stringify(out));
"""
    raw = run("node", "--input-type=module", "-e", probe)
    try:
        return json.loads(raw or "")
    except ValueError:
        return {"error": raw or "node did not run"}


def observe(profile: Profile, read: Callable[[Profile, str], object | None] = api) -> Observed:
    health = read(profile, "/api/health")
    units = {
        name: run("systemctl", "--user", "is-active", name)
        for name in (profile.bot_unit, profile.jester_unit, profile.voice_unit)
        if name
    }
    repeated: dict[str, int] | None = None
    rejections: int | None = None
    if profile.bot_log is not None and profile.bot_unit is not None:
        lines = log_since(profile.bot_log, unit_started_at(profile.bot_unit))
        if lines is not None:
            errors: dict[str, int] = {}
            for line in lines:
                if "[ERROR]" in line:
                    key = re.sub(r"\d{15,}", "ID", line.split("[ERROR]")[1])[:90]
                    errors[key] = errors.get(key, 0) + 1
            repeated = {k: v for k, v in errors.items() if v >= 3}
            rejections = sum(1 for x in lines if "429" in x and "PATCH" in x and "messages" in x)
    digests = None
    if profile.name == "legacy-voice" and profile.voice_runtime is not None:
        live = profile.voice_runtime / "src"
        digests = (
            tree_digest(REPO / "extensions/voice_transcripts/src"),
            tree_digest(live) if live.exists() else "<missing>",
        )
    return Observed(
        health=health if isinstance(health, dict) else None,
        disk_commit=run("git", "-C", str(REPO), "rev-parse", "HEAD"),
        report=inspect_database(profile.db),
        units=units,
        jester_sessions=(
            read(profile, "/api/jester/sessions?limit=100") if profile.name == "jester" else None
        ),
        voice_digests=digests,
        voice_probe=voice_probe(profile.db) if profile.name == "legacy-voice" else None,
        repeated_errors=repeated,
        edit_rejections=rejections,
    )


def main() -> int:
    profile = load_profile(os.environ)
    results = evaluate(profile, observe(profile))
    width = max(len(name) for _mark, name, _detail in results)
    for mark, name, detail in results:
        # Detail only when a line is not a pass: a passing line explaining itself
        # is noise, and noise is what stopped anyone reading the log.
        print(f"[{mark}] {name.ljust(width)}  {'' if mark == PASS else detail}".rstrip())
    failed = sum(mark == FAIL for mark, _n, _d in results)
    skipped = sum(mark == SKIP for mark, _n, _d in results)
    print(
        f"\nprofile {profile.name}: {len(results) - failed - skipped} passed, "
        f"{failed} failed, {skipped} skipped (not configured or unreadable)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
