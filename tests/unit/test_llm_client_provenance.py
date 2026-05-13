"""Tests for provenance recording in AsyncLLMClient."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting


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


def _mock_response(text: str = "hi", pt: int = 4, ct: int = 2) -> SimpleNamespace:
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg)
    usage = {"prompt_tokens": pt, "completion_tokens": ct}
    resp = SimpleNamespace(choices=[choice], usage=usage)
    # Code reads `response.get("usage", {})` so wrap with .get
    resp.get = lambda k, default=None: usage if k == "usage" else default  # type: ignore[attr-defined]
    return resp


@pytest.mark.asyncio
async def test_llm_client_records_when_collector_set() -> None:
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hello", 10, 5))
        get_litellm.return_value = litellm
        c = ProvenanceCollector(
            conversation_id="c", message_id="m", rag_mode="basic", request_params={}
        )
        with collecting(c):
            out = await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-chat", provider="deepseek",
                stage="basic.answer",
            )
        assert out == "hello"
        assert len(c.llm_calls) == 1
        rec = c.llm_calls[0]
        assert rec.stage_label == "basic.answer"
        assert rec.provider == "deepseek"
        assert rec.prompt_tokens == 10
        assert rec.completion_tokens == 5
        assert rec.response_text == "hello"


@pytest.mark.asyncio
async def test_llm_client_no_recording_without_collector() -> None:
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi"))
        get_litellm.return_value = litellm
        out = await client.complete(
            messages=[{"role": "user", "content": "x"}],
            model="deepseek-chat", provider="deepseek",
        )
        assert out == "hi"


@pytest.mark.asyncio
async def test_llm_client_stage_kwarg_is_optional() -> None:
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi"))
        get_litellm.return_value = litellm
        c = ProvenanceCollector(
            conversation_id=None, message_id="m", rag_mode="basic", request_params={}
        )
        with collecting(c):
            await client.complete(
                messages=[{"role": "user", "content": "x"}],
                model="deepseek-chat", provider="deepseek",
            )
        assert c.llm_calls[0].stage_label == "llm"


@pytest.mark.asyncio
async def test_llm_client_stream_records_when_collector_set() -> None:
    """Streaming should accumulate chunks and record a single LLMCallRecord."""
    client = AsyncLLMClient(_mock_config())

    async def fake_acompletion(**kwargs: object) -> object:
        class FakeChunk:
            class Choice:
                class Delta:
                    content: str = ""

                delta = Delta()

            choices: ClassVar = [Choice()]

        async def gen() -> object:
            for piece in ["Hello", " world"]:
                chunk = FakeChunk()
                chunk.choices[0].delta.content = piece
                yield chunk

        return gen()

    with patch.object(client, "_get_litellm") as get_litellm:
        litellm_mock = MagicMock()
        litellm_mock.acompletion = fake_acompletion
        get_litellm.return_value = litellm_mock

        c = ProvenanceCollector(
            conversation_id="c", message_id="m", rag_mode="basic", request_params={}
        )
        chunks: list[str] = []
        with collecting(c):
            async for piece in client.stream(
                messages=[{"role": "user", "content": "hi"}],
                model="deepseek-chat",
                provider="deepseek",
                stage="basic.stream",
            ):
                chunks.append(piece)

    assert chunks == ["Hello", " world"]
    assert len(c.llm_calls) == 1
    rec = c.llm_calls[0]
    assert rec.stage_label == "basic.stream"
    assert rec.response_text == "Hello world"
    assert rec.provider == "deepseek"


@pytest.mark.asyncio
async def test_llm_client_stream_stage_kwarg_is_optional() -> None:
    """stage kwarg defaults to 'llm' for streaming too."""
    client = AsyncLLMClient(_mock_config())

    async def fake_acompletion(**kwargs: object) -> object:
        class FakeChunk:
            class Choice:
                class Delta:
                    content = "ok"

                delta = Delta()

            choices: ClassVar = [Choice()]

        async def gen() -> object:
            yield FakeChunk()

        return gen()

    with patch.object(client, "_get_litellm") as get_litellm:
        litellm_mock = MagicMock()
        litellm_mock.acompletion = fake_acompletion
        get_litellm.return_value = litellm_mock

        c = ProvenanceCollector(
            conversation_id=None, message_id="m", rag_mode="basic", request_params={}
        )
        with collecting(c):
            async for _ in client.stream(
                messages=[{"role": "user", "content": "x"}],
                model="deepseek-chat",
                provider="deepseek",
            ):
                pass

    assert c.llm_calls[0].stage_label == "llm"
