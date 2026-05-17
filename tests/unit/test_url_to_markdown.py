"""Tests for ``perspicacite.pipeline.download.url_to_markdown``.

The module has two routes:

- ``github.com/owner/repo`` short-circuits to the GitHub REST API for
  raw README markdown.
- Everything else fetches HTML and converts via ``trafilatura`` when
  installed, or a BeautifulSoup text-extraction fallback.

The tests mock the network with ``respx`` and exercise both routes.
"""
from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.download.url_to_markdown import (
    _extract_title_from_html,
    _parse_github_repo,
    fetch_url_as_markdown,
)

# ---------------------------------------------------------------------------
# _parse_github_repo
# ---------------------------------------------------------------------------


def test_parse_github_simple_url():
    assert _parse_github_repo("https://github.com/huggingface/smolagents") == (
        "huggingface", "smolagents",
    )


def test_parse_github_url_with_path():
    assert _parse_github_repo(
        "https://github.com/langchain-ai/langgraph/blob/main/README.md"
    ) == ("langchain-ai", "langgraph")


def test_parse_github_strips_git_suffix():
    assert _parse_github_repo("https://github.com/anthropics/claude.git") == (
        "anthropics", "claude",
    )


def test_parse_github_no_scheme():
    assert _parse_github_repo("github.com/owner/repo") == ("owner", "repo")


def test_parse_github_returns_none_for_non_github():
    assert _parse_github_repo("https://example.com/foo/bar") is None
    assert _parse_github_repo("https://anthropic.com/engineering") is None


# ---------------------------------------------------------------------------
# _extract_title_from_html
# ---------------------------------------------------------------------------


def test_extract_title_strips_site_suffix():
    html = "<html><head><title>My Article — My Site</title></head></html>"
    # "My Article" and "My Site" — the longer half wins
    assert _extract_title_from_html(html) == "My Article"


def test_extract_title_no_title_tag():
    assert _extract_title_from_html("<html><body>no title</body></html>") == ""


def test_extract_title_collapses_whitespace():
    html = "<html><head><title>\n  Hello\n\n  World  \n</title></head></html>"
    assert _extract_title_from_html(html) == "Hello World"


# ---------------------------------------------------------------------------
# Fetch — GitHub route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_github_returns_raw_readme(respx_mock):
    readme_md = (
        "# smolagents\n\n"
        "A smol library to build great agentic systems.\n\n"
        "## Installation\n\n"
        "```bash\npip install smolagents\n```\n"
    )
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/huggingface/smolagents/readme"
    ).mock(return_value=httpx.Response(200, text=readme_md))

    async with httpx.AsyncClient() as http:
        md, title = await fetch_url_as_markdown(
            "https://github.com/huggingface/smolagents",
            http_client=http,
        )
    assert title == "smolagents"
    assert "pip install smolagents" in md
    assert md.startswith("# smolagents")


@pytest.mark.asyncio
async def test_fetch_github_raises_on_empty_readme(respx_mock):
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/.*/readme"
    ).mock(return_value=httpx.Response(200, text=""))

    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="empty"):
            await fetch_url_as_markdown(
                "https://github.com/owner/empty-repo",
                http_client=http,
            )


@pytest.mark.asyncio
async def test_fetch_github_raises_on_404(respx_mock):
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/.*/readme"
    ).mock(return_value=httpx.Response(404, text="Not Found"))

    async with httpx.AsyncClient() as http:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_url_as_markdown(
                "https://github.com/owner/missing",
                http_client=http,
            )


# ---------------------------------------------------------------------------
# Fetch — HTML route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_html_returns_markdown_via_fallback_when_trafilatura_missing(
    respx_mock, monkeypatch,
):
    """When trafilatura isn't installed, the BS4 fallback should still
    extract text from <article> tags and strip nav/footer."""
    html = """
    <html><head><title>Test Article</title></head>
    <body>
      <nav>SHOULD NOT APPEAR</nav>
      <article>
        <h1>Real Headline</h1>
        <p>This is the body paragraph that should be extracted.</p>
      </article>
      <footer>copyright junk</footer>
    </body></html>
    """
    respx_mock.get(url__regex=r"https://example\.com/.*").mock(
        return_value=httpx.Response(
            200, text=html, headers={"content-type": "text/html"},
        )
    )

    # Force the trafilatura fallback path
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == "trafilatura":
            raise ImportError("not installed for this test")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    async with httpx.AsyncClient() as http:
        md, title = await fetch_url_as_markdown(
            "https://example.com/article",
            http_client=http,
        )
    assert "Real Headline" in md
    assert "body paragraph" in md
    assert "SHOULD NOT APPEAR" not in md
    assert "copyright junk" not in md
    assert title == "Test Article"


@pytest.mark.asyncio
async def test_fetch_non_html_content_passes_through(respx_mock):
    """A response with Content-Type other than html/xml is returned as-is.
    Lets users point the tool at a raw ``.md`` URL on a static host."""
    raw_md = "# Hello\n\nThis is already markdown."
    respx_mock.get(url__regex=r"https://example\.com/.*\.md").mock(
        return_value=httpx.Response(
            200, text=raw_md,
            headers={"content-type": "text/markdown; charset=utf-8"},
        )
    )

    async with httpx.AsyncClient() as http:
        md, _title = await fetch_url_as_markdown(
            "https://example.com/doc.md",
            http_client=http,
        )
    assert md == raw_md


@pytest.mark.asyncio
async def test_fetch_raises_on_html_with_no_extractable_content(
    respx_mock, monkeypatch,
):
    """Empty body should raise so the caller knows the URL failed."""
    respx_mock.get(url__regex=r"https://example\.com/.*").mock(
        return_value=httpx.Response(
            200, text="<html><body></body></html>",
            headers={"content-type": "text/html"},
        )
    )

    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == "trafilatura":
            raise ImportError("force fallback")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="no content extracted"):
            await fetch_url_as_markdown(
                "https://example.com/empty",
                http_client=http,
            )
