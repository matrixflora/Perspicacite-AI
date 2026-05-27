# tests/unit/test_agentic_query_optimization.py
"""Verifies that AgenticOrchestrator._scilex_search runs the shared optimizer
before the SciLEx search and substitutes the rewritten query."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.search.query_optimizer import OptimizationResult


@pytest.mark.asyncio
async def test_scilex_search_substitutes_rewritten_query():
    """SciLEx adapter must receive the optimizer's `searched_query`, not the
    original query passed to _scilex_search."""
    captured = {}

    fake_scilex = MagicMock()
    fake_scilex.search = AsyncMock(return_value=[])

    async def capture_optimize(**kwargs):
        captured.update(kwargs)
        return OptimizationResult(
            searched_query="rewritten agentic phrase",
            enabled=True,
            applied=True,
            context_used=False,
            fallback_reason=None,
        )

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=capture_optimize,
    ), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        side_effect=RuntimeError("no aggregator in unit test"),
    ):
        from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator

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

        # Build a minimal AgenticOrchestrator without real services.
        orch = AgenticOrchestrator.__new__(AgenticOrchestrator)
        orch.scilex_adapter = fake_scilex
        orch._found_papers_lock = asyncio.Lock()
        # Provide stubs for attributes accessed in the method body.
        orch._found_papers = []
        orch._accumulate_lit_evidence = MagicMock()
        orch._format_paper_list = MagicMock(return_value="[]")
        # Inject app_state directly (no longer read from global).
        orch.app_state = stub_state

        await orch._scilex_search("original query")

    # Optimizer received the original query.
    assert captured["query"] == "original query"
    # SciLEx adapter received the rewritten query.
    assert fake_scilex.search.call_args.kwargs["query"] == "rewritten agentic phrase"


@pytest.mark.asyncio
async def test_scilex_search_optimizer_failure_falls_back_to_original():
    """When optimize_query raises, the original query reaches SciLEx."""
    fake_scilex = MagicMock()
    fake_scilex.search = AsyncMock(return_value=[])

    async def raise_on_optimize(**kwargs):
        raise RuntimeError("optimizer exploded")

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=raise_on_optimize,
    ), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        side_effect=RuntimeError("no aggregator in unit test"),
    ):
        from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator

        stub_state = MagicMock()
        stub_state.config.search.query_optimization.enabled = True
        stub_state.config.search.query_optimization.timeout_s = 5.0
        stub_state.config.search.query_optimization.max_context_chars = 300
        stub_state.config.llm.default_provider = "anthropic"
        stub_state.config.llm.default_model = "claude-haiku-4-5"
        stub_state.config.llm.models = {}
        stub_state.config.llm.providers_per_stage = {}
        stub_state.llm_client = MagicMock()

        orch = AgenticOrchestrator.__new__(AgenticOrchestrator)
        orch.scilex_adapter = fake_scilex
        orch._found_papers_lock = asyncio.Lock()
        orch._found_papers = []
        orch._accumulate_lit_evidence = MagicMock()
        orch._format_paper_list = MagicMock(return_value="[]")
        orch.app_state = stub_state

        await orch._scilex_search("original query")

    # SciLEx adapter must receive the original (unmodified) query on optimizer failure.
    assert fake_scilex.search.call_args.kwargs["query"] == "original query"


@pytest.mark.asyncio
async def test_scilex_search_forces_optimization_even_when_config_disabled():
    """_scilex_search must pass optimize_enabled=True, not None.

    B-9 fix: agentic mode internal web searches should always run the keyword
    rewrite, even if the user has disabled query optimization globally in their
    config (``search.query_optimization.enabled = False``).
    """
    captured_kwargs: dict = {}

    fake_scilex = MagicMock()
    fake_scilex.search = AsyncMock(return_value=[])

    async def capture_optimize(**kwargs):
        captured_kwargs.update(kwargs)
        return OptimizationResult(
            searched_query="optimised internal query",
            enabled=True,
            applied=True,
            context_used=False,
            fallback_reason=None,
        )

    with patch(
        "perspicacite.search.query_optimizer.optimize_query",
        side_effect=capture_optimize,
    ), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        side_effect=RuntimeError("no aggregator in unit test"),
    ):
        from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator

        stub_state = MagicMock()
        # Config has optimization DISABLED globally — but _scilex_search should
        # override this by passing optimize_enabled=True explicitly.
        stub_state.config.search.query_optimization.enabled = False
        stub_state.config.search.query_optimization.timeout_s = 5.0
        stub_state.config.search.query_optimization.max_context_chars = 300
        stub_state.config.llm.default_provider = "anthropic"
        stub_state.config.llm.default_model = "claude-haiku-4-5"
        stub_state.config.llm.models = {}
        stub_state.config.llm.providers_per_stage = {}
        stub_state.llm_client = MagicMock()

        orch = AgenticOrchestrator.__new__(AgenticOrchestrator)
        orch.scilex_adapter = fake_scilex
        orch._found_papers_lock = asyncio.Lock()
        orch._found_papers = []
        orch._accumulate_lit_evidence = MagicMock()
        orch._format_paper_list = MagicMock(return_value="[]")
        orch.app_state = stub_state

        await orch._scilex_search("some raw claim text")

    # The optimizer must have been called with optimize_enabled=True, not None.
    assert captured_kwargs.get("optimize_enabled") is True, (
        "_scilex_search must pass optimize_enabled=True to force optimisation "
        "even when config.search.query_optimization.enabled=False"
    )
    # And the rewritten query must reach SciLEx.
    assert fake_scilex.search.call_args.kwargs["query"] == "optimised internal query"
