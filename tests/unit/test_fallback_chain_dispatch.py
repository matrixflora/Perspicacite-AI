"""Verify complete_with_chain advances on failure (Wave 3.2)."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.budget import (
    BudgetExceededError,
)
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.errors import RateLimitError


def _client(tmp_path: Path) -> AsyncLLMClient:
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    return AsyncLLMClient(cfg)


@pytest.mark.asyncio
async def test_chain_returns_first_success(tmp_path):
    client = _client(tmp_path)
    fake = AsyncMock(return_value="success!")
    with patch.object(client, "complete", new=fake):
        out = await client.complete_with_chain(
            messages=[{"role": "user", "content": "hi"}],
            chain=[("anthropic", "claude-haiku-4-5"),
                   ("claude_cli", "sonnet")],
        )
    assert out == "success!"
    # Only the first provider was called.
    assert fake.call_count == 1


@pytest.mark.asyncio
async def test_chain_falls_through_on_rate_limit(tmp_path):
    client = _client(tmp_path)

    calls = {"n": 0}

    async def fake_complete(messages, model=None, provider=None, **kw):
        calls["n"] += 1
        if provider == "anthropic":
            raise RateLimitError("anthropic limited", provider="anthropic")
        return f"from-{provider}"

    with patch.object(client, "complete", new=fake_complete):
        out = await client.complete_with_chain(
            messages=[{"role": "user", "content": "x"}],
            chain=[("anthropic", "claude-haiku-4-5"),
                   ("claude_cli", "sonnet")],
        )
    assert out == "from-claude_cli"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_chain_falls_through_on_generic_exception(tmp_path):
    client = _client(tmp_path)

    async def fake_complete(messages, model=None, provider=None, **kw):
        if provider == "anthropic":
            raise RuntimeError("transient network error")
        return "ok"

    with patch.object(client, "complete", new=fake_complete):
        out = await client.complete_with_chain(
            messages=[{"role": "user", "content": "x"}],
            chain=[("anthropic", "claude-haiku-4-5"),
                   ("deepseek", "deepseek-chat")],
        )
    assert out == "ok"


@pytest.mark.asyncio
async def test_chain_raises_last_exception_when_all_fail(tmp_path):
    client = _client(tmp_path)

    async def fake_complete(messages, model=None, provider=None, **kw):
        raise RateLimitError(f"{provider} limited", provider=provider)

    with patch.object(client, "complete", new=fake_complete):
        with pytest.raises(RateLimitError) as exc:
            await client.complete_with_chain(
                messages=[{"role": "user", "content": "x"}],
                chain=[("anthropic", "claude-haiku-4-5"),
                       ("claude_cli", "sonnet")],
            )
    # The last exception raised should be from the final provider.
    assert exc.value.provider == "claude_cli"


@pytest.mark.asyncio
async def test_chain_short_circuits_on_budget_exceeded(tmp_path):
    client = _client(tmp_path)

    async def fake_complete(messages, model=None, provider=None, **kw):
        raise BudgetExceededError("over budget")

    with patch.object(client, "complete", new=fake_complete):
        with pytest.raises(BudgetExceededError):
            await client.complete_with_chain(
                messages=[{"role": "user", "content": "x"}],
                chain=[("anthropic", "claude-haiku-4-5"),
                       ("claude_cli", "sonnet")],
            )


@pytest.mark.asyncio
async def test_empty_chain_raises_value_error(tmp_path):
    client = _client(tmp_path)
    with pytest.raises(ValueError):
        await client.complete_with_chain(
            messages=[{"role": "user", "content": "x"}],
            chain=[],
        )
