"""Unit tests for the snowball → Semantic Scholar fallback path."""
from __future__ import annotations

import pytest

from perspicacite.pipeline.snowball import (
    _seed_needs_ss_fallback,
    _ss_id_for_seed,
)


def test_seed_needs_ss_fallback_arxiv_doi_uppercase():
    assert _seed_needs_ss_fallback("10.48550/arXiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"}) is True


def test_seed_needs_ss_fallback_arxiv_doi_lowercase():
    assert _seed_needs_ss_fallback("10.48550/arxiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"}) is True


def test_seed_needs_ss_fallback_crossref_doi_returns_false():
    assert _seed_needs_ss_fallback("10.1145/3404835.3462913", {"doi": "10.1145/3404835.3462913"}) is False


def test_seed_needs_ss_fallback_work_without_doi_returns_true():
    # OpenAlex resolved via title.search but has no canonical DOI
    assert _seed_needs_ss_fallback("foo", {"id": "W123", "doi": None}) is True
    assert _seed_needs_ss_fallback("foo", {"id": "W123"}) is True


def test_seed_needs_ss_fallback_none_work_returns_false():
    # If the seed didn't resolve at all, snowball already skipped it — the
    # SS branch never runs. Returning False here is defensive.
    assert _seed_needs_ss_fallback("10.48550/arxiv.X", None) is False


def test_ss_id_for_seed_arxiv_doi():
    """When the seed DOI is an arxiv DOI, prefer the ArXiv: form so
    Semantic Scholar can resolve the preprint directly."""
    out = _ss_id_for_seed("10.48550/arXiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"})
    assert out == "ArXiv:2005.11401"


def test_ss_id_for_seed_arxiv_doi_with_version_suffix():
    """arXiv ids can carry a vN version suffix; SS accepts the base id."""
    out = _ss_id_for_seed("10.48550/arXiv.2005.11401v2", {"doi": "10.48550/arxiv.2005.11401v2"})
    assert out == "ArXiv:2005.11401"


def test_ss_id_for_seed_crossref_doi_falls_back_to_doi_prefix():
    out = _ss_id_for_seed("10.1145/3404835.3462913", {"doi": "10.1145/3404835.3462913"})
    assert out == "DOI:10.1145/3404835.3462913"


# ---------------------------------------------------------------------------
# Task 4 integration tests — wire SS pass into snowball_expand
# ---------------------------------------------------------------------------

import httpx

from perspicacite.pipeline.snowball import snowball_expand


def _arxiv_seed_work_response():
    """OpenAlex 200 response for the RAG paper's arXiv DOI."""
    return {
        "id": "https://openalex.org/W_RAG",
        "doi": "https://doi.org/10.48550/arxiv.2005.11401",
        "title": "Retrieval-Augmented Generation",
        "display_name": "Retrieval-Augmented Generation",
        "publication_year": 2020,
        "cited_by_count": 18,
        "referenced_works": [],
        "authorships": [],
    }


def _oa_forward_hit():
    return {
        "id": "https://openalex.org/W_OAFWD",
        "doi": "https://doi.org/10.1234/oa-fwd",
        "title": "OA-only citer",
        "display_name": "OA-only citer",
        "publication_year": 2023,
        "cited_by_count": 2,
        "authorships": [],
    }


def _ss_only_hit_dict():
    """An OpenAlex-shaped dict that fetch_ss_citations would produce
    (already passed through _ss_record_to_oa_like_work)."""
    return {
        "id": "https://openalex.org/W_S2_ssid-1",
        "doi": "https://doi.org/10.1234/ss-only",
        "title": "SS-only citer",
        "display_name": "SS-only citer",
        "publication_year": 2024,
        "cited_by_count": 99,
        "authorships": [],
        "metadata": {"arxiv_id": None, "s2_paper_id": "ssid-1"},
    }


def _ss_dup_hit_dict():
    """SS hit that duplicates the OpenAlex forward hit (same DOI)."""
    return {
        "id": "https://openalex.org/W_S2_dup",
        "doi": "https://doi.org/10.1234/oa-fwd",   # same DOI as _oa_forward_hit
        "title": "OA-only citer",
        "publication_year": 2023,
        "cited_by_count": 2,
        "authorships": [],
        "metadata": {"s2_paper_id": "dup"},
    }


@pytest.mark.asyncio
async def test_snowball_appends_ss_only_hits_for_arxiv_seed(monkeypatch):
    """SS hit that OpenAlex didn't return → appended with
    provenance=semantic_scholar."""

    async def fake_oa_get(self, url, **kwargs):
        # Seed resolution: /works/doi:<arxiv-doi> returns the seed work
        if "/works/doi:" in url:
            return httpx.Response(200, json=_arxiv_seed_work_response(),
                                  request=httpx.Request("GET", url))
        # Forward citations: filter=cites:...
        params = kwargs.get("params") or {}
        if params.get("filter", "").startswith("cites:"):
            return httpx.Response(200,
                                  json={"results": [_oa_forward_hit()], "meta": {}},
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(paper_id, *, limit=100, http_client=None):
        # Returns one OpenAlex-shaped dict the OA branch did NOT see
        return [_ss_only_hit_dict()]

    async def fake_ss_references(paper_id, *, limit=100, http_client=None):
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr(
        "perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations,
    )
    monkeypatch.setattr(
        "perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references,
    )

    hits = await snowball_expand(
        seed_dois=["10.48550/arXiv.2005.11401"],
        direction="forward",
        max_per_seed=10,
    )

    # Expect 2 forward hits for this seed: OA-only and SS-only
    fwd = [h for h in hits if h.direction == "forward"]
    assert len(fwd) == 2
    by_doi = {h.expanded_doi: h for h in fwd}
    assert by_doi["10.1234/oa-fwd"].provenance == "openalex"
    assert by_doi["10.1234/ss-only"].provenance == "semantic_scholar"


@pytest.mark.asyncio
async def test_snowball_marks_duplicate_as_both(monkeypatch):
    """SS hit that DOES match an OpenAlex DOI → existing OA entry's
    provenance flips to 'both'; no duplicate ExpansionHit is appended."""

    async def fake_oa_get(self, url, **kwargs):
        if "/works/doi:" in url:
            return httpx.Response(200, json=_arxiv_seed_work_response(),
                                  request=httpx.Request("GET", url))
        params = kwargs.get("params") or {}
        if params.get("filter", "").startswith("cites:"):
            return httpx.Response(200,
                                  json={"results": [_oa_forward_hit()], "meta": {}},
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(paper_id, *, limit=100, http_client=None):
        return [_ss_dup_hit_dict()]   # same DOI as the OA hit

    async def fake_ss_references(paper_id, *, limit=100, http_client=None):
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references)

    hits = await snowball_expand(
        seed_dois=["10.48550/arXiv.2005.11401"],
        direction="forward",
    )

    fwd = [h for h in hits if h.direction == "forward"]
    assert len(fwd) == 1                  # dedup'd to one
    assert fwd[0].expanded_doi == "10.1234/oa-fwd"
    assert fwd[0].provenance == "both"


@pytest.mark.asyncio
async def test_snowball_skips_ss_when_flag_disabled(monkeypatch):
    """include_semantic_scholar=False → no SS HTTP calls."""
    ss_called: list = []

    async def fake_oa_get(self, url, **kwargs):
        if "/works/doi:" in url:
            return httpx.Response(200, json=_arxiv_seed_work_response(),
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(paper_id, *, limit=100, http_client=None):
        ss_called.append("citations")
        return []

    async def fake_ss_references(paper_id, *, limit=100, http_client=None):
        ss_called.append("references")
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references)

    await snowball_expand(
        seed_dois=["10.48550/arXiv.2005.11401"],
        direction="both",
        include_semantic_scholar=False,
    )
    assert ss_called == []


@pytest.mark.asyncio
async def test_snowball_skips_ss_for_crossref_seed(monkeypatch):
    """Non-arxiv seed with a real DOI in OpenAlex → no SS calls."""
    ss_called: list = []

    async def fake_oa_get(self, url, **kwargs):
        if "/works/doi:" in url:
            return httpx.Response(200, json={
                "id": "https://openalex.org/W_CROSSREF",
                "doi": "https://doi.org/10.1145/foo",
                "title": "A CrossRef Paper",
                "display_name": "A CrossRef Paper",
                "publication_year": 2022,
                "cited_by_count": 50,
                "referenced_works": [],
                "authorships": [],
            }, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(*a, **kw):
        ss_called.append("c")
        return []

    async def fake_ss_references(*a, **kw):
        ss_called.append("r")
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references)

    await snowball_expand(
        seed_dois=["10.1145/foo"], direction="both",
    )
    assert ss_called == []
