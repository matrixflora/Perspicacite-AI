"""End-to-end tests for AsyncLLMClient ↔ LLMResponseCache wiring (Wave 2.1)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.client import AsyncLLMClient


def _mk_config(tmp_path: Path, *, enabled: bool = True) -> LLMConfig:
    """Build an LLMConfig pointing the cache at a tmp file."""
    return LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=enabled,
        cache_path=tmp_path / "test_cache.db",
        cache_ttl_hours=24,
    )


def _mock_litellm_response(text: str):
    """Build a fake LiteLLM response object."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.get = MagicMock(
        side_effect=lambda k, d=None: {"usage": {"prompt_tokens": 10,
                                                  "completion_tokens": 5}}.get(k, d)
    )
    return response


@pytest.mark.asyncio
async def test_first_call_hits_provider_second_call_returns_cached(tmp_path):
    """The core invariant: re-asking the same prompt skips the network."""
    client = AsyncLLMClient(_mk_config(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    fake_acompletion = AsyncMock(return_value=_mock_litellm_response("hello!"))
    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = fake_acompletion
        mock_get.return_value = mock_litellm

        r1 = await client.complete(messages=messages, temperature=0.0)
        r2 = await client.complete(messages=messages, temperature=0.0)

    assert r1 == "hello!"
    assert r2 == "hello!"
    # The provider was only called once — the second call was served
    # from cache.
    assert fake_acompletion.call_count == 1


@pytest.mark.asyncio
async def test_cache_false_bypasses_both_read_and_write(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    fake_acompletion = AsyncMock(return_value=_mock_litellm_response("uncached"))
    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = fake_acompletion
        mock_get.return_value = mock_litellm

        await client.complete(messages=messages, temperature=0.0, cache=False)
        await client.complete(messages=messages, temperature=0.0, cache=False)

    # Both calls hit the provider because the cache was bypassed.
    assert fake_acompletion.call_count == 2


@pytest.mark.asyncio
async def test_cache_disabled_globally_doesnt_touch_db(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path, enabled=False))
    messages = [{"role": "user", "content": "hi"}]

    fake_acompletion = AsyncMock(return_value=_mock_litellm_response("nope"))
    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = fake_acompletion
        mock_get.return_value = mock_litellm

        await client.complete(messages=messages, temperature=0.0)
        await client.complete(messages=messages, temperature=0.0)

    # No cache → both calls hit the provider.
    assert fake_acompletion.call_count == 2
    # The DB file should not have been created when cache_enabled=False.
    assert not (tmp_path / "test_cache.db").exists()


@pytest.mark.asyncio
async def test_different_temperatures_are_separate_entries(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    responses = iter(["t0", "t07"])

    async def fake_call(*args, **kwargs):
        return _mock_litellm_response(next(responses))

    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(side_effect=fake_call)
        mock_get.return_value = mock_litellm

        r1 = await client.complete(messages=messages, temperature=0.0)
        r2 = await client.complete(messages=messages, temperature=0.7)

    assert r1 == "t0"
    assert r2 == "t07"
