"""Title-based DOI discovery for entries with no DOI or usable URL.

When the bibtex/url route can't produce a routable identifier, this
module tries to recover a DOI by querying scholarly metadata APIs
with the paper title. Used as a last-resort fallback inside
``_resolve_push_input`` (mcp/server.py) so URL-only / title-only
entries can still route through the DOI path (real PDF fetch +
preprint item_type) instead of being demoted to a webpage shell.

Tiers (cheapest -> broadest):

1. OpenAlex   ``works?search=<title>``
2. Crossref   ``works?query.bibliographic=<title>&query.author=<lastname>``
3. Semantic Scholar ``paper/search?query=<title>``
4. arXiv      ``query?search_query=ti:"<title>"`` (returns arXiv DOI)
5. Headless Chromium -> Google Scholar (opt-in; needs the ``browser``
   extra and ``playwright install chromium``). Used when HTTP tiers
   miss — e.g., bib entries with title typos that defeat fuzzy match,
   or very-new papers not yet indexed by the JSON APIs. Each DOI
   scraped from the Scholar SERP is verified via Crossref before
   being accepted.

Each tier validates the top hits against the bib entry:

- first-author last-name overlap (case-insensitive substring),
- year +/-1 (when both sides have one),
- candidate title length within 60-150% of the bib title.

Returns the first DOI that passes validation, or ``None``.

Agent-side note: clients with a browser MCP available (e.g. the
``claude-in-chrome`` server) can also pre-resolve a title to a DOI
themselves and pass it to ``push_to_zotero`` directly. The Chromium
tier here exists for non-agent server-side callers (batch ingest,
``build_kbs_from_zotero``, CLI flows) that can't reach an agent.
"""
from __future__ import annotations

import os
import re
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger(__name__)


def _last_name(author: str) -> str:
    """Extract last name. Accepts ``"Smith, John"`` or ``"John Smith"``."""
    author = (author or "").strip()
    if not author:
        return ""
    if "," in author:
        return author.split(",", 1)[0].strip().lower()
    parts = author.split()
    return parts[-1].lower() if parts else ""


def _validate_match(
    candidate_title: str,
    candidate_authors: list[str],
    candidate_year: int | None,
    target_title: str,
    target_first_lastname: str,
    target_year: int | None,
) -> bool:
    """Validate a candidate scholar API result against the bib entry."""
    if not candidate_title:
        return False
    # First-author last-name overlap (substring, case-insensitive).
    # Only enforce when target side has a last name.
    if target_first_lastname:
        if not candidate_authors:
            return False
        cand_first_last = _last_name(candidate_authors[0])
        if (
            cand_first_last != target_first_lastname
            and target_first_lastname not in cand_first_last
            and cand_first_last not in target_first_lastname
        ):
            return False
    # Year proximity. Only enforce when both sides have a year.
    if target_year is not None and candidate_year is not None:
        try:
            if abs(int(candidate_year) - int(target_year)) > 1:
                return False
        except (TypeError, ValueError):
            pass
    # Title-length sanity: avoids matches where the search returned a
    # short related work or a long survey that happens to mention this
    # paper's keywords.
    t_len = len(target_title)
    c_len = len(candidate_title)
    return not (t_len and (c_len < 0.6 * t_len or c_len > 1.5 * t_len))


async def _try_openalex(
    title: str,
    first_lastname: str,
    year: int | None,
    *,
    http_client: Any,
) -> str | None:
    mailto = os.getenv("OPENALEX_MAILTO") or os.getenv("UNPAYWALL_EMAIL") or ""
    params: dict[str, Any] = {"search": title, "per-page": 5}
    if mailto:
        params["mailto"] = mailto
    r = await http_client.get(
        "https://api.openalex.org/works", params=params, timeout=20.0,
    )
    if r.status_code != 200:
        return None
    for w in (r.json() or {}).get("results", []) or []:
        c_title = (w.get("title") or "").strip()
        c_authors = [
            ((a.get("author") or {}).get("display_name") or "")
            for a in (w.get("authorships") or [])
        ]
        c_year = w.get("publication_year")
        c_doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
        if c_doi and _validate_match(
            c_title, c_authors, c_year, title, first_lastname, year,
        ):
            return c_doi
    return None


async def _try_crossref(
    title: str,
    first_lastname: str,
    year: int | None,
    *,
    http_client: Any,
) -> str | None:
    params: dict[str, Any] = {"query.bibliographic": title, "rows": 5}
    if first_lastname:
        params["query.author"] = first_lastname
    r = await http_client.get(
        "https://api.crossref.org/works", params=params, timeout=20.0,
    )
    if r.status_code != 200:
        return None
    items = ((r.json() or {}).get("message") or {}).get("items") or []
    for w in items:
        titles = w.get("title") or []
        c_title = (titles[0] if titles else "").strip()
        c_authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (w.get("author") or [])
            if a.get("family")
        ]
        c_year: int | None = None
        date_parts = ((w.get("issued") or {}).get("date-parts") or [[None]])[0]
        if date_parts and date_parts[0]:
            try:
                c_year = int(date_parts[0])
            except (TypeError, ValueError):
                c_year = None
        c_doi = (w.get("DOI") or "").strip()
        if c_doi and _validate_match(
            c_title, c_authors, c_year, title, first_lastname, year,
        ):
            return c_doi
    return None


async def _try_semantic_scholar(
    title: str,
    first_lastname: str,
    year: int | None,
    *,
    http_client: Any,
) -> str | None:
    params: dict[str, Any] = {
        "query": title,
        "limit": 5,
        "fields": "title,authors,year,externalIds",
    }
    r = await http_client.get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params=params,
        timeout=20.0,
    )
    if r.status_code != 200:
        return None
    for w in (r.json() or {}).get("data") or []:
        c_title = (w.get("title") or "").strip()
        c_authors = [(a.get("name") or "") for a in (w.get("authors") or [])]
        c_year = w.get("year")
        ext = w.get("externalIds") or {}
        c_doi = (ext.get("DOI") or "").strip()
        if not c_doi and ext.get("ArXiv"):
            c_doi = f"10.48550/arXiv.{ext['ArXiv'].strip()}"
        if c_doi and _validate_match(
            c_title, c_authors, c_year, title, first_lastname, year,
        ):
            return c_doi
    return None


async def _try_arxiv(
    title: str,
    first_lastname: str,
    year: int | None,
    *,
    http_client: Any,
) -> str | None:
    # arXiv API doesn't return JSON; entries come back as Atom XML.
    # We parse the minimum we need with a regex rather than pulling
    # in an XML parser dependency.
    params: dict[str, Any] = {
        "search_query": f'ti:"{title}"',
        "start": 0,
        "max_results": 5,
    }
    r = await http_client.get(
        "https://export.arxiv.org/api/query",
        params=params,
        timeout=20.0,
    )
    if r.status_code != 200:
        return None
    text = r.text or ""
    entries = re.split(r"<entry>", text)[1:]
    for entry in entries:
        title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
        cand_title = (title_m.group(1) if title_m else "").strip()
        cand_title = re.sub(r"\s+", " ", cand_title)
        authors = re.findall(r"<name>(.*?)</name>", entry)
        published = re.search(r"<published>(\d{4})", entry)
        cand_year = int(published.group(1)) if published else None
        id_m = re.search(
            r"<id>https?://arxiv\.org/abs/([0-9]{4}\.[0-9]{4,6})",
            entry,
        )
        if not id_m:
            continue
        arxiv_id = id_m.group(1)
        if _validate_match(
            cand_title, authors, cand_year, title, first_lastname, year,
        ):
            return f"10.48550/arXiv.{arxiv_id}"
    return None


async def _try_chromium_scholar(
    title: str,
    first_lastname: str,
    year: int | None,
    *,
    http_client: Any,
) -> str | None:
    """Headless Chromium -> Google Scholar -> DOI extraction + Crossref verify.

    Opt-in tier. Returns ``None`` immediately when ``playwright`` is
    not importable. When Chromium isn't installed either, the
    ``async_playwright`` launch raises and the tier logs + returns
    ``None``.

    Strategy: render the Scholar SERP for ``"<title> <year> <author>"``,
    extract DOI patterns from the rendered HTML (max 5 unique), then
    confirm each via Crossref ``/works/<doi>`` and validate the
    returned metadata against the bib entry. The Crossref verify step
    is essential — Scholar SERP DOIs can come from neighboring
    "cited by" / "related works" links, not the actual hit.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.info(
            "title_resolver_chromium_skipped",
            reason="playwright_not_installed",
        )
        return None

    from urllib.parse import quote

    query_parts = [title]
    if year:
        query_parts.append(str(year))
    if first_lastname:
        query_parts.append(first_lastname)
    scholar_url = (
        "https://scholar.google.com/scholar?q=" + quote(" ".join(query_parts))
    )

    html = ""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                await page.goto(
                    scholar_url, wait_until="domcontentloaded", timeout=30000,
                )
                html = await page.content()
            finally:
                await browser.close()
    except Exception as exc:
        logger.info("title_resolver_chromium_failed", error=str(exc))
        return None

    # Pull DOI candidates from the rendered HTML. The pattern is
    # deliberately conservative — Crossref's syntactically valid set
    # is wider, but this catches everything we'd ever match against.
    doi_pattern = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
    seen: set[str] = set()
    candidates: list[str] = []
    for raw in doi_pattern.findall(html):
        cleaned = raw.rstrip('.,;)"\'')
        if cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)
        if len(candidates) >= 5:
            break

    for doi in candidates:
        try:
            r = await http_client.get(
                f"https://api.crossref.org/works/{doi}",
                timeout=15.0,
            )
            if r.status_code != 200:
                continue
            msg = ((r.json() or {}).get("message") or {})
            cand_title = ((msg.get("title") or [""])[0]).strip()
            cand_authors = [
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (msg.get("author") or [])
                if a.get("family")
            ]
            cand_year: int | None = None
            date_parts = (
                (msg.get("issued") or {}).get("date-parts") or [[None]]
            )[0]
            if date_parts and date_parts[0]:
                try:
                    cand_year = int(date_parts[0])
                except (TypeError, ValueError):
                    cand_year = None
            if _validate_match(
                cand_title, cand_authors, cand_year, title, first_lastname, year,
            ):
                return doi
        except Exception:
            continue
    return None


async def resolve_doi_from_title(
    title: str,
    authors: list[str] | None,
    year: int | str | None,
    *,
    http_client: Any,
    enable_browser: bool = False,
) -> str | None:
    """Try to discover a DOI from a paper title via scholarly metadata APIs.

    Walks OpenAlex -> Crossref -> Semantic Scholar -> arXiv; first
    validated match wins. With ``enable_browser=True``, appends a
    headless-Chromium Google-Scholar tier as the final fallback. A
    match requires first-author last-name overlap, year within +/-1,
    and title length 60-150% of the bib entry.

    Returns the discovered DOI bare (``"10.1234/abc"``), or ``None``
    if no tier produced a validated hit. Network/HTTP errors at any
    tier are logged and treated as misses — the resolver never raises.
    """
    if not title or len(title.strip()) < 10:
        return None
    title = title.strip()

    first_lastname = _last_name(authors[0]) if authors else ""

    year_int: int | None = None
    if year is not None:
        try:
            year_int = int(str(year)[:4])
        except (TypeError, ValueError):
            year_int = None

    tiers: list[tuple[str, Any]] = [
        ("openalex", _try_openalex),
        ("crossref", _try_crossref),
        ("semantic_scholar", _try_semantic_scholar),
        ("arxiv", _try_arxiv),
    ]
    if enable_browser:
        tiers.append(("chromium_scholar", _try_chromium_scholar))

    for tier_name, fn in tiers:
        try:
            doi = await fn(
                title, first_lastname, year_int, http_client=http_client,
            )
            if doi:
                logger.info(
                    "title_resolver_match",
                    tier=tier_name,
                    doi=doi,
                    title=title[:80],
                )
                return doi
        except Exception as exc:
            logger.info(
                "title_resolver_tier_failed",
                tier=tier_name,
                error=str(exc),
            )

    logger.info(
        "title_resolver_no_match",
        title=title[:80],
        first_lastname=first_lastname,
        year=year_int,
        browser_used=enable_browser,
    )
    return None
