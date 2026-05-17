# Literature Survey Multi-KB Support Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add two capabilities to `LiteratureSurveyRAGMode`: (1) retrieve semantically similar papers from all provided KBs *before* the broad search, pre-filter already-known papers out of the analysis pipeline, and surface them in the survey summary; (2) store final-recommendation papers as lightweight SQLite references into every extra KB (`kb_names[1:]`) for future re-ingestion.

**Architecture:** Two private methods added directly to `LiteratureSurveyRAGMode` (Approach A — no new files). A new SQLite table `kb_paper_references` in `session_store.py`. `RAGEngine` accepts an optional `session_store` kwarg and injects it into the survey mode at construction. All changes are additive and backward-compatible; single-KB surveys are unaffected.

**Tech Stack:** `ChromaVectorStore.list_paper_ids_in_collection()` (already exists), `MultiKBRetriever` via `BaseRAGMode._build_kb_retriever()` (already exists), `aiosqlite` (already in project), `chroma_collection_name_for_kb()` from `models/kb.py`.

---

## Context

`LiteratureSurveyRAGMode` uses `SciLExAdapter` for external broad search. It accepts `kb_names` from `RAGRequest` but currently only uses the first entry (`_target_kb()`) as a storage target. The `vector_store` and `embedding_provider` arguments to `execute()` / `execute_stream()` are passed through but never used.

This design adds two orthogonal improvements to the survey pipeline without changing any existing method signatures or breaking any callers.

---

## Data Flow (Updated)

```
execute(request, llm, vector_store, embedding_provider, tools)
 │
 ├─ [NEW] _prepare_kb_context(request, vector_store, embedding_provider)
 │         ↳ semantic top-10 search across all KBs  → context_block: str
 │         ↳ list_paper_ids_in_collection per KB     → all_known_ids: set[str]
 │         Returns (context_block, all_known_ids)
 │
 ├─ _broad_search()                             ← unchanged
 │
 ├─ [NEW] filter: remove papers in all_known_ids from broad results (1 line)
 │
 ├─ _convert_to_candidates()                   ← unchanged
 ├─ _analyze_abstracts_batch()                 ← unchanged (fewer papers)
 ├─ _identify_themes()                         ← unchanged
 ├─ _generate_recommendations()                ← unchanged (rule-based)
 │
 ├─ _generate_interim_summary(session)
 │         ↳ [MODIFIED] appends "Already in your KB(s):" section
 │           using context_block when non-empty
 │
 └─ [NEW] _store_references_to_all_kbs(
             recommended_papers, kb_names[1:], survey_query
           )  ← called at end of execute/execute_stream; no-op if session_store is None
```

Both `execute()` and `execute_stream()` get the same treatment. The existing `survey_multi_kb_storage` log event is removed (now handled by the new code).

---

## Files Changed

| Action | Path | What changes |
|--------|------|--------------|
| Modify | `src/perspicacite/memory/session_store.py` | New `kb_paper_references` table in SCHEMA; two new async methods |
| Modify | `src/perspicacite/rag/engine.py` | Add optional `session_store` kwarg to `__init__`; inject into survey mode |
| Modify | `src/perspicacite/web/state.py` | Pass `session_store=self.session_store` when constructing `RAGEngine` |
| Modify | `src/perspicacite/rag/modes/literature_survey.py` | Two new private methods; call sites in `execute` / `execute_stream`; updated summary |
| Create | `tests/unit/test_literature_survey_kb.py` | 8 new unit tests (all mocked) |
| Modify | `CLAUDE.md` | Update the multi-KB follow-up note to "implemented" |

---

## Component Design

### 1. New SQLite table — `session_store.py`

Add to `SCHEMA` (before `CREATE INDEX` statements):

```sql
CREATE TABLE IF NOT EXISTS kb_paper_references (
    id         TEXT    PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
    kb_name    TEXT    NOT NULL,
    doi        TEXT,
    title      TEXT    NOT NULL,
    authors_json TEXT  DEFAULT '[]',
    year       INTEGER,
    abstract   TEXT,
    survey_query TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kb_name, doi)
);
CREATE INDEX IF NOT EXISTS idx_kb_paper_refs_kb ON kb_paper_references(kb_name);
```

The `UNIQUE(kb_name, doi)` constraint silently ignores duplicate inserts (`INSERT OR IGNORE`). Only papers with a non-null `doi` are stored (see `_store_references_to_all_kbs` below) — papers without a DOI cannot be looked up by a future `add_dois_to_kb` call anyway, and `NULL != NULL` in SQL would create duplicate rows.

### 2. New `SessionStore` methods

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
    """Write a reference-only paper record. Returns True if new, False if duplicate."""
    import json as _json
    async with aiosqlite.connect(self.db_path) as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO kb_paper_references
                (kb_name, doi, title, authors_json, year, abstract, survey_query)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (kb_name, doi, title, _json.dumps(authors), year,
             abstract, survey_query),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_paper_references(self, kb_name: str) -> list[dict[str, Any]]:
    """Return all reference-only records for a KB (for future rebuild UIs)."""
    import json as _json
    async with aiosqlite.connect(self.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM kb_paper_references WHERE kb_name = ? ORDER BY created_at DESC",
            (kb_name,),
        )
        rows = await cur.fetchall()
    return [
        {**dict(r), "authors": _json.loads(r["authors_json"] or "[]")}
        for r in rows
    ]
```

### 3. `RAGEngine.__init__` — accept `session_store`

```python
def __init__(
    self,
    llm_client: AsyncLLMClient,
    vector_store: ChromaVectorStore,
    embedding_provider: EmbeddingProvider,
    tool_registry: ToolRegistry,
    config: Config,
    session_store: Any = None,   # ← NEW (optional, default None)
) -> None:
    ...
    # Construct survey mode and inject session_store
    survey_mode = LiteratureSurveyRAGMode(config)
    survey_mode.session_store = session_store   # attribute injection

    self._modes: dict[RAGMode, Any] = {
        RAGMode.BASIC:              BasicRAGMode(config),
        RAGMode.ADVANCED:           AdvancedRAGMode(config),
        RAGMode.PROFOUND:           ProfoundRAGMode(config),
        RAGMode.AGENTIC:            AgenticRAGMode(config),
        RAGMode.LITERATURE_SURVEY:  survey_mode,   # ← use pre-built instance
        RAGMode.CONTRADICTION:      ContradictionRAGMode(config),
    }
```

### 4. `AppState.initialize()` — pass `session_store`

In `web/state.py`, find the `RAGEngine(...)` call and add `session_store=self.session_store`:

```python
self.rag_engine = RAGEngine(
    llm_client=self.llm,
    vector_store=self.vector_store,
    embedding_provider=self.embedding_provider,
    tool_registry=self.tool_registry,
    config=self.config,
    session_store=self.session_store,   # ← NEW
)
```

The `MCPState` in `mcp/server.py` also constructs a `RAGEngine`; add `session_store=self.session_store` there too.

### 5. `LiteratureSurveyRAGMode` — new attributes and methods

Add `session_store: Any = None` as an instance attribute in `__init__`:
```python
def __init__(self, config: Any):
    super().__init__(config)
    self.session_store: Any = None   # ← injected by RAGEngine
    ...
```

#### `_prepare_kb_context()`

```python
async def _prepare_kb_context(
    self,
    request: Any,         # RAGRequest
    vector_store: Any,    # ChromaVectorStore
    embedding_provider: Any,
) -> tuple[str, set[str]]:
    """
    Retrieve known papers from all KBs for context injection and pre-filtering.

    Returns:
        context_block: formatted top-10 known papers (for summary section)
        all_known_ids: full set of paper_ids across all provided KBs (for pre-filtering)
    """
    from perspicacite.models.kb import chroma_collection_name_for_kb

    kb_names: list[str] = list(getattr(request, "kb_names", None) or [])
    if not kb_names:
        return "", set()

    # ── A. Collect ALL paper_ids from ChromaDB (for pre-filtering) ──────────
    all_known_ids: set[str] = set()
    for kb_name in kb_names:
        col = chroma_collection_name_for_kb(kb_name)
        try:
            rows = await vector_store.list_paper_ids_in_collection(col)
            # rows is list[tuple[paper_id, title, chunk_count]]
            all_known_ids.update(pid for pid, _, _ in rows)
        except Exception as exc:
            logger.warning("survey_kb_id_fetch_error", kb=kb_name, error=str(exc))

    # ── B. Semantic top-10 retrieval for context block ──────────────────────
    context_block = ""
    try:
        retriever = self._build_kb_retriever(request, vector_store, embedding_provider)
        results = await retriever.search(request.query, top_k=10)
        if results:
            lines: list[str] = []
            seen: set[str] = set()
            for chunk in results:
                pid = getattr(chunk, "paper_id", None) or ""
                if pid in seen:
                    continue
                seen.add(pid)
                title = getattr(chunk, "title", None) or "Unknown"
                year = getattr(chunk, "year", None) or ""
                kb_tag = getattr(chunk, "kb_name", None) or ""
                doi = getattr(chunk, "doi", None) or ""
                line = f"- {title} ({year})"
                if kb_tag:
                    line += f" [KB: {kb_tag}]"
                if doi:
                    line += f" DOI: {doi}"
                lines.append(line)
            if lines:
                context_block = (
                    "Papers already in your knowledge base(s) — "
                    "these were excluded from analysis:\n" + "\n".join(lines)
                )
    except Exception as exc:
        logger.warning("survey_kb_context_error", error=str(exc))

    logger.info(
        "survey_kb_context_prepared",
        known_ids=len(all_known_ids),
        context_lines=context_block.count("\n"),
        kb_names=kb_names,
    )
    return context_block, all_known_ids
```

#### `_store_references_to_all_kbs()`

```python
async def _store_references_to_all_kbs(
    self,
    papers: list[Any],   # list[PaperCandidate]
    kb_names: list[str],
    survey_query: str,
) -> int:
    """Store reference rows in SQLite for all KBs beyond the primary (index 0).

    The primary KB (kb_names[0]) already receives full ingestion via the
    existing add_paper_to_kb path.  Extra KBs (indices 1..n) get a
    lightweight 'reference_only' row in kb_paper_references so a future
    add_dois_to_kb / rebuild operation can fully ingest them.

    Returns total number of NEW rows written.
    """
    if self.session_store is None or len(kb_names) < 2:
        return 0

    extra_kbs = kb_names[1:]
    total = 0
    for kb_name in extra_kbs:
        for paper in papers:
            doi = getattr(paper, "doi", None)
            if not doi:
                continue  # skip papers without DOI — can't be used in future add_dois_to_kb
            try:
                new = await self.session_store.store_paper_reference(
                    kb_name=kb_name,
                    doi=doi,
                    title=str(paper.title),
                    authors=list(getattr(paper, "authors", []) or []),
                    year=getattr(paper, "year", None),
                    abstract=(paper.abstract[:500] if paper.abstract else None),
                    survey_query=survey_query[:200],
                )
                if new:
                    total += 1
                    logger.info(
                        "survey_reference_stored",
                        kb=kb_name,
                        doi=getattr(paper, "doi", None) or paper.id,
                    )
            except Exception as exc:
                logger.warning(
                    "survey_reference_store_error",
                    kb=kb_name,
                    paper=str(paper.title)[:50],
                    error=str(exc),
                )
    logger.info(
        "survey_references_complete",
        extra_kbs=extra_kbs,
        total_new=total,
    )
    return total
```

#### Call sites in `execute()` and `execute_stream()`

Replace the existing `survey_multi_kb_storage` multi-KB warning block at the top of each method with:

```python
# Prepare KB context: retrieve known papers + collect all known IDs
context_block, known_ids = await self._prepare_kb_context(
    request, vector_store, embedding_provider
)
```

After `_broad_search()` returns `papers`, add:

```python
# Pre-filter: remove papers already in any provided KB
if known_ids:
    before = len(papers)
    papers = [p for p in papers if p.id not in known_ids and p.doi not in known_ids]
    filtered = before - len(papers)
    if filtered:
        logger.info("survey_known_papers_filtered", count=filtered)
```

Pass `context_block` to `_generate_interim_summary()`:

```python
summary = self._generate_interim_summary(session, known_context=context_block)
```

At the end of `execute()` (before `return`), and at the end of `execute_stream()` (before the final `yield StreamEvent.done(...)`):

```python
# Store references to extra KBs
recommended = [p for p in session.papers if p.recommended]
await self._store_references_to_all_kbs(
    recommended,
    list(request.kb_names or [request.kb_name]),
    request.query,
)
```

#### `_generate_interim_summary()` signature update

```python
def _generate_interim_summary(
    self,
    session: SurveySession,
    known_context: str = "",
) -> str:
    ...
    # After the existing "## Recommended Papers" section, append:
    if known_context:
        lines.extend(["", "---", "", "## Already in Your Knowledge Base(s)", "", known_context])
    return "\n".join(lines)
```

---

## Error Handling

| Failure | Behaviour |
|---------|-----------|
| `list_paper_ids_in_collection` raises (collection missing, ChromaDB down) | Log `survey_kb_id_fetch_error`, skip that KB, continue with partial `all_known_ids` |
| `_build_kb_retriever` / `retriever.search` raises | Log `survey_kb_context_error`, `context_block = ""` — survey continues without context |
| Pre-filter leaves 0 papers (all known) | Survey continues to `_convert_to_candidates` → empty → returns "No papers found" response |
| `store_paper_reference` raises for one paper/KB | Log `survey_reference_store_error`, continue to next paper/KB — partial writes are fine |
| `session_store` is None (standalone / MCP without session store) | `_store_references_to_all_kbs` returns 0 immediately |

---

## Testing — `tests/unit/test_literature_survey_kb.py`

All tests are mocked (no ChromaDB, no SQLite, no network).

```python
# 1 — _prepare_kb_context returns context_block and known_ids set
async def test_prepare_kb_context_returns_context_and_ids():
    ...  # mock list_paper_ids_in_collection, mock retriever.search
    ctx, ids = await mode._prepare_kb_context(request, vector_store, embedding)
    assert "KB:" in ctx
    assert len(ids) > 0

# 2 — no-op when kb_names is empty
async def test_prepare_kb_context_noop_without_kb_names():
    ctx, ids = await mode._prepare_kb_context(fake_request(kb_names=[]), ...)
    assert ctx == "" and ids == set()

# 3 — returns empty context_block when retriever raises, but still returns known_ids
async def test_prepare_kb_context_returns_ids_despite_retrieval_error():
    ...  # retriever.search raises; list_paper_ids works
    ctx, ids = await mode._prepare_kb_context(...)
    assert ctx == ""
    assert len(ids) > 0  # IDs still collected

# 4 — pre-filter removes known papers from broad results
def test_prefilter_removes_known_papers():
    papers = [fake_paper(id="doi:10.1/known"), fake_paper(id="doi:10.1/new")]
    known_ids = {"doi:10.1/known"}
    filtered = [p for p in papers if p.id not in known_ids and p.doi not in known_ids]
    assert len(filtered) == 1
    assert filtered[0].id == "doi:10.1/new"

# 5 — _store_references_to_all_kbs skips primary KB (index 0)
async def test_store_references_skips_primary_kb():
    mode.session_store = mock_store
    await mode._store_references_to_all_kbs(papers, ["primary", "extra"], "query")
    # assert store_paper_reference called with kb_name="extra" only, not "primary"

# 6 — _store_references_to_all_kbs returns 0 when session_store is None
async def test_store_references_noop_without_session_store():
    mode.session_store = None
    result = await mode._store_references_to_all_kbs(papers, ["kb1", "kb2"], "q")
    assert result == 0

# 7 — _store_references_to_all_kbs returns 0 when only one KB provided
async def test_store_references_noop_with_single_kb():
    mode.session_store = mock_store
    result = await mode._store_references_to_all_kbs(papers, ["only-kb"], "q")
    assert result == 0

# 8 — SessionStore.store_paper_reference returns False for duplicate (same kb_name+doi)
async def test_session_store_reference_dedup():
    ...  # uses in-memory SQLite
    first = await store.store_paper_reference("kb-a", "10.1/x", "Title", [], 2021, "abs")
    second = await store.store_paper_reference("kb-a", "10.1/x", "Title", [], 2021, "abs")
    assert first is True
    assert second is False
```

---

## Non-Goals

- No new API endpoint for listing `kb_paper_references` (future work)
- No automatic re-ingestion trigger when references are stored
- No changes to how the primary KB (`kb_names[0]`) receives full content ingestion
- No change to the `_generate_recommendations()` logic (remains rule-based)
- No pre-filtering when `kb_names` is empty or only one entry (existing behavior unchanged)
