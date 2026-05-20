"""profound.py honors request.screen_method / screen_threshold and emits a
reflect phase_progress around the per-cycle iteration summary.

CHANGE A: `_filter_documents_by_relevance` dispatches to the configured
screening function (bm25 / rerank / llm) at the configured threshold,
preserving the KB-passthrough + top-N rerank-of-tail behavior.

CHANGE B: the per-cycle `_create_iteration_summary` call is wrapped in
`emit_phase(..., phase="reflect", state="running"/"done", cycle=...)`.
"""

from __future__ import annotations

from typing import Any

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGRequest
from perspicacite.rag.modes import profound as profound_mod
from perspicacite.rag.modes.profound import ProfoundRAGMode


def _web_docs(n: int) -> list[dict[str, Any]]:
    """Non-KB (web_search) docs so they go through the rerank/screen tail."""
    return [
        {
            "source": "web_search",
            "title": f"doc {i}",
            "abstract": f"abstract {i}",
        }
        for i in range(n)
    ]


def _fake_screen_result(item: dict[str, Any], score: float):
    from perspicacite.search.screening import ScreenResult

    return ScreenResult(item=item, score=score, kept=score > 0.0)


@pytest.mark.asyncio
async def test_filter_default_uses_rerank_at_zero(monkeypatch):
    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    docs = _web_docs(15)

    calls: dict[str, Any] = {}

    async def fake_rerank(candidates, query, threshold=0.3, **kw):
        calls["fn"] = "rerank"
        calls["threshold"] = threshold
        return [_fake_screen_result(c, 0.5) for c in candidates]

    monkeypatch.setattr(profound_mod, "screen_papers_rerank", fake_rerank)

    req = RAGRequest(query="q")
    out = await mode._filter_documents_by_relevance(
        documents=docs, query="q", request=req, llm=None, max_keep=5
    )
    assert calls["fn"] == "rerank"
    assert calls["threshold"] == 0.0
    assert len(out) == 5


@pytest.mark.asyncio
async def test_filter_bm25_at_threshold(monkeypatch):
    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    docs = _web_docs(15)

    calls: dict[str, Any] = {}

    def fake_bm25(candidates, reference, method="bm25", threshold=0.3):
        calls["fn"] = "bm25"
        calls["threshold"] = threshold
        return [_fake_screen_result(c, 0.5) for c in candidates]

    monkeypatch.setattr(profound_mod, "screen_papers", fake_bm25)

    req = RAGRequest(query="q", screen_method="bm25", screen_threshold=0.4)
    out = await mode._filter_documents_by_relevance(
        documents=docs, query="q", request=req, llm=None, max_keep=5
    )
    assert calls["fn"] == "bm25"
    assert calls["threshold"] == 0.4
    assert len(out) == 5


@pytest.mark.asyncio
async def test_filter_llm_at_threshold_when_llm_available(monkeypatch):
    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    docs = _web_docs(15)

    calls: dict[str, Any] = {}

    async def fake_llm_screen(candidates, query, llm, threshold=0.5, **kw):
        calls["fn"] = "llm"
        calls["threshold"] = threshold
        calls["llm"] = llm
        return [_fake_screen_result(c, 0.7) for c in candidates]

    monkeypatch.setattr(profound_mod, "screen_papers_llm", fake_llm_screen)

    sentinel_llm = object()
    req = RAGRequest(query="q", screen_method="llm", screen_threshold=0.6)
    out = await mode._filter_documents_by_relevance(
        documents=docs, query="q", request=req, llm=sentinel_llm, max_keep=5
    )
    assert calls["fn"] == "llm"
    assert calls["threshold"] == 0.6
    assert calls["llm"] is sentinel_llm
    assert len(out) == 5


@pytest.mark.asyncio
async def test_filter_llm_falls_back_to_rerank_without_llm(monkeypatch):
    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    docs = _web_docs(15)

    calls: dict[str, Any] = {}

    async def fake_rerank(candidates, query, threshold=0.3, **kw):
        calls["fn"] = "rerank"
        calls["threshold"] = threshold
        return [_fake_screen_result(c, 0.5) for c in candidates]

    monkeypatch.setattr(profound_mod, "screen_papers_rerank", fake_rerank)

    req = RAGRequest(query="q", screen_method="llm", screen_threshold=0.6)
    out = await mode._filter_documents_by_relevance(
        documents=docs, query="q", request=req, llm=None, max_keep=5
    )
    # No llm in scope -> fall back to rerank, still honoring threshold.
    assert calls["fn"] == "rerank"
    assert calls["threshold"] == 0.6
    assert len(out) == 5


@pytest.mark.asyncio
async def test_filter_unknown_method_falls_back_to_rerank(monkeypatch):
    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    docs = _web_docs(15)

    calls: dict[str, Any] = {}

    async def fake_rerank(candidates, query, threshold=0.3, **kw):
        calls["fn"] = "rerank"
        calls["threshold"] = threshold
        return [_fake_screen_result(c, 0.5) for c in candidates]

    monkeypatch.setattr(profound_mod, "screen_papers_rerank", fake_rerank)

    req = RAGRequest(query="q", screen_method="bogus")
    out = await mode._filter_documents_by_relevance(
        documents=docs, query="q", request=req, llm=None, max_keep=5
    )
    assert calls["fn"] == "rerank"
    assert calls["threshold"] == 0.0
    assert len(out) == 5


# ---- CHANGE B: reflect phase_progress source-level + emit shape -------


def test_profound_emits_reflect_phase_literals():
    """Defend against typos; reflect phase wraps the iteration summary."""
    with open(profound_mod.__file__) as fh:
        src = fh.read()
    assert 'phase="reflect", state="running"' in src
    assert 'phase="reflect", state="done"' in src
