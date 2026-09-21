"""E2 — the one-shot helper CLIs never inherit the bot's transport credentials.

``_quick_ai`` (the step-AI picker) and ``_interview_ai`` (the goal interview)
spawn ``claude -p`` directly. When the chat cog has no runner, or the runner's
``_build_env`` raises, the fallback used to be ``env=None`` — the whole bot
environment, ``DISCORD_BOT_TOKEN`` and ``CCDB_API_SECRET`` included. The
fallback is now the same stripped environment every session gets, and the
picker (a text-in, text-out call) runs with no settings, no tools and its own
empty working folder.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from claude_code_core.child_env import STRIPPED_ENV_KEYS
from claude_code_core.loop_store import LoopStore
from claude_discord.cogs.task_loop import TaskLoopCog


class _Proc:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"B\n", b""

    def kill(self) -> None:  # pragma: no cover - never reached
        pass


def _cog(runner: Any) -> TaskLoopCog:
    bot = MagicMock()
    chat = MagicMock()
    chat.runner = runner
    bot.cogs = {"ClaudeChatCog": chat}
    tmp = Path(tempfile.mkdtemp(prefix="gowork-env-"))
    return TaskLoopCog(bot, work_root=tmp / "copies", store=LoopStore(tmp / "loops.json"))


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bot-token-must-not-leak")
    monkeypatch.setenv("CCDB_API_SECRET", "api-secret-must-not-leak")
    monkeypatch.setenv("CCDB_API_URL", "http://127.0.0.1:1/")
    monkeypatch.setenv("PATH_MARKER_FOR_TEST", "kept")


async def _spawn(cog: TaskLoopCog, method: str, *args: Any) -> dict[str, Any]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_exec(*a: Any, **kw: Any) -> _Proc:
        cwd = kw.get("cwd")
        if cwd:  # looked at now: a scratch folder is gone again once the call ends
            kw["cwd_was_empty_dir"] = Path(cwd).is_dir() and not any(Path(cwd).iterdir())
        calls.append((a, kw))
        return _Proc()

    with (
        patch("claude_discord.cogs.task_loop.asyncio.create_subprocess_exec", fake_exec),
        patch("claude_discord.cogs.task_loop.shutil.which", return_value="claude"),
    ):
        out = await getattr(cog, method)(*args)
    assert out == "B"
    assert len(calls) == 1
    argv, kwargs = calls[0]
    return {"argv": list(argv), **kwargs}


@pytest.mark.usefixtures("secrets")
async def test_quick_ai_with_no_runner_strips_the_bot_credentials() -> None:
    call = await _spawn(_cog(None), "_quick_ai", "pick one")

    env = call["env"]
    assert env is not None, "env=None would hand the child the whole bot environment"
    assert not STRIPPED_ENV_KEYS & set(env)
    assert env.get("PATH_MARKER_FOR_TEST") == "kept"  # the rest of the environment survives


@pytest.mark.usefixtures("secrets")
async def test_quick_ai_when_build_env_raises_strips_the_bot_credentials() -> None:
    runner = MagicMock()
    runner._build_env.side_effect = RuntimeError("bad CODEX_HOME")

    call = await _spawn(_cog(runner), "_quick_ai", "pick one")

    assert not STRIPPED_ENV_KEYS & set(call["env"])


@pytest.mark.usefixtures("secrets")
async def test_quick_ai_uses_the_runners_env_when_it_has_one() -> None:
    runner = MagicMock()
    runner._build_env.return_value = {"PATH": "x", "FROM_RUNNER": "1"}

    call = await _spawn(_cog(runner), "_quick_ai", "pick one")

    assert call["env"] == {"PATH": "x", "FROM_RUNNER": "1"}


@pytest.mark.usefixtures("secrets")
async def test_quick_ai_runs_with_no_settings_no_tools_and_its_own_folder() -> None:
    call = await _spawn(_cog(None), "_quick_ai", "pick one")

    argv = call["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv
    disallowed = argv[argv.index("--disallowedTools") + 1 : argv.index("--")]
    assert {"Bash", "Edit", "Write", "Read", "WebFetch"} <= set(disallowed)
    assert argv[-2:] == ["--", "pick one"]
    assert call.get("cwd") and call["cwd_was_empty_dir"]
    assert not Path(call["cwd"]).exists(), "the scratch folder is removed afterwards"


@pytest.mark.usefixtures("secrets")
async def test_interview_ai_with_no_runner_strips_the_bot_credentials(tmp_path: Path) -> None:
    call = await _spawn(_cog(None), "_interview_ai", "agree the goal", tmp_path)

    assert call["env"] is not None
    assert not STRIPPED_ENV_KEYS & set(call["env"])
    assert call["cwd"] == str(tmp_path)
    assert call["argv"][-2:] == ["--", "agree the goal"]


@pytest.mark.usefixtures("secrets")
async def test_interview_ai_when_build_env_raises_strips_the_bot_credentials(
    tmp_path: Path,
) -> None:
    runner = MagicMock()
    runner._build_env.side_effect = RuntimeError("bad CODEX_HOME")

    call = await _spawn(_cog(runner), "_interview_ai", "agree the goal", tmp_path)

    assert not STRIPPED_ENV_KEYS & set(call["env"])
