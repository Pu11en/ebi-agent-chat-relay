"""The archive/unarchive/delete proof judges snapshots purely and always cleans up (step A4).

The script itself talks to the running bot and to Discord, so it is never run
here; these exercise the pure judge, the confirmation gate, the .env reader,
the request shapes of both clients and the runner's cleanup with fakes.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "discord-thread-proof.py"
spec = importlib.util.spec_from_file_location("discord_thread_proof", SCRIPT)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
sys.modules["discord_thread_proof"] = proof
spec.loader.exec_module(proof)

TOKEN = "MTIz.fake.token-never-printed"


def row(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "thread_id": "42",
        "tag": "zoro",
        "name": "[zoro] scratch archive check",
        "closed": False,
        "visible": True,
    }
    base.update(fields)
    return base


def marks(verdicts: list[Any]) -> list[tuple[str, str]]:
    return [(v.stage, v.mark) for v in verdicts]


# --- the pure judge ---------------------------------------------------------


def test_judge_passes_the_full_archive_cycle() -> None:
    verdicts = proof.judge(
        [
            ("spawned", row()),
            ("archived", row(closed=True, tag=None, visible=False)),
            ("unarchived", row()),
            ("deleted", row(closed=True, tag=None, visible=False)),
        ]
    )
    assert marks(verdicts) == [
        ("spawned", "PASS"),
        ("archived", "PASS"),
        ("unarchived", "PASS"),
        ("deleted", "PASS"),
    ]
    assert "zoro" in verdicts[0].detail


def test_judge_fails_an_archived_thread_that_stays_open() -> None:
    verdicts = proof.judge([("spawned", row()), ("archived", row())])
    failed = [v for v in verdicts if v.mark == "FAIL"]
    assert [v.stage for v in failed] == ["archived"]
    assert "archive" in failed[0].stage
    assert "still open" in failed[0].detail and "zoro" in failed[0].detail


def test_judge_fails_a_missing_row_and_a_missing_tag() -> None:
    verdicts = proof.judge([("spawned", row(tag=None)), ("archived", None), ("unarchived", None)])
    assert marks(verdicts) == [("spawned", "FAIL"), ("archived", "FAIL"), ("unarchived", "FAIL")]
    assert "no tag" in verdicts[0].detail
    assert "no row" in verdicts[1].detail


def test_judge_fails_a_closed_row_that_keeps_its_tag_and_an_unknown_stage() -> None:
    verdicts = proof.judge([("deleted", row(closed=True, tag="zoro")), ("frobbed", row())])
    assert marks(verdicts) == [("deleted", "FAIL"), ("frobbed", "FAIL")]
    assert "tag" in verdicts[0].detail
    assert "unknown stage" in verdicts[1].detail


def test_exit_code_is_nonzero_on_any_fail() -> None:
    assert proof.exit_code([proof.Verdict("spawned", "PASS", "")]) == 0
    assert (
        proof.exit_code([proof.Verdict("spawned", "PASS", ""), proof.Verdict("x", "FAIL", "")]) == 1
    )


def test_table_names_every_stage() -> None:
    table = proof.render_table(proof.judge([("spawned", row()), ("archived", row())]))
    assert "spawned" in table and "archived" in table and "FAIL" in table and "PASS" in table


# --- the confirmation gate and the .env reader -------------------------------


def test_refuses_to_run_without_explicit_confirmation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert proof.main(["--env-file", str(tmp_path / ".env")], env={}) == 2
    assert "CCDB_PROOF_CONFIRM=yes" in capsys.readouterr().err


def test_refuses_without_a_bot_token_and_never_touches_the_network(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("API_PORT=9876\n")
    code = proof.main(["--env-file", str(tmp_path / ".env")], env={"CCDB_PROOF_CONFIRM": "yes"})
    assert code == 2
    assert "DISCORD_BOT_TOKEN" in capsys.readouterr().err


def test_read_env_value_parses_quotes_exports_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# the bot\n"
        f'DISCORD_BOT_TOKEN="{TOKEN}"\n'
        "export CCDB_API_SECRET='s3cret'\n"
        "API_PORT=9876 \n"
        "EMPTY=\n"
    )
    assert proof.read_env_value(env, "DISCORD_BOT_TOKEN") == TOKEN
    assert proof.read_env_value(env, "CCDB_API_SECRET") == "s3cret"
    assert proof.read_env_value(env, "API_PORT") == "9876"
    assert proof.read_env_value(env, "EMPTY") is None
    assert proof.read_env_value(env, "MISSING") is None
    assert proof.read_env_value(tmp_path / "none", "DISCORD_BOT_TOKEN") is None


# --- the two HTTP clients (request shapes only; no network) ------------------


class Opener:
    """Stands in for urlopen: records requests, answers with canned bodies."""

    def __init__(self, *bodies: object, error: Exception | None = None) -> None:
        self.bodies = list(bodies)
        self.error = error
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []

    def __call__(self, request: Any, timeout: float) -> bytes:
        data = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url, dict(request.headers), data))
        if self.error is not None:
            raise self.error
        return json.dumps(self.bodies.pop(0) if self.bodies else {}).encode()


def test_ebi_client_spawns_an_empty_tagged_thread_and_polls_the_read_only_snapshot() -> None:
    opener = Opener({"thread_id": "42", "voice_label": "zoro"}, {"sessions": [row()]})
    ebi = proof.EbiApi("http://127.0.0.1:9876", "s3cret", opener=opener)
    assert ebi.spawn(proof.SCRATCH_NAME, None)["thread_id"] == "42"
    assert ebi.rows() == [row()]
    (method, url, headers, body), (poll_method, poll_url, poll_headers, _) = opener.requests
    assert (method, url) == ("POST", "http://127.0.0.1:9876/api/spawn")
    assert body == {"empty": True, "auto_start": False, "thread_name": "scratch archive check"}
    assert headers["Authorization"] == "Bearer s3cret"
    assert (poll_method, poll_url) == (
        "GET",
        "http://127.0.0.1:9876/api/jester/sessions?include_closed=1&limit=100",
    )
    assert poll_headers["Authorization"] == "Bearer s3cret"
    assert not any("/api/" + "sessions" in r[1] for r in opener.requests)


def test_ebi_client_passes_a_chosen_channel_and_no_auth_header_without_a_secret() -> None:
    opener = Opener({"thread_id": "42"})
    proof.EbiApi("http://127.0.0.1:9876", "", opener=opener).spawn(
        "scratch archive check", "77", working_dir="/srv/project"
    )
    _method, _url, headers, body = opener.requests[0]
    assert body["channel_id"] == "77"
    # A bot without a default working directory makes no row for an empty
    # spawn; binding a directory guarantees the row the proof polls for.
    assert body["working_dir"] == "/srv/project"
    assert "Authorization" not in headers


def test_discord_client_archives_unarchives_and_deletes_with_bot_auth() -> None:
    opener = Opener({}, {}, {})
    discord = proof.DiscordThreads(TOKEN, opener=opener)
    discord.set_archived("42", True)
    discord.set_archived("42", False)
    discord.delete("42")
    assert [(m, u, b) for m, u, _h, b in opener.requests] == [
        ("PATCH", "https://discord.com/api/v10/channels/42", {"archived": True}),
        ("PATCH", "https://discord.com/api/v10/channels/42", {"archived": False}),
        ("DELETE", "https://discord.com/api/v10/channels/42", None),
    ]
    assert all(h["Authorization"] == f"Bot {TOKEN}" for _m, _u, h, _b in opener.requests)


def test_http_failures_name_the_status_but_never_the_token() -> None:
    error = urllib.error.HTTPError(
        "https://discord.com/api/v10/channels/42", 403, "Forbidden", Message(), io.BytesIO(b"{}")
    )
    discord = proof.DiscordThreads(TOKEN, opener=Opener(error=error))
    with pytest.raises(proof.ProofError) as raised:
        discord.delete("42")
    assert "403" in str(raised.value)
    assert TOKEN not in str(raised.value) and TOKEN not in repr(raised.value)


# --- the runner: one scratch thread, always deleted ---------------------------


class FakeDiscord:
    """Discord as the proof sees it; ``follow`` decides whether EBI keeps up."""

    def __init__(self, ebi: FakeEbi, *, follow: bool = True, fail_on: str | None = None) -> None:
        self.ebi = ebi
        self.follow = follow
        self.fail_on = fail_on
        self.actions: list[tuple[str, str]] = []

    def set_archived(self, thread_id: str, archived: bool) -> None:
        action = "archive" if archived else "unarchive"
        self.actions.append((action, thread_id))
        if self.fail_on == action:
            raise proof.ProofError(f"Discord refused {action}: HTTP 403")
        if self.follow:
            self.ebi.row.update(
                {"closed": archived, "tag": None if archived else "zoro", "visible": not archived}
            )

    def delete(self, thread_id: str) -> None:
        self.actions.append(("delete", thread_id))
        if self.follow:
            self.ebi.row.update({"closed": True, "tag": None, "visible": False})


class FakeEbi:
    def __init__(self) -> None:
        self.spawns: list[tuple[str, str | None]] = []
        self.row: dict[str, object] = {}
        self.polls = 0

    def spawn(
        self, name: str, channel_id: str | None, working_dir: str | None = None
    ) -> Mapping[str, object]:
        self.spawns.append((name, channel_id))
        self.row = row()
        return {"thread_id": "42", "voice_label": "zoro"}

    def rows(self) -> list[Mapping[str, object]]:
        self.polls += 1
        return [row(thread_id="7", tag="nami"), dict(self.row)]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def run(ebi: FakeEbi, discord: FakeDiscord) -> tuple[list[Any], list[str]]:
    clock = Clock()
    return proof.run_proof(
        ebi, discord, channel_id=None, timeout=10.0, interval=1.0, sleep=clock.sleep, clock=clock
    )


def test_run_proof_uses_one_scratch_thread_and_passes_when_ebi_follows_discord() -> None:
    ebi = FakeEbi()
    discord = FakeDiscord(ebi)
    verdicts, notes = run(ebi, discord)
    assert marks(verdicts) == [
        ("spawned", "PASS"),
        ("archived", "PASS"),
        ("unarchived", "PASS"),
        ("deleted", "PASS"),
    ]
    assert ebi.spawns == [("scratch archive check", None)]
    assert discord.actions == [("archive", "42"), ("unarchive", "42"), ("delete", "42")]
    assert proof.exit_code(verdicts) == 0
    assert any("42" in note for note in notes)


def test_run_proof_deletes_the_scratch_thread_even_when_a_stage_fails() -> None:
    ebi = FakeEbi()
    discord = FakeDiscord(ebi, follow=False)  # EBI never notices the archive
    verdicts, _notes = run(ebi, discord)
    assert marks(verdicts) == [
        ("spawned", "PASS"),
        ("archived", "FAIL"),
        ("unarchived", "PASS"),
        ("deleted", "FAIL"),
    ]
    assert [v.detail for v in verdicts if v.stage == "archived"][0].startswith("still open")
    assert discord.actions.count(("delete", "42")) == 1
    assert proof.exit_code(verdicts) == 1
    assert ebi.polls > 4  # it kept polling until the timeout, not just once


def test_run_proof_deletes_the_scratch_thread_when_discord_refuses_a_stage() -> None:
    ebi = FakeEbi()
    discord = FakeDiscord(ebi, fail_on="archive")
    verdicts, notes = run(ebi, discord)
    assert marks(verdicts) == [
        ("spawned", "PASS"),
        ("archived", "FAIL"),
        ("unarchived", "FAIL"),
        ("deleted", "FAIL"),
    ]
    assert "HTTP 403" in verdicts[1].detail
    assert verdicts[2].detail == "not reached"
    assert discord.actions == [("archive", "42"), ("delete", "42")]
    assert any("deleted" in note for note in notes)


def test_run_proof_reports_a_failed_cleanup_instead_of_hiding_it() -> None:
    ebi = FakeEbi()
    discord = FakeDiscord(ebi, fail_on="archive")
    original_delete = discord.delete

    def refuse(thread_id: str) -> None:
        original_delete(thread_id)
        raise proof.ProofError("Discord refused delete: HTTP 500")

    discord.delete = refuse  # type: ignore[method-assign]
    _verdicts, notes = run(ebi, discord)
    assert any("NOT deleted" in note and "HTTP 500" in note for note in notes)


def test_run_proof_never_spawns_twice_when_the_spawn_itself_fails() -> None:
    class BrokenEbi(FakeEbi):
        def spawn(
            self, name: str, channel_id: str | None, working_dir: str | None = None
        ) -> Mapping[str, object]:
            self.spawns.append((name, channel_id))
            raise proof.ProofError("EBI refused spawn: HTTP 503")

    ebi = BrokenEbi()
    discord = FakeDiscord(ebi)
    verdicts, _notes = run(ebi, discord)
    assert marks(verdicts)[0] == ("spawned", "FAIL")
    assert "HTTP 503" in verdicts[0].detail
    assert len(ebi.spawns) == 1
    assert discord.actions == []  # nothing to delete
