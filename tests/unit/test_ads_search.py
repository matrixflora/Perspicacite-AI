# tests/unit/test_ads_search.py
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from perspicacite.models.papers import PaperSource

_SAMPLE_RESPONSE = {
    "response": {
        "numFound": 1,
        "docs": [
            {
                "title": ["Exoplanet atmospheric characterization with JWST"],
                "author": ["Smith, J.", "Jones, A."],
                "year": "2023",
                "doi": ["10.1086/123456"],
                "bibcode": "2023ApJ...123..456S",
                "abstract": "We characterize atmospheres of exoplanets.",
                "identifier": ["arxiv:2301.12345"],
            }
        ],
    }
}


def _mock_resp(data: dict, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_search_returns_papers(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider

    async def mock_get(self, url, **kwargs):
        return _mock_resp(_SAMPLE_RESPONSE)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="testtoken")
    papers = await provider.search("exoplanet atmosphere")

    assert len(papers) == 1
    assert papers[0].doi == "10.1086/123456"
    assert papers[0].title == "Exoplanet atmospheric characterization with JWST"
    assert papers[0].year == 2023
    assert papers[0].source == PaperSource.ADS
    assert len(papers[0].authors) == 2
    assert papers[0].metadata.get("bibcode") == "2023ApJ...123..456S"
    assert papers[0].metadata.get("arxiv_id") == "2301.12345"


@pytest.mark.asyncio
async def test_search_sends_auth_header(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider

    headers_sent: list[dict] = []

    async def mock_get(self, url, *, headers=None, **kwargs):
        headers_sent.append(headers or {})
        return _mock_resp({"response": {"numFound": 0, "docs": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="myadstoken")
    await provider.search("test")
    assert any("Authorization" in h for h in headers_sent)
    assert any("myadstoken" in str(h) for h in headers_sent)


@pytest.mark.asyncio
async def test_year_filter_in_query(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider

    queries_sent: list[str] = []

    async def mock_get(self, url, *, params=None, **kwargs):
        queries_sent.append((params or {}).get("q", ""))
        return _mock_resp({"response": {"numFound": 0, "docs": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="tok")
    await provider.search("galaxy formation", year_min=2020, year_max=2023)
    assert queries_sent
    assert "2020" in queries_sent[0]
    assert "2023" in queries_sent[0]


@pytest.mark.asyncio
async def test_http_error_returns_empty(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider

    async def mock_get(self, url, **kwargs):
        raise Exception("auth failed")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="tok")
    papers = await provider.search("test")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.ads_search import ADSSearchProvider
    p = ADSSearchProvider(api_key="tok")
    assert p.name == "ads"
    assert "astronomy" in p.domains
    assert p.tier == "external"
    assert p.retry == 1
