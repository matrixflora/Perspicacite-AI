"""Unit tests for ReasoningRAGMode dispatch + extra-check + default strategy."""

import json

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest


async def _drain(stream):
    return [ev async for ev in stream]


async def test_default_strategy_after_subplan_a_is_contradiction(tmp_path, monkeypatch):
    from perspicacite.rag.modes.reasoning import (
        DEFAULT_REASONING_STRATEGY,
        SHIPPED_STRATEGIES,
    )

    assert DEFAULT_REASONING_STRATEGY == "contradiction"
    assert "provenance" in SHIPPED_STRATEGIES
    assert "contradiction" in SHIPPED_STRATEGIES
    assert "graph" not in SHIPPED_STRATEGIES
    assert "evidence_graded" not in SHIPPED_STRATEGIES


async def test_unshipped_strategy_yields_not_implemented_error(monkeypatch):
    from perspicacite.rag.modes import reasoning as reasoning_mod
    from perspicacite.rag.modes.reasoning import ReasoningRAGMode

    # Ensure the indicia gate is open so we reach the strategy dispatcher.
    monkeypatch.setattr(reasoning_mod, "_HAS_INDICIA", True)
    mode = ReasoningRAGMode(Config())
    req = RAGRequest(query="q", mode=RAGMode.REASONING, reasoning_strategy="graph")
    events = await _drain(
        mode.execute_stream(req, llm=None, vector_store=None, embedding_provider=None, tools=None)
    )
    err = next(e for e in events if e.event == "error")
    payload = json.loads(err.data)
    assert "Subplan B" in payload["message"] or "not yet shipped" in payload["message"]


async def test_missing_indicia_extra_yields_error(monkeypatch):
    from perspicacite.rag.modes import reasoning as reasoning_mod

    monkeypatch.setattr(reasoning_mod, "_HAS_INDICIA", False)
    mode = reasoning_mod.ReasoningRAGMode(Config())
    req = RAGRequest(query="q", mode=RAGMode.REASONING)
    events = await _drain(
        mode.execute_stream(req, llm=None, vector_store=None, embedding_provider=None, tools=None)
    )
    err = next(e for e in events if e.event == "error")
    payload = json.loads(err.data)
    assert "indicia" in payload["message"]
