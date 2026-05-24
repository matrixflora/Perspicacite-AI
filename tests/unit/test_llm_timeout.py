"""Unit tests for global LiteLLM timeout (Issue 1 — three-tier policy)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_config(default_timeout_s: float | None = None) -> MagicMock:
    """Build a minimal LLMConfig-like mock."""
    cfg = MagicMock()
    cfg.default_provider = "deepseek"
    cfg.default_model = "deepseek-chat"
    cfg.providers = {
        "deepseek": MagicMock(
            timeout=None,   # no provider-level timeout set
            base_url="",
            max_retries=1,
            executable=None,
        )
    }
    cfg.free_tier_fallback_models = []
    cfg.free_auto_mode = False
    cfg.use_mcp_sampling = False
    cfg.cache_enabled = False
    if default_timeout_s is not None:
        cfg.default_timeout_s = default_timeout_s
    else:
        del cfg.default_timeout_s   # attribute absent
    return cfg


@pytest.mark.asyncio
async def test_global_timeout_fallback_applied_when_no_provider_timeout():
    """When provider.timeout is None and no kwargs timeout, DEFAULT_LLM_TIMEOUT_S is used."""
    from perspicacite.llm.client import AsyncLLMClient, DEFAULT_LLM_TIMEOUT_S

    cfg = _make_config()
    client = AsyncLLMClient(cfg)

    captured_kwargs: dict = {}

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="ok"))]
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        resp.model = "deepseek/deepseek-chat"
        resp.get = lambda k, d=None: {"usage": resp.usage}.get(k, d)
        return resp

    litellm_mock = MagicMock()
    litellm_mock.acompletion = AsyncMock(side_effect=fake_acompletion)

    with patch.object(client, "_get_litellm", return_value=litellm_mock):
        with patch.object(client, "_get_provider_config") as mock_get_cfg:
            mock_get_cfg.return_value = MagicMock(timeout=None, base_url="", max_retries=1)
            with patch.object(client, "_build_model_string", return_value="deepseek/deepseek-chat"):
                with patch.object(client, "_is_agent_cli_provider", return_value=False):
                    await client.complete(
                        messages=[{"role": "user", "content": "hello"}],
                        model="deepseek-chat",
                        provider="deepseek",
                    )

    assert captured_kwargs.get("timeout") == DEFAULT_LLM_TIMEOUT_S


@pytest.mark.asyncio
async def test_config_level_timeout_overrides_default():
    """llm.default_timeout_s in config replaces the code constant."""
    from perspicacite.llm.client import AsyncLLMClient

    cfg = _make_config(default_timeout_s=120.0)
    client = AsyncLLMClient(cfg)

    captured_kwargs: dict = {}

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="ok"))]
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        resp.model = "deepseek/deepseek-chat"
        resp.get = lambda k, d=None: {"usage": resp.usage}.get(k, d)
        return resp

    litellm_mock = MagicMock()
    litellm_mock.acompletion = AsyncMock(side_effect=fake_acompletion)

    with patch.object(client, "_get_litellm", return_value=litellm_mock):
        with patch.object(client, "_get_provider_config") as mock_get_cfg:
            mock_get_cfg.return_value = MagicMock(timeout=None, base_url="", max_retries=1)
            with patch.object(client, "_build_model_string", return_value="deepseek/deepseek-chat"):
                with patch.object(client, "_is_agent_cli_provider", return_value=False):
                    await client.complete(
                        messages=[{"role": "user", "content": "hello"}],
                        model="deepseek-chat",
                        provider="deepseek",
                    )

    assert captured_kwargs.get("timeout") == 120.0


@pytest.mark.asyncio
async def test_per_call_kwarg_timeout_wins_over_all():
    """Explicit timeout=N kwarg always takes precedence."""
    from perspicacite.llm.client import AsyncLLMClient

    cfg = _make_config(default_timeout_s=120.0)
    client = AsyncLLMClient(cfg)

    captured_kwargs: dict = {}

    async def fake_acompletion(**kwargs):
        captured_kwargs.update(kwargs)
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="ok"))]
        resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        resp.model = "deepseek/deepseek-chat"
        resp.get = lambda k, d=None: {"usage": resp.usage}.get(k, d)
        return resp

    litellm_mock = MagicMock()
    litellm_mock.acompletion = AsyncMock(side_effect=fake_acompletion)

    with patch.object(client, "_get_litellm", return_value=litellm_mock):
        with patch.object(client, "_get_provider_config") as mock_get_cfg:
            mock_get_cfg.return_value = MagicMock(timeout=None, base_url="", max_retries=1)
            with patch.object(client, "_build_model_string", return_value="deepseek/deepseek-chat"):
                with patch.object(client, "_is_agent_cli_provider", return_value=False):
                    await client.complete(
                        messages=[{"role": "user", "content": "hello"}],
                        model="deepseek-chat",
                        provider="deepseek",
                        timeout=30.0,   # explicit per-call kwarg
                    )

    assert captured_kwargs.get("timeout") == 30.0
