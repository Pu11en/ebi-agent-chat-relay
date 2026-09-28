"""GLM reuses Claude Code without redirecting unrelated Claude sessions."""

from __future__ import annotations

import os

import pytest

from claude_code_core.runner import ClaudeRunner
from claude_discord.cogs.event_processor import _backend_name_from_runner
from tests.test_backend_factory_codex_defaults import _factory


def test_glm_factory_reuses_claude_and_preserves_route_on_clone(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    runner = _factory(append_system_prompt="shared rules").build(backend="glm")
    assert isinstance(runner, ClaudeRunner)
    assert runner.model == "glm-5.3"
    clone = runner.clone(thread_id=12, working_dir="/tmp/project", model="glm-5.3-flash")
    assert _backend_name_from_runner(clone) == "glm"
    assert clone.append_system_prompt == "shared rules"
    assert clone.working_dir == "/tmp/project"
    assert clone._build_env()["ANTHROPIC_AUTH_TOKEN"] == "test-zai-key"
    assert clone._build_env()["ANTHROPIC_BASE_URL"] == "https://api.z.ai/api/anthropic"
    assert "test-zai-key" not in str(clone._build_args("hello", None))
    assert clone.describe_api() == "Z.ai Coding Plan (Claude Code)"


def test_glm_does_not_redirect_claude_or_inherit_another_provider(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    before = dict(os.environ)
    env = _factory().build(backend="glm")._build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "glm-5.3"
    assert dict(os.environ) == before
    assert _factory().build(backend="claude")._build_env()["ANTHROPIC_BASE_URL"] == (
        "https://example.invalid"
    )


def test_glm_missing_key_fails_clearly_without_falling_back(monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ZAI_API_KEY"):
        _factory().build(backend="glm")._build_env()
