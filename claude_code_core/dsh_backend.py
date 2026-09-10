"""The ``dsh`` backend — the DeepSeek Harness, hosting more than DeepSeek.

ccdb's other backends are one vendor's CLI each: Claude Code for Claude, Codex
for OpenAI. This one is a **harness rather than a vendor**: DeepSeek Harness
(DSH) composes its agent loop from plugins, and its LLM seam is pluggable, so
one runtime serves several providers under one set of tools, one session model,
and one place to configure. Claude Code and Codex stay exactly as they were;
this is the third harness, and the one that grows.

It drives the official Python SDK (``deepseek-harness-sdk``), which launches a
bundled DSH runtime and speaks newline-delimited JSON-RPC to it over stdio.

**Routes.** A provider is selected by the model name. ``deepseek-*`` uses the
DeepSeek adapter the runtime ships; ``glm-*`` uses the ``zai`` route that
``llm-pi-ai`` serves. Any model may also be written explicitly as
``route/model`` (``zai/glm-5.2``) when a prefix is ambiguous. Extra routes are
config, not code: ``~/.config/ccdb/dsh/providers.patch.yml`` is applied over the
bundled composition at boot, and adding a provider pi-ai already ships a catalog
for is one block in that file — see ``DEFAULT_PATCH_CONTENT``. A route naming a
provider pi-ai does not ship is declared outright in the same place.

Why not reuse the Codex backend, which already reaches OpenAI-compatible
endpoints? Measured on codex-cli 0.153.4, ``wire_api = "chat"`` is rejected
outright ("no longer supported"), and DeepSeek and Z.ai both serve Chat
Completions, so the Codex CLI can no longer be pointed at either.

Three measured properties of the SDK shape everything below.

* **A runtime outlives one turn.** A DSH session continues across turns only
  while the runtime that owns it is alive, and a runtime is initialised with one
  route, so this module keeps one runtime per (provider, model, working
  directory) for the bot's lifetime. Reusing a session id whose persisted log
  was written by an *earlier* process is refused ("already has a persisted log
  on disk that does not match this lineage"), so the runner mints a fresh
  session id whenever it is asked to continue one this process has never run. A
  restart therefore starts threads fresh instead of failing.
* **The SDK is synchronous.** ``Session.run()`` blocks until the agent is idle
  and returns committed events, so each turn runs on a worker thread and the
  notification callback streams into the async generator Discord consumes.
  Notifications for different sessions fan out into separate queues, so several
  threads can run against one runtime at once.
* **The runtime inherits the caller's environment.** ``HarnessClient`` builds
  the child environment as ``os.environ.copy()`` plus overrides, with no way to
  pass a replacement. Left alone, every secret the bot holds — including
  ``DISCORD_BOT_TOKEN`` — would be readable by the agent's own bash tool. This
  module therefore scrubs the transport credentials ccdb strips from every other
  backend out of the environment for the moment the runtime starts. Provider
  credentials (``DEEPSEEK_API_KEY``, ``ZAI_API_KEY``) deliberately survive: a
  route's ``apiKeyEnv`` names them, and the runtime resolves that reference
  itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import logging
import os
import threading
import uuid
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

from .child_env import STRIPPED_ENV_KEYS
from .types import (
    TOOL_CATEGORIES,
    ImageData,
    MessageType,
    StreamEvent,
    ToolCategory,
    ToolUseEvent,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "DEFAULT_PATCH_CONTENT",
    "MODEL_PROVIDER_PREFIXES",
    "PATCH_PATH",
    "SDK_EXTRA_HINT",
    "DshRunner",
    "dsh_sdk_available",
    "ensure_patch_file",
    "reset_runtimes",
    "resolve_provider",
    "shutdown_runtimes",
]

#: The route the DeepSeek adapter owns, and the default for an unknown model.
DEFAULT_PROVIDER = "deepseek-official"

#: The model the harness ships as its default.
DEFAULT_MODEL = "deepseek-v4-flash"

#: What to tell an operator whose install predates this backend's extra.
SDK_EXTRA_HINT = "uv sync --extra deepseek"

#: Model-id prefix → provider route. The first match wins, longest first, so a
#: future ``deepseek-chat`` and ``deepseek-v4-flash`` can diverge without a
#: table rewrite. A model written as ``route/model`` overrides this entirely.
MODEL_PROVIDER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("glm", "zai"),
    ("deepseek", DEFAULT_PROVIDER),
)

#: Reasoning effort levels the DeepSeek adapter accepts.
VALID_EFFORTS = frozenset({"low", "medium", "high"})

#: Where the extra-route patch layer lives (``CCDB_DSH_PATCH`` overrides it).
PATCH_PATH = Path(
    os.environ.get("CCDB_DSH_PATCH")
    or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "ccdb"
    / "dsh"
    / "providers.patch.yml"
)

#: Written on first use when no patch file exists, so a fresh install can run
#: GLM without anyone hand-writing YAML. DSH applies this as a patch layer over
#: its bundled composition: naming an id the composition already mounts
#: *overrides* that row's config, and inserting a second copy of the same id is
#: refused at boot ("duplicate loader entry id").
DEFAULT_PATCH_CONTENT = """\
# Extra model routes for the ccdb `dsh` backend.
#
# DSH boots a bundled composition (the `sdk` profile) and applies this file as a
# patch layer over it. The bundled profile already mounts
# `@deepseek-ai/dsh-llm-pi-ai` with no routes configured, so naming that id
# again is an override rather than an insert — a second copy is refused at boot.
#
# `zai` is a provider pi-ai ships a catalog for, so the route inherits its
# endpoint, wire protocol, and model list. Only the credential reference is
# ours; the key itself stays in the environment (`ZAI_API_KEY`).
#
# To add another provider: add its route key here and put the credential in the
# bot's environment. A provider pi-ai does not ship is declared outright with
# `api`, `baseURL`, and a `models` list instead.
- id: llm-pi-ai
  config:
    providers:
      zai:
        apiKeyEnv: ZAI_API_KEY
"""


#: Tool names DSH registers (lower-case, snake_case) mapped to the Claude-style
#: names ``TOOL_CATEGORIES`` and ``ToolUseEvent.display_name`` already know. The
#: argument names match too (``file_path``, ``command``, ``pattern``), so the
#: existing display strings work unchanged.
TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
    "read_image": "Read",
    "write": "Write",
    "edit": "Edit",
    "str_replace_editor": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "ls": "LS",
    "todo_write": "TodoWrite",
    "todo": "TodoWrite",
    "task": "Task",
    "subagent": "Task",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "ask_user_question": "AskUserQuestion",
}

#: The notifications this module turns into Discord-visible events.
_EVENT_ASSISTANT_MESSAGE = "assistant/message"
_EVENT_TOOL_CALL = "tool/call"
_EVENT_TOOL_RESULT = "tool/result"
_EVENT_TURN_END = "turn/end"


class _Done:
    """Sentinel pushed after a worker thread finishes its turn."""


_DONE = _Done()


#: The optional distribution's import name. It is imported dynamically so a
#: deployment that never selects this backend carries no SDK weight, and so CI
#: does not have to install a large platform wheel to type check.
SDK_MODULE = "deepseek_harness"


def dsh_sdk_available() -> bool:
    """Whether the optional DeepSeek Harness SDK is importable."""
    try:
        importlib.import_module(SDK_MODULE)
    except ImportError:
        return False
    return True


def _require_sdk() -> Any:
    """Import the SDK, or explain which extra installs it."""
    try:
        return importlib.import_module(SDK_MODULE)
    except ImportError as exc:
        raise RuntimeError(
            f"The DeepSeek backend needs the DeepSeek Harness SDK ({exc}). "
            f"Install it with `{SDK_EXTRA_HINT}`."
        ) from exc


def resolve_provider(model: str, explicit: str | None = None) -> tuple[str, str]:
    """Split a model selection into ``(provider_route, model_id)``.

    ``explicit`` (the stored per-backend provider, or ``provider/model``)
    always wins; otherwise the model's own prefix picks the route. An
    unrecognised model keeps the whole string and runs on the DeepSeek route,
    which is the harness's own adapter — the same "suggest, never reject"
    stance the rest of ccdb's model handling takes.
    """
    if explicit:
        return explicit, model
    if "/" in model:
        route, _, rest = model.partition("/")
        if route and rest:
            return route, rest
    lowered = model.lower()
    for prefix, provider in sorted(MODEL_PROVIDER_PREFIXES, key=lambda p: -len(p[0])):
        if lowered.startswith(prefix):
            return provider, model
    return DEFAULT_PROVIDER, model


def ensure_patch_file(path: Path | None = None) -> Path | None:
    """Write the extra-route patch layer if nobody has yet. Returns its path.

    A missing file is not an error: DSH boots its bundled composition and the
    DeepSeek route still works. Creating it on first use is what makes GLM (and
    every future route) available without hand-written YAML, while leaving the
    file editable afterwards.
    """
    target = PATCH_PATH if path is None else path
    try:
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_PATCH_CONTENT, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write the DSH route patch file %s: %s", target, exc)
        return target if target.exists() else None
    logger.info("Wrote the DSH route patch file to %s", target)
    return target


class _RuntimeEntry:
    """One lazily started DSH runtime plus the lock that starts it once."""

    def __init__(self) -> None:
        self.harness: Any = None
        self.lock = threading.Lock()


# Keyed by (model, provider, working dir). One runtime per key: the SDK fixes
# the provider/model route at handshake, so a runtime cannot serve two models.
_RUNTIMES: dict[tuple[str, str, str], _RuntimeEntry] = {}
_RUNTIMES_LOCK = threading.Lock()

# Session ids this process has established. Anything else — including an id
# loaded from the database that a previous process wrote — must be replaced,
# because the runtime refuses to adopt a foreign persisted log.
_LIVE_SESSIONS: set[str] = set()
_LIVE_SESSIONS_LOCK = threading.Lock()

# Guards the momentary environment scrub around runtime startup.
_ENVIRON_LOCK = threading.Lock()


def reset_runtimes() -> None:
    """Drop every runtime and session identity (tests, and full restarts)."""
    with _RUNTIMES_LOCK:
        entries = list(_RUNTIMES.values())
        _RUNTIMES.clear()
    with _LIVE_SESSIONS_LOCK:
        _LIVE_SESSIONS.clear()
    for entry in entries:
        harness = entry.harness
        if harness is not None:
            with contextlib.suppress(Exception):
                harness.close()


def shutdown_runtimes() -> None:
    """Close every runtime. Safe to call from a bot shutdown path."""
    reset_runtimes()


@contextlib.contextmanager
def _scrubbed_environ() -> Any:
    """Hide the relay's own credentials while a child environment is built.

    ``HarnessClient.start()`` copies ``os.environ`` wholesale, so the only lever
    is the process environment itself. Another thread spawning Claude or Codex
    during this window loses nothing: those runners strip the same keys anyway.
    """
    with _ENVIRON_LOCK:
        saved = {key: os.environ.pop(key) for key in STRIPPED_ENV_KEYS if key in os.environ}
        try:
            yield
        finally:
            os.environ.update(saved)


class DshRunner:
    """A ``SessionBackend`` that runs DeepSeek models on the DeepSeek Harness."""

    def __init__(
        self,
        *,
        command: str = "dsh",
        model: str | None = None,
        permission_mode: str = "default",
        working_dir: str | None = None,
        timeout_seconds: int = 300,
        dangerously_skip_permissions: bool = False,
        allowed_tools: list[str] | None = None,
        api_port: int | None = None,
        api_secret: str | None = None,
        thread_id: int | None = None,
        append_system_prompt: str | None = None,
        images: list[ImageData] | None = None,
        effort: str | None = None,
        provider: str | None = None,
        dsh_home: str | None = None,
        patch_path: Path | None = None,
        **_kwargs: object,
    ) -> None:
        # ``command`` exists because the SessionBackend protocol carries it and
        # the UI prints it; the SDK launches its own bundled runtime.
        self.command = command
        # The provider route follows the model unless one was named outright,
        # so `/model glm-5.2` and `/model zai/glm-5.2` both work.
        self.route, self.model = resolve_provider(model or DEFAULT_MODEL, provider)
        self.effort = effort
        self.permission_mode = permission_mode
        self.working_dir = working_dir
        self.timeout_seconds = timeout_seconds
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.allowed_tools = allowed_tools
        self.api_port = api_port
        self.api_secret = api_secret
        self.thread_id = thread_id
        self.append_system_prompt = append_system_prompt
        self.images = images
        self.dsh_home = dsh_home
        self._patch_path = patch_path
        self._active_session: str | None = None
        self._cancelled = threading.Event()
        self._turn_lock = threading.Lock()

    # ── Session identity ────────────────────────────────────

    def _session_for_turn(self, session_id: str | None) -> tuple[str, bool]:
        """The DSH session id for this turn, and whether it is a new session.

        An id this process has already established is continued. Anything else
        — a stored id from a previous process, a foreign id, or no id at all —
        becomes a fresh one, which the caller then stores because every event
        this runner yields carries it.
        """
        if session_id:
            with _LIVE_SESSIONS_LOCK:
                if session_id in _LIVE_SESSIONS:
                    return session_id, False
        fresh = f"ccdb-{uuid.uuid4().hex}"
        with _LIVE_SESSIONS_LOCK:
            _LIVE_SESSIONS.add(fresh)
        return fresh, True

    def _with_standing_instruction(self, prompt: str) -> str:
        """Lead the first turn of a session with the operator's instruction.

        The harness composition owns its own persona and the SDK exposes no
        ``developer_instructions`` equivalent, so a standing instruction has
        nowhere else to go. It is applied to the *first* turn only: later turns
        already carry it in the session's history, and this process starts a
        fresh session whenever it cannot continue the old one.
        """
        if not self.append_system_prompt:
            return prompt
        return f"{self.append_system_prompt.strip()}\n\n{prompt}"

    # ── Runtime lifecycle ───────────────────────────────────

    def _runtime_key(self) -> tuple[str, str, str]:
        return (self.route, self.model, self.working_dir or os.getcwd())

    def _ensure_runtime(self) -> Any:
        """Return the live runtime for this route and model, starting it once."""
        deepseek_harness = _require_sdk()
        key = self._runtime_key()
        with _RUNTIMES_LOCK:
            entry = _RUNTIMES.get(key)
            if entry is None:
                entry = _RuntimeEntry()
                _RUNTIMES[key] = entry

        with entry.lock:
            if entry.harness is not None:
                return entry.harness
            kwargs: dict[str, Any] = {
                "provider": self.route,
                "model": self.model,
                "cwd": self.working_dir or os.getcwd(),
            }
            patch_file = ensure_patch_file(self._patch_path)
            if patch_file is not None:
                kwargs["patches"] = (str(patch_file),)
            if self.effort:
                kwargs["reasoning_effort"] = self.effort
            if self.dsh_home:
                kwargs["dsh_home"] = self.dsh_home
            logger.info(
                "Starting DeepSeek Harness runtime (model=%s, cwd=%s)",
                self.model,
                kwargs["cwd"],
            )
            with _scrubbed_environ():
                harness = deepseek_harness.DeepSeekHarness(**kwargs)
                harness.start()
            entry.harness = harness
            return harness

    # ── Turn execution ──────────────────────────────────────

    def _drive(
        self,
        runtime: Any,
        session_id: str,
        prompt: str,
        on_notification: Callable[[Any], None],
        push: Callable[[Any], None],
    ) -> None:
        """Blocking worker: run one turn and hand its outcome back."""
        try:
            session = runtime.start_session(session_id)
            outcome = session.run(prompt, on_notification=on_notification)
        except BaseException as exc:  # noqa: BLE001 - reported as a stream event
            push(exc)
        else:
            push(outcome)
        finally:
            push(_DONE)

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run one turn and yield the events Discord renders."""
        dsh_session, is_new_session = self._session_for_turn(session_id)
        if not prompt.strip():
            yield self._error_event(dsh_session, "Empty prompt")
            return
        turn_prompt = self._with_standing_instruction(prompt) if is_new_session else prompt

        try:
            runtime = await asyncio.to_thread(self._ensure_runtime)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            logger.warning("DeepSeek Harness runtime unavailable: %s", exc)
            yield self._error_event(dsh_session, str(exc))
            return

        queue: asyncio.Queue[Any] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        self._cancelled.clear()
        with self._turn_lock:
            self._active_session = dsh_session

        def push(item: Any) -> None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, item)

        worker = asyncio.create_task(
            asyncio.to_thread(self._drive, runtime, dsh_session, turn_prompt, push, push)
        )

        error: str | None = None
        timed_out = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=self.timeout_seconds)
                except TimeoutError:
                    timed_out = True
                    break
                if item is _DONE:
                    break
                if isinstance(item, BaseException):
                    error = f"DeepSeek Harness run failed: {item}"
                    break
                for event in self._map_notification(item, dsh_session):
                    yield event
                if self._cancelled.is_set():
                    break
        finally:
            if timed_out:
                error = f"Timed out after {self.timeout_seconds} seconds"
                asyncio.ensure_future(self.interrupt())
            # Give the worker a bounded chance to unwind; a shared runtime means
            # there is no process to kill, and abandoning the thread is not a
            # licence to block the event loop waiting for it.
            await asyncio.wait({worker}, timeout=5.0)
            if not worker.done():
                worker.cancel()
            with self._turn_lock:
                self._active_session = None

        if self._cancelled.is_set() and error is None:
            error = "Stopped by the user"
        yield self._final_event(dsh_session, error=error)

    async def interrupt(self) -> None:
        """Ask the runtime to cancel the in-flight turn."""
        self._cancelled.set()
        session_id = self._active_session
        if not session_id:
            return
        try:
            runtime = await asyncio.to_thread(self._ensure_runtime)
        except Exception:  # noqa: BLE001 - nothing running to cancel
            return
        try:
            await asyncio.to_thread(
                runtime.client.request,
                "session/cancel",
                {"sessionId": session_id},
            )
        except Exception as exc:  # noqa: BLE001 - the turn still ends by itself
            logger.debug("DeepSeek cancel request failed: %s", exc)

    async def kill(self) -> None:
        """Stop the turn. The runtime is shared, so it is cancelled, not killed."""
        await self.interrupt()

    async def inject_tool_result(self, request_id: str, data: dict) -> None:
        """Not supported: the SDK runtime exposes no tool-approval channel."""
        logger.debug("DeepSeek backend ignores tool result for %s", request_id)

    # ── Event mapping ───────────────────────────────────────

    def _map_notification(self, notification: Any, session_id: str) -> list[StreamEvent]:
        """Translate one DSH notification into Discord-visible stream events."""
        if getattr(notification, "method", None) != "session.event":
            return []
        payload = getattr(notification, "payload", None)
        if not isinstance(payload, dict):
            return []
        # Subagent sessions report through the same subscription. Only the
        # thread's own session is the answer the user asked for.
        if payload.get("sessionId") != session_id:
            return []
        event = payload.get("event")
        if not isinstance(event, dict):
            return []
        data = event.get("data")
        if not isinstance(data, dict):
            return []

        kind = event.get("type")
        if kind == _EVENT_ASSISTANT_MESSAGE:
            return self._map_assistant_message(data, session_id)
        if kind == _EVENT_TOOL_CALL:
            return self._map_tool_call(data, session_id)
        if kind == _EVENT_TOOL_RESULT:
            return self._map_tool_result(data, session_id)
        if kind == _EVENT_TURN_END:
            return self._map_turn_end(data, session_id)
        return []

    def _map_assistant_message(self, data: dict, session_id: str) -> list[StreamEvent]:
        """Committed assistant text and reasoning.

        Tool-call blocks are skipped here: the session emits a ``tool/call``
        event for the same call, and rendering both would double every tool.
        """
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        events: list[StreamEvent] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text") or "")
                if text:
                    events.append(
                        StreamEvent(
                            raw=block,
                            message_type=MessageType.ASSISTANT,
                            session_id=session_id,
                            text=text,
                        )
                    )
            elif block_type == "reasoning":
                thinking = str(block.get("text") or "")
                if thinking:
                    events.append(
                        StreamEvent(
                            raw=block,
                            message_type=MessageType.ASSISTANT,
                            session_id=session_id,
                            thinking=thinking,
                        )
                    )
        return events

    def _map_tool_call(self, data: dict, session_id: str) -> list[StreamEvent]:
        raw_name = str(data.get("name") or "")
        if not raw_name:
            return []
        name = TOOL_NAME_ALIASES.get(raw_name, raw_name)
        return [
            StreamEvent(
                raw=data,
                message_type=MessageType.ASSISTANT,
                session_id=session_id,
                tool_use=ToolUseEvent(
                    tool_id=str(data.get("callId") or ""),
                    tool_name=name,
                    tool_input=_parse_tool_arguments(data.get("arguments")),
                    category=TOOL_CATEGORIES.get(name, ToolCategory.OTHER),
                ),
            )
        ]

    def _map_tool_result(self, data: dict, session_id: str) -> list[StreamEvent]:
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return []
        events: list[StreamEvent] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool-result":
                continue
            call_id = str(block.get("toolCallId") or "")
            text = _tool_result_text(block.get("content"))
            events.append(
                StreamEvent(
                    raw=block,
                    message_type=MessageType.USER,
                    session_id=session_id,
                    tool_result_id=call_id,
                    tool_result_content=text,
                )
            )
        return events

    def _map_turn_end(self, data: dict, session_id: str) -> list[StreamEvent]:
        reason = data.get("reason")
        kind = reason.get("kind") if isinstance(reason, dict) else None
        if kind == "error":
            detail = reason.get("error") if isinstance(reason, dict) else None
            message = detail.get("message") if isinstance(detail, dict) else None
            return [
                self._error_event(session_id, str(message or "DeepSeek Harness reported an error"))
            ]
        if kind == "max-tokens":
            return [
                StreamEvent(
                    raw=data,
                    message_type=MessageType.SYSTEM,
                    session_id=session_id,
                    text="DeepSeek Harness stopped at its output token limit.",
                )
            ]
        return []

    # ── Events emitted by this runner itself ────────────────

    def _final_event(self, session_id: str, *, error: str | None = None) -> StreamEvent:
        """The event that closes the turn."""
        if error:
            return self._error_event(session_id, error)
        return StreamEvent(
            raw={},
            message_type=MessageType.SYSTEM,
            session_id=session_id,
            is_complete=True,
        )

    def _error_event(self, session_id: str, error: str) -> StreamEvent:
        return StreamEvent(
            raw={},
            message_type=MessageType.RESULT,
            session_id=session_id,
            is_complete=True,
            error=error,
        )

    # ── Protocol surface ────────────────────────────────────

    def clone(
        self,
        model: str | None = None,
        working_dir: str | None = None,
        thread_id: int | None = None,
        effort: str | None = None,
        **_kwargs: object,
    ) -> DshRunner:
        """A fresh runner with the same configuration and no active turn."""
        return DshRunner(
            command=self.command,
            model=model if model is not None else self.model,
            provider=self.route,
            permission_mode=self.permission_mode,
            working_dir=working_dir if working_dir is not None else self.working_dir,
            timeout_seconds=self.timeout_seconds,
            dangerously_skip_permissions=self.dangerously_skip_permissions,
            allowed_tools=self.allowed_tools,
            api_port=self.api_port,
            api_secret=self.api_secret,
            thread_id=thread_id if thread_id is not None else self.thread_id,
            append_system_prompt=self.append_system_prompt,
            images=self.images,
            effort=effort if effort is not None else self.effort,
            dsh_home=self.dsh_home,
            patch_path=self._patch_path,
        )

    def _build_env(self) -> dict[str, str]:
        """The environment the runtime inherits.

        The SDK hands the child ``os.environ`` itself, so this reports the
        policy rather than enforcing it — ``_scrubbed_environ`` enforces it
        around startup.
        """
        return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV_KEYS}

    def describe_api(self) -> str:
        """One line naming this backend for the status surfaces."""
        return f"DeepSeek Harness ({self.route}/{self.model})"


def _parse_tool_arguments(raw: object) -> dict[str, Any]:
    """DSH sends tool arguments as a JSON string; the UI wants a mapping."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        with contextlib.suppress(json.JSONDecodeError, ValueError):
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
    return {}


def _tool_result_text(content: object) -> str:
    """Flatten a DSH tool-result content list into the text the UI shows."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)
