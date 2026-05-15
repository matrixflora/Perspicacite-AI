"""Pin adapter-specific PaperSource values after the 2026-05-15 audit.

These three adapters historically all defaulted to ``WEB_SEARCH``, which
made downstream "where did this paper come from?" queries useless. The
fix labels each path with its true origin:

  - ``search.pubmed.py``        → ``PaperSource.PUBMED`` (Task 4)
  - ``search.doi_resolver.py``  → ``PaperSource.CROSSREF`` (this batch)
  - ``pipeline.snowball.py``    → ``PaperSource.CITATION_FOLLOW`` (this batch)
"""
from __future__ import annotations

import httpx
import pytest

from perspicacite.models.papers import PaperSource


@pytest.mark.asyncio
async def test_doi_resolver_uses_crossref_enum(monkeypatch):
    """resolve_doi calls the CrossRef API; the returned Paper must carry
    ``source=CROSSREF`` (not the generic WEB_SEARCH)."""
    from perspicacite.search.doi_resolver import resolve_doi

    sample = {
        "message": {
            "title": ["Test Paper"],
            "author": [{"given": "Ada", "family": "Lovelace"}],
            "published-print": {"date-parts": [[1843]]},
            "container-title": ["Notes on the Analytical Engine"],
            "URL": "https://example.org/test",
            "is-referenced-by-count": 42,
        }
    }

    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=sample, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        paper = await resolve_doi("10.1000/test", http_client=client)
    assert paper is not None
    assert paper.source is PaperSource.CROSSREF


def test_snowball_expansion_hit_uses_citation_follow_enum():
    """ExpansionHit-derived Papers in snowball.py are the canonical
    citation-follow case (forward/backward cite-graph walk). Pin the
    source so the migration isn't accidentally reverted."""
    from perspicacite.pipeline.snowball import ExpansionHit, _papers_from_hits

    hits = [
        ExpansionHit(
            seed_doi="10.1234/seed", expanded_doi="10.5678/expanded",
            direction="forward",
            title="A Cited Work",
            authors=["Author A"],
            year=2024, abstract="…", journal="Journal", citation_count=3,
        )
    ]
    papers = _papers_from_hits(hits)
    assert len(papers) == 1
    assert papers[0].source is PaperSource.CITATION_FOLLOW


@pytest.mark.asyncio
async def test_semantic_scholar_lookup_uses_ss_enum(monkeypatch):
    """semantic_scholar.lookup_paper() calls the S2 API; the returned
    Paper must carry source=SEMANTIC_SCHOLAR (not WEB_SEARCH)."""
    from perspicacite.search.semantic_scholar import lookup_paper

    sample = {
        "paperId": "s2id123",
        "title": "Attention Is All You Need",
        "abstract": "We propose a new simple network architecture...",
        "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        "year": 2017,
        "externalIds": {"DOI": "10.48550/arXiv.1706.03762", "ArXiv": "1706.03762"},
        "citationCount": 100000,
        "venue": "NeurIPS",
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
        "url": "https://www.semanticscholar.org/paper/s2id123",
    }

    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=sample, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    paper = await lookup_paper("10.48550/arXiv.1706.03762")
    assert paper is not None
    assert paper.source is PaperSource.SEMANTIC_SCHOLAR


@pytest.mark.asyncio
async def test_chunking_stub_paper_uses_local(monkeypatch):
    """AdvancedChunkerAdapter builds an internal Paper stub for the
    chunker. That stub must carry source=LOCAL (not WEB_SEARCH) — it is
    a transient, not a search result."""
    from perspicacite.rag.chunking import AdvancedChunkerAdapter

    captured = {}

    async def fake_chunk_text(self_chunker, text, paper, llm_client=None):
        captured["paper_source"] = paper.source
        # Return a single DocumentChunk-like object with a .text attr
        from types import SimpleNamespace
        return [SimpleNamespace(text=text)]

    monkeypatch.setattr(
        "perspicacite.pipeline.chunking_advanced.AdvancedChunker.chunk_text",
        fake_chunk_text,
    )

    adapter = AdvancedChunkerAdapter(method="semantic", chunk_size=100, overlap=20)
    out = await adapter.chunk_text_async("hello world")
    assert out == ["hello world"]
    assert captured["paper_source"] is PaperSource.LOCAL
