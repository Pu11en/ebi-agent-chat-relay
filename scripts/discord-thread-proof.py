#!/usr/bin/env python3
"""Prove, with one scratch thread, that sessions follow Discord both ways.

The owner's acceptance checks (2026-09-29) are stated in Discord terms: archive
a thread by hand and its session closes and frees its tag; unarchive it and it
is open again; delete it and it closes. Unit tests show the code does this
with mocks. This shows the *running bot* does it, by doing exactly what the
owner would do, to a thread that only exists for the proof:

  1. spawn      an empty thread named "scratch archive check" through the EBI
                API (no prompt, no run, no model) — it must get a row and a tag
  2. archive    PATCH /channels/{id} {"archived": true}   -> closed, tag released
  3. unarchive  PATCH /channels/{id} {"archived": false}  -> open again
  4. delete     DELETE /channels/{id}                     -> closed

After every step it polls GET /api/jester/sessions?include_closed=1 (the
read-only snapshot; never the allocating listing) until the row shows the
expected state or the stage times out. A pure ``judge`` turns the snapshots
into PASS/FAIL per stage, the table is printed, and the exit code is 1 on any
FAIL. The scratch thread is deleted even when a stage fails or raises.

The Discord bot token is read from the EBI ``.env`` at run time (``--env-file``,
default ``<repo>/.env``) and is never printed or logged; failures report only
the HTTP status. Because this creates and deletes a real thread, it refuses to
run unless ``CCDB_PROOF_CONFIRM=yes`` is in the environment. It must not run
against a bot that predates the Discord-following code: the archived stage
would simply time out.

Usage:  CCDB_PROOF_CONFIRM=yes uv run python scripts/discord-thread-proof.py
        [--api-url http://127.0.0.1:9876] [--channel-id ID] [--working-dir DIR]
        [--timeout 90]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

REPO = Path(__file__).resolve().parent.parent
SCRATCH_NAME = "scratch archive check"
DISCORD_API = "https://discord.com/api/v10"
PASS, FAIL = "PASS", "FAIL"
SNAPSHOT_PATH = "/api/jester/sessions?include_closed=1&limit=100"

Row = Mapping[str, object]
Snapshot = tuple[str, Row | None]
Opener = Callable[[urllib.request.Request, float], bytes]


class ProofError(Exception):
    """A stage could not be carried out; the message never carries a credential."""


@dataclass(frozen=True)
class Verdict:
    stage: str
    mark: str
    detail: str


# --------------------------------------------------------------------------
# The judge. Pure: snapshots in, verdicts out.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage:
    name: str
    expected: str
    closed: bool
    needs_tag: bool = False


STAGES: tuple[Stage, ...] = (
    Stage("spawned", "open with a tag", closed=False, needs_tag=True),
    Stage("archived", "closed, tag released", closed=True),
    Stage("unarchived", "open again", closed=False),
    Stage("deleted", "closed, tag released", closed=True),
)
STAGE_BY_NAME = {stage.name: stage for stage in STAGES}


def describe(row: Row | None) -> str:
    if row is None:
        return "no row"
    state = "closed" if row.get("closed") else "open"
    tag = row.get("tag")
    visible = row.get("visible")
    seen = f"{state}, tag {tag!r}"
    if visible is not None:
        seen += ", visible" if visible else ", not visible"
    return seen


def judge_one(stage: Stage, row: Row | None) -> Verdict:
    if row is None:
        return Verdict(
            stage.name, FAIL, f"no row for the scratch thread; expected {stage.expected}"
        )
    closed = bool(row.get("closed"))
    tag = row.get("tag")
    if stage.closed and not closed:
        return Verdict(stage.name, FAIL, f"still open, tag {tag!r}; expected {stage.expected}")
    if not stage.closed and closed:
        return Verdict(stage.name, FAIL, f"still closed; expected {stage.expected}")
    if stage.closed and tag is not None:
        return Verdict(stage.name, FAIL, f"closed but tag {tag!r} not released")
    if stage.needs_tag and not tag:
        return Verdict(stage.name, FAIL, f"open but no tag ({describe(row)})")
    return Verdict(stage.name, PASS, describe(row))


def judge(snapshots: Sequence[Snapshot]) -> list[Verdict]:
    """PASS/FAIL per stage from what the snapshot showed after that stage."""
    out: list[Verdict] = []
    for name, row in snapshots:
        stage = STAGE_BY_NAME.get(name)
        if stage is None:
            out.append(Verdict(name, FAIL, f"unknown stage {name!r}"))
        else:
            out.append(judge_one(stage, row))
    return out


def exit_code(verdicts: Sequence[Verdict]) -> int:
    return 1 if any(v.mark == FAIL for v in verdicts) else 0


def render_table(verdicts: Sequence[Verdict]) -> str:
    width = max(len(v.stage) for v in verdicts) if verdicts else 0
    lines = []
    for v in verdicts:
        expected = STAGE_BY_NAME[v.stage].expected if v.stage in STAGE_BY_NAME else "?"
        lines.append(f"[{v.mark}] {v.stage.ljust(width)}  {expected.ljust(22)}  {v.detail}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Reading credentials. The token is a value passed to a header, nothing else.
# --------------------------------------------------------------------------


def read_env_value(path: Path, key: str) -> str | None:
    """One ``KEY=value`` from a dotenv file: quotes and ``export`` stripped, blanks None."""
    if not path.is_file():
        return None
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        name, sep, value = line.partition("=")
        if not sep or name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value or None
    return None


# --------------------------------------------------------------------------
# The two clients. Every call is argv-free urllib; failures carry the status only.
# --------------------------------------------------------------------------


def _urlopen(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _call(
    opener: Opener,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    body: object | None = None,
    what: str,
    timeout: float = 30.0,
) -> object:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for attempt in range(3):
        try:
            raw = opener(request, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                # Discord meters by route; wait what it asks for, then try once more.
                time.sleep(_retry_after(exc))
                continue
            raise ProofError(f"{what} refused: HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProofError(f"{what} unreachable: {type(exc).__name__}") from None
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise ProofError(f"{what} answered with something other than JSON") from None
    raise ProofError(f"{what} rate limited three times")


def _retry_after(exc: urllib.error.HTTPError) -> float:
    try:
        return min(float(json.loads(exc.read()).get("retry_after", 1.0)), 30.0)
    except (ValueError, TypeError, AttributeError, OSError):
        return 1.0


class EbiApi:
    """The relay's control API: one empty spawn, then read-only snapshot polls."""

    def __init__(self, base_url: str, secret: str, *, opener: Opener = _urlopen) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        self.opener = opener

    def spawn(self, name: str, channel_id: str | None, working_dir: str | None = None) -> Row:
        # An empty spawn makes the thread, its session row and its tag, and
        # starts no run: nothing here ever calls a model. The row comes from
        # binding a working directory, so a bot with no default one (no
        # CCDB_WORKING_DIR) needs the directory passed explicitly.
        body: dict[str, object] = {"empty": True, "auto_start": False, "thread_name": name}
        if channel_id:
            body["channel_id"] = channel_id
        if working_dir:
            body["working_dir"] = working_dir
        answer = _call(
            self.opener,
            "POST",
            f"{self.base_url}/api/spawn",
            headers=self.headers,
            body=body,
            what="EBI spawn",
        )
        # The id goes into a Discord URL path, so only a snowflake (digits) will do.
        if not isinstance(answer, dict) or not str(answer.get("thread_id") or "").isdigit():
            raise ProofError("EBI spawn answered without a thread id")
        return answer

    def rows(self) -> list[Row]:
        answer = _call(
            self.opener,
            "GET",
            f"{self.base_url}{SNAPSHOT_PATH}",
            headers=self.headers,
            what="EBI snapshot",
        )
        rows = answer.get("sessions") if isinstance(answer, dict) else None
        if not isinstance(rows, list):
            raise ProofError("EBI snapshot answered without a session list")
        return [r for r in rows if isinstance(r, dict)]


class DiscordThreads:
    """Archive, unarchive and delete a thread the way the owner's client does."""

    def __init__(self, token: str, *, opener: Opener = _urlopen) -> None:
        self.headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": "DiscordBot (ebi-agent-chat-relay thread proof, 1.0)",
        }
        self.opener = opener

    def set_archived(self, thread_id: str, archived: bool) -> None:
        _call(
            self.opener,
            "PATCH",
            f"{DISCORD_API}/channels/{thread_id}",
            headers=self.headers,
            body={"archived": archived},
            what="Discord " + ("archive" if archived else "unarchive"),
        )

    def delete(self, thread_id: str) -> None:
        _call(
            self.opener,
            "DELETE",
            f"{DISCORD_API}/channels/{thread_id}",
            headers=self.headers,
            what="Discord delete",
        )


class Ebi(Protocol):
    def spawn(self, name: str, channel_id: str | None, working_dir: str | None = None) -> Row: ...

    def rows(self) -> list[Row]: ...


class Threads(Protocol):
    def set_archived(self, thread_id: str, archived: bool) -> None: ...

    def delete(self, thread_id: str) -> None: ...


# --------------------------------------------------------------------------
# The runner: one thread, four stages, cleanup no matter what.
# --------------------------------------------------------------------------


def wait_for(
    ebi: Ebi,
    thread_id: str,
    stage: Stage,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Snapshot:
    """Poll the snapshot until the row satisfies *stage* or the time is up.

    The last row seen is returned either way, so a timeout is judged on what
    the bot actually showed ("still open, tag 'zoro'") rather than on nothing.
    """
    deadline = clock() + timeout
    row: Row | None = None
    while True:
        row = next((r for r in ebi.rows() if str(r.get("thread_id")) == thread_id), None)
        if judge_one(stage, row).mark == PASS or clock() >= deadline:
            return (stage.name, row)
        sleep(interval)


def run_proof(
    ebi: Ebi,
    threads: Threads,
    *,
    channel_id: str | None,
    timeout: float,
    interval: float,
    working_dir: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[Verdict], list[str]]:
    snapshots: list[Snapshot] = []
    errors: dict[str, str] = {}
    notes: list[str] = []
    thread_id: str | None = None
    deleted = False

    def settle(stage: Stage) -> None:
        assert thread_id is not None
        snapshots.append(
            wait_for(
                ebi, thread_id, stage, timeout=timeout, interval=interval, sleep=sleep, clock=clock
            )
        )

    try:
        thread_id = str(ebi.spawn(SCRATCH_NAME, channel_id, working_dir)["thread_id"])
        settle(STAGES[0])
        threads.set_archived(thread_id, True)
        settle(STAGES[1])
        threads.set_archived(thread_id, False)
        settle(STAGES[2])
        threads.delete(thread_id)
        deleted = True
        settle(STAGES[3])
    except ProofError as exc:
        # The stage that raised is a FAIL with its reason; nothing after it ran.
        failed = STAGES[len(snapshots)]
        snapshots.append((failed.name, None))
        errors[failed.name] = str(exc)
    finally:
        if thread_id is not None and not deleted:
            try:
                threads.delete(thread_id)
            except ProofError as exc:
                notes.append(f"scratch thread {thread_id} NOT deleted: {exc}")
            else:
                notes.append(f"scratch thread {thread_id} deleted after the failed stage")
        elif thread_id is not None:
            notes.append(f"scratch thread {thread_id} deleted")

    verdicts = [
        Verdict(v.stage, FAIL, errors[v.stage]) if v.stage in errors else v
        for v in judge(snapshots)
    ]
    verdicts += [Verdict(s.name, FAIL, "not reached") for s in STAGES[len(snapshots) :]]
    return verdicts, notes


# --------------------------------------------------------------------------
# Entry point.
# --------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--api-url", default=None, help="EBI control API (default: .env port)")
    parser.add_argument("--env-file", type=Path, default=REPO / ".env")
    parser.add_argument("--channel-id", default=None, help="parent channel (default: the bot's)")
    parser.add_argument(
        "--working-dir",
        default=None,
        help="project to bind the scratch thread to (needed when the bot has no default one)",
    )
    parser.add_argument("--timeout", type=float, default=90.0, help="seconds per stage")
    parser.add_argument("--interval", type=float, default=3.0, help="seconds between polls")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    args = parse_args(argv)
    if env.get("CCDB_PROOF_CONFIRM") != "yes":
        print(
            "refusing: this creates, archives and deletes a real Discord thread. "
            "Set CCDB_PROOF_CONFIRM=yes to run it on purpose.",
            file=sys.stderr,
        )
        return 2
    token = read_env_value(args.env_file, "DISCORD_BOT_TOKEN")
    if not token:
        print(f"refusing: no DISCORD_BOT_TOKEN in {args.env_file}", file=sys.stderr)
        return 2
    secret = env.get("CCDB_API_SECRET") or read_env_value(args.env_file, "CCDB_API_SECRET") or ""
    port = read_env_value(args.env_file, "API_PORT") or "9876"
    api_url = args.api_url or env.get("CCDB_API_URL") or f"http://127.0.0.1:{port}"

    verdicts, notes = run_proof(
        EbiApi(api_url, secret),
        DiscordThreads(token),
        channel_id=args.channel_id,
        timeout=args.timeout,
        interval=args.interval,
        working_dir=args.working_dir,
    )
    print(render_table(verdicts))
    for note in notes:
        print(note)
    code = exit_code(verdicts)
    print(
        "\nall stages passed: sessions follow Discord"
        if code == 0
        else "\na stage failed: sessions do not yet follow Discord"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
