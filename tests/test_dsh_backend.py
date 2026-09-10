"""Tests for the DeepSeek backend (DeepSeek Harness over the official SDK).

The SDK itself is never imported here: these tests drive the runner's own logic
— session identity, notification mapping, turn lifecycle, environment handling
— through a fake runtime, so they run without the optional extra installed.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from claude_code_core import dsh_backend
from claude_code_core.dsh_backend import DshRunner
from claude_code_core.types import MessageType, ToolCategory


@dataclass
class FakeNotification:
    method: str
    payload: dict[str, Any]


@dataclass
class FakeResult:
    session_id: str
    final_response: str = ""
    finish_reason: str | None = "completed"


@dataclass
class FakeSession:
    notifications: list[FakeNotification] = field(default_factory=list)
    result: FakeResult | None = None
    error: BaseException | None = None
    prompts: list[str] = field(default_factory=list)

    def run(self, prompt: str, *, on_notification=None) -> FakeResult:
        self.prompts.append(prompt)
        for notification in self.notifications:
            if on_notification is not None:
                on_notification(notification)
        if self.error is not None:
            raise self.error
        return self.result or FakeResult(session_id="fake")


@dataclass
class FakeClient:
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def request(self, method: str, params: object = None, **_kwargs: object) -> None:
        self.requests.append((method, params if isinstance(params, dict) else {}))


class FakeRuntime:
    """Stands in for ``deepseek_harness.DeepSeekHarness``."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.client = FakeClient()
        self.session_ids: list[str] = []

    def start_session(self, session_id: str) -> FakeSession:
        self.session_ids.append(session_id)
        return self.session

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_runtimes():
    dsh_backend.reset_runtimes()
    yield
    dsh_backend.reset_runtimes()


def _session_event(session_id: str, event: dict[str, Any]) -> FakeNotification:
    return FakeNotification(
        method="session.event",
        payload={"sessionId": session_id, "event": event},
    )


def _assistant_text(session_id: str, text: str) -> FakeNotification:
    return _session_event(
        session_id,
        {
            "type": "assistant/message",
            "data": {"message": {"content": [{"type": "text", "text": text}]}},
        },
    )


def _tool_call(session_id: str, name: str, arguments: object) -> FakeNotification:
    return _session_event(
        session_id,
        {
            "type": "tool/call",
            "data": {"callId": "call-1", "name": name, "arguments": arguments},
        },
    )


async def _collect(runner: DshRunner, prompt: str, session_id: str | None = None):
    return [event async for event in runner.run(prompt, session_id)]


# ── Session identity ────────────────────────────────────────


def test_unknown_session_id_is_replaced_with_a_fresh_one():
    runner = DshRunner()
    session_id, is_new = runner._session_for_turn("session-stored-by-an-earlier-process")
    assert is_new is True
    assert session_id != "session-stored-by-an-earlier-process"


def test_a_session_this_process_started_is_continued():
    runner = DshRunner()
    first, _ = runner._session_for_turn(None)
    again, is_new = runner._session_for_turn(first)
    assert again == first
    assert is_new is False


def test_fresh_session_ids_never_repeat():
    runner = DshRunner()
    ids = {runner._session_for_turn(None)[0] for _ in range(5)}
    assert len(ids) == 5


# ── Event mapping ───────────────────────────────────────────


def test_assistant_text_and_reasoning_become_events():
    runner = DshRunner()
    session_id = "session-a"
    notification = _session_event(
        session_id,
        {
            "type": "assistant/message",
            "data": {
                "message": {
                    "content": [
                        {"type": "reasoning", "text": "thinking hard"},
                        {"type": "text", "text": "the answer"},
                    ]
                }
            },
        },
    )
    events = runner._map_notification(notification, session_id)
    assert [e.text for e in events if e.text] == ["the answer"]
    assert [e.thinking for e in events if e.thinking] == ["thinking hard"]
    assert all(e.session_id == session_id for e in events)


def test_another_sessions_notifications_are_ignored():
    """A subagent session reports through the same subscription."""
    runner = DshRunner()
    events = runner._map_notification(_assistant_text("session-other", "hi"), "session-mine")
    assert events == []


def test_tool_calls_map_to_the_names_and_categories_the_ui_knows():
    runner = DshRunner()
    session_id = "session-a"
    events = runner._map_notification(
        _tool_call(session_id, "bash", json.dumps({"command": "ls -la"})),
        session_id,
    )
    (event,) = events
    assert event.tool_use is not None
    assert event.tool_use.tool_name == "Bash"
    assert event.tool_use.category is ToolCategory.COMMAND
    assert event.tool_use.tool_input == {"command": "ls -la"}
    assert event.tool_use.display_name == "Running: ls -la"


def test_read_tool_uses_the_claude_style_display_string():
    runner = DshRunner()
    session_id = "session-a"
    events = runner._map_notification(
        _tool_call(session_id, "read", json.dumps({"file_path": "/tmp/x.py"})),
        session_id,
    )
    assert events[0].tool_use is not None
    assert events[0].tool_use.category is ToolCategory.READ
    assert events[0].tool_use.display_name == "Reading: /tmp/x.py"


def test_unparsable_tool_arguments_do_not_break_the_event():
    runner = DshRunner()
    events = runner._map_notification(_tool_call("session-a", "bash", "not json"), "session-a")
    assert events[0].tool_use is not None
    assert events[0].tool_use.tool_input == {}


def test_unknown_tool_names_fall_back_to_other():
    runner = DshRunner()
    events = runner._map_notification(_tool_call("session-a", "mystery", "{}"), "session-a")
    assert events[0].tool_use is not None
    assert events[0].tool_use.category is ToolCategory.OTHER


def test_tool_results_carry_their_text():
    runner = DshRunner()
    session_id = "session-a"
    notification = _session_event(
        session_id,
        {
            "type": "tool/result",
            "data": {
                "message": {
                    "content": [
                        {
                            "type": "tool-result",
                            "toolCallId": "call-1",
                            "content": [{"type": "text", "text": "ccdb-tool-ok"}],
                        }
                    ]
                }
            },
        },
    )
    (event,) = runner._map_notification(notification, session_id)
    assert event.message_type is MessageType.USER
    assert event.tool_result_id == "call-1"
    assert event.tool_result_content == "ccdb-tool-ok"


def test_turn_end_error_becomes_a_completing_error_event():
    runner = DshRunner()
    session_id = "session-a"
    notification = _session_event(
        session_id,
        {
            "type": "turn/end",
            "data": {"reason": {"kind": "error", "error": {"message": "model exploded"}}},
        },
    )
    (event,) = runner._map_notification(notification, session_id)
    assert event.error == "model exploded"
    assert event.is_complete is True


def test_successful_turn_end_maps_to_nothing_until_the_turn_closes():
    runner = DshRunner()
    notification = _session_event(
        "session-a", {"type": "turn/end", "data": {"reason": {"kind": "completed"}}}
    )
    assert runner._map_notification(notification, "session-a") == []


def test_non_session_events_are_ignored():
    runner = DshRunner()
    notification = FakeNotification(method="session.status", payload={"sessionId": "session-a"})
    assert runner._map_notification(notification, "session-a") == []


# ── Turn lifecycle ──────────────────────────────────────────


async def test_run_streams_mapped_events_and_closes_the_turn(monkeypatch):
    runner = DshRunner(timeout_seconds=30)
    runtime = FakeRuntime(
        FakeSession(
            notifications=[_assistant_text("ignored", "x")],
            result=FakeResult(session_id="whatever", final_response="done"),
        )
    )
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)
    # The session id is minted by the runner, so the fake must answer with it.
    session_id, _ = runner._session_for_turn(None)
    runtime.session.notifications = [_assistant_text(session_id, "hello")]

    events = await _collect(runner, "hi", session_id)

    assert [e.text for e in events if e.text] == ["hello"]
    final = events[-1]
    assert final.is_complete is True
    assert final.error is None
    assert all(e.session_id == session_id for e in events)


async def test_every_event_carries_the_session_id_the_caller_must_store(monkeypatch):
    """The event processor drops session-less events, so this is load-bearing."""
    runner = DshRunner()
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: FakeRuntime(FakeSession()))

    events = await _collect(runner, "hi")

    assert events, "a turn must always produce at least the closing event"
    assert all(e.session_id for e in events)
    assert len({e.session_id for e in events}) == 1


async def test_a_stored_id_from_another_process_starts_a_new_session(monkeypatch):
    runner = DshRunner()
    runtime = FakeRuntime(FakeSession())
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)

    events = await _collect(runner, "hi", "session-written-by-a-previous-process")

    used = runtime.session_ids[0]
    assert used != "session-written-by-a-previous-process"
    assert events[-1].session_id == used


async def test_empty_prompt_fails_without_starting_a_runtime(monkeypatch):
    runner = DshRunner()

    def _explode() -> None:
        raise AssertionError("runtime must not start for an empty prompt")

    monkeypatch.setattr(runner, "_ensure_runtime", _explode)

    events = await _collect(runner, "   ")

    assert events[-1].error == "Empty prompt"
    assert events[-1].is_complete is True


async def test_runtime_startup_failure_is_reported_as_an_error_event(monkeypatch):
    runner = DshRunner()

    def _fail() -> None:
        raise RuntimeError("The DeepSeek backend needs the DeepSeek Harness SDK")

    monkeypatch.setattr(runner, "_ensure_runtime", _fail)

    events = await _collect(runner, "hi")

    assert events[-1].is_complete is True
    assert "DeepSeek Harness SDK" in (events[-1].error or "")


async def test_a_worker_that_raises_ends_the_turn_with_that_error(monkeypatch):
    runner = DshRunner()
    runtime = FakeRuntime(FakeSession(error=RuntimeError("runtime died")))
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)

    events = await _collect(runner, "hi")

    assert "runtime died" in (events[-1].error or "")
    assert events[-1].is_complete is True


async def test_timeout_interrupts_and_reports(monkeypatch):
    """A turn that never reports back must still close, and must be cancelled."""

    class HangingSession(FakeSession):
        def run(self, prompt: str, *, on_notification=None) -> FakeResult:
            import time

            time.sleep(0.5)
            return FakeResult(session_id="x")

    runner = DshRunner(timeout_seconds=1)
    runtime = FakeRuntime(HangingSession())
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)
    runner.timeout_seconds = 0.05

    events = await _collect(runner, "hi")

    assert "Timed out" in (events[-1].error or "")
    # The cancel is issued from a task started as the turn unwinds.
    for _ in range(5):
        await asyncio.sleep(0)
    assert [method for method, _ in runtime.client.requests] == ["session/cancel"]


async def test_interrupt_sends_a_cancel_for_the_active_session(monkeypatch):
    runner = DshRunner()
    runtime = FakeRuntime(FakeSession())
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)
    runner._active_session = "session-live"

    await runner.interrupt()

    assert runtime.client.requests == [("session/cancel", {"sessionId": "session-live"})]


async def test_interrupt_without_an_active_turn_is_a_noop(monkeypatch):
    runner = DshRunner()
    runtime = FakeRuntime(FakeSession())
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)

    await runner.interrupt()

    assert runtime.client.requests == []


# ── Standing instruction ────────────────────────────────────


def test_standing_instruction_leads_the_first_turn_only():
    runner = DshRunner(append_system_prompt="Act, do not ask.")
    assert runner._with_standing_instruction("fix it") == "Act, do not ask.\n\nfix it"


def test_standing_instruction_is_absent_when_not_configured():
    runner = DshRunner()
    assert runner._with_standing_instruction("fix it") == "fix it"


async def test_standing_instruction_is_sent_once_per_session(monkeypatch):
    runner = DshRunner(append_system_prompt="Act, do not ask.")
    session = FakeSession()
    runtime = FakeRuntime(session)
    monkeypatch.setattr(runner, "_ensure_runtime", lambda: runtime)

    events = await _collect(runner, "first")
    session_id = events[-1].session_id
    assert session is not None
    await _collect(runner, "second", session_id)

    assert session.prompts == ["Act, do not ask.\n\nfirst", "second"]


# ── Environment ─────────────────────────────────────────────


def test_build_env_hides_the_relays_own_credentials(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "super-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    env = DshRunner()._build_env()
    assert "DISCORD_BOT_TOKEN" not in env
    assert env["DEEPSEEK_API_KEY"] == "sk-deepseek"


def test_the_runtime_never_inherits_transport_credentials(monkeypatch):
    """The SDK copies os.environ wholesale, so startup is the only lever."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "super-secret")
    seen: dict[str, str | None] = {}

    class Probe:
        def __init__(self, **_kwargs: object) -> None:
            seen["token"] = os.environ.get("DISCORD_BOT_TOKEN")

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        dsh_backend, "_require_sdk", lambda: type("S", (), {"DeepSeekHarness": Probe})
    )

    runner = DshRunner()
    runner._ensure_runtime()

    assert seen["token"] is None
    assert os.environ["DISCORD_BOT_TOKEN"] == "super-secret"


# ── Protocol surface ────────────────────────────────────────


def test_clone_keeps_configuration_and_adds_no_turn_state():
    runner = DshRunner(
        model="deepseek-v4-pro",
        working_dir="/tmp/work",
        thread_id=42,
        effort="high",
        timeout_seconds=99,
        append_system_prompt="be brief",
    )
    clone = runner.clone()
    assert isinstance(clone, DshRunner)
    assert clone.model == "deepseek-v4-pro"
    assert clone.working_dir == "/tmp/work"
    assert clone.thread_id == 42
    assert clone.effort == "high"
    assert clone.timeout_seconds == 99
    assert clone.append_system_prompt == "be brief"
    assert clone._active_session is None


def test_clone_overrides_are_honoured():
    runner = DshRunner(model="deepseek-v4-flash", working_dir="/a")
    clone = runner.clone(model="deepseek-v4-pro", working_dir="/b")
    assert clone.model == "deepseek-v4-pro"
    assert clone.working_dir == "/b"


def test_describe_api_names_the_harness_route():
    runner = DshRunner(model="deepseek-v4-pro")
    assert runner.describe_api() == "DeepSeek Harness (deepseek-official/deepseek-v4-pro)"


def test_default_model_is_the_harness_default():
    assert DshRunner().model == dsh_backend.DEFAULT_MODEL


def test_shutdown_closes_every_runtime(monkeypatch):
    closed: list[bool] = []

    class Closable:
        def close(self) -> None:
            closed.append(True)

    with dsh_backend._RUNTIMES_LOCK:
        entry = dsh_backend._RuntimeEntry()
        entry.harness = Closable()
        dsh_backend._RUNTIMES[("m", "p", "/w")] = entry

    dsh_backend.shutdown_runtimes()

    assert closed == [True]
    assert dsh_backend._RUNTIMES == {}


def test_missing_sdk_reports_the_extra(monkeypatch):
    import importlib

    real_import = importlib.import_module

    def _no_sdk(name: str, *args: object, **kwargs: object):
        if name == dsh_backend.SDK_MODULE:
            raise ImportError("No module named 'deepseek_harness'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", _no_sdk)

    assert dsh_backend.dsh_sdk_available() is False
    with pytest.raises(RuntimeError, match="deepseek"):
        dsh_backend._require_sdk()


# ── Wiring ──────────────────────────────────────────────────


def test_create_backend_builds_the_dsh_runner():
    from claude_code_core.backend import create_backend

    runner = create_backend(backend="dsh", model="deepseek-v4-pro")
    assert isinstance(runner, DshRunner)
    assert runner.model == "deepseek-v4-pro"


def test_dsh_is_a_selectable_backend():
    from claude_discord.backend_settings import ALL_BACKENDS

    assert "dsh" in ALL_BACKENDS


def test_factory_defaults_name_a_model_and_a_command():
    from claude_discord.backend_factory import DEFAULT_COMMAND, DEFAULT_MODEL

    assert DEFAULT_MODEL["dsh"] == dsh_backend.DEFAULT_MODEL
    assert DEFAULT_COMMAND["dsh"] == "dsh"


def test_backend_names_map_to_the_embed_tag():
    from claude_discord.cogs.event_processor import _backend_name_from_runner

    assert _backend_name_from_runner(DshRunner()) == "dsh"


def test_model_suggestions_are_available_without_network():
    from claude_discord.cogs.backend_command import SUGGESTED_MODELS

    values = [value for value, _ in SUGGESTED_MODELS["dsh"]]
    assert dsh_backend.DEFAULT_MODEL in values
    assert "deepseek-v4-pro" in values


async def test_autocomplete_falls_back_when_discovery_is_off(monkeypatch):
    from claude_discord.model_catalog import dsh_model_choices

    monkeypatch.setenv("CCDB_MODEL_DISCOVERY", "0")
    fallback = [("deepseek-v4-flash", "fallback")]
    assert await dsh_model_choices(fallback=fallback) == fallback


def test_the_backend_only_starts_one_runtime_per_model_and_directory(monkeypatch):
    """A runtime is initialised with a fixed model route; it cannot be shared."""
    starts: list[dict[str, object]] = []

    class Probe:
        def __init__(self, **kwargs: object) -> None:
            starts.append(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        dsh_backend, "_require_sdk", lambda: type("S", (), {"DeepSeekHarness": Probe})
    )

    runner = DshRunner(model="deepseek-v4-flash", working_dir="/tmp/w")
    first = runner._ensure_runtime()
    second = runner._ensure_runtime()
    other_model = DshRunner(model="deepseek-v4-pro", working_dir="/tmp/w")._ensure_runtime()

    assert first is second
    assert other_model is not first
    assert len(starts) == 2
    assert starts[0]["model"] == "deepseek-v4-flash"
    assert starts[0]["cwd"] == "/tmp/w"


def test_effort_is_forwarded_to_the_runtime(monkeypatch):
    starts: list[dict[str, object]] = []

    class Probe:
        def __init__(self, **kwargs: object) -> None:
            starts.append(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        dsh_backend, "_require_sdk", lambda: type("S", (), {"DeepSeekHarness": Probe})
    )
    DshRunner(effort="high", working_dir="/tmp/w")._ensure_runtime()

    assert starts[0]["reasoning_effort"] == "high"


# ── Provider routes ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "explicit", "expected_route", "expected_model"),
    [
        ("deepseek-v4-pro", None, "deepseek-official", "deepseek-v4-pro"),
        ("deepseek-v4-flash", None, "deepseek-official", "deepseek-v4-flash"),
        ("glm-5.2", None, "zai", "glm-5.2"),
        ("GLM-5-Turbo", None, "zai", "GLM-5-Turbo"),
        # An explicit route wins over the prefix, and is stripped from the id.
        ("glm-5.2", "deepseek-official", "deepseek-official", "glm-5.2"),
        ("zai/glm-5.2", None, "zai", "glm-5.2"),
        ("deepseek-official/deepseek-v4-pro", None, "deepseek-official", "deepseek-v4-pro"),
        # An unknown model still runs, on the harness's own adapter.
        ("something-new", None, "deepseek-official", "something-new"),
    ],
)
def test_provider_route_follows_the_model_name(model, explicit, expected_route, expected_model):
    from claude_code_core.dsh_backend import resolve_provider

    assert resolve_provider(model, explicit) == (expected_route, expected_model)


def test_runner_splits_the_route_out_of_the_model():
    runner = DshRunner(model="glm-5.2")
    assert runner.route == "zai"
    assert runner.model == "glm-5.2"
    assert runner.describe_api() == "DeepSeek Harness (zai/glm-5.2)"


def test_one_runtime_per_route_even_for_the_same_directory(monkeypatch, tmp_path):
    """A runtime is bound to one route at handshake, so routes never share one."""
    starts: list[dict[str, object]] = []

    class Probe:
        def __init__(self, **kwargs: object) -> None:
            starts.append(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        dsh_backend, "_require_sdk", lambda: type("S", (), {"DeepSeekHarness": Probe})
    )
    patch = tmp_path / "providers.patch.yml"

    DshRunner(model="deepseek-v4-flash", working_dir="/tmp/w", patch_path=patch)._ensure_runtime()
    DshRunner(model="deepseek-v4-flash", working_dir="/tmp/w", patch_path=patch)._ensure_runtime()
    DshRunner(model="glm-5.2", working_dir="/tmp/w", patch_path=patch)._ensure_runtime()

    assert [s["provider"] for s in starts] == ["deepseek-official", "zai"]
    assert starts[0]["model"] == "deepseek-v4-flash"
    assert starts[1]["model"] == "glm-5.2"


def test_the_route_patch_file_is_passed_to_the_runtime(monkeypatch, tmp_path):
    (tmp_path / "providers.patch.yml").write_text("- id: llm-pi-ai\n", encoding="utf-8")
    seen: dict[str, object] = {}

    class Probe:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(
        dsh_backend, "_require_sdk", lambda: type("S", (), {"DeepSeekHarness": Probe})
    )
    DshRunner(
        model="glm-5.2",
        working_dir="/tmp/w",
        patch_path=tmp_path / "providers.patch.yml",
    )._ensure_runtime()

    assert seen["patches"] == (str(tmp_path / "providers.patch.yml"),)


def test_patch_file_is_written_once_and_left_alone_afterwards(tmp_path):
    from claude_code_core.dsh_backend import DEFAULT_PATCH_CONTENT, ensure_patch_file

    target = tmp_path / "nested" / "providers.patch.yml"
    assert ensure_patch_file(target) == target
    assert target.read_text(encoding="utf-8") == DEFAULT_PATCH_CONTENT

    # An operator's own edit must survive; the file is config, not package state.
    target.write_text("- id: llm-pi-ai\n  config: {}\n", encoding="utf-8")
    ensure_patch_file(target)
    assert target.read_text(encoding="utf-8") == "- id: llm-pi-ai\n  config: {}\n"


def test_default_patch_names_the_zai_route_and_its_credential():
    """The shipped default is what makes GLM available without hand-written YAML."""
    assert "llm-pi-ai" in dsh_backend.DEFAULT_PATCH_CONTENT
    assert "zai:" in dsh_backend.DEFAULT_PATCH_CONTENT
    assert "ZAI_API_KEY" in dsh_backend.DEFAULT_PATCH_CONTENT


def test_unwritable_patch_location_does_not_break_the_backend(tmp_path):
    """A read-only config dir must degrade to DeepSeek-only, not to a crash."""
    from claude_code_core.dsh_backend import ensure_patch_file

    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x", encoding="utf-8")
    assert ensure_patch_file(blocked / "providers.patch.yml") is None
