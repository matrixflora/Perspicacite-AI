# tests/unit/test_opencitations.py
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

_SAMPLE_COCI = [
    {
        "oci": "020...",
        "citing": "10.1234/citing1",
        "cited": "10.1234/seed",
        "creation": "2023-01",
        "timespan": "P1Y2M",
        "journal_sc": "no",
        "author_sc": "no",
    },
    {
        "oci": "021...",
        "citing": "10.1234/citing2",
        "cited": "10.1234/seed",
        "creation": "2021-06",
        "timespan": "P3Y0M",
        "journal_sc": "no",
        "author_sc": "no",
    },
]


def _mock_resp(data, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_fetch_returns_citing_dois(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations

    async def mock_get(self, url, **kwargs):
        return _mock_resp(_SAMPLE_COCI)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/seed")
    dois = [r["doi"] for r in results]
    assert "10.1234/citing1" in dois
    assert "10.1234/citing2" in dois


@pytest.mark.asyncio
async def test_fetch_extracts_year_from_creation(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations

    async def mock_get(self, url, **kwargs):
        return _mock_resp(_SAMPLE_COCI)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/seed")
    # creation "2023-01" → year 2023
    r1 = next(r for r in results if r["doi"] == "10.1234/citing1")
    assert r1["publication_year"] == 2023


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_404(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations

    async def mock_get(self, url, **kwargs):
        return _mock_resp([], status=404)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/unknown")
    assert results == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_error(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations

    async def mock_get(self, url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/any")
    assert results == []


def test_multi_source_bonus_applied():
    from perspicacite.config.schema import CiteGraphConfig
    from perspicacite.pipeline.cite_graph import CiteHit, score_cite_hit

    cfg = CiteGraphConfig()
    hit = CiteHit(doi="10.1/x", title="Test", year=2022, venue=None, citation_count=10, is_oa=True)
    score_without_bonus = score_cite_hit(hit, [], cfg, now_year=2024, source_count=1)

    hit2 = CiteHit(doi="10.1/x", title="Test", year=2022, venue=None, citation_count=10, is_oa=True)
    score_with_bonus = score_cite_hit(hit2, [], cfg, now_year=2024, source_count=2)

    assert score_with_bonus > score_without_bonus
