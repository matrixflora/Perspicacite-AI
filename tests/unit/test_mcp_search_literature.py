# tests/unit/test_mcp_search_literature.py
"""Tests for the optimizer wiring in the MCP search_literature tool.

We patch the aggregator and the optimizer at the import sites used by
``search_literature``, so these tests don't touch the network or the LLM.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server
from perspicacite.search.query_optimizer import OptimizationResult


@pytest.fixture
def app_state(tmp_path):
    """Build a stub app_state attached to the MCP module's mcp_state slot."""
    state = MagicMock()
    state.initialized = True
    state.config.search.query_optimization.enabled = True
    state.config.search.query_optimization.timeout_s = 5.0
    state.config.search.query_optimization.max_context_chars = 300
    state.config.llm.default_provider = "anthropic"
    state.config.llm.default_model = "claude-haiku-4-5"
    state.config.llm.models = {}
    state.config.llm.providers_per_stage = {}
    state.llm_client = MagicMock()
    state.vector_store = MagicMock()

    # Patch the module-level mcp_state that _require_state() checks.
    with patch.object(mcp_server, "mcp_state", state):
        yield state


def _stub_aggregator(papers: list) -> MagicMock:
    agg = MagicMock()
    agg.available = True
    agg.search = AsyncMock(return_value=papers)
    return agg


@pytest.mark.asyncio
async def test_search_literature_calls_optimizer_when_enabled(app_state):
    fake_agg = _stub_aggregator([])
    fake_result = OptimizationResult(
        searched_query="myocardial infarction biomarkers",
        enabled=True, applied=True, context_used=False, fallback_reason=None,
    )
    with patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=fake_agg,
    ), patch(
        "perspicacite.search.query_optimizer.optimize_query",
        AsyncMock(return_value=fake_result),
    ):
        raw = await mcp_server.search_literature(
            query="heart attack biomarkers",
            max_results=5,
        )
    payload = json.loads(raw)
    assert payload["original_query"] == "heart attack biomarkers"
    assert payload["searched_query"] == "myocardial infarction biomarkers"
    assert payload["query_optimization"]["enabled"] is True
    assert payload["query_optimization"]["applied"] is True
    assert payload["query_optimization"]["context_used"] is False
    assert payload["query_optimization"]["fallback_reason"] is None
    # The aggregator should receive the rewritten query, not the original.
    assert fake_agg.search.call_args.kwargs["query"] == (
        "myocardial infarction biomarkers"
    )


@pytest.mark.asyncio
async def test_search_literature_passes_context_to_optimizer(app_state):
    fake_agg = _stub_aggregator([])
    fake_result = OptimizationResult(
        searched_query="LSD1 inhibitors AML",
        enabled=True, applied=True, context_used=True, fallback_reason=None,
    )
    captured: dict = {}

    async def fake_opt(**kwargs):
        captured.update(kwargs)
        return fake_result

    with patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=fake_agg,
    ), patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=fake_opt,
    ):
        raw = await mcp_server.search_literature(
            query="how does this inhibitor work",
            context="user has been discussing LSD1 inhibitors in AML",
            max_results=5,
        )
    payload = json.loads(raw)
    assert captured["query"] == "how does this inhibitor work"
    assert captured["context"] == "user has been discussing LSD1 inhibitors in AML"
    assert captured["optimize_enabled"] is None  # default — use config
    assert payload["query_optimization"]["context_used"] is True


@pytest.mark.asyncio
async def test_search_literature_optimize_query_false_skips_call(app_state):
    fake_agg = _stub_aggregator([])
    with patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=fake_agg,
    ), patch(
        "perspicacite.search.query_optimizer.optimize_query",
        new_callable=AsyncMock,
    ) as fake_opt:
        # Even with optimize_query=False, we still pass through the
        # optimizer so it returns the no-op result; the optimizer itself
        # short-circuits when optimize_enabled is False.
        fake_opt.return_value = OptimizationResult(
            searched_query="heart attack biomarkers",
            enabled=False, applied=False, context_used=False,
            fallback_reason=None,
        )
        raw = await mcp_server.search_literature(
            query="heart attack biomarkers",
            optimize_query=False,
            max_results=5,
        )
    payload = json.loads(raw)
    assert payload["searched_query"] == "heart attack biomarkers"
    assert payload["query_optimization"]["applied"] is False
    assert fake_opt.call_args.kwargs["optimize_enabled"] is False


@pytest.mark.asyncio
async def test_search_literature_fallback_reason_surfaces(app_state):
    fake_agg = _stub_aggregator([])
    fake_result = OptimizationResult(
        searched_query="heart attack biomarkers",
        enabled=True, applied=False, context_used=False,
        fallback_reason="llm_error",
    )
    with patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=fake_agg,
    ), patch(
        "perspicacite.search.query_optimizer.optimize_query",
        AsyncMock(return_value=fake_result),
    ):
        raw = await mcp_server.search_literature(
            query="heart attack biomarkers",
            max_results=5,
        )
    payload = json.loads(raw)
    assert payload["query_optimization"]["fallback_reason"] == "llm_error"
    # Search still ran with the verbatim query.
    assert fake_agg.search.call_args.kwargs["query"] == "heart attack biomarkers"
