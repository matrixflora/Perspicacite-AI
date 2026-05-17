# Literature Survey Multi-KB Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two capabilities to `LiteratureSurveyRAGMode`: retrieve known papers from all provided KBs before the broad search (pre-filtering + context injection), and store final-recommendation papers as lightweight SQLite references in extra KBs.

**Architecture:** Two private methods added to `LiteratureSurveyRAGMode`; a new `kb_paper_references` SQLite table with two `SessionStore` methods; `RAGEngine` receives an optional `session_store` kwarg and injects it into the survey mode. All changes are additive and backward-compatible.

**Tech Stack:** `aiosqlite`, `ChromaVectorStore.list_paper_ids_in_collection()`, `BaseRAGMode._build_kb_retriever()`, `chroma_collection_name_for_kb()` — all already in the project.

---

## File Map

| File | Role |
|------|------|
| `src/perspicacite/memory/session_store.py` | Add `kb_paper_references` table to `SCHEMA`; add `store_paper_reference()` + `get_paper_references()` methods |
| `src/perspicacite/rag/modes/literature_survey.py` | Add `session_store = None` attr; add `_prepare_kb_context()`; add `_store_references_to_all_kbs()`; update `execute()`, `execute_stream()`, `_generate_interim_summary()` |
| `src/perspicacite/rag/engine.py` | Add `session_store: Any = None` kwarg to `__init__`; inject into survey mode |
| `src/perspicacite/web/state.py` | Move session-store init before `RAGEngine`; pass `session_store=self.session_store` |
| `src/perspicacite/mcp/server.py` | Pass `session_store=state.session_store` to locally-constructed `RAGEngine` |
| `tests/unit/test_session_store_references.py` | New — 5 tests for `store_paper_reference` + `get_paper_references` |
| `tests/unit/test_literature_survey_kb.py` | New — 8 tests for survey KB methods (all mocked, no network/ChromaDB) |

---

### Task 1: SQLite `kb_paper_references` table + `SessionStore` methods

**Files:**
- Modify: `src/perspicacite/memory/session_store.py`
- Create: `tests/unit/test_session_store_references.py`

- [ ] **Step 1: Create the test file**

```python
# tests/unit/test_session_store_references.py
"""Tests for SessionStore kb_paper_references table methods."""
from __future__ import annotations

import pytest


@pytest.fixture
async def store(tmp_path):
    from perspicacite.memory.session_store import SessionStore
    s = SessionStore(tmp_path / "test.db")
    await s.init_db()
    return s


async def test_store_paper_reference_returns_true_for_new(store):
    result = await store.store_paper_reference(
        kb_name="kb-a",
        doi="10.1/test",
        title="Test Paper",
        authors=["Author A", "Author B"],
        year=2021,
        abstract="An abstract.",
        survey_query="test query",
    )
    assert result is True


async def test_store_paper_reference_returns_false_for_duplicate(store):
    await store.store_paper_reference("kb-a", "10.1/x", "Title", [], 2021, "abs")
    result = await store.store_paper_reference("kb-a", "10.1/x", "Title", [], 2021, "abs")
    assert result is False


async def test_store_paper_reference_same_doi_different_kb_both_succeed(store):
    r1 = await store.store_paper_reference("kb-a", "10.1/x", "Title", [], 2021, "abs")
    r2 = await store.store_paper_reference("kb-b", "10.1/x", "Title", [], 2021, "abs")
    assert r1 is True
    assert r2 is True


async def test_get_paper_references_returns_stored(store):
    await store.store_paper_reference(
        "kb-a", "10.1/test", "Test", ["A"], 2021, "abs", "query"
    )
    refs = await store.get_paper_references("kb-a")
    assert len(refs) == 1
    r = refs[0]
    assert r["doi"] == "10.1/test"
    assert r["title"] == "Test"
    assert r["authors"] == ["A"]
    assert r["year"] == 2021


async def test_get_paper_references_filters_by_kb(store):
    await store.store_paper_reference("kb-a", "10.1/a", "Paper A", [], 2020, None)
    await store.store_paper_reference("kb-b", "10.1/b", "Paper B", [], 2021, None)
    refs = await store.get_paper_references("kb-a")
    assert len(refs) == 1
    assert refs[0]["doi"] == "10.1/a"
```

- [ ] **Step 2: Run tests — expect ImportError / AttributeError (methods not yet present)**

```bash
uv run pytest tests/unit/test_session_store_references.py -v
```

Expected: 5 failures (AttributeError: 'SessionStore' object has no attribute 'store_paper_reference')

- [ ] **Step 3: Add table to SCHEMA in `session_store.py`**

Open `src/perspicacite/memory/session_store.py`. Find `SCHEMA = """` (line 17). The string ends at line 70 before the `CREATE INDEX` statements. Add the new table before the closing `"""`:

```python
# In SCHEMA string, after the `jobs` table and before the CREATE INDEX lines, add:

CREATE TABLE IF NOT EXISTS kb_paper_references (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    kb_name      TEXT NOT NULL,
    doi          TEXT,
    title        TEXT NOT NULL,
    authors_json TEXT DEFAULT '[]',
    year         INTEGER,
    abstract     TEXT,
    survey_query TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kb_name, doi)
);
CREATE INDEX IF NOT EXISTS idx_kb_paper_refs_kb ON kb_paper_references(kb_name);
```

The full `SCHEMA` string section to replace (lines 56–70 of `session_store.py`):

```python
# BEFORE (existing end of SCHEMA string):
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
"""
```

```python
# AFTER:
CREATE TABLE IF NOT EXISTS kb_paper_references (
    id           TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    kb_name      TEXT NOT NULL,
    doi          TEXT,
    title        TEXT NOT NULL,
    authors_json TEXT DEFAULT '[]',
    year         INTEGER,
    abstract     TEXT,
    survey_query TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kb_name, doi)
);
CREATE INDEX IF NOT EXISTS idx_kb_paper_refs_kb ON kb_paper_references(kb_name);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id);
"""
```

- [ ] **Step 4: Add `store_paper_reference()` method to `SessionStore`**

Add immediately after the `list_kbs()` method (around line 400 in `session_store.py`). Add this exact code:

```python
async def store_paper_reference(
    self,
    kb_name: str,
    doi: str | None,
    title: str,
    authors: list[str],
    year: int | None,
    abstract: str | None,
    survey_query: str | None = None,
) -> bool:
    """Write a reference-only paper record to kb_paper_references.

    Returns True if a new row was inserted, False if it already existed
    (UNIQUE(kb_name, doi) conflict).  Only records with a non-null ``doi``
    can trigger the uniqueness check; callers should skip papers without DOIs.
    """
    import json as _json

    async with aiosqlite.connect(self.db_path) as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO kb_paper_references
                (kb_name, doi, title, authors_json, year, abstract, survey_query)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kb_name,
                doi,
                title,
                _json.dumps(authors),
                year,
                abstract,
                survey_query,
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_paper_references(self, kb_name: str) -> list[dict]:
    """Return all reference-only paper records for a KB, newest first."""
    import json as _json

    async with aiosqlite.connect(self.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM kb_paper_references WHERE kb_name = ? ORDER BY created_at DESC",
            (kb_name,),
        )
        rows = await cur.fetchall()

    return [
        {
            **dict(r),
            "authors": _json.loads(r["authors_json"] or "[]"),
        }
        for r in rows
    ]
```

- [ ] **Step 5: Run tests — expect all 5 to pass**

```bash
uv run pytest tests/unit/test_session_store_references.py -v
```

Expected: 5 passed

- [ ] **Step 6: Run full unit suite to check for regressions**

```bash
uv run pytest tests/unit/ -v --tb=short
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/memory/session_store.py tests/unit/test_session_store_references.py
git commit -m "feat: add kb_paper_references SQLite table + SessionStore methods"
```

---

### Task 2: `_prepare_kb_context()` method

**Files:**
- Modify: `src/perspicacite/rag/modes/literature_survey.py`
- Create: `tests/unit/test_literature_survey_kb.py`

- [ ] **Step 1: Create the test file with `_prepare_kb_context` tests**

```python
# tests/unit/test_literature_survey_kb.py
"""Unit tests for LiteratureSurveyRAGMode KB-context and reference-storage methods.

All ChromaDB, retriever, and session-store calls are mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _make_mode():
    """Return a LiteratureSurveyRAGMode with default Config (no external services)."""
    from perspicacite.config.schema import Config
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    return LiteratureSurveyRAGMode(Config())


def _fake_request(kb_names: list[str], query: str = "protein folding"):
    req = MagicMock()
    req.kb_names = kb_names
    req.kb_name = kb_names[0] if kb_names else "default"
    req.query = query
    return req


# ── _prepare_kb_context ──────────────────────────────────────────────────────

async def test_prepare_kb_context_noop_without_kb_names():
    mode = _make_mode()
    ctx, ids = await mode._prepare_kb_context(
        _fake_request([]), MagicMock(), MagicMock()
    )
    assert ctx == ""
    assert ids == set()


async def test_prepare_kb_context_collects_paper_ids_from_chromadb():
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(
        return_value=[
            ("doi:10.1/a", "Paper A", 3),
            ("doi:10.1/b", "Paper B", 2),
        ]
    )
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[])
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, ids = await mode._prepare_kb_context(
            _fake_request(["kb-a"]), mock_vs, MagicMock()
        )
    assert "doi:10.1/a" in ids
    assert "doi:10.1/b" in ids


async def test_prepare_kb_context_builds_context_block_from_retriever():
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(return_value=[])

    fake_meta = MagicMock()
    fake_meta.title = "AlphaFold"
    fake_meta.year = 2021
    fake_meta.doi = "10.1038/s41586-021-03819-2"
    fake_result = {
        "paper_id": "doi:10.1038/s41586",
        "kb_name": "biology-kb",
        "metadata": fake_meta,
    }

    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[fake_result])
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, ids = await mode._prepare_kb_context(
            _fake_request(["biology-kb"]), mock_vs, MagicMock()
        )
    assert "AlphaFold" in ctx
    assert "biology-kb" in ctx


async def test_prepare_kb_context_returns_empty_context_on_retrieval_error():
    """Even if retriever raises, known_ids (from ChromaDB listing) should still return."""
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(
        return_value=[("doi:10.1/a", "Paper A", 1)]
    )
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(side_effect=RuntimeError("embed crash"))
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, ids = await mode._prepare_kb_context(
            _fake_request(["kb-a"]), mock_vs, MagicMock()
        )
    assert ctx == ""         # context block empty on retriever error
    assert "doi:10.1/a" in ids  # IDs still collected from ChromaDB
```

- [ ] **Step 2: Run tests — expect AttributeError (method not yet added)**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py -v -k "prepare_kb_context"
```

Expected: 4 failures with `AttributeError: '_prepare_kb_context'`

- [ ] **Step 3: Add `session_store` attribute to `LiteratureSurveyRAGMode.__init__`**

In `src/perspicacite/rag/modes/literature_survey.py`, find `__init__` (line 139). After `self.sessions: dict[str, SurveySession] = {}` add:

```python
# Injected by RAGEngine when a SessionStore is available.
# Used by _store_references_to_all_kbs to write reference rows.
self.session_store: Any = None
```

- [ ] **Step 4: Add `_prepare_kb_context()` method to `LiteratureSurveyRAGMode`**

Add this method after `_convert_to_candidates()` (around line 436):

```python
async def _prepare_kb_context(
    self,
    request: Any,
    vector_store: Any,
    embedding_provider: Any,
    top_k: int = 10,
) -> tuple[str, set[str]]:
    """Retrieve known papers from all provided KBs.

    Performs two operations:
    1. Fetches ALL paper_ids from every KB's ChromaDB collection (for
       pre-filtering broad search results).
    2. Runs a semantic top-K search across KBs (via _build_kb_retriever)
       and formats a human-readable context block for the survey summary.

    Returns:
        context_block: Formatted string listing known papers (for summary).
        all_known_ids: Full set of paper_ids/DOIs already in any provided KB.

    Both return values are empty if kb_names is absent or empty.
    Never raises — errors are caught and logged.
    """
    from perspicacite.models.kb import chroma_collection_name_for_kb

    kb_names: list[str] = list(getattr(request, "kb_names", None) or [])
    if not kb_names:
        return "", set()

    # ── A. Collect ALL paper_ids from ChromaDB across every KB ──────────────
    all_known_ids: set[str] = set()
    for kb_name in kb_names:
        col = chroma_collection_name_for_kb(kb_name)
        try:
            rows = await vector_store.list_paper_ids_in_collection(col)
            # rows: list[tuple[paper_id, title, chunk_count]]
            all_known_ids.update(pid for pid, _, _ in rows)
        except Exception as exc:
            logger.warning(
                "survey_kb_id_fetch_error", kb=kb_name, error=str(exc)
            )

    # ── B. Semantic top-K retrieval for the context block ───────────────────
    context_block = ""
    try:
        retriever = self._build_kb_retriever(request, vector_store, embedding_provider)
        results = await retriever.search(request.query, top_k=top_k)
        if results:
            lines: list[str] = []
            seen_pids: set[str] = set()
            for r in results:
                pid = r.get("paper_id") or ""
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)
                meta = r.get("metadata")
                title = (getattr(meta, "title", None) or "Unknown title")
                year = getattr(meta, "year", None) or ""
                doi = getattr(meta, "doi", None) or ""
                kb_tag = r.get("kb_name") or ""
                line = f"- {title} ({year})"
                if kb_tag:
                    line += f" [KB: {kb_tag}]"
                if doi:
                    line += f" DOI: {doi}"
                lines.append(line)
            if lines:
                context_block = (
                    "Papers already in your knowledge base(s) — "
                    "excluded from new-paper analysis:\n"
                    + "\n".join(lines)
                )
    except Exception as exc:
        logger.warning("survey_kb_context_retrieval_error", error=str(exc))

    logger.info(
        "survey_kb_context_prepared",
        known_ids_total=len(all_known_ids),
        context_lines=len(context_block.splitlines()),
        kb_names=kb_names,
    )
    return context_block, all_known_ids
```

- [ ] **Step 5: Run tests — expect 4 to pass**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py -v -k "prepare_kb_context"
```

Expected: 4 passed

- [ ] **Step 6: Run full unit suite**

```bash
uv run pytest tests/unit/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/rag/modes/literature_survey.py tests/unit/test_literature_survey_kb.py
git commit -m "feat: add _prepare_kb_context() to LiteratureSurveyRAGMode"
```

---

### Task 3: `_store_references_to_all_kbs()` method

**Files:**
- Modify: `src/perspicacite/rag/modes/literature_survey.py`
- Modify: `tests/unit/test_literature_survey_kb.py`

- [ ] **Step 1: Add `_store_references_to_all_kbs` tests to the test file**

Append to `tests/unit/test_literature_survey_kb.py`:

```python
# ── _store_references_to_all_kbs ─────────────────────────────────────────────

async def test_store_references_noop_without_session_store():
    mode = _make_mode()
    mode.session_store = None
    result = await mode._store_references_to_all_kbs([], ["kb-a", "kb-b"], "query")
    assert result == 0


async def test_store_references_noop_with_single_kb():
    mode = _make_mode()
    mock_store = AsyncMock()
    mode.session_store = mock_store
    result = await mode._store_references_to_all_kbs([], ["kb-a"], "query")
    assert result == 0
    mock_store.store_paper_reference.assert_not_called()


async def test_store_references_skips_primary_kb():
    mode = _make_mode()
    mock_store = AsyncMock()
    mock_store.store_paper_reference = AsyncMock(return_value=True)
    mode.session_store = mock_store

    paper = MagicMock()
    paper.doi = "10.1/test"
    paper.title = "Test Paper"
    paper.authors = ["Author A"]
    paper.year = 2021
    paper.abstract = "Abstract text."

    await mode._store_references_to_all_kbs([paper], ["primary-kb", "extra-kb"], "q")

    call_kb_names = [c.kwargs["kb_name"] for c in mock_store.store_paper_reference.call_args_list]
    assert "primary-kb" not in call_kb_names
    assert "extra-kb" in call_kb_names


async def test_store_references_skips_papers_without_doi():
    mode = _make_mode()
    mock_store = AsyncMock()
    mock_store.store_paper_reference = AsyncMock(return_value=True)
    mode.session_store = mock_store

    paper = MagicMock()
    paper.doi = None
    paper.title = "No DOI Paper"
    paper.authors = []
    paper.year = 2021
    paper.abstract = "Abstract."

    result = await mode._store_references_to_all_kbs([paper], ["kb-a", "kb-b"], "q")
    mock_store.store_paper_reference.assert_not_called()
    assert result == 0


async def test_store_references_returns_correct_count():
    mode = _make_mode()
    mock_store = AsyncMock()
    mock_store.store_paper_reference = AsyncMock(return_value=True)
    mode.session_store = mock_store

    papers = [
        MagicMock(doi="10.1/a", title="A", authors=[], year=2020, abstract=""),
        MagicMock(doi="10.1/b", title="B", authors=[], year=2021, abstract=""),
    ]
    # primary + 2 extra KBs; 2 papers × 2 extra KBs = 4 new rows
    result = await mode._store_references_to_all_kbs(
        papers, ["primary", "extra-1", "extra-2"], "q"
    )
    assert result == 4
```

- [ ] **Step 2: Run new tests — expect failures**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py -v -k "store_references"
```

Expected: 5 failures (method not yet defined)

- [ ] **Step 3: Add `_store_references_to_all_kbs()` to `LiteratureSurveyRAGMode`**

Add this method right after `_prepare_kb_context()` in `literature_survey.py`:

```python
async def _store_references_to_all_kbs(
    self,
    papers: list[Any],
    kb_names: list[str],
    survey_query: str,
) -> int:
    """Store reference rows in SQLite for every KB beyond the first.

    ``kb_names[0]`` (the primary KB) already receives full ingestion via the
    existing ``add_paper_to_kb`` path.  Indices 1..n receive a lightweight
    ``kb_paper_references`` row per paper so a future ``add_dois_to_kb`` /
    rebuild can fully ingest them.

    Only papers with a non-null ``doi`` are stored (papers without a DOI
    cannot be looked up by a future ingestion command anyway).

    Returns the total number of NEW rows written.
    Never raises.
    """
    if self.session_store is None or len(kb_names) < 2:
        return 0

    extra_kbs = kb_names[1:]
    total = 0
    query_snippet = str(survey_query)[:200]

    for kb_name in extra_kbs:
        for paper in papers:
            doi = getattr(paper, "doi", None)
            if not doi:
                continue  # skip: no DOI means can't re-ingest via add_dois_to_kb
            try:
                authors = list(getattr(paper, "authors", []) or [])
                abstract_raw = getattr(paper, "abstract", None)
                abstract = abstract_raw[:500] if abstract_raw else None
                new = await self.session_store.store_paper_reference(
                    kb_name=kb_name,
                    doi=doi,
                    title=str(getattr(paper, "title", "") or "Untitled"),
                    authors=authors,
                    year=getattr(paper, "year", None),
                    abstract=abstract,
                    survey_query=query_snippet,
                )
                if new:
                    total += 1
                    logger.info(
                        "survey_reference_stored",
                        kb=kb_name,
                        doi=doi,
                    )
            except Exception as exc:
                logger.warning(
                    "survey_reference_store_error",
                    kb=kb_name,
                    paper=str(getattr(paper, "title", "?"))[:50],
                    error=str(exc),
                )

    logger.info(
        "survey_references_complete",
        extra_kbs=extra_kbs,
        total_new=total,
    )
    return total
```

- [ ] **Step 4: Run tests — expect 5 to pass**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py -v -k "store_references"
```

Expected: 5 passed

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/modes/literature_survey.py tests/unit/test_literature_survey_kb.py
git commit -m "feat: add _store_references_to_all_kbs() to LiteratureSurveyRAGMode"
```

---

### Task 4: Wire both methods into `execute()`, `execute_stream()`, and `_generate_interim_summary()`

**Files:**
- Modify: `src/perspicacite/rag/modes/literature_survey.py`
- Modify: `tests/unit/test_literature_survey_kb.py`

- [ ] **Step 1: Add integration-level wire-up test**

Append to `tests/unit/test_literature_survey_kb.py`:

```python
# ── Wiring: execute() calls both new methods ─────────────────────────────────

async def test_execute_calls_prepare_kb_context_and_store_references():
    """execute() should call _prepare_kb_context before search and
    _store_references_to_all_kbs after recommendations."""
    from perspicacite.models.rag import RAGMode, RAGRequest
    from perspicacite.rag.modes.literature_survey import PaperCandidate

    mode = _make_mode()

    prepare_called = []
    store_called = []

    async def fake_prepare(request, vs, ep):
        prepare_called.append(True)
        return ("", set())  # empty context, no known IDs so nothing is filtered

    async def fake_store(papers, kb_names, query):
        store_called.append(True)
        return 0

    # A minimal PaperCandidate to get the pipeline past the empty-list guard
    fake_candidate = PaperCandidate(
        id="doi:10.1/x",
        title="Paper X",
        authors=[],
        year=2021,
        abstract="Abstract text.",
        doi="10.1/x",
    )

    with (
        patch.object(mode, "_prepare_kb_context", side_effect=fake_prepare),
        patch.object(mode, "_store_references_to_all_kbs", side_effect=fake_store),
        patch.object(mode, "_broad_search", return_value=["__marker__"]),
        patch.object(mode, "_convert_to_candidates", return_value=[fake_candidate]),
        patch.object(mode, "_analyze_abstracts_batch", new=AsyncMock(return_value=[])),
        patch.object(mode, "_generate_recommendations", new=AsyncMock(return_value=None)),
    ):
        request = RAGRequest(
            query="protein folding",
            mode=RAGMode.LITERATURE_SURVEY,
            kb_name="kb-a",
            kb_names=["kb-a", "kb-b"],
        )
        await mode.execute(
            request=request,
            llm=AsyncMock(),
            vector_store=AsyncMock(),
            embedding_provider=AsyncMock(),
            tools=MagicMock(),
        )

    assert prepare_called, "_prepare_kb_context was not called"
    assert store_called, "_store_references_to_all_kbs was not called"
```

- [ ] **Step 2: Run the new test — expect failure**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py::test_execute_calls_prepare_kb_context_and_store_references -v
```

Expected: FAIL (neither method is called from `execute()` yet)

- [ ] **Step 3: Update `execute()` in `literature_survey.py`**

Locate the `execute()` method (starts at line 155). Find the multi-KB log block (lines 184–190):

```python
# REMOVE this entire block:
        # Multi-KB: literature_survey doesn't read from a KB, but if a caller
        # passed multiple kb_names for storage targeting, only the first will
        # be used. Log the decision once so it's visible in traces.
        if request.kb_names and len(request.kb_names) > 1:
            logger.info(
                "survey_multi_kb_storage",
                selected_storage_kb=_target_kb(request),
                other_kbs=list(request.kb_names[1:]),
            )
```

Replace it with:

```python
        # Prepare KB context: retrieve semantically similar papers from all
        # provided KBs and collect ALL known paper_ids for pre-filtering.
        kb_context_block, known_paper_ids = await self._prepare_kb_context(
            request, vector_store, embedding_provider
        )
```

Then find the line `papers = await self._broad_search(request.query)` (line 193). The line after it starts `if not papers:`. Insert the filter between `_broad_search` and `if not papers:`:

```python
        # Phase 1: Broad search
        logger.info("phase_1_search")
        papers = await self._broad_search(request.query)

        # Pre-filter: remove papers already in any provided KB
        if known_paper_ids and papers:
            before_count = len(papers)
            papers = [
                p for p in papers
                if (p.id not in known_paper_ids)
                and (not p.doi or p.doi not in known_paper_ids)
            ]
            filtered_count = before_count - len(papers)
            if filtered_count:
                logger.info("survey_known_papers_filtered", count=filtered_count)
```

Then find `summary = self._generate_interim_summary(session)` near the end of `execute()` (line 254). Replace with:

```python
        summary = self._generate_interim_summary(session, known_context=kb_context_block)
```

Then find the `return RAGResponse(...)` call. Insert the reference-storage call immediately before it:

```python
        # Store references to extra KBs (indices 1..n) for future re-ingestion
        all_kb_names = list(request.kb_names or [request.kb_name])
        recommended_papers = [p for p in session.papers if p.recommended]
        await self._store_references_to_all_kbs(
            recommended_papers, all_kb_names, request.query
        )

        return RAGResponse(
```

- [ ] **Step 4: Apply the same changes to `execute_stream()`**

Locate `execute_stream()` (starts at line 269). Find the multi-KB block (lines 287–293):

```python
# REMOVE:
        # Multi-KB: literature_survey doesn't read from a KB, but if a caller
        # passed multiple kb_names for storage targeting, only the first will
        # be used. Log the decision once so it's visible in traces.
        if request.kb_names and len(request.kb_names) > 1:
            logger.info(
                "survey_multi_kb_storage",
                selected_storage_kb=_target_kb(request),
                other_kbs=list(request.kb_names[1:]),
            )
```

Replace with:

```python
        # Prepare KB context
        kb_context_block, known_paper_ids = await self._prepare_kb_context(
            request, vector_store, embedding_provider
        )
```

Then find `papers = await self._broad_search(request.query, request.databases)` (line 298). After the `_broad_search` call and before `if not papers:`, insert:

```python
        # Pre-filter: remove papers already in any provided KB
        if known_paper_ids and papers:
            before_count = len(papers)
            papers = [
                p for p in papers
                if (p.id not in known_paper_ids)
                and (not p.doi or p.doi not in known_paper_ids)
            ]
            filtered_count = before_count - len(papers)
            if filtered_count:
                logger.info("survey_known_papers_filtered", count=filtered_count)
```

Then find `summary = self._generate_interim_summary(session)` (line 363). Replace with:

```python
        summary = self._generate_interim_summary(session, known_context=kb_context_block)
```

Then find `yield StreamEvent.done(...)` near the end of `execute_stream()` (line 378). Insert the reference-storage call immediately before it:

```python
        # Store references to extra KBs
        all_kb_names = list(request.kb_names or [request.kb_name])
        recommended_papers = [p for p in session.papers if p.recommended]
        await self._store_references_to_all_kbs(
            recommended_papers, all_kb_names, request.query
        )

        yield StreamEvent.done(
```

- [ ] **Step 5: Update `_generate_interim_summary()` to accept and display `known_context`**

Find the method signature (line 753):

```python
# BEFORE:
    def _generate_interim_summary(self, session: SurveySession) -> str:
```

```python
# AFTER:
    def _generate_interim_summary(
        self, session: SurveySession, known_context: str = ""
    ) -> str:
```

Find the `return "\n".join(lines)` at the end of the method (line 801). Just before it add:

```python
        if known_context:
            lines.extend([
                "",
                "---",
                "",
                "## Already in Your Knowledge Base(s)",
                "",
                known_context,
            ])
```

- [ ] **Step 6: Run the wiring test — expect pass**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py::test_execute_calls_prepare_kb_context_and_store_references -v
```

Expected: PASS

- [ ] **Step 7: Run all literature-survey KB tests**

```bash
uv run pytest tests/unit/test_literature_survey_kb.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 8: Run full unit suite**

```bash
uv run pytest tests/unit/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/perspicacite/rag/modes/literature_survey.py tests/unit/test_literature_survey_kb.py
git commit -m "feat: wire _prepare_kb_context and _store_references into survey execute paths"
```

---

### Task 5: Wire `session_store` into `RAGEngine`, `AppState`, and `MCPState`

**Files:**
- Modify: `src/perspicacite/rag/engine.py`
- Modify: `src/perspicacite/web/state.py`
- Modify: `src/perspicacite/mcp/server.py`
- Modify: `CLAUDE.md`

No new test file needed — the unit tests from Tasks 2–4 already exercise the `session_store = None` path. A smoke test verifying the attribute is set is included below.

- [ ] **Step 1: Update `RAGEngine.__init__` to accept and inject `session_store`**

In `src/perspicacite/rag/engine.py`, find `__init__` (line 34). Add `session_store: Any = None` to the signature and wire it in:

```python
# BEFORE (lines 34–67):
    def __init__(
        self,
        llm_client: AsyncLLMClient,
        vector_store: ChromaVectorStore,
        embedding_provider: EmbeddingProvider,
        tool_registry: ToolRegistry,
        config: Config,
    ):
        ...
        # Initialize mode handlers for all supported modes
        self._modes: dict[RAGMode, Any] = {
            RAGMode.BASIC: BasicRAGMode(config),
            RAGMode.ADVANCED: AdvancedRAGMode(config),
            RAGMode.PROFOUND: ProfoundRAGMode(config),
            RAGMode.AGENTIC: AgenticRAGMode(config),
            RAGMode.LITERATURE_SURVEY: LiteratureSurveyRAGMode(config),
            RAGMode.CONTRADICTION: ContradictionRAGMode(config),
        }
```

```python
# AFTER:
    def __init__(
        self,
        llm_client: AsyncLLMClient,
        vector_store: ChromaVectorStore,
        embedding_provider: EmbeddingProvider,
        tool_registry: ToolRegistry,
        config: Config,
        session_store: Any = None,
    ):
        ...
        # Build survey mode and inject session_store so _store_references_to_all_kbs
        # can write SQLite reference rows.
        survey_mode = LiteratureSurveyRAGMode(config)
        survey_mode.session_store = session_store

        # Initialize mode handlers for all supported modes
        self._modes: dict[RAGMode, Any] = {
            RAGMode.BASIC: BasicRAGMode(config),
            RAGMode.ADVANCED: AdvancedRAGMode(config),
            RAGMode.PROFOUND: ProfoundRAGMode(config),
            RAGMode.AGENTIC: AgenticRAGMode(config),
            RAGMode.LITERATURE_SURVEY: survey_mode,
            RAGMode.CONTRADICTION: ContradictionRAGMode(config),
        }
```

- [ ] **Step 2: Quick smoke test that `session_store` is injected**

```bash
python -c "
from perspicacite.config.schema import Config
from perspicacite.rag.engine import RAGEngine
from unittest.mock import MagicMock
from perspicacite.models.rag import RAGMode
e = RAGEngine(MagicMock(), MagicMock(), MagicMock(), MagicMock(), Config(), session_store='MOCK')
survey = e._modes[RAGMode.LITERATURE_SURVEY]
assert survey.session_store == 'MOCK', f'Expected MOCK, got {survey.session_store}'
print('OK: session_store injected correctly')
"
```

Expected output: `OK: session_store injected correctly`

- [ ] **Step 3: Update `AppState.initialize()` to move session-store init before `RAGEngine`**

In `src/perspicacite/web/state.py`, find lines 150–159 (RAGEngine construction) and lines 162–166 (session_store init). Move the session-store block BEFORE the RAGEngine block and pass `session_store`:

```python
# BEFORE (lines 150–166):
        # Initialize RAG engine for multi-mode support
        from perspicacite.rag.engine import RAGEngine

        self.rag_engine = RAGEngine(
            llm_client=self.llm_client,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
            tool_registry=tool_registry,
            config=config,
        )
        logger.info("RAG engine initialized (supports all modes)")

        # Initialize session store (SQLite for KB metadata)
        db_path = Path("./data/perspicacite.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_store = SessionStore(db_path)
        await self.session_store.init_db()
        logger.info("Session store initialized")
```

```python
# AFTER:
        # Initialize session store FIRST so RAGEngine can receive it
        db_path = Path("./data/perspicacite.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_store = SessionStore(db_path)
        await self.session_store.init_db()
        logger.info("Session store initialized")

        # Initialize RAG engine for multi-mode support
        from perspicacite.rag.engine import RAGEngine

        self.rag_engine = RAGEngine(
            llm_client=self.llm_client,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
            tool_registry=tool_registry,
            config=config,
            session_store=self.session_store,
        )
        logger.info("RAG engine initialized (supports all modes)")
```

- [ ] **Step 4: Update `MCPState` locally-constructed `RAGEngine` in `mcp/server.py`**

In `src/perspicacite/mcp/server.py`, find the `RAGEngine(...)` call (lines 1221–1227):

```python
# BEFORE:
        engine = RAGEngine(
            llm_client=state.llm_client,
            vector_store=state.vector_store,
            embedding_provider=state.embedding_provider,
            tool_registry=state.tool_registry,
            config=state.config,
        )
```

```python
# AFTER:
        engine = RAGEngine(
            llm_client=state.llm_client,
            vector_store=state.vector_store,
            embedding_provider=state.embedding_provider,
            tool_registry=state.tool_registry,
            config=state.config,
            session_store=getattr(state, "session_store", None),
        )
```

- [ ] **Step 5: Update `CLAUDE.md` multi-KB note**

In `CLAUDE.md`, find this line:
```
`literature_survey` accepts `kb_names` but fans retrieval across only the first KB for survey storage; full multi-KB retrieval in `literature_survey` is a tracked follow-up.
```

Replace with:
```
`literature_survey` accepts `kb_names` and now retrieves semantically similar papers from ALL provided KBs before the survey (pre-filtering already-known papers), and stores final-recommendation DOIs as reference rows in `kb_paper_references` for every KB beyond the first. These references can be ingested later via `add_dois_to_kb`.
```

- [ ] **Step 6: Run full unit test suite**

```bash
uv run pytest tests/unit/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 7: Run linter and type checker**

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Expected: no errors (fix any that appear).

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/rag/engine.py \
        src/perspicacite/web/state.py \
        src/perspicacite/mcp/server.py \
        CLAUDE.md
git commit -m "feat: inject session_store into RAGEngine and wire multi-KB survey support end-to-end"
```

---

## Summary

After all 5 tasks, the following new behaviour is active:

| What | Where |
|------|-------|
| `kb_paper_references` table + 2 `SessionStore` methods | `session_store.py` |
| `_prepare_kb_context()` — ChromaDB ID fetch + semantic top-10 context | `literature_survey.py` |
| `_store_references_to_all_kbs()` — SQLite ref rows for extra KBs | `literature_survey.py` |
| Pre-filter of known papers from broad search results | `execute()` / `execute_stream()` |
| "Already in KB" section in survey summary | `_generate_interim_summary()` |
| `RAGEngine` passes `session_store` to survey mode | `engine.py` / `state.py` / `mcp/server.py` |
