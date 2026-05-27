# tests/unit/test_literature_survey_query_optimization.py
"""Verifies that LiteratureSurveyRAGMode._broad_search runs the shared optimizer
before the search and substitutes the rewritten query into the pipeline call."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.search.query_optimizer import OptimizationResult


@pytest.mark.asyncio
async def test_broad_search_substitutes_rewritten_query():
    """The pipeline must receive the optimizer's `searched_query`, not the
    original query passed to _broad_search."""
    captured = {}

    async def capture_optimize(**kwargs):
        captured.update(kwargs)
        return OptimizationResult(
            searched_query="rewritten literature survey phrase",
            enabled=True,
            applied=True,
            context_used=False,
            fallback_reason=None,
        )

    mock_pipeline = AsyncMock(return_value=[])

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=capture_optimize,
    ), patch(
        "perspicacite.rag.resolve_papers.resolve_papers_pipeline",
        mock_pipeline,
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

        mode = LiteratureSurveyRAGMode.__new__(LiteratureSurveyRAGMode)
        mode.config = MagicMock()

        await mode._broad_search(
            query="original user query",
            databases=["semantic_scholar"],
            app_state=stub_state,
        )

    # Optimizer received the original query.
    assert captured["query"] == "original user query"
    # Pipeline received the rewritten query.
    assert mock_pipeline.call_args.kwargs["query"] == "rewritten literature survey phrase"


@pytest.mark.asyncio
async def test_broad_search_optimizer_failure_falls_back_to_original():
    """When optimize_query raises, the original query reaches the pipeline."""
    mock_pipeline = AsyncMock(return_value=[])

    async def raise_on_optimize(**kwargs):
        raise RuntimeError("optimizer exploded")

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=raise_on_optimize,
    ), patch(
        "perspicacite.rag.resolve_papers.resolve_papers_pipeline",
        mock_pipeline,
    ):
        from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode

        stub_state = MagicMock()
        stub_state.config.search.query_optimization.enabled = True
        stub_state.config.search.query_optimization.timeout_s = 5.0
        stub_state.config.search.query_optimization.max_context_chars = 300
        stub_state.config.llm.default_provider = "anthropic"
        stub_state.config.llm.default_model = "claude-haiku-4-5"
        stub_state.config.llm.models = {}
        stub_state.config.llm.providers_per_stage = {}
        stub_state.llm_client = MagicMock()

        mode = LiteratureSurveyRAGMode.__new__(LiteratureSurveyRAGMode)
        mode.config = MagicMock()

        await mode._broad_search(
            query="original user query",
            databases=["semantic_scholar"],
            app_state=stub_state,
        )

    # Pipeline must receive the original (unmodified) query on optimizer failure.
    assert mock_pipeline.call_args.kwargs["query"] == "original user query"


@pytest.mark.asyncio
async def test_broad_search_forces_optimization_even_when_config_disabled():
    """_broad_search must pass optimize_enabled=True, not None.

    B-9 fix: literature_survey internal web searches should always run the
    keyword rewrite, even if the user has disabled query optimization globally
    in their config (``search.query_optimization.enabled = False``).
    """
    captured_kwargs: dict = {}

    async def capture_optimize(**kwargs):
        captured_kwargs.update(kwargs)
        return OptimizationResult(
            searched_query="optimised survey query",
            enabled=True,
            applied=True,
            context_used=False,
            fallback_reason=None,
        )

    mock_pipeline = AsyncMock(return_value=[])

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=capture_optimize,
    ), patch(
        "perspicacite.rag.resolve_papers.resolve_papers_pipeline",
        mock_pipeline,
    ):
        from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode

        stub_state = MagicMock()
        # Config has optimization DISABLED globally — but _broad_search should
        # override this by passing optimize_enabled=True explicitly.
        stub_state.config.search.query_optimization.enabled = False
        stub_state.config.search.query_optimization.timeout_s = 5.0
        stub_state.config.search.query_optimization.max_context_chars = 300
        stub_state.config.llm.default_provider = "anthropic"
        stub_state.config.llm.default_model = "claude-haiku-4-5"
        stub_state.config.llm.models = {}
        stub_state.config.llm.providers_per_stage = {}
        stub_state.llm_client = MagicMock()

        mode = LiteratureSurveyRAGMode.__new__(LiteratureSurveyRAGMode)
        mode.config = MagicMock()

        await mode._broad_search(
            query="some raw claim text",
            databases=["semantic_scholar"],
            app_state=stub_state,
        )

    # The optimizer must have been called with optimize_enabled=True, not None.
    assert captured_kwargs.get("optimize_enabled") is True, (
        "_broad_search must pass optimize_enabled=True to force optimisation "
        "even when config.search.query_optimization.enabled=False"
    )
    # And the rewritten query must reach the pipeline.
    assert mock_pipeline.call_args.kwargs["query"] == "optimised survey query"
