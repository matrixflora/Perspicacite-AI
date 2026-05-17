"""Verify LiteLLM rate-limit exceptions are re-raised as our type (Wave 3.1)."""
from unittest.mock import MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.errors import RateLimitError


@pytest.mark.asyncio
async def test_litellm_rate_limit_exception_wrapped(tmp_path):
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    client = AsyncLLMClient(cfg)

    # Build a fake litellm.exceptions.RateLimitError-like exception.
    class FakeRateLimit(Exception):
        pass
    FakeRateLimit.__name__ = "RateLimitError"
    FakeRateLimit.__module__ = "litellm.exceptions"

    async def boom(*args, **kwargs):
        raise FakeRateLimit("rate limit reached")

    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock()
        litellm.acompletion = boom
        mock_get.return_value = litellm
        with pytest.raises(RateLimitError) as exc:
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                cache=False,
            )
    assert exc.value.provider == "anthropic"
