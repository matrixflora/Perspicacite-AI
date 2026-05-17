# tests/unit/test_inspire_search.py
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from perspicacite.models.papers import PaperSource

_SAMPLE_RESPONSE = {
    "hits": {
        "total": 1,
        "hits": [
            {
                "id": "1234567",
                "metadata": {
                    "titles": [{"title": "Dark matter detection theory"}],
                    "authors": [
                        {"full_name": "Smith, John"},
                        {"full_name": "Jones, Alice"},
                    ],
                    "publication_info": [{"year": 2023, "journal_title": "Physical Review D"}],
                    "dois": [{"value": "10.1103/PhysRevD.107.123456"}],
                    "arxiv_eprints": [{"value": "2301.12345"}],
                    "abstracts": [{"value": "We study dark matter detection."}],
                    "texkeys": ["Smith:2023abc"],
                },
            }
        ],
    }
}


def _mock_resp(data: dict):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_search_returns_papers(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider

    async def mock_get(self, url, **kwargs):
        return _mock_resp(_SAMPLE_RESPONSE)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    papers = await provider.search("dark matter detection")

    assert len(papers) == 1
    assert papers[0].doi == "10.1103/PhysRevD.107.123456"
    assert papers[0].title == "Dark matter detection theory"
    assert papers[0].year == 2023
    assert papers[0].journal == "Physical Review D"
    assert papers[0].source == PaperSource.INSPIRE_HEP
    assert len(papers[0].authors) == 2
    assert papers[0].metadata.get("arxiv_id") == "2301.12345"
    assert papers[0].metadata.get("texkey") == "Smith:2023abc"


@pytest.mark.asyncio
async def test_year_filter_appended_to_query(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider

    queries_sent: list[str] = []

    async def mock_get(self, url, *, params=None, **kwargs):
        queries_sent.append((params or {}).get("q", ""))
        return _mock_resp({"hits": {"total": 0, "hits": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    await provider.search("quantum gravity", year_min=2020, year_max=2023)
    assert queries_sent
    assert "2020" in queries_sent[0]
    assert "2023" in queries_sent[0]


@pytest.mark.asyncio
async def test_search_empty_result(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider

    async def mock_get(self, url, **kwargs):
        return _mock_resp({"hits": {"total": 0, "hits": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    papers = await provider.search("nonexistent topic xyz")
    assert papers == []


@pytest.mark.asyncio
async def test_http_error_returns_empty(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider

    async def mock_get(self, url, **kwargs):
        raise Exception("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    papers = await provider.search("test")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
    p = INSPIREHEPSearchProvider()
    assert p.name == "inspire"
    assert "physics" in p.domains
    assert p.tier == "reliable"
    assert p.retry == 0
