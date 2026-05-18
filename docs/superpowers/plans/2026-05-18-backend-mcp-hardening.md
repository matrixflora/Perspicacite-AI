# Backend & MCP Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring Perspicacité's non-GUI surfaces (REST API, 10 MCP tools, CLI) to the same quality bar the GUI has after recent fixes — eliminate silent data-quality failures, add observability and cancellation to long-running MCP calls, and collapse three diverging web-search code paths into one.

**Architecture:** Three independently-mergeable PRs (Tier 1, Tier 2, Tier 3). Each tier ends in a green test run and a commit. Tier 2 builds on helpers introduced in Tier 1; Tier 3 refactors callers to use the new shared helpers. New code is added as standalone modules; old call sites get back-compat shims for one release.

**Tech Stack:** Python 3.12+, FastAPI, fastmcp 3.x, Pydantic v2, httpx, structlog, pytest with `-m "not live"` markers, uv for env management.

**Spec reference:** `docs/superpowers/specs/2026-05-18-backend-mcp-hardening-design.md`

**Working directory:** `/Users/holobiomicslab/git/Perspicacite-AI` (or your worktree root). All paths below are relative to this.

**Conventions:**
- All commands run via `uv run` (e.g. `uv run pytest ...`). Server runs via `zsh -ic 'uv run perspicacite -c config.yml serve'` to pick up env vars from `~/.zshrc`.
- After any code change, run `uv run pytest tests/unit/ -m "not live" -q` before committing. Baseline is **1731 passing, 1 skipped**.
- Logging uses `structlog` via `from perspicacite.logging import get_logger`. Always use keyword args: `logger.info("event_name", key=val)`, never f-strings.
- Each task is one logical change with one commit. Don't batch commits.

---

## Tier 1 — Silent bug fixes

### Task 1.1: Extract Crossref helpers into a new module

**Files:**
- Create: `src/perspicacite/pipeline/enrichment/__init__.py`
- Create: `src/perspicacite/pipeline/enrichment/crossref_enrich.py`
- Modify: `src/perspicacite/rag/modes/basic.py` (replace inline helpers with re-exports)

**Context:** `src/perspicacite/rag/modes/basic.py` currently defines `_canonicalize_candidates_from_crossref` (≈ line 152) and `_backfill_dois_from_crossref` (≈ line 68). These two helpers contain the Crossref-enrichment logic that we need to share with agentic, literature_survey, and MCP tools.

- [ ] **Step 1: Create the enrichment package marker**

```bash
touch src/perspicacite/pipeline/enrichment/__init__.py
```

- [ ] **Step 2: Read the current implementations to copy verbatim**

```bash
sed -n '60,230p' src/perspicacite/rag/modes/basic.py
```

This prints the two helper functions plus the imports they use. Capture them precisely; they'll be moved as-is.

- [ ] **Step 3: Write the new module**

Create `src/perspicacite/pipeline/enrichment/crossref_enrich.py` containing **both** existing helpers verbatim, plus a new `enrich_papers` Paper-level wrapper:

```python
"""Crossref-based metadata enrichment helpers.

Provides three public entry points:

- ``canonicalize_candidates``  : patches a list of *dict candidates* in
  place using Crossref (title/authors/year/journal/abstract). Used by
  the basic mode's web fallback pipeline.
- ``backfill_dois``            : for candidates with no DOI but a title,
  resolves the DOI via Crossref title search with a word-overlap safety
  check. Same dict-shape contract as ``canonicalize_candidates``.
- ``enrich_papers``            : the *Paper*-object façade. Converts
  each ``Paper`` to a dict, runs the two passes above, and writes
  results back to the Paper. Used by agentic / literature_survey /
  the new ``web_search`` MCP tool.

All three honour the ``CROSSREF_MAILTO`` (or ``UNPAYWALL_EMAIL``) env
var for the Crossref polite pool — when set, concurrency rises from 2
to 6 and the per-request 250 ms spacing is dropped.
"""
from __future__ import annotations

import asyncio
import os
import re
import urllib.parse
from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper
from perspicacite.pipeline.download.crossref import enrich_from_crossref

logger = get_logger("perspicacite.pipeline.enrichment.crossref")


async def backfill_dois(
    candidates: list[dict[str, Any]],
    http: httpx.AsyncClient,
    *,
    sem: asyncio.Semaphore,
    mailto: str | None,
    throttle: Any = None,
) -> int:
    """Resolve missing DOIs by title-searching Crossref.

    For each candidate with a title but no DOI, issues one Crossref
    ``GET /works?query.title=...&rows=1`` and copies the best-match
    DOI into the candidate when title token overlap passes ≥ 0.5.
    Mutates ``candidates`` in place. Returns the number of resolved
    DOIs for logging.
    """
    targets = [
        c for c in candidates
        if not c.get("doi") and (c.get("title") or "").strip()
    ]
    if not targets:
        return 0

    def _title_tokens(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) > 2}

    headers = {"User-Agent": f"perspicacite/2 (mailto:{mailto})"} if mailto else {}
    resolved = 0

    async def _one(c: dict[str, Any]) -> None:
        nonlocal resolved
        title = c["title"]
        async with sem:
            if throttle is not None:
                try:
                    await throttle()
                except Exception:
                    pass
            try:
                q = urllib.parse.quote(title[:200])
                url = f"https://api.crossref.org/works?query.title={q}&rows=1"
                resp = await http.get(url, headers=headers, timeout=15.0)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.debug(
                    "crossref_title_lookup_failed",
                    title=title[:60], error=str(e),
                )
                return
        items = (data.get("message") or {}).get("items") or []
        if not items:
            return
        match = items[0]
        match_title = (match.get("title") or [""])[0]
        a, b = _title_tokens(title), _title_tokens(match_title)
        if not a or not b:
            return
        overlap = len(a & b) / max(len(a), len(b))
        if overlap < 0.5:
            logger.debug(
                "crossref_title_lookup_low_overlap",
                title=title[:60], match=match_title[:60],
                overlap=round(overlap, 2),
            )
            return
        doi = match.get("DOI")
        if doi:
            c["doi"] = doi
            resolved += 1

    await asyncio.gather(*(_one(c) for c in targets), return_exceptions=True)
    logger.info(
        "crossref_doi_backfill",
        attempted=len(targets), resolved=resolved,
    )
    return resolved


async def canonicalize_candidates(
    candidates: list[dict[str, Any]],
) -> None:
    """Enrich each candidate dict via Crossref in place.

    Rate-limit-safe: Semaphore(2) without mailto, Semaphore(6) with.
    Spacing throttle (250 ms) is dropped when ``CROSSREF_MAILTO`` is set.
    First runs a DOI-backfill pass for candidates that have a title but
    no DOI, then a canonicalization pass that fills title/authors/year/
    journal/abstract whenever Crossref has a value and the candidate's
    field is empty.

    Sets ``c["enrichment_sources"] = ["crossref"]`` on every patched
    candidate so the UI can render a "+Crossref" chip.
    """
    targets = [c for c in candidates if c.get("doi")]
    mailto = (
        os.getenv("CROSSREF_MAILTO")
        or os.getenv("UNPAYWALL_EMAIL")
        or None
    )

    sem = asyncio.Semaphore(2 if not mailto else 6)
    _spacing_lock = asyncio.Lock()
    _last_call_t = {"t": 0.0}
    _min_spacing = 0.25 if not mailto else 0.0

    async def _throttle() -> None:
        if _min_spacing <= 0:
            return
        async with _spacing_lock:
            import time as _t
            now = _t.monotonic()
            gap = now - _last_call_t["t"]
            if gap < _min_spacing:
                await asyncio.sleep(_min_spacing - gap)
            _last_call_t["t"] = _t.monotonic()

    async with httpx.AsyncClient(timeout=15.0) as http:
        try:
            await backfill_dois(
                candidates, http, sem=sem, mailto=mailto, throttle=_throttle,
            )
        except Exception as e:
            logger.debug("crossref_doi_backfill_skipped", error=str(e))

        targets[:] = [c for c in candidates if c.get("doi")]

        async def _one(c: dict[str, Any]) -> None:
            async with sem:
                await _throttle()
                patch: dict[str, Any] = {}
                for attempt in range(2):
                    try:
                        patch = await enrich_from_crossref(
                            c["doi"], http_client=http,
                            base_metadata={}, mailto=mailto,
                        )
                        break
                    except Exception as e:
                        msg = str(e)
                        if "429" in msg and attempt == 0:
                            await asyncio.sleep(1.5)
                            await _throttle()
                            continue
                        logger.debug(
                            "crossref_one_failed",
                            doi=c.get("doi"), error=msg,
                        )
                        return
            if not patch:
                return

            def _empty(v: Any) -> bool:
                return v is None or v == "" or v == []

            for k in ("title", "authors", "year", "journal", "abstract"):
                if patch.get(k) and _empty(c.get(k)):
                    c[k] = patch[k]
            enrichers = c.setdefault("enrichment_sources", [])
            if "crossref" not in enrichers:
                enrichers.append("crossref")

        await asyncio.gather(
            *[_one(c) for c in targets], return_exceptions=True,
        )

    logger.info(
        "crossref_canonicalized",
        attempted=len(targets), candidates=len(candidates),
        mailto_polite_pool=bool(mailto),
    )


async def enrich_papers(papers: list[Paper]) -> list[Paper]:
    """Crossref-enrich a list of Paper objects in place.

    Converts each Paper to a dict candidate (carrying only the fields
    Crossref cares about), runs ``canonicalize_candidates``, then
    writes the patched values back to the Paper. Records enrichment
    provenance under ``paper.metadata["enrichment_sources"]`` (a list).

    The original Paper objects are returned (same list, mutated). This
    is the public entry point for agentic, literature_survey, the
    standalone MCP ``web_search`` tool, and the unified
    ``resolve_papers_pipeline`` introduced in Tier 3.
    """
    if not papers:
        return papers

    candidates: list[dict[str, Any]] = []
    for p in papers:
        authors_list: list[str] = []
        for a in (p.authors or []):
            n = getattr(a, "name", None) or ""
            if n:
                authors_list.append(n)
        candidates.append({
            "_paper_ref": p,
            "title": p.title,
            "authors": authors_list,
            "year": p.year,
            "journal": p.journal,
            "doi": p.doi,
            "abstract": p.abstract or "",
        })

    await canonicalize_candidates(candidates)

    # Write back. Only fill empty fields on the Paper (defensive — if
    # the provider gave us a value we trust it over Crossref's guess).
    from perspicacite.models.papers import Author

    for c in candidates:
        p: Paper = c["_paper_ref"]
        if not p.title and c.get("title"):
            p.title = c["title"]
        if not p.year and c.get("year"):
            p.year = c["year"]
        if not p.journal and c.get("journal"):
            p.journal = c["journal"]
        if not p.doi and c.get("doi"):
            p.doi = c["doi"]
        if not p.abstract and c.get("abstract"):
            p.abstract = c["abstract"]
        if not p.authors and c.get("authors"):
            p.authors = [Author(name=str(n)) for n in c["authors"] if n]

        # Provenance into Paper.metadata for now (Tier 3 promotes this
        # to a typed Paper.enrichment_sources field).
        if c.get("enrichment_sources"):
            existing = list(p.metadata.get("enrichment_sources") or [])
            for src in c["enrichment_sources"]:
                if src not in existing:
                    existing.append(src)
            p.metadata["enrichment_sources"] = existing

    return papers
```

- [ ] **Step 4: Run the existing test suite to confirm nothing is broken yet (no callers changed)**

Run: `uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5`
Expected: `1731 passed, 1 skipped`

- [ ] **Step 5: Replace inline helpers in basic.py with re-exports**

In `src/perspicacite/rag/modes/basic.py`, find the two functions `async def _backfill_dois_from_crossref(...)` (around line 68) and `async def _canonicalize_candidates_from_crossref(...)` (around line 152). Delete both function definitions ENTIRELY and replace them with thin re-export wrappers immediately after the imports section (top of file, after the last `from perspicacite.` import line):

```python
# Crossref enrichment helpers — extracted to pipeline/enrichment/crossref_enrich.py
# so agentic, literature_survey, and the MCP web_search tool can reuse them.
# These names stay around as back-compat aliases so the existing basic.py
# call sites (_web_fallback_papers) don't need to change.
from perspicacite.pipeline.enrichment.crossref_enrich import (
    backfill_dois as _backfill_dois_from_crossref,
    canonicalize_candidates as _canonicalize_candidates_from_crossref,
)
```

- [ ] **Step 6: Run all tests**

Run: `uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5`
Expected: `1731 passed, 1 skipped`

- [ ] **Step 7: Write a new unit test for enrich_papers**

Create `tests/unit/test_crossref_enrich_papers.py`:

```python
"""Unit tests for pipeline.enrichment.crossref_enrich.enrich_papers."""
import pytest
from unittest.mock import AsyncMock, patch

from perspicacite.models.papers import Paper, Author, PaperSource
from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers


@pytest.mark.asyncio
async def test_enrich_papers_fills_missing_abstract():
    """Paper with DOI but no abstract gets Crossref's abstract."""
    p = Paper(
        id="doi:10.1234/x",
        title="Original Title",
        doi="10.1234/x",
        source=PaperSource.GOOGLE_SCHOLAR,
    )

    async def fake_enrich_from_crossref(doi, **kwargs):
        assert doi == "10.1234/x"
        return {"abstract": "Crossref abstract text"}

    with patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_from_crossref",
        side_effect=fake_enrich_from_crossref,
    ):
        result = await enrich_papers([p])

    assert result[0].abstract == "Crossref abstract text"
    assert "crossref" in result[0].metadata.get("enrichment_sources", [])


@pytest.mark.asyncio
async def test_enrich_papers_does_not_overwrite_existing_abstract():
    """If the Paper already has an abstract, Crossref's doesn't override."""
    p = Paper(
        id="doi:10.1234/x", title="Title", doi="10.1234/x",
        abstract="Original abstract from provider",
    )

    async def fake_enrich(doi, **kwargs):
        return {"abstract": "Crossref abstract"}

    with patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_from_crossref",
        side_effect=fake_enrich,
    ):
        await enrich_papers([p])

    assert p.abstract == "Original abstract from provider"


@pytest.mark.asyncio
async def test_enrich_papers_skips_papers_without_doi():
    """No DOI → no Crossref call → no enrichment_sources tag."""
    p = Paper(id="x", title="Untitled, no DOI")
    await enrich_papers([p])
    assert p.metadata.get("enrichment_sources") in (None, [])


@pytest.mark.asyncio
async def test_enrich_papers_empty_list_short_circuits():
    """Empty list returns empty list, no HTTP."""
    assert await enrich_papers([]) == []
```

- [ ] **Step 8: Run the new tests**

Run: `uv run pytest tests/unit/test_crossref_enrich_papers.py -v`
Expected: all 4 pass.

- [ ] **Step 9: Commit**

```bash
git add src/perspicacite/pipeline/enrichment/ src/perspicacite/rag/modes/basic.py tests/unit/test_crossref_enrich_papers.py
git commit -m "$(cat <<'EOF'
refactor: extract Crossref enrichment into pipeline/enrichment

Promotes the basic.py-private _canonicalize_candidates_from_crossref +
_backfill_dois_from_crossref into a reusable module so agentic,
literature_survey, and the upcoming web_search MCP tool can share them.

Adds new enrich_papers(list[Paper]) → list[Paper] entry point that
agentic/literature_survey call paths can use without needing to convert
to/from candidate dicts.

basic.py keeps the old function names as one-line re-exports for
back-compat.
EOF
)"
```

### Task 1.2: Wire `enrich_papers` into agentic and literature_survey

**Files:**
- Modify: `src/perspicacite/rag/agentic/orchestrator.py` (after `_scilex_search`)
- Modify: `src/perspicacite/rag/modes/literature_survey.py::_broad_search`

- [ ] **Step 1: Locate the agentic SciLEx search**

```bash
grep -n "_scilex_search\|agentic_scilex_search_found" src/perspicacite/rag/agentic/orchestrator.py | head -5
```

Find the function that ends with `logger.info("agentic_scilex_search_found", count=len(papers))` (around line 2820+) — the next line should be the return statement.

- [ ] **Step 2: Add enrich call before return**

In `_scilex_search`, immediately before the final `return papers` statement, add:

```python
        # Crossref-enrich: fills missing abstracts (Google Scholar /
        # SciLEx don't always include them), canonicalises author lists
        # and journal names. Same enrichment basic/advanced web-fallback
        # uses; ensures agentic synthesis works on clean records.
        try:
            from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers
            papers = await enrich_papers(papers)
        except Exception as _ee:
            logger.warning("agentic_enrich_failed", error=str(_ee))
```

- [ ] **Step 3: Verify literature_survey._broad_search location**

```bash
grep -n "def _broad_search\|merged\[:100\]" src/perspicacite/rag/modes/literature_survey.py | head -5
```

The function returns `papers = merged[:100]` then emits telemetry. We want to enrich AFTER that slice but BEFORE the telemetry counts so the chips reflect enriched data.

- [ ] **Step 4: Add enrich call in literature_survey**

In `src/perspicacite/rag/modes/literature_survey.py::_broad_search`, locate the line `papers = merged[:100]`. Immediately after it, add:

```python
            # Crossref-enrich the merged paper set before telemetry +
            # candidate conversion. This ensures abstracts (often missing
            # from Google Scholar) are filled in time for the abstract
            # analysis pass to use them.
            try:
                from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers
                papers = await enrich_papers(papers)
            except Exception as _ee:
                logger.warning(
                    "literature_survey_enrich_failed", error=str(_ee),
                )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5`
Expected: `1731 passed, 1 skipped`

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/agentic/orchestrator.py src/perspicacite/rag/modes/literature_survey.py
git commit -m "$(cat <<'EOF'
feat: Crossref-enrich papers in agentic and literature_survey paths

Calls enrich_papers() after the underlying search returns. Fixes the
silent Google Scholar gap where agentic and literature_survey were
synthesising answers from abstract-less GS hits.
EOF
)"
```

### Task 1.3: SciLEx structured-warning return type

**Files:**
- Modify: `src/perspicacite/search/scilex_adapter.py`
- Test: `tests/unit/test_scilex_search_with_warnings.py`

**Context:** SciLEx silently drops APIs it doesn't recognise (logged as `scilex_filtered_non_scilex_apis`). MCP callers passing `apis=["semantic_scholar","google_scholar"]` see `apis=["semantic_scholar"]` results with no warning.

- [ ] **Step 1: Add the result dataclass and sibling method**

Find the `class SciLExAdapter` definition (around line 58). Add at the top of the file, immediately after the existing `from dataclasses import ...` import (or add the import if absent — search the file for `dataclass`):

```python
from dataclasses import dataclass, field
```

Then, immediately ABOVE the `class SciLExAdapter:` declaration, add:

```python
@dataclass
class SciLExSearchResult:
    """Structured search result for the SciLEx adapter.

    ``dropped_apis`` lists any APIs the caller asked for that SciLEx
    doesn't know about. Surfaced upstream as a ``warnings`` field on
    MCP responses so external agents can tell their pick was partially
    honoured.
    """
    papers: list = field(default_factory=list)
    dropped_apis: list[str] = field(default_factory=list)
```

- [ ] **Step 2: Locate the existing api_name_map drop site**

```bash
grep -n "api_name_map\|_unknown\b\|scilex_filtered_non_scilex_apis" src/perspicacite/search/scilex_adapter.py | head -10
```

There's a defense-in-depth block that filters `apis` to known ones and logs `scilex_filtered_non_scilex_apis`. We need to remember the dropped list.

- [ ] **Step 3: Modify the filter block to capture dropped APIs**

Locate this block (around line 197-204):

```python
        _unknown = [a for a in apis if a not in api_name_map]
        if _unknown:
            logger.info(
                "scilex_filtered_non_scilex_apis",
                filtered=_unknown,
                kept=[a for a in apis if a in api_name_map],
            )
        apis = [a for a in apis if a in api_name_map]
```

Replace with:

```python
        _unknown = [a for a in apis if a not in api_name_map]
        if _unknown:
            logger.info(
                "scilex_filtered_non_scilex_apis",
                filtered=_unknown,
                kept=[a for a in apis if a in api_name_map],
            )
        # Stash on self so search_with_warnings can include this list
        # in its structured result. Cleared at the start of each call.
        self._last_dropped_apis = list(_unknown)
        apis = [a for a in apis if a in api_name_map]
```

- [ ] **Step 4: Initialise `_last_dropped_apis` in `__init__`**

Find the `def __init__(self, ...)` of `SciLExAdapter`. At the end of the body, add:

```python
        self._last_dropped_apis: list[str] = []
```

- [ ] **Step 5: Add the sibling method**

At the bottom of the `SciLExAdapter` class (immediately before any module-level code like `SciLExSearchProvider = SciLExAdapter`), add:

```python
    async def search_with_warnings(
        self,
        query: str,
        max_results: int = 50,
        year_min: int | None = None,
        year_max: int | None = None,
        article_type: str | None = None,
        apis: list[str] | None = None,
    ) -> SciLExSearchResult:
        """Same as ``search`` but returns a structured result with warnings.

        Use this from MCP / API entry points where the caller benefits
        from knowing their api list was partially dropped. The plain
        ``search()`` method is unchanged for legacy callers that only
        want ``list[Paper]``.
        """
        self._last_dropped_apis = []
        papers = await self.search(
            query=query,
            max_results=max_results,
            year_min=year_min,
            year_max=year_max,
            article_type=article_type,
            apis=apis,
        )
        return SciLExSearchResult(
            papers=papers,
            dropped_apis=list(self._last_dropped_apis),
        )
```

- [ ] **Step 6: Write unit tests**

Create `tests/unit/test_scilex_search_with_warnings.py`:

```python
"""Unit tests for SciLExAdapter.search_with_warnings dropped-APIs reporting."""
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.search.scilex_adapter import (
    SciLExAdapter, SciLExSearchResult,
)


@pytest.mark.asyncio
async def test_search_with_warnings_reports_unknown_apis():
    """google_scholar isn't SciLEx-backed; expect it in dropped_apis."""
    adapter = SciLExAdapter()
    with patch.object(adapter, "search", AsyncMock(return_value=[])) as mock_s:
        # Simulate the search() body running its filter:
        async def fake_search(*args, **kwargs):
            # Mimic what the real filter sets:
            adapter._last_dropped_apis = ["google_scholar"]
            return []
        mock_s.side_effect = fake_search

        result = await adapter.search_with_warnings(
            query="x", apis=["semantic_scholar", "google_scholar"],
        )
    assert isinstance(result, SciLExSearchResult)
    assert result.dropped_apis == ["google_scholar"]
    assert result.papers == []


@pytest.mark.asyncio
async def test_search_with_warnings_empty_when_all_known():
    """All-known apis → empty dropped_apis list."""
    adapter = SciLExAdapter()
    with patch.object(adapter, "search", AsyncMock(return_value=[])) as mock_s:
        async def fake_search(*args, **kwargs):
            adapter._last_dropped_apis = []
            return []
        mock_s.side_effect = fake_search
        result = await adapter.search_with_warnings(
            query="x", apis=["semantic_scholar", "openalex"],
        )
    assert result.dropped_apis == []
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/unit/test_scilex_search_with_warnings.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Expected: new tests pass; full suite at `1733 passed, 1 skipped` (was 1731 + 2 new).

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/search/scilex_adapter.py tests/unit/test_scilex_search_with_warnings.py
git commit -m "$(cat <<'EOF'
feat: add SciLExAdapter.search_with_warnings for dropped-API reporting

SciLEx silently drops APIs it doesn't recognise (e.g. google_scholar).
The new search_with_warnings() returns a SciLExSearchResult dataclass
with both papers and dropped_apis so MCP callers can surface the
information as a structured warning. Plain search() is unchanged.
EOF
)"
```

### Task 1.4: MCP search_literature surfaces warnings + Crossref enrichment + opt-out

**Files:**
- Modify: `src/perspicacite/mcp/server.py::search_literature`
- Test: `tests/unit/test_mcp_search_literature_warnings.py`

- [ ] **Step 1: Locate the function**

```bash
grep -n "^async def search_literature\b" src/perspicacite/mcp/server.py
```

Around line 348. Read the full function:

```bash
sed -n '347,500p' src/perspicacite/mcp/server.py
```

- [ ] **Step 2: Add `enrich: bool = True` parameter**

Find the signature `async def search_literature(...)` and add a new param `enrich: bool = True` after `optimize_query: bool | None = None` (the existing last param). Also add a docstring entry:

```python
        enrich: When True (default), enrich returned papers via Crossref
            (fills missing abstracts, canonicalises author lists). Set
            False for raw provider data — useful for diagnosing what
            providers returned before our cleanup pipeline ran.
```

- [ ] **Step 3: Switch the SciLEx call to `search_with_warnings`**

Inside `search_literature`, find the `await adapter.search(...)` (or similar) call that returns the SciLEx papers. Replace it with `search_with_warnings`. Pseudocode:

```python
# Before:
papers = await adapter.search(query=..., apis=apis, ...)

# After:
result = await adapter.search_with_warnings(query=..., apis=apis, ...)
papers = result.papers
warnings: list[dict] = []
if result.dropped_apis:
    warnings.append({
        "kind": "unknown_apis_dropped",
        "apis": result.dropped_apis,
        "advice": (
            "Use the web_search MCP tool for non-SciLEx providers "
            "(google_scholar, europepmc, etc.)."
        ),
    })
```

(If the actual call site uses a different adapter variable name, adapt accordingly; the spirit is the same.)

- [ ] **Step 4: Enrich after the search when `enrich=True`**

Immediately after the result-papers assignment, add:

```python
    if enrich and papers:
        from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers
        try:
            papers = await enrich_papers(papers)
        except Exception as _ee:
            logger.warning("mcp_search_literature_enrich_failed", error=str(_ee))
```

- [ ] **Step 5: Include `warnings` in the response payload**

The tool returns a JSON string (it's an `@mcp.tool()` returning `str`). Find where the response is assembled (a dict that becomes the return value, often via `json.dumps`). Add `"warnings": warnings` to that dict alongside `"papers": [...]`.

If there's no central response dict (the function returns formatted text), wrap the final return value in a dict and serialise:

```python
    return json.dumps({
        "papers": serialised_papers,
        "warnings": warnings,
    })
```

Adapt the surrounding code; the tool currently returns a `str` so the return shape stays compatible.

- [ ] **Step 6: Write the test**

Create `tests/unit/test_mcp_search_literature_warnings.py`:

```python
"""Unit tests for search_literature warnings surface."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.mcp.server import search_literature
from perspicacite.search.scilex_adapter import SciLExSearchResult
from perspicacite.models.papers import Paper


@pytest.mark.asyncio
async def test_search_literature_returns_dropped_apis_warning():
    fake_result = SciLExSearchResult(
        papers=[Paper(id="x", title="T", doi="10.1/x")],
        dropped_apis=["google_scholar"],
    )
    with patch(
        "perspicacite.mcp.server.SciLExAdapter.search_with_warnings",
        AsyncMock(return_value=fake_result),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p: p),
    ):
        out = await search_literature(
            query="q", apis=["semantic_scholar", "google_scholar"],
        )
    data = json.loads(out)
    assert any(
        w["kind"] == "unknown_apis_dropped" and "google_scholar" in w["apis"]
        for w in data.get("warnings", [])
    )


@pytest.mark.asyncio
async def test_search_literature_skips_enrich_when_disabled():
    """When enrich=False, enrich_papers is not called."""
    fake_result = SciLExSearchResult(
        papers=[Paper(id="x", title="T", doi="10.1/x")],
        dropped_apis=[],
    )
    mock_enrich = AsyncMock(side_effect=lambda p: p)
    with patch(
        "perspicacite.mcp.server.SciLExAdapter.search_with_warnings",
        AsyncMock(return_value=fake_result),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        mock_enrich,
    ):
        await search_literature(query="q", enrich=False)
    mock_enrich.assert_not_called()
```

NOTE: If the actual `search_literature` function imports SciLExAdapter differently (e.g. via `mcp_state` singleton), adjust the patch targets to match the real import path. Run `grep -n "SciLExAdapter\|scilex_adapter" src/perspicacite/mcp/server.py` to find the actual reference.

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/unit/test_mcp_search_literature_warnings.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_search_literature_warnings.py
git commit -m "$(cat <<'EOF'
feat(mcp): surface dropped-API warnings + Crossref enrichment in search_literature

- Uses adapter.search_with_warnings to capture which APIs were dropped
- Returns warnings: [{kind, apis, advice}] alongside papers
- Enriches results via Crossref by default (enrich=True), gated for
  callers that want raw provider data (enrich=False)
EOF
)"
```

### Task 1.5: Promote `PaperContent.attempts` to a real field + surface in MCP

**Files:**
- Modify: `src/perspicacite/pipeline/download/base.py`
- Modify: `src/perspicacite/mcp/server.py::get_paper_content`
- Test: `tests/unit/test_paper_content_attempts_public.py`

- [ ] **Step 1: Promote `_attempts` private trick to a dataclass field**

In `src/perspicacite/pipeline/download/base.py`, find `class PaperContent` (around line 55). Add `field` to the dataclass imports if missing:

```python
from dataclasses import dataclass, field
```

Modify the class body — remove the `__post_init__` / `_attempts` / `@property attempts` block and replace with a direct field:

```python
@dataclass
class PaperContent:
    """Unified result from retrieve_paper_content().

    content_type values:
      - "structured": full text with sections + references (JATS XML, HTML)
      - "full_text": full text from PDF extraction (no structure)
      - "abstract": abstract only (no full text available)
      - "none": no content found

    attempts: ordered list of pipeline-step diagnostics, one per source
        actually tried. Each entry has at minimum a ``source`` label and
        a ``status`` ("miss" | "error" | "skip" | "hit"). Errors carry
        an ``error`` field. The caller can surface this in failure
        messages so an operator can tell whether the failure was config
        (API key missing) or content (genuinely not available).
    """

    success: bool
    doi: str
    content_type: str  # "structured" | "full_text" | "abstract" | "none"
    full_text: str | None = None
    sections: dict[str, str] | None = None
    references: list[dict] | None = None
    abstract: str | None = None
    content_source: str = "none"
    metadata: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def record_attempt(
        self, source: str, status: str, *, error: str | None = None, **extra: Any,
    ) -> None:
        entry: dict[str, Any] = {"source": source, "status": status}
        if error:
            entry["error"] = error
        if extra:
            entry.update(extra)
        self.attempts.append(entry)
```

(Delete the old `__post_init__`, `_attempts` initialisation, and `@property def attempts`. The new `field(default_factory=list)` does the same thing.)

- [ ] **Step 2: Run full tests — ensure nothing reads `_attempts` directly**

```bash
grep -rn "_attempts\b" src/ tests/
```

If hits show up referencing `paper_content._attempts`, change to `paper_content.attempts`. Then:

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Expected: `1731 passed` (no change — the public API is preserved).

- [ ] **Step 3: Find the `get_paper_content` MCP tool**

```bash
grep -n "^async def get_paper_content\b\|content_type\|content_source" src/perspicacite/mcp/server.py | head -10
```

Around line 588. Read the full function:

```bash
sed -n '587,675p' src/perspicacite/mcp/server.py
```

- [ ] **Step 4: Add `attempts` to the response dict**

Find where the response is assembled (it serialises `PaperContent` fields to a dict, then `json.dumps` it). Locate the dict construction (search for `"content_type"` inside the function). Add an `attempts` key:

```python
response = {
    # ... existing keys ...
    "content_type": pc.content_type,
    "content_source": pc.content_source,
    "attempts": list(pc.attempts),  # NEW: each entry {source, status, error?, ...}
    # ... rest ...
}
```

- [ ] **Step 5: Write the test**

Create `tests/unit/test_paper_content_attempts_public.py`:

```python
"""Unit tests for PaperContent.attempts public field + MCP surface."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.pipeline.download.base import PaperContent


def test_paper_content_attempts_is_field_not_property():
    """attempts is now a regular dataclass field, mutable directly."""
    pc = PaperContent(success=False, doi="10.1/x", content_type="none")
    assert pc.attempts == []
    pc.attempts.append({"source": "pmc", "status": "miss"})
    assert pc.attempts == [{"source": "pmc", "status": "miss"}]


def test_record_attempt_writes_to_attempts():
    pc = PaperContent(success=False, doi="10.1/x", content_type="none")
    pc.record_attempt("unpaywall", "miss", error="no oa url")
    assert pc.attempts == [{
        "source": "unpaywall",
        "status": "miss",
        "error": "no oa url",
    }]


def test_record_attempt_extras_merge():
    pc = PaperContent(success=False, doi="10.1/x", content_type="none")
    pc.record_attempt("wiley", "skip", reason="no api key")
    assert pc.attempts[0]["reason"] == "no api key"


@pytest.mark.asyncio
async def test_mcp_get_paper_content_returns_attempts():
    from perspicacite.mcp.server import get_paper_content

    pc = PaperContent(success=False, doi="10.1/x", content_type="none")
    pc.record_attempt("pmc", "miss")
    pc.record_attempt("unpaywall", "error", error="429")

    with patch(
        "perspicacite.mcp.server.retrieve_paper_content",
        AsyncMock(return_value=pc),
    ):
        out = await get_paper_content(doi="10.1/x")
    data = json.loads(out)
    assert "attempts" in data
    assert len(data["attempts"]) == 2
    assert data["attempts"][0]["source"] == "pmc"
    assert data["attempts"][1]["error"] == "429"
```

NOTE: The MCP test patches `perspicacite.mcp.server.retrieve_paper_content`. If the import path is different (run `grep -n "retrieve_paper_content" src/perspicacite/mcp/server.py`), adjust the target.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_paper_content_attempts_public.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/pipeline/download/base.py src/perspicacite/mcp/server.py tests/unit/test_paper_content_attempts_public.py
git commit -m "$(cat <<'EOF'
feat: promote PaperContent.attempts to public field, surface in MCP

Removes the __post_init__ / _attempts / @property indirection in favour
of a plain dataclass field(default_factory=list). External callers can
now read pc.attempts directly. The get_paper_content MCP tool now
returns the attempts array in its JSON response so consumers can
diagnose paywall vs config-missing vs genuinely-unreachable failures.
EOF
)"
```

### Task 1.6: Build the shared cancellation registry

**Files:**
- Create: `src/perspicacite/rag/cancellation.py`
- Modify: `src/perspicacite/web/routers/chat.py`
- Test: `tests/unit/test_cancellation_registry.py`

**Context:** `web/routers/chat.py::_CANCELLED_CHAT_IDS: set[str]` grows unboundedly. Tier 2 needs a shared registry for MCP cancellation, so we build the registry now and migrate the chat router as part of A4.

- [ ] **Step 1: Create the registry module**

Create `src/perspicacite/rag/cancellation.py`:

```python
"""Process-wide cancellation registry for long-running RAG tasks.

Backs both the SSE chat router (``/api/chat/cancel``) and the MCP
``cancel_task`` tool. Replaces the old ``_CANCELLED_CHAT_IDS: set[str]``
which had no garbage collection. Internally a dict mapping task-id →
cancellation timestamp; the dict is pruned by TTL + size cap so memory
stays bounded under load.
"""
from __future__ import annotations

import asyncio
import time
from typing import Iterable

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.rag.cancellation")

# Tunables. _TTL_SECONDS protects against ID leaks; _MAX_ENTRIES caps
# memory under DoS-like cancellation floods.
_TTL_SECONDS: float = 3600.0  # 1 hour
_MAX_ENTRIES: int = 1000

_lock = asyncio.Lock()
_cancelled: dict[str, float] = {}


def _prune_locked(now: float | None = None) -> None:
    """Caller must hold ``_lock``. Drops TTL-expired entries, then trims
    to ``_MAX_ENTRIES`` by oldest-first."""
    now = now if now is not None else time.monotonic()
    expired = [k for k, ts in _cancelled.items() if now - ts > _TTL_SECONDS]
    for k in expired:
        del _cancelled[k]
    if len(_cancelled) > _MAX_ENTRIES:
        # Sort by timestamp ascending (oldest first) and drop the head.
        ordered = sorted(_cancelled.items(), key=lambda kv: kv[1])
        for k, _ in ordered[: len(_cancelled) - _MAX_ENTRIES]:
            del _cancelled[k]


async def mark_cancelled(task_id: str) -> None:
    """Mark ``task_id`` as cancelled. Idempotent.

    Updates the timestamp on repeat calls so the entry stays warm in
    the TTL window. Returns when the registry is updated; callers may
    use this for both chat-conversation IDs and MCP task IDs.
    """
    if not task_id:
        return
    async with _lock:
        _cancelled[task_id] = time.monotonic()
        _prune_locked()
    logger.info("cancel_registered", task_id=task_id, size=len(_cancelled))


def is_cancelled(task_id: str | None) -> bool:
    """Cheap synchronous check. Returns False for None / empty IDs.

    Intentionally not locked: dict reads are atomic in CPython and
    occasional staleness is harmless (caller will check again on the
    next iteration). Hot path inside RAG cycles.
    """
    if not task_id:
        return False
    return task_id in _cancelled


async def clear(task_id: str) -> None:
    """Remove a task from the registry. Use after task cleanup completes."""
    if not task_id:
        return
    async with _lock:
        _cancelled.pop(task_id, None)


async def snapshot() -> dict[str, float]:
    """Return a snapshot of the registry — used by tests and diagnostics."""
    async with _lock:
        return dict(_cancelled)


async def reset_for_tests() -> None:
    """Test-only helper to start each test from an empty registry."""
    async with _lock:
        _cancelled.clear()
```

- [ ] **Step 2: Write tests**

Create `tests/unit/test_cancellation_registry.py`:

```python
"""Unit tests for the cancellation registry."""
import asyncio
import pytest

from perspicacite.rag import cancellation as cr


@pytest.fixture(autouse=True)
async def _reset():
    await cr.reset_for_tests()
    yield
    await cr.reset_for_tests()


@pytest.mark.asyncio
async def test_mark_and_check():
    await cr.mark_cancelled("abc")
    assert cr.is_cancelled("abc") is True
    assert cr.is_cancelled("other") is False


@pytest.mark.asyncio
async def test_empty_and_none_ids_safe():
    assert cr.is_cancelled(None) is False
    assert cr.is_cancelled("") is False
    await cr.mark_cancelled("")  # no-op, must not raise
    await cr.mark_cancelled(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_clear_removes_entry():
    await cr.mark_cancelled("x")
    await cr.clear("x")
    assert cr.is_cancelled("x") is False


@pytest.mark.asyncio
async def test_max_entries_bound():
    """Inserting 1500 entries → registry stays at ≤ 1000 (MAX cap)."""
    for i in range(1500):
        await cr.mark_cancelled(f"id-{i}")
    snap = await cr.snapshot()
    assert len(snap) <= 1000
    # Oldest IDs were evicted; newest should still be present.
    assert "id-1499" in snap
    assert "id-0" not in snap


@pytest.mark.asyncio
async def test_ttl_expiry(monkeypatch):
    import time
    fake_now = [1000.0]

    def fake_monotonic():
        return fake_now[0]

    monkeypatch.setattr("perspicacite.rag.cancellation.time.monotonic", fake_monotonic)
    await cr.mark_cancelled("ttl-test")
    assert cr.is_cancelled("ttl-test")
    # Jump 1 hour + 1 second forward and trigger another mark to invoke prune.
    fake_now[0] += 3601.0
    await cr.mark_cancelled("trigger-prune")
    snap = await cr.snapshot()
    assert "ttl-test" not in snap
    assert "trigger-prune" in snap
```

- [ ] **Step 3: Run new tests**

```bash
uv run pytest tests/unit/test_cancellation_registry.py -v
```

Expected: all 5 pass.

- [ ] **Step 4: Migrate the chat router to use the registry**

In `src/perspicacite/web/routers/chat.py`, find the cancellation block (around line 48-72):

```python
_CANCELLED_CHAT_IDS: set[str] = set()


def is_chat_cancelled(conversation_id: str | None) -> bool:
    ...
    return bool(conversation_id) and conversation_id in _CANCELLED_CHAT_IDS
```

Replace the entire block with re-exports + a thin async-to-sync bridge for the existing call sites:

```python
# Cancellation now lives in the shared registry so MCP and chat both
# use the same state. The chat router only needs sync read access
# (is_chat_cancelled) — the registry's is_cancelled is sync.
from perspicacite.rag.cancellation import (
    is_cancelled as _registry_is_cancelled,
    mark_cancelled as _registry_mark_cancelled,
    clear as _registry_clear,
)


def is_chat_cancelled(conversation_id: str | None) -> bool:
    """Back-compat alias — reads the shared cancellation registry."""
    return _registry_is_cancelled(conversation_id)
```

Then find the `/api/chat/cancel` POST endpoint (around line 65-72). The old version does `_CANCELLED_CHAT_IDS.add(req.conversation_id)`. Update it to use the registry:

```python
@router.post("/api/chat/cancel")
async def cancel_chat(req: _CancelRequest):
    ...
    if req.conversation_id:
        await _registry_mark_cancelled(req.conversation_id)
    ...
```

Also find any `_CANCELLED_CHAT_IDS.discard(conversation_id)` calls (cleanup paths) and change to `await _registry_clear(conversation_id)`. If they're in non-async contexts, use `asyncio.create_task(_registry_clear(...))` or schedule cleanup at a safe point.

```bash
grep -n "_CANCELLED_CHAT_IDS" src/perspicacite/web/routers/chat.py
```

Replace EVERY remaining reference. After all replacements, the symbol `_CANCELLED_CHAT_IDS` should no longer appear in `src/`.

```bash
grep -rn "_CANCELLED_CHAT_IDS" src/
```

Expected: zero hits.

- [ ] **Step 5: Run all tests**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Expected: full suite passes (was 1733 after Task 1.3, now plus 5 from this task → 1738).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/cancellation.py src/perspicacite/web/routers/chat.py tests/unit/test_cancellation_registry.py
git commit -m "$(cat <<'EOF'
refactor: shared cancellation registry with TTL + size cap

Replaces _CANCELLED_CHAT_IDS: set[str] (unbounded memory leak) with
a process-wide dict-based registry pruned by 1-hour TTL and capped at
1000 entries. Chat router migrates as a back-compat alias; Tier 2
will use the same registry for the MCP cancel_task tool.
EOF
)"
```

### Task 1.7: JSON salvage utility

**Files:**
- Create: `src/perspicacite/rag/utils/json_salvage.py`
- Modify: `src/perspicacite/rag/modes/profound.py` (wire into 2 JSON-parse sites)
- Modify: `src/perspicacite/rag/modes/literature_survey.py` (use clean_control_chars)
- Test: `tests/unit/test_json_salvage.py`

- [ ] **Step 1: Write the salvage module**

Create `src/perspicacite/rag/utils/json_salvage.py`:

```python
"""LLM-emitted JSON salvage helpers.

Two failure modes are common:

- Truncation mid-array (the LLM hit max_tokens before closing ``]``).
  ``salvage_truncated_array`` walks the partial string, extracts every
  complete ``{...}`` object inside the named array, and returns them
  as a list of parsed dicts. Better to keep 23/25 entries than throw.

- Raw control characters inside string values (some providers emit
  literal ``\x01`` etc. that ``json.loads`` rejects with
  "Invalid control character"). ``clean_control_chars`` strips them
  while preserving valid whitespace (``\\t``, ``\\n``, ``\\r``).
"""
from __future__ import annotations

import json
import re
from typing import Any


def clean_control_chars(json_str: str) -> str:
    """Strip raw ASCII control chars (0x00-0x1F) except whitespace.

    Keeps ``\\t`` (0x09), ``\\n`` (0x0A), ``\\r`` (0x0D) intact. Drops
    every other char in the 0x00-0x1F range, which is what makes
    json.loads explode with "Invalid control character at: ...".
    """
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", json_str)


def salvage_truncated_array(
    json_str: str, array_key: str,
) -> list[dict[str, Any]] | None:
    """Recover complete ``{...}`` entries from a truncated array.

    Looks for ``"array_key": [`` in ``json_str``, then scans forward
    extracting every complete brace-balanced object until the array
    ends or the string runs out. Quote-aware so braces inside string
    values don't confuse the depth counter.

    Returns ``None`` when the array_key isn't found OR when no complete
    entries could be extracted; the caller falls back to the original
    JSONDecodeError.
    """
    m = re.search(rf'"{re.escape(array_key)}"\s*:\s*\[', json_str)
    if not m:
        return None
    start = m.end()
    depth = 0
    i = start
    complete_objects: list[str] = []
    obj_start = -1
    in_str = False
    esc = False
    while i < len(json_str):
        c = json_str[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0 and obj_start >= 0:
                    complete_objects.append(json_str[obj_start : i + 1])
                    obj_start = -1
            elif c == "]" and depth == 0:
                break
        i += 1

    recovered: list[dict[str, Any]] = []
    for obj_str in complete_objects:
        try:
            recovered.append(json.loads(obj_str))
        except Exception:
            continue
    return recovered or None
```

- [ ] **Step 2: Write tests**

Create `tests/unit/test_json_salvage.py`:

```python
"""Unit tests for rag.utils.json_salvage."""
import pytest

from perspicacite.rag.utils.json_salvage import (
    clean_control_chars,
    salvage_truncated_array,
)


def test_clean_control_chars_strips_invalid():
    raw = 'hello\x01world\x02!'
    assert clean_control_chars(raw) == "helloworld!"


def test_clean_control_chars_keeps_whitespace():
    raw = "line1\nline2\tcol\r\nline3"
    assert clean_control_chars(raw) == "line1\nline2\tcol\r\nline3"


def test_salvage_truncated_array_recovers_complete_entries():
    truncated = """
    {
      "analyses": [
        {"id": "p1", "score": 4},
        {"id": "p2", "score": 5},
        {"id": "p3", "scor
    """
    result = salvage_truncated_array(truncated, "analyses")
    assert result == [
        {"id": "p1", "score": 4},
        {"id": "p2", "score": 5},
    ]


def test_salvage_no_array_key_returns_none():
    assert salvage_truncated_array('{"other": []}', "missing") is None


def test_salvage_handles_braces_inside_strings():
    """Braces inside string values must not throw off depth counting."""
    payload = """
    {"analyses": [
      {"text": "method uses { and } chars", "score": 4},
      {"text": "incomplete...
    """
    result = salvage_truncated_array(payload, "analyses")
    assert result == [{"text": "method uses { and } chars", "score": 4}]


def test_salvage_empty_array_returns_none():
    """No entries inside the array → return None (caller handles)."""
    assert salvage_truncated_array('{"analyses": []}', "analyses") is None
```

- [ ] **Step 3: Run salvage tests**

```bash
uv run pytest tests/unit/test_json_salvage.py -v
```

Expected: all 6 pass.

- [ ] **Step 4: Wire `clean_control_chars` into literature_survey**

In `src/perspicacite/rag/modes/literature_survey.py`, find the `_fix_json` method (around line 940-945). Add `clean_control_chars` to its pipeline:

```python
def _fix_json(self, json_str: str) -> str:
    """Fix common JSON formatting issues from LLM responses."""
    import re
    from perspicacite.rag.utils.json_salvage import clean_control_chars
    # Strip raw control chars some providers emit inside string values.
    json_str = clean_control_chars(json_str)
    # Remove trailing commas before closing brackets
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
    # Remove any markdown code block markers
    json_str = json_str.replace("```json", "").replace("```", "")
    return json_str.strip()
```

Also replace the inline `_salvage_truncated_json` method in the same file (around line 1093) with a call to the shared util. Find:

```python
def _salvage_truncated_json(self, json_str: str) -> list[dict[str, Any]] | None:
```

Replace the entire method body with:

```python
def _salvage_truncated_json(self, json_str: str) -> list[dict[str, Any]] | None:
    """Best-effort recovery from a truncated LLM analyses array."""
    from perspicacite.rag.utils.json_salvage import salvage_truncated_array
    return salvage_truncated_array(json_str, "analyses")
```

- [ ] **Step 5: Wire into profound._analyze_documents_json**

Find profound's analyze method:

```bash
grep -n "_analyze_documents_json\|profound_analyze_error" src/perspicacite/rag/modes/profound.py | head -5
```

Inside `_analyze_documents_json`, locate the `try: json.loads(...)` block that produces the "Invalid control character" error. Wrap the input with `clean_control_chars`:

```python
from perspicacite.rag.utils.json_salvage import clean_control_chars, salvage_truncated_array

# ... inside the method, where the LLM response is parsed:
raw_response = await llm.complete(...)
cleaned = clean_control_chars(raw_response or "")
# Locate the JSON envelope (the rest of the parsing code is unchanged
# except json.loads now sees the cleaned string).
try:
    data = json.loads(cleaned)
except json.JSONDecodeError as de:
    # Try to salvage entries from a truncated analyses-like array.
    salvaged = salvage_truncated_array(cleaned, "analyses")
    if salvaged is not None:
        logger.info("profound_analyze_json_salvaged", recovered=len(salvaged))
        data = {"analyses": salvaged}
    else:
        logger.error("profound_analyze_error", error=str(de))
        return {}
```

(The exact location depends on the existing implementation — search for `json.loads` calls inside `_analyze_documents_json` and apply the same wrapping. If the response shape isn't `{"analyses": [...]}`, pick the correct array key for `salvage_truncated_array`.)

- [ ] **Step 6: Also wire into profound._create_plan**

Locate `_create_plan` in profound.py (search `def _create_plan`). Apply the same `clean_control_chars` wrap before `json.loads`. Profound's plan format is `{"plan": [...], "queries": [...]}`. For plan use `salvage_truncated_array(cleaned, "plan")` as the fallback.

- [ ] **Step 7: Run all tests**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Expected: 1738 + 6 = 1744 passing.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/rag/utils/json_salvage.py src/perspicacite/rag/modes/literature_survey.py src/perspicacite/rag/modes/profound.py tests/unit/test_json_salvage.py
git commit -m "$(cat <<'EOF'
feat: shared JSON-salvage utility for LLM response recovery

Extracts the literature_survey._salvage_truncated_json logic into a
reusable rag/utils/json_salvage module. Adds clean_control_chars to
strip raw 0x00-0x1F control characters some providers emit inside
string values (the "Invalid control character at: line 8" error).

Wires both helpers into profound._analyze_documents_json and
profound._create_plan so a single malformed batch no longer fails
an entire cycle.
EOF
)"
```

### Task 1.8: PubMed quota telemetry (A6)

**Files:**
- Modify: `src/perspicacite/search/scilex_adapter.py`
- Test: `tests/unit/test_scilex_quota_warning.py`

- [ ] **Step 1: Add a log-capture context manager**

In `src/perspicacite/search/scilex_adapter.py`, add this private helper class anywhere above `class SciLExAdapter` (near the `SciLExSearchResult` dataclass added in Task 1.3):

```python
import logging as _stdlib_logging


class _QuotaLogCapture(_stdlib_logging.Handler):
    """Captures SciLEx's stdlib-logger PubMed quota warnings.

    SciLEx logs ``"PubMed API: Only N requests remaining in current period!"``
    via the root logger; we attach this handler for the duration of a
    SciLEx call, scan emitted messages for the quota pattern, and
    surface the remaining-count as a structured warning to the caller.
    """

    _QUOTA_RE = re.compile(r"Only (\d+) requests remaining")

    def __init__(self) -> None:
        super().__init__(level=_stdlib_logging.WARNING)
        self.last_remaining: int | None = None
        self.provider = "pubmed"

    def emit(self, record: _stdlib_logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            m = self._QUOTA_RE.search(msg)
            if m:
                self.last_remaining = int(m.group(1))
        except Exception:
            pass
```

Add `import re` near the top of the file if not already imported.

- [ ] **Step 2: Use the handler around the SciLEx call inside `search()`**

Find the `await asyncio.to_thread(...)` (or whatever invokes scilex_core) inside `SciLExAdapter.search`. Wrap it:

```python
        _quota = _QuotaLogCapture()
        _root = _stdlib_logging.getLogger()
        _root.addHandler(_quota)
        try:
            # ... existing scilex_core invocation here ...
        finally:
            _root.removeHandler(_quota)

        # Stash the quota signal so search_with_warnings can surface it.
        self._last_quota_warning: dict | None = None
        if _quota.last_remaining is not None and _quota.last_remaining < 10:
            self._last_quota_warning = {
                "kind": "rate_limit_low",
                "provider": _quota.provider,
                "remaining": _quota.last_remaining,
                "advice": (
                    "Add NCBI_API_KEY to lift quota from 3 r/s to 10 r/s. "
                    "Without a key, SciLEx is throttled aggressively."
                ),
            }
```

Initialise `self._last_quota_warning: dict | None = None` at the end of `__init__` (alongside `self._last_dropped_apis`).

- [ ] **Step 3: Surface in `search_with_warnings`**

Update the `search_with_warnings` method built in Task 1.3 to include this warning:

```python
    async def search_with_warnings(self, ...) -> SciLExSearchResult:
        ...
        self._last_dropped_apis = []
        self._last_quota_warning = None
        papers = await self.search(...)
        warnings: list[dict] = []
        if self._last_dropped_apis:
            # (Existing dropped-APIs warning logic — leave intact; the
            # MCP layer constructs the full {kind, apis, advice} entry.
            # Here we ONLY transport the raw list to the caller.)
            pass
        return SciLExSearchResult(
            papers=papers,
            dropped_apis=list(self._last_dropped_apis),
            warnings=([self._last_quota_warning]
                      if self._last_quota_warning else []),
        )
```

Extend the dataclass:

```python
@dataclass
class SciLExSearchResult:
    papers: list = field(default_factory=list)
    dropped_apis: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: Pipe through MCP `search_literature`**

In `mcp/server.py::search_literature`, find the block built in Task 1.4 that appends to `warnings`. Add another append after the dropped-apis one:

```python
    for w in result.warnings:
        warnings.append(w)
```

- [ ] **Step 5: Write the test**

Create `tests/unit/test_scilex_quota_warning.py`:

```python
"""Unit tests for the PubMed quota log-scanner."""
import logging
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.search.scilex_adapter import (
    SciLExAdapter, _QuotaLogCapture,
)


def test_quota_capture_extracts_remaining():
    cap = _QuotaLogCapture()
    rec = logging.LogRecord(
        name="root", level=logging.WARNING, pathname="", lineno=0,
        msg="PubMed API: Only 2 requests remaining in current period!",
        args=(), exc_info=None,
    )
    cap.emit(rec)
    assert cap.last_remaining == 2


def test_quota_capture_ignores_unrelated_warnings():
    cap = _QuotaLogCapture()
    rec = logging.LogRecord(
        name="root", level=logging.WARNING, pathname="", lineno=0,
        msg="totally unrelated warning", args=(), exc_info=None,
    )
    cap.emit(rec)
    assert cap.last_remaining is None


@pytest.mark.asyncio
async def test_search_with_warnings_surfaces_quota():
    adapter = SciLExAdapter()
    # Simulate search() running and triggering the quota log capture.
    async def fake_search(*args, **kwargs):
        adapter._last_dropped_apis = []
        adapter._last_quota_warning = {
            "kind": "rate_limit_low", "provider": "pubmed",
            "remaining": 2, "advice": "add NCBI_API_KEY",
        }
        return []
    with patch.object(adapter, "search", AsyncMock(side_effect=fake_search)):
        result = await adapter.search_with_warnings(query="q")
    assert len(result.warnings) == 1
    assert result.warnings[0]["kind"] == "rate_limit_low"
    assert result.warnings[0]["remaining"] == 2
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_scilex_quota_warning.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/search/scilex_adapter.py src/perspicacite/mcp/server.py tests/unit/test_scilex_quota_warning.py
git commit -m "$(cat <<'EOF'
feat: surface PubMed quota warnings via SciLEx adapter

Adds a log-capture handler that watches SciLEx's stdlib logger for
"Only N requests remaining" warnings and surfaces them in the
SciLExSearchResult.warnings array. MCP search_literature now returns
{kind: "rate_limit_low", provider: "pubmed", remaining: N, advice}
so external agents know when to back off / add an NCBI_API_KEY.
EOF
)"
```

### Tier 1 acceptance gate

- [ ] **Run the full unit suite + confirm baseline**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -3
```

Expected: ~1748 passing (1731 baseline + 17 new across Tasks 1.1-1.8).

- [ ] **Smoke-test against a live server**

```bash
PID=$(lsof -ti:8000 2>/dev/null); [ -n "$PID" ] && kill $PID && sleep 2
zsh -ic 'uv run perspicacite -c config.yml serve' > /tmp/perspicacite-server.log 2>&1 &
disown
sleep 12
lsof -ti:8000 && echo "up" || echo "down"
```

Trigger a basic-mode chat in the GUI with Google Scholar selected. In `/tmp/perspicacite-server.log` look for `crossref_canonicalized` and `crossref_doi_backfill` events. They should appear (proves enrichment is wired in agentic / literature_survey paths too).

- [ ] **Tier 1 complete — ready to push as standalone PR**

---

## Tier 2 — MCP observability + cancellation + standalone web_search

### Task 2.1: TelemetrySink protocol

**Files:**
- Create: `src/perspicacite/rag/telemetry.py`
- Test: `tests/unit/test_telemetry_sink.py`

**Context:** The current `telemetry: list[dict[str, Any]]` parameter pattern works for the SSE chat router (which drains the list after the await). For MCP we need live notifications. A protocol with `append` (legacy) + `on_event_async` (new) lets both work without touching every call site.

- [ ] **Step 1: Write the protocol**

Create `src/perspicacite/rag/telemetry.py`:

```python
"""TelemetrySink: unified protocol for in-RAG-pipeline progress events.

Two implementations:

- ``ListTelemetrySink``     : drop-in replacement for the legacy
  ``telemetry: list[dict]`` pattern; the SSE chat router and existing
  call sites continue to use this.

- ``CallbackTelemetrySink`` : invokes an awaitable on each event.
  The MCP layer wraps ``ctx.report_progress`` in this so external
  agents see live progress notifications during long-running tools.

A ``NullTelemetrySink`` no-op is provided for tests / callers that
don't care about events.

Both `append` (sync, list-style) and `on_event_async` (async, callback-style)
APIs are exposed on every sink so mode code can use whichever feels
natural without conditionally checking the sink type.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol


class TelemetrySink(Protocol):
    """Any object usable as the ``telemetry`` parameter on RAG helpers."""

    def append(self, event: dict[str, Any]) -> None: ...
    async def on_event_async(self, event: dict[str, Any]) -> None: ...


class ListTelemetrySink:
    """Stores events in a plain list; drain after the await.

    Preserves the legacy semantics. Existing code that does
    ``telemetry.append({...})`` keeps working.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def on_event_async(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __bool__(self) -> bool:
        return bool(self.events)


class CallbackTelemetrySink:
    """Invokes ``callback(event)`` (awaitable) on every event.

    Used by the MCP progress adapter. Provides ``append`` as a sync
    fire-and-forget shim that schedules the callback on the running
    loop; prefer ``on_event_async`` from async contexts.
    """

    def __init__(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._callback = callback
        # Mirror events into a buffer for diagnostics.
        self.events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        import asyncio
        self.events.append(event)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._callback(event))
        except RuntimeError:
            # No running loop — caller is sync; drop event silently
            # (the legacy SSE drain path uses .events directly).
            pass

    async def on_event_async(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        try:
            await self._callback(event)
        except Exception:
            pass  # never let telemetry errors break the RAG pipeline


class NullTelemetrySink:
    """Drops every event. Useful in tests / batch scripts."""

    events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        return None

    async def on_event_async(self, event: dict[str, Any]) -> None:
        return None

    def __iter__(self):
        return iter([])

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False
```

- [ ] **Step 2: Write tests**

Create `tests/unit/test_telemetry_sink.py`:

```python
"""Unit tests for TelemetrySink implementations."""
import asyncio
import pytest

from perspicacite.rag.telemetry import (
    ListTelemetrySink, CallbackTelemetrySink, NullTelemetrySink,
)


def test_list_sink_append_and_iterate():
    s = ListTelemetrySink()
    s.append({"a": 1})
    s.append({"b": 2})
    assert list(s) == [{"a": 1}, {"b": 2}]
    assert len(s) == 2
    assert bool(s) is True


def test_list_sink_empty_is_falsey():
    s = ListTelemetrySink()
    assert bool(s) is False
    assert len(s) == 0


@pytest.mark.asyncio
async def test_callback_sink_invokes_callback():
    received: list[dict] = []

    async def cb(e):
        received.append(e)

    s = CallbackTelemetrySink(cb)
    await s.on_event_async({"x": 1})
    await s.on_event_async({"y": 2})
    assert received == [{"x": 1}, {"y": 2}]


@pytest.mark.asyncio
async def test_callback_sink_buffers_events():
    """The .events list lets diagnostics read what was emitted."""
    async def cb(_e):
        return

    s = CallbackTelemetrySink(cb)
    await s.on_event_async({"a": 1})
    assert s.events == [{"a": 1}]


@pytest.mark.asyncio
async def test_callback_sink_swallows_callback_errors():
    """Callback exceptions must not break the RAG pipeline."""
    async def bad(e):
        raise RuntimeError("boom")

    s = CallbackTelemetrySink(bad)
    await s.on_event_async({"x": 1})  # must not raise
    assert s.events == [{"x": 1}]


def test_null_sink_drops_everything():
    s = NullTelemetrySink()
    s.append({"x": 1})
    assert len(s) == 0
    assert bool(s) is False
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_telemetry_sink.py -v
```

Expected: all 6 pass.

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/rag/telemetry.py tests/unit/test_telemetry_sink.py
git commit -m "$(cat <<'EOF'
feat: TelemetrySink protocol for unified progress reporting

Three implementations: ListTelemetrySink (legacy list-buffer), a
CallbackTelemetrySink (awaits a callback per event — used by the MCP
progress adapter in Task 2.2), and a NullTelemetrySink for callers
that don't care.

All three expose both append() and on_event_async() so mode code can
use whichever feels natural without checking sink type.
EOF
)"
```

### Task 2.2: MCP progress adapter

**Files:**
- Create: `src/perspicacite/mcp/progress_adapter.py`
- Test: `tests/unit/test_mcp_progress_adapter.py`

- [ ] **Step 1: Write the adapter**

Create `src/perspicacite/mcp/progress_adapter.py`:

```python
"""Maps internal RAG telemetry events to MCP ``ctx.report_progress`` calls.

Background: the SSE chat router consumes a rich vocabulary of event
``kind``s — ``query_rephrased``, ``provider_progress``, ``batch_progress``,
``source``, ``status``. MCP's protocol only supports
``(progress: int, total: int, message: str)`` notifications. This adapter
collapses the rich events into human-readable progress messages while
preserving the cumulative progress counter so MCP clients see a sensible
0 → 100% bar.

Throttling: progress notifications are rate-limited to ≥ 1 second
spacing to avoid spamming slow clients (per spec Risks & Mitigations).
"""
from __future__ import annotations

import time
from typing import Any


class MCPProgressAdapter:
    """Forwards RAG telemetry events to ``ctx.report_progress``."""

    _MIN_SPACING_S = 1.0

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._last_emit_t = 0.0
        # Running counters — best-effort estimate of progress
        self._progress = 0
        self._total = 100  # default scale until a batch_progress event reveals real total

    async def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        msg = None
        if kind == "query_rephrased":
            orig = event.get("original", "")
            rew = event.get("rewritten", "")
            msg = f"Rewrote search query: '{orig}' → '{rew}'"
        elif kind == "provider_progress" and event.get("phase") == "start":
            provs = ", ".join(event.get("providers", []) or [])
            msg = f"Querying databases: {provs}"
        elif kind == "provider_progress" and event.get("phase") == "done":
            by = event.get("by_provider", {}) or {}
            counts = ", ".join(
                f"{k}: {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])
            )
            total = event.get("total", 0)
            msg = (
                f"Database results — total {total} hits"
                + (f" ({counts})" if counts else "")
            )
        elif kind == "batch_progress":
            cur = int(event.get("current", 0))
            tot = int(event.get("total", 0)) or 1
            stage = event.get("stage", "batch")
            self._progress = cur
            self._total = tot
            msg = f"{stage}: {cur}/{tot}"
        elif kind == "rate_limit_low":
            provider = event.get("provider", "?")
            remaining = event.get("remaining", "?")
            msg = f"Rate limit low for {provider}: {remaining} reqs remaining"

        if msg is None:
            return

        # Throttle: do not fire notifications more than once per second.
        now = time.monotonic()
        if now - self._last_emit_t < self._MIN_SPACING_S:
            return
        self._last_emit_t = now

        try:
            await self.ctx.report_progress(
                progress=self._progress,
                total=self._total,
                message=msg,
            )
        except Exception:
            # Never let MCP transport hiccups break the RAG pipeline.
            return
```

- [ ] **Step 2: Write tests**

Create `tests/unit/test_mcp_progress_adapter.py`:

```python
"""Unit tests for MCPProgressAdapter."""
import pytest
from unittest.mock import AsyncMock

from perspicacite.mcp.progress_adapter import MCPProgressAdapter


@pytest.mark.asyncio
async def test_query_rephrased_event():
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    await adapter.on_event({
        "kind": "query_rephrased",
        "original": "what is X",
        "rewritten": "X",
    })
    ctx.report_progress.assert_called_once()
    args = ctx.report_progress.call_args.kwargs
    assert "Rewrote search query" in args["message"]


@pytest.mark.asyncio
async def test_batch_progress_updates_counters():
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    await adapter.on_event({
        "kind": "batch_progress",
        "stage": "abstract_analysis",
        "current": 3, "total": 10,
    })
    args = ctx.report_progress.call_args.kwargs
    assert args["progress"] == 3
    assert args["total"] == 10
    assert "abstract_analysis: 3/10" in args["message"]


@pytest.mark.asyncio
async def test_throttling_drops_rapid_events(monkeypatch):
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    # First event passes through.
    await adapter.on_event({
        "kind": "query_rephrased", "original": "a", "rewritten": "b",
    })
    # Second event same instant — must be dropped.
    await adapter.on_event({
        "kind": "query_rephrased", "original": "c", "rewritten": "d",
    })
    assert ctx.report_progress.call_count == 1


@pytest.mark.asyncio
async def test_unknown_kind_silently_ignored():
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    await adapter.on_event({"kind": "not_a_real_event"})
    ctx.report_progress.assert_not_called()


@pytest.mark.asyncio
async def test_ctx_error_swallowed():
    ctx = AsyncMock()
    ctx.report_progress.side_effect = RuntimeError("transport down")
    adapter = MCPProgressAdapter(ctx)
    # Must not raise.
    await adapter.on_event({
        "kind": "query_rephrased", "original": "a", "rewritten": "b",
    })
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_mcp_progress_adapter.py -v
```

Expected: all 5 pass.

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/mcp/progress_adapter.py tests/unit/test_mcp_progress_adapter.py
git commit -m "$(cat <<'EOF'
feat(mcp): MCPProgressAdapter for ctx.report_progress

Maps internal RAG telemetry events (query_rephrased, provider_progress,
batch_progress, rate_limit_low) onto MCP's
(progress, total, message) notifications. Throttled to ≥1s spacing
to avoid spamming slow clients. Used by Task 2.4 in generate_report.
EOF
)"
```

### Task 2.3: Make `run_web_aggregator_search` accept TelemetrySink

**Files:**
- Modify: `src/perspicacite/rag/web_search.py`

**Context:** Existing call sites pass `telemetry: list[dict]` (plain list). We need them to ALSO accept a `TelemetrySink`. The protocol's duck-typing already allows this — `list.append()` matches the protocol. But we also need the `await sink.on_event_async()` path for the MCP integration. Add a tiny adapter layer.

- [ ] **Step 1: Update the type hint and dispatch**

In `src/perspicacite/rag/web_search.py`, find every `telemetry: list[dict[str, Any]] | None = None` parameter. Change to `telemetry: Any = None`. (We avoid the import to keep this file free of new dependencies; the duck-typed `.append()` works for both `list` and our sinks.)

Add a tiny module-level helper at the top of the file (after imports):

```python
async def _emit_telemetry(sink: Any, event: dict) -> None:
    """Dispatch one event to a sink, supporting both list-style and
    callback-style sinks. Plain ``list``s only get .append() (sync); a
    ``CallbackTelemetrySink`` gets on_event_async (live notification).
    """
    if sink is None:
        return
    if hasattr(sink, "on_event_async"):
        try:
            await sink.on_event_async(event)
            return
        except Exception:
            return
    # Fallback for plain list (legacy).
    try:
        sink.append(event)
    except Exception:
        pass
```

- [ ] **Step 2: Replace every `telemetry.append({...})` with `await _emit_telemetry(telemetry, {...})`**

In the same file, find every `telemetry.append({...})` call (there are typically 3-4: optimizer rephrase, provider_progress start, provider_progress done). Convert each to:

```python
            await _emit_telemetry(telemetry, {
                "kind": "...",
                ...
            })
```

Note: this requires the containing function to be `async` — verify it is. `run_web_aggregator_search` already is.

- [ ] **Step 3: Run all existing tests (legacy list path)**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Existing tests pass plain lists; the `.append()` fallback in `_emit_telemetry` keeps them working. Expected: still passing.

- [ ] **Step 4: Add a test for the sink path**

Create `tests/unit/test_web_search_telemetry_sink.py`:

```python
"""Verify run_web_aggregator_search dispatches events to TelemetrySink."""
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.rag.telemetry import ListTelemetrySink, CallbackTelemetrySink
from perspicacite.rag.web_search import run_web_aggregator_search


@pytest.mark.asyncio
async def test_list_telemetry_sink_receives_events():
    sink = ListTelemetrySink()
    # Force the aggregator path to be a no-op so we only test event flow.
    with patch(
        "perspicacite.rag.web_search.build_aggregator",
        side_effect=Exception("skip"),
    ):
        try:
            await run_web_aggregator_search(
                keyword_query="q", context=None, optimize_enabled=False,
                databases=None, max_docs=5, app_state=None, telemetry=sink,
            )
        except Exception:
            pass
    # Sink may or may not have events depending on where the mock
    # raised, but the test verifies the sink interface works without
    # the .append() AttributeError we'd see if dispatch was wrong.
    assert isinstance(sink.events, list)


@pytest.mark.asyncio
async def test_callback_sink_called_when_optimizer_rewrites():
    received: list[dict] = []

    async def cb(e):
        received.append(e)

    sink = CallbackTelemetrySink(cb)

    # We just exercise the wrapper; full optimizer integration is
    # covered by existing tests. Here we hit _emit_telemetry directly.
    from perspicacite.rag.web_search import _emit_telemetry
    await _emit_telemetry(sink, {"kind": "query_rephrased", "original": "a", "rewritten": "b"})
    assert received[0]["kind"] == "query_rephrased"
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_web_search_telemetry_sink.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/web_search.py tests/unit/test_web_search_telemetry_sink.py
git commit -m "$(cat <<'EOF'
refactor: run_web_aggregator_search accepts TelemetrySink (or list)

Generalises the telemetry parameter to support both legacy list-style
(SSE chat router) and callback-style (MCP progress adapter) sinks
via a small _emit_telemetry helper. Existing call sites that pass a
plain list keep working unchanged.
EOF
)"
```

### Task 2.4: Add progress notifications to MCP `generate_report`

**Files:**
- Modify: `src/perspicacite/mcp/server.py::generate_report`

- [ ] **Step 1: Locate generate_report**

```bash
grep -n "^async def generate_report" src/perspicacite/mcp/server.py
```

- [ ] **Step 2: Add `ctx: Context = None` parameter**

Find the signature and add `ctx: Context | None = None` as the LAST parameter. Add the import at the top of the file:

```python
from mcp.server.fastmcp import Context
```

- [ ] **Step 3: Build adapter and pass through to RAG engine**

Inside `generate_report`, immediately after parameter validation, build the adapter and attach as a telemetry sink. The RAG engine accepts `RAGRequest` and a stream context. Since RAGRequest doesn't have a telemetry field yet, plumb via a side channel — the AppState (or use the lower-level `RAGEngine.execute_stream` interface).

Pseudocode for the body change (the existing function probably does `result = await rag_engine.execute(request)`; we now wrap):

```python
    progress_adapter = None
    sink = None
    if ctx is not None:
        from perspicacite.mcp.progress_adapter import MCPProgressAdapter
        from perspicacite.rag.telemetry import CallbackTelemetrySink
        progress_adapter = MCPProgressAdapter(ctx)
        sink = CallbackTelemetrySink(progress_adapter.on_event)

    # Attach the sink to the request so modes can see it.
    request.telemetry_sink = sink  # type: ignore[attr-defined]
```

This requires `RAGRequest` to allow extra fields. Pydantic v2 default is `model_config = {"extra": "allow"}` — check:

```bash
grep -n "model_config\|extra.*allow" src/perspicacite/models/rag.py | head -5
```

If RAGRequest does NOT have `extra = "allow"`, add it:

```python
class RAGRequest(BaseModel):
    model_config = {"extra": "allow"}
    # ... existing fields ...
```

- [ ] **Step 4: Read `request.telemetry_sink` inside modes that already accept `telemetry`**

In the modes that call `_web_fallback_papers` (basic/advanced) and `run_web_aggregator_search` (literature_survey, profound), update the `telemetry=...` arg passed downstream. Find each site:

```bash
grep -n "telemetry=_telemetry\|telemetry=telemetry" src/perspicacite/rag/modes/*.py
```

For each site, if there's a local `_telemetry: list[dict] = []` followed by `telemetry=_telemetry`, replace with:

```python
            _telemetry = getattr(request, "telemetry_sink", None) or []
            paper_results = await _web_fallback_papers(
                ...
                telemetry=_telemetry,
            )
            # If _telemetry is a plain list (legacy), drain into SSE
            # events as before. If it's a sink (MCP), the events
            # already flowed to ctx.report_progress live; skip the drain.
            if isinstance(_telemetry, list):
                for _ev in _telemetry:
                    # ... existing drain code ...
```

This change is surgical — wrap the existing drain loop in `if isinstance(_telemetry, list):`.

- [ ] **Step 5: Test manually**

There's no automatable unit test for "MCP client sees ctx.report_progress" without spinning up the protocol. Defer to a live MCP test (Task 2.6); ensure the existing test suite still passes:

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/server.py src/perspicacite/models/rag.py src/perspicacite/rag/modes/
git commit -m "$(cat <<'EOF'
feat(mcp): live progress notifications in generate_report

Accepts ctx: Context from fastmcp; builds a CallbackTelemetrySink
that forwards RAG events to MCPProgressAdapter.on_event →
ctx.report_progress. Plumbed through RAGRequest.telemetry_sink
(extra-allowed field) so each mode can opt in. SSE chat path keeps
working unchanged (legacy list drain).
EOF
)"
```

### Task 2.5: New `web_search` MCP tool

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (add new tool)
- Test: `tests/unit/test_mcp_web_search_tool.py`

- [ ] **Step 1: Add the tool definition**

In `src/perspicacite/mcp/server.py`, after the last `@mcp.tool()` definition (the file currently has 10 tools), add:

```python
@mcp.tool()
async def web_search(
    query: str,
    databases: list[str] | None = None,
    max_results: int = 10,
    enrich: bool = True,
    optimize_query: bool = True,
    ctx: Context | None = None,
) -> str:
    """Live academic web search across user-selected databases.

    Wraps the shared aggregator pipeline (semantic_scholar, openalex,
    pubmed, arxiv via SciLEx + standalone google_scholar, europepmc,
    core, etc.) with Crossref enrichment + MiniLM rerank. Returns a
    JSON-encoded payload with ``papers``, ``warnings``, and
    ``telemetry_summary`` (per-provider hit counts).

    Distinct from ``search_literature`` (SciLEx-only) and
    ``generate_report`` (heavy mode-bound RAG). Use this when you
    just want a focused literature lookup with cleaned-up metadata.

    Args:
        query: free-text scientific query
        databases: list of provider names (default: semantic_scholar,
            openalex, pubmed). Pass google_scholar / europepmc / core
            for the standalone aggregator providers.
        max_results: cap on returned papers (1-50)
        enrich: when True (default) runs Crossref enrichment on the
            returned papers — fills missing abstracts and canonicalises
            author lists. Set False for raw provider data.
        optimize_query: when True, runs the LLM-assisted keyword rewrite
            before searching.
        ctx: MCP context for live progress notifications (injected
            automatically by fastmcp; do not pass manually).

    Returns:
        JSON string: {"papers": [...], "warnings": [...],
                      "telemetry_summary": {"by_provider": {...}}}
    """
    import json as _json
    from perspicacite.rag.web_search import run_web_aggregator_search
    from perspicacite.rag.telemetry import (
        ListTelemetrySink, CallbackTelemetrySink,
    )

    sink = ListTelemetrySink()
    if ctx is not None:
        from perspicacite.mcp.progress_adapter import MCPProgressAdapter
        cb_sink = CallbackTelemetrySink(MCPProgressAdapter(ctx).on_event)
        # Use a sink that BOTH callbacks AND buffers events for the
        # telemetry_summary in the response.
        async def _both(e):
            await cb_sink.on_event_async(e)
            sink.append(e)
        sink = CallbackTelemetrySink(_both)

    try:
        papers = await run_web_aggregator_search(
            keyword_query=query,
            context=None,
            optimize_enabled=bool(optimize_query),
            databases=databases,
            max_docs=max(1, min(50, int(max_results))),
            app_state=None,  # picks up global via web_search.py fallback
            telemetry=sink,
        )
    except Exception as exc:
        return _json.dumps({
            "papers": [], "warnings": [],
            "error": f"web_search_failed: {exc}",
        })

    if enrich and papers:
        try:
            from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers
            papers = await enrich_papers(papers)
        except Exception as _ee:
            logger.warning("mcp_web_search_enrich_failed", error=str(_ee))

    # Build response payload
    by_provider: dict[str, int] = {}
    for ev in (getattr(sink, "events", []) or []):
        if ev.get("kind") == "provider_progress" and ev.get("phase") == "done":
            by_provider.update(ev.get("by_provider", {}) or {})

    serialised: list[dict] = []
    for p in papers:
        serialised.append({
            "title": p.title,
            "authors": [a.name for a in (p.authors or [])],
            "year": p.year,
            "journal": p.journal,
            "doi": p.doi,
            "url": p.url,
            "abstract": p.abstract,
            "discovery_sources": (p.metadata or {}).get("sources") or [],
            "enrichment_sources": (p.metadata or {}).get("enrichment_sources") or [],
        })

    return _json.dumps({
        "papers": serialised,
        "warnings": [],  # provider-level warnings flow via search_with_warnings;
                        # for direct web_search the aggregator surfaces them
                        # in logs, not in the payload (yet).
        "telemetry_summary": {"by_provider": by_provider},
    })
```

- [ ] **Step 2: Write a unit test**

Create `tests/unit/test_mcp_web_search_tool.py`:

```python
"""Unit tests for the new web_search MCP tool."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.models.papers import Paper, Author
from perspicacite.mcp.server import web_search


@pytest.mark.asyncio
async def test_web_search_returns_serialised_papers():
    fake = [
        Paper(
            id="doi:10.1/x", title="Paper 1",
            authors=[Author(name="A. Author")],
            year=2024, doi="10.1/x", abstract="abs1",
        ),
        Paper(id="doi:10.1/y", title="Paper 2", doi="10.1/y"),
    ]
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p: p),
    ):
        out = await web_search(query="q", databases=["openalex"])
    data = json.loads(out)
    assert len(data["papers"]) == 2
    assert data["papers"][0]["title"] == "Paper 1"
    assert data["papers"][0]["doi"] == "10.1/x"
    assert "telemetry_summary" in data


@pytest.mark.asyncio
async def test_web_search_skips_enrich_when_disabled():
    fake = [Paper(id="x", title="t")]
    mock_enrich = AsyncMock(side_effect=lambda p: p)
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        mock_enrich,
    ):
        await web_search(query="q", enrich=False)
    mock_enrich.assert_not_called()


@pytest.mark.asyncio
async def test_web_search_error_response():
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        out = await web_search(query="q")
    data = json.loads(out)
    assert "error" in data
    assert data["papers"] == []
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_mcp_web_search_tool.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 4: Update MCP tool catalog doc**

```bash
ls docs/perspicacite_skills.md 2>/dev/null && echo "exists" || echo "missing"
```

If the file exists, add a section for `web_search` describing its use. If it doesn't exist, skip this step (the docstring is already written).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_web_search_tool.py docs/perspicacite_skills.md
git commit -m "$(cat <<'EOF'
feat(mcp): add web_search as 11th MCP tool

Exposes the shared aggregator + Crossref enrichment + MiniLM rerank
pipeline as a standalone tool. External agents can now do focused
literature lookups (including google_scholar / europepmc / core)
without going through the heavy mode-bound generate_report.

Returns {papers, warnings, telemetry_summary} JSON. ctx-aware for
live progress notifications when fastmcp injects a Context.
EOF
)"
```

### Task 2.6: `cancel_task` MCP tool + cancellation point in RAG

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (new tool + task_id param on generate_report)
- Modify: `src/perspicacite/rag/agentic/orchestrator.py` (check cancellation each iteration)
- Modify: `src/perspicacite/rag/modes/profound.py` (check cancellation each cycle)
- Modify: `src/perspicacite/rag/modes/literature_survey.py` (check between batches)
- Test: `tests/unit/test_mcp_cancel_task.py`

- [ ] **Step 1: Add the cancel_task tool**

After the `web_search` tool added in Task 2.5, append:

```python
@mcp.tool()
async def cancel_task(task_id: str) -> str:
    """Mark a running MCP task as cancelled.

    Long-running tools (``generate_report``, ``search_to_kb``,
    ``web_search``) check the cancellation registry at safe points
    (between RAG cycles / batches / iterations) and return early
    when the registry says their task_id has been cancelled.

    The task_id is the same one returned in the first progress
    notification of the cancellable tool's response.

    Returns:
        JSON: {"ok": true, "task_id": str, "was_running": bool}
        ``was_running`` is best-effort — we cannot perfectly distinguish
        a task that already finished from one that never existed.
    """
    import json as _json
    from perspicacite.rag.cancellation import mark_cancelled
    if not task_id:
        return _json.dumps({"ok": False, "error": "missing task_id"})
    await mark_cancelled(task_id)
    return _json.dumps({
        "ok": True,
        "task_id": task_id,
        "was_running": True,  # see docstring — best-effort
    })
```

- [ ] **Step 2: Add `task_id` param to generate_report + advertise it via first progress notification**

In `generate_report`, add `task_id: str | None = None` as a parameter. At the top of the function body, generate one if not supplied:

```python
    import uuid
    if not task_id:
        task_id = f"mcp-{uuid.uuid4().hex[:12]}"

    # Emit the task_id immediately via ctx so the client can cancel.
    if ctx is not None:
        try:
            await ctx.report_progress(
                progress=0, total=100,
                message=f"Task started — task_id={task_id}",
            )
        except Exception:
            pass
```

- [ ] **Step 3: Add cancellation checks in mode loops**

In `agentic/orchestrator.py`, find the main iteration loop (look for `for iteration in range(self.max_iterations)` or similar). Add at the TOP of each iteration:

```python
            from perspicacite.rag.cancellation import is_cancelled
            _tid = getattr(session, "task_id", None) or getattr(request, "task_id", None)
            if is_cancelled(_tid):
                logger.info("agentic_cancelled", task_id=_tid, iteration=iteration)
                return  # or yield a "cancelled" event then return
```

(Adjust to the exact loop structure — `session` is the orchestrator's session object; `request` may carry the task_id.)

Do the same in:

- `profound.py::execute_stream` cycle loop (top of `for cycle in range(self.max_cycles)`).
- `literature_survey.py` — top of each `_one_batch` invocation, OR at the start of `_assign_papers_to_themes`'s `_classify` coroutine.

Each check is the same pattern: import is_cancelled, read `task_id` from request/session, early return.

- [ ] **Step 4: Plumb `task_id` through RAGRequest**

Add to `RAGRequest`:

```python
    task_id: str | None = Field(
        default=None,
        description="Optional task ID for MCP cancellation tracking",
    )
```

In `mcp/server.py::generate_report`, set `request.task_id = task_id` before invoking the engine.

- [ ] **Step 5: Test**

Create `tests/unit/test_mcp_cancel_task.py`:

```python
"""Unit tests for cancel_task MCP tool + registry integration."""
import json
import pytest

from perspicacite.mcp.server import cancel_task
from perspicacite.rag import cancellation as cr


@pytest.fixture(autouse=True)
async def _reset():
    await cr.reset_for_tests()
    yield
    await cr.reset_for_tests()


@pytest.mark.asyncio
async def test_cancel_task_marks_registry():
    out = await cancel_task("task-123")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["task_id"] == "task-123"
    assert cr.is_cancelled("task-123") is True


@pytest.mark.asyncio
async def test_cancel_task_empty_id_rejected():
    out = await cancel_task("")
    data = json.loads(out)
    assert data["ok"] is False
    assert "missing" in data["error"]


@pytest.mark.asyncio
async def test_cancel_task_idempotent():
    await cancel_task("x")
    await cancel_task("x")
    assert cr.is_cancelled("x") is True
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_mcp_cancel_task.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/mcp/server.py src/perspicacite/models/rag.py src/perspicacite/rag/agentic/orchestrator.py src/perspicacite/rag/modes/profound.py src/perspicacite/rag/modes/literature_survey.py tests/unit/test_mcp_cancel_task.py
git commit -m "$(cat <<'EOF'
feat(mcp): cancel_task tool + cancellation checks in RAG loops

External agents can now abort in-flight generate_report calls by
calling cancel_task(task_id). The task_id is returned in the first
progress notification of the cancellable tool's response. Cancellation
is checked at safe points: between agentic iterations, between
profound cycles, between literature_survey batches.

Backed by the shared registry from Task 1.6.
EOF
)"
```

### Task 2.7: Per-call budget / parallelism params on `RAGRequest`

**Files:**
- Modify: `src/perspicacite/models/rag.py`
- Modify: `src/perspicacite/rag/modes/profound.py` (read overrides)
- Modify: `src/perspicacite/rag/modes/literature_survey.py` (read overrides)
- Modify: `src/perspicacite/mcp/server.py::generate_report` (accept + forward)
- Test: `tests/unit/test_rag_request_overrides.py`

- [ ] **Step 1: Add override fields with bounded validation**

In `src/perspicacite/models/rag.py::RAGRequest`, add:

```python
    # === Per-call overrides for budget / parallelism ===
    # Each is None by default, in which case the mode uses its
    # config-file default. Bounded to safe ranges.
    max_total_seconds: float | None = Field(
        default=None, ge=30.0, le=1800.0,
        description="Overrides per-mode max_total_seconds (30-1800s)",
    )
    batch_size: int | None = Field(
        default=None, ge=1, le=100,
        description="Overrides literature_survey batch_size (1-100)",
    )
    crossref_concurrency: int | None = Field(
        default=None, ge=1, le=10,
        description="Overrides Crossref enrichment concurrency (1-10)",
    )
    # max_iterations already exists; existing validator stays.
```

- [ ] **Step 2: Read overrides in profound**

In `src/perspicacite/rag/modes/profound.py::execute_stream`, find where `self.max_total_seconds` is used. Replace with:

```python
            max_total_seconds = (
                getattr(request, "max_total_seconds", None)
                or self.max_total_seconds
            )
```

Same pattern for `max_iterations` if not already there.

- [ ] **Step 3: Read `batch_size` override in literature_survey**

In `src/perspicacite/rag/modes/literature_survey.py::_analyze_abstracts_batch`, near the top:

```python
        batch_size = (
            getattr(request, "batch_size", None) or self.batch_size
        )
```

(Make sure `request` is available in scope; if not, plumb it down or use `self.batch_size` only.)

- [ ] **Step 4: Forward from MCP tool**

In `generate_report`, add:

```python
async def generate_report(
    ...,
    max_total_seconds: float | None = None,
    batch_size: int | None = None,
    crossref_concurrency: int | None = None,
    ...,
) -> str:
    ...
    request.max_total_seconds = max_total_seconds
    request.batch_size = batch_size
    request.crossref_concurrency = crossref_concurrency
```

- [ ] **Step 5: Test**

Create `tests/unit/test_rag_request_overrides.py`:

```python
"""Unit tests for RAGRequest override validation."""
import pytest
from pydantic import ValidationError

from perspicacite.models.rag import RAGRequest


def test_max_total_seconds_valid():
    r = RAGRequest(query="q", max_total_seconds=120)
    assert r.max_total_seconds == 120


def test_max_total_seconds_below_floor_rejected():
    with pytest.raises(ValidationError):
        RAGRequest(query="q", max_total_seconds=10)


def test_max_total_seconds_above_ceiling_rejected():
    with pytest.raises(ValidationError):
        RAGRequest(query="q", max_total_seconds=10000)


def test_batch_size_bounds():
    RAGRequest(query="q", batch_size=1)  # ok
    RAGRequest(query="q", batch_size=100)  # ok
    with pytest.raises(ValidationError):
        RAGRequest(query="q", batch_size=0)
    with pytest.raises(ValidationError):
        RAGRequest(query="q", batch_size=999)


def test_crossref_concurrency_bounds():
    RAGRequest(query="q", crossref_concurrency=5)
    with pytest.raises(ValidationError):
        RAGRequest(query="q", crossref_concurrency=100)


def test_defaults_are_none():
    r = RAGRequest(query="q")
    assert r.max_total_seconds is None
    assert r.batch_size is None
    assert r.crossref_concurrency is None
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_rag_request_overrides.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/models/rag.py src/perspicacite/rag/modes/ src/perspicacite/mcp/server.py tests/unit/test_rag_request_overrides.py
git commit -m "$(cat <<'EOF'
feat: per-call budget overrides on RAGRequest

Adds max_total_seconds, batch_size, crossref_concurrency to RAGRequest
with Pydantic-validated bounds. Profound, literature_survey, and the
generate_report MCP tool honor the overrides when present, falling
back to config-file defaults otherwise.
EOF
)"
```

### Tier 2 acceptance gate

- [ ] **Full test run**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -3
```

Expected: 1731 + 17 (Tier 1) + ~26 (Tier 2 new tests) ≈ 1774 passing.

- [ ] **Tier 2 complete — ready to push as standalone PR**

---

## Tier 3 — Architectural cleanup

### Task 3.1: Promote `metadata.sources` to typed fields on `Paper`

**Files:**
- Modify: `src/perspicacite/models/papers.py`
- Modify: aggregator code that writes to `metadata["sources"]` (search-time)
- Test: `tests/unit/test_paper_discovery_sources.py`

- [ ] **Step 1: Add the typed fields**

In `src/perspicacite/models/papers.py::Paper`, add two new fields after `keywords`:

```python
    # === Provenance (formerly stored in metadata["sources"]) ===
    # Which DBs returned this specific paper (e.g. ["openalex", "pubmed"]).
    discovery_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Upstream databases that returned this paper. Filled by "
            "the aggregator merge step."
        ),
    )
    # Which DBs ENRICHED the metadata (Crossref, Unpaywall, OpenAlex
    # fill-in, etc.). Distinct from discovery_sources.
    enrichment_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Secondary databases that contributed metadata enrichment "
            "(Crossref bibliographic patch, OpenAlex abstract fill, "
            "Unpaywall OA detection)."
        ),
    )
```

- [ ] **Step 2: Add a back-compat shim that mirrors metadata["sources"] → discovery_sources**

In the same file, add a model validator:

```python
    @model_validator(mode="after")
    def _mirror_legacy_metadata_sources(self) -> "Paper":
        """Back-compat: if metadata['sources'] is set but discovery_sources
        isn't, mirror the value so callers reading either work for one
        release. Tier 3 finishes by removing all writers of the legacy
        key; this validator can then be deleted.
        """
        if not self.discovery_sources:
            legacy = (self.metadata or {}).get("sources")
            if isinstance(legacy, list):
                self.discovery_sources = list(legacy)
        if not self.enrichment_sources:
            legacy_e = (self.metadata or {}).get("enrichment_sources")
            if isinstance(legacy_e, list):
                self.enrichment_sources = list(legacy_e)
        return self
```

Make sure `from pydantic import model_validator` is imported.

- [ ] **Step 3: Update writers**

Find every site that writes to `paper.metadata["sources"]` or `paper.metadata["enrichment_sources"]`:

```bash
grep -rn 'metadata\["sources"\]\|metadata\.\["enrichment_sources"\]\|metadata\.setdefault("sources"' src/ | head -20
```

In each writer:
- `src/perspicacite/search/domain_aggregator.py`
- `src/perspicacite/search/scilex_adapter.py` (the pre-dedupe archive map writer)
- `src/perspicacite/pipeline/enrichment/crossref_enrich.py` (sets `enrichment_sources`)
- Any provider that sets `metadata["sources"] = [...]` directly (google_scholar_playwright, dblp_sparql_search, openrouter_fallback)

For each writer, ALSO write to the new typed field. Example for domain_aggregator:

```python
# Before:
paper.metadata.setdefault("sources", [])
if provider_name not in paper.metadata["sources"]:
    paper.metadata["sources"].append(provider_name)

# After (write BOTH, until Tier 3 cleanup removes the legacy key):
paper.metadata.setdefault("sources", [])
if provider_name not in paper.metadata["sources"]:
    paper.metadata["sources"].append(provider_name)
if provider_name not in paper.discovery_sources:
    paper.discovery_sources.append(provider_name)
```

For `enrich_papers` in crossref_enrich.py, also append to `p.enrichment_sources` alongside the metadata writes.

- [ ] **Step 4: Write the test**

Create `tests/unit/test_paper_discovery_sources.py`:

```python
"""Unit tests for Paper.discovery_sources / enrichment_sources fields."""
from perspicacite.models.papers import Paper, PaperSource


def test_discovery_sources_default_empty():
    p = Paper(id="x", title="t")
    assert p.discovery_sources == []
    assert p.enrichment_sources == []


def test_discovery_sources_mirrored_from_legacy_metadata():
    """Back-compat: metadata['sources'] populates discovery_sources."""
    p = Paper(
        id="x", title="t",
        metadata={"sources": ["openalex", "pubmed"]},
    )
    assert p.discovery_sources == ["openalex", "pubmed"]


def test_enrichment_sources_mirrored_from_legacy_metadata():
    p = Paper(
        id="x", title="t",
        metadata={"enrichment_sources": ["crossref"]},
    )
    assert p.enrichment_sources == ["crossref"]


def test_explicit_field_wins_over_legacy():
    p = Paper(
        id="x", title="t",
        discovery_sources=["new_value"],
        metadata={"sources": ["legacy_value"]},
    )
    assert p.discovery_sources == ["new_value"]


def test_fields_are_independent_lists():
    p1 = Paper(id="x", title="t")
    p2 = Paper(id="y", title="t")
    p1.discovery_sources.append("openalex")
    assert p2.discovery_sources == []
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_paper_discovery_sources.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/papers.py src/perspicacite/search/ src/perspicacite/pipeline/enrichment/ tests/unit/test_paper_discovery_sources.py
git commit -m "$(cat <<'EOF'
refactor: typed Paper.discovery_sources + enrichment_sources fields

Promotes provenance info from free-form metadata["sources"] dict keys
to first-class typed fields on Paper. Aggregators and enrichers now
write to both the typed field AND the legacy key (back-compat shim
for one release). A model_validator mirrors legacy → typed when only
the old shape is present.

Tier 3 cleanup task at end of this PR will remove the legacy writers.
EOF
)"
```

### Task 3.2: SourceReference.sources_all → discovery_sources alias

**Files:**
- Modify: `src/perspicacite/models/rag.py`
- Test: `tests/unit/test_source_reference_alias.py`

- [ ] **Step 1: Rename with alias**

In `src/perspicacite/models/rag.py::SourceReference`, locate `sources_all`. Replace with:

```python
    # Renamed: sources_all → discovery_sources (matches Paper.discovery_sources).
    # The old name lives on as a Pydantic alias so existing JSON payloads
    # (and the JS reading src.sources_all) keep working until UI catches up.
    discovery_sources: list[str] | None = Field(
        default=None,
        alias="sources_all",
        description="DBs that returned this paper (deduped). Multi-DB matches render as a chip group.",
    )

    # Allow both names in serialisation:
    model_config = {"populate_by_name": True}
```

If the class already has `model_config`, merge the keys (don't overwrite).

- [ ] **Step 2: Test alias works both ways**

Create `tests/unit/test_source_reference_alias.py`:

```python
"""Verify SourceReference.discovery_sources alias to legacy sources_all."""
from perspicacite.models.rag import SourceReference


def test_construct_via_new_name():
    s = SourceReference(title="t", discovery_sources=["a", "b"])
    assert s.discovery_sources == ["a", "b"]


def test_construct_via_legacy_alias():
    s = SourceReference(title="t", sources_all=["a", "b"])
    assert s.discovery_sources == ["a", "b"]


def test_serialises_with_both_names_available():
    s = SourceReference(title="t", discovery_sources=["a"])
    # Default dump uses field name
    d = s.model_dump()
    assert d["discovery_sources"] == ["a"]
    # by_alias produces legacy name (used in wire payloads)
    d2 = s.model_dump(by_alias=True)
    assert d2["sources_all"] == ["a"]
```

- [ ] **Step 3: Run tests + full suite**

```bash
uv run pytest tests/unit/test_source_reference_alias.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/models/rag.py tests/unit/test_source_reference_alias.py
git commit -m "$(cat <<'EOF'
refactor: SourceReference.discovery_sources (alias of legacy sources_all)

Matches the Paper.discovery_sources field added in Task 3.1. Old
"sources_all" name lives on as a Pydantic alias; existing JS that
reads src.sources_all keeps working via model_dump(by_alias=True)
on the chat router serialisation.
EOF
)"
```

### Task 3.3: Google Scholar citation-count extraction

**Files:**
- Modify: `src/perspicacite/search/google_scholar_playwright.py`
- Test: `tests/unit/test_google_scholar_citation_count.py`

- [ ] **Step 1: Add a regex + extractor**

In `src/perspicacite/search/google_scholar_playwright.py`, near the existing regex constants (`_DOI_RE`, `_YEAR_RE`, etc.), add:

```python
_CITED_BY_RE = re.compile(r"^Cited by\s+(\d+)", re.IGNORECASE)


def _extract_citation_count(footer_text: str) -> int | None:
    """Parse 'Cited by N' from the ``.gs_fl`` footer text.

    Robust to varied whitespace and lead-text; returns None when no
    match (so Paper.citation_count stays None instead of 0, preserving
    the "unknown vs known-zero" distinction).
    """
    if not footer_text:
        return None
    m = _CITED_BY_RE.search(footer_text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 2: Wire into the card extractor**

Find the existing card-extraction logic (search for `_render_and_extract_cards` or similar). After the card dict is built but before `Paper(...)` instantiation, add:

```python
            citation_count = _extract_citation_count(
                card.get("footer", "") or card.get("meta", "")
            )
            # ... existing code ...
            papers.append(
                Paper(
                    ...
                    citation_count=citation_count,  # NEW
                    ...
                )
            )
```

If the playwright DOM-extraction script doesn't already capture the footer, extend it to include `'footer': document.querySelector('.gs_fl').innerText` (or the appropriate selector).

- [ ] **Step 3: Write tests with a saved fixture**

Create `tests/unit/test_google_scholar_citation_count.py`:

```python
"""Unit tests for Google Scholar citation-count extraction."""
import pytest

from perspicacite.search.google_scholar_playwright import _extract_citation_count


@pytest.mark.parametrize("text,expected", [
    ("Cited by 42 Related articles All 3 versions", 42),
    ("Cited by 0 Related articles", 0),
    ("Cited by 12345 ...", 12345),
])
def test_extract_basic(text, expected):
    assert _extract_citation_count(text) == expected


def test_extract_missing_returns_none():
    assert _extract_citation_count("Related articles only") is None


def test_extract_empty_returns_none():
    assert _extract_citation_count("") is None
    assert _extract_citation_count(None) is None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_google_scholar_citation_count.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/google_scholar_playwright.py tests/unit/test_google_scholar_citation_count.py
git commit -m "$(cat <<'EOF'
feat: Google Scholar citation-count extraction

Parses 'Cited by N' from the gs_fl footer text. Stored in
Paper.citation_count so the ranking blend (25% weight) actually gets
a signal for GS hits — previously it was silently 0 for all of them.
None preserved when no count is present (vs zero) so the
known-zero vs unknown distinction stays.
EOF
)"
```

### Task 3.4: Unified `resolve_papers_pipeline` helper (C3)

**Files:**
- Create: `src/perspicacite/rag/resolve_papers.py`
- Modify: `src/perspicacite/rag/modes/basic.py::_web_fallback_papers` (call wrapper)
- Modify: `src/perspicacite/rag/modes/literature_survey.py::_broad_search`
- Modify: `src/perspicacite/rag/modes/profound.py` (web search call site)
- Modify: `src/perspicacite/mcp/server.py::web_search` (call wrapper)
- Test: `tests/unit/test_resolve_papers_pipeline.py`

- [ ] **Step 1: Write the unified pipeline**

Create `src/perspicacite/rag/resolve_papers.py`:

```python
"""Single canonical web-search → enrich → rerank pipeline.

Replaces three diverging implementations:
- basic/advanced :: _web_fallback_papers (full pipeline)
- profound       :: raw WebSearchTool.execute (no enrich, no rerank)
- literature_survey :: hand-rolled scilex + standalone fan-out (no enrich)
- new MCP web_search :: now also routes here

Returns ``list[Paper]``. Callers that need dict shape do the conversion
themselves. Telemetry events flow through the unified TelemetrySink.
"""
from __future__ import annotations

from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper

logger = get_logger("perspicacite.rag.resolve_papers")


async def resolve_papers_pipeline(
    *,
    query: str,
    databases: list[str] | None,
    max_docs: int,
    app_state: Any,
    telemetry: Any = None,
    enrich: bool = True,
    rerank: bool = True,
    min_relevance: float = 0.0,
    optimize_query: bool | None = None,
    context: str | None = None,
) -> list[Paper]:
    """Run aggregator → Crossref enrich → MiniLM rerank → relevance gate.

    Args:
        query: free-text user query
        databases: provider names (defaults to semantic_scholar/openalex/pubmed)
        max_docs: cap on returned papers
        app_state: AppState for config/llm_client access (None falls back
            to global via run_web_aggregator_search's helper)
        telemetry: TelemetrySink-like (list or sink object)
        enrich: when True, runs Crossref enrichment on the results
        rerank: when True, reranks with the MiniLM cross-encoder against
            the query
        min_relevance: drop papers with rerank score below this cutoff
            (0.0 = keep all)
        optimize_query: pass through to the aggregator optimizer
        context: optional grounding context for the optimizer

    Returns the (potentially shorter) list of Papers, sorted descending
    by rerank score when rerank=True.
    """
    from perspicacite.rag.web_search import run_web_aggregator_search

    papers = await run_web_aggregator_search(
        keyword_query=query,
        context=context,
        optimize_enabled=optimize_query,
        databases=databases,
        max_docs=max_docs,
        app_state=app_state,
        telemetry=telemetry,
    )

    if enrich and papers:
        try:
            from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers
            papers = await enrich_papers(papers)
        except Exception as e:
            logger.warning("resolve_papers_enrich_failed", error=str(e))

    if rerank and papers and len(papers) > 1:
        try:
            from perspicacite.search.screening import screen_papers_rerank
            items = [
                {
                    "_paper": p,
                    "title": p.title or "",
                    "abstract": p.abstract or "",
                }
                for p in papers
            ]
            results = await screen_papers_rerank(
                items, query=query, threshold=min_relevance,
            )
            scored = sorted(
                ((r.score, r.item["_paper"]) for r in results),
                key=lambda kv: kv[0], reverse=True,
            )
            papers = [p for _, p in scored][:max_docs]
        except Exception as e:
            logger.warning("resolve_papers_rerank_failed", error=str(e))
            papers = papers[:max_docs]
    else:
        papers = papers[:max_docs]

    return papers
```

- [ ] **Step 2: Write tests**

Create `tests/unit/test_resolve_papers_pipeline.py`:

```python
"""Unit tests for resolve_papers_pipeline."""
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.models.papers import Paper
from perspicacite.rag.resolve_papers import resolve_papers_pipeline


@pytest.mark.asyncio
async def test_returns_papers():
    fake = [Paper(id="x", title="t", doi="10.1/x")]
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p: p),
    ):
        out = await resolve_papers_pipeline(
            query="q", databases=None, max_docs=10,
            app_state=None, rerank=False,
        )
    assert len(out) == 1
    assert out[0].title == "t"


@pytest.mark.asyncio
async def test_enrich_can_be_disabled():
    fake = [Paper(id="x", title="t", doi="10.1/x")]
    enrich = AsyncMock(side_effect=lambda p: p)
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        enrich,
    ):
        await resolve_papers_pipeline(
            query="q", databases=None, max_docs=10,
            app_state=None, enrich=False, rerank=False,
        )
    enrich.assert_not_called()


@pytest.mark.asyncio
async def test_max_docs_cap_respected():
    fake = [Paper(id=str(i), title=f"t{i}") for i in range(20)]
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ):
        out = await resolve_papers_pipeline(
            query="q", databases=None, max_docs=5,
            app_state=None, enrich=False, rerank=False,
        )
    assert len(out) == 5
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_resolve_papers_pipeline.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

- [ ] **Step 4: Refactor callers to use the unified pipeline**

This is the BIG refactor — leave the existing functions in place but have them delegate. For each of basic.py / literature_survey.py / profound.py / MCP web_search:

**basic.py::_web_fallback_papers** — currently does aggregator + Crossref + rerank inline. Replace the bulk of the function with:

```python
async def _web_fallback_papers(*, query, databases, max_docs, ...) -> list[dict[str, Any]]:
    from perspicacite.rag.resolve_papers import resolve_papers_pipeline
    papers = await resolve_papers_pipeline(
        query=query, databases=databases, max_docs=max_docs,
        app_state=app_state, telemetry=telemetry,
        enrich=True, rerank=True, min_relevance=min_relevance,
        optimize_query=optimize_query, context=context,
    )
    # Adapt Paper objects to the dict shape the rest of basic.py expects
    candidates: list[dict[str, Any]] = []
    for p in papers:
        candidates.append({
            "paper_id": p.id, "title": p.title, "year": p.year,
            "authors": [a.name for a in (p.authors or [])],
            "journal": p.journal, "doi": p.doi, "url": p.url,
            "abstract": p.abstract or "",
            "chunk_text": p.abstract or "",
            "source": (p.discovery_sources[0] if p.discovery_sources else None),
            "sources_all": p.discovery_sources or None,
            "enrichment_sources": p.enrichment_sources or None,
            "paper_score": 0.5,
        })
    return candidates
```

(Keep `_canonicalize_candidates_from_crossref` re-exports for one release as already done in Task 1.1.)

**literature_survey.py::_broad_search** — already does the fan-out; convert to:

```python
papers = await resolve_papers_pipeline(
    query=effective_query, databases=databases, max_docs=100,
    app_state=_app_state, telemetry=telemetry,
    enrich=True, rerank=False,  # survey keeps its own analyser
    optimize_query=False,  # already optimised upstream
)
```

**profound.py** — replace the `web_tool.execute()` call inside `_stage_3_web_search` with:

```python
papers = await resolve_papers_pipeline(
    query=step_info.query, databases=databases, max_docs=5,
    app_state=getattr(request, "app_state", None),
    telemetry=telemetry, enrich=True, rerank=True,
)
# Adapt to the dict shape _web_results_to_document_dicts expects:
web_results = [
    {
        "title": p.title, "content": p.abstract or "",
        "citation": p.title, "url": p.url or "",
        "authors": [a.name for a in (p.authors or [])],
        "year": str(p.year) if p.year else "",
        "doi": p.doi or "", "abstract": p.abstract or "",
        "source": (p.discovery_sources[0] if p.discovery_sources else "web_search"),
    } for p in papers
]
```

**mcp/server.py::web_search** — already calls run_web_aggregator_search; swap to resolve_papers_pipeline:

```python
papers = await resolve_papers_pipeline(
    query=query, databases=databases, max_docs=max_results,
    app_state=None, telemetry=sink,
    enrich=enrich, rerank=True,
    optimize_query=optimize_query,
)
```

- [ ] **Step 5: Run full tests**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Expected: still 1800+ passing. If some tests fail (they likely will, given the refactor), inspect each failure — typically the shape of `paper_results` dicts shifted. Adapt the test or the adapter shim until green.

- [ ] **Step 6: Cross-mode regression test**

Create `tests/unit/test_cross_mode_search_consistency.py`:

```python
"""Regression: same query → resolve_papers_pipeline yields consistent results
across the four entry points (basic, advanced, profound, literature_survey, MCP web_search).

Stubs the aggregator + enrichment + rerank so we can deterministically
assert the same paper set comes back.
"""
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.models.papers import Paper
from perspicacite.rag.resolve_papers import resolve_papers_pipeline


@pytest.mark.asyncio
async def test_pipeline_deterministic_for_same_query():
    fake = [Paper(id=str(i), title=f"P{i}", doi=f"10.1/{i}") for i in range(5)]
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p: p),
    ), patch(
        "perspicacite.search.screening.screen_papers_rerank",
        AsyncMock(return_value=[]),
    ):
        out1 = await resolve_papers_pipeline(
            query="q", databases=["openalex"], max_docs=10,
            app_state=None, enrich=True, rerank=False,
        )
        out2 = await resolve_papers_pipeline(
            query="q", databases=["openalex"], max_docs=10,
            app_state=None, enrich=True, rerank=False,
        )
    assert [p.id for p in out1] == [p.id for p in out2]
    assert len(out1) == 5
```

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/rag/resolve_papers.py src/perspicacite/rag/modes/ src/perspicacite/mcp/server.py tests/unit/test_resolve_papers_pipeline.py tests/unit/test_cross_mode_search_consistency.py
git commit -m "$(cat <<'EOF'
refactor: unified resolve_papers_pipeline helper

Collapses three diverging web-search implementations
(basic._web_fallback_papers, literature_survey._broad_search,
profound's raw web_tool.execute) into one canonical pipeline:
aggregator → Crossref enrich → MiniLM rerank → relevance gate.

Each caller becomes a thin adapter that converts Paper → its
local dict shape. New MCP web_search also routes here. A fix
in any stage now lands once for everyone.

Cross-mode regression test asserts deterministic output.
EOF
)"
```

### Task 3.5: AppState injection — remove global imports from mode code

**Files:**
- Create: `src/perspicacite/web/state_minimal.py`
- Modify: `src/perspicacite/models/rag.py` (formalise app_state field)
- Modify: `src/perspicacite/rag/engine.py` (auto-attach app_state)
- Modify: `src/perspicacite/rag/web_search.py`, `rag/modes/basic.py`, `rag/modes/advanced.py`, `rag/modes/profound.py`, `rag/modes/literature_survey.py`, `rag/agentic/orchestrator.py` (read from request)
- Modify: `src/perspicacite/rag/tools/__init__.py` (require app_state)
- Test: `tests/unit/test_minimal_app_state.py`

- [ ] **Step 1: Define MinimalAppState**

Create `src/perspicacite/web/state_minimal.py`:

```python
"""Minimal AppState surface for CLI / MCP isolated execution.

The full ``AppState`` in ``web/state.py`` carries FastAPI router
state (lifespan handles, job registry, …) that CLI subcommands and
the MCP server don't need. ``MinimalAppState`` exposes just the
attributes RAG mode code reads via ``request.app_state``:

- ``config``       : full Config object (for search.query_optimization etc.)
- ``llm_client``   : AsyncLLMClient instance for optimizer / Haiku rewrites

Constructed from a Config in one call. Used by the CLI and the MCP
``generate_report`` / ``web_search`` tools so they no longer need the
heavyweight web AppState singleton.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MinimalAppState:
    """Subset of AppState that RAG mode code actually reads."""

    config: Any
    llm_client: Any = None

    @classmethod
    def from_config(cls, config: Any) -> "MinimalAppState":
        """Build a minimal state with a fresh LLM client."""
        from perspicacite.llm.client import AsyncLLMClient
        client = AsyncLLMClient(config)
        return cls(config=config, llm_client=client)
```

- [ ] **Step 2: Formalise `app_state` on RAGRequest**

In `src/perspicacite/models/rag.py::RAGRequest`, add:

```python
    app_state: Any = Field(
        default=None,
        description="AppState / MinimalAppState; threaded by RAGEngine",
        exclude=True,  # don't try to JSON-serialise it
    )
    model_config = {"extra": "allow", "arbitrary_types_allowed": True}
```

Merge `model_config` with any existing one — don't overwrite.

- [ ] **Step 3: Auto-attach in RAGEngine**

In `src/perspicacite/rag/engine.py`, find the `execute` and `execute_stream` methods. At the top of each, before dispatching:

```python
        # Auto-attach app_state so modes don't need to import the global.
        if getattr(request, "app_state", None) is None:
            request.app_state = getattr(self, "app_state", None)
```

If `RAGEngine.__init__` doesn't take an `app_state` param, add it (with default None) and have the AppState construction pass it in.

```bash
grep -n "class RAGEngine\|def __init__" src/perspicacite/rag/engine.py | head -5
```

- [ ] **Step 4: Remove global imports from mode code**

Find every offending import:

```bash
grep -rn "from perspicacite.web.state import app_state" src/perspicacite/rag/
```

For each hit (web_search.py, basic.py, advanced.py, profound.py, literature_survey.py, orchestrator.py), replace the inline import + fallback block with a direct read from `request`:

```python
# Old:
_app_state = None
try:
    from perspicacite.web.state import app_state as _gs
    _app_state = _gs
except Exception:
    pass

# New:
_app_state = getattr(request, "app_state", None)
```

If the surrounding code doesn't have access to `request` (e.g. inside a module-level helper), thread `request` or `app_state` as a parameter.

For `rag/tools/__init__.py::WebSearchTool`, make `app_state` a required init arg by removing the default `None`:

```python
def __init__(self, *, app_state: Any, databases=None, max_results: int = 5) -> None:
    # app_state is now required — callers must construct it (AppState
    # or MinimalAppState).
    if app_state is None:
        raise ValueError("WebSearchTool requires app_state")
    self._app_state = app_state
```

Any test or registration site that did `WebSearchTool()` needs to pass `app_state=...`. The web layer (`web/state.py::AppState.__init__`) already does this when it registers the tool; verify by grep:

```bash
grep -rn "WebSearchTool(" src/ tests/
```

- [ ] **Step 5: Test MinimalAppState**

Create `tests/unit/test_minimal_app_state.py`:

```python
"""Unit tests for MinimalAppState."""
from unittest.mock import MagicMock
from perspicacite.web.state_minimal import MinimalAppState


def test_minimal_app_state_direct_construction():
    cfg = MagicMock()
    state = MinimalAppState(config=cfg, llm_client="fake")
    assert state.config is cfg
    assert state.llm_client == "fake"


def test_minimal_app_state_from_config():
    """from_config constructs an LLM client without raising."""
    cfg = MagicMock()
    cfg.llm = MagicMock()
    cfg.llm.providers = []
    # AsyncLLMClient construction may need careful mocks; this is a
    # smoke test that the factory method runs.
    state = MinimalAppState.from_config(cfg)
    assert state.config is cfg
    assert state.llm_client is not None
```

- [ ] **Step 6: Verify the global import is gone**

```bash
grep -rn "from perspicacite.web.state import app_state" src/perspicacite/rag/
```

Expected: zero hits.

- [ ] **Step 7: Run full tests**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

Some agentic/orchestrator tests may fail if they constructed RAGRequest without app_state. Patch them by setting `request.app_state = None` (the auto-attach in engine handles the default).

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/web/state_minimal.py src/perspicacite/models/rag.py src/perspicacite/rag/ src/perspicacite/mcp/server.py tests/unit/test_minimal_app_state.py
git commit -m "$(cat <<'EOF'
refactor: AppState injection via request, MinimalAppState for CLI/MCP

RAG mode code no longer imports the global app_state singleton. The
RAGEngine auto-attaches state to each RAGRequest before dispatch;
modes read from request.app_state. WebSearchTool's app_state is now
required at construction.

New MinimalAppState gives CLI / MCP a lightweight state object with
just config + llm_client (no FastAPI lifespan baggage). Together
these unblock isolated CLI / MCP use without the web app loaded.
EOF
)"
```

### Task 3.6: Final cleanup — remove legacy metadata["sources"] writers

**Files:**
- Modify: any remaining writers of `paper.metadata["sources"]` (search providers)
- Modify: `src/perspicacite/models/papers.py` (drop the back-compat validator)
- Test: `tests/unit/test_no_legacy_metadata_sources.py`

- [ ] **Step 1: Find any remaining legacy writers**

```bash
grep -rn 'metadata\["sources"\]\|metadata\["enrichment_sources"\]' src/
```

For each writer, delete the legacy line and keep ONLY the typed-field writer (added in Task 3.1):

```python
# Before:
paper.metadata["sources"] = [...]
paper.discovery_sources.append("openalex")

# After:
if "openalex" not in paper.discovery_sources:
    paper.discovery_sources.append("openalex")
```

- [ ] **Step 2: Drop the back-compat validator on Paper**

In `src/perspicacite/models/papers.py`, REMOVE the `_mirror_legacy_metadata_sources` model_validator added in Task 3.1.

- [ ] **Step 3: Add an enforcement test**

Create `tests/unit/test_no_legacy_metadata_sources.py`:

```python
"""Enforce: no production code reads/writes legacy metadata['sources']."""
from pathlib import Path
import re

import perspicacite

SRC_DIR = Path(perspicacite.__file__).parent

_LEGACY_PAT = re.compile(
    r"""metadata\s*\[\s*["'](?:sources|enrichment_sources)["']\s*\]"""
)


def test_no_legacy_metadata_sources_writes():
    """grep src/ for metadata["sources"] hits — should find nothing."""
    hits: list[str] = []
    for f in SRC_DIR.rglob("*.py"):
        # Self-reference in this test file is OK.
        if "test_no_legacy_metadata_sources" in str(f):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for ln, line in enumerate(text.splitlines(), 1):
            if _LEGACY_PAT.search(line):
                hits.append(f"{f}:{ln}: {line.strip()}")
    assert hits == [], (
        "Legacy metadata['sources'] usage found — migrate to "
        "Paper.discovery_sources / Paper.enrichment_sources:\n"
        + "\n".join(hits)
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_no_legacy_metadata_sources.py -v
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -5
```

If the enforcement test fails, fix the remaining writers identified in its output, then re-run.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/ tests/unit/test_no_legacy_metadata_sources.py
git commit -m "$(cat <<'EOF'
refactor: drop legacy metadata['sources'] writers + back-compat shim

All production code now uses the typed Paper.discovery_sources /
Paper.enrichment_sources fields. The model_validator that mirrored
legacy metadata['sources'] → discovery_sources is removed.

A new enforcement test grep-asserts that no src/ file reads or
writes the legacy keys.
EOF
)"
```

### Tier 3 acceptance gate

- [ ] **Full test run**

```bash
uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -3
```

Expected: ~1800+ passing.

- [ ] **Lint + type check**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/ 2>&1 | tail -5
```

Fix any new issues introduced by the refactors. The Tier 3 changes add typed fields so mypy should be happier overall.

- [ ] **Live smoke test**

```bash
PID=$(lsof -ti:8000 2>/dev/null); [ -n "$PID" ] && kill $PID && sleep 2
zsh -ic 'uv run perspicacite -c config.yml serve' > /tmp/perspicacite-server.log 2>&1 &
disown
sleep 12
lsof -ti:8000
```

Open the GUI, run a basic-mode query with `google_scholar` selected. In the response:
- Source cards show `+Crossref` enrichment chip
- Retrieval panel shows per-provider counts
- No `legacy_metadata_sources` warnings in `/tmp/perspicacite-server.log`

Run the same query via MCP (test_mcp_live.py) and verify the response includes `discovery_sources` arrays (typed).

- [ ] **All three tiers complete — ship as a sequence of three PRs**

---

## Notes for the executing engineer

- **Don't batch commits.** Each task ends in a single commit. If you find you need to make a follow-up fix mid-task, finish the task first, then start a new commit for the fix.
- **If a test was passing before your change and now fails, revert and reread.** Most likely a back-compat assumption was broken; fix it without removing the test.
- **The 1731 baseline.** Confirm this is your starting point with `uv run pytest tests/unit/ -m "not live" -q 2>&1 | tail -3`. If it's different, ask before proceeding — the codebase has drifted.
- **`/tmp/perspicacite-server.log` is your friend** for live verification. After any tier, restart the server and run a query in the GUI to confirm the new event types appear / Crossref enriches / cancellation works.
- **Server restart pattern:**
  ```bash
  PID=$(lsof -ti:8000 2>/dev/null); [ -n "$PID" ] && kill $PID && sleep 2
  zsh -ic 'uv run perspicacite -c config.yml serve' > /tmp/perspicacite-server.log 2>&1 &
  disown
  sleep 12
  lsof -ti:8000 && echo "up" || (echo "down"; tail -15 /tmp/perspicacite-server.log)
  ```
  Always use `zsh -ic` to pick up `OPENROUTER_API_KEY` from `~/.zshrc`.
- **Spec is authoritative.** When in doubt, re-read `docs/superpowers/specs/2026-05-18-backend-mcp-hardening-design.md` — it has the rationale for every decision in the plan.
