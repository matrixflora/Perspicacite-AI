"""Unit tests for AgenticOrchestrator._rerank_papers_by_relevance.

The rerank step keeps the most query-relevant papers when a multi-database
search floods the candidate pool with off-topic results. These tests use a
stub reranker so no cross-encoder model is loaded.
"""

from types import SimpleNamespace

import pytest

from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator


def _paper(title: str, abstract: str = ""):
    return SimpleNamespace(title=title, abstract=abstract)


class _StubReranker:
    """Returns caller-supplied scores keyed by substring match in the text."""

    def __init__(self, score_by_keyword: dict[str, float]):
        self.score_by_keyword = score_by_keyword

    async def score_texts(self, query: str, texts: list[str]) -> list[float]:
        out = []
        for t in texts:
            score = 0.0
            for kw, sc in self.score_by_keyword.items():
                if kw.lower() in t.lower():
                    score = sc
                    break
            out.append(score)
        return out


class _ExplodingReranker:
    async def score_texts(self, query: str, texts: list[str]) -> list[float]:
        raise RuntimeError("model unavailable")


def _orch() -> AgenticOrchestrator:
    # Bypass __init__ — the helper only touches self._relevance_reranker.
    return AgenticOrchestrator.__new__(AgenticOrchestrator)


@pytest.mark.asyncio
async def test_rerank_surfaces_relevant_papers():
    """Relevant papers buried deep in the pool float to the top after rerank."""
    orch = _orch()
    orch._relevance_reranker = _StubReranker(
        {"agentbench": 9.0, "llm agents": 8.0, "chemotherapeutic": 1.0}
    )
    papers = [
        _paper("Evaluation of Chemotherapeutic Agents"),
        _paper("Anti-inflammatory agents review"),
        _paper("AgentBench: Evaluating LLMs as Agents"),
        _paper("On Evaluating LLM Agents with Databases"),
    ]
    out = await orch._rerank_papers_by_relevance("agent evaluation", papers, top_k=2)
    titles = [p.title for p in out]
    assert titles == [
        "AgentBench: Evaluating LLMs as Agents",
        "On Evaluating LLM Agents with Databases",
    ]


@pytest.mark.asyncio
async def test_rerank_passthrough_when_pool_small():
    """No reranking when the pool already fits within top_k."""
    orch = _orch()
    orch._relevance_reranker = _ExplodingReranker()  # must not be called
    papers = [_paper("a"), _paper("b")]
    out = await orch._rerank_papers_by_relevance("q", papers, top_k=5)
    assert [p.title for p in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_rerank_falls_back_on_error():
    """A reranker failure degrades to the original order, trimmed to top_k."""
    orch = _orch()
    orch._relevance_reranker = _ExplodingReranker()
    papers = [_paper("a"), _paper("b"), _paper("c")]
    out = await orch._rerank_papers_by_relevance("q", papers, top_k=2)
    assert [p.title for p in out] == ["a", "b"]


def test_fallback_notice_names_rate_limited_source():
    """A 429 on a selected source produces a rate-limited notice."""
    orch = _orch()
    orch.scilex_adapter = SimpleNamespace(
        last_errors_by_database={"arxiv": "429 Client Error: Too Many Requests"}
    )
    session = SimpleNamespace()
    orch._note_search_fallback(session, ["arxiv"])
    assert len(session.search_notices) == 1
    assert "arxiv (rate-limited)" in session.search_notices[0]
    assert "OpenAlex" in session.search_notices[0]


def test_fallback_notice_generic_when_no_recorded_error():
    """No per-DB error recorded → generic 'returned nothing' notice."""
    orch = _orch()
    orch.scilex_adapter = SimpleNamespace(last_errors_by_database={})
    session = SimpleNamespace()
    orch._note_search_fallback(session, ["arxiv"])
    assert len(session.search_notices) == 1
    assert "OpenAlex" in session.search_notices[0]


def test_fallback_notice_noop_without_session():
    """No session → no crash, nothing stashed."""
    orch = _orch()
    orch.scilex_adapter = SimpleNamespace(last_errors_by_database={})
    orch._note_search_fallback(None, ["arxiv"])  # must not raise
