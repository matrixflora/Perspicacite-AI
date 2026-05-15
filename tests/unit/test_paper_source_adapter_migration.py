"""Pin adapter-specific PaperSource values after the 2026-05-15 audit.

These adapters historically defaulted to ``WEB_SEARCH``, which made
downstream "where did this paper come from?" queries useless. The
migration labels each path with its true origin:

  - ``search.doi_resolver.py``        → ``PaperSource.CROSSREF``
  - ``pipeline.snowball.py``          → ``PaperSource.CITATION_FOLLOW``
  - ``search.semantic_scholar.py``    → ``PaperSource.SEMANTIC_SCHOLAR``
  - ``rag.chunking.py`` (stub Paper)  → ``PaperSource.LOCAL``
  - ``mcp.server.py`` (2 sites)       → ``USER_UPLOAD`` / ``OPENALEX``
  - ``web.routers.kb`` (3 sites)      → ``USER_UPLOAD`` / ``OPENALEX``
  - ``pipeline.search_to_kb.py``      → ``PaperSource.OPENALEX``
  - ``rag.agentic.orchestrator.py``   → ``OPENALEX`` / ``SEMANTIC_SCHOLAR``

See also ``test_paper_source_no_websearch_defaults.py`` for the
file-wide invariant that no production module constructs Papers with
``source=PaperSource.WEB_SEARCH``.
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


def test_mcp_add_papers_to_kb_uses_user_upload():
    """The MCP add_papers_to_kb tool accepts user-supplied paper dicts —
    these come from an external client, so the source must be
    USER_UPLOAD (not the legacy WEB_SEARCH)."""
    import inspect
    from perspicacite.mcp import server

    src = inspect.getsource(server)
    assert "source=PaperSource.USER_UPLOAD" in src, (
        "mcp/server.py add_papers_to_kb must build papers with "
        "PaperSource.USER_UPLOAD"
    )
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "mcp/server.py must not default to PaperSource.WEB_SEARCH anymore"
    )


def test_mcp_add_dois_to_kb_uses_openalex():
    """The MCP add_dois_to_kb tool fetches via retrieve_paper_content
    (unified pipeline); OpenAlex is the discovery source."""
    import inspect
    from perspicacite.mcp import server

    src = inspect.getsource(server)
    assert "source=PaperSource.OPENALEX" in src, (
        "mcp/server.py add_dois_to_kb must build papers with "
        "PaperSource.OPENALEX"
    )


def test_kb_router_uses_correct_enum_values():
    """src/perspicacite/web/routers/kb.py has three Paper-construction
    sites that previously defaulted to WEB_SEARCH:

    - _dois_ingest_worker (line ~335) — async DOI ingest → OPENALEX
    - add_papers_to_kb route (line ~564) — user paper dicts → USER_UPLOAD
    - add_dois_to_kb route (line ~1062) — sync DOI ingest → OPENALEX

    This invariant test guards all three at once via source scan.
    """
    import inspect
    from perspicacite.web.routers import kb

    src = inspect.getsource(kb)
    # Must NOT contain the legacy default anywhere
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "web/routers/kb.py must not default any Paper to WEB_SEARCH"
    )
    # Must contain the new values (count = at least one each)
    assert src.count("source=PaperSource.USER_UPLOAD") >= 1, (
        "kb.py add_papers_to_kb must use USER_UPLOAD"
    )
    assert src.count("source=PaperSource.OPENALEX") >= 2, (
        "kb.py must use OPENALEX for both DOI-ingest paths (sync + async)"
    )


def test_search_to_kb_ingest_dois_uses_openalex():
    """pipeline/search_to_kb.ingest_dois_into_kb fetches via the unified
    download pipeline; built Papers must carry source=OPENALEX."""
    import inspect
    from perspicacite.pipeline import search_to_kb

    src = inspect.getsource(search_to_kb)
    assert "source=PaperSource.OPENALEX" in src, (
        "pipeline/search_to_kb.py must use PaperSource.OPENALEX"
    )
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "pipeline/search_to_kb.py must not default to WEB_SEARCH"
    )


def test_orchestrator_url_prefetch_uses_correct_enums():
    """rag/agentic/orchestrator._try_resolve_url has two Paper-source
    sites:

    - Unified-pipeline branch (line ~1142) → OPENALEX
    - S2 fallback branch (line ~1174) → SEMANTIC_SCHOLAR

    Source scan keeps both pinned."""
    import inspect
    from perspicacite.rag.agentic import orchestrator

    src = inspect.getsource(orchestrator)
    assert "source=PaperSource.OPENALEX" in src, (
        "orchestrator URL prefetch via unified pipeline must use OPENALEX"
    )
    assert "source=PaperSource.SEMANTIC_SCHOLAR" in src, (
        "orchestrator URL prefetch S2 fallback must use SEMANTIC_SCHOLAR"
    )
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "orchestrator must not default to WEB_SEARCH"
    )


def test_snowball_ss_provenance_papers_still_use_citation_follow_enum():
    """Cite-graph hits — regardless of whether OpenAlex or SS sourced
    them — produce Papers with source=CITATION_FOLLOW. provenance is
    the edge-level signal; Paper.source is the paper-record signal."""
    from perspicacite.pipeline.snowball import ExpansionHit, _papers_from_hits
    h = ExpansionHit(
        seed_doi="10.48550/arXiv.2005.11401",
        expanded_doi="10.1234/cited",
        direction="forward",
        title="A Cited Work",
        authors=["Author A"],
        year=2024,
        abstract="...",
        journal="Journal",
        citation_count=3,
        provenance="semantic_scholar",
    )
    papers = _papers_from_hits([h])
    assert len(papers) == 1
    assert papers[0].source is PaperSource.CITATION_FOLLOW
