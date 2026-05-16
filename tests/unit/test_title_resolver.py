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
        target_authors=["Vaswani, Ashish"],
        target_year=2017,
    )


def test_validate_rejects_wrong_first_author():
    assert not _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Some Other Person"],
        candidate_year=2017,
        target_title="Attention Is All You Need",
        target_authors=["Vaswani, Ashish"],
        target_year=2017,
    )


def test_validate_rejects_year_off_by_more_than_one():
    assert not _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Ashish Vaswani"],
        candidate_year=2010,
        target_title="Attention Is All You Need",
        target_authors=["Vaswani, Ashish"],
        target_year=2017,
    )


def test_validate_accepts_year_off_by_one():
    # preprint vs journal year drift is common — ±1 is allowed
    assert _validate_match(
        candidate_title="Attention Is All You Need",
        candidate_authors=["Ashish Vaswani"],
        candidate_year=2018,
        target_title="Attention Is All You Need",
        target_authors=["Vaswani, Ashish"],
        target_year=2017,
    )


def test_validate_rejects_junk_unknown_author_with_loose_title():
    """Regression: bib entries with author='Unknown' must not match
    any arbitrary DOI just because the title length is in range.

    The old substring check treated ``target_first_lastname='unknown'``
    as a real surname, accepting any candidate whose lastname was a
    substring of 'unknown' (e.g. 'u', 'no', 'know'). Now we strip junk
    placeholders and require strong title overlap when no real author
    is available."""
    assert not _validate_match(
        candidate_title="A 1980 Workshop Paper On Symposium Graphics",
        candidate_authors=["Smith J"],
        candidate_year=1980,
        target_title="LangGraph: Build resilient language agents as graphs",
        target_authors=["Unknown"],
        target_year=None,
    )


def test_validate_accepts_swapped_chinese_name():
    """Regression: Zotero sometimes stores given+family swapped (esp.
    Chinese names). With first-author-only matching, "Qingyan, Guo"
    would yield target_first_lastname='qingyan' and never match the
    Crossref/arXiv record whose family name is 'Guo'. Now we pool all
    name tokens across all authors and accept any 4+ char overlap."""
    assert _validate_match(
        candidate_title=(
            "Connecting Large Language Models with Evolutionary "
            "Algorithms Yields Powerful Prompt Optimizers"
        ),
        candidate_authors=["Qingyan Guo", "Rui Wang", "Junliang Guo"],
        candidate_year=2024,
        target_title=(
            "Connecting Large Language Models with Evolutionary "
            "Algorithms Yields Powerful Prompt Optimizers"
        ),
        target_authors=["Qingyan, Guo", "Rui, Wang"],  # name parts in either field
        target_year=2024,
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
        target_authors=["Vaswani, Ashish"],
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


# ---------------------------------------------------------------------------
# Tier 5: headless Chromium (opt-in via enable_browser=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browser_tier_skipped_when_disabled(respx_mock):
    """With ``enable_browser=False`` (default), Chromium tier never
    runs even when all four HTTP tiers miss."""
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
            "Some Title That Doesn't Match Anywhere",
            ["Author, X"],
            2024,
            http_client=http,
            enable_browser=False,
        )
    assert doi is None


@pytest.mark.asyncio
async def test_browser_tier_returns_none_when_playwright_missing(monkeypatch):
    """Tier 5 short-circuits to ``None`` when playwright isn't
    importable, never blowing up the resolver."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.async_api":
            raise ImportError("playwright not installed in CI")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    from perspicacite.pipeline.download.title_resolver import (
        _try_chromium_scholar,
    )
    async with httpx.AsyncClient() as http:
        doi = await _try_chromium_scholar(
            "Some Title",
            ["Vaswani, Ashish"],
            2017,
            http_client=http,
        )
    assert doi is None


@pytest.mark.asyncio
async def test_browser_tier_scrapes_doi_and_verifies_via_crossref(
    monkeypatch, respx_mock,
):
    """Happy path: Chromium renders Scholar HTML, we extract a DOI,
    Crossref confirms title + author + year match."""
    # Fake playwright API: returns a chunk of HTML containing the DOI.
    scholar_html = """
    <html><body>
      <div class="gs_r">
        <h3><a href="https://doi.org/10.48550/arXiv.1706.03762">
          Attention Is All You Need
        </a></h3>
        <p>10.48550/arXiv.1706.03762 — Vaswani et al., 2017</p>
      </div>
    </body></html>
    """
    # Use a fake playwright module that yields ``scholar_html``.
    class _FakePage:
        async def goto(self, *a, **kw):
            return None

        async def content(self):
            return scholar_html

    class _FakeContext:
        async def new_page(self):
            return _FakePage()

    class _FakeBrowser:
        async def new_context(self, **kw):
            return _FakeContext()

        async def close(self):
            return None

    class _FakeChromium:
        async def launch(self, **kw):
            return _FakeBrowser()

    class _FakePlaywrightCtx:
        chromium = _FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    def _fake_async_playwright():
        return _FakePlaywrightCtx()

    import sys
    import types

    fake_mod = types.ModuleType("playwright.async_api")
    fake_mod.async_playwright = _fake_async_playwright
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_mod)

    # Crossref verification step
    respx_mock.get(
        url__regex=r"https://api\.crossref\.org/works/.*"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.48550/arXiv.1706.03762",
                    "title": ["Attention Is All You Need"],
                    "issued": {"date-parts": [[2017]]},
                    "author": [{"given": "Ashish", "family": "Vaswani"}],
                }
            },
        )
    )

    from perspicacite.pipeline.download.title_resolver import (
        _try_chromium_scholar,
    )
    async with httpx.AsyncClient() as http:
        doi = await _try_chromium_scholar(
            "Attention Is All You Need",
            ["Vaswani, Ashish"],
            2017,
            http_client=http,
        )
    assert doi == "10.48550/arXiv.1706.03762"


@pytest.mark.asyncio
async def test_browser_tier_rejects_when_crossref_metadata_doesnt_match(
    monkeypatch, respx_mock,
):
    """Scholar SERP often contains DOIs from neighbouring 'related work'
    citations. If Crossref shows a different author or wildly different
    title, we must reject and try the next DOI."""
    scholar_html = "Found 10.1234/wrong-paper and 10.5678/right-paper here."

    class _FakePage:
        async def goto(self, *a, **kw):
            return None

        async def content(self):
            return scholar_html

    class _FakeContext:
        async def new_page(self):
            return _FakePage()

    class _FakeBrowser:
        async def new_context(self, **kw):
            return _FakeContext()

        async def close(self):
            return None

    class _FakeChromium:
        async def launch(self, **kw):
            return _FakeBrowser()

    class _FakeCtx:
        chromium = _FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

    import sys
    import types

    fake_mod = types.ModuleType("playwright.async_api")
    fake_mod.async_playwright = lambda: _FakeCtx()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_mod)

    # Crossref returns wildly-wrong metadata for first DOI, correct for second
    def _crossref_response(request):
        url = str(request.url)
        if "wrong-paper" in url:
            return httpx.Response(
                200,
                json={
                    "message": {
                        "DOI": "10.1234/wrong-paper",
                        "title": ["A Totally Different Paper About Cats"],
                        "issued": {"date-parts": [[2010]]},
                        "author": [{"given": "Wrong", "family": "Person"}],
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {
                    "DOI": "10.5678/right-paper",
                    "title": ["Attention Is All You Need"],
                    "issued": {"date-parts": [[2017]]},
                    "author": [{"given": "Ashish", "family": "Vaswani"}],
                }
            },
        )

    respx_mock.get(
        url__regex=r"https://api\.crossref\.org/works/.*"
    ).mock(side_effect=_crossref_response)

    from perspicacite.pipeline.download.title_resolver import (
        _try_chromium_scholar,
    )
    async with httpx.AsyncClient() as http:
        doi = await _try_chromium_scholar(
            "Attention Is All You Need",
            ["Vaswani, Ashish"],
            2017,
            http_client=http,
        )
    assert doi == "10.5678/right-paper"
