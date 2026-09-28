"""GLM discovery uses subscription metadata, never model generation or DSH."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from claude_discord import model_catalog
from tests.test_backend_command_session_clear import _make_cog
from tests.test_switch_command import _settings


@pytest.fixture(autouse=True)
def reset_catalog(monkeypatch):
    monkeypatch.setattr(model_catalog, "_glm_cache", None, raising=False)
    monkeypatch.setattr(model_catalog, "_glm_cache_lock", None, raising=False)


async def test_subscription_catalog_cached_and_fallback_models_ranked_first(monkeypatch):
    get = Mock(
        return_value={
            "data": [
                {"id": "glm-4.7"},
                {"id": "glm-5.3"},
                {"id": "glm-5.3-flashx"},
                {"id": "glm-5.3"},
                {"id": "embedding"},
                {"id": None},
            ]
        }
    )
    monkeypatch.setattr(model_catalog, "_get_json", get)
    fallback = [("glm-5.3", "preferred")]
    choices = await model_catalog.glm_model_choices(fallback=fallback, env={"ZAI_API_KEY": "fake"})
    assert [value for value, _ in choices] == ["glm-5.3", "glm-4.7", "glm-5.3-flashx"]
    assert (
        await model_catalog.glm_model_choices(fallback=fallback, env={"ZAI_API_KEY": "fake"})
        == choices
    )
    get.assert_called_once_with(
        "https://api.z.ai/api/coding/paas/v4/models",
        {"Authorization": "Bearer fake", "accept": "application/json"},
        model_catalog.REQUEST_TIMEOUT_SECONDS,
    )


@pytest.mark.parametrize("env", [{}, {"ZAI_API_KEY": "fake", "CCDB_MODEL_DISCOVERY": "0"}])
async def test_unconfigured_or_disabled_never_calls_network(monkeypatch, env):
    get = Mock(side_effect=AssertionError("network not allowed"))
    monkeypatch.setattr(model_catalog, "_get_json", get)
    fallback = [("glm-5.3", "fallback")]
    assert await model_catalog.glm_model_choices(fallback=fallback, env=env) == fallback
    get.assert_not_called()


@pytest.mark.parametrize("payload", [{"data": None}, {"data": []}, None])
async def test_malformed_metadata_falls_back(monkeypatch, payload):
    monkeypatch.setattr(model_catalog, "_get_json", Mock(return_value=payload))
    fallback = [("glm-5.3", "fallback")]
    assert (
        await model_catalog.glm_model_choices(fallback=fallback, env={"ZAI_API_KEY": "fake"})
        == fallback
    )


async def test_failure_is_cached_without_logging_exception_secret(monkeypatch, caplog):
    get = Mock(side_effect=RuntimeError("secret-for-test"))
    monkeypatch.setattr(model_catalog, "_get_json", get)
    for _ in range(2):
        assert await model_catalog.glm_model_choices(fallback=[], env={"ZAI_API_KEY": "fake"}) == []
    get.assert_called_once()
    assert "secret-for-test" not in caplog.text


async def test_switch_uses_complete_glm_catalog(monkeypatch):
    monkeypatch.setenv("CCDB_ENABLED_BACKENDS", "glm")
    choices = [("glm-5.3-flashx", "subscription")]
    with patch(
        "claude_discord.cogs.backend_command.glm_model_choices", AsyncMock(return_value=choices)
    ):
        assert await _make_cog(await _settings())._switch_catalog() == {"glm": choices}
