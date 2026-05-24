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
from perspicacite.rag.modes import deep_research as profound_mod
from perspicacite.rag.modes.deep_research import ProfoundRAGMode


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


# ---- CHANGE B: reflect phase_progress is emitted by a real cycle ------


@pytest.mark.asyncio
async def test_profound_emits_reflect_phase_progress_for_cycle(monkeypatch):
    """Drive execute_stream through one full cycle and assert the reflect
    phase_progress events (running + done) fire with cycle index 0.

    The cycle is forced to reach ``_create_iteration_summary`` by stubbing
    the per-cycle helpers so there is no early_exit / plan_limit_reason and
    the final-response stream is a no-op.
    """
    from perspicacite.models.rag import StreamEvent
    from perspicacite.rag.modes.deep_research import ResearchStep

    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    mode.max_cycles = 1  # single cycle: cycle index 0
    mode.use_websearch = False  # avoid tools.list_tools() web branch

    async def fake_create_plan(query, llm):
        return ["step-1"]

    async def fake_execute_cycle_steps(*args, **kwargs):
        step = ResearchStep(step_purpose="p", query="q")
        step.success = True
        # (cycle_steps, cycle_documents, plan_limit_reason=None, early_exit=False)
        return [step], [], None, False

    summary_called: dict[str, Any] = {}

    async def fake_iteration_summary(query, cycle_steps, llm):
        summary_called["hit"] = True
        return {"findings": "f", "missing": [], "should_continue": False}

    async def fake_stream_final(*args, **kwargs):
        yield StreamEvent.status("final")

    # Upfront keyword optimizer runs before the loop; stub it out so the
    # test needs no live LLM / app_state.
    async def fake_optimize_query(**kwargs):
        class _Res:
            applied = False
            searched_query = None

        return _Res()

    monkeypatch.setattr(mode, "_create_plan", fake_create_plan)
    monkeypatch.setattr(mode, "_execute_cycle_steps", fake_execute_cycle_steps)
    monkeypatch.setattr(mode, "_create_iteration_summary", fake_iteration_summary)
    monkeypatch.setattr(mode, "_stream_final_response", fake_stream_final)
    monkeypatch.setattr(
        "perspicacite.search.query_optimizer.optimize_query", fake_optimize_query
    )

    # Fake telemetry sink: a plain list, matching how emit_phase appends and
    # how the MCP path reads getattr(request, "telemetry_sink", None).
    sink: list[dict[str, Any]] = []
    req = RAGRequest(query="q")
    req.telemetry_sink = sink  # type: ignore[attr-defined]

    class _Tools:
        def list_tools(self):
            return []

    async for _ in mode.execute_stream(
        request=req, llm=object(), vector_store=None, embedding_provider=None, tools=_Tools()
    ):
        pass

    assert summary_called.get("hit"), "cycle did not reach _create_iteration_summary"

    reflect_events = [
        ev
        for ev in sink
        if ev.get("kind") == "phase_progress" and ev.get("phase") == "reflect"
    ]
    assert {"kind": "phase_progress", "phase": "reflect", "state": "running", "cycle": 0} in sink
    assert {"kind": "phase_progress", "phase": "reflect", "state": "done", "cycle": 0} in sink
    # running must precede done within the same cycle.
    states = [ev["state"] for ev in reflect_events]
    assert states == ["running", "done"]
