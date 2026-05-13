"""Tests for Europe PMC structured full-text source."""

import httpx
import pytest

from perspicacite.pipeline.download.europepmc import get_content_from_europepmc

PMC_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<article xmlns:xlink="http://www.w3.org/1999/xlink">'
    b"<front><article-meta>"
    b"<title-group><article-title>Test article</article-title></title-group>"
    b"</article-meta></front>"
    b"<body><sec><title>Intro</title><p>Hello world. This text must be long "
    b"enough to pass the threshold downstream, so we pad with extra filler "
    b"content describing the experiment design, materials and methods, and a "
    b"brief overview of the results that justify the conclusions drawn in "
    b"this study.</p></sec></body>"
    b"</article>"
)

_EPMC_RE = r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/PMC/.*/fullTextXML"
_EPMC_SEARCH_RE = r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*"


@pytest.mark.asyncio
async def test_europepmc_returns_structured_for_known_pmcid(respx_mock):
    respx_mock.get(url__regex=_EPMC_RE).mock(
        return_value=httpx.Response(200, content=PMC_XML)
    )
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(
            doi=None, pmid=None, pmcid="PMC123", http_client=client
        )
    assert out is not None
    assert out.success is True
    assert out.content_type == "structured"
    assert out.content_source == "europepmc"
    assert "Hello world" in (out.full_text or "")


@pytest.mark.asyncio
async def test_europepmc_404_returns_none(respx_mock):
    _re = r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/PMC/PMC404/fullTextXML"
    respx_mock.get(url__regex=_re).mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(
            doi=None, pmid=None, pmcid="PMC404", http_client=client
        )
    assert out is None


@pytest.mark.asyncio
async def test_europepmc_resolves_doi_via_search(respx_mock):
    respx_mock.get(url__regex=_EPMC_SEARCH_RE).mock(
        return_value=httpx.Response(
            200, json={"resultList": {"result": [{"source": "MED", "id": "999"}]}}
        )
    )
    respx_mock.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/MED/999/fullTextXML"
    ).mock(return_value=httpx.Response(200, content=PMC_XML))
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(
            doi="10.1/x", pmid=None, pmcid=None, http_client=client
        )
    assert out is not None and out.success
    assert out.content_source == "europepmc"


@pytest.mark.asyncio
async def test_europepmc_no_doi_no_id_returns_none() -> None:
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(
            doi=None, pmid=None, pmcid=None, http_client=client
        )
    assert out is None


@pytest.mark.asyncio
async def test_europepmc_pmid_path(respx_mock):
    respx_mock.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/MED/12345/fullTextXML"
    ).mock(return_value=httpx.Response(200, content=PMC_XML))
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(
            doi=None, pmid="12345", pmcid=None, http_client=client
        )
    assert out is not None and out.success


@pytest.mark.asyncio
async def test_europepmc_search_bare_numeric_pmc_id_gets_prefix(respx_mock):
    """When search returns source=PMC with a bare numeric id (no 'PMC' prefix),
    the fullTextXML URL must include the 'PMC' prefix — e.g. PMC1234567."""
    respx_mock.get(url__regex=_EPMC_SEARCH_RE).mock(
        return_value=httpx.Response(
            200,
            json={"resultList": {"result": [{"source": "PMC", "id": "1234567"}]}},
        )
    )
    # Only register the prefixed URL; a bare-numeric URL would 404 (not registered)
    full_text_route = respx_mock.get(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC/PMC1234567/fullTextXML"
    ).mock(return_value=httpx.Response(200, content=PMC_XML))

    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(
            doi="10.1/bare", pmid=None, pmcid=None, http_client=client
        )

    assert full_text_route.called, (
        "fullTextXML was not fetched at the expected PMC-prefixed URL"
    )
    assert out is not None and out.success
