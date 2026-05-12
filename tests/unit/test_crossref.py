import httpx
import pytest

from perspicacite.pipeline.download.crossref import enrich_from_crossref


@pytest.mark.asyncio
async def test_enrich_fills_missing_only(respx_mock):
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "title": ["Crossref Title"],
                    "published": {"date-parts": [[2020, 5]]},
                    "author": [{"given": "Jane", "family": "Doe"}],
                    "container-title": ["J. Test"],
                    "abstract": "<jats:p>An abstract.</jats:p>",
                    "reference": [{"DOI": "10.1/ref"}],
                    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
                }
            },
        )
    )
    base = {
        "title": "Existing Title",
        "authors": [],
        "year": None,
        "journal": None,
        "abstract": None,
    }
    async with httpx.AsyncClient() as client:
        patch = await enrich_from_crossref(
            "10.1/x", http_client=client, base_metadata=base, mailto="me@example.com"
        )
    # title was already present -> NOT patched:
    assert "title" not in patch
    assert patch["year"] == 2020
    assert patch["journal"] == "J. Test"
    assert patch["abstract"] == "An abstract."  # JATS tags stripped
    assert patch["authors"] == ["Jane Doe"]  # filled because base had empty list
    assert patch.get("references")  # filled because base had no references key
    assert patch.get("license")


@pytest.mark.asyncio
async def test_enrich_network_error_returns_empty(respx_mock):
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        side_effect=httpx.ConnectError("boom")
    )
    async with httpx.AsyncClient() as client:
        patch = await enrich_from_crossref(
            "10.1/x", http_client=client, base_metadata={"title": None}, mailto=None
        )
    assert patch == {}


@pytest.mark.asyncio
async def test_enrich_404_returns_empty(respx_mock):
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as client:
        patch = await enrich_from_crossref(
            "10.1/missing", http_client=client, base_metadata={"title": None}, mailto=None
        )
    assert patch == {}


@pytest.mark.asyncio
async def test_enrich_normalizes_doi(respx_mock):
    route = respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {"title": ["T"]}})
    )
    async with httpx.AsyncClient() as client:
        await enrich_from_crossref(
            "https://doi.org/10.1/abc",
            http_client=client,
            base_metadata={"title": None},
            mailto=None,
        )
    # the request URL must contain the bare DOI, not the doi.org prefix
    assert "10.1/abc" in str(route.calls[0].request.url)
    assert "doi.org" not in str(route.calls[0].request.url).split("/works/")[-1]
