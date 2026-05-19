"""Verify AsyncLLMClient emits tokens + cost telemetry events via a sink."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.rag.telemetry import ListTelemetrySink


def _mock_config() -> LLMConfig:
    return LLMConfig(
        default_provider="deepseek",
        default_model="deepseek-chat",
        providers={
            "deepseek": LLMProviderConfig(
                api_key_env="DEEPSEEK_API_KEY",
                base_url="https://api.deepseek.com",
                timeout=30,
            ),
        },
    )


def _mock_response(text: str = "ok", pt: int = 10, ct: int = 5) -> SimpleNamespace:
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg)
    usage = {"prompt_tokens": pt, "completion_tokens": ct}
    resp = SimpleNamespace(choices=[choice], usage=usage)
    resp.get = lambda k, default=None: usage if k == "usage" else default  # type: ignore[attr-defined]
    return resp


@pytest.mark.asyncio
async def test_complete_emits_tokens_event_via_sink() -> None:
    """A telemetry sink passed via `sink=` receives a tokens event."""
    client = AsyncLLMClient(_mock_config())
    sink = ListTelemetrySink()
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi", 12, 7))
        # completion_cost may or may not be present — force a value we control
        litellm.completion_cost = MagicMock(return_value=0.0123)
        get_litellm.return_value = litellm
        # Also patch the module-level litellm import used by _safe_completion_cost
        with patch("litellm.completion_cost", return_value=0.0123):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-chat",
                provider="deepseek",
                sink=sink,
                cache=False,
            )

    kinds = [ev.get("kind") for ev in sink.events]
    assert "tokens" in kinds, f"expected tokens event, got {kinds}"
    assert "cost_estimate" in kinds, f"expected cost_estimate event, got {kinds}"
    tok = next(e for e in sink.events if e.get("kind") == "tokens")
    assert tok["in"] == 12
    assert tok["out"] == 7
    cost = next(e for e in sink.events if e.get("kind") == "cost_estimate")
    assert cost["model"] == "deepseek-chat"
    # Cost may be 0.0 if litellm pricing tables don't recognize the model;
    # we only require the field shape, not a specific value.
    assert isinstance(cost["usd"], float)


@pytest.mark.asyncio
async def test_complete_without_sink_does_not_raise() -> None:
    """Omitting `sink=` is the default — must not break completion."""
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi", 3, 1))
        get_litellm.return_value = litellm
        out = await client.complete(
            messages=[{"role": "user", "content": "x"}],
            model="deepseek-chat",
            provider="deepseek",
            cache=False,
        )
    assert out == "hi"


@pytest.mark.asyncio
async def test_cost_lookup_failure_emits_zero_cost() -> None:
    """When litellm.completion_cost raises, telemetry still emits cost=0.0."""
    client = AsyncLLMClient(_mock_config())
    sink = ListTelemetrySink()
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi", 4, 2))
        get_litellm.return_value = litellm
        with patch("litellm.completion_cost", side_effect=Exception("no pricing")):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-chat",
                provider="deepseek",
                sink=sink,
                cache=False,
            )

    cost = next(e for e in sink.events if e.get("kind") == "cost_estimate")
    assert cost["usd"] == 0.0
