"""Fetch a URL and return clean Markdown suitable for KB ingest.

Used by the ``ingest_urls_to_kb`` MCP tool. The output flows into the
existing heading-aware Markdown chunker (``pipeline.chunking_dispatch
._chunk_markdown``), so no new chunking logic is needed downstream.

Two routes:

1. **GitHub repository URLs** (``github.com/owner/repo`` and friends)
   short-circuit to a raw-README fetch via GitHub's REST API. The HTML
   of a GitHub page is mostly chrome (nav, file tree, social proof);
   the canonical content is the project README.md, which is already
   Markdown. We grab it directly and skip the HTML-to-MD round-trip.

2. **Everything else** fetches the page HTML and converts to Markdown.
   When the optional ``[html-ingest]`` extra is installed (trafilatura),
   the conversion preserves headings + lists and strips boilerplate
   (nav, footers, cookie banners, ads). Without it, falls back to a
   basic BeautifulSoup text extraction — usable but unstructured.

Both routes return ``(markdown_text, fetched_title)`` or raise.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger(__name__)


_REALISTIC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# GitHub route
# ---------------------------------------------------------------------------


def _parse_github_repo(url: str) -> tuple[str, str] | None:
    """Extract ``(owner, repo)`` from a GitHub URL, or ``None`` if not GitHub.

    Accepts the common surface forms:

    - ``https://github.com/owner/repo``
    - ``https://github.com/owner/repo/``
    - ``https://github.com/owner/repo/tree/main``
    - ``https://github.com/owner/repo/blob/main/README.md``
    - ``github.com/owner/repo`` (no scheme)
    """
    m = re.search(
        r"github\.com/([^/\s]+)/([^/\s#?]+)",
        url,
    )
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    # Drop ``.git`` suffix if present
    repo = re.sub(r"\.git$", "", repo)
    return owner, repo


async def _fetch_github_readme(
    owner: str,
    repo: str,
    *,
    http_client: httpx.AsyncClient,
) -> tuple[str, str]:
    """Fetch the README markdown for ``owner/repo`` via the GitHub API.

    Returns ``(markdown, title)``. Raises on 4xx/5xx so the caller can
    surface a clear error.
    """
    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    r = await http_client.get(
        api_url,
        headers={"Accept": "application/vnd.github.raw"},
        timeout=20.0,
    )
    r.raise_for_status()
    md = r.text
    # GitHub READMEs typically start with an H1 — use it as the title
    # if present, otherwise fall back to the repo name.
    title = f"{owner}/{repo}"
    m = re.search(r"^#\s+(.+?)\s*$", md, re.MULTILINE)
    if m:
        title = m.group(1).strip()
    return md, title


# ---------------------------------------------------------------------------
# Generic HTML route
# ---------------------------------------------------------------------------


def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown.

    Prefers ``trafilatura`` (installed via the ``[html-ingest]`` extra)
    because it strips boilerplate (nav, ads, cookie banners) and
    preserves heading hierarchy + lists. Falls back to BeautifulSoup
    text extraction when trafilatura isn't installed — usable for
    chunking but loses structure.
    """
    try:
        import trafilatura  # type: ignore[import-not-found]
    except ImportError:
        logger.info(
            "url_to_markdown_fallback",
            reason="trafilatura_not_installed",
        )
        # Soft fallback: strip tags, return plaintext. The downstream
        # markdown chunker will treat the whole document as one block
        # — not ideal but better than nothing.
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        main = soup.find("article") or soup.find("main") or soup
        return main.get_text(separator="\n\n", strip=True)

    md = trafilatura.extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_links=False,  # links bloat chunks without aiding retrieval
        favor_precision=True,  # err on the side of less, more focused content
    )
    return md or ""


def _extract_title_from_html(html: str) -> str:
    """Pull the page <title> as a best-effort document title."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    # Drop common site suffixes ("My Site - Article" -> "Article")
    for sep in (" | ", " — ", " - "):
        if sep in title:
            parts = title.split(sep)
            # Heuristic: longer half is usually the article title
            title = max(parts, key=len).strip()
            break
    return title


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def fetch_url_as_markdown(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    llm_client: Any | None = None,
    youtube_correct: bool = False,
) -> tuple[str, str]:
    """Fetch ``url`` and return ``(markdown_text, title)`` for KB ingest.

    Three routes:

    - **GitHub repos** ``github.com/owner/repo`` → raw README via the
      GitHub REST API.
    - **YouTube videos** ``youtube.com/watch?v=...`` /
      ``youtu.be/...`` → public transcript (manual or auto-captions)
      via ``youtube-transcript-api``. Requires the ``[youtube-ingest]``
      extra. LLM correction is **opt-in** via ``youtube_correct=True``
      (cost concern on long videos). When skipped, a warning header
      is prepended to the markdown so downstream chunks carry the
      "may be garbled" signal.
    - **Everything else** → HTML via trafilatura (or BS4 fallback).

    Raises ``httpx.HTTPError`` / ``ValueError`` for unreachable URLs
    or empty results — callers should treat exceptions as per-URL
    failures rather than aborting a batch.
    """
    client = http_client or httpx.AsyncClient(
        timeout=30.0, follow_redirects=True,
        headers={"User-Agent": _REALISTIC_UA},
    )
    should_close = http_client is None

    try:
        from perspicacite.pipeline.download.youtube import (
            fetch_youtube_transcript,
            is_youtube_url,
        )
        if is_youtube_url(url):
            return await fetch_youtube_transcript(
                url, http_client=client, llm_client=llm_client,
                correct_with_llm=youtube_correct,
            )

        gh = _parse_github_repo(url)
        if gh:
            owner, repo = gh
            md, title = await _fetch_github_readme(
                owner, repo, http_client=client,
            )
            if not md.strip():
                raise ValueError(f"GitHub README for {owner}/{repo} is empty")
            return md, title

        r = await client.get(url, headers={"User-Agent": _REALISTIC_UA})
        r.raise_for_status()
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "xml" not in ctype:
            # Direct markdown / plaintext / etc — return as-is
            return r.text, _extract_title_from_html(r.text) or url
        md = _html_to_markdown(r.text)
        if not md.strip():
            raise ValueError(f"no content extracted from {url}")
        title = _extract_title_from_html(r.text) or url
        return md, title
    finally:
        if should_close:
            await client.aclose()
