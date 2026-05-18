# tests/unit/test_grounding_extractor.py
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.web.routers._grounding import extract_grounding_context


def _state(
    enabled: bool = True,
    timeout_s: float = 4.0,
    prior_cap: int = 200,
    query_cap: int = 200,
) -> MagicMock:
    state = MagicMock()
    state.config.search.query_optimization.enabled = True
    state.config.search.query_optimization.grounding_enabled = enabled
    state.config.search.query_optimization.grounding_timeout_s = timeout_s
    state.config.search.query_optimization.grounding_max_prior_chars = prior_cap
    state.config.search.query_optimization.grounding_max_query_chars = query_cap
    state.config.llm.default_provider = "anthropic"
    state.config.llm.default_model = "claude-haiku-4-5"
    state.config.llm.models = {}
    state.config.llm.providers_per_stage = {}
    state.llm_client = MagicMock()
    state.llm_client.complete = AsyncMock(
        return_value='{"context": "LSD1 inhibitors in AML"}'
    )
    return state


@pytest.mark.asyncio
async def test_skip_when_no_prior_turn():
    state = _state()
    result = await extract_grounding_context(
        prior_excerpt=None,
        query="what about clinical trials",
        app_state=state,
    )
    assert result is None
    state.llm_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_skip_when_grounding_disabled():
    state = _state(enabled=False)
    result = await extract_grounding_context(
        prior_excerpt="discussion of LSD1 inhibitors",
        query="how does it work",
        app_state=state,
    )
    assert result is None
    state.llm_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_skip_when_query_is_self_contained():
    """Long query with subject+verb pattern triggers short-circuit."""
    state = _state()
    long_query = (
        "Find papers on the role of LSD1 inhibitors in acute myeloid leukemia "
        "treatment and resistance mechanisms"
    )
    result = await extract_grounding_context(
        prior_excerpt="earlier discussion of something else",
        query=long_query,
        app_state=state,
    )
    assert result is None
    state.llm_client.complete.assert_not_called()


@pytest.mark.asyncio
async def test_extract_success_returns_context():
    state = _state()
    result = await extract_grounding_context(
        prior_excerpt="earlier we covered LSD1 inhibitors and AML",
        query="how does it work",
        app_state=state,
    )
    assert result == "LSD1 inhibitors in AML"
    state.llm_client.complete.assert_called_once()


@pytest.mark.asyncio
async def test_pivot_returns_none():
    state = _state()
    state.llm_client.complete = AsyncMock(return_value='{"context": ""}')
    result = await extract_grounding_context(
        prior_excerpt="earlier we covered LSD1 inhibitors and AML",
        query="how does it work",
        app_state=state,
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_error_returns_none():
    state = _state()
    state.llm_client.complete = AsyncMock(side_effect=RuntimeError("boom"))
    result = await extract_grounding_context(
        prior_excerpt="earlier we covered LSD1 inhibitors",
        query="how does it work",
        app_state=state,
    )
    assert result is None


@pytest.mark.asyncio
async def test_timeout_returns_none():
    state = _state(timeout_s=0.05)

    async def slow(**_kwargs):
        await asyncio.sleep(0.5)
        return '{"context": "..."}'

    state.llm_client.complete = AsyncMock(side_effect=slow)
    result = await extract_grounding_context(
        prior_excerpt="earlier we covered LSD1 inhibitors",
        query="how does it work",
        app_state=state,
    )
    assert result is None


@pytest.mark.asyncio
async def test_unparseable_returns_none():
    state = _state()
    state.llm_client.complete = AsyncMock(return_value="garbage")
    result = await extract_grounding_context(
        prior_excerpt="earlier we covered LSD1 inhibitors",
        query="how does it work",
        app_state=state,
    )
    assert result is None


@pytest.mark.asyncio
async def test_prior_excerpt_truncated_head_keep():
    state = _state(prior_cap=20)
    long_prior = "a" * 1000
    await extract_grounding_context(
        prior_excerpt=long_prior,
        query="how does it work",
        app_state=state,
    )
    body = state.llm_client.complete.call_args.kwargs["messages"][0]["content"]
    assert "a" * 20 in body
    assert "a" * 21 not in body


@pytest.mark.asyncio
async def test_query_truncated_head_keep():
    state = _state(query_cap=10)
    long_query = "b" * 500
    await extract_grounding_context(
        prior_excerpt="something",
        query=long_query,
        app_state=state,
    )
    body = state.llm_client.complete.call_args.kwargs["messages"][0]["content"]
    assert "b" * 10 in body
    assert "b" * 11 not in body
