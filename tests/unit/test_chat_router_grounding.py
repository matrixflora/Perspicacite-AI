# tests/unit/test_chat_router_grounding.py
"""Verify the chat endpoint runs the grounding extractor and threads the
resulting context into the RAG mode invocation when no explicit context
field is provided by the client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_chat_endpoint_calls_grounding_extractor_with_prior_turn():
    """When messages history is present and request.context is None, the
    extractor should run with the last assistant message as `prior_excerpt`."""
    captured = {}

    async def fake_extract(**kwargs):
        captured.update(kwargs)
        return "LSD1 inhibitors in AML"

    with patch(
        "perspicacite.web.routers.chat.extract_grounding_context",
        side_effect=fake_extract,
    ), patch(
        "perspicacite.web.routers.chat._invoke_basic_rag",
        new_callable=AsyncMock,
    ) as fake_invoke:
        from perspicacite.web.routers.chat import (
            ChatMessage,
            ChatRequest,
            chat_endpoint,
        )
        request = ChatRequest(
            query="how does it work",
            messages=[
                ChatMessage(role="user", content="tell me about LSD1 inhibitors"),
                ChatMessage(role="assistant", content="LSD1 inhibitors target ..."),
            ],
            mode="basic",
            stream=False,
        )
        # The endpoint requires a FastAPI Request — we pass a stub.
        await chat_endpoint(request, raw_request=MagicMock())

    assert captured["prior_excerpt"] == "LSD1 inhibitors target ..."
    assert captured["query"] == "how does it work"
    assert fake_invoke.call_args.kwargs["context"] == "LSD1 inhibitors in AML"


@pytest.mark.asyncio
async def test_chat_endpoint_uses_explicit_context_when_provided():
    """If the client passes a non-empty `context`, skip the extractor."""
    with patch(
        "perspicacite.web.routers.chat.extract_grounding_context",
        new_callable=AsyncMock,
    ) as fake_extract, patch(
        "perspicacite.web.routers.chat._invoke_basic_rag",
        new_callable=AsyncMock,
    ) as fake_invoke:
        from perspicacite.web.routers.chat import (
            ChatMessage,
            ChatRequest,
            chat_endpoint,
        )
        request = ChatRequest(
            query="how does it work",
            messages=[
                ChatMessage(role="assistant", content="prior turn body"),
            ],
            context="explicit user context",
            mode="basic",
            stream=False,
        )
        await chat_endpoint(request, raw_request=MagicMock())

    fake_extract.assert_not_called()
    assert fake_invoke.call_args.kwargs["context"] == "explicit user context"


@pytest.mark.asyncio
async def test_chat_endpoint_first_turn_skips_extractor():
    with patch(
        "perspicacite.web.routers.chat.extract_grounding_context",
        new_callable=AsyncMock,
    ) as fake_extract, patch(
        "perspicacite.web.routers.chat._invoke_basic_rag",
        new_callable=AsyncMock,
    ) as fake_invoke:
        from perspicacite.web.routers.chat import (
            ChatRequest,
            chat_endpoint,
        )
        request = ChatRequest(
            query="what is mass spectrometry",
            messages=[],  # no prior turns
            mode="basic",
            stream=False,
        )
        await chat_endpoint(request, raw_request=MagicMock())

    fake_extract.assert_not_called()
    assert fake_invoke.call_args.kwargs["context"] is None
