"""HTML fallback capture (Priority 3b in the content-acquisition plan).

When the PDF route fails for a paper, fall back to capturing the publisher
landing page as a self-contained HTML snapshot. Worse than a PDF, better
than nothing — unlocks the body for open-abstract pages, gives the KB
chunker something to ingest, and gives Zotero an attachment to surface.

Three quality tiers are reported back so callers can weight chunks
appropriately:

- ``full_text_html``: page contains the article body (PMC HTML, BMC,
  MDPI, OA pages with body in HTML).
- ``extended_abstract``: page has abstract + section headers + figure
  captions but body is paywalled.
- ``bibliographic_stub``: page has only title, authors, DOI, abstract.

The module is intentionally dependency-light — uses the same stdlib HTML
parser the rest of the codebase relies on. A future enhancement can
plug Trafilatura in behind a feature flag for higher-quality main-content
extraction.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.download.html_capture")


# Length thresholds (chars of extracted text body) for the three tiers.
# Tuned against a Mimosa-AI sample of 8 publisher landing pages where the
# body sizes ranged from ~600 chars (Nature paywalled stub) to ~80k chars
# (PMC full-text HTML).
_TIER_FULLTEXT_MIN_CHARS = 8_000
_TIER_ABSTRACT_MIN_CHARS = 1_500


@dataclass(frozen=True)
class HtmlCapture:
    """One captured landing-page snapshot.

    ``path`` is where the HTML file was written. ``tier`` reports the
    extraction quality (see module docstring). ``char_count`` is the
    extracted-text character count — used both by the tier classifier
    and by downstream chunkers.
    """

    path: Path
    tier: str
    char_count: int
    extracted_title: str | None = None


class _TextExtractor(HTMLParser):
    """Collect visible text, drop <script>/<style>/<nav>/<header>/<footer> blocks."""

    _SKIP = {"script", "style", "nav", "header", "footer", "aside", "form"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._depth_skip = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP:
            self._depth_skip += 1
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP and self._depth_skip > 0:
            self._depth_skip -= 1
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._depth_skip > 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title and self.title is None:
            self.title = text
        self._chunks.append(text)

    def get_text(self) -> str:
        return " ".join("".join(c + " " for c in self._chunks).split()).strip()


def _classify_tier(char_count: int) -> str:
    if char_count >= _TIER_FULLTEXT_MIN_CHARS:
        return "full_text_html"
    if char_count >= _TIER_ABSTRACT_MIN_CHARS:
        return "extended_abstract"
    return "bibliographic_stub"


def _build_snapshot_html(
    *,
    title: str,
    doi: str,
    landing_url: str,
    body_html: str,
    extracted_text: str,
    tier: str,
) -> str:
    """Wrap the raw page HTML in a small banner so the snapshot is
    self-identifying when opened from Zotero's attachment list."""
    safe_title = re.sub(r"[<>&]", "", title or doi or "Captured page")
    return (
        "<!DOCTYPE html>\n<html><head>"
        "<meta charset='utf-8'>"
        f"<title>{safe_title}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:60em;margin:2em auto;padding:0 1em;color:#222;}"
        ".banner{background:#fff3cd;border:1px solid #ffeeba;padding:1em;margin-bottom:1em;border-radius:4px;}"
        ".banner small{color:#664d03;}"
        "</style>"
        "</head><body>"
        "<div class='banner'>"
        f"<strong>Captured landing page</strong> — {safe_title}<br>"
        f"<small>tier: <code>{tier}</code> · chars: {len(extracted_text):,} · "
        f"source: <a href='{landing_url}'>{landing_url}</a> · "
        f"doi: <code>{doi}</code></small>"
        "</div>"
        f"{body_html}"
        "</body></html>"
    )


def _doi_slug(doi: str) -> str:
    """Filesystem-safe slug derived from the DOI."""
    return re.sub(r"[^a-zA-Z0-9.]+", "_", (doi or "no-doi").lower())[:120]


async def capture_landing_html(
    *,
    doi: str,
    landing_url: str | None,
    abstract: str = "",
    title: str = "",
    http_client: httpx.AsyncClient,
    cache_dir: str | Path | None,
    timeout_s: float = 15.0,
) -> HtmlCapture | None:
    """Capture the publisher landing page as an HTML snapshot.

    Used as a Priority 3b fallback when the PDF route fails. The captured
    snapshot is written to ``<cache_dir>/html/<doi-slug>.html`` and
    classified into one of three tiers by extracted body length.

    Returns ``None`` when:
    - No landing URL is available (no DOI redirect possible, no
      caller-supplied URL).
    - The landing page returns non-2xx.
    - The fetched body is empty or non-HTML.

    Falls back gracefully on transient errors so it never breaks the
    parent push_to_zotero flow — surfaces ``None`` instead.
    """
    out_dir = (
        Path(cache_dir).expanduser() / "html"
        if cache_dir else Path.home() / ".cache" / "perspicacite" / "html"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    url = landing_url or (f"https://doi.org/{doi}" if doi else "")
    if not url:
        logger.debug("html_capture_no_url", doi=doi)
        return None

    try:
        r = await http_client.get(
            url, timeout=timeout_s, follow_redirects=True,
            headers={"User-Agent": "Perspicacite/0.1 (HTML-fallback capture)"},
        )
    except httpx.HTTPError as exc:
        logger.warning("html_capture_fetch_error", url=url, error=str(exc))
        return None
    if r.status_code >= 400:
        logger.info("html_capture_status_error", url=url, status=r.status_code)
        return None
    content_type = r.headers.get("content-type", "").lower()
    if "html" not in content_type:
        logger.debug("html_capture_not_html", url=url, content_type=content_type)
        return None

    body_html = r.text or ""
    if not body_html.strip():
        return None

    ext = _TextExtractor()
    try:
        ext.feed(body_html)
    except Exception as exc:
        logger.warning("html_capture_parse_error", url=url, error=str(exc))
        return None
    text = ext.get_text()
    if not text:
        return None

    tier = _classify_tier(len(text))

    # When the page is a paywalled stub but the caller supplied an
    # abstract from OpenAlex/Crossref discovery, splice it in so the
    # KB chunker has *something* to retrieve.
    augmented_body = body_html
    if tier == "bibliographic_stub" and abstract and abstract.strip() not in text:
        # Append the abstract block to the body so the file we write
        # contains it. Doesn't change the live page, just our snapshot.
        augmented_body = body_html + (
            "<section data-source='openalex-abstract'>"
            "<h2>Abstract (from OpenAlex/Crossref discovery)</h2>"
            f"<p>{abstract}</p></section>"
        )

    extracted_title = ext.title or title or doi or url
    snapshot = _build_snapshot_html(
        title=extracted_title,
        doi=doi,
        landing_url=url,
        body_html=augmented_body,
        extracted_text=text,
        tier=tier,
    )

    dest = out_dir / f"{_doi_slug(doi)}.html"
    dest.write_text(snapshot, encoding="utf-8")
    logger.info(
        "html_capture_saved",
        doi=doi, tier=tier, chars=len(text), path=str(dest),
    )
    return HtmlCapture(
        path=dest,
        tier=tier,
        char_count=len(text),
        extracted_title=extracted_title,
    )
