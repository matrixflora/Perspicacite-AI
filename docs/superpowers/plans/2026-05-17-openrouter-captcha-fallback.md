# OpenRouter CAPTCHA Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `GoogleScholarPlaywrightProvider` detects a Google Scholar CAPTCHA, fall back to OpenRouter's `web_search` server tool (Exa engine + academic domain allowlist) and return structured `Paper` objects instead of silently returning `[]`.

**Architecture:** A new `openrouter_fallback.py` helper handles all OpenRouter HTTP logic in isolation. `google_scholar_playwright.py` gains a module-level sentinel object to distinguish CAPTCHA from empty results, and calls the helper when the sentinel is detected. Config fields are added to `GoogleScholarConfig`. No aggregator callers change — the provider still returns `list[Paper]`.

**Tech Stack:** `httpx` (already a project dependency), OpenRouter Chat Completions API at `https://openrouter.ai/api/v1/chat/completions`, `openrouter:web_search` server tool, Exa engine, `deepseek/deepseek-v2-fast` (default model, configurable).

---

## File Map

| Action | Path |
|--------|------|
| Modify | `src/perspicacite/models/papers.py` — add `OPENROUTER_WEB` to `PaperSource` enum |
| Modify | `src/perspicacite/config/schema.py` — add 4 fields to `GoogleScholarConfig` |
| Modify | `config.example.yml` — document new keys under `google_scholar:` |
| Create | `src/perspicacite/search/openrouter_fallback.py` — HTTP + parsing helper |
| Modify | `src/perspicacite/search/google_scholar_playwright.py` — sentinel + fallback wiring |
| Modify | `src/perspicacite/search/domain_aggregator.py` — pass new config fields to provider |
| Create | `tests/unit/test_openrouter_fallback.py` — unit tests for helper |
| Modify | `tests/unit/test_google_scholar_playwright.py` — CAPTCHA-triggers-fallback tests |

---

## Task 1: Foundation — enum value + config fields + config.example.yml

**Files:**
- Modify: `src/perspicacite/models/papers.py` (line 38, after `DBLP_SPARQL`)
- Modify: `src/perspicacite/config/schema.py` (line 1111, after `user_agent` field in `GoogleScholarConfig`)
- Modify: `config.example.yml` (under the `google_scholar:` block)
- Test: `tests/unit/test_paper_source_new_values.py` (add one assertion)

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/unit/test_paper_source_new_values.py`:

```python
def test_openrouter_web_paper_source():
    from perspicacite.models.papers import PaperSource
    assert PaperSource.OPENROUTER_WEB.value == "openrouter_web"
    # Round-trip
    assert PaperSource("openrouter_web") is PaperSource.OPENROUTER_WEB
```

- [ ] **Step 2: Run to confirm it fails**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
uv run pytest tests/unit/test_paper_source_new_values.py::test_openrouter_web_paper_source -v
```

Expected: `FAILED` with `AttributeError: OPENROUTER_WEB`

- [ ] **Step 3: Add `OPENROUTER_WEB` to `PaperSource` in `src/perspicacite/models/papers.py`**

Find the line `DBLP_SPARQL = "dblp_sparql"` (currently line 38) and add immediately after it:

```python
    DBLP_SPARQL = "dblp_sparql"
    OPENROUTER_WEB = "openrouter_web"
```

- [ ] **Step 4: Run test — expect PASS**

```bash
uv run pytest tests/unit/test_paper_source_new_values.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Add 4 config fields to `GoogleScholarConfig` in `src/perspicacite/config/schema.py`**

Find `class GoogleScholarConfig(BaseModel):` (around line 1082). The last existing field ends with the `user_agent` field (around line 1111). Add these four fields immediately after `user_agent`:

```python
    openrouter_fallback_enabled: bool = Field(
        default=True,
        description=(
            "Call OpenRouter web_search when Scholar returns a CAPTCHA. "
            "Requires openrouter_api_key or OPENROUTER_API_KEY env var."
        ),
    )
    openrouter_api_key: str = Field(
        default="",
        description="OpenRouter API key. Also read from OPENROUTER_API_KEY env var.",
    )
    openrouter_fallback_model: str = Field(
        default="deepseek/deepseek-v2-fast",
        description=(
            "OpenRouter model for CAPTCHA fallback. Use 'deepseek/deepseek-v2-fast' "
            "for cheap Exa-backed search. For native search: 'anthropic/claude-haiku-4-5' "
            "or 'openai/gpt-4o-mini' (omit engine override in that case)."
        ),
    )
    openrouter_fallback_domains: list[str] = Field(
        default_factory=lambda: [
            "arxiv.org",
            "biorxiv.org",
            "chemrxiv.org",
            "pubmed.ncbi.nlm.nih.gov",
            "europepmc.org",
            "semanticscholar.org",
            "crossref.org",
            "nature.com",
            "sciencedirect.com",
            "springer.com",
            "wiley.com",
        ],
        description="Exa search restricted to these academic domains.",
    )
```

- [ ] **Step 6: Write a config schema test**

Add a new file `tests/unit/test_config_scholar_openrouter_fields.py`:

```python
"""Tests for GoogleScholarConfig OpenRouter fallback fields."""


def test_google_scholar_config_openrouter_defaults():
    from perspicacite.config.schema import GoogleScholarConfig

    cfg = GoogleScholarConfig()
    assert cfg.openrouter_fallback_enabled is True
    assert cfg.openrouter_api_key == ""
    assert cfg.openrouter_fallback_model == "deepseek/deepseek-v2-fast"
    assert "arxiv.org" in cfg.openrouter_fallback_domains
    assert "pubmed.ncbi.nlm.nih.gov" in cfg.openrouter_fallback_domains
    assert len(cfg.openrouter_fallback_domains) >= 8


def test_google_scholar_config_openrouter_fields_settable():
    from perspicacite.config.schema import GoogleScholarConfig

    cfg = GoogleScholarConfig(
        openrouter_fallback_enabled=False,
        openrouter_api_key="sk-test",
        openrouter_fallback_model="openai/gpt-4o-mini",
        openrouter_fallback_domains=["arxiv.org"],
    )
    assert cfg.openrouter_fallback_enabled is False
    assert cfg.openrouter_api_key == "sk-test"
    assert cfg.openrouter_fallback_model == "openai/gpt-4o-mini"
    assert cfg.openrouter_fallback_domains == ["arxiv.org"]
```

- [ ] **Step 7: Run config tests — expect PASS**

```bash
uv run pytest tests/unit/test_config_scholar_openrouter_fields.py -v
```

Expected: 2 passed

- [ ] **Step 8: Update `config.example.yml`**

Find the `google_scholar:` block (around line 410 of `config.example.yml`). It currently ends with `max_results: 20`. Add the OpenRouter block immediately after `max_results`:

```yaml
  # --- CAPTCHA fallback via OpenRouter web search ---
  # When Scholar serves a CAPTCHA, fall back to OpenRouter's openrouter:web_search
  # server tool (Exa engine + academic domain allowlist). Costs ~$0.005/trigger.
  # Set openrouter_api_key or OPENROUTER_API_KEY env var to activate.
  openrouter_fallback_enabled: true
  openrouter_api_key: ""            # or set OPENROUTER_API_KEY env var
  openrouter_fallback_model: "deepseek/deepseek-v2-fast"
  # For native-search models (Anthropic/OpenAI/xAI), use e.g. "anthropic/claude-haiku-4-5"
  openrouter_fallback_domains:
    - "arxiv.org"
    - "biorxiv.org"
    - "chemrxiv.org"
    - "pubmed.ncbi.nlm.nih.gov"
    - "europepmc.org"
    - "semanticscholar.org"
    - "crossref.org"
    - "nature.com"
    - "sciencedirect.com"
    - "springer.com"
    - "wiley.com"
```

- [ ] **Step 9: Run full unit suite to check no regressions**

```bash
uv run pytest tests/unit/ -x -q
```

Expected: all previously passing tests still pass

- [ ] **Step 10: Commit**

```bash
git add src/perspicacite/models/papers.py \
        src/perspicacite/config/schema.py \
        config.example.yml \
        tests/unit/test_paper_source_new_values.py \
        tests/unit/test_config_scholar_openrouter_fields.py
git commit -m "feat: add PaperSource.OPENROUTER_WEB and GoogleScholarConfig openrouter fields"
```

---

## Task 2: `openrouter_fallback.py` helper + full test suite

**Files:**
- Create: `src/perspicacite/search/openrouter_fallback.py`
- Create: `tests/unit/test_openrouter_fallback.py`

**Context:** This module is pure async HTTP + JSON parsing. It has no dependency on Playwright and no knowledge of `GoogleScholarPlaywrightProvider`. All tests mock `httpx.AsyncClient`. The module never raises — every error path returns `[]`.

- [ ] **Step 1: Write all failing tests first**

Create `tests/unit/test_openrouter_fallback.py`:

```python
"""Unit tests for openrouter_fallback helper — all HTTP mocked."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _resolve_api_key ──────────────────────────────────────────────────────────

def test_resolve_api_key_uses_config_value():
    from perspicacite.search.openrouter_fallback import _resolve_api_key
    assert _resolve_api_key("sk-config") == "sk-config"


def test_resolve_api_key_falls_back_to_env(monkeypatch):
    from perspicacite.search.openrouter_fallback import _resolve_api_key
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert _resolve_api_key("") == "sk-env"


def test_resolve_api_key_returns_empty_when_neither_set(monkeypatch):
    from perspicacite.search.openrouter_fallback import _resolve_api_key
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _resolve_api_key("") == ""


# ── _build_payload ─────────────────────────────────────────────────────────────

def test_build_payload_structure():
    from perspicacite.search.openrouter_fallback import _build_payload
    payload = _build_payload("CRISPR", "deepseek/deepseek-v2-fast", 5, ["arxiv.org"])
    assert payload["model"] == "deepseek/deepseek-v2-fast"
    assert payload["tool_choice"] == "required"
    assert payload["tools"][0]["type"] == "openrouter:web_search"
    assert payload["tools"][0]["parameters"]["engine"] == "exa"
    assert payload["tools"][0]["parameters"]["max_results"] == 5
    assert "arxiv.org" in payload["tools"][0]["parameters"]["allowed_domains"]
    assert "CRISPR" in payload["messages"][0]["content"]


def test_build_payload_clamps_max_results_to_25():
    from perspicacite.search.openrouter_fallback import _build_payload
    payload = _build_payload("test", "model", 99, [])
    assert payload["tools"][0]["parameters"]["max_results"] == 25


# ── _parse_response ────────────────────────────────────────────────────────────

def test_parse_response_extracts_json_array():
    from perspicacite.search.openrouter_fallback import _parse_response
    content = 'Here are papers: [{"title": "Test", "year": 2021}] done.'
    result = _parse_response(content)
    assert result == [{"title": "Test", "year": 2021}]


def test_parse_response_returns_empty_on_no_array():
    from perspicacite.search.openrouter_fallback import _parse_response
    assert _parse_response("No JSON here.") == []


def test_parse_response_handles_multiline_array():
    from perspicacite.search.openrouter_fallback import _parse_response
    content = '[\n  {"title": "A"},\n  {"title": "B"}\n]'
    result = _parse_response(content)
    assert len(result) == 2


# ── _build_paper ───────────────────────────────────────────────────────────────

def test_build_paper_full_entry():
    from perspicacite.search.openrouter_fallback import _build_paper
    from perspicacite.models.papers import PaperSource

    entry = {
        "title": "AlphaFold",
        "authors": ["Jumper J", "Evans R"],
        "year": 2021,
        "doi": "10.1038/s41586-021-03819-2",
        "abstract": "Protein structure prediction...",
        "url": "https://nature.com/articles/s41586-021-03819-2",
    }
    paper = _build_paper(entry)
    assert paper is not None
    assert paper.title == "AlphaFold"
    assert paper.doi == "10.1038/s41586-021-03819-2"
    assert paper.id == "10.1038/s41586-021-03819-2"
    assert paper.year == 2021
    assert paper.source == PaperSource.OPENROUTER_WEB
    assert len(paper.authors) == 2
    assert paper.abstract == "Protein structure prediction..."


def test_build_paper_uses_url_hash_when_no_doi():
    from perspicacite.search.openrouter_fallback import _build_paper
    entry = {"title": "No DOI Paper", "url": "https://arxiv.org/abs/1234.5678"}
    paper = _build_paper(entry)
    assert paper is not None
    assert paper.doi is None
    assert paper.id.startswith("openrouter:")


def test_build_paper_source_is_openrouter_web():
    from perspicacite.search.openrouter_fallback import _build_paper
    from perspicacite.models.papers import PaperSource
    paper = _build_paper({"title": "Test", "url": "https://arxiv.org/abs/1"})
    assert paper.source == PaperSource.OPENROUTER_WEB


def test_build_paper_handles_null_year():
    from perspicacite.search.openrouter_fallback import _build_paper
    paper = _build_paper({"title": "Test", "year": None, "url": "https://arxiv.org/1"})
    assert paper.year is None


# ── openrouter_academic_search (integration, mocked HTTP) ────────────────────

def _make_mock_client(response_content: str, status_code: int = 200):
    """Build a mock httpx.AsyncClient that returns the given JSON."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    if status_code != 200:
        mock_resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    mock_resp.json = MagicMock(return_value=json.loads(response_content))

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


_VALID_OR_RESPONSE = json.dumps({
    "choices": [{
        "message": {
            "content": json.dumps([
                {
                    "title": "Attention Is All You Need",
                    "authors": ["Vaswani A", "Shazeer N"],
                    "year": 2017,
                    "doi": "10.5555/3295222.3295349",
                    "abstract": "The dominant sequence transduction models...",
                    "url": "https://arxiv.org/abs/1706.03762",
                }
            ])
        }
    }]
})


@pytest.mark.asyncio
async def test_search_returns_papers_on_valid_response():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    mock_client = _make_mock_client(_VALID_OR_RESPONSE)
    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search(
            "attention transformer",
            api_key="sk-test",
            max_results=5,
        )

    assert len(papers) == 1
    assert papers[0].title == "Attention Is All You Need"
    assert papers[0].doi == "10.5555/3295222.3295349"
    assert papers[0].year == 2017


@pytest.mark.asyncio
async def test_search_returns_empty_on_http_error():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search("test", api_key="sk-test")

    assert papers == []


@pytest.mark.asyncio
async def test_search_returns_empty_when_no_api_key(monkeypatch):
    from perspicacite.search.openrouter_fallback import openrouter_academic_search
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    papers = await openrouter_academic_search("test", api_key="")
    assert papers == []


@pytest.mark.asyncio
async def test_search_returns_empty_on_malformed_json():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    bad_response = json.dumps({"choices": [{"message": {"content": "No JSON here."}}]})
    mock_client = _make_mock_client(bad_response)

    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search("test", api_key="sk-test")

    assert papers == []


@pytest.mark.asyncio
async def test_search_uses_env_var_key(monkeypatch):
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    mock_client = _make_mock_client(_VALID_OR_RESPONSE)

    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search("test", api_key="")

    # Env var was used — we got results (not rejected for missing key)
    assert len(papers) == 1
    # Verify Bearer token was sent
    call_kwargs = mock_client.post.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-from-env"


@pytest.mark.asyncio
async def test_search_passes_allowed_domains_to_payload():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    mock_client = _make_mock_client(_VALID_OR_RESPONSE)
    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        await openrouter_academic_search(
            "test",
            api_key="sk-test",
            allowed_domains=["arxiv.org", "biorxiv.org"],
        )

    sent_payload = mock_client.post.call_args[1]["json"]
    domains = sent_payload["tools"][0]["parameters"]["allowed_domains"]
    assert domains == ["arxiv.org", "biorxiv.org"]
```

- [ ] **Step 2: Run tests to confirm they all fail**

```bash
uv run pytest tests/unit/test_openrouter_fallback.py -v
```

Expected: `ImportError: No module named 'perspicacite.search.openrouter_fallback'`

- [ ] **Step 3: Create `src/perspicacite/search/openrouter_fallback.py`**

```python
"""OpenRouter web-search fallback for Google Scholar CAPTCHA events.

Called by GoogleScholarPlaywrightProvider when Scholar returns a CAPTCHA.
Uses OpenRouter's ``openrouter:web_search`` server tool (Exa engine) with an
academic domain allowlist to retrieve paper metadata via an LLM.

No external dependencies beyond httpx (already in project requirements).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.openrouter_fallback")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_DEFAULT_DOMAINS: list[str] = [
    "arxiv.org",
    "biorxiv.org",
    "chemrxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "europepmc.org",
    "semanticscholar.org",
    "crossref.org",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
]

_PROMPT_TEMPLATE = (
    "Search scientific literature for papers about: {query}\n"
    "Return ONLY a JSON array of up to {n} papers. Each element must be:\n"
    '  {{"title": str, "authors": [str], "year": int or null, '
    '"doi": str or null, "abstract": str, "url": str}}\n'
    "No prose, no markdown, no explanation. Just the raw JSON array."
)


def _resolve_api_key(config_key: str) -> str:
    """Return config key if non-empty, else OPENROUTER_API_KEY env var, else ''."""
    if config_key:
        return config_key
    return os.environ.get("OPENROUTER_API_KEY", "")


def _build_payload(
    query: str,
    model: str,
    max_results: int,
    allowed_domains: list[str],
) -> dict[str, Any]:
    return {
        "model": model,
        "tool_choice": "required",
        "tools": [
            {
                "type": "openrouter:web_search",
                "parameters": {
                    "engine": "exa",
                    "max_results": min(max_results, 25),
                    "allowed_domains": allowed_domains,
                },
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": _PROMPT_TEMPLATE.format(query=query, n=max_results),
            }
        ],
    }


def _parse_response(content: str) -> list[dict[str, Any]]:
    """Extract and parse the first JSON array from LLM response text."""
    m = re.search(r"\[.*?\]", content, re.DOTALL)
    if not m:
        return []
    return json.loads(m.group(0))


def _build_paper(entry: dict[str, Any]) -> Paper | None:
    """Convert a parsed JSON entry to a Paper. Returns None on unrecoverable errors."""
    try:
        title = str(entry.get("title") or "Untitled").strip()
        doi = entry.get("doi") or None
        url = str(entry.get("url") or "")
        year_raw = entry.get("year")
        year: int | None = int(year_raw) if year_raw is not None else None

        authors: list[Author] = []
        for name in entry.get("authors") or []:
            name_str = str(name).strip()
            if name_str:
                authors.append(Author(name=name_str))

        abstract = str(entry.get("abstract") or "").strip() or None
        paper_id = doi or f"openrouter:{abs(hash(url or title)) & 0xFFFFFF:06x}"

        return Paper(
            id=paper_id,
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            abstract=abstract,
            source=PaperSource.OPENROUTER_WEB,
            metadata={"sources": ["openrouter_web"], "scholar_url": url},
        )
    except Exception as exc:
        logger.warning("openrouter_fallback_build_paper_error", error=str(exc))
        return None


async def openrouter_academic_search(
    query: str,
    *,
    api_key: str,
    model: str = "deepseek/deepseek-v2-fast",
    max_results: int = 10,
    allowed_domains: list[str] | None = None,
    timeout: float = 20.0,
) -> list[Paper]:
    """Call OpenRouter web_search server tool and return Paper objects.

    Returns [] on any error (HTTP failure, bad API key, parse failure).
    Never raises.
    """
    key = _resolve_api_key(api_key)
    if not key:
        logger.warning("openrouter_fallback_no_key")
        return []

    domains = allowed_domains if allowed_domains is not None else _DEFAULT_DOMAINS
    payload = _build_payload(query, model, max_results, domains)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                _OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("openrouter_fallback_http_error", error=str(exc))
            return []

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        entries = _parse_response(content)
    except Exception as exc:
        logger.warning("openrouter_fallback_parse_error", error=str(exc))
        return []

    papers: list[Paper] = []
    for entry in entries:
        paper = _build_paper(entry)
        if paper:
            papers.append(paper)

    logger.info("openrouter_fallback_done", query=query[:80], n=len(papers))
    return papers
```

- [ ] **Step 4: Run tests — expect all PASS**

```bash
uv run pytest tests/unit/test_openrouter_fallback.py -v
```

Expected: all tests pass (17 tests)

- [ ] **Step 5: Run full unit suite to check no regressions**

```bash
uv run pytest tests/unit/ -x -q
```

Expected: all previously passing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/search/openrouter_fallback.py \
        tests/unit/test_openrouter_fallback.py
git commit -m "feat: add openrouter_academic_search helper for Scholar CAPTCHA fallback"
```

---

## Task 3: Wire sentinel + fallback into `google_scholar_playwright.py` and `domain_aggregator.py`

**Files:**
- Modify: `src/perspicacite/search/google_scholar_playwright.py`
- Modify: `src/perspicacite/search/domain_aggregator.py` (lines 294–303)
- Modify: `tests/unit/test_google_scholar_playwright.py`

**Context:** `_render_and_extract_cards` is the single Playwright seam — all tests mock it. The function currently returns `[]` on CAPTCHA. After this task it returns a module-level sentinel object. `search()` checks `cards is _CAPTCHA_SENTINEL` and calls the OpenRouter fallback if configured. The `domain_aggregator.py` change just passes the four new config fields when constructing the provider.

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_google_scholar_playwright.py`:

```python
from perspicacite.search.google_scholar_playwright import _CAPTCHA_SENTINEL


@pytest.mark.asyncio
async def test_captcha_sentinel_is_module_level_object():
    """Sentinel is a unique list identity, not a new [] each call."""
    from perspicacite.search.google_scholar_playwright import _CAPTCHA_SENTINEL as s1
    from perspicacite.search.google_scholar_playwright import _CAPTCHA_SENTINEL as s2
    assert s1 is s2


@pytest.mark.asyncio
async def test_captcha_triggers_openrouter_fallback():
    """When _render_and_extract_cards returns _CAPTCHA_SENTINEL, search() calls fallback."""
    from perspicacite.search.google_scholar_playwright import (
        GoogleScholarPlaywrightProvider,
        _CAPTCHA_SENTINEL,
    )
    from perspicacite.models.papers import Paper, PaperSource

    fallback_paper = Paper(
        id="10.1/test",
        title="Fallback Paper",
        doi="10.1/test",
        source=PaperSource.OPENROUTER_WEB,
    )

    async def fake_render(url, *, delay, headless, user_agent):
        return _CAPTCHA_SENTINEL

    async def fake_openrouter(query, *, api_key, model, max_results, allowed_domains):
        return [fallback_paper]

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        with patch(
            "perspicacite.search.openrouter_fallback.openrouter_academic_search",
            new=fake_openrouter,
        ):
            provider = GoogleScholarPlaywrightProvider(
                delay_seconds=0.0,
                openrouter_fallback_enabled=True,
                openrouter_api_key="sk-test",
            )
            papers = await provider.search("CRISPR", max_results=5)

    assert len(papers) == 1
    assert papers[0].title == "Fallback Paper"
    assert papers[0].source == PaperSource.OPENROUTER_WEB


@pytest.mark.asyncio
async def test_captcha_fallback_disabled_returns_empty():
    """When openrouter_fallback_enabled=False, CAPTCHA → [] without calling fallback."""
    from perspicacite.search.google_scholar_playwright import (
        GoogleScholarPlaywrightProvider,
        _CAPTCHA_SENTINEL,
    )

    fallback_called = []

    async def fake_render(url, *, delay, headless, user_agent):
        return _CAPTCHA_SENTINEL

    async def fake_openrouter(*a, **kw):
        fallback_called.append(True)
        return []

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        with patch(
            "perspicacite.search.openrouter_fallback.openrouter_academic_search",
            new=fake_openrouter,
        ):
            provider = GoogleScholarPlaywrightProvider(
                delay_seconds=0.0,
                openrouter_fallback_enabled=False,
            )
            papers = await provider.search("test", max_results=5)

    assert papers == []
    assert not fallback_called


@pytest.mark.asyncio
async def test_captcha_fallback_passes_correct_args():
    """search() passes query, api_key, model, max_results, domains to fallback."""
    from perspicacite.search.google_scholar_playwright import (
        GoogleScholarPlaywrightProvider,
        _CAPTCHA_SENTINEL,
    )

    captured = {}

    async def fake_render(url, *, delay, headless, user_agent):
        return _CAPTCHA_SENTINEL

    async def fake_openrouter(query, *, api_key, model, max_results, allowed_domains):
        captured.update(
            query=query, api_key=api_key, model=model,
            max_results=max_results, domains=allowed_domains,
        )
        return []

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        with patch(
            "perspicacite.search.openrouter_fallback.openrouter_academic_search",
            new=fake_openrouter,
        ):
            provider = GoogleScholarPlaywrightProvider(
                delay_seconds=0.0,
                openrouter_fallback_enabled=True,
                openrouter_api_key="sk-abc",
                openrouter_fallback_model="openai/gpt-4o-mini",
                openrouter_fallback_domains=["arxiv.org"],
            )
            await provider.search("deep learning", max_results=7)

    assert captured["query"] == "deep learning"
    assert captured["api_key"] == "sk-abc"
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["max_results"] == 7
    assert captured["domains"] == ["arxiv.org"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_google_scholar_playwright.py::test_captcha_sentinel_is_module_level_object \
              tests/unit/test_google_scholar_playwright.py::test_captcha_triggers_openrouter_fallback \
              tests/unit/test_google_scholar_playwright.py::test_captcha_fallback_disabled_returns_empty \
              tests/unit/test_google_scholar_playwright.py::test_captcha_fallback_passes_correct_args \
              -v
```

Expected: `ImportError` or `AttributeError: _CAPTCHA_SENTINEL`

- [ ] **Step 3: Update `google_scholar_playwright.py`**

The full updated file. Read the current file first, then apply these changes:

**3a. Add module-level sentinel** — after the last `import` statement and before `logger = ...`:

```python
# Module-level sentinel returned by _render_and_extract_cards when a CAPTCHA
# is detected. Allows search() to distinguish "CAPTCHA block" from "no results"
# via identity check (cards is _CAPTCHA_SENTINEL).
_CAPTCHA_SENTINEL: list[dict[str, str]] = []
```

**3b. Update the CAPTCHA branch** inside `_render_and_extract_cards` — replace:

```python
                if "captcha" in html.lower() or "unusual traffic" in html.lower():
                    logger.warning("google_scholar_captcha_detected", url=url[:100])
                    return []
```

with:

```python
                if "captcha" in html.lower() or "unusual traffic" in html.lower():
                    logger.warning("google_scholar_captcha_detected", url=url[:100])
                    return _CAPTCHA_SENTINEL
```

**3c. Update `GoogleScholarPlaywrightProvider.__init__`** — replace the entire `__init__` method:

```python
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
        openrouter_fallback_enabled: bool = True,
        openrouter_api_key: str = "",
        openrouter_fallback_model: str = "deepseek/deepseek-v2-fast",
        openrouter_fallback_domains: list[str] | None = None,
    ) -> None:
        self._delay = delay_seconds
        self._headless = headless
        self._user_agent = user_agent
        self._openrouter_enabled = openrouter_fallback_enabled
        self._openrouter_api_key = openrouter_api_key
        self._openrouter_model = openrouter_fallback_model
        self._openrouter_domains = openrouter_fallback_domains
```

**3d. Update `search()` method** — replace the section that runs the card loop. Find:

```python
        papers: list[Paper] = []
        for card in cards[:max_results]:
```

and insert the sentinel check **immediately after** `cards = await _render_and_extract_cards(...)` and **before** the existing `papers: list[Paper] = []` loop. The full replacement block starting from the `try:` that wraps `_render_and_extract_cards`:

```python
        try:
            cards = await _render_and_extract_cards(
                url,
                delay=self._delay,
                headless=self._headless,
                user_agent=self._user_agent,
            )
        except Exception as exc:
            logger.warning("google_scholar_search_error", error=str(exc))
            return []

        # CAPTCHA detected — fall back to OpenRouter web search if configured
        if cards is _CAPTCHA_SENTINEL:
            if self._openrouter_enabled:
                from perspicacite.search.openrouter_fallback import (
                    openrouter_academic_search,
                )
                return await openrouter_academic_search(
                    query,
                    api_key=self._openrouter_api_key,
                    model=self._openrouter_model,
                    max_results=max_results,
                    allowed_domains=self._openrouter_domains,
                )
            return []

        papers: list[Paper] = []
        for card in cards[:max_results]:
            # ... rest of existing loop unchanged
```

- [ ] **Step 4: Run the new Scholar tests — expect all PASS**

```bash
uv run pytest tests/unit/test_google_scholar_playwright.py -v
```

Expected: all tests pass (existing 7 + new 4 = 11 total)

- [ ] **Step 5: Update `domain_aggregator.py` to pass new config fields**

Find lines 294–303 in `src/perspicacite/search/domain_aggregator.py`:

```python
                providers.append(GoogleScholarPlaywrightProvider(
                    delay_seconds=float(getattr(scholar_cfg, "delay_seconds", 2.0)),
                    headless=bool(getattr(scholar_cfg, "headless", True)),
                    user_agent=str(getattr(
                        scholar_cfg, "user_agent",
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36",
                    )),
                ))
```

Replace with:

```python
                providers.append(GoogleScholarPlaywrightProvider(
                    delay_seconds=float(getattr(scholar_cfg, "delay_seconds", 2.0)),
                    headless=bool(getattr(scholar_cfg, "headless", True)),
                    user_agent=str(getattr(
                        scholar_cfg, "user_agent",
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36",
                    )),
                    openrouter_fallback_enabled=bool(
                        getattr(scholar_cfg, "openrouter_fallback_enabled", True)
                    ),
                    openrouter_api_key=str(
                        getattr(scholar_cfg, "openrouter_api_key", "")
                    ),
                    openrouter_fallback_model=str(
                        getattr(scholar_cfg, "openrouter_fallback_model",
                                "deepseek/deepseek-v2-fast")
                    ),
                    openrouter_fallback_domains=list(
                        getattr(scholar_cfg, "openrouter_fallback_domains", [])
                    ) or None,
                ))
```

- [ ] **Step 6: Run full unit suite**

```bash
uv run pytest tests/unit/ -x -q
```

Expected: all tests pass

- [ ] **Step 7: Lint check**

```bash
uv run ruff check src/perspicacite/search/google_scholar_playwright.py \
                  src/perspicacite/search/openrouter_fallback.py \
                  src/perspicacite/search/domain_aggregator.py
```

Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/search/google_scholar_playwright.py \
        src/perspicacite/search/domain_aggregator.py \
        tests/unit/test_google_scholar_playwright.py
git commit -m "feat: wire OpenRouter CAPTCHA fallback into GoogleScholarPlaywrightProvider"
```
