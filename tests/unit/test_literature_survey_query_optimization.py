# tests/unit/test_literature_survey_query_optimization.py
"""Verifies that LiteratureSurveyRAGMode._broad_search runs the shared optimizer
before the SciLEx search and substitutes the rewritten query."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.search.query_optimizer import OptimizationResult


@pytest.mark.asyncio
async def test_broad_search_substitutes_rewritten_query():
    """SciLEx adapter must receive the optimizer's `searched_query`, not the
    original query passed to _broad_search."""
    captured = {}

    fake_scilex = MagicMock()
    fake_scilex.search = AsyncMock(return_value=[])

    async def capture_optimize(**kwargs):
        captured.update(kwargs)
        return OptimizationResult(
            searched_query="rewritten literature survey phrase",
            enabled=True,
            applied=True,
            context_used=False,
            fallback_reason=None,
        )

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=capture_optimize,
    ):
        from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode

        # Build a minimal stub app_state that satisfies the optimizer gate check.
        stub_state = MagicMock()
        stub_state.config.search.query_optimization.enabled = True
        stub_state.config.search.query_optimization.timeout_s = 5.0
        stub_state.config.search.query_optimization.max_context_chars = 300
        stub_state.config.llm.default_provider = "anthropic"
        stub_state.config.llm.default_model = "claude-haiku-4-5"
        stub_state.config.llm.models = {}
        stub_state.config.llm.providers_per_stage = {}
        stub_state.llm_client = MagicMock()

        # Patch the global app_state import inside the module.
        with patch(
            "perspicacite.web.state.app_state",
            stub_state,
        ):
            # Build a minimal LiteratureSurveyRAGMode instance without a real
            # config / session_store.
            mode = LiteratureSurveyRAGMode.__new__(LiteratureSurveyRAGMode)
            mode.scilex_adapter = fake_scilex
            # Provide a real-enough config object so BaseRAGMode attributes
            # don't blow up (not actually needed by _broad_search).
            mode.config = MagicMock()

            await mode._broad_search(
                query="original user query",
                databases=["semantic_scholar"],
            )

    # Optimizer received the original query.
    assert captured["query"] == "original user query"
    # SciLEx adapter received the rewritten query.
    assert fake_scilex.search.call_args.kwargs["query"] == "rewritten literature survey phrase"
