# tests/unit/test_basic_mode_query_optimization.py
"""Verifies that the basic RAG mode runs the shared optimizer before the
aggregator fan-out and substitutes the rewritten query."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.search.query_optimizer import OptimizationResult


@pytest.mark.asyncio
async def test_basic_mode_substitutes_rewritten_query():
    """The aggregator must receive the optimizer's `searched_query`, not
    the original `keyword_query`."""
    captured = {}

    fake_agg = MagicMock()
    fake_agg._providers = []
    fake_agg.search = AsyncMock(return_value=[])

    async def capture_optimize(**kwargs):
        captured.update(kwargs)
        return OptimizationResult(
            searched_query="rewritten scientific phrase",
            enabled=True, applied=True, context_used=True,
            fallback_reason=None,
        )

    with patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=fake_agg,
    ), patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=capture_optimize,
    ):
        from perspicacite.rag.web_search import run_web_aggregator_search
        # Build a minimal stub app_state with config attributes the
        # function reads.
        stub_state = MagicMock()
        stub_state.config.search.query_optimization.enabled = True
        stub_state.config.search.query_optimization.timeout_s = 5.0
        stub_state.config.search.query_optimization.max_context_chars = 300
        stub_state.config.llm.default_provider = "anthropic"
        stub_state.config.llm.default_model = "claude-haiku-4-5"
        stub_state.config.llm.models = {}
        stub_state.config.llm.providers_per_stage = {}
        stub_state.llm_client = MagicMock()

        await run_web_aggregator_search(
            keyword_query="user typed this",
            context="recent finding about X",
            optimize_enabled=None,
            databases=["semantic_scholar"],
            max_docs=5,
            app_state=stub_state,
        )

    # Optimizer received the original keyword_query and the context.
    assert captured["query"] == "user typed this"
    assert captured["context"] == "recent finding about X"
    # Aggregator received the rewritten query.
    assert fake_agg.search.call_args.kwargs["query"] == "rewritten scientific phrase"
