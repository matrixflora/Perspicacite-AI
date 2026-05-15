# Audit Bug-Fix Batch (2026-05-15) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six real bugs / DX issues surfaced by the 2026-05-15 full-pipeline live audit (`tests/audit/results/2026-05-15-full-pipeline-findings.md`).

**Architecture:** Six small, surgical fixes. Each task owns one file pair (source + unit test) and lands an isolated commit on `main`. Tasks are TDD red→green→commit, ordered by priority (P0→P0→P1→P1→P2→P2).

**Tech Stack:** Python 3.13, Pydantic v2, dataclasses, aiosqlite, httpx, pytest + pytest-asyncio.

---

## File Structure

| Task | Source file(s) modified | Test file (new or extended) |
|---|---|---|
| 1 | `src/perspicacite/provenance/store.py` (+ `src/perspicacite/provenance/schema.py` NEW), `src/perspicacite/memory/session_store.py` | `tests/unit/test_provenance_store_init_db.py` (NEW) |
| 2 | `src/perspicacite/models/rag.py` | `tests/unit/test_source_reference_authors.py` (NEW) |
| 3 | `src/perspicacite/pipeline/snowball.py` | `tests/unit/test_snowball_seed_work_arxiv.py` (NEW) |
| 4 | `src/perspicacite/models/papers.py` | `tests/unit/test_paper_source_enum.py` (NEW) |
| 5 | `src/perspicacite/llm/budget.py` | `tests/unit/test_budget_tracker_kwargs.py` (NEW) |
| 6 | `src/perspicacite/rag/kb_router.py` | `tests/unit/test_kb_route_hit_iter.py` (NEW) |

---

### Task 1: ProvenanceStore.init_db() + escalate schema errors (P0)

**Why:** `ProvenanceStore(...)` against a fresh sqlite file silently drops every record via a broad `except Exception` around the INSERT. `get_for_message` raises `OperationalError: no such table: provenance`. The `provenance` table is only created by `SessionStore.init_db()` — an undocumented coupling.

**Files:**
- Create: `src/perspicacite/provenance/schema.py`
- Modify: `src/perspicacite/provenance/store.py`
- Modify: `src/perspicacite/memory/session_store.py:56-68`
- Test: `tests/unit/test_provenance_store_init_db.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_provenance_store_init_db.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_init_db_creates_provenance_table_in_fresh_db(tmp_path):
    db = tmp_path / "audit.sqlite"
    sidecar = tmp_path / "sidecar"
    store = ProvenanceStore(db_path=db, sidecar_dir=sidecar)

    # Round-trip a record against a *fresh* DB (no SessionStore involved).
    await store.init_db()
    await store.save({
        "message_id": "m1",
        "conversation_id": "c1",
        "rag_mode": "basic",
        "request_params": {"q": "x"},
        "retrieval_events": [],
        "mode_trace": [],
        "llm_calls": [],
    })

    rec = await store.get_for_message("m1")
    assert rec is not None
    assert rec["message_id"] == "m1"
    assert rec["rag_mode"] == "basic"


@pytest.mark.asyncio
async def test_save_escalates_when_schema_missing(tmp_path):
    """save() must NOT silently swallow OperationalError when the table is
    absent — that masked the original ProvenanceStore-standalone bug."""
    import aiosqlite

    db = tmp_path / "no_schema.sqlite"
    sidecar = tmp_path / "sidecar"
    # Touch the DB so the file exists but has no `provenance` table.
    async with aiosqlite.connect(db) as raw:
        await raw.execute("CREATE TABLE other (x INTEGER)")
        await raw.commit()

    store = ProvenanceStore(db_path=db, sidecar_dir=sidecar)
    # No init_db called → save() must raise, not log-and-return.
    with pytest.raises(aiosqlite.OperationalError):
        await store.save({
            "message_id": "m2",
            "conversation_id": None,
            "rag_mode": "basic",
            "llm_calls": [],
        })


@pytest.mark.asyncio
async def test_session_store_still_creates_provenance_table(tmp_path):
    """Regression: SessionStore must continue to create the provenance
    table for the existing shared-DB path."""
    import aiosqlite

    from perspicacite.memory.session_store import SessionStore

    db = tmp_path / "shared.sqlite"
    ss = SessionStore(db_path=db)
    await ss.init_db()

    async with aiosqlite.connect(db) as raw:
        cur = await raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provenance'"
        )
        row = await cur.fetchone()
    assert row is not None, "SessionStore must still create the provenance table"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_provenance_store_init_db.py -v`
Expected: FAIL — `init_db` does not exist on `ProvenanceStore`; second test sees `save()` swallow the OperationalError.

- [ ] **Step 3: Extract shared schema constant**

Create `src/perspicacite/provenance/schema.py`:

```python
"""Schema for the ``provenance`` table.

Single source of truth shared by :mod:`perspicacite.provenance.store`
and :mod:`perspicacite.memory.session_store` so a ``ProvenanceStore``
can be used standalone (without a ``SessionStore`` having booted first).
"""

PROVENANCE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provenance (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    rag_mode TEXT NOT NULL,
    request_params TEXT DEFAULT '{}',
    retrieval_events TEXT DEFAULT '[]',
    mode_trace TEXT DEFAULT '[]',
    llm_calls_index TEXT DEFAULT '[]',
    sidecar_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_provenance_conversation
    ON provenance(conversation_id);
"""
```

- [ ] **Step 4: Add `init_db()` and tighten error handling on `ProvenanceStore`**

In `src/perspicacite/provenance/store.py`:

1. At the top, add `import aiosqlite` (already present) and `from perspicacite.provenance.schema import PROVENANCE_TABLE_SQL`.

2. Inside `class ProvenanceStore`, add right after `__init__`:

```python
async def init_db(self) -> None:
    """Create the ``provenance`` table + index if absent.

    Idempotent. Safe to call alongside ``SessionStore.init_db()`` —
    both use ``CREATE TABLE IF NOT EXISTS``.
    """
    async with aiosqlite.connect(self.db_path) as db:
        await db.executescript(PROVENANCE_TABLE_SQL)
        await db.commit()
```

3. Tighten the broad `except Exception` in `save()` — re-raise `aiosqlite.OperationalError` (schema/connection problems) while still allowing other narrow exceptions to log-and-continue. Replace:

```python
        except Exception as exc:  # best-effort
            logger.warning("provenance_save_failed", error=str(exc), message_id=message_id)
```

with:

```python
        except aiosqlite.OperationalError:
            # Schema missing or DB locked — surface to caller so silent
            # data loss can't happen (2026-05-15 audit finding #1).
            logger.error(
                "provenance_save_schema_error",
                message_id=message_id,
                hint="call ProvenanceStore.init_db() before save()",
            )
            raise
        except Exception as exc:  # other failures stay best-effort
            logger.warning(
                "provenance_save_failed", error=str(exc), message_id=message_id,
            )
```

- [ ] **Step 5: Point SessionStore at the shared schema constant**

In `src/perspicacite/memory/session_store.py`, replace the inline `CREATE TABLE IF NOT EXISTS provenance (...)` block in the `SCHEMA` literal with a clean delete (lines 56-68 of the current file — the `provenance` CREATE TABLE and the `idx_provenance_conversation` index), and append, just before `class SessionStore:`:

```python
from perspicacite.provenance.schema import PROVENANCE_TABLE_SQL

SCHEMA = SCHEMA + "\n" + PROVENANCE_TABLE_SQL
```

This keeps SessionStore's behaviour identical (still creates the table on `init_db()`) while letting both stores share one source of truth.

- [ ] **Step 6: Run all tests in the affected area**

Run: `pytest tests/unit/test_provenance_store_init_db.py tests/unit/test_session_store.py tests/unit/test_provenance_store.py tests/unit/test_provenance_collector.py -v`
Expected: PASS — new tests pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/provenance/schema.py \
        src/perspicacite/provenance/store.py \
        src/perspicacite/memory/session_store.py \
        tests/unit/test_provenance_store_init_db.py
git commit -m "fix(provenance): ProvenanceStore.init_db() + escalate schema errors

Audit 2026-05-15 finding #1: standalone ProvenanceStore silently dropped
every record because the schema was only created by SessionStore.init_db.
Extract PROVENANCE_TABLE_SQL into provenance/schema.py, add init_db() to
ProvenanceStore, and re-raise aiosqlite.OperationalError in save() so
silent data loss can't recur.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: `SourceReference.authors: list[str]` with back-compat validator (P0)

**Why:** Field is currently `Optional[str]` (`src/perspicacite/models/rag.py:34`). Construction with `authors=["A", "B"]` fails Pydantic validation. All call sites in `rag/modes/*` and `rag/utils/__init__.py` already feed `p.get("authors")` which is a `list[str]` (from `normalize_paper_dict`).

**Files:**
- Modify: `src/perspicacite/models/rag.py:30-53`
- Test: `tests/unit/test_source_reference_authors.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_source_reference_authors.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.models.rag import SourceReference


def test_authors_accepts_list_of_strings():
    s = SourceReference(title="T", authors=["Alice", "Bob"])
    assert s.authors == ["Alice", "Bob"]


def test_authors_defaults_to_empty_list():
    s = SourceReference(title="T")
    assert s.authors == []


def test_authors_coerces_comma_joined_string():
    # Backward-compat: pre-fix call sites passed "A, B, C"
    s = SourceReference(title="T", authors="Alice, Bob, Carol")
    assert s.authors == ["Alice", "Bob", "Carol"]


def test_authors_coerces_and_separated_string():
    s = SourceReference(title="T", authors="Alice and Bob")
    assert s.authors == ["Alice", "Bob"]


def test_authors_coerces_none_to_empty():
    s = SourceReference(title="T", authors=None)
    assert s.authors == []


def test_authors_single_string_becomes_single_element_list():
    s = SourceReference(title="T", authors="OnlyAuthor")
    assert s.authors == ["OnlyAuthor"]


def test_to_citation_uses_first_author_with_et_al():
    s = SourceReference(title="T", authors=["Jumper", "Evans", "Pritzel"], year=2021)
    assert s.to_citation() == "[Jumper et al., 2021]"


def test_to_citation_single_author_no_et_al():
    s = SourceReference(title="T", authors=["Solo"], year=2024)
    assert s.to_citation() == "[Solo, 2024]"


def test_to_citation_empty_authors_unknown():
    s = SourceReference(title="T", year=2024)
    assert s.to_citation() == "[Unknown, 2024]"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_source_reference_authors.py -v`
Expected: FAIL — field rejects list, no validator, `to_citation` checks `"," in author_part`.

- [ ] **Step 3: Update `SourceReference` model**

In `src/perspicacite/models/rag.py`, replace the existing `SourceReference` class (currently lines 30-53) with:

```python
class SourceReference(BaseModel):
    """Reference to a source paper."""

    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    chunk_text: Optional[str] = None
    kb_name: Optional[str] = None

    @field_validator("authors", mode="before")
    @classmethod
    def _coerce_authors(cls, v):
        """Accept str (legacy), None, or list — always store list[str].

        Audit 2026-05-15 finding #2: the field was previously
        ``Optional[str]`` which broke construction from upstream
        ``normalize_paper_dict`` (returns ``list[str]``). This validator
        keeps backward compat for the comma-joined-string call sites
        that pre-dated the fix.
        """
        if v is None:
            return []
        if isinstance(v, list):
            return [str(a).strip() for a in v if str(a).strip()]
        if isinstance(v, str):
            # Split on " and " (BibTeX-style) then commas.
            parts: list[str] = []
            for chunk in v.replace(" and ", ",").split(","):
                chunk = chunk.strip()
                if chunk:
                    parts.append(chunk)
            return parts
        return [str(v)]

    def __repr__(self) -> str:
        return f"SourceReference(title='{self.title[:40]}...', score={self.relevance_score:.2f})"

    def to_citation(self, style: str = "nature") -> str:
        """Format as citation string."""
        if not self.authors:
            author_part = "Unknown"
        elif len(self.authors) == 1:
            author_part = self.authors[0]
        else:
            author_part = f"{self.authors[0]} et al."
        year_part = f", {self.year}" if self.year else ""
        return f"[{author_part}{year_part}]"
```

Make sure `from pydantic import BaseModel, Field, field_validator` is on the imports line (currently `from pydantic import BaseModel, Field` — add `field_validator`).

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/unit/test_source_reference_authors.py -v`
Expected: PASS — 9/9.

- [ ] **Step 5: Run broader RAG / models tests to check for regressions**

Run: `pytest tests/unit/test_models.py tests/unit/test_session_store.py tests/unit/test_rag_modes_basic.py -v`
Expected: PASS — the validator accepts the legacy `str` shape, so existing call sites stay green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/rag.py tests/unit/test_source_reference_authors.py
git commit -m "fix(rag): SourceReference.authors → list[str] with back-compat validator

Audit 2026-05-15 finding #2: the str-only field broke construction from
upstream normalize_paper_dict which returns list[str]. Change the field
to list[str] (default empty) and add a field_validator that coerces
None, comma-joined strings, and ' and '-separated BibTeX strings for
backward compatibility with pre-fix call sites.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: arXiv-id fallback in `_fetch_seed_work` (P1)

**Why:** All three arXiv-DOI papers in the audit returned 0 cite-graph hits via the DOI happy-path because `_fetch_seed_work` doesn't have the arXiv-id fallback that `openalex_id_for_doi` already does (added in the prior P1/P2/P3 batch).

**Files:**
- Modify: `src/perspicacite/pipeline/snowball.py:139-152`
- Test: `tests/unit/test_snowball_seed_work_arxiv.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_snowball_seed_work_arxiv.py`:

```python
from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.snowball import _fetch_seed_work


@pytest.mark.asyncio
async def test_fetch_seed_work_falls_back_to_arxiv_id_on_doi_404(monkeypatch):
    """For arXiv DOIs, when /works/doi:... 404s, _fetch_seed_work must
    retry via the ids.arxiv filter — same fallback as openalex_id_for_doi.
    """
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append({"url": url, "params": dict(kwargs.get("params") or {})})
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        # Fallback path: list endpoint with ids.arxiv filter.
        assert kwargs.get("params", {}).get("filter", "").startswith("ids.arxiv:")
        return httpx.Response(
            200,
            json={"results": [{"id": "https://openalex.org/W3098425262",
                                "display_name": "RAG"}]},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.48550/arXiv.2005.11401", {})
    assert work is not None
    assert work["id"] == "https://openalex.org/W3098425262"
    # Two HTTP calls: doi miss then arxiv filter hit.
    assert len(calls) == 2
    assert calls[0]["url"].endswith("doi:10.48550/arXiv.2005.11401")
    assert calls[1]["params"]["filter"] == "ids.arxiv:2005.11401"


@pytest.mark.asyncio
async def test_fetch_seed_work_returns_none_when_arxiv_fallback_also_misses(monkeypatch):
    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.48550/arXiv.9999.99999", {})
    assert work is None


@pytest.mark.asyncio
async def test_fetch_seed_work_non_arxiv_doi_returns_none_on_404(monkeypatch):
    """Non-arXiv DOIs should not trigger the fallback — return None as before."""
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(404, json={}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.1234/not-arxiv", {})
    assert work is None
    # Only one call — fallback was not triggered.
    assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_snowball_seed_work_arxiv.py -v`
Expected: FAIL — `_fetch_seed_work` returns `None` on the 404 instead of trying the arxiv fallback.

- [ ] **Step 3: Add fallback to `_fetch_seed_work`**

In `src/perspicacite/pipeline/snowball.py`, replace the existing `_fetch_seed_work` body (currently lines 139-152) with:

```python
async def _fetch_seed_work(
    client: httpx.AsyncClient, doi: str, headers: dict[str, str],
) -> dict[str, Any] | None:
    """One OpenAlex work record for a single DOI, or None on miss.

    Falls back to the ``ids.arxiv`` filter when /works/doi:... 404s on
    an arXiv DOI — mirrors :func:`openalex_id_for_doi` (audit
    2026-05-15 finding #3).
    """
    url = f"{OPENALEX_BASE}/works/doi:{doi}"
    try:
        r = await client.get(url, headers=headers, timeout=20.0)
        if r.status_code == 200:
            return r.json()
        logger.info("snowball_oa_seed_miss", doi=doi, status=r.status_code)
    except httpx.HTTPError as exc:
        logger.warning("snowball_oa_seed_error", doi=doi, error=str(exc))
        return None

    # Fallback: arXiv-id filter for arXiv DOIs.
    arxiv_id = parse_arxiv_doi(doi)
    if arxiv_id is None:
        return None
    list_url = f"{OPENALEX_BASE}/works"
    try:
        r = await client.get(
            list_url,
            params={"filter": f"ids.arxiv:{arxiv_id}", "per-page": "1"},
            headers=headers,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("snowball_oa_arxiv_fallback_error", doi=doi, error=str(exc))
        return None
    if r.status_code != 200:
        logger.info("snowball_oa_arxiv_fallback_miss", doi=doi, status=r.status_code)
        return None
    results = (r.json() or {}).get("results") or []
    if not results:
        return None
    return results[0]
```

`parse_arxiv_doi` is already imported at line 45.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_snowball_seed_work_arxiv.py tests/unit/test_snowball_public_helpers.py -v`
Expected: PASS — new tests + existing `openalex_id_for_doi` tests stay green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/snowball.py tests/unit/test_snowball_seed_work_arxiv.py
git commit -m "fix(snowball): _fetch_seed_work arXiv-id fallback when DOI 404s

Audit 2026-05-15 finding #3: cite-graph DOI happy-path returned 0 hits
for all three arXiv papers in the live audit because _fetch_seed_work
didn't have the same arXiv-id fallback as openalex_id_for_doi. Mirror
that fallback so the cite-graph orchestrator can seed from arXiv DOIs.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Extend `PaperSource` enum (P1)

**Why:** The enum lacks `OPENALEX`, `PUBMED`, `ARXIV`, `CROSSREF` even though the project ingests from all four. Callers use `WEB_SEARCH` semantically wrong. The harness hit this when it tried `PaperSource.PUBMED` and got `AttributeError`.

**Files:**
- Modify: `src/perspicacite/models/papers.py:10-19`
- Modify: `src/perspicacite/search/pubmed.py:207` (use `PUBMED`)
- Test: `tests/unit/test_paper_source_enum.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_paper_source_enum.py`:

```python
from __future__ import annotations

from perspicacite.models.papers import PaperSource


def test_enum_has_legacy_values():
    """Legacy values must keep working — no regressions."""
    assert PaperSource.BIBTEX.value == "bibtex"
    assert PaperSource.SCILEX.value == "scilex"
    assert PaperSource.WEB_SEARCH.value == "web_search"
    assert PaperSource.USER_UPLOAD.value == "user_upload"
    assert PaperSource.CITATION_FOLLOW.value == "citation_follow"
    assert PaperSource.LOCAL.value == "local"


def test_enum_has_new_database_values():
    """Audit 2026-05-15 finding #5: explicit DB sources required."""
    assert PaperSource.OPENALEX.value == "openalex"
    assert PaperSource.PUBMED.value == "pubmed"
    assert PaperSource.ARXIV.value == "arxiv"
    assert PaperSource.CROSSREF.value == "crossref"


def test_enum_constructs_from_string_for_chroma_roundtrip():
    """retrieval/chroma_store.py:599 calls PaperSource(metadata.get('source','bibtex'))."""
    assert PaperSource("openalex") is PaperSource.OPENALEX
    assert PaperSource("pubmed") is PaperSource.PUBMED
    assert PaperSource("arxiv") is PaperSource.ARXIV
    assert PaperSource("crossref") is PaperSource.CROSSREF
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_paper_source_enum.py -v`
Expected: FAIL — new attributes don't exist.

- [ ] **Step 3: Extend the enum**

In `src/perspicacite/models/papers.py`, replace the existing `class PaperSource` block (lines 10-19) with:

```python
class PaperSource(str, Enum):
    """Source of a paper.

    Legacy values (BIBTEX, SCILEX, WEB_SEARCH, USER_UPLOAD,
    CITATION_FOLLOW, LOCAL) are kept for backward compat.
    Audit 2026-05-15 finding #5 added explicit database sources.
    """

    BIBTEX = "bibtex"
    SCILEX = "scilex"
    WEB_SEARCH = "web_search"
    USER_UPLOAD = "user_upload"
    CITATION_FOLLOW = "citation_follow"
    LOCAL = "local"
    OPENALEX = "openalex"
    PUBMED = "pubmed"
    ARXIV = "arxiv"
    CROSSREF = "crossref"
```

- [ ] **Step 4: Migrate the obvious PubMed ingest call site**

In `src/perspicacite/search/pubmed.py:207`, change:

```python
                    source=PaperSource.WEB_SEARCH,
```

to:

```python
                    source=PaperSource.PUBMED,
```

(Scope is intentionally narrow: just this one obviously-wrong site. The remaining `WEB_SEARCH` sites span multi-source aggregators and stay as-is to avoid scope creep; threading explicit sources through every ingest path is a follow-up.)

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_paper_source_enum.py tests/unit/test_models.py tests/unit/test_pubmed_search.py -v 2>&1 | tail -30`
Expected: PASS for the new test and no regressions. (If `test_pubmed_search.py` doesn't exist, skip it — pytest will report `no tests ran` for that path, fine.)

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/papers.py \
        src/perspicacite/search/pubmed.py \
        tests/unit/test_paper_source_enum.py
git commit -m "feat(models): PaperSource gains OPENALEX/PUBMED/ARXIV/CROSSREF

Audit 2026-05-15 finding #5: enum was missing the four databases the
project actually ingests from. Add the values and migrate the one
obvious mis-attributed call site (search/pubmed.py was returning
WEB_SEARCH). Other ingest paths stay as-is to keep the change small;
threading explicit sources through every adapter is a follow-up.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: `BudgetTracker` accepts `max_tokens` / `max_cost_usd` kwargs (P2)

**Why:** The audit harness tried `BudgetTracker(max_tokens=1000, max_cost_usd=1.0)` and got `TypeError`. Real fields are `max_input_tokens / max_output_tokens / max_usd`. We add the natural-API aliases without removing the existing fields, and add a new combined-tokens cap for `max_tokens`.

**Files:**
- Modify: `src/perspicacite/llm/budget.py:71-95` (dataclass fields) and `_enforce` (lines 156-186)
- Test: `tests/unit/test_budget_tracker_kwargs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_budget_tracker_kwargs.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.llm.budget import BudgetExceededError, BudgetTracker


def test_max_cost_usd_alias_for_max_usd():
    t = BudgetTracker(max_cost_usd=1.0)
    assert t.max_usd == 1.0


def test_explicit_max_usd_still_works():
    t = BudgetTracker(max_usd=2.5)
    assert t.max_usd == 2.5
    assert t.max_cost_usd is None or t.max_cost_usd == 2.5  # either is fine


def test_max_usd_wins_when_both_set_to_different_values():
    """If a caller sets both, the canonical (max_usd) value wins."""
    t = BudgetTracker(max_usd=3.0, max_cost_usd=1.0)
    assert t.max_usd == 3.0


def test_max_tokens_combined_cap_enforced():
    """max_tokens caps tokens_in + tokens_out combined."""
    t = BudgetTracker(max_tokens=100, action="abort")
    t.record(provider="claude_cli", model="*", input_tokens=40, output_tokens=40)
    # 80 < 100 → ok
    with pytest.raises(BudgetExceededError):
        t.record(provider="claude_cli", model="*", input_tokens=30, output_tokens=0)
    # 110 > 100 → breach


def test_max_tokens_warn_mode_does_not_raise():
    t = BudgetTracker(max_tokens=10, action="warn")
    # Should not raise even when breached
    t.record(provider="claude_cli", model="*", input_tokens=20, output_tokens=0)
    assert any("max_tokens" in b or "total_tokens" in b for b in t.breaches)


def test_natural_audit_harness_call_works():
    """The exact call from the audit harness — must not raise."""
    t = BudgetTracker(max_tokens=1000, max_cost_usd=1.0)
    assert t.max_usd == 1.0
    # Recording within cap is fine
    t.record(provider="claude_cli", model="*", input_tokens=10, output_tokens=10)
    assert t.tokens_in + t.tokens_out == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_budget_tracker_kwargs.py -v`
Expected: FAIL — `BudgetTracker.__init__()` got an unexpected keyword argument 'max_tokens' / 'max_cost_usd'.

- [ ] **Step 3: Add the aliases + combined-tokens cap**

In `src/perspicacite/llm/budget.py`, modify the dataclass:

1. Add the two new optional fields to the dataclass, immediately after `max_usd: float | None = None`:

```python
    # Aliases / additions added by audit 2026-05-15 finding #4.
    max_tokens: int | None = None       # combined input+output cap
    max_cost_usd: float | None = None   # alias of max_usd
```

2. Add `__post_init__` right after the dataclass fields (before `# ---- core API ------`):

```python
    def __post_init__(self) -> None:
        # `max_cost_usd` is a friendly alias for `max_usd`. If the caller
        # set only the alias, copy it through; if both were set, canonical
        # `max_usd` wins (so existing internal callers stay deterministic).
        if self.max_cost_usd is not None and self.max_usd is None:
            self.max_usd = self.max_cost_usd
```

3. Extend `_enforce` to check the combined-tokens cap. Inside `_enforce`, after the existing `max_output_tokens` block and before the `max_usd` block, insert:

```python
        if self.max_tokens is not None:
            total = self.tokens_in + self.tokens_out
            if total > self.max_tokens:
                breaches.append(("total_tokens",
                    f"total_tokens={total} > cap={self.max_tokens}"))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_budget_tracker_kwargs.py tests/unit/test_budget.py -v 2>&1 | tail -30`
Expected: PASS — new tests + existing budget tests stay green. (If `test_budget.py` doesn't exist under that name, locate the existing budget tests with `find tests -name 'test_budget*.py'` and run those instead.)

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/budget.py tests/unit/test_budget_tracker_kwargs.py
git commit -m "feat(budget): accept max_tokens + max_cost_usd kwargs

Audit 2026-05-15 finding #4: BudgetTracker rejected the natural-looking
BudgetTracker(max_tokens=1000, max_cost_usd=1.0). Add max_cost_usd as
an alias for max_usd and add max_tokens as a combined input+output
token cap (canonical max_input_tokens / max_output_tokens still work).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: `KBRouteHit.__iter__` for destructuring (P2)

**Why:** `route_kbs(...)` returns `list[KBRouteHit]`. The natural pattern `for name, score in route_kbs(...)` raises `TypeError: cannot unpack non-iterable KBRouteHit object`. The harness silently got wrong answers when treating hits like tuples.

**Files:**
- Modify: `src/perspicacite/rag/kb_router.py:82-90` (KBRouteHit dataclass)
- Test: `tests/unit/test_kb_route_hit_iter.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_kb_route_hit_iter.py`:

```python
from __future__ import annotations

from perspicacite.rag.kb_router import KBRouteHit, route_kbs, _bm25_cache_clear


def test_kb_route_hit_destructures_into_name_and_score():
    hit = KBRouteHit(kb_name="biochem", score=0.75, reason=None, sampled_titles=3)
    name, score = hit
    assert name == "biochem"
    assert score == 0.75


def test_kb_route_hit_iter_yields_only_name_and_score():
    hit = KBRouteHit(kb_name="x", score=0.1)
    assert list(hit) == ["x", 0.1]


def test_route_kbs_results_destructure_in_a_loop():
    _bm25_cache_clear()
    contexts = {
        "biochem": "protein structure prediction alphafold",
        "ml_general": "transformers attention deep learning",
    }
    pairs = [(name, score) for name, score in
             route_kbs(query="alphafold protein", kb_contexts=contexts, top_k=2)]
    assert len(pairs) == 2
    # biochem ranks first for this query.
    assert pairs[0][0] == "biochem"
    assert isinstance(pairs[0][1], float)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_kb_route_hit_iter.py -v`
Expected: FAIL — `cannot unpack non-iterable KBRouteHit object`.

- [ ] **Step 3: Add `__iter__` to the dataclass**

In `src/perspicacite/rag/kb_router.py`, replace the `KBRouteHit` dataclass (currently lines 82-90) with:

```python
@dataclass
class KBRouteHit:
    kb_name: str
    score: float
    reason: str | None = None
    sampled_titles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __iter__(self):
        """Allow ``for name, score in route_kbs(...)`` destructuring.

        Audit 2026-05-15 finding #7: the harness naturally tried to
        unpack hits and silently got wrong answers. Yielding only the
        two most-commonly-needed fields keeps the cheap-tuple ergonomics
        without removing access to the richer attributes.
        """
        yield self.kb_name
        yield self.score
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_kb_route_hit_iter.py tests/unit/test_kb_router.py tests/unit/test_kb_router_bm25s.py -v 2>&1 | tail -30`
Expected: PASS — new test passes, existing router tests stay green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/kb_router.py tests/unit/test_kb_route_hit_iter.py
git commit -m "feat(rag): KBRouteHit.__iter__ for (name, score) destructuring

Audit 2026-05-15 finding #7: route_kbs returns list[KBRouteHit], but the
natural ``for name, score in route_kbs(...)`` raised TypeError. Add
__iter__ yielding (kb_name, score) so the common case destructures
cleanly; full attributes remain accessible.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:** Each of the seven findings in `2026-05-15-full-pipeline-findings.md` is covered, except #6 (OpenAlex underreports arXiv-only citations) which is explicitly P3 and out of scope, and the priority queue's P3 (Semantic Scholar alternative) which the findings doc itself flags as a separate spec. The six tasks map 1:1 to priority queue items 1-6.

**2. Placeholder scan:** No TBDs, no "implement later", every code block is complete, every command is concrete. Each step has a clear pass/fail criterion.

**3. Type consistency:** `parse_arxiv_doi` is referenced in Task 3 only (already imported in snowball.py at line 45 — confirmed in pre-plan exploration). `PROVENANCE_TABLE_SQL` is defined in Task 1 Step 3 before being imported in Step 5. `field_validator` is added to imports in Task 2 Step 3 before use.

**4. Risk notes:**
- Task 2's validator runs `mode="before"` so it sees raw input, which is necessary to accept legacy `str` shapes. Test coverage includes None / list / str / single-str / "and"-separated.
- Task 1's `aiosqlite.OperationalError` re-raise changes save() from log-and-swallow to log-and-raise — this is the explicit intent of finding #1 (no silent data loss) but means a caller that currently catches `OperationalError` upstream still gets the same exception shape.
- Task 4 keeps WEB_SEARCH in places (semantic_scholar, web routers) intentionally; only the obviously-mis-attributed PubMed call site is migrated to scope the change.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-audit-bug-fixes-batch.md`. Per standing instructions (per-task commit directly to `main`, never push, no clarifying-question pauses), this plan will be executed via **subagent-driven-development**: one fresh implementer subagent per task, two-stage review (spec compliance → code quality) between tasks.
