"""Tests for AgenticOrchestrator provenance tracing and recency/KB-meta params."""

from unittest.mock import MagicMock

import pytest

from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator


def test_orchestrator_accepts_recency_and_kb_metas():
    o = AgenticOrchestrator(
        llm_client=MagicMock(),
        vector_store=MagicMock(),
        embedding_provider=MagicMock(),
        tool_registry=MagicMock(),
        max_iterations=5,
        map_reduce_max_papers=8,
        recency_weight=0.5,
        recency_half_life_years=10.0,
        kb_metas=[MagicMock(), MagicMock()],
    )
    assert o.recency_weight == 0.5
    assert o.recency_half_life_years == 10.0
    assert len(o.kb_metas) == 2


def test_orchestrator_defaults_recency_and_kb_metas():
    """New kwargs default to None / empty list — backward-compat."""
    o = AgenticOrchestrator(
        llm_client=None,
        tool_registry=None,
        embedding_provider=None,
        vector_store=None,
    )
    assert o.recency_weight is None
    assert o.recency_half_life_years is None
    assert o.kb_metas == []


def test_orchestrator_source_mentions_get_collector():
    """Smoke check that the orchestrator file references provenance."""
    from perspicacite.rag.agentic import orchestrator as mod

    src = open(mod.__file__).read()
    assert "get_collector" in src
