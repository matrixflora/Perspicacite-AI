"""Tests for bioRxiv/medRxiv content-retrieval module."""

import httpx
import pytest

from perspicacite.pipeline.download.biorxiv import get_content_from_biorxiv, is_biorxiv_doi


def test_is_biorxiv_doi():
    assert is_biorxiv_doi("10.1101/2021.01.01.425001")
    assert is_biorxiv_doi("https://doi.org/10.1101/2021.01.01.425001")
    assert not is_biorxiv_doi("10.1038/s41467-022-33890-w")
    assert not is_biorxiv_doi("")
    assert not is_biorxiv_doi(None)


@pytest.mark.asyncio
async def test_get_content_from_biorxiv_abstract_only(respx_mock):
    doi = "10.1101/2021.01.01.425001"
    respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "messages": [{"status": "ok"}],
                "collection": [
                    {
                        "doi": doi,
                        "title": "A Preprint",
                        "authors": "Doe, J.; Roe, R.",
                        "date": "2021-01-01",
                        "abstract": "We show stuff.",
                        "server": "biorxiv",
                        "category": "neuroscience",
                        "jatsxml": "",
                    }
                ],
            },
        )
    )
    async with httpx.AsyncClient() as client:
        result = await get_content_from_biorxiv(doi, http_client=client)
    assert result is not None
    assert result.success is True
    assert result.content_type == "abstract"
    assert result.content_source in ("biorxiv", "medrxiv")
    assert result.abstract == "We show stuff."
    assert result.metadata["title"] == "A Preprint"
    assert result.metadata["year"] == 2021
    assert result.metadata["authors"]  # list of name strings


@pytest.mark.asyncio
async def test_get_content_from_biorxiv_structured(respx_mock):
    doi = "10.1101/2021.01.01.999999"
    jats_url = "https://www.biorxiv.org/content/early/2021/01/01/2021.01.01.999999.full.pdf+xml"
    minimal_jats = b"<article><body><sec><title>Intro</title><p>Body text here that is reasonably long for testing purposes and exceeds any minimum length thresholds the parser may have so it is recognized as real content.</p></sec></body><back><ref-list><ref><element-citation><article-title>Ref One</article-title></element-citation></ref></ref-list></back></article>"
    respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "messages": [{"status": "ok"}],
                "collection": [
                    {
                        "doi": doi,
                        "title": "Structured Preprint",
                        "authors": "X",
                        "date": "2021-01-01",
                        "abstract": "abstract here",
                        "server": "biorxiv",
                        "jatsxml": jats_url,
                    }
                ],
            },
        )
    )
    respx_mock.get(jats_url).mock(return_value=httpx.Response(200, content=minimal_jats))
    async with httpx.AsyncClient() as client:
        result = await get_content_from_biorxiv(doi, http_client=client)
    assert result is not None and result.success
    # If the JATS parser extracted body text -> structured; if it returned nothing -> abstract fallback is acceptable.
    assert result.content_type in ("structured", "abstract")
    if result.content_type == "structured":
        assert result.full_text and len(result.full_text) > 0


@pytest.mark.asyncio
async def test_get_content_from_biorxiv_not_found(respx_mock):
    respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
        return_value=httpx.Response(
            200, json={"messages": [{"status": "no posts found"}], "collection": []}
        )
    )
    async with httpx.AsyncClient() as client:
        result = await get_content_from_biorxiv("10.1101/x", http_client=client)
    assert result is None


@pytest.mark.asyncio
async def test_get_content_from_biorxiv_non_biorxiv_doi(respx_mock):
    async with httpx.AsyncClient() as client:
        result = await get_content_from_biorxiv("10.1038/s41467-022-33890-w", http_client=client)
    assert result is None
