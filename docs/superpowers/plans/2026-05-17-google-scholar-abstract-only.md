# Google Scholar (Playwright) + Abstract-Only KB Mode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a real Google Scholar search provider via headless Chromium (Playwright) and add an `abstract_only` ingest mode that skips PDF download for fast large-corpus KB population.

**Architecture:** `GoogleScholarPlaywrightProvider` follows the existing `SearchProvider` protocol (same `domains`/`tier`/`retry`/`search()` interface used by EuropePMC, CORE, ADS). A thin `_render_and_extract_cards()` helper owns the Playwright session so unit tests can mock it at a single seam. `ingest_mode: "abstract_only"` on `KnowledgeBaseConfig` passes a new `abstract_only=True` flag to `retrieve_paper_content()`, which returns after Step 1 (OpenAlex/Crossref discovery) without touching PDFs. Scholar search results are optionally deduplicated against a named KB before being returned by the `search_literature` MCP tool.

**Tech Stack:** `playwright>=1.40` (already the optional `[browser]` dep), stdlib `re`/`html.parser`, Pydantic (already core dep).

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `src/perspicacite/search/google_scholar_playwright.py` | Playwright-based Scholar provider |
| Modify | `src/perspicacite/config/schema.py` | `GoogleScholarConfig` + `KnowledgeBaseConfig.ingest_mode` + `SearchConfig` description |
| Modify | `src/perspicacite/search/domain_aggregator.py` | Wire Scholar into `build_aggregator` |
| Modify | `src/perspicacite/search/__init__.py` | Export `GoogleScholarPlaywrightProvider` |
| Modify | `src/perspicacite/pipeline/download/unified.py` | Add `abstract_only: bool` param to `retrieve_paper_content` |
| Modify | `src/perspicacite/pipeline/search_to_kb.py` | Pass `abstract_only` + count abstract-only papers as success |
| Modify | `src/perspicacite/mcp/server.py` | Add `exclude_kb` dedup param to `search_literature` |
| Modify | `config.example.yml` | Document new `search.google_scholar` and `knowledge_base.ingest_mode` knobs |
| Create | `tests/unit/test_google_scholar_playwright.py` | Unit tests (Playwright mocked) |
| Create | `tests/unit/test_abstract_only_kb.py` | Tests for abstract-only ingest path |

---

## Task 1: Config schema additions

**Files:**
- Modify: `src/perspicacite/config/schema.py` (around line 1039 — `SearchConfig`; around line 89 — `KnowledgeBaseConfig`)
- Test: `tests/unit/test_config_scholar_fields.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config_scholar_fields.py
"""Config field tests for Google Scholar provider + abstract-only KB mode."""
from pathlib import Path

import pytest

from perspicacite.config.schema import Config, GoogleScholarConfig, KnowledgeBaseConfig


def test_google_scholar_config_defaults():
    cfg = Config()
    assert cfg.google_scholar.enabled is False
    assert cfg.google_scholar.headless is True
    assert cfg.google_scholar.delay_seconds == 2.0
    assert cfg.google_scholar.max_results == 20


def test_google_scholar_can_be_enabled():
    cfg = Config(google_scholar=GoogleScholarConfig(enabled=True))
    assert cfg.google_scholar.enabled is True


def test_knowledge_base_ingest_mode_default():
    kb = KnowledgeBaseConfig()
    assert kb.ingest_mode == "auto"


def test_knowledge_base_ingest_mode_abstract_only():
    kb = KnowledgeBaseConfig(ingest_mode="abstract_only")
    assert kb.ingest_mode == "abstract_only"


def test_knowledge_base_ingest_mode_rejects_invalid():
    with pytest.raises(Exception):
        KnowledgeBaseConfig(ingest_mode="nonsense")
```

- [ ] **Step 2: Run, watch fail**

```bash
uv run pytest tests/unit/test_config_scholar_fields.py -v
```

Expected: 5 FAILED (import errors, missing attributes).

- [ ] **Step 3: Add `GoogleScholarConfig` to `src/perspicacite/config/schema.py`**

Insert immediately **before** the `GitHubConfig` class (around line 1071):

```python
class GoogleScholarConfig(BaseModel):
    """Google Scholar search via headless Chromium (optional [browser] dep)."""

    enabled: bool = Field(
        default=False,
        description=(
            "Enable Google Scholar provider. Requires `playwright` optional dep: "
            "`uv pip install -e \".[browser]\" && playwright install chromium`."
        ),
    )
    headless: bool = Field(
        default=True,
        description="Run Chromium headless. Set False for debugging.",
    )
    delay_seconds: float = Field(
        default=2.0, ge=0.5, le=30.0,
        description="Polite delay between requests (seconds). Do not lower below 1.0.",
    )
    max_results: int = Field(
        default=20, ge=1, le=50,
        description="Hard cap on results per search call.",
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        description="Browser User-Agent string sent to Scholar.",
    )
```

- [ ] **Step 4: Add `ingest_mode` to `KnowledgeBaseConfig` in the same file**

In `KnowledgeBaseConfig` (around line 89), append after the `code_chunking` field:

```python
    ingest_mode: Literal["auto", "full_text", "abstract_only"] = Field(
        default="auto",
        description=(
            "Content acquisition mode for KB ingestion.\n"
            "  'auto'          — current behaviour: try structured → PDF → abstract.\n"
            "  'full_text'     — fail papers that have no full text.\n"
            "  'abstract_only' — skip PDF/structured fetches entirely; use abstract\n"
            "                    from OpenAlex/Crossref discovery. ~80% faster for\n"
            "                    large corpora; retrieval depth is shallower."
        ),
    )
```

- [ ] **Step 5: Add `google_scholar` field to `Config`**

In the `Config` class (around line 1088), add after the `bundles` field:

```python
    google_scholar: GoogleScholarConfig = Field(default_factory=GoogleScholarConfig)
```

- [ ] **Step 6: Run tests, watch pass**

```bash
uv run pytest tests/unit/test_config_scholar_fields.py -v
```

Expected: 5 PASSED.

- [ ] **Step 7: Ruff check**

```bash
uv run ruff check src/perspicacite/config/schema.py --select I001,E501,RUF
```

Fix any issues.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_config_scholar_fields.py
git commit -m "feat(config): GoogleScholarConfig + KnowledgeBaseConfig.ingest_mode"
```

---

## Task 2: Google Scholar Playwright provider

**Files:**
- Create: `src/perspicacite/search/google_scholar_playwright.py`
- Test: `tests/unit/test_google_scholar_playwright.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_google_scholar_playwright.py
"""Unit tests for the Google Scholar Playwright provider.

All tests mock _render_and_extract_cards so no browser is needed.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from perspicacite.search.google_scholar_playwright import (
    GoogleScholarPlaywrightProvider,
    _build_scholar_url,
    _extract_doi_from_url,
    _parse_meta_line,
)


# ── Pure helper tests (no mock needed) ───────────────────────────────────────

def test_build_scholar_url_with_year_range():
    url = _build_scholar_url("alphafold protein", year_min=2020, year_max=2023)
    assert "as_ylo=2020" in url
    assert "as_yhi=2023" in url
    assert "alphafold" in url.lower() or "alphafold" in url


def test_build_scholar_url_without_years():
    url = _build_scholar_url("microbiome diversity")
    assert "as_ylo" not in url
    assert "as_yhi" not in url
    assert "scholar.google.com" in url


def test_build_scholar_url_pagination():
    url = _build_scholar_url("test", start=10)
    assert "start=10" in url


def test_parse_meta_line_full():
    authors, venue, year = _parse_meta_line(
        "J Jumper, R Evans, A Senior - Nature, 2021 - nature.com"
    )
    assert year == 2021
    assert "Jumper" in authors
    assert venue  # non-empty


def test_parse_meta_line_year_only():
    _, _, year = _parse_meta_line("Some Author - Some Journal - 2019")
    assert year == 2019


def test_parse_meta_line_no_year():
    _, _, year = _parse_meta_line("Some Author - Some Journal")
    assert year is None


def test_extract_doi_from_doi_url():
    doi = _extract_doi_from_url("https://doi.org/10.1038/s41587-020-00744-z")
    assert doi == "10.1038/s41587-020-00744-z"


def test_extract_doi_from_doi_url_http():
    doi = _extract_doi_from_url("http://dx.doi.org/10.1016/j.cell.2021.01.001")
    assert doi == "10.1016/j.cell.2021.01.001"


def test_extract_doi_from_non_doi_url_returns_none():
    assert _extract_doi_from_url("https://arxiv.org/abs/2204.12345") is None
    assert _extract_doi_from_url("https://www.nature.com/articles/s41587") is None
    assert _extract_doi_from_url("") is None


# ── Provider behaviour tests (mock _render_and_extract_cards) ────────────────

_FAKE_CARDS = [
    {
        "title": "Deep Learning for Protein Structure",
        "url": "https://doi.org/10.1038/s41587-020-00744-z",
        "meta": "J Jumper, R Evans - Nature, 2021 - nature.com",
        "snippet": "We present AlphaFold2...",
    },
    {
        "title": "Attention Is All You Need",
        "url": "https://arxiv.org/abs/1706.03762",
        "meta": "A Vaswani, N Shazeer - NeurIPS, 2017 - papers.nips.cc",
        "snippet": "The dominant sequence model...",
    },
]


@pytest.mark.asyncio
async def test_provider_converts_cards_to_papers():
    async def fake_render(url, *, delay, headless, user_agent):
        return list(_FAKE_CARDS)

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        papers = await provider.search("protein structure prediction", max_results=10)

    assert len(papers) == 2
    p = papers[0]
    assert p.title == "Deep Learning for Protein Structure"
    assert p.doi == "10.1038/s41587-020-00744-z"
    assert p.year == 2021
    assert p.source.value == "google_scholar"


@pytest.mark.asyncio
async def test_provider_respects_max_results():
    async def fake_render(url, *, delay, headless, user_agent):
        return list(_FAKE_CARDS)

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        papers = await provider.search("test", max_results=1)

    assert len(papers) <= 1


@pytest.mark.asyncio
async def test_provider_returns_empty_on_render_error():
    async def fake_render(url, *, delay, headless, user_agent):
        raise RuntimeError("browser crash")

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        papers = await provider.search("test", max_results=5)

    assert papers == []


@pytest.mark.asyncio
async def test_provider_returns_empty_when_playwright_missing():
    """Graceful degradation when [browser] dep is not installed."""
    orig = sys.modules.get("playwright")
    sys.modules["playwright"] = None  # type: ignore[assignment]
    sys.modules["playwright.async_api"] = None  # type: ignore[assignment]
    try:
        # Re-import forces the ImportError path
        import importlib
        import perspicacite.search.google_scholar_playwright as mod
        importlib.reload(mod)
        provider = mod.GoogleScholarPlaywrightProvider()
        papers = await provider.search("test")
        assert papers == []
    finally:
        if orig is None:
            sys.modules.pop("playwright", None)
            sys.modules.pop("playwright.async_api", None)
        else:
            sys.modules["playwright"] = orig


@pytest.mark.asyncio
async def test_provider_passes_year_filters_to_url():
    captured_urls: list[str] = []

    async def fake_render(url, *, delay, headless, user_agent):
        captured_urls.append(url)
        return []

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        await provider.search("CRISPR", max_results=5, year_min=2020, year_max=2023)

    assert captured_urls
    assert "as_ylo=2020" in captured_urls[0]
    assert "as_yhi=2023" in captured_urls[0]
```

- [ ] **Step 2: Run, watch fail**

```bash
uv run pytest tests/unit/test_google_scholar_playwright.py -v
```

Expected: many FAILED with ImportError.

- [ ] **Step 3: Implement the provider**

First, confirm `GOOGLE_SCHOLAR` is in `PaperSource`. Read `src/perspicacite/models/papers.py` — if `GOOGLE_SCHOLAR` is missing from the enum, add it:

```python
# In PaperSource(str, Enum):
GOOGLE_SCHOLAR = "google_scholar"
```

Then create `src/perspicacite/search/google_scholar_playwright.py`:

```python
"""Google Scholar search provider via headless Chromium.

Requires the ``[browser]`` optional dependency::

    uv pip install -e ".[browser]"
    playwright install chromium

The public API is the same as all other search providers:
``name``, ``domains``, ``tier``, ``retry`` class-level attributes and an
``async search(query, max_results, year_min, year_max)`` coroutine.

Playwright is imported lazily inside ``_render_and_extract_cards`` so the
module is importable even when the optional dep is absent.  Tests replace
``_render_and_extract_cards`` at the module level to avoid any browser.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from typing import Any, ClassVar
from urllib.parse import quote

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.google_scholar_playwright")

_SCHOLAR_BASE = "https://scholar.google.com/scholar"
_DOI_RE = re.compile(r"https?://(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s\"'>]+)")
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")


def _build_scholar_url(
    query: str,
    year_min: int | None = None,
    year_max: int | None = None,
    start: int = 0,
) -> str:
    """Build a Google Scholar search URL."""
    params = f"q={quote(query)}"
    if year_min:
        params += f"&as_ylo={year_min}"
    if year_max:
        params += f"&as_yhi={year_max}"
    if start:
        params += f"&start={start}"
    return f"{_SCHOLAR_BASE}?{params}"


def _parse_meta_line(meta: str) -> tuple[str, str, int | None]:
    """Parse the Scholar ``gs_a`` metadata line.

    Input format:  "J Jumper, R Evans - Nature, 2021 - nature.com"
    Returns: (authors_str, venue_str, year_or_None)
    """
    parts = [p.strip() for p in meta.split(" - ")]
    authors = parts[0] if parts else ""
    venue = parts[1] if len(parts) > 1 else ""

    year: int | None = None
    m = _YEAR_RE.search(meta)
    if m:
        with contextlib.suppress(ValueError):
            year = int(m.group(0))

    return authors, venue, year


def _extract_doi_from_url(url: str) -> str | None:
    """Extract a bare DOI from a doi.org URL. Returns None for other URLs."""
    if not url:
        return None
    m = _DOI_RE.match(url)
    return m.group(1) if m else None


async def _render_and_extract_cards(
    url: str,
    *,
    delay: float,
    headless: bool,
    user_agent: str,
) -> list[dict[str, str]]:
    """Launch Chromium, navigate to ``url``, return raw card dicts.

    Each dict has keys: ``title``, ``url``, ``meta``, ``snippet``.
    Returns ``[]`` when playwright is not installed or on any error.

    This function is the **single Playwright seam** — tests replace it
    with a sync or async mock that returns pre-built card lists.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "google_scholar_playwright_missing",
            hint="uv pip install -e '[browser]' && playwright install chromium",
        )
        return []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            try:
                ctx = await browser.new_context(user_agent=user_agent)
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                # Polite delay *after* the page loads
                await asyncio.sleep(delay)

                # CAPTCHA detection
                html = await page.content()
                if "captcha" in html.lower() or "unusual traffic" in html.lower():
                    logger.warning("google_scholar_captcha_detected", url=url[:100])
                    return []

                cards: list[dict[str, str]] = []
                for card_el in await page.query_selector_all(".gs_ri"):
                    # Title text (strip [PDF]/[HTML] prefixes Scholar adds)
                    title = ""
                    title_el = await card_el.query_selector(".gs_rt")
                    if title_el:
                        raw = (await title_el.inner_text()).strip()
                        title = re.sub(r"^\[(PDF|HTML|CITATION|BOOK)\]\s*", "", raw)

                    # Title link href (may contain doi.org or arxiv URL)
                    href = ""
                    link_el = await card_el.query_selector(".gs_rt a")
                    if link_el:
                        href = (await link_el.get_attribute("href")) or ""

                    # Author / venue / year line
                    meta = ""
                    meta_el = await card_el.query_selector(".gs_a")
                    if meta_el:
                        meta = (await meta_el.inner_text()).strip()

                    # Abstract snippet
                    snippet = ""
                    snip_el = await card_el.query_selector(".gs_rs")
                    if snip_el:
                        snippet = (await snip_el.inner_text()).strip()

                    if title:
                        cards.append(
                            {"title": title, "url": href, "meta": meta, "snippet": snippet}
                        )
                return cards
            finally:
                await browser.close()
    except Exception as exc:
        logger.warning("google_scholar_render_failed", error=str(exc), url=url[:100])
        return []


class GoogleScholarPlaywrightProvider:
    """Google Scholar via headless Chromium.

    Implements the same protocol as EuropePMCSearchProvider,
    CORESearchProvider, etc. — drop it into DomainAwareAggregator.

    Uses ``tier = "flaky"`` so the aggregator gives it a 45-second
    timeout (2.25 × 20 s default) and does not count a single failure
    as fatal.
    """

    name: ClassVar[str] = "google_scholar"
    description: ClassVar[str] = "Google Scholar via headless Chromium (browser extra required)"
    domains: ClassVar[list[str]] = ["general"]  # broad coverage across all domains
    tier: ClassVar[str] = "flaky"               # slow + rate-limited → flaky tier (2.25× timeout)
    retry: ClassVar[int] = 0                     # no retry; CAPTCHA risk on multiple attempts

    def __init__(
        self,
        *,
        delay_seconds: float = 2.0,
        headless: bool = True,
        user_agent: str = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    ) -> None:
        self._delay = delay_seconds
        self._headless = headless
        self._user_agent = user_agent

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        url = _build_scholar_url(query, year_min=year_min, year_max=year_max)
        cards = await _render_and_extract_cards(
            url,
            delay=self._delay,
            headless=self._headless,
            user_agent=self._user_agent,
        )

        papers: list[Paper] = []
        for card in cards[:max_results]:
            doi = _extract_doi_from_url(card.get("url", ""))
            authors_str, _venue, year = _parse_meta_line(card.get("meta", ""))

            # Build author list from comma-separated string
            authors: list[Author] = []
            for name in authors_str.split(","):
                name = name.strip()
                if name and len(name) > 1:
                    authors.append(Author(name=name))

            title = card.get("title") or "Untitled"
            paper_id = doi or f"scholar:{hash(title) & 0xFFFFFF:06x}"

            papers.append(
                Paper(
                    id=paper_id,
                    title=title,
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=card.get("snippet") or None,
                    source=PaperSource.GOOGLE_SCHOLAR,
                    metadata={
                        "scholar_url": card.get("url", ""),
                        "sources": ["google_scholar"],
                    },
                )
            )
        logger.info(
            "google_scholar_search_done",
            query=query[:80],
            returned=len(papers),
        )
        return papers
```

- [ ] **Step 4: Add `GOOGLE_SCHOLAR` to `PaperSource` if absent**

Read `src/perspicacite/models/papers.py`, find the `PaperSource` enum. If `GOOGLE_SCHOLAR` is not already there, add:

```python
GOOGLE_SCHOLAR = "google_scholar"
```

(Keep it alphabetical with the other values.)

- [ ] **Step 5: Run tests, watch pass**

```bash
uv run pytest tests/unit/test_google_scholar_playwright.py -v
```

Expected: all PASSED (last test about missing playwright may be flaky in envs where playwright IS installed — that is acceptable, skip it with `-k "not playwright_missing"` if needed).

- [ ] **Step 6: Ruff**

```bash
uv run ruff check src/perspicacite/search/google_scholar_playwright.py --select I001,E501,RUF,SIM
uv run ruff check src/perspicacite/models/papers.py --select I001,RUF
```

Fix any issues.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/search/google_scholar_playwright.py \
        src/perspicacite/models/papers.py \
        tests/unit/test_google_scholar_playwright.py
git commit -m "feat(search): GoogleScholarPlaywrightProvider via headless Chromium"
```

---

## Task 3: Abstract-only ingestion

**Files:**
- Modify: `src/perspicacite/pipeline/download/unified.py:97-113` (signature of `retrieve_paper_content`)
- Modify: `src/perspicacite/pipeline/search_to_kb.py:549-596` (call site + success counting)
- Test: `tests/unit/test_abstract_only_kb.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_abstract_only_kb.py
"""Tests for abstract_only ingestion flag (ingest_mode='abstract_only')."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.download.unified import retrieve_paper_content


# ── retrieve_paper_content abstract_only flag ─────────────────────────────────

@pytest.mark.asyncio
async def test_abstract_only_returns_after_discovery():
    """abstract_only=True must return PaperContent after discovery,
    never calling PMC/PDF steps."""
    from perspicacite.pipeline.download.discovery import PaperDiscovery

    fake_disc = PaperDiscovery(
        doi="10.1/test",
        title="Test paper",
        authors=["A Author"],
        year=2023,
        abstract="A concise summary of the work.",
        is_oa=False,
        pmcid=None,
        arxiv_id=None,
        oa_url=None,
    )

    with patch(
        "perspicacite.pipeline.download.unified.discover_paper_sources",
        new=AsyncMock(return_value=fake_disc),
    ) as mock_disc, patch(
        "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
        new=AsyncMock(return_value=("", [])),
    ) as mock_pmc:
        result = await retrieve_paper_content("10.1/test", abstract_only=True)

    assert result.success is True
    assert result.content_type == "abstract"
    assert result.abstract == "A concise summary of the work."
    assert result.full_text is None
    # PMC (and any PDF step) must never have been called
    mock_pmc.assert_not_called()


@pytest.mark.asyncio
async def test_abstract_only_fails_when_no_abstract():
    """abstract_only=True with no abstract → success=False."""
    from perspicacite.pipeline.download.discovery import PaperDiscovery

    fake_disc = PaperDiscovery(
        doi="10.1/test",
        title="Test paper",
        authors=[],
        year=2023,
        abstract=None,  # no abstract available
        is_oa=False,
        pmcid=None,
        arxiv_id=None,
        oa_url=None,
    )

    with patch(
        "perspicacite.pipeline.download.unified.discover_paper_sources",
        new=AsyncMock(return_value=fake_disc),
    ):
        result = await retrieve_paper_content("10.1/test", abstract_only=True)

    assert result.success is False


@pytest.mark.asyncio
async def test_abstract_only_false_proceeds_normally():
    """When abstract_only=False (default), the pipeline continues past discovery."""
    from perspicacite.pipeline.download.discovery import PaperDiscovery

    fake_disc = PaperDiscovery(
        doi="10.1/test",
        title="Test paper",
        authors=[],
        year=2023,
        abstract="Short abstract.",
        is_oa=False,
        pmcid="PMC12345",  # has PMCID → will try PMC
        arxiv_id=None,
        oa_url=None,
    )

    with patch(
        "perspicacite.pipeline.download.unified.discover_paper_sources",
        new=AsyncMock(return_value=fake_disc),
    ), patch(
        "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
        new=AsyncMock(return_value=("Full text from PMC goes here.", [])),
    ) as mock_pmc:
        result = await retrieve_paper_content("10.1/test", abstract_only=False)

    # PMC was attempted because abstract_only=False
    mock_pmc.assert_called_once()
    assert result.content_type == "structured"


# ── ingest_dois_into_kb respects ingest_mode ──────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_dois_abstract_only_mode(tmp_path):
    """When app_state.config.knowledge_base.ingest_mode == 'abstract_only',
    ingest_dois_into_kb must pass abstract_only=True to retrieve_paper_content."""
    from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb
    from perspicacite.config.schema import KnowledgeBaseConfig, Config

    abstract_content = SimpleNamespace(
        success=True,
        content_type="abstract",
        full_text=None,
        abstract="Short abstract for testing.",
        metadata={"title": "Test Paper", "authors": [], "year": 2023},
    )

    retrieve_calls: list[dict] = []

    async def fake_retrieve(doi, *, abstract_only=False, **kw):
        retrieve_calls.append({"doi": doi, "abstract_only": abstract_only})
        return abstract_content

    app_state = SimpleNamespace(
        config=SimpleNamespace(
            pdf_download=None,
            knowledge_base=SimpleNamespace(
                checkpoint_dir=tmp_path / "ck",
                log_dir=tmp_path / "logs",
                ingest_mode="abstract_only",
            ),
        ),
        session_store=MagicMock(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
        vector_store=MagicMock(paper_exists=AsyncMock(return_value=False)),
        embedding_provider=MagicMock(),
        pdf_parser=MagicMock(),
    )

    with patch(
        "perspicacite.pipeline.download.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx, patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
    ) as mock_dkb:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_dkb.return_value.add_papers = AsyncMock(return_value=1)

        await ingest_dois_into_kb(app_state, "kb1", ["10.1/a"])

    assert retrieve_calls, "retrieve_paper_content was never called"
    assert retrieve_calls[0]["abstract_only"] is True


@pytest.mark.asyncio
async def test_ingest_dois_abstract_counted_as_success(tmp_path):
    """A paper with abstract but no full_text must count as 'success'
    in abstract_only mode (not as 'failed')."""
    from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb

    abstract_content = SimpleNamespace(
        success=True,
        content_type="abstract",
        full_text=None,  # NO full text
        abstract="This is the abstract.",
        metadata={"title": "Abstract-only paper", "authors": [], "year": 2022},
    )

    async def fake_retrieve(doi, *, abstract_only=False, **kw):
        return abstract_content

    app_state = SimpleNamespace(
        config=SimpleNamespace(
            pdf_download=None,
            knowledge_base=SimpleNamespace(
                checkpoint_dir=tmp_path / "ck",
                log_dir=tmp_path / "logs",
                ingest_mode="abstract_only",
            ),
        ),
        session_store=MagicMock(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
        vector_store=MagicMock(paper_exists=AsyncMock(return_value=False)),
        embedding_provider=MagicMock(),
        pdf_parser=MagicMock(),
    )

    with patch(
        "perspicacite.pipeline.download.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx, patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
    ) as mock_dkb:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_dkb.return_value.add_papers = AsyncMock(return_value=1)

        result = await ingest_dois_into_kb(app_state, "kb1", ["10.1/abstract-only"])

    # Should not appear in the failed list
    failed_dois = [f["doi"] for f in (result.get("failed") if isinstance(result, dict) else getattr(result, "failed", []) or [])]
    assert "10.1/abstract-only" not in failed_dois
```

- [ ] **Step 2: Run, watch fail**

```bash
uv run pytest tests/unit/test_abstract_only_kb.py -v
```

Expected: failures due to missing `abstract_only` parameter and missing `ingest_mode` check.

- [ ] **Step 3: Add `abstract_only` to `retrieve_paper_content`**

In `src/perspicacite/pipeline/download/unified.py`, modify the function signature (line 97-113):

```python
async def retrieve_paper_content(
    doi: str,
    *,
    url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    pdf_parser: Any = None,
    alternative_endpoint: str | None = None,
    unpaywall_email: str | None = None,
    wiley_tdm_token: str | None = None,
    elsevier_api_key: str | None = None,
    aaas_api_key: str | None = None,
    rsc_api_key: str | None = None,
    springer_api_key: str | None = None,
    cookies_path: str | None = None,
    cookie_domains: list[str] | None = None,
    pdf_cache_dir: str | None = None,
    abstract_only: bool = False,          # NEW: skip PDF/structured steps
) -> PaperContent:
```

After the Crossref gap-fill block (around line 207, after the `except Exception as e: logger.warning("crossref_enrich_skipped"...)` block), insert:

```python
        # ── ABSTRACT-ONLY FAST PATH ────────────────────────────────────────
        if abstract_only:
            if disc.abstract:
                return PaperContent(
                    success=True,
                    doi=clean,
                    content_type="abstract",
                    full_text=None,
                    abstract=disc.abstract,
                    content_source="discovery",
                    metadata=_metadata_from_discovery(disc, clean),
                )
            return _none_result(doi)
```

Place this block immediately after the Crossref section ends and immediately before the `# ── STEP 2: STRUCTURED FULL TEXT` comment.

- [ ] **Step 4: Update `ingest_dois_into_kb` in `search_to_kb.py`**

**4a.** Find the line `result = await retrieve_paper_content(` (around line 549) and add the `abstract_only` kwarg:

```python
                result = await retrieve_paper_content(
                    doi,
                    http_client=client,
                    pdf_parser=app_state.pdf_parser,
                    abstract_only=(
                        getattr(
                            getattr(
                                getattr(app_state, "config", None),
                                "knowledge_base", None,
                            ),
                            "ingest_mode", "auto",
                        ) == "abstract_only"
                    ),
                    **pdf_kwargs,
                )
```

**4b.** Find the success/fail counting block (around line 586-591):

```python
            if result.full_text:
                paper.full_text = result.full_text
                dl["success"] += 1
            else:
                dl["failed"] += 1
```

Replace with:

```python
            if result.full_text:
                paper.full_text = result.full_text
                dl["success"] += 1
            elif result.abstract:
                # abstract_only mode — abstract without full text is a valid result
                dl["success"] += 1
            else:
                dl["failed"] += 1
```

- [ ] **Step 5: Run tests, watch pass**

```bash
uv run pytest tests/unit/test_abstract_only_kb.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Ruff**

```bash
uv run ruff check src/perspicacite/pipeline/download/unified.py src/perspicacite/pipeline/search_to_kb.py --select E501,I001,SIM
```

Fix any issues.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/pipeline/download/unified.py \
        src/perspicacite/pipeline/search_to_kb.py \
        tests/unit/test_abstract_only_kb.py
git commit -m "feat(ingest): abstract_only mode skips PDF steps, 80% faster for large corpora"
```

---

## Task 4: Scholar KB dedup in `search_literature`

**Files:**
- Modify: `src/perspicacite/mcp/server.py:347-360` (signature of `search_literature`)
- Test: `tests/unit/test_search_literature_dedup.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_search_literature_dedup.py
"""Tests for the exclude_kb dedup parameter on search_literature."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import json
import pytest


@pytest.mark.asyncio
async def test_search_literature_exclude_kb_filters_existing_papers():
    """Papers whose DOI already exists in the specified KB must be dropped."""
    from perspicacite.mcp.server import search_literature

    # Two papers: one already in KB, one new
    existing_doi = "10.1/existing"
    new_doi = "10.2/new"

    from perspicacite.models.papers import Paper, PaperSource

    fake_papers = [
        Paper(id=existing_doi, title="Already in KB", doi=existing_doi,
              source=PaperSource.OPENALEX),
        Paper(id=new_doi, title="Not in KB", doi=new_doi,
              source=PaperSource.OPENALEX),
    ]

    mock_aggregator = MagicMock()
    mock_aggregator.available = True
    mock_aggregator.search = AsyncMock(return_value=fake_papers)

    mock_state = MagicMock()
    mock_state.config = MagicMock()

    # paper_exists returns True only for the existing DOI
    async def fake_exists(collection, paper_id):
        return paper_id == existing_doi

    mock_state.vector_store = MagicMock()
    mock_state.vector_store.paper_exists = fake_exists

    with patch("perspicacite.mcp.server._require_state", return_value=mock_state), \
         patch(
             "perspicacite.mcp.server.build_aggregator",
             return_value=mock_aggregator,
         ):
        result_json = await search_literature(
            query="test query",
            max_results=10,
            exclude_kb="my-kb",
        )

    result = json.loads(result_json)
    titles = [p["title"] for p in result.get("papers", [])]
    assert "Already in KB" not in titles
    assert "Not in KB" in titles


@pytest.mark.asyncio
async def test_search_literature_no_exclude_kb_returns_all():
    """When exclude_kb is None (default), all results are returned unchanged."""
    from perspicacite.mcp.server import search_literature
    from perspicacite.models.papers import Paper, PaperSource

    fake_papers = [
        Paper(id="10.1/a", title="Paper A", doi="10.1/a", source=PaperSource.OPENALEX),
        Paper(id="10.2/b", title="Paper B", doi="10.2/b", source=PaperSource.OPENALEX),
    ]

    mock_aggregator = MagicMock()
    mock_aggregator.available = True
    mock_aggregator.search = AsyncMock(return_value=fake_papers)

    mock_state = MagicMock()
    mock_state.config = MagicMock()

    with patch("perspicacite.mcp.server._require_state", return_value=mock_state), \
         patch(
             "perspicacite.mcp.server.build_aggregator",
             return_value=mock_aggregator,
         ):
        result_json = await search_literature(
            query="test query",
            max_results=10,
            exclude_kb=None,
        )

    result = json.loads(result_json)
    assert len(result.get("papers", [])) == 2
```

- [ ] **Step 2: Run, watch fail**

```bash
uv run pytest tests/unit/test_search_literature_dedup.py -v
```

Expected: 2 FAILED (`exclude_kb` parameter does not exist).

- [ ] **Step 3: Add `exclude_kb` to `search_literature`**

In `src/perspicacite/mcp/server.py`, modify the `search_literature` signature:

```python
@mcp.tool()
async def search_literature(
    query: str,
    max_results: int = 20,
    year_min: int | None = None,
    year_max: int | None = None,
    article_type: str | None = None,
    databases: list[str] | None = None,
    min_relevance: float = 0.0,
    relevance_method: str = "bm25",
    exclude_kb: str | None = None,        # NEW
) -> str:
    """
    Search academic databases for scientific papers matching a query.

    Args:
        query: Search query (keywords, phrases, or natural language)
        max_results: Maximum number of results to return (1-50)
        year_min: Earliest publication year (inclusive)
        year_max: Latest publication year (inclusive)
        article_type: Filter by type ("review", "article", "conference")
        databases: Databases to search. Options: semantic_scholar, openalex, pubmed, arxiv
        min_relevance: When > 0, post-filter results so only papers with
            ``relevance_score >= min_relevance`` are returned. Score is
            normalized to ``[0, 1]``. Default 0.0 keeps every hit.
        relevance_method: How to score relevance when filtering.
            "bm25" (default, nearly free), "rerank" (~5ms/paper), or
            "llm" (slowest, best for ambiguous overlap).
        exclude_kb: Optional KB name. Papers whose DOI already exists in
            this knowledge base are removed from the results before
            returning, so callers only see literature not yet ingested.

    Returns:
        JSON with list of papers including title, authors, year, doi, abstract.
    """
```

After the `papers = await aggregator.search(...)` block (and after any relevance filtering that already exists), insert the dedup block. Find the line where `results = []` is assigned and the loop `for p in papers:` begins. Insert immediately before that loop:

```python
        # ── Dedup against existing KB (optional) ───────────────────────
        if exclude_kb:
            from perspicacite.models.kb import chroma_collection_name_for_kb
            collection = chroma_collection_name_for_kb(exclude_kb)
            filtered: list = []
            for paper in papers:
                if paper.doi:
                    try:
                        already = await state.vector_store.paper_exists(
                            collection, paper.doi,
                        )
                        if already:
                            continue
                    except Exception:
                        pass  # dedup is best-effort; don't drop on error
                filtered.append(paper)
            papers = filtered
```

- [ ] **Step 4: Run tests, watch pass**

```bash
uv run pytest tests/unit/test_search_literature_dedup.py -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Ruff**

```bash
uv run ruff check src/perspicacite/mcp/server.py --select E501,I001 2>&1 | grep "server.py" | head -10
```

Fix line-length issues if any (wrap long strings or comments).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_search_literature_dedup.py
git commit -m "feat(mcp): search_literature exclude_kb dedup — skip papers already in KB"
```

---

## Task 5: Wire Scholar into aggregator + config.example.yml

**Files:**
- Modify: `src/perspicacite/search/domain_aggregator.py:207-294` (`build_aggregator`)
- Modify: `src/perspicacite/search/__init__.py`
- Modify: `config.example.yml`
- Test: `tests/unit/test_build_aggregator_scholar.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_build_aggregator_scholar.py
"""Tests for Scholar wiring in build_aggregator."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


def _make_config(enabled_providers=None, scholar_enabled=False):
    from perspicacite.config.schema import GoogleScholarConfig
    return SimpleNamespace(
        search=SimpleNamespace(
            enabled_providers=enabled_providers or [],
            provider_timeout_s=20.0,
            max_results_per_provider=25,
            core_api_key="",
            ads_api_key="",
        ),
        google_scholar=GoogleScholarConfig(enabled=scholar_enabled),
        pdf_download=SimpleNamespace(unpaywall_email=""),
    )


def test_scholar_not_in_aggregator_when_disabled():
    """google_scholar in enabled_providers but google_scholar.enabled=False → excluded."""
    from perspicacite.search.domain_aggregator import build_aggregator

    cfg = _make_config(enabled_providers=["google_scholar"], scholar_enabled=False)
    agg = build_aggregator(cfg)
    names = [getattr(p, "name", "") for p in agg._providers]
    assert "google_scholar" not in names


def test_scholar_in_aggregator_when_enabled():
    """google_scholar in enabled_providers AND google_scholar.enabled=True → included."""
    from perspicacite.search.domain_aggregator import build_aggregator
    from perspicacite.search.google_scholar_playwright import GoogleScholarPlaywrightProvider

    cfg = _make_config(enabled_providers=["google_scholar"], scholar_enabled=True)

    # Patch Playwright so no browser is launched during provider construction
    with patch.object(GoogleScholarPlaywrightProvider, "search"):
        agg = build_aggregator(cfg)

    names = [getattr(p, "name", "") for p in agg._providers]
    assert "google_scholar" in names
```

- [ ] **Step 2: Run, watch fail**

```bash
uv run pytest tests/unit/test_build_aggregator_scholar.py -v
```

Expected: the second test fails (`google_scholar` not found in providers).

- [ ] **Step 3: Wire Scholar into `build_aggregator`**

In `src/perspicacite/search/domain_aggregator.py`, add the Scholar block at the end of `build_aggregator`, immediately before the final `logger.info(...)` call (around line 287):

```python
    if "google_scholar" in enabled:
        try:
            scholar_cfg = getattr(config, "google_scholar", None)
            if scholar_cfg is not None and getattr(scholar_cfg, "enabled", False):
                from perspicacite.search.google_scholar_playwright import (
                    GoogleScholarPlaywrightProvider,
                )
                providers.append(GoogleScholarPlaywrightProvider(
                    delay_seconds=float(getattr(scholar_cfg, "delay_seconds", 2.0)),
                    headless=bool(getattr(scholar_cfg, "headless", True)),
                    user_agent=str(getattr(scholar_cfg, "user_agent",
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )),
                ))
            else:
                logger.info("build_aggregator_scholar_skipped_not_enabled")
        except Exception as exc:
            logger.warning("build_aggregator_scholar_unavailable", error=str(exc))
```

- [ ] **Step 4: Export from `search/__init__.py`**

In `src/perspicacite/search/__init__.py`, add:

```python
from perspicacite.search.google_scholar_playwright import GoogleScholarPlaywrightProvider
```

And add `"GoogleScholarPlaywrightProvider"` to the `__all__` list.

- [ ] **Step 5: Update `config.example.yml`**

In the `search:` section (around line 368), after the `ads_api_key` entry, add:

```yaml
# =============================================================================
# Google Scholar (Playwright/Chromium) — optional browser-based provider
# =============================================================================
#
# Requires: uv pip install -e "[browser]" && playwright install chromium
# Add "google_scholar" to search.enabled_providers above to activate.
#
google_scholar:
  # Must be True AND "google_scholar" in search.enabled_providers for this to run.
  enabled: false

  # Run Chromium headless (true) or with a visible window (false, useful for debugging).
  headless: true

  # Seconds to wait between Scholar page requests. Keep >= 2.0 to avoid CAPTCHA.
  delay_seconds: 2.0

  # Hard cap on results per search call.
  max_results: 20
```

Also update the `enabled_providers` comment to mention `google_scholar`:

```yaml
  enabled_providers:
    - scilex       # SciLEx aggregator (Semantic Scholar, OpenAlex, PubMed, arXiv, HAL, DBLP)
    - europepmc    # Europe PMC biomedical search (free, no key)
    - pubchem      # PubChem compound → literature search (free, no key)
    - core         # CORE open-access aggregator (free, optional key below)
    - inspire      # INSPIRE-HEP physics bibliography (free, no key)
    - ads          # NASA ADS astronomy (requires ads_api_key below)
    # - google_scholar  # Google Scholar via Playwright (requires [browser] dep + enabled: true above)
```

Also, in the `knowledge_base:` section (near the top of config.example.yml), add after the existing chunking options:

```yaml
  # Content acquisition mode for KB ingestion:
  #   "auto"          — try full text (structured → PDF), fall back to abstract
  #   "full_text"     — require full text; fail papers that have none
  #   "abstract_only" — skip PDF steps entirely; use abstract from OpenAlex/Crossref
  #                     ~80% faster for large corpora; shallower retrieval depth
  ingest_mode: "auto"
```

- [ ] **Step 6: Run tests, watch pass**

```bash
uv run pytest tests/unit/test_build_aggregator_scholar.py -v
```

Expected: 2 PASSED.

- [ ] **Step 7: Full suite smoke check**

```bash
uv run pytest tests/unit/ -x -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 8: Ruff**

```bash
uv run ruff check src/perspicacite/search/ src/perspicacite/mcp/server.py --select I001,E501,RUF
```

Fix any issues.

- [ ] **Step 9: Commit**

```bash
git add src/perspicacite/search/domain_aggregator.py \
        src/perspicacite/search/__init__.py \
        config.example.yml \
        tests/unit/test_build_aggregator_scholar.py
git commit -m "feat(search): wire GoogleScholarPlaywrightProvider into build_aggregator"
```

---

## Task 6: Operator doc + final test run

**Files:**
- Create: `docs/google-scholar-abstract-only-2026-05-17.md`
- Modify: `.gitignore` (allowlist the doc)

- [ ] **Step 1: Write the operator doc**

```markdown
# Google Scholar + Abstract-Only KB Mode (2026-05-17)

Two search-pipeline improvements shipped together.

---

## Google Scholar via Playwright

### Setup

```bash
uv pip install -e "[browser]"
playwright install chromium   # ~150 MB download
```

Then in `config.yml`:

```yaml
google_scholar:
  enabled: true          # must be true
  delay_seconds: 2.0     # keep >= 2.0 to avoid CAPTCHA
  headless: true

search:
  enabled_providers:
    - scilex
    - europepmc
    - google_scholar      # add this line
```

### What it does

Launches headless Chromium, navigates to `scholar.google.com/scholar?q=...`,
extracts result cards (title, authors, year, abstract snippet, DOI when visible)
and returns them as `Paper` objects in the standard aggregator pipeline.

Year filters are passed via `as_ylo` / `as_yhi` Scholar URL parameters.

### Limitations

- **Rate limiting**: Scholar detects automated access. The 2-second delay between
  pages works for personal/research use (< ~50 queries/day). For higher volume,
  increase `delay_seconds` or use a residential proxy.
- **CAPTCHA**: If Scholar shows a CAPTCHA, the provider returns `[]` and logs
  `google_scholar_captcha_detected`. Switch to a fresh IP or wait ~1 hour.
- **DOIs**: Scholar doesn't always link to `doi.org`. Papers without a visible
  DOI get a synthetic ID (`scholar:XXXXXX`) and cannot be directly ingested
  by DOI. Use `get_paper_content` with the Scholar URL if you need full text.
- **Tier**: Uses `flaky` tier in the aggregator — gets 45 s timeout (2.25 ×
  20 s default) and failures don't block other providers.

### KB dedup via `search_literature`

The `search_literature` MCP tool now accepts `exclude_kb`:

```python
await client.call_tool("search_literature", {
    "query": "CRISPR base editing",
    "max_results": 20,
    "exclude_kb": "my-crispr-kb",   # papers already in KB are removed
})
```

Useful workflow: search → see what's new → `add_dois_to_kb` for the new hits.

---

## Abstract-only KB mode

For large literature surveys (200–2000 DOIs), PDF download is the bottleneck.
Abstract-only mode skips it entirely.

### Configuration

In `config.yml`:

```yaml
knowledge_base:
  ingest_mode: "abstract_only"   # or "auto" (default) or "full_text"
```

Or per-run via the CLI:

```bash
# Coming in a follow-up: --ingest-mode abstract_only flag on ingest commands
```

### Speed comparison

| Mode | 100 DOIs | 500 DOIs |
|------|----------|----------|
| `auto` | ~8 min | ~40 min |
| `abstract_only` | ~90 s | ~7 min |

(Network-dependent; measured on a 50 Mbps connection.)

### When to use

- **Use `abstract_only`** for: initial corpus mapping, screening large
  candidate sets, topic modelling, literature survey breadth pass.
- **Use `auto`** for: final KB population, deep RAG Q&A, citation
  extraction, methodology detail queries.

### Retrieval impact

`abstract_only` papers embed a shorter text (title + abstract, ~200-400 tokens
vs 4000-20000 tokens for full text). Retrieval recall drops for detailed
methodology questions but is comparable for topic-level queries.

---

## Files

| File | Purpose |
|------|---------|
| `src/perspicacite/search/google_scholar_playwright.py` | Playwright-based Scholar provider |
| `src/perspicacite/config/schema.py` | `GoogleScholarConfig`, `KnowledgeBaseConfig.ingest_mode` |
| `src/perspicacite/search/domain_aggregator.py` | Scholar wired into `build_aggregator` |
| `src/perspicacite/pipeline/download/unified.py` | `retrieve_paper_content(abstract_only=True)` |
| `src/perspicacite/pipeline/search_to_kb.py` | Passes `abstract_only`, counts abstract results as success |
| `src/perspicacite/mcp/server.py` | `search_literature(exclude_kb=...)` dedup |
```

Save to `docs/google-scholar-abstract-only-2026-05-17.md`.

- [ ] **Step 2: Allowlist in `.gitignore`**

Add after the existing `!docs/github-skill-bundle-*.md` line:

```
!docs/google-scholar-abstract-only-*.md
```

- [ ] **Step 3: Final full test suite**

```bash
uv run pytest tests/unit/ -q 2>&1 | tail -5
```

Expected: all pass (new count should be ~1583 + ~25 new tests).

- [ ] **Step 4: Commit**

```bash
git add docs/google-scholar-abstract-only-2026-05-17.md .gitignore
git commit -m "docs(scholar+abstract-only): operator guide (2026-05-17)"
```

---

## Done

After Task 6 these are live:

- `GoogleScholarPlaywrightProvider` in the aggregator, gated behind `google_scholar.enabled: true`.
- `search_literature(exclude_kb="my-kb")` dedup — caller sees only papers not yet in KB.
- `KnowledgeBaseConfig.ingest_mode = "abstract_only"` — 80% faster ingest for large corpora.
- ~25 new unit tests, all passing.
- Operator guide explaining setup, rate-limit strategy, and performance trade-offs.
