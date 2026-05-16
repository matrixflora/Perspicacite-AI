"""Tests for ``perspicacite.pipeline.download.title_resolver``.

The resolver is the last-resort fallback that turns a title + author
+ year into a DOI when the bib entry / URL provided no routable
identifier. Each tier is mocked independently via ``respx``; the
resolver should walk them in order and stop at the first validated
match.
"""
from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.download.title_resolver import (
    _validate_match,
    resolve_doi_from_title,
)

# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------


def test_validate_accepts_close_match():
    assert _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Ashish Vaswani", "Noam Shazeer"],
        candidate_year=2017,
        target_title="Attention Is All You Need",
        target_first_lastname="vaswani",
        target_year=2017,
    )


def test_validate_rejects_wrong_first_author():
    assert not _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Some Other Person"],
        candidate_year=2017,
        target_title="Attention Is All You Need",
        target_first_lastname="vaswani",
        target_year=2017,
    )


def test_validate_rejects_year_off_by_more_than_one():
    assert not _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Ashish Vaswani"],
        candidate_year=2010,
        target_title="Attention Is All You Need",
        target_first_lastname="vaswani",
        target_year=2017,
    )


def test_validate_accepts_year_off_by_one():
    # preprint vs journal year drift is common — ±1 is allowed
    assert _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Ashish Vaswani"],
        candidate_year=2018,
        target_title="Attention Is All You Need",
        target_first_lastname="vaswani",
        target_year=2017,
    )


def test_validate_rejects_title_length_mismatch():
    # candidate is much longer (e.g. survey that mentions this work)
    assert not _validate_match(
        candidate_title=(
            "A Comprehensive Survey on Attention Mechanisms in Modern "
            "Deep Learning Architectures Across Multiple Domains"
        ),
        candidate_authors=["Vaswani A"],
        candidate_year=2017,
        target_title="Attention Is All You Need",
        target_first_lastname="vaswani",
        target_year=2017,
    )


# ---------------------------------------------------------------------------
# Tier 1: OpenAlex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openalex_tier_returns_doi(respx_mock):
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Attention Is All You Need",
                        "publication_year": 2017,
                        "doi": "https://doi.org/10.48550/arXiv.1706.03762",
                        "authorships": [
                            {"author": {"display_name": "Ashish Vaswani"}},
                        ],
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "Attention Is All You Need",
            ["Vaswani, Ashish"],
            2017,
            http_client=http,
        )
    assert doi == "10.48550/arXiv.1706.03762"


# ---------------------------------------------------------------------------
# Tier 2: Crossref (fallback after OpenAlex returns no validated hit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crossref_tier_used_when_openalex_misses(respx_mock):
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.1038/s41586-024-12345-6",
                            "title": ["Some Nature Paper Title"],
                            "issued": {"date-parts": [[2024]]},
                            "author": [
                                {"given": "Jane", "family": "Doe"},
                                {"given": "John", "family": "Roe"},
                            ],
                        }
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "Some Nature Paper Title",
            ["Doe, Jane"],
            2024,
            http_client=http,
        )
    assert doi == "10.1038/s41586-024-12345-6"


# ---------------------------------------------------------------------------
# Tier 3: Semantic Scholar (after OpenAlex + Crossref both miss)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semantic_scholar_tier_used_after_first_two_miss(respx_mock):
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works.*").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx_mock.get(
        url__regex=r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "title": "PaperBench Evaluating AI Research",
                        "year": 2025,
                        "authors": [{"name": "Giulio Starace"}],
                        "externalIds": {"ArXiv": "2504.01848"},
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "PaperBench Evaluating AI Research",
            ["Starace, Giulio"],
            2025,
            http_client=http,
        )
    assert doi == "10.48550/arXiv.2504.01848"


# ---------------------------------------------------------------------------
# Tier 4: arXiv (after the three JSON tiers miss)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arxiv_tier_used_as_final_fallback(respx_mock):
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works.*").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx_mock.get(
        url__regex=r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"
    ).mock(return_value=httpx.Response(200, json={"data": []}))

    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<id>http://arxiv.org/abs/2510.09901</id>
<title>Autonomous Agents for Scientific Discovery</title>
<published>2025-10-11T00:00:00Z</published>
<name>Lianhao Zhou</name>
<name>Hongyi Ling</name>
</entry>
</feed>
"""
    respx_mock.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(
        return_value=httpx.Response(200, text=atom)
    )

    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "Autonomous Agents for Scientific Discovery",
            ["Zhou, Lianhao"],
            2025,
            http_client=http,
        )
    assert doi == "10.48550/arXiv.2510.09901"


# ---------------------------------------------------------------------------
# All tiers miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_none_when_no_tier_matches(respx_mock):
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works.*").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx_mock.get(
        url__regex=r"https://api\.semanticscholar\.org/graph/v1/paper/search.*"
    ).mock(return_value=httpx.Response(200, json={"data": []}))
    respx_mock.get(url__regex=r"https://export\.arxiv\.org/api/query.*").mock(
        return_value=httpx.Response(200, text="<feed></feed>")
    )
    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "Some Obscure Paper Nobody Has Heard Of",
            ["Nobody, Mr"],
            2024,
            http_client=http,
        )
    assert doi is None


# ---------------------------------------------------------------------------
# Validation actually rejects bad matches in the network path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openalex_match_rejected_when_author_wrong_falls_through(respx_mock):
    """OpenAlex returns a hit but with the wrong first author → reject
    and fall through. Crossref should be queried next."""
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Attention Is All You Need",
                        "publication_year": 2017,
                        "doi": "https://doi.org/10.9999/wrong",
                        "authorships": [
                            {"author": {"display_name": "Wrong Person"}},
                        ],
                    }
                ]
            },
        )
    )
    cr_route = respx_mock.get(
        url__regex=r"https://api\.crossref\.org/works.*"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.48550/arXiv.1706.03762",
                            "title": ["Attention Is All You Need"],
                            "issued": {"date-parts": [[2017]]},
                            "author": [
                                {"given": "Ashish", "family": "Vaswani"},
                            ],
                        }
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "Attention Is All You Need",
            ["Vaswani, Ashish"],
            2017,
            http_client=http,
        )
    assert doi == "10.48550/arXiv.1706.03762"
    assert cr_route.called


# ---------------------------------------------------------------------------
# Network error in one tier should not crash; resolver moves on
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_error_in_one_tier_falls_through(respx_mock):
    respx_mock.get(url__regex=r"https://api\.openalex\.org/works.*").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.1234/rescue",
                            "title": ["Attention Is All You Need"],
                            "issued": {"date-parts": [[2017]]},
                            "author": [
                                {"given": "Ashish", "family": "Vaswani"},
                            ],
                        }
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        doi = await resolve_doi_from_title(
            "Attention Is All You Need",
            ["Vaswani, Ashish"],
            2017,
            http_client=http,
        )
    assert doi == "10.1234/rescue"


# ---------------------------------------------------------------------------
# Empty / unreasonably short title is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_title_short_circuits():
    async with httpx.AsyncClient() as http:
        assert await resolve_doi_from_title("", [], 2024, http_client=http) is None
        assert (
            await resolve_doi_from_title("short", [], 2024, http_client=http)
            is None
        )
