#!/usr/bin/env python3
"""Does the running system actually work? Ask it, don't ask the tests.

Every bug found on 2026-09-26 was found by Drew hitting it. 6,029 unit tests were
green the whole time. They were green because they check the code sitting still,
and every one of those bugs lived somewhere a unit test cannot reach:

* the deployed artefact differed from the committed source (the speech worker)
* a refactor dropped a field the *other* process reads (the tag mishearings)
* Discord's real limit is metered differently from how the code paced itself
* live state had accumulated (25 of 26 tags held by closed sessions)

So this talks to the running bot, reads the real database, drives the real voice
decision code, and prints one line per check. It is the thing that is supposed to
notice before he does.

Usage:  uv run python scripts/live-check.py
Exit code is 1 if anything failed, so it can gate a deploy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = os.environ.get("CCDB_API_URL", "http://127.0.0.1:9876")
SECRET = os.environ.get("CCDB_API_SECRET", "")
BOT_UNIT = "ebi-agent-chat-relay.service"
VOICE_UNIT = "drew-ai-voice-transcripts.service"
VOICE_RUNTIME = Path("/home/drewp/main-projects/drew-ai-voice-runtime/extensions/voice_transcripts")
BOT_LOG = Path("/home/drewp/.local/state/ebi-agent-chat-relay/discord-bot.log")

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((ok, name, detail))
    return ok


def run(*args: str) -> str:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception as exc:
        return f"<failed: {exc}>"


def api(path: str) -> object | None:
    req = urllib.request.Request(f"{API}{path}")
    if SECRET:
        req.add_header("Authorization", f"Bearer {SECRET}")
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


def bot_started_at() -> datetime | None:
    raw = run("systemctl", "--user", "show", BOT_UNIT, "-p", "ActiveEnterTimestamp")
    value = raw.partition("=")[2].strip()
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def log_since(started: datetime | None) -> list[str]:
    """Lines logged after *started*.

    Tracks the last timestamp seen rather than filtering line by line: a
    traceback's continuation lines carry no timestamp, and comparing them as
    strings lets nineteen days of old ones through — which is exactly the mistake
    that produced a false alarm about 131 tracebacks.
    """
    if started is None or not BOT_LOG.exists():
        return []
    stamp = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    cutoff = started.strftime("%Y-%m-%d %H:%M:%S")
    out: list[str] = []
    current = ""
    with BOT_LOG.open(errors="replace") as handle:
        for line in handle:
            m = stamp.match(line)
            if m:
                current = m.group(1)
            if current >= cutoff:
                out.append(line.rstrip("\n"))
    return out


# --------------------------------------------------------------------------
# Is it even up, and is it running what we wrote?
# --------------------------------------------------------------------------

check(
    run("systemctl", "--user", "is-active", BOT_UNIT) == "active",
    "bot service is running",
)
check(
    run("systemctl", "--user", "is-active", VOICE_UNIT) == "active",
    "voice service is running",
)
check(api("/api/health") is not None, "bot answers its API")

started = bot_started_at()
newest_commit = run("git", "-C", str(REPO), "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S")
drifted = bool(started and newest_commit and newest_commit > started.strftime("%Y-%m-%d %H:%M:%S"))
check(
    not drifted,
    "running code is the committed code",
    f"newest commit {newest_commit} is after the bot started {started}" if drifted else "",
)

repo_voice = tree_digest(REPO / "extensions/voice_transcripts/src")
live_voice = tree_digest(VOICE_RUNTIME / "src") if VOICE_RUNTIME.exists() else "<missing>"
check(
    repo_voice == live_voice,
    "deployed voice code is the committed voice code",
    f"repo {repo_voice} vs live {live_voice}" if repo_voice != live_voice else "",
)

# --------------------------------------------------------------------------
# The spoken tags — the thing that broke all day
# --------------------------------------------------------------------------

payload = api("/api/sessions?limit=100")
sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
live = [s for s in sessions if not s.get("closed")]
tagged = [s for s in live if s.get("voice_label")]

check(bool(sessions), "the session list can be read")

untagged = [s for s in live if not s.get("voice_label")][:3]
check(
    not untagged,
    "every live session has a spoken tag",
    f"untagged: {[s['thread_id'] for s in untagged]}" if untagged else "",
)

labels = [s["voice_label"] for s in tagged]
dupes = sorted({x for x in labels if labels.count(x) > 1})
check(not dupes, "no two threads answer to the same tag", f"duplicated: {dupes}" if dupes else "")

missing_aliases = [s["voice_label"] for s in tagged if not s.get("voice_label_aliases")]
check(
    not missing_aliases,
    "every tag carries the words it is misheard as",
    f"no aliases for: {missing_aliases}" if missing_aliases else "",
)

held = run(
    "sqlite3",
    str(REPO / "data/sessions.db"),
    "SELECT COUNT(*) FROM settings s JOIN sessions x "
    "ON s.key = 'voice_label:' || x.thread_id WHERE x.lifecycle_state = 'closed'",
)
check(held == "0", "no closed session is holding a tag", f"{held} still held" if held != "0" else "")

# --------------------------------------------------------------------------
# Drive the real voice decision code against the real session list
# --------------------------------------------------------------------------

probe = f"""
import {{ parseByTag, parseTidyUp }} from "{REPO}/extensions/voice_transcripts/src/control/command.mjs";
const live = {json.dumps(tagged)};
const tags = live.map((s) => ({{ label: s.voice_label, aliases: s.voice_label_aliases }}));
const out = {{}};
if (live.length) {{
  const tag = live[0].voice_label;
  const alias = (live[0].voice_label_aliases || [])[0];
  out.exact = parseByTag(`${{tag}} run make verify`, tags)?.target ?? null;
  out.misheard = alias ? (parseByTag(`${{alias}} run make verify`, tags)?.target ?? null) : "<none>";
  out.expected = tag;
}}
out.tidy = !!parseTidyUp("close everything I'm not using");
out.notTidy = !parseTidyUp("I should close everything I'm not using at some point");
console.log(JSON.stringify(out));
"""
raw = run("node", "--input-type=module", "-e", probe)
try:
    voice = json.loads(raw)
except Exception:
    voice = {}
    check(False, "the voice decision code runs", raw[:200])

if voice:
    check(
        voice.get("exact") == voice.get("expected"),
        "saying a tag reaches its own thread",
        f"{voice.get('exact')} != {voice.get('expected')}",
    )
    check(
        voice.get("misheard") in (voice.get("expected"), "<none>"),
        "a misheard tag still reaches its own thread",
        f"{voice.get('misheard')} != {voice.get('expected')}",
    )
    check(bool(voice.get("tidy")), '"close everything I am not using" is understood')
    check(bool(voice.get("notTidy")), "talking about closing does not close anything")

# --------------------------------------------------------------------------
# Is it complaining about anything, quietly?
# --------------------------------------------------------------------------

lines = log_since(started)
errors: dict[str, int] = {}
for line in lines:
    if "[ERROR]" not in line:
        continue
    key = re.sub(r"\d{15,}", "ID", line.split("[ERROR]")[1])[:90]
    errors[key] = errors.get(key, 0) + 1
repeated = {k: v for k, v in errors.items() if v >= 3}
check(not repeated, "nothing is failing over and over", "; ".join(f"{v}x{k}" for k, v in repeated.items()))

edit_429 = sum(1 for line in lines if "429" in line and "PATCH" in line and "messages" in line)
check(edit_429 == 0, "Discord is not rejecting message updates", f"{edit_429} rejections")

# --------------------------------------------------------------------------

width = max(len(name) for _ok, name, _d in results)
failed = 0
for ok, name, detail in results:
    mark = "PASS" if ok else "FAIL"
    # Detail only on a failure: a passing line explaining itself is noise, and
    # noise is what stopped anyone reading the log in the first place.
    print(f"[{mark}] {name.ljust(width)}  {'' if ok else detail}".rstrip())
    failed += not ok
print(f"\n{len(results) - failed}/{len(results)} checks passed")
sys.exit(1 if failed else 0)
