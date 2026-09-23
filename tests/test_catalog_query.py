"""The harness-side catalog query helper (OpenSpec shared-project-catalog task 3.3).

Claude, Codex and DSH all reach the catalog the same way: through the local
control plane, on demand. Session context carries only a concise invocation
hint — never the projects themselves — and the helper prints bounded JSON.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.catalog_query import (
    CatalogQueryClient,
    CatalogQueryError,
    build_catalog_hint,
    main,
)
from claude_discord.cogs._run_helper import _build_system_context
from claude_discord.cogs.run_config import RunConfig

# --- the hint --------------------------------------------------------------


def test_hint_is_concise_names_the_endpoints_and_takes_no_catalog() -> None:
    hint = build_catalog_hint()
    assert len(hint) < 1200
    assert "/api/projects" in hint
    assert "/api/projects/resolve" in hint
    assert "$CCDB_API_URL" in hint
    assert "remote_target" in hint
    # The hint is a function of nothing: it cannot carry a project name.
    assert build_catalog_hint() == hint


@pytest.mark.real_system_context
async def test_session_context_carries_the_hint_but_no_project_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "zeta-secret-project").mkdir()
    (tmp_path / "omega-private").mkdir()
    monkeypatch.setenv("CCDB_PROJECT_ROOTS", str(tmp_path))
    runner = MagicMock()
    runner.working_dir = str(tmp_path / "zeta-secret-project")
    runner.api_port = 8080
    thread = MagicMock()
    thread.id = 4242
    config = RunConfig(thread=thread, runner=runner, prompt="what time is it?")
    context = await _build_system_context(config)
    assert context is not None
    assert "/api/projects" in context
    assert "omega-private" not in context
    assert "zeta-secret-project" not in context.replace(runner.working_dir, "")


@pytest.mark.real_system_context
@pytest.mark.parametrize("api_port, slim", [(None, False), (8080, True)])
async def test_no_hint_without_a_control_plane_or_in_a_slim_briefing(
    api_port: int | None, slim: bool
) -> None:
    runner = MagicMock()
    runner.working_dir = "/work"
    runner.api_port = api_port
    thread = MagicMock()
    thread.id = 4242
    config = RunConfig(thread=thread, runner=runner, prompt="hi", slim_context=slim)
    context = await _build_system_context(config)
    assert context is not None
    assert "/api/projects" not in context


# --- the client ------------------------------------------------------------


class FakeTransport:
    def __init__(self, status: int = 200, body: object = None) -> None:
        self.status = status
        self.body = body if body is not None else {"projects": [], "roots": [], "truncated": False}
        self.calls: list[tuple[str, str, bytes | None, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, body: bytes | None, headers: dict[str, str], timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, body, headers))
        return self.status, json.dumps(self.body).encode("utf-8")


def test_list_sends_bounded_query_params_and_the_bearer_token() -> None:
    transport = FakeTransport()
    client = CatalogQueryClient("http://127.0.0.1:8080/", secret="s3cret", transport=transport)
    client.list("alp ha", owner="drew", limit=5)
    method, url, body, headers = transport.calls[0]
    assert method == "GET" and body is None
    assert url.startswith("http://127.0.0.1:8080/api/projects?")
    assert "q=alp+ha" in url and "owner=drew" in url and "limit=5" in url
    assert headers["Authorization"] == "Bearer s3cret"


def test_resolve_posts_json_and_find_encodes_the_key() -> None:
    transport = FakeTransport(body={"kind": "no_match"})
    client = CatalogQueryClient("http://127.0.0.1:8080", transport=transport)
    assert client.resolve("David's alpha")["kind"] == "no_match"
    method, url, body, headers = transport.calls[0]
    assert method == "POST" and url == "http://127.0.0.1:8080/api/projects/resolve"
    assert body is not None and json.loads(body) == {"text": "David's alpha"}
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers
    client.find("drew:drewai:main:my project")
    assert (
        transport.calls[1][1]
        == "http://127.0.0.1:8080/api/projects/drew%3Adrewai%3Amain%3Amy%20project"
    )


def test_an_error_status_becomes_a_typed_error() -> None:
    client = CatalogQueryClient(
        "http://127.0.0.1:8080", transport=FakeTransport(404, {"error": "unknown"})
    )
    with pytest.raises(CatalogQueryError) as info:
        client.find("nope")
    assert info.value.status == 404 and "unknown" in str(info.value)


def test_client_refuses_a_non_local_control_plane() -> None:
    with pytest.raises(CatalogQueryError):
        CatalogQueryClient("http://example.com:8080", transport=FakeTransport())


# --- the command -----------------------------------------------------------


def test_main_prints_bounded_json_and_uses_the_environment() -> None:
    transport = FakeTransport(
        body={
            "projects": [{"key": "drew:drewai:main:alpha", "label": "alpha", "path": "/p/alpha"}],
            "roots": [],
            "truncated": False,
        }
    )
    out = io.StringIO()
    code = main(
        ["alpha", "--limit", "3"],
        env={"CCDB_API_URL": "http://127.0.0.1:8080", "CCDB_API_SECRET": "tok"},
        transport=transport,
        stdout=out,
    )
    assert code == 0
    assert json.loads(out.getvalue())["projects"][0]["key"] == "drew:drewai:main:alpha"
    assert transport.calls[0][3]["Authorization"] == "Bearer tok"
    assert "limit=3" in transport.calls[0][1]


def test_main_resolve_and_key_modes() -> None:
    transport = FakeTransport(body={"kind": "remote_target", "computer": "davidpc"})
    out = io.StringIO()
    assert (
        main(
            ["--resolve", "David's alpha"],
            env={"CCDB_API_URL": "http://127.0.0.1:8080"},
            transport=transport,
            stdout=out,
        )
        == 0
    )
    assert transport.calls[0][0] == "POST"
    assert json.loads(out.getvalue())["kind"] == "remote_target"
    transport = FakeTransport(body={"availability": "available"})
    assert (
        main(
            ["--key", "drew:drewai:main:alpha"],
            env={"CCDB_API_URL": "http://127.0.0.1:8080"},
            transport=transport,
            stdout=io.StringIO(),
        )
        == 0
    )
    assert transport.calls[0][1].endswith("/api/projects/drew%3Adrewai%3Amain%3Aalpha")


def test_main_without_a_control_plane_or_with_an_error_is_a_clear_failure() -> None:
    err = io.StringIO()
    assert main(["alpha"], env={}, transport=FakeTransport(), stdout=io.StringIO(), stderr=err) == 2
    assert "CCDB_API_URL" in err.getvalue()
    err = io.StringIO()
    code = main(
        ["--key", "nope"],
        env={"CCDB_API_URL": "http://127.0.0.1:8080"},
        transport=FakeTransport(404, {"error": "unknown catalog project"}),
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 1 and "unknown catalog project" in err.getvalue()


def test_main_never_spawns_or_reads_the_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import subprocess

    monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=AssertionError("spawned")))
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(side_effect=AssertionError("spawned"))
    )
    transport = FakeTransport()
    assert (
        main(
            ["alpha"],
            env={"CCDB_API_URL": "http://127.0.0.1:8080"},
            transport=transport,
            stdout=io.StringIO(),
        )
        == 0
    )
