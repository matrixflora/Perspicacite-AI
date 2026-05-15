# PaperSource WEB_SEARCH Adapter Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the audit 2026-05-15 follow-up — replace every remaining `PaperSource.WEB_SEARCH` default in `src/` with a domain-correct enum value (introducing one new value, `SEMANTIC_SCHOLAR`), pinned by unit tests so the regression cannot return. Add a live Mistral `codestral-embed` smoke test.

**Architecture:**
- Each `PaperSource.WEB_SEARCH` call-site is reviewed for what *actually* produced the paper (unified pipeline → OPENALEX; Semantic Scholar API → SEMANTIC_SCHOLAR; user-supplied dicts via MCP/web → USER_UPLOAD; internal stub → LOCAL).
- One new enum value (`SEMANTIC_SCHOLAR = "semantic_scholar"`) is added — every other migration uses an existing value.
- One adapter-migration pin test per call-site, extending the existing `tests/unit/test_paper_source_adapter_migration.py`.
- A final grep-based invariant test prevents new `WEB_SEARCH` defaults from re-entering production code.
- Live integration test for `mistral/codestral-embed` via the existing `LiteLLMEmbeddingProvider` (requires `MISTRAL_API_KEY`).

**Tech Stack:** Python 3.11, pydantic v2, pytest, pytest-asyncio, httpx, litellm.

**Migration map (verified via grep at plan-time):**

| File:line | Function | Current | Target | Rationale |
|---|---|---|---|---|
| `src/perspicacite/search/semantic_scholar.py:140` | `_map_s2_response` | WEB_SEARCH | **SEMANTIC_SCHOLAR** (new) | Direct S2 API hit |
| `src/perspicacite/rag/agentic/orchestrator.py:1142` | `_try_resolve_url` (unified pipeline branch) | WEB_SEARCH | **OPENALEX** | Discovery via OpenAlex/CrossRef |
| `src/perspicacite/rag/agentic/orchestrator.py:1174` | `_try_resolve_url` (S2 fallback branch) | WEB_SEARCH | **SEMANTIC_SCHOLAR** | Calls `semantic_scholar.lookup_paper` |
| `src/perspicacite/mcp/server.py:721` | `add_papers_to_kb` | WEB_SEARCH | **USER_UPLOAD** | Caller supplies paper dicts |
| `src/perspicacite/mcp/server.py:1188` | `add_dois_to_kb` | WEB_SEARCH | **OPENALEX** | `retrieve_paper_content` (unified pipeline) |
| `src/perspicacite/web/routers/kb.py:335` | `_dois_ingest_worker` (async) | WEB_SEARCH | **OPENALEX** | `retrieve_paper_content` |
| `src/perspicacite/web/routers/kb.py:564` | `add_papers_to_kb` route | WEB_SEARCH | **USER_UPLOAD** | Caller supplies paper dicts via REST |
| `src/perspicacite/web/routers/kb.py:1062` | `add_dois_to_kb` route (sync) | WEB_SEARCH | **OPENALEX** | `retrieve_paper_content` |
| `src/perspicacite/pipeline/search_to_kb.py:571` | `ingest_dois_into_kb` (CLI) | WEB_SEARCH | **OPENALEX** | `retrieve_paper_content` with checkpoint |
| `src/perspicacite/rag/chunking.py:79` | `AdvancedChunkerAdapter.chunk_text_async` stub | WEB_SEARCH | **LOCAL** | In-memory transient, never persisted |

**Out of scope (deliberately left alone):**
- `src/perspicacite/models/papers.py:173` — `normalize_paper_dict(source=WEB_SEARCH)` default parameter. This is a legacy fallback for callers that don't pass `source=`. Migrated callers already override it. Leave as-is.
- `tests/unit/test_url_prefetch.py`, `tests/integration/test_persistence_integrity.py`, `tests/integration/test_perf_baseline.py`, `tests/audit/run_full_pipeline_audit.py`, `tests/e2e/conftest.py`, `tests/unit/test_cli_pubmed.py` — these are tests that still legitimately reference `PaperSource.WEB_SEARCH` as input or expectation. They will need follow-up updates as part of broader test cleanup (separate task), but updating them now would mix concerns. The migration pin tests run alongside the legacy tests and either pass or the legacy expectations need updating (we update each as we touch its production code).

---

### Task 1: Add `PaperSource.SEMANTIC_SCHOLAR` enum value

**Files:**
- Modify: `src/perspicacite/models/papers.py:10-27`
- Modify: `tests/unit/test_paper_source_enum.py`

- [ ] **Step 1.1: Extend the existing enum pin test with SEMANTIC_SCHOLAR assertions**

Edit `tests/unit/test_paper_source_enum.py` and add the two assertions to the existing tests:

In `test_enum_has_new_database_values()` add:
```python
    assert PaperSource.SEMANTIC_SCHOLAR.value == "semantic_scholar"
```

In `test_enum_constructs_from_string_for_chroma_roundtrip()` add:
```python
    assert PaperSource("semantic_scholar") is PaperSource.SEMANTIC_SCHOLAR
```

- [ ] **Step 1.2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_paper_source_enum.py -v`
Expected: FAIL with `AttributeError: SEMANTIC_SCHOLAR` (the enum value doesn't exist yet).

- [ ] **Step 1.3: Add the enum value**

Edit `src/perspicacite/models/papers.py` and insert `SEMANTIC_SCHOLAR = "semantic_scholar"` into the `PaperSource` enum, after `CROSSREF`. Also update the docstring to mention the new value.

```python
class PaperSource(str, Enum):
    """Source of a paper.

    Legacy values (BIBTEX, SCILEX, WEB_SEARCH, USER_UPLOAD,
    CITATION_FOLLOW, LOCAL) are kept for backward compat.
    Audit 2026-05-15 finding #5 added explicit database sources
    (OPENALEX, PUBMED, ARXIV, CROSSREF). The 2026-05-15 follow-up
    migration added SEMANTIC_SCHOLAR for direct S2 API hits.
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
    SEMANTIC_SCHOLAR = "semantic_scholar"
```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_paper_source_enum.py -v`
Expected: 3 PASSED.

- [ ] **Step 1.5: Commit**

```bash
git add src/perspicacite/models/papers.py tests/unit/test_paper_source_enum.py
git commit -m "$(cat <<'EOF'
feat(models): PaperSource.SEMANTIC_SCHOLAR for direct S2 API hits

Adds the enum value before migrating the adapters that need it.
Pin test in test_paper_source_enum.py covers the string round-trip
that chroma_store.py:599 relies on.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Migrate `semantic_scholar.py` → SEMANTIC_SCHOLAR

**Files:**
- Modify: `src/perspicacite/search/semantic_scholar.py:140`
- Modify: `tests/unit/test_paper_source_adapter_migration.py` (extend)

- [ ] **Step 2.1: Add the failing pin test**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
@pytest.mark.asyncio
async def test_semantic_scholar_lookup_uses_ss_enum(monkeypatch):
    """semantic_scholar.lookup_paper() calls the S2 API; the returned
    Paper must carry source=SEMANTIC_SCHOLAR (not WEB_SEARCH)."""
    from perspicacite.search.semantic_scholar import lookup_paper

    sample = {
        "paperId": "s2id123",
        "title": "Attention Is All You Need",
        "abstract": "We propose a new simple network architecture...",
        "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        "year": 2017,
        "externalIds": {"DOI": "10.48550/arXiv.1706.03762", "ArXiv": "1706.03762"},
        "citationCount": 100000,
        "venue": "NeurIPS",
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
        "url": "https://www.semanticscholar.org/paper/s2id123",
    }

    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=sample, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    paper = await lookup_paper("10.48550/arXiv.1706.03762")
    assert paper is not None
    assert paper.source is PaperSource.SEMANTIC_SCHOLAR
```

- [ ] **Step 2.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_semantic_scholar_lookup_uses_ss_enum -v`
Expected: FAIL with `AssertionError` — `paper.source is PaperSource.WEB_SEARCH`, not SEMANTIC_SCHOLAR.

- [ ] **Step 2.3: Apply the migration**

In `src/perspicacite/search/semantic_scholar.py:140`, change:
```python
        source=PaperSource.WEB_SEARCH,
```
to:
```python
        source=PaperSource.SEMANTIC_SCHOLAR,
```

- [ ] **Step 2.4: Run the test to verify it passes**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_semantic_scholar_lookup_uses_ss_enum -v`
Expected: PASS.

- [ ] **Step 2.5: Run all adapter-migration pin tests**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py -v`
Expected: all PASS (the existing 2 + the new one).

- [ ] **Step 2.6: Commit**

```bash
git add src/perspicacite/search/semantic_scholar.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(search): semantic_scholar.lookup_paper → PaperSource.SEMANTIC_SCHOLAR

Direct S2 API hits no longer mis-label as WEB_SEARCH. Pinned by
test_semantic_scholar_lookup_uses_ss_enum.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Migrate `rag/chunking.py` stub Paper → LOCAL

**Files:**
- Modify: `src/perspicacite/rag/chunking.py:79`
- Modify: `tests/unit/test_paper_source_adapter_migration.py`

The stub is an in-memory Paper created only to satisfy `AdvancedChunker.chunk_text(text, paper, ...)`. It is never persisted, never embedded — only its title/id are used for chunk metadata stamping. `LOCAL` correctly says "no external source."

- [ ] **Step 3.1: Add the failing pin test**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
@pytest.mark.asyncio
async def test_chunking_stub_paper_uses_local(monkeypatch):
    """AdvancedChunkerAdapter builds an internal Paper stub for the
    chunker. That stub must carry source=LOCAL (not WEB_SEARCH) — it is
    a transient, not a search result."""
    from perspicacite.rag.chunking import AdvancedChunkerAdapter

    captured = {}

    async def fake_chunk_text(self_chunker, text, paper, llm_client=None):
        captured["paper_source"] = paper.source
        # Return a single DocumentChunk-like object with a .text attr
        from types import SimpleNamespace
        return [SimpleNamespace(text=text)]

    monkeypatch.setattr(
        "perspicacite.pipeline.chunking_advanced.AdvancedChunker.chunk_text",
        fake_chunk_text,
    )

    adapter = AdvancedChunkerAdapter(method="semantic", chunk_size=100, overlap=20)
    out = await adapter.chunk_text_async("hello world")
    assert out == ["hello world"]
    assert captured["paper_source"] is PaperSource.LOCAL
```

- [ ] **Step 3.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_chunking_stub_paper_uses_local -v`
Expected: FAIL — `paper.source is PaperSource.WEB_SEARCH`, not LOCAL.

- [ ] **Step 3.3: Apply the migration**

In `src/perspicacite/rag/chunking.py:79`, change:
```python
            source=PaperSource.WEB_SEARCH,
```
to:
```python
            source=PaperSource.LOCAL,
```

- [ ] **Step 3.4: Run the test to verify it passes**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_chunking_stub_paper_uses_local -v`
Expected: PASS.

- [ ] **Step 3.5: Run full chunking-related tests to confirm no regressions**

Run: `pytest tests/unit/test_chunking_dispatch.py tests/unit/test_chunking_decorator_kinds.py -v`
Expected: all PASS.

- [ ] **Step 3.6: Commit**

```bash
git add src/perspicacite/rag/chunking.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(rag): chunker stub Paper → PaperSource.LOCAL

The Paper instance built inside AdvancedChunkerAdapter is a transient
metadata bearer, never persisted. LOCAL is more accurate than the
legacy WEB_SEARCH default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Migrate `mcp/server.py` (2 sites)

**Files:**
- Modify: `src/perspicacite/mcp/server.py:721` (`add_papers_to_kb`)
- Modify: `src/perspicacite/mcp/server.py:1188` (`add_dois_to_kb`)
- Modify: `tests/unit/test_paper_source_adapter_migration.py`

- [ ] **Step 4.1: Add the failing pin tests**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
def test_mcp_add_papers_to_kb_uses_user_upload():
    """The MCP add_papers_to_kb tool accepts user-supplied paper dicts —
    these come from an external client, so the source must be
    USER_UPLOAD (not the legacy WEB_SEARCH)."""
    # Read the source file and assert the call-site uses USER_UPLOAD
    # (full integration would need a running MCP server; this guards
    # against accidental reverts at the syntactic level).
    import inspect
    from perspicacite.mcp import server

    src = inspect.getsource(server)
    # The add_papers_to_kb tool constructs Paper(..., source=PaperSource.USER_UPLOAD, ...)
    assert "source=PaperSource.USER_UPLOAD" in src, (
        "mcp/server.py add_papers_to_kb must build papers with "
        "PaperSource.USER_UPLOAD"
    )
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "mcp/server.py must not default to PaperSource.WEB_SEARCH anymore"
    )


def test_mcp_add_dois_to_kb_uses_openalex():
    """The MCP add_dois_to_kb tool fetches via retrieve_paper_content
    (unified pipeline); OpenAlex is the discovery source."""
    import inspect
    from perspicacite.mcp import server

    src = inspect.getsource(server)
    # add_dois_to_kb constructs Paper(..., source=PaperSource.OPENALEX, ...)
    assert "source=PaperSource.OPENALEX" in src, (
        "mcp/server.py add_dois_to_kb must build papers with "
        "PaperSource.OPENALEX"
    )
```

Note: The `WEB_SEARCH not in src` assertion in the first test is the strong invariant; the second test relies on the union of both call-sites being covered. Both sites get migrated together so both assertions hold.

- [ ] **Step 4.2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_mcp_add_papers_to_kb_uses_user_upload tests/unit/test_paper_source_adapter_migration.py::test_mcp_add_dois_to_kb_uses_openalex -v`
Expected: both FAIL — current source contains `source=PaperSource.WEB_SEARCH`.

- [ ] **Step 4.3: Apply both migrations**

In `src/perspicacite/mcp/server.py:721`, change:
```python
                source=PaperSource.WEB_SEARCH,
```
to:
```python
                source=PaperSource.USER_UPLOAD,
```

In `src/perspicacite/mcp/server.py:1188`, change:
```python
                    source=PaperSource.WEB_SEARCH,
```
to:
```python
                    source=PaperSource.OPENALEX,
```

- [ ] **Step 4.4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_mcp_add_papers_to_kb_uses_user_upload tests/unit/test_paper_source_adapter_migration.py::test_mcp_add_dois_to_kb_uses_openalex -v`
Expected: both PASS.

- [ ] **Step 4.5: Smoke-check that mcp/server.py still imports**

Run: `python -c "from perspicacite.mcp import server; print('ok')"`
Expected: `ok`.

- [ ] **Step 4.6: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(mcp): server tools — USER_UPLOAD + OPENALEX instead of WEB_SEARCH

add_papers_to_kb now stamps caller-supplied dicts as USER_UPLOAD;
add_dois_to_kb stamps unified-pipeline output as OPENALEX. Pinned by
two new tests in test_paper_source_adapter_migration.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Migrate `web/routers/kb.py` (3 sites)

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py:335` (`_dois_ingest_worker`)
- Modify: `src/perspicacite/web/routers/kb.py:564` (`add_papers_to_kb` route)
- Modify: `src/perspicacite/web/routers/kb.py:1062` (`add_dois_to_kb` route, sync)
- Modify: `tests/unit/test_paper_source_adapter_migration.py`

- [ ] **Step 5.1: Add the failing pin tests**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
def test_kb_router_uses_correct_enum_values():
    """src/perspicacite/web/routers/kb.py has three Paper-construction
    sites that previously defaulted to WEB_SEARCH:

    - _dois_ingest_worker (line ~335) — async DOI ingest → OPENALEX
    - add_papers_to_kb route (line ~564) — user paper dicts → USER_UPLOAD
    - add_dois_to_kb route (line ~1062) — sync DOI ingest → OPENALEX

    This invariant test guards all three at once via source scan.
    """
    import inspect
    from perspicacite.web.routers import kb

    src = inspect.getsource(kb)
    # Must NOT contain the legacy default anywhere
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "web/routers/kb.py must not default any Paper to WEB_SEARCH"
    )
    # Must contain the new values (count = at least one each)
    assert src.count("source=PaperSource.USER_UPLOAD") >= 1, (
        "kb.py add_papers_to_kb must use USER_UPLOAD"
    )
    assert src.count("source=PaperSource.OPENALEX") >= 2, (
        "kb.py must use OPENALEX for both DOI-ingest paths (sync + async)"
    )
```

- [ ] **Step 5.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_kb_router_uses_correct_enum_values -v`
Expected: FAIL (legacy WEB_SEARCH still present).

- [ ] **Step 5.3: Apply all three migrations**

In `src/perspicacite/web/routers/kb.py:335`:
```python
                source=PaperSource.WEB_SEARCH,
```
→
```python
                source=PaperSource.OPENALEX,
```

In `src/perspicacite/web/routers/kb.py:564`:
```python
            source=PaperSource.WEB_SEARCH,
```
→
```python
            source=PaperSource.USER_UPLOAD,
```

In `src/perspicacite/web/routers/kb.py:1062`:
```python
            source=PaperSource.WEB_SEARCH,
```
→
```python
            source=PaperSource.OPENALEX,
```

(Use `Edit` tool with unique surrounding context — three of these patterns are textually identical, so include 1-2 surrounding lines to disambiguate each.)

- [ ] **Step 5.4: Run the test to verify it passes**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_kb_router_uses_correct_enum_values -v`
Expected: PASS.

- [ ] **Step 5.5: Smoke-check the router still imports**

Run: `python -c "from perspicacite.web.routers import kb; print('ok')"`
Expected: `ok`.

- [ ] **Step 5.6: Run any related route tests**

Run: `pytest tests/unit/ -k "kb_router or kb_route or local_docs_capsule" -v`
Expected: same pass/fail state as `main` (no new failures introduced by this task).

- [ ] **Step 5.7: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(web): kb router — USER_UPLOAD + OPENALEX instead of WEB_SEARCH

Three call-sites migrated: _dois_ingest_worker, add_papers_to_kb,
add_dois_to_kb. Caller-supplied paper dicts → USER_UPLOAD;
retrieve_paper_content output → OPENALEX. Pinned by
test_kb_router_uses_correct_enum_values.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Migrate `pipeline/search_to_kb.py` → OPENALEX

**Files:**
- Modify: `src/perspicacite/pipeline/search_to_kb.py:571`
- Modify: `tests/unit/test_paper_source_adapter_migration.py`

- [ ] **Step 6.1: Add the failing pin test**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
def test_search_to_kb_ingest_dois_uses_openalex():
    """pipeline/search_to_kb.ingest_dois_into_kb fetches via the unified
    download pipeline; built Papers must carry source=OPENALEX."""
    import inspect
    from perspicacite.pipeline import search_to_kb

    src = inspect.getsource(search_to_kb)
    assert "source=PaperSource.OPENALEX" in src, (
        "pipeline/search_to_kb.py must use PaperSource.OPENALEX"
    )
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "pipeline/search_to_kb.py must not default to WEB_SEARCH"
    )
```

- [ ] **Step 6.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_search_to_kb_ingest_dois_uses_openalex -v`
Expected: FAIL.

- [ ] **Step 6.3: Apply the migration**

In `src/perspicacite/pipeline/search_to_kb.py:571`:
```python
                source=PaperSource.WEB_SEARCH,
```
→
```python
                source=PaperSource.OPENALEX,
```

- [ ] **Step 6.4: Run the test to verify it passes**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_search_to_kb_ingest_dois_uses_openalex -v`
Expected: PASS.

- [ ] **Step 6.5: Commit**

```bash
git add src/perspicacite/pipeline/search_to_kb.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(pipeline): search_to_kb DOI ingest → PaperSource.OPENALEX

CLI DOI ingestion through retrieve_paper_content now stamps with the
unified-pipeline discovery source.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Migrate `rag/agentic/orchestrator.py` (2 sites)

**Files:**
- Modify: `src/perspicacite/rag/agentic/orchestrator.py:1142` (unified-pipeline branch)
- Modify: `src/perspicacite/rag/agentic/orchestrator.py:1174` (S2 fallback branch)
- Modify: `tests/unit/test_paper_source_adapter_migration.py`

- [ ] **Step 7.1: Add the failing pin test**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
def test_orchestrator_url_prefetch_uses_correct_enums():
    """rag/agentic/orchestrator._try_resolve_url has two Paper-source
    sites:

    - Unified-pipeline branch (line ~1142) → OPENALEX
    - S2 fallback branch (line ~1174) → SEMANTIC_SCHOLAR

    Source scan keeps both pinned."""
    import inspect
    from perspicacite.rag.agentic import orchestrator

    src = inspect.getsource(orchestrator)
    assert "source=PaperSource.OPENALEX" in src, (
        "orchestrator URL prefetch via unified pipeline must use OPENALEX"
    )
    assert "source=PaperSource.SEMANTIC_SCHOLAR" in src, (
        "orchestrator URL prefetch S2 fallback must use SEMANTIC_SCHOLAR"
    )
    assert "source=PaperSource.WEB_SEARCH" not in src, (
        "orchestrator must not default to WEB_SEARCH"
    )
```

- [ ] **Step 7.2: Run the test to verify it fails**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_orchestrator_url_prefetch_uses_correct_enums -v`
Expected: FAIL.

- [ ] **Step 7.3: Apply both migrations**

In `src/perspicacite/rag/agentic/orchestrator.py:1142` (inside the `if result.success and result.full_text:` unified-pipeline branch):
```python
                        source=PaperSource.WEB_SEARCH,
```
→
```python
                        source=PaperSource.OPENALEX,
```

In `src/perspicacite/rag/agentic/orchestrator.py:1174` (inside the S2 fallback branch, right after `lookup_paper`):
```python
                    source=PaperSource.WEB_SEARCH,
```
→
```python
                    source=PaperSource.SEMANTIC_SCHOLAR,
```

Use `Edit` with enough surrounding context to disambiguate the two near-identical patterns.

- [ ] **Step 7.4: Run the test to verify it passes**

Run: `pytest tests/unit/test_paper_source_adapter_migration.py::test_orchestrator_url_prefetch_uses_correct_enums -v`
Expected: PASS.

- [ ] **Step 7.5: Smoke-check the orchestrator still imports**

Run: `python -c "from perspicacite.rag.agentic.orchestrator import AgentOrchestrator; print('ok')"`
Expected: `ok` (or whichever public class is exported — adjust the import if `AgentOrchestrator` is the wrong name; just verify the module imports cleanly).

- [ ] **Step 7.6: Commit**

```bash
git add src/perspicacite/rag/agentic/orchestrator.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(rag): orchestrator URL prefetch — OPENALEX + SEMANTIC_SCHOLAR

Unified-pipeline branch labels papers OPENALEX (discovery source);
S2 fallback labels SEMANTIC_SCHOLAR. Pinned by
test_orchestrator_url_prefetch_uses_correct_enums.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Final invariant — no `WEB_SEARCH` defaults in `src/`

**Files:**
- Create: `tests/unit/test_paper_source_no_websearch_defaults.py`

- [ ] **Step 8.1: Write the invariant test**

Create `tests/unit/test_paper_source_no_websearch_defaults.py`:

```python
"""Regression guard for the 2026-05-15 PaperSource migration.

After the audit follow-up, no production module under ``src/`` should
construct a Paper with ``source=PaperSource.WEB_SEARCH``. The string
``PaperSource.WEB_SEARCH`` is still allowed where it is *referenced*
non-constructively — for example, in the enum definition itself,
in the docstring of normalize_paper_dict, or in legacy default-param
declarations that callers always override.

We allow the literal string ``PaperSource.WEB_SEARCH`` to appear, but
forbid the specific pattern ``source=PaperSource.WEB_SEARCH`` which is
what every Paper-construction call-site uses.
"""
from __future__ import annotations

from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "perspicacite"

# Files allowed to mention ``source=PaperSource.WEB_SEARCH`` even after
# the migration — e.g. legacy parameter defaults whose callers all
# override. Each entry is the *relative* path under src/perspicacite/.
ALLOWED_FILES: set[str] = {
    # normalize_paper_dict default param — historical fallback for
    # callers that don't specify source=; migrated callers always do.
    "models/papers.py",
}


def test_no_web_search_construction_in_src():
    """grep ``source=PaperSource.WEB_SEARCH`` across src/ must return
    only the explicitly-allow-listed files."""
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        rel = str(py.relative_to(SRC_ROOT))
        if rel in ALLOWED_FILES:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "source=PaperSource.WEB_SEARCH" in text:
            offenders.append(rel)
    assert not offenders, (
        f"PaperSource.WEB_SEARCH default has regressed in: {offenders}.\n"
        "If this is intentional, add the path to ALLOWED_FILES with a "
        "comment explaining why."
    )
```

- [ ] **Step 8.2: Run the invariant test**

Run: `pytest tests/unit/test_paper_source_no_websearch_defaults.py -v`
Expected: PASS (all 10 call-sites already migrated by Tasks 2–7).

- [ ] **Step 8.3: Commit**

```bash
git add tests/unit/test_paper_source_no_websearch_defaults.py
git commit -m "$(cat <<'EOF'
test(models): invariant — no PaperSource.WEB_SEARCH defaults in src/

Regression guard for the 2026-05-15 adapter migration. Files that
legitimately reference WEB_SEARCH (the enum definition; the
normalize_paper_dict legacy default param) are explicitly
allow-listed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Live `mistral/codestral-embed` smoke test

**Files:**
- Create: `tests/integration/test_codestral_embed_live.py`

This verifies that the existing `LiteLLMEmbeddingProvider` works end-to-end against Mistral's API when `MISTRAL_API_KEY` is set in the environment. Skips cleanly if the key is missing, so CI without the secret doesn't break.

- [ ] **Step 9.1: Write the live integration test**

Create `tests/integration/test_codestral_embed_live.py`:

```python
"""Live smoke test for Mistral's codestral-embed via LiteLLM.

Why: 2026-05-15 audit P3 follow-up flagged that mistral/codestral-embed
had only stub-level test coverage. This is a live smoke test that
embeds a real code snippet and checks the response shape.

Requires the MISTRAL_API_KEY env var. Skipped otherwise.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MISTRAL_API_KEY"),
    reason="MISTRAL_API_KEY not set — skip live codestral-embed test",
)


@pytest.mark.asyncio
async def test_codestral_embed_returns_vector_for_code():
    """Embed a small Python snippet via mistral/codestral-embed.

    Asserts:
      - the response is a list of vectors, one per input
      - each vector is a non-empty list of floats
      - the vector is not all zeros (i.e. the fallback zero-vector
        branch in LiteLLMEmbeddingProvider didn't trigger)
    """
    from perspicacite.llm.embeddings import LiteLLMEmbeddingProvider

    provider = LiteLLMEmbeddingProvider(model="mistral/codestral-embed")

    code_snippet = (
        "def fibonacci(n: int) -> int:\n"
        "    if n < 2:\n"
        "        return n\n"
        "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
    )

    vectors = await provider.embed([code_snippet])

    assert isinstance(vectors, list)
    assert len(vectors) == 1
    vec = vectors[0]
    assert isinstance(vec, list)
    assert len(vec) > 0, "embedding vector is empty"
    assert all(isinstance(x, float) for x in vec), "vector must be list[float]"
    # Guard against the empty-text fallback ([[0.0] * dim])
    nonzero = sum(1 for x in vec if x != 0.0)
    assert nonzero > 0, "codestral-embed returned an all-zero vector"


@pytest.mark.asyncio
async def test_codestral_embed_batches_two_snippets():
    """Confirm the batch path returns one vector per input."""
    from perspicacite.llm.embeddings import LiteLLMEmbeddingProvider

    provider = LiteLLMEmbeddingProvider(model="mistral/codestral-embed", batch_size=2)

    inputs = [
        "def add(a, b): return a + b",
        "def mul(a, b): return a * b",
    ]
    vectors = await provider.embed(inputs)
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]), (
        "codestral-embed must return same-dim vectors per call"
    )
```

- [ ] **Step 9.2: Run the live test (with the key)**

Run: `MISTRAL_API_KEY="$(grep '^export MISTRAL_API_KEY=' ~/.zshrc | sed -E 's/.*=["'\'']?//;s/["'\''].*//')" pytest tests/integration/test_codestral_embed_live.py -v`

(Or simpler: `source ~/.zshrc && pytest tests/integration/test_codestral_embed_live.py -v` — but only if the user's shell exports it. Easiest: spawn a Bash that sources zshrc.)

Expected: 2 PASSED. Each embed call typically returns a 1536-dim or 3072-dim vector (depending on Mistral's current codestral-embed dimension — both are valid).

- [ ] **Step 9.3: Confirm the skip path works (no key)**

Run: `MISTRAL_API_KEY= pytest tests/integration/test_codestral_embed_live.py -v`
Expected: 2 SKIPPED with the documented reason.

- [ ] **Step 9.4: Commit**

```bash
git add tests/integration/test_codestral_embed_live.py
git commit -m "$(cat <<'EOF'
test(integration): live mistral/codestral-embed smoke test

Embeds a real code snippet via LiteLLMEmbeddingProvider and asserts
the response shape + non-zero vector. Skipped when MISTRAL_API_KEY
is not set, so CI without the secret is unaffected. Closes the
2026-05-15 audit P3 follow-up on codestral coverage.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Full unit-suite sanity run

**Files:** none modified — verification only.

- [ ] **Step 10.1: Run the full unit-test suite, excluding the live integration tests**

Run: `pytest tests/unit -v --tb=short 2>&1 | tail -60`

Expected: the same 7 pre-existing failures the prior session documented (mock-signature drift in `test_local_docs_capsule_reader_route`, `test_mcp_multi_kb_passthrough`, `test_provenance_engine_wiring`, `test_zotero_ingest_worker`), but no *new* failures introduced by Tasks 1–8. Specifically, every test in `test_paper_source_adapter_migration.py`, `test_paper_source_enum.py`, and `test_paper_source_no_websearch_defaults.py` must pass.

- [ ] **Step 10.2: Print the migration summary**

Run a final grep to confirm zero unmigrated call-sites:
`grep -rn 'source=PaperSource.WEB_SEARCH' src/`
Expected: no output (everything migrated).

`grep -rn 'PaperSource.WEB_SEARCH' src/`
Expected: only the enum definition in `models/papers.py:20` and (if still present) the `normalize_paper_dict` legacy default parameter in `models/papers.py:173`.

No commit on this task — it is verification only. If anything in Step 10.1 fails *because* of Tasks 1–8, stop and investigate before declaring complete.

---

## Self-review

**Spec coverage:**
- Add `SEMANTIC_SCHOLAR` enum value → Task 1 ✓
- Migrate `semantic_scholar.py` → Task 2 ✓
- Migrate `orchestrator.py` (2 sites) → Task 7 ✓
- Migrate `kb.py` (3 sites) → Task 5 ✓
- Migrate `mcp/server.py` (2 sites) → Task 4 ✓
- Migrate `chunking.py` → Task 3 ✓
- Pin tests like `test_paper_source_adapter_migration.py` → extended in every task ✓
- Live codestral embed test (MISTRAL_API_KEY) → Task 9 ✓
- `pipeline/search_to_kb.py:571` (found via grep, not in user's list) → Task 6 ✓

**Placeholder scan:** No "TBD", "implement later", or generic-error-handling placeholders. Every step has the exact code or exact command.

**Type consistency:** All enum value references use the literal `PaperSource.<NAME>` form; test assertions use `is PaperSource.<NAME>`. The string-form assertions (`"source=PaperSource.<NAME>" in src`) are syntax-level guards that match the actual source.

**One known caveat:** `tests/unit/test_url_prefetch.py` (and a few other tests in the deferred list) may need updates if they assert `source == PaperSource.WEB_SEARCH` after the orchestrator migration. The plan deliberately does not pre-empt that — if Task 7 breaks `test_url_prefetch.py`, the executor should fix the test to assert the new value (`OPENALEX` or `SEMANTIC_SCHOLAR` depending on the branch under test) inside Task 7's commit. The prior session's 7 "pre-existing" failures don't include `test_url_prefetch.py`, so this is the most likely new-failure surface.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-paper-source-web-search-migration.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

User's standing instruction ("no clarifying questions") → defaulting to **Subagent-Driven**.
