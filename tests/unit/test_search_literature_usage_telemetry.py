"""search_literature must populate the response ``usage`` block from the
telemetry the query optimizer's LLM call emits into the response collector.

The optimizer's ``complete`` call receives the response-level collector as its
``sink``; the LLM client emits ``tokens`` / ``cost_estimate`` events into it.
``ResponseMetadataCollector.as_response_extras()`` then surfaces a ``usage``
block in the final payload. When optimization is disabled (no LLM call), no
telemetry flows and ``usage`` is absent.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server
from perspicacite.rag.telemetry import emit_cost, emit_tokens


def _state(optimize_enabled: bool = True):
    s = MagicMock()
    s.session_store = MagicMock()
    s.config = MagicMock()
    s.config.search.query_optimization.enabled = optimize_enabled
    s.config.search.query_optimization.timeout_s = 5.0
    s.config.search.query_optimization.max_context_chars = 300
    s.config.llm.default_provider = "anthropic"
    s.config.llm.default_model = "claude-haiku-4-5"
    s.config.llm.models = {}
    s.config.llm.providers_per_stage = {}

    async def _complete(**kwargs):
        sink = kwargs.get("sink")
        emit_tokens(sink, input_tokens=10, output_tokens=5, model="x")
        emit_cost(sink, usd=0.001, model="x")
        return '{"searched_query": "rewritten"}'

    s.llm_client = MagicMock()
    s.llm_client.complete = AsyncMock(side_effect=_complete)
    return s


def _stub_aggregator() -> MagicMock:
    async def _fake_search(*args, **kwargs):
        return []

    agg = MagicMock()
    agg.available = True
    agg.search = AsyncMock(side_effect=_fake_search)
    agg.last_errors_by_database = {}
    agg._providers = []
    return agg


@pytest.mark.asyncio
async def test_usage_populated_when_optimization_emits_telemetry():
    state = _state(optimize_enabled=True)
    agg = _stub_aggregator()

    with patch.object(mcp_server, "_require_state", return_value=state), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=agg,
    ):
        raw = await mcp_server.search_literature(
            query="x", max_results=5, optimize_query=True,
        )

    payload = json.loads(raw)
    assert "usage" in payload
    assert payload["usage"]["tokens_in"] == 10
    assert payload["usage"]["tokens_out"] == 5


@pytest.mark.asyncio
async def test_usage_absent_when_optimization_disabled():
    state = _state(optimize_enabled=False)
    agg = _stub_aggregator()

    with patch.object(mcp_server, "_require_state", return_value=state), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=agg,
    ):
        raw = await mcp_server.search_literature(
            query="x", max_results=5, optimize_query=False,
        )

    payload = json.loads(raw)
    assert "usage" not in payload
    state.llm_client.complete.assert_not_called()
