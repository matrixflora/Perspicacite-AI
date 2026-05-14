# Time-bounded queries — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Plumb `year_min` / `year_max` filters from the MCP layer
through `DynamicKnowledgeBase.search` into the Chroma where-clause.

**Spec:** `docs/superpowers/specs/2026-05-14-time-bounded-queries-design.md`

---

## Task 1: DynamicKnowledgeBase.search accepts filters

**Files:**
- Modify: `src/perspicacite/rag/dynamic_kb.py`
- Test: `tests/unit/test_dynamic_kb_filters.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_dynamic_kb_filters.py
"""Verify DynamicKnowledgeBase.search threads filters through (Wave 4.2)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.models.search import SearchFilters
from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig


def _kb_with_mocks():
    """Build a DynamicKnowledgeBase whose vector_store / embedding are mocked."""
    vstore = MagicMock()
    vstore.search = AsyncMock(return_value=[])  # empty result is fine
    embed = MagicMock()
    embed.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    kb = DynamicKnowledgeBase(vstore, embed, config=KnowledgeBaseConfig())
    kb.collection_name = "test_coll"
    kb._initialized = True
    return kb, vstore


@pytest.mark.asyncio
async def test_search_passes_filters_to_store():
    kb, vstore = _kb_with_mocks()
    filters = SearchFilters(year_min=2020, year_max=2024)
    await kb.search("query", filters=filters)
    # Inspect the call: filters should appear as a kwarg.
    args, kwargs = vstore.search.call_args
    assert kwargs.get("filters") is filters or args[-1] is filters or "filters" in kwargs


@pytest.mark.asyncio
async def test_search_without_filters_passes_none():
    kb, vstore = _kb_with_mocks()
    await kb.search("query")
    args, kwargs = vstore.search.call_args
    # Filters must be absent or explicitly None — never some other default.
    assert kwargs.get("filters") is None or "filters" not in kwargs
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_dynamic_kb_filters.py -v
```

- [ ] **Step 3: Wire filters through**

In `src/perspicacite/rag/dynamic_kb.py`, find the `search` method
(around line 290). Add a `filters: SearchFilters | None = None`
parameter and pass it to `vector_store.search`. Update imports if
needed:

```python
    async def search(
        self,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        filters: "SearchFilters | None" = None,
    ) -> list[dict[str, Any]]:
        """
        Search the knowledge base.

        Args:
            query: Search query
            top_k: Number of results (default: config.top_k)
            min_score: Minimum relevance score
            filters: Optional ``SearchFilters`` (year_min/year_max/...).
                Translated to Chroma where-clauses inside the vector
                store. See Wave 4.2.

        Returns:
            List of search results with text and metadata
        """
        if not self._initialized:
            raise RuntimeError("Knowledge base not initialized")

        top_k = top_k or self.config.top_k
        min_score = min_score or self.config.min_relevance_score

        query_embeddings = await self.embedding_service.embed([query])
        query_embedding = query_embeddings[0]

        results = await self.vector_store.search(
            collection=self.collection_name,
            query_embedding=query_embedding,
            top_k=top_k * 2,
            filters=filters,
        )
        # ... rest unchanged
```

Add the lazy `SearchFilters` import to the top of the file if not
present:

```python
from perspicacite.models.search import SearchFilters
```

(Use a `TYPE_CHECKING` guard if the existing import scheme prefers
forward refs — read the file to decide.)

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_dynamic_kb_filters.py -v
```

Also run the existing dynamic_kb tests to make sure nothing
regressed:

```bash
pytest tests/unit/ -k "dynamic_kb" --timeout=15 --timeout-method=signal -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/dynamic_kb.py \
        tests/unit/test_dynamic_kb_filters.py
git commit -m "feat(rag): DynamicKnowledgeBase.search accepts SearchFilters (Wave 4.2)"
```

---

## Task 2: MCP search_knowledge_base surfaces year params

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (the `search_knowledge_base` tool)
- Test: `tests/unit/test_mcp_search_kb_year_filters.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mcp_search_kb_year_filters.py
"""Verify search_knowledge_base MCP tool wires year params (Wave 4.2)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp.server import search_knowledge_base
from perspicacite.models.search import SearchFilters


def _state_with_mocked_kb():
    state = MagicMock()
    state.session_store.get_kb_metadata = AsyncMock(return_value=MagicMock(
        embedding_model="text-embedding-3-small",
        paper_count=10,
    ))
    return state


@pytest.mark.asyncio
async def test_year_params_become_search_filters(monkeypatch):
    state = _state_with_mocked_kb()
    # Patch _require_state to return our mock.
    monkeypatch.setattr(
        "perspicacite.mcp.server._require_state",
        lambda: state,
    )

    captured_filters: list[SearchFilters | None] = []

    class _FakeDKB:
        def __init__(self, *a, **kw):
            self.config = MagicMock()
            self.collection_name = "c"
            self._initialized = True

        async def search(self, query, top_k=None, min_score=None, filters=None):
            captured_filters.append(filters)
            return []

    monkeypatch.setattr(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
        _FakeDKB,
    )

    result = await search_knowledge_base(
        query="q", kb_name="kb1", top_k=5,
        year_min=2018, year_max=2023,
    )
    assert "results" in json.loads(result)
    assert len(captured_filters) == 1
    f = captured_filters[0]
    assert f is not None
    assert f.year_min == 2018
    assert f.year_max == 2023


@pytest.mark.asyncio
async def test_no_year_params_passes_no_filters(monkeypatch):
    state = _state_with_mocked_kb()
    monkeypatch.setattr(
        "perspicacite.mcp.server._require_state",
        lambda: state,
    )

    captured_filters: list = []

    class _FakeDKB:
        def __init__(self, *a, **kw):
            self.collection_name = "c"
            self._initialized = True

        async def search(self, query, top_k=None, min_score=None, filters=None):
            captured_filters.append(filters)
            return []

    monkeypatch.setattr(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
        _FakeDKB,
    )

    await search_knowledge_base(query="q", kb_name="kb1", top_k=5)
    assert captured_filters == [None]


@pytest.mark.asyncio
async def test_only_year_min(monkeypatch):
    state = _state_with_mocked_kb()
    monkeypatch.setattr(
        "perspicacite.mcp.server._require_state",
        lambda: state,
    )

    captured: list = []

    class _FakeDKB:
        def __init__(self, *a, **kw):
            self.collection_name = "c"
            self._initialized = True

        async def search(self, query, top_k=None, min_score=None, filters=None):
            captured.append(filters)
            return []

    monkeypatch.setattr(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
        _FakeDKB,
    )

    await search_knowledge_base(query="q", kb_name="kb1", year_min=2020)
    assert captured[0] is not None
    assert captured[0].year_min == 2020
    assert captured[0].year_max is None
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_mcp_search_kb_year_filters.py -v
```

The tests fail because `search_knowledge_base` doesn't accept
`year_min` / `year_max` yet.

- [ ] **Step 3: Add the parameters**

In `src/perspicacite/mcp/server.py`, locate `search_knowledge_base`
(around line 441). Modify the signature and docstring:

```python
@mcp.tool()
async def search_knowledge_base(
    query: str,
    kb_name: str = "default",
    top_k: int = 5,
    kb_names: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """
    Search within a specific knowledge base (or multiple KBs) using semantic similarity.

    Args:
        query: Search query
        kb_name: Knowledge base name (single-KB path)
        top_k: Number of top results to return
        kb_names: Optional list of KBs to query together. ...
        year_min: Restrict to papers published in or after this year (inclusive).
        year_max: Restrict to papers published in or before this year (inclusive).

    Returns:
        JSON with matching chunks ...
    """
```

In the single-KB branch (around line 514+), after constructing
`dkb`, build the filters and pass them. Find the line that calls
`dkb.search(...)`:

```python
        # Build year-bounded filters (Wave 4.2).
        from perspicacite.models.search import SearchFilters
        filters = None
        if year_min is not None or year_max is not None:
            filters = SearchFilters(year_min=year_min, year_max=year_max)

        results = await dkb.search(query, top_k=top_k, filters=filters)
```

For the multi-KB branch (line 470+), the `MultiKBRetriever.search`
signature doesn't accept `filters` today; that's the documented
Wave 4.2 followup. Year filters in multi-KB mode → ignored, with a
warning log:

```python
        if year_min is not None or year_max is not None:
            logger.warning(
                "search_kb_multi_year_filter_ignored",
                year_min=year_min, year_max=year_max,
                note="multi-KB filter passthrough is a Wave 4.2 followup",
            )
```

(Locate the multi-KB branch and place the warning at its top.)

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_mcp_search_kb_year_filters.py -v
```

Then re-run the MCP smoke suite to ensure no regression:

```bash
pytest tests/integration/test_mcp_smoke.py -v --timeout=30 --timeout-method=signal
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/mcp/server.py \
        tests/unit/test_mcp_search_kb_year_filters.py
git commit -m "feat(mcp): search_knowledge_base year_min/year_max filters (Wave 4.2)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/time-bounded-queries-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
# Time-bounded queries — operator guide (2026-05-14)

Wave 4.2 of the framework-hardening roadmap. Restrict KB search to a
publication-year window.

## Usage

```python
# MCP tool call
search_knowledge_base(
    query="DESI redshift surveys",
    kb_name="astro",
    year_min=2020,        # inclusive lower bound
    year_max=2024,        # inclusive upper bound
)
```

Either bound is optional. Omit one for an open window in that
direction.

## How it works

The two parameters become a `SearchFilters(year_min=..., year_max=...)`
object. The existing `_filters_to_where` translator converts that
to a Chroma where-clause:

```
{"$and": [
  {"year": {"$gte": 2020}},
  {"year": {"$lte": 2024}}
]}
```

Chunks without a `year` field are excluded once either bound is
set — if you asked for a year window, undated material is silently
dropped.

## Limitations (today)

- **Multi-KB mode** (`kb_names=[...]` with len > 1) does not yet
  pass filters through `MultiKBRetriever`. Year params are accepted
  but ignored, with a warning log. Wave 4.2 followup.
- **`generate_report`** doesn't surface year filters yet. Followup.
- **Granularity**: chunk metadata stores `year: int` only — no month
  or day. Filtering on full publication dates requires a metadata
  schema bump, deferred to a future wave.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/models/search.py` | `SearchFilters` (existed) |
| `src/perspicacite/retrieval/chroma_store.py` | `_filters_to_where` (existed) |
| `src/perspicacite/rag/dynamic_kb.py` | now accepts `filters` |
| `src/perspicacite/mcp/server.py` | `year_min` / `year_max` MCP params |

## Followups

- `MultiKBRetriever.search` plumbing.
- `generate_report` filter passthrough.
- Month-level granularity once metadata grows a `published_at` field.
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/time-bounded-queries-*.md` to `.gitignore` after
`!docs/multimodal-extraction-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/time-bounded-queries-2026-05-14.md .gitignore
git commit -m "docs(time-bounded): operator guide (Wave 4.2)"
```

---

## Done

After Task 3:

- `DynamicKnowledgeBase.search` accepts `filters`.
- `search_knowledge_base` MCP tool surfaces `year_min` / `year_max`.
- Multi-KB filter passthrough is a documented followup.
- 6 new tests, all passing.
- Operator doc landed.
