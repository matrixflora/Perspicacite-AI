# Passage retrieval & structured extraction — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Plan/spec files in `docs/superpowers/specs/2026-05-20-*.md` and `docs/superpowers/plans/2026-05-20-*.md` are NOT to be committed** (user request). Only code changes are committed.

**Goal:** Add server-side passage-level retrieval and LLM-backed structured extraction to Perspicacité, then wire ASB to use them in place of regex mining and Scriptorium to use them for paragraph-aware citation suggestions.

**Architecture:** Three new MCP tools plus one new MCP tool with built-in adaptive retry on the Perspicacité server (FastMCP, `mcp/server.py`). New retrieval and extraction modules under `src/perspicacite/retrieval/passage_search.py` and `src/perspicacite/pipeline/extraction.py`. ASB's `perspicacite_client.py` gains real implementations of `search_by_passage`, `get_relevant_passages`, `extract_parameters`, and `extract_failure_modes`; `skill_pack_v3.py` drops regex extraction in the MCP-enrichment path only. Scriptorium gets a new `/find-related` slash command backed by a thin wrapper around `search_by_passage`.

**Tech Stack:** Python 3.12+, FastMCP, ChromaDB, `AsyncLLMClient` via LiteLLM (default DeepSeek; per-call model override supported), pytest (Perspicacité), unittest (ASB), pytest (Scriptorium), uv.

**Spec:** `docs/superpowers/specs/2026-05-20-passage-retrieval-and-structured-extraction-design.md`.

---

## File map

**Perspicacite-AI**
- Create: `src/perspicacite/retrieval/passage_search.py`
- Create: `src/perspicacite/pipeline/extraction.py`
- Modify: `src/perspicacite/mcp/server.py` (append 3 new `@mcp.tool` functions; ~150–250 lines added)
- Create: `tests/unit/test_mcp_search_by_passage.py`
- Create: `tests/unit/test_mcp_get_relevant_passages.py`
- Create: `tests/unit/test_mcp_extract_parameters.py`
- Create: `tests/unit/test_mcp_extract_failure_modes.py`
- Create: `tests/unit/test_passage_search_core.py`
- Create: `tests/unit/test_extraction_core.py`

**AgenticScienceBuilder**
- Modify: `src/agentic_science_builder/perspicacite_client.py` (lines 441–496 + new methods)
- Modify: `src/agentic_science_builder/skill_pack_v3.py` (lines ~1290–1310 and ~1340–1400)
- Modify: `tests/test_perspicacite_client.py` (or create if absent — verify in Step 1 of Task 5)

**Scriptorium**
- Create: `.claude/commands/find-related.md`
- Create: `scriptorium/literature/passage_search.py`
- Create: `tests/test_passage_search.py`

**research-tools-audit**
- Modify or add: `scenarios/combined/08_extraction_tools.yaml` (verify scenarios dir layout in Task 9 Step 1)

---

## Task 1: Core passage search module (Perspicacité)

**Files:**
- Create: `src/perspicacite/retrieval/passage_search.py`
- Test: `tests/unit/test_passage_search_core.py`

This task lays the foundation for both `search_by_passage` and `get_relevant_passages` MCP tools. It produces a single retrieval function that takes already-embedded query text and returns license-tagged passages.

- [ ] **Step 1: Read existing retrieval primitives**

Read these to understand the API surfaces this module will wrap:
- `src/perspicacite/retrieval/multi_kb.py` (functions `MultiKBRetriever.__init__`, `MultiKBRetriever.search`, `check_embedding_compat`)
- `src/perspicacite/rag/dynamic_kb.py` (class `DynamicKnowledgeBase`, methods `search`, `__init__`)
- `src/perspicacite/models/kb.py` (function `chroma_collection_name_for_kb`)
- `src/perspicacite/mcp/server.py:828–997` (existing `search_knowledge_base` tool — our new tool reuses the same retriever construction pattern)

Confirm the data shape returned by `DynamicKnowledgeBase.search`: `{"text", "score", "paper_id", "metadata", "kb_name"}` where `metadata` is either an object with `__dict__` or a plain dict containing `title`, `section`, `doi`, `year`, `license_id` (if present).

- [ ] **Step 2: Write the failing test for the happy path**

Create `tests/unit/test_passage_search_core.py`:

```python
"""Tests for src/perspicacite/retrieval/passage_search.py."""
from __future__ import annotations

import pytest

from perspicacite.retrieval.passage_search import (
    PassageMatch,
    search_passages,
)


class _FakeRetriever:
    """Returns canned chunk dicts in the shape DynamicKnowledgeBase.search emits."""

    def __init__(self, results):
        self._results = results
        self.calls: list[tuple] = []

    async def search(self, query, top_k=10, filters=None):
        self.calls.append((query, top_k, filters))
        return self._results


@pytest.mark.asyncio
async def test_search_passages_returns_license_tagged_matches():
    retriever = _FakeRetriever(
        results=[
            {
                "text": "neural network temperature 37 degC",
                "score": 0.91,
                "paper_id": "10.1/abc",
                "metadata": {
                    "title": "Hot Networks",
                    "doi": "10.1/abc",
                    "year": 2024,
                    "license_id": "CC-BY",
                    "source_url": "https://example.org/abc",
                },
                "kb_name": "test_kb",
            }
        ],
    )

    out = await search_passages(
        retriever, text="how does temperature affect networks?", k=3
    )

    assert len(out) == 1
    m = out[0]
    assert isinstance(m, PassageMatch)
    assert m.chunk_text == "neural network temperature 37 degC"
    assert m.score == pytest.approx(0.91)
    assert m.source.doi == "10.1/abc"
    assert m.source.license_id == "CC-BY"
    assert m.source.year == 2024
    assert m.kb_name == "test_kb"
    assert retriever.calls == [
        ("how does temperature affect networks?", 3, None)
    ]


@pytest.mark.asyncio
async def test_search_passages_filters_by_min_score():
    retriever = _FakeRetriever(
        results=[
            {"text": "high", "score": 0.9, "paper_id": "a", "metadata": {}, "kb_name": "kb"},
            {"text": "low", "score": 0.1, "paper_id": "b", "metadata": {}, "kb_name": "kb"},
        ],
    )

    out = await search_passages(retriever, text="x", k=5, min_score=0.5)

    assert [m.chunk_text for m in out] == ["high"]


@pytest.mark.asyncio
async def test_search_passages_rejects_empty_text():
    with pytest.raises(ValueError, match="empty"):
        await search_passages(_FakeRetriever([]), text="", k=5)


@pytest.mark.asyncio
async def test_search_passages_rejects_oversized_text():
    big = "x" * 4001
    with pytest.raises(ValueError, match="4000"):
        await search_passages(_FakeRetriever([]), text=big, k=5)


@pytest.mark.asyncio
async def test_search_passages_clamps_k_to_max():
    retriever = _FakeRetriever([])
    await search_passages(retriever, text="x", k=999)
    assert retriever.calls[0][1] == 50  # MAX_K
```

- [ ] **Step 3: Run the test and confirm it fails**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
uv run pytest tests/unit/test_passage_search_core.py -v
```

Expected: `ModuleNotFoundError: No module named 'perspicacite.retrieval.passage_search'`.

- [ ] **Step 4: Implement the module**

Create `src/perspicacite/retrieval/passage_search.py`:

```python
"""Passage-level semantic retrieval.

This module is the shared core behind two MCP tools — ``search_by_passage``
(text input from the caller, e.g. a paragraph) and ``get_relevant_passages``
(keyword query with optional adaptive retry). Both end up calling
:func:`search_passages` with a fully-constructed retriever.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

MAX_TEXT_CHARS = 4000
MAX_K = 50


class _AsyncRetriever(Protocol):
    async def search(
        self, query: str, top_k: int = 10, filters: Any | None = None
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class PassageSource:
    doi: str | None
    title: str | None
    authors: list[str] | None
    year: int | None
    bibkey: str | None
    source_url: str | None
    license_id: str | None


@dataclass(frozen=True)
class PassageMatch:
    chunk_id: str
    chunk_text: str
    score: float
    source: PassageSource
    kb_name: str | None


def _validate_text(text: str) -> None:
    if not text or not text.strip():
        raise ValueError("input text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"input text exceeds {MAX_TEXT_CHARS} chars "
            "(caller must chunk longer inputs)"
        )


def _coerce_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if hasattr(raw, "__dict__") and not isinstance(raw, dict):
        return dict(raw.__dict__)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _to_match(raw: dict[str, Any]) -> PassageMatch:
    meta = _coerce_metadata(raw.get("metadata"))
    paper_id = raw.get("paper_id") or meta.get("paper_id") or meta.get("doi") or ""
    kb = raw.get("kb_name") or meta.get("kb_name")
    chunk_id = raw.get("chunk_id") or f"{kb}:{paper_id}:{hash(raw.get('text', '')) & 0xFFFF}"
    source = PassageSource(
        doi=meta.get("doi"),
        title=meta.get("title"),
        authors=meta.get("authors"),
        year=meta.get("year"),
        bibkey=meta.get("bibkey"),
        source_url=meta.get("source_url") or meta.get("url"),
        license_id=meta.get("license_id") or meta.get("license"),
    )
    return PassageMatch(
        chunk_id=str(chunk_id),
        chunk_text=str(raw.get("text", "")),
        score=float(raw.get("score") or 0.0),
        source=source,
        kb_name=kb,
    )


async def search_passages(
    retriever: _AsyncRetriever,
    *,
    text: str,
    k: int = 5,
    min_score: float | None = None,
) -> list[PassageMatch]:
    """Run a passage-level search against an already-constructed retriever.

    The retriever knows which KB(s) to query and how. This function only
    handles input validation, k-clamping, response normalisation, and the
    optional min_score filter.
    """
    _validate_text(text)
    capped_k = min(max(k, 1), MAX_K)
    raw_results = await retriever.search(text, top_k=capped_k, filters=None)
    matches = [_to_match(r) for r in raw_results]
    if min_score is not None:
        matches = [m for m in matches if m.score >= min_score]
    return matches
```

- [ ] **Step 5: Add pytest-asyncio config check**

Run:

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
grep -n "asyncio_mode\|pytest-asyncio" pyproject.toml pytest.ini setup.cfg 2>/dev/null
```

If `asyncio_mode = "auto"` is configured, remove the `@pytest.mark.asyncio` decorators in the test file. If it's `strict` or absent, leave the decorators as-is. Adjust the test file inline if needed.

- [ ] **Step 6: Run tests and confirm pass**

```bash
uv run pytest tests/unit/test_passage_search_core.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
git add src/perspicacite/retrieval/passage_search.py tests/unit/test_passage_search_core.py
git commit -m "feat(retrieval): passage-level search core with license-tagged matches"
```

---

## Task 2: `search_by_passage` MCP tool (Perspicacité)

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (append new `@mcp.tool` at end of the tools block, before `__all__`)
- Test: `tests/unit/test_mcp_search_by_passage.py`

- [ ] **Step 1: Read existing tool wiring for the construction pattern**

Re-read `src/perspicacite/mcp/server.py:828–997` (`search_knowledge_base`). The new tool uses the same retriever-construction logic (KB lookup → `DynamicKnowledgeBase` or `MultiKBRetriever`).

- [ ] **Step 2: Write the failing MCP test**

Create `tests/unit/test_mcp_search_by_passage.py`:

```python
"""Tests for the ``search_by_passage`` MCP tool."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.session_store.get_kb_metadata = AsyncMock(
        return_value=MagicMock(embedding_model="text-embedding-3-small")
    )
    state.vector_store = MagicMock()
    state.embedding_provider = MagicMock(dimension=1536)
    return state


@pytest.mark.asyncio
async def test_search_by_passage_returns_matches(mock_state):
    with patch.object(mcp_server, "_require_state", return_value=mock_state):
        fake_results = [
            {
                "text": "Temperature affects neural training stability.",
                "score": 0.88,
                "paper_id": "10.x/y",
                "metadata": {
                    "doi": "10.x/y",
                    "title": "T",
                    "year": 2024,
                    "license_id": "CC-BY",
                },
                "kb_name": "kb_a",
            }
        ]
        with patch(
            "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase.search",
            new=AsyncMock(return_value=fake_results),
        ):
            raw = await mcp_server.search_by_passage(
                text="how does temperature affect training?",
                kb_name="kb_a",
                k=3,
            )

    payload = json.loads(raw)
    assert payload["ok"] is True
    results = payload["results"]
    assert len(results) == 1
    assert results[0]["chunk_text"].startswith("Temperature affects")
    assert results[0]["source"]["license_id"] == "CC-BY"
    assert results[0]["score"] == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_search_by_passage_rejects_empty(mock_state):
    with patch.object(mcp_server, "_require_state", return_value=mock_state):
        raw = await mcp_server.search_by_passage(text="", kb_name="kb_a")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "empty" in payload["error"].lower()


@pytest.mark.asyncio
async def test_search_by_passage_unknown_kb_returns_error(mock_state):
    mock_state.session_store.get_kb_metadata = AsyncMock(return_value=None)
    with patch.object(mcp_server, "_require_state", return_value=mock_state):
        raw = await mcp_server.search_by_passage(text="hi", kb_name="ghost")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "ghost" in payload["error"]
```

- [ ] **Step 3: Run the test and confirm failure**

```bash
uv run pytest tests/unit/test_mcp_search_by_passage.py -v
```

Expected: `AttributeError: module 'perspicacite.mcp.server' has no attribute 'search_by_passage'`.

- [ ] **Step 4: Implement the MCP tool**

In `src/perspicacite/mcp/server.py`, find the closing `__all__` block (around line 4076+) and add the new tool BEFORE it. Use this exact code:

```python
# =============================================================================
# Tool: search_by_passage
# =============================================================================


@mcp.tool()
async def search_by_passage(
    text: str,
    kb_name: str = "default",
    kb_names: list[str] | None = None,
    k: int = 5,
    min_score: float | None = None,
) -> str:
    """
    Retrieve KB passages similar to an arbitrary input text (sentence / paragraph).

    Differs from ``search_knowledge_base`` in that the response surfaces
    ``license_id`` and a structured ``source`` record per match — designed for
    consumers that need to make citation decisions on the returned chunks.

    Args:
        text: Free-form input text (sentence, paragraph, claim). 1–4000 chars.
        kb_name: Single-KB scope (used when ``kb_names`` is None or 1 entry).
        kb_names: Optional list of KBs to query together. All must share the
            same embedding model.
        k: Top-k matches to return (max 50).
        min_score: Optional similarity floor; matches below are dropped.

    Returns:
        JSON {"ok": True, "results": [...]} or {"ok": False, "error": "..."}.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        from perspicacite.retrieval.passage_search import (
            search_passages,
        )

        # Multi-KB path
        if kb_names and len(kb_names) > 1:
            from perspicacite.retrieval.multi_kb import (
                MultiKBRetriever,
                check_embedding_compat,
            )

            metas = [
                await state.session_store.get_kb_metadata(n) for n in kb_names
            ]
            for i, meta in enumerate(metas):
                if meta is None:
                    return _json_error(
                        f"Knowledge base not found: {kb_names[i]}"
                    )
            compat_msg = check_embedding_compat(metas)
            if compat_msg:
                return _json_error(compat_msg)

            retriever = MultiKBRetriever(
                vector_store=state.vector_store,
                embedding_service=state.embedding_provider,
                kb_metas=metas,
            )
        else:
            from perspicacite.models.kb import chroma_collection_name_for_kb
            from perspicacite.rag.dynamic_kb import (
                DynamicKnowledgeBase,
                KnowledgeBaseConfig,
            )

            effective_kb = (
                kb_names[0] if (kb_names and len(kb_names) == 1) else kb_name
            )
            kb_meta = await state.session_store.get_kb_metadata(effective_kb)
            if not kb_meta:
                return _json_error(
                    f"Knowledge base '{effective_kb}' not found"
                )
            retriever = DynamicKnowledgeBase(
                state.vector_store,
                state.embedding_provider,
                config=KnowledgeBaseConfig(
                    vector_size=state.embedding_provider.dimension,
                ),
            )
            retriever.collection_name = chroma_collection_name_for_kb(
                effective_kb
            )
            retriever._initialized = True

        matches = await search_passages(
            retriever, text=text, k=k, min_score=min_score
        )

        return _json_ok(
            {
                "results": [
                    {
                        "chunk_id": m.chunk_id,
                        "chunk_text": m.chunk_text,
                        "score": m.score,
                        "source": {
                            "doi": m.source.doi,
                            "title": m.source.title,
                            "authors": m.source.authors,
                            "year": m.source.year,
                            "bibkey": m.source.bibkey,
                            "source_url": m.source.source_url,
                            "license_id": m.source.license_id,
                        },
                        "kb_name": m.kb_name,
                    }
                    for m in matches
                ],
            }
        )

    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        logger.error("mcp_search_by_passage_error", error=str(e))
        return _json_error(f"search_by_passage failed: {e}")
```

Also append `"search_by_passage"` to the `__all__` tuple at the bottom of the file.

- [ ] **Step 5: Run the test and confirm pass**

```bash
uv run pytest tests/unit/test_mcp_search_by_passage.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 6: Run full unit suite to confirm no regression**

```bash
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -30
```

Expected: pre-existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_search_by_passage.py
git commit -m "feat(mcp): search_by_passage tool for passage-level KB retrieval"
```

---

## Task 3: `get_relevant_passages` MCP tool with adaptive retry (Perspicacité)

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (append new tool + extend `__all__`)
- Test: `tests/unit/test_mcp_get_relevant_passages.py`

Functionally similar to `search_by_passage`, but takes a keyword-style `query` and supports `adaptive: bool` — on zero hits, the existing query optimizer rephrases and retries once. Returns `attempts` and `refined_query` metadata.

- [ ] **Step 1: Locate the query optimizer entry point**

Run:

```bash
grep -n "def rephrase\|def optimize\|class QueryOptimizer" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/search/query_optimizer.py
```

Note the public function/method name (likely `optimize_query` or similar). Confirm whether it's sync or async; you will use it from the new MCP tool.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_mcp_get_relevant_passages.py`:

```python
"""Tests for the ``get_relevant_passages`` MCP tool, including adaptive mode."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


def _state():
    state = MagicMock()
    state.session_store.get_kb_metadata = AsyncMock(
        return_value=MagicMock(embedding_model="text-embedding-3-small")
    )
    state.vector_store = MagicMock()
    state.embedding_provider = MagicMock(dimension=1536)
    return state


@pytest.mark.asyncio
async def test_non_adaptive_returns_passages():
    fake_hits = [
        {"text": "a", "score": 0.5, "paper_id": "x", "metadata": {}, "kb_name": "kb"}
    ]
    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase.search",
        new=AsyncMock(return_value=fake_hits),
    ):
        raw = await mcp_server.get_relevant_passages(
            query="enzyme kinetics", kb_name="kb", k=5
        )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert len(payload["passages"]) == 1
    assert payload["attempts"][0]["query"] == "enzyme kinetics"
    assert payload["attempts"][0]["hit_count"] == 1
    assert "refined_query" not in payload or payload["refined_query"] is None


@pytest.mark.asyncio
async def test_adaptive_retries_on_empty():
    sequence = [[], [{"text": "found", "score": 0.7, "paper_id": "x", "metadata": {}, "kb_name": "kb"}]]
    search_mock = AsyncMock(side_effect=lambda *a, **kw: sequence.pop(0))

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase.search", new=search_mock
    ), patch.object(
        mcp_server, "_rephrase_query", AsyncMock(return_value="rephrased q")
    ):
        raw = await mcp_server.get_relevant_passages(
            query="obscure terms", kb_name="kb", k=5, adaptive=True
        )

    payload = json.loads(raw)
    assert payload["ok"] is True
    assert len(payload["passages"]) == 1
    assert payload["refined_query"] == "rephrased q"
    assert [a["query"] for a in payload["attempts"]] == [
        "obscure terms",
        "rephrased q",
    ]
    assert [a["hit_count"] for a in payload["attempts"]] == [0, 1]
    assert search_mock.await_count == 2


@pytest.mark.asyncio
async def test_adaptive_disabled_does_not_retry():
    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase.search",
        new=AsyncMock(return_value=[]),
    ) as search_mock, patch.object(
        mcp_server, "_rephrase_query", AsyncMock(return_value="never used")
    ) as rephrase_mock:
        raw = await mcp_server.get_relevant_passages(
            query="zero hits", kb_name="kb", k=5, adaptive=False
        )
    payload = json.loads(raw)
    assert payload["passages"] == []
    assert search_mock.await_count == 1
    rephrase_mock.assert_not_awaited()
```

- [ ] **Step 3: Run and confirm failure**

```bash
uv run pytest tests/unit/test_mcp_get_relevant_passages.py -v
```

Expected: AttributeError on `get_relevant_passages` and/or `_rephrase_query`.

- [ ] **Step 4: Implement the tool + helper**

In `src/perspicacite/mcp/server.py`, append BEFORE `__all__`:

```python
# =============================================================================
# Tool: get_relevant_passages (with adaptive retry)
# =============================================================================


async def _rephrase_query(query: str, *, context: str | None = None) -> str | None:
    """Wrap the search.query_optimizer for one-shot rephrasing.

    Returns None when the optimizer can't suggest a rewrite (we then bail
    on adaptive retry rather than loop). Internal helper; patched in tests.
    """
    try:
        from perspicacite.search.query_optimizer import optimize_query

        optimized = await optimize_query(query, context=context)
        if not optimized or optimized.strip() == query.strip():
            return None
        return optimized
    except Exception as e:
        logger.warning("query_rephrase_failed", error=str(e), query=query)
        return None


@mcp.tool()
async def get_relevant_passages(
    query: str,
    kb_name: str = "default",
    kb_names: list[str] | None = None,
    k: int = 10,
    paper_doi: str | None = None,
    adaptive: bool = False,
) -> str:
    """
    Keyword-style passage retrieval with optional adaptive re-query on empty.

    Like ``search_by_passage`` but treats the input as a search query rather
    than a piece of source text. When ``adaptive=True`` and the first call
    returns zero passages, the server invokes the query optimizer once and
    retries. The response always includes ``attempts`` (1 or 2 entries) and,
    when adaptive fired, ``refined_query``.

    Args:
        query: Search query (keywords / short prompt).
        kb_name: Single KB scope.
        kb_names: Optional multi-KB list (same embedding model required).
        k: Top-k passages per attempt (max 50).
        paper_doi: Optional DOI scope-filter (reserved; not yet enforced).
        adaptive: When True, retry once with a rephrased query on empty.

    Returns:
        JSON {"ok": True, "passages": [...], "attempts": [...], "refined_query": "..."?}
        or {"ok": False, "error": "..."}.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        from perspicacite.retrieval.passage_search import search_passages

        # Build retriever (same pattern as search_by_passage; could be factored
        # later but kept local for clarity).
        if kb_names and len(kb_names) > 1:
            from perspicacite.retrieval.multi_kb import (
                MultiKBRetriever,
                check_embedding_compat,
            )
            metas = [
                await state.session_store.get_kb_metadata(n) for n in kb_names
            ]
            for i, meta in enumerate(metas):
                if meta is None:
                    return _json_error(
                        f"Knowledge base not found: {kb_names[i]}"
                    )
            compat_msg = check_embedding_compat(metas)
            if compat_msg:
                return _json_error(compat_msg)
            retriever = MultiKBRetriever(
                vector_store=state.vector_store,
                embedding_service=state.embedding_provider,
                kb_metas=metas,
            )
        else:
            from perspicacite.models.kb import chroma_collection_name_for_kb
            from perspicacite.rag.dynamic_kb import (
                DynamicKnowledgeBase,
                KnowledgeBaseConfig,
            )
            effective_kb = (
                kb_names[0] if (kb_names and len(kb_names) == 1) else kb_name
            )
            kb_meta = await state.session_store.get_kb_metadata(effective_kb)
            if not kb_meta:
                return _json_error(
                    f"Knowledge base '{effective_kb}' not found"
                )
            retriever = DynamicKnowledgeBase(
                state.vector_store,
                state.embedding_provider,
                config=KnowledgeBaseConfig(
                    vector_size=state.embedding_provider.dimension,
                ),
            )
            retriever.collection_name = chroma_collection_name_for_kb(
                effective_kb
            )
            retriever._initialized = True

        attempts: list[dict] = []
        matches = await search_passages(retriever, text=query, k=k)
        attempts.append({"query": query, "hit_count": len(matches)})
        refined: str | None = None

        if adaptive and not matches:
            refined = await _rephrase_query(query)
            if refined:
                matches = await search_passages(retriever, text=refined, k=k)
                attempts.append({"query": refined, "hit_count": len(matches)})

        return _json_ok(
            {
                "passages": [
                    {
                        "text": m.chunk_text,
                        "source_doi": m.source.doi,
                        "source_url": m.source.source_url,
                        "license_id": m.source.license_id,
                        "score": m.score,
                        "kb_name": m.kb_name,
                    }
                    for m in matches
                ],
                "attempts": attempts,
                "refined_query": refined,
            }
        )

    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        logger.error("mcp_get_relevant_passages_error", error=str(e))
        return _json_error(f"get_relevant_passages failed: {e}")
```

Add `"get_relevant_passages"` to `__all__`.

- [ ] **Step 5: Verify query_optimizer.optimize_query signature**

If `optimize_query` doesn't exist or has a different signature, adapt the `_rephrase_query` helper to match. Run:

```bash
grep -n "def \|class " /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/search/query_optimizer.py
```

If the entry point differs, update `_rephrase_query` accordingly. Do not change the test — patch the helper, not the optimizer.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/test_mcp_get_relevant_passages.py -v
```

Expected: 3 pass.

- [ ] **Step 7: Run full unit suite**

```bash
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -20
```

Expected: no regression.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_get_relevant_passages.py
git commit -m "feat(mcp): get_relevant_passages tool with adaptive query retry"
```

---

## Task 4: Extraction core module (Perspicacité)

**Files:**
- Create: `src/perspicacite/pipeline/extraction.py`
- Test: `tests/unit/test_extraction_core.py`

A single LLM-backed module that takes a list of passages and a JSON schema, calls the LLM, salvages malformed JSON, and returns deduplicated structured records. Both `extract_parameters_from_passages` and `extract_failure_modes_from_passages` MCP tools use it.

- [ ] **Step 1: Read the existing JSON-salvage utility**

```bash
grep -rn "json_salvage\|salvage_json\|def parse_json\|def repair_json" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/ | head -5
```

Note the import path and signature. Per commit `6133148`, a shared JSON-salvage utility exists.

- [ ] **Step 2: Read AsyncLLMClient.complete signature**

```bash
grep -n "async def complete\|class AsyncLLMClient" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/llm/client.py | head -10
```

Note the call shape: `await client.complete(messages=[...], model=..., temperature=..., max_tokens=...)`.

- [ ] **Step 3: Read license tier categorisation reference**

Read `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/license_safety.py` lines 30–140. We re-implement the same Tier A/B/C classifier server-side (avoid a runtime dependency on ASB).

- [ ] **Step 4: Write the failing test**

Create `tests/unit/test_extraction_core.py`:

```python
"""Tests for src/perspicacite/pipeline/extraction.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from perspicacite.pipeline.extraction import (
    Passage,
    classify_license_tier,
    extract_structured,
    handle_quote_for_license,
)


def _llm(json_str: str):
    client = AsyncMock()
    client.complete = AsyncMock(return_value=json_str)
    return client


def test_classify_license_tier():
    assert classify_license_tier("CC-BY") == "A"
    assert classify_license_tier("MIT") == "A"
    assert classify_license_tier("CC0") == "A"
    assert classify_license_tier("CC-BY-NC") == "B"
    assert classify_license_tier("CC-BY-ND") == "B"
    assert classify_license_tier("all rights reserved") == "C"
    assert classify_license_tier(None) == "C"
    assert classify_license_tier("") == "C"


def test_handle_quote_for_license_tier_a_keeps_verbatim():
    out = handle_quote_for_license("hello world", license_id="CC-BY")
    assert out == "hello world"


def test_handle_quote_for_license_tier_b_short_keeps():
    short = "x" * 250
    out = handle_quote_for_license(short, license_id="CC-BY-NC")
    assert out == short


def test_handle_quote_for_license_tier_b_long_paraphrases():
    long = "y" * 350
    out = handle_quote_for_license(
        long, license_id="CC-BY-NC",
        paraphraser=lambda s: f"PARAPHRASED:{s[:5]}",
    )
    assert out == "PARAPHRASED:yyyyy"


def test_handle_quote_for_license_tier_c_paraphrases():
    out = handle_quote_for_license(
        "secret text", license_id=None,
        paraphraser=lambda s: f"P:{s}",
    )
    assert out == "P:secret text"


def test_handle_quote_for_license_tier_c_no_paraphraser_drops():
    out = handle_quote_for_license("secret", license_id=None, paraphraser=None)
    assert out is None


@pytest.mark.asyncio
async def test_extract_structured_happy_path():
    llm = _llm('[{"name":"temp","typical":"37","units":"C"}]')
    schema = {"type": "array"}
    passages = [Passage(text="grew at 37 C", source_doi="10/a", license_id="CC-BY")]

    out = await extract_structured(
        llm_client=llm,
        passages=passages,
        prompt_template="Extract {what}",
        schema=schema,
        what="parameters",
        dedup_key=lambda r: (r.get("name"), r.get("units")),
    )

    assert out == [{"name": "temp", "typical": "37", "units": "C"}]
    llm.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_structured_dedups():
    llm = _llm(
        '[{"name":"temp","units":"C"},{"name":"temp","units":"C"},{"name":"pH","units":""}]'
    )
    out = await extract_structured(
        llm_client=llm,
        passages=[Passage(text="x", source_doi="d", license_id="CC-BY")],
        prompt_template="x",
        schema={},
        what="p",
        dedup_key=lambda r: (r.get("name"), r.get("units")),
    )
    assert [r["name"] for r in out] == ["temp", "pH"]


@pytest.mark.asyncio
async def test_extract_structured_invalid_json_returns_empty_with_warning():
    llm = _llm("not json at all {{{")
    out = await extract_structured(
        llm_client=llm,
        passages=[Passage(text="x", source_doi="d", license_id="CC-BY")],
        prompt_template="x",
        schema={},
        what="p",
        dedup_key=lambda r: tuple(r.items()),
    )
    assert out == []


@pytest.mark.asyncio
async def test_extract_structured_empty_passages_returns_empty():
    llm = AsyncMock()
    llm.complete = AsyncMock()
    out = await extract_structured(
        llm_client=llm,
        passages=[],
        prompt_template="x",
        schema={},
        what="p",
        dedup_key=lambda r: tuple(r.items()),
    )
    assert out == []
    llm.complete.assert_not_awaited()
```

- [ ] **Step 5: Run and confirm failure**

```bash
uv run pytest tests/unit/test_extraction_core.py -v
```

Expected: ImportError on `perspicacite.pipeline.extraction`.

- [ ] **Step 6: Implement extraction.py**

Create `src/perspicacite/pipeline/extraction.py`:

```python
"""LLM-backed structured extraction from passages.

Shared core behind ``extract_parameters_from_passages`` and
``extract_failure_modes_from_passages`` MCP tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Protocol

# Tier A — verbatim safe.
_TIER_A_PATTERNS = (
    r"^cc0", r"^public.?domain", r"^cc.?by(?!-)", r"^mit", r"^apache",
    r"^bsd", r"^isc", r"^unlicense",
)
# Tier B — quote with caution (CC-BY-NC / -ND / -SA combinations).
_TIER_B_PATTERNS = (
    r"^cc.?by.?nc", r"^cc.?by.?nd", r"^cc.?by.?sa",
)
_TIER_B_VERBATIM_MAX_CHARS = 300
_BATCH_SIZE = 8


def classify_license_tier(license_id: str | None) -> str:
    """Return 'A', 'B', or 'C' for the given license_id."""
    norm = (license_id or "").strip().lower().replace(" ", "-")
    if not norm:
        return "C"
    for pat in _TIER_A_PATTERNS:
        if re.match(pat, norm):
            return "A"
    for pat in _TIER_B_PATTERNS:
        if re.match(pat, norm):
            return "B"
    return "C"


def handle_quote_for_license(
    text: str,
    *,
    license_id: str | None,
    paraphraser: Callable[[str], str] | None = None,
) -> str | None:
    """Apply Tier A/B/C policy to a quoted source string.

    Returns None when the policy says drop and no paraphraser is supplied.
    """
    tier = classify_license_tier(license_id)
    if tier == "A":
        return text
    if tier == "B":
        if len(text) <= _TIER_B_VERBATIM_MAX_CHARS:
            return text
        if paraphraser is None:
            return None
        return paraphraser(text)
    # Tier C
    if paraphraser is None:
        return None
    return paraphraser(text)


@dataclass(frozen=True)
class Passage:
    text: str
    source_doi: str
    license_id: str | None = None
    source_url: str | None = None


class _LLM(Protocol):
    async def complete(self, *, messages: list[dict], **kwargs: Any) -> str:
        ...


def _try_parse_json(raw: str) -> list[dict] | None:
    """Two-stage parse: direct, then trivial salvage (strip surrounding text)."""
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        pass
    # Salvage: take first [...] block
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else None
    except json.JSONDecodeError:
        return None


def _build_prompt(template: str, batch: list[Passage], context: str | None) -> str:
    lines = [template]
    if context:
        lines.append(f"Context: {context}")
    lines.append("Passages:")
    for i, p in enumerate(batch, 1):
        lines.append(f"[{i}] DOI={p.source_doi}\n{p.text}")
    lines.append(
        "Return JSON array. Each item must include the keys you were asked to "
        "extract, plus 'source_doi' (the [n] DOI it came from)."
    )
    return "\n\n".join(lines)


async def extract_structured(
    *,
    llm_client: _LLM,
    passages: list[Passage],
    prompt_template: str,
    schema: dict[str, Any],
    what: str,
    dedup_key: Callable[[dict], tuple],
    context: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """Run LLM extraction across batches of passages with JSON salvage + dedup.

    Returns [] on total failure rather than raising — callers log and move on.
    """
    if not passages:
        return []

    seen: set[tuple] = set()
    out: list[dict] = []

    for start in range(0, len(passages), _BATCH_SIZE):
        batch = passages[start : start + _BATCH_SIZE]
        prompt = _build_prompt(prompt_template, batch, context)
        kwargs: dict[str, Any] = {"temperature": 0.1, "max_tokens": 1500}
        if model:
            kwargs["model"] = model

        raw = await llm_client.complete(
            messages=[{"role": "user", "content": prompt}], **kwargs
        )
        records = _try_parse_json(raw)
        if not records:
            continue

        for r in records:
            if not isinstance(r, dict):
                continue
            key = dedup_key(r)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)

    return out
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/unit/test_extraction_core.py -v
```

Expected: 10 pass.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/pipeline/extraction.py tests/unit/test_extraction_core.py
git commit -m "feat(pipeline): LLM-backed structured extraction core with license tiers"
```

---

## Task 5: `extract_parameters_from_passages` MCP tool (Perspicacité)

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_mcp_extract_parameters.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_mcp_extract_parameters.py`:

```python
"""Tests for the ``extract_parameters_from_passages`` MCP tool."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_extract_parameters_returns_records():
    state = MagicMock()
    state.llm_client = AsyncMock()
    state.llm_client.complete = AsyncMock(
        return_value='[{"name":"temperature","typical":"37","units":"C","source_doi":"10/a"}]'
    )
    with patch.object(mcp_server, "_require_state", return_value=state):
        raw = await mcp_server.extract_parameters_from_passages(
            passages=[
                {
                    "text": "Cells grown at 37 C",
                    "source_doi": "10/a",
                    "license_id": "CC-BY",
                }
            ],
            context="cell-culture",
        )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert len(payload["parameters"]) == 1
    assert payload["parameters"][0]["name"] == "temperature"
    assert payload["parameters"][0]["units"] == "C"


@pytest.mark.asyncio
async def test_extract_parameters_empty_passages_returns_empty():
    state = MagicMock()
    state.llm_client = AsyncMock()
    state.llm_client.complete = AsyncMock()
    with patch.object(mcp_server, "_require_state", return_value=state):
        raw = await mcp_server.extract_parameters_from_passages(passages=[])
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["parameters"] == []
    state.llm_client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_parameters_license_tier_c_drops_quote():
    state = MagicMock()
    state.llm_client = AsyncMock()
    state.llm_client.complete = AsyncMock(
        return_value=(
            '[{"name":"pH","typical":"7.4","units":"","source_doi":"10/x",'
            '"source_quote":"verbatim text from closed paper"}]'
        )
    )
    with patch.object(mcp_server, "_require_state", return_value=state):
        raw = await mcp_server.extract_parameters_from_passages(
            passages=[
                {
                    "text": "pH 7.4 buffer used",
                    "source_doi": "10/x",
                    "license_id": "all rights reserved",
                }
            ],
        )
    payload = json.loads(raw)
    assert payload["ok"] is True
    p = payload["parameters"][0]
    # Quote either omitted or paraphrased; never verbatim closed-source text.
    assert p.get("source_quote") != "verbatim text from closed paper"
```

- [ ] **Step 2: Run and confirm failure**

```bash
uv run pytest tests/unit/test_mcp_extract_parameters.py -v
```

Expected: AttributeError.

- [ ] **Step 3: Implement the tool**

Append to `src/perspicacite/mcp/server.py` before `__all__`:

```python
# =============================================================================
# Tool: extract_parameters_from_passages
# =============================================================================

_PARAM_EXTRACTION_PROMPT = """\
You are extracting numeric experimental or methodological parameters from
scientific passages. Return a JSON array of objects with keys:
  name, type ("numeric"|"categorical"), typical, units, min, max,
  source_doi, source_quote, confidence (0..1)

Only include parameters explicitly stated in the passages. If a value is
absent, omit that key. Skip parameters not relevant to {context}.
"""


@mcp.tool()
async def extract_parameters_from_passages(
    passages: list[dict],
    context: str | None = None,
    parameter_families: list[str] | None = None,
    model: str | None = None,
) -> str:
    """
    Extract structured numeric parameters (thresholds, concentrations, ranges)
    from a list of passages using an LLM with JSON-schema-style output.

    Args:
        passages: list of {text, source_doi, license_id?, source_url?}
        context: Optional domain/skill hint to guide extraction.
        parameter_families: Optional list of family names to bias the LLM
            (e.g., ["threshold","concentration","pH","temperature"]).
        model: Optional model override (LiteLLM-style "provider/model").

    Returns:
        JSON {"ok": True, "parameters": [...]} or {"ok": False, "error": "..."}.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        from perspicacite.pipeline.extraction import (
            Passage,
            extract_structured,
            handle_quote_for_license,
        )

        passage_objs = [
            Passage(
                text=str(p.get("text", "")),
                source_doi=str(p.get("source_doi", "")),
                license_id=p.get("license_id"),
                source_url=p.get("source_url"),
            )
            for p in passages
            if p.get("text")
        ]

        families = parameter_families or [
            "threshold", "concentration", "pH",
            "temperature", "time", "rate",
        ]
        prompt = _PARAM_EXTRACTION_PROMPT.format(context=context or "general")
        prompt += f"\nFocus on these families when relevant: {', '.join(families)}."

        records = await extract_structured(
            llm_client=state.llm_client,
            passages=passage_objs,
            prompt_template=prompt,
            schema={},
            what="parameters",
            context=context,
            dedup_key=lambda r: (r.get("name"), r.get("units")),
            model=model,
        )

        # Apply license-tier policy to source_quote on each record.
        doi_to_license = {
            p.source_doi: p.license_id for p in passage_objs
        }
        cleaned: list[dict] = []
        for r in records:
            quote = r.get("source_quote")
            if quote:
                tier_quote = handle_quote_for_license(
                    str(quote),
                    license_id=doi_to_license.get(r.get("source_doi", "")),
                    paraphraser=None,  # MVP: drop when paraphraser is unavailable
                )
                if tier_quote is None:
                    r = {k: v for k, v in r.items() if k != "source_quote"}
                else:
                    r = {**r, "source_quote": tier_quote}
            cleaned.append(r)

        return _json_ok({"parameters": cleaned})

    except Exception as e:
        logger.error("mcp_extract_parameters_error", error=str(e))
        return _json_error(f"extract_parameters_from_passages failed: {e}")
```

Add `"extract_parameters_from_passages"` to `__all__`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_mcp_extract_parameters.py -v
```

Expected: 3 pass.

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_extract_parameters.py
git commit -m "feat(mcp): extract_parameters_from_passages tool with license-tier quote handling"
```

---

## Task 6: `extract_failure_modes_from_passages` MCP tool (Perspicacité)

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_mcp_extract_failure_modes.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/test_mcp_extract_failure_modes.py`:

```python
"""Tests for the ``extract_failure_modes_from_passages`` MCP tool."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_extract_failure_modes_returns_records():
    state = MagicMock()
    state.llm_client = AsyncMock()
    state.llm_client.complete = AsyncMock(
        return_value=(
            '[{"symptom":"fails on dilute samples",'
            '"root_cause":"detection limit","mitigation":"concentrate first",'
            '"source_doi":"10/a","confidence":0.9}]'
        )
    )
    with patch.object(mcp_server, "_require_state", return_value=state):
        raw = await mcp_server.extract_failure_modes_from_passages(
            passages=[
                {
                    "text": "Method fails on dilute samples below the LOD.",
                    "source_doi": "10/a",
                    "license_id": "CC-BY",
                }
            ],
            context="LC-MS quantification",
        )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert len(payload["failure_modes"]) == 1
    assert "dilute" in payload["failure_modes"][0]["symptom"]


@pytest.mark.asyncio
async def test_extract_failure_modes_empty_returns_empty():
    state = MagicMock()
    state.llm_client = AsyncMock()
    state.llm_client.complete = AsyncMock()
    with patch.object(mcp_server, "_require_state", return_value=state):
        raw = await mcp_server.extract_failure_modes_from_passages(passages=[])
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["failure_modes"] == []
    state.llm_client.complete.assert_not_awaited()
```

- [ ] **Step 2: Run and confirm failure**

```bash
uv run pytest tests/unit/test_mcp_extract_failure_modes.py -v
```

- [ ] **Step 3: Implement the tool**

Append to `src/perspicacite/mcp/server.py` before `__all__`:

```python
# =============================================================================
# Tool: extract_failure_modes_from_passages
# =============================================================================

_FAILURE_EXTRACTION_PROMPT = """\
You are extracting failure modes, limitations, caveats, and pitfalls from
scientific passages. Return a JSON array of objects with keys:
  symptom (one sentence), root_cause, mitigation, source_doi,
  source_quote, confidence (0..1)

Only include failure modes explicitly stated. Skip generic disclaimers.
Domain context: {context}.
"""


@mcp.tool()
async def extract_failure_modes_from_passages(
    passages: list[dict],
    context: str | None = None,
    model: str | None = None,
) -> str:
    """
    Extract structured failure modes from a list of passages using an LLM.

    Args:
        passages: list of {text, source_doi, license_id?, source_url?}
        context: Optional domain/skill hint.
        model: Optional model override.

    Returns:
        JSON {"ok": True, "failure_modes": [...]} or {"ok": False, "error": "..."}.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        from perspicacite.pipeline.extraction import (
            Passage,
            extract_structured,
            handle_quote_for_license,
        )

        passage_objs = [
            Passage(
                text=str(p.get("text", "")),
                source_doi=str(p.get("source_doi", "")),
                license_id=p.get("license_id"),
                source_url=p.get("source_url"),
            )
            for p in passages
            if p.get("text")
        ]

        prompt = _FAILURE_EXTRACTION_PROMPT.format(context=context or "general")

        records = await extract_structured(
            llm_client=state.llm_client,
            passages=passage_objs,
            prompt_template=prompt,
            schema={},
            what="failure_modes",
            context=context,
            dedup_key=lambda r: (str(r.get("symptom", "")).strip().lower(),),
            model=model,
        )

        doi_to_license = {p.source_doi: p.license_id for p in passage_objs}
        cleaned: list[dict] = []
        for r in records:
            quote = r.get("source_quote")
            if quote:
                tier_quote = handle_quote_for_license(
                    str(quote),
                    license_id=doi_to_license.get(r.get("source_doi", "")),
                    paraphraser=None,
                )
                if tier_quote is None:
                    r = {k: v for k, v in r.items() if k != "source_quote"}
                else:
                    r = {**r, "source_quote": tier_quote}
            cleaned.append(r)

        return _json_ok({"failure_modes": cleaned})

    except Exception as e:
        logger.error("mcp_extract_failure_modes_error", error=str(e))
        return _json_error(f"extract_failure_modes_from_passages failed: {e}")
```

Add `"extract_failure_modes_from_passages"` to `__all__`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_mcp_extract_failure_modes.py -v
```

Expected: 2 pass.

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_extract_failure_modes.py
git commit -m "feat(mcp): extract_failure_modes_from_passages tool"
```

---

## Task 7: ASB client — wire the four new methods

**Files:**
- Modify: `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/perspicacite_client.py`
- Test: `/Users/holobiomicslab/git/AgenticScienceBuilder/tests/test_perspicacite_client.py` (find existing or create)

- [ ] **Step 1: Locate or create the test file**

```bash
cd /Users/holobiomicslab/git/AgenticScienceBuilder
ls tests/test_perspicacite_client.py 2>/dev/null || ls tests/ | grep -i perspic
```

If a test file exists, append to it. If not, create one — ASB uses `unittest`.

- [ ] **Step 2: Add the failing test cases**

Append (or create) `tests/test_perspicacite_client.py`:

```python
"""Tests for src/agentic_science_builder/perspicacite_client.py — new methods."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from agentic_science_builder.perspicacite_client import (
    MCPPerspicaciteClient,
    Passage,
)


class TestGetRelevantPassagesAdaptive(unittest.TestCase):
    def test_parses_extended_response_with_attempts(self):
        client = MCPPerspicaciteClient(base_url="http://x", session_id_hint="s")
        fake_response = {
            "passages": [
                {
                    "text": "found content",
                    "source_doi": "10/a",
                    "source_url": "http://x/a",
                    "license_id": "CC-BY",
                }
            ],
            "attempts": [
                {"query": "orig", "hit_count": 0},
                {"query": "refined", "hit_count": 1},
            ],
            "refined_query": "refined",
        }
        with patch.object(
            client, "_sess",
            return_value=MagicMock(call_tool=MagicMock(return_value=fake_response)),
        ):
            results = client.get_relevant_passages("orig", k=5, adaptive=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "found content")

    def test_back_compat_when_response_is_plain_list(self):
        client = MCPPerspicaciteClient(base_url="http://x", session_id_hint="s")
        fake_response = [
            {"text": "x", "source_doi": "10/a", "license_id": "MIT"}
        ]
        with patch.object(
            client, "_sess",
            return_value=MagicMock(call_tool=MagicMock(return_value=fake_response)),
        ):
            results = client.get_relevant_passages("orig", k=5)
        self.assertEqual(len(results), 1)


class TestSearchByPassage(unittest.TestCase):
    def test_search_by_passage_parses_results(self):
        client = MCPPerspicaciteClient(base_url="http://x", session_id_hint="s")
        fake = {
            "results": [
                {
                    "chunk_id": "c1",
                    "chunk_text": "content",
                    "score": 0.9,
                    "source": {
                        "doi": "10/a",
                        "title": "T",
                        "year": 2024,
                        "license_id": "CC-BY",
                        "source_url": "http://x",
                    },
                    "kb_name": "kb",
                }
            ],
        }
        with patch.object(
            client, "_sess",
            return_value=MagicMock(call_tool=MagicMock(return_value=fake)),
        ):
            out = client.search_by_passage("a paragraph", k=3)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].text, "content")
        self.assertEqual(out[0].source_doi, "10/a")


class TestExtractParameters(unittest.TestCase):
    def test_extract_parameters_passes_through(self):
        client = MCPPerspicaciteClient(base_url="http://x", session_id_hint="s")
        fake = {
            "parameters": [
                {"name": "temp", "typical": "37", "units": "C", "source_doi": "10/a"}
            ]
        }
        with patch.object(
            client, "_sess",
            return_value=MagicMock(call_tool=MagicMock(return_value=fake)),
        ):
            out = client.extract_parameters(
                passages=[Passage(text="grew at 37C", source_doi="10/a", license_id="CC-BY")],
                context="cell culture",
            )
        self.assertEqual(out, fake["parameters"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run and confirm failure**

```bash
cd /Users/holobiomicslab/git/AgenticScienceBuilder
PYTHONPATH=src python3 -m unittest tests.test_perspicacite_client -v
```

Expected: AttributeError on `search_by_passage` and/or `extract_parameters` (the new methods don't exist yet).

- [ ] **Step 4: Modify perspicacite_client.py**

Open `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/perspicacite_client.py`.

Replace the existing `get_relevant_passages` method (lines ~472–496) with:

```python
    def get_relevant_passages(
        self,
        query: str,
        *,
        paper_doi: str | None = None,
        k: int = 10,
        adaptive: bool = False,
    ) -> list[Passage]:
        """Keyword-style passage retrieval via Perspicacité MCP.

        When ``adaptive=True`` the server retries with a rephrased query on
        empty first-pass results. Either way, this method returns the parsed
        Passage list and discards the ``attempts`` / ``refined_query``
        metadata (callers that want them should switch to a raw call).
        """
        args: dict = {"query": query, "k": k, "adaptive": adaptive}
        if paper_doi:
            args["paper_doi"] = paper_doi
        try:
            raw = self._sess().call_tool("get_relevant_passages", args)
        except RuntimeError:
            return []

        # Server contract: {ok, passages, attempts, refined_query?}
        # Back-compat: tolerate either a plain list (legacy stub) or a
        # dict shaped like {"passages": [...]} or {"result": [...]}.
        if isinstance(raw, list):
            rows = raw
        elif isinstance(raw, dict):
            rows = (
                raw.get("passages")
                or raw.get("result")
                or []
            )
        else:
            rows = []

        out: list[Passage] = []
        for r in rows:
            text = r.get("text") or r.get("passage")
            if not text:
                continue
            out.append(
                Passage(
                    text=str(text),
                    source_doi=str(r.get("source_doi") or r.get("doi") or ""),
                    source_url=r.get("source_url"),
                    license_id=r.get("license_id") or r.get("license"),
                )
            )
        return out
```

Then append these two new methods immediately after `get_relevant_passages`:

```python
    def search_by_passage(
        self,
        text: str,
        *,
        kb_names: list[str] | None = None,
        k: int = 5,
        min_score: float | None = None,
    ) -> list[Passage]:
        """Paragraph/sentence-level retrieval. Returns ``Passage`` records.

        New in 2026-05-20 — corresponds to the Perspicacité
        ``search_by_passage`` MCP tool.
        """
        args: dict = {"text": text, "k": k}
        if kb_names:
            args["kb_names"] = kb_names
        if min_score is not None:
            args["min_score"] = min_score
        try:
            raw = self._sess().call_tool("search_by_passage", args)
        except RuntimeError:
            return []

        rows = (
            raw.get("results")
            if isinstance(raw, dict)
            else raw if isinstance(raw, list) else []
        )
        out: list[Passage] = []
        for r in rows or []:
            chunk_text = r.get("chunk_text") or r.get("text")
            if not chunk_text:
                continue
            src = r.get("source") or {}
            out.append(
                Passage(
                    text=str(chunk_text),
                    source_doi=str(src.get("doi") or ""),
                    source_url=src.get("source_url"),
                    license_id=src.get("license_id"),
                )
            )
        return out

    def extract_parameters(
        self,
        *,
        passages: list[Passage],
        context: str | None = None,
        parameter_families: list[str] | None = None,
    ) -> list[dict]:
        """Server-side LLM extraction of numeric parameters from passages."""
        args: dict = {
            "passages": [
                {
                    "text": p.text,
                    "source_doi": p.source_doi,
                    "license_id": p.license_id,
                    "source_url": p.source_url,
                }
                for p in passages
            ],
        }
        if context:
            args["context"] = context
        if parameter_families:
            args["parameter_families"] = parameter_families
        try:
            raw = self._sess().call_tool("extract_parameters_from_passages", args)
        except RuntimeError:
            return []
        if isinstance(raw, dict):
            return list(raw.get("parameters") or [])
        return []

    def extract_failure_modes(
        self,
        *,
        passages: list[Passage],
        context: str | None = None,
    ) -> list[dict]:
        """Server-side LLM extraction of failure modes from passages."""
        args: dict = {
            "passages": [
                {
                    "text": p.text,
                    "source_doi": p.source_doi,
                    "license_id": p.license_id,
                    "source_url": p.source_url,
                }
                for p in passages
            ],
        }
        if context:
            args["context"] = context
        try:
            raw = self._sess().call_tool(
                "extract_failure_modes_from_passages", args,
            )
        except RuntimeError:
            return []
        if isinstance(raw, dict):
            return list(raw.get("failure_modes") or [])
        return []
```

Also remove the now-misleading comment at line 441 (`# --- Search / passages (no Perspicacité analog yet — see spec) ----------`) — replace with `# --- Search / passages -------------------------------------------------`.

- [ ] **Step 5: Run tests**

```bash
cd /Users/holobiomicslab/git/AgenticScienceBuilder
PYTHONPATH=src python3 -m unittest tests.test_perspicacite_client -v
```

Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_science_builder/perspicacite_client.py tests/test_perspicacite_client.py
git commit -m "feat(perspicacite-client): wire search_by_passage + extract_* methods; adaptive flag for get_relevant_passages"
```

---

## Task 8: ASB enrichment — replace regex with MCP extraction

**Files:**
- Modify: `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/skill_pack_v3.py` (MCP-path functions only — leave `extract_parameters_from_skill` and `extract_parameters_from_eval_signals` untouched)

- [ ] **Step 1: Read the current MCP-path functions**

```bash
sed -n '1280,1410p' /Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/skill_pack_v3.py
```

Locate the functions `enrich_parameters_from_passages` and `enrich_failure_modes_from_passages`. Confirm they currently use `_VALUE_TOKEN_RE`, `_PARAM_NAME_KEYWORDS`, and `_FAILURE_TRIGGER_RE` on passage text.

- [ ] **Step 2: Write a failing integration test**

Append to `tests/test_perspicacite_client.py` (or create `tests/test_skill_pack_v3_mcp_extraction.py`). Use a real `tmp_path` for `skill_dir` because the refactored function will still write `parameters.json` / `failure_modes.jsonl` to disk:

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agentic_science_builder.perspicacite_client import Passage


class TestEnrichParametersMCPPath(unittest.TestCase):
    """Verify enrich_parameters_from_passages uses the MCP extraction tool."""

    def test_calls_client_extract_parameters_not_regex(self):
        from agentic_science_builder import skill_pack_v3

        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td)
            client = MagicMock()
            client.get_relevant_passages = MagicMock(return_value=[
                Passage(text="grew at 37C", source_doi="10/a", license_id="CC-BY"),
            ])
            client.extract_parameters = MagicMock(return_value=[
                {
                    "name": "temperature",
                    "typical": "37",
                    "units": "C",
                    "source_doi": "10/a",
                    "source_quote": "grew at 37C",
                }
            ])

            added = skill_pack_v3.enrich_parameters_from_passages(
                skill_dir=skill_dir,
                client=client,
                query="cell-growth",
                skill_name="cell_growth",
            )

            client.extract_parameters.assert_called_once()
            self.assertEqual(added, 1)
            # The function must persist the records — assert the file exists
            # and contains the temperature record.
            out_file = skill_dir / "parameters.json"
            self.assertTrue(out_file.exists())
            persisted = json.loads(out_file.read_text())
            self.assertTrue(any(p.get("name") == "temperature" for p in persisted))
```

If the actual signature of `enrich_parameters_from_passages` in the current codebase differs (positional args, different param names), update the test call accordingly before the implementation step. The core assertions (`client.extract_parameters` was called; output file gained the record) should remain.

- [ ] **Step 3: Run and confirm failure**

```bash
PYTHONPATH=src python3 -m unittest tests.test_perspicacite_client.TestEnrichParametersMCPPath -v
```

- [ ] **Step 4: Rewrite `enrich_parameters_from_passages`**

In `skill_pack_v3.py`, replace the body of `enrich_parameters_from_passages` (currently ~lines 1280–1340) with:

```python
def enrich_parameters_from_passages(
    *,
    skill_dir,
    client,
    query: str,
    skill_name: str,
    families: list[str] | None = None,
) -> int:
    """Mine cross-paper parameter values via Perspicacité MCP extraction.

    Replaces the previous regex-mining path (2026-05-20). The server
    handles license-tier policy on ``source_quote`` so the local
    ``_LicenseSafeClientShim`` is not used in this path.
    """
    passages = client.get_relevant_passages(query, k=10, adaptive=True)
    if not passages:
        return 0

    records = client.extract_parameters(
        passages=passages,
        context=skill_name,
        parameter_families=families,
    )
    if not records:
        return 0

    out: list[ParameterSpec] = []
    seen: set[tuple] = set()
    for r in records:
        key = (str(r.get("name", "")).lower(), str(r.get("units", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ParameterSpec(
                name=str(r.get("name", "")),
                type=str(r.get("type") or "numeric"),
                typical=str(r.get("typical") or ""),
                units=str(r.get("units") or ""),
                min=str(r.get("min") or ""),
                max=str(r.get("max") or ""),
                source_citation=str(r.get("source_quote") or ""),
                source_url=str(r.get("source_url") or ""),
                provenance="perspicacite_mcp",
                source_doi=str(r.get("source_doi") or ""),
            )
        )

    _append_parameters_json(skill_dir, out)
    return len(out)
```

Then do the same for `enrich_failure_modes_from_passages` (replacing the `_FAILURE_TRIGGER_RE` body):

```python
def enrich_failure_modes_from_passages(
    *,
    skill_dir,
    client,
    query: str,
    skill_name: str,
) -> int:
    """Mine cross-paper failure modes via Perspicacité MCP extraction."""
    passages = client.get_relevant_passages(query, k=10, adaptive=True)
    if not passages:
        return 0

    records = client.extract_failure_modes(
        passages=passages, context=skill_name,
    )
    if not records:
        return 0

    out: list[FailureMode] = []
    seen: set[str] = set()
    for r in records:
        sym = str(r.get("symptom", "")).strip()
        key = sym.lower()
        if not sym or key in seen:
            continue
        seen.add(key)
        out.append(
            FailureMode(
                symptom=sym,
                root_cause=str(r.get("root_cause") or "") or None,
                mitigation=str(r.get("mitigation") or "") or None,
                source_url=str(r.get("source_url") or ""),
                provenance="perspicacite_mcp",
                source_doi=str(r.get("source_doi") or ""),
            )
        )

    _append_failure_modes_jsonl(skill_dir, out)
    return len(out)
```

**Persistence helpers — verify before naming:** The current MCP-path functions in `skill_pack_v3.py` already write `parameters.json` and `failure_modes.jsonl` somehow. Before adopting `_append_parameters_json` / `_append_failure_modes_jsonl`, grep the file for the actual write idiom:

```bash
grep -n "parameters.json\|failure_modes.jsonl\|json.dump\|with open" /Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/skill_pack_v3.py | head -20
```

Three plausible patterns: (a) an existing private helper — use it as-is; (b) an inline `with open(...)` block — extract into a helper of the same name we used above for clarity; (c) a class method — call it. Pick whichever matches the current code with the smallest change. Whatever you name them, the test from Step 2 only asserts the file exists and contains the record — it does not assume any specific helper name.

The regex constants `_VALUE_TOKEN_RE`, `_PARAM_NAME_KEYWORDS`, `_FAILURE_TRIGGER_RE` are still used by the SKILL-MD path (`extract_parameters_from_skill`, `extract_failure_modes_from_skill`, `extract_parameters_from_eval_signals`) — **do not remove them**. Only the MCP-path functions change.

- [ ] **Step 5: Run unit tests**

```bash
PYTHONPATH=src python3 -m unittest tests.test_perspicacite_client -v
```

Then full suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests 2>&1 | tail -30
```

Expected: new tests pass, prior tests still pass. If any pre-existing test for these enrichment functions fails because it relied on regex output specifics, update it to assert on `client.extract_parameters` being called rather than on regex-derived numeric values.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_science_builder/skill_pack_v3.py tests/test_perspicacite_client.py
git commit -m "refactor(asb): replace regex parameter/failure-mode mining with MCP extraction tools

The MCP-enrichment path now calls Perspicacité's extract_parameters_from_passages
and extract_failure_modes_from_passages tools. The local _VALUE_TOKEN_RE /
_FAILURE_TRIGGER_RE pipelines remain in place for the skill.md path."
```

---

## Task 9: Scriptorium `/find-related` slash command

**Files:**
- Create: `/Users/holobiomicslab/git/Scriptorium/scriptorium/literature/passage_search.py`
- Create: `/Users/holobiomicslab/git/Scriptorium/.claude/commands/find-related.md`
- Create: `/Users/holobiomicslab/git/Scriptorium/tests/test_passage_search.py`

- [ ] **Step 1: Read existing Scriptorium MCP-call patterns**

```bash
grep -n "perspicacite\|search_literature\|call_tool\|_sess" /Users/holobiomicslab/git/Scriptorium/scriptorium/literature/doi_sources.py | head -30
```

Look at how `PerspicaciteSource` calls the MCP. The new wrapper follows the same shape.

- [ ] **Step 2: Read `refs/kb_manifest.json` format**

```bash
ls /Users/holobiomicslab/git/Scriptorium/refs/ 2>&1
cat /Users/holobiomicslab/git/Scriptorium/refs/kb_manifest.json 2>/dev/null | head -20
```

Confirm the key name for the project KB (e.g., `project_kb_name`). If the file doesn't exist on disk, look for the schema in `scriptorium/literature/`. Note the key for use in the wrapper.

- [ ] **Step 3: Write failing test**

Create `tests/test_passage_search.py`:

```python
"""Tests for scriptorium/literature/passage_search.py."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scriptorium.literature.passage_search import (
    PassageHit,
    find_related,
)


def test_find_related_returns_ranked_hits():
    fake_response = {
        "ok": True,
        "results": [
            {
                "chunk_id": "c1",
                "chunk_text": "neural networks ...",
                "score": 0.92,
                "source": {
                    "doi": "10.x/a",
                    "title": "Title A",
                    "year": 2024,
                    "authors": ["A", "B"],
                    "source_url": "http://x",
                    "license_id": "CC-BY",
                },
                "kb_name": "project_kb",
            },
            {
                "chunk_id": "c2",
                "chunk_text": "convex optimization ...",
                "score": 0.71,
                "source": {
                    "doi": "10.x/b", "title": "Title B", "year": 2023,
                    "authors": ["C"], "source_url": "http://y",
                    "license_id": "CC-BY-NC",
                },
                "kb_name": "project_kb",
            },
        ],
    }
    fake_session = MagicMock()
    fake_session.call_tool = MagicMock(return_value=fake_response)
    with patch(
        "scriptorium.literature.passage_search._mcp_session",
        return_value=fake_session,
    ):
        hits = find_related(
            text="paragraph about neural networks and optimization",
            kb_name="project_kb",
            k=5,
        )
    assert len(hits) == 2
    assert hits[0].score >= hits[1].score
    assert hits[0].doi == "10.x/a"


def test_find_related_handles_empty_text():
    with pytest.raises(ValueError):
        find_related(text="   ", kb_name="project_kb")
```

- [ ] **Step 4: Run and confirm failure**

```bash
cd /Users/holobiomicslab/git/Scriptorium
uv run pytest tests/test_passage_search.py -v
```

Expected: ImportError on `scriptorium.literature.passage_search`.

- [ ] **Step 5: Implement the wrapper**

Create `scriptorium/literature/passage_search.py`:

```python
"""Thin wrapper around perspicacite:search_by_passage.

Used by ``/find-related`` to surface ranked paper suggestions for a
sentence / paragraph the writer is composing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Imported here for monkey-patching in tests. Real implementation lives
# alongside the existing PerspicaciteSource in doi_sources.py — we reuse
# the same MCP session machinery to avoid drift.
from scriptorium.literature.doi_sources import (
    _open_perspicacite_session as _mcp_session,
)


@dataclass(frozen=True)
class PassageHit:
    doi: str | None
    title: str | None
    year: int | None
    authors: list[str] | None
    score: float
    chunk_text: str
    source_url: str | None
    license_id: str | None
    kb_name: str | None


def find_related(
    *,
    text: str,
    kb_name: str | None = None,
    kb_names: list[str] | None = None,
    k: int = 5,
    min_score: float | None = None,
) -> list[PassageHit]:
    """Return top-k papers from the KB whose chunks are similar to ``text``."""
    if not text or not text.strip():
        raise ValueError("input text is empty")

    args: dict[str, Any] = {"text": text, "k": k}
    if kb_names:
        args["kb_names"] = kb_names
    elif kb_name:
        args["kb_name"] = kb_name
    if min_score is not None:
        args["min_score"] = min_score

    payload = _mcp_session().call_tool("search_by_passage", args)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return []

    out: list[PassageHit] = []
    for r in payload.get("results") or []:
        src = r.get("source") or {}
        out.append(
            PassageHit(
                doi=src.get("doi"),
                title=src.get("title"),
                year=src.get("year"),
                authors=src.get("authors"),
                score=float(r.get("score") or 0.0),
                chunk_text=str(r.get("chunk_text") or ""),
                source_url=src.get("source_url"),
                license_id=src.get("license_id"),
                kb_name=r.get("kb_name"),
            )
        )
    return out
```

If `_open_perspicacite_session` isn't the actual name in `doi_sources.py`, look for the existing MCP-session helper (e.g., `_get_session`, `_session`, `_client`) and import that instead — adapt the import line and the test patch target consistently.

- [ ] **Step 6: Run test and confirm pass**

```bash
uv run pytest tests/test_passage_search.py -v
```

Expected: 2 tests pass.

- [ ] **Step 7: Create the slash command**

Create `.claude/commands/find-related.md`:

```markdown
---
description: Find papers in the project KB similar to a sentence/paragraph.
argument-hint: "[text? — falls back to selection or current paragraph]"
---

# /find-related

Search the project knowledge base for papers whose chunks are semantically
similar to a sentence or paragraph. Use during drafting to surface
candidates that may already cover the claim you're making.

**Input resolution order:**

1. Explicit text passed as `$ARGUMENTS`
2. The active selection in the editor
3. The paragraph at the cursor

**Steps:**

1. Resolve the text using the order above. If empty, ask the writer to
   select a paragraph or pass text.
2. Read `refs/kb_manifest.json` to find the project KB name (key
   `project_kb_name`).
3. Call:

```python
from scriptorium.literature.passage_search import find_related

hits = find_related(text=<resolved>, kb_name=<project_kb_name>, k=5)
```

4. Render the results as a numbered list:

```
1. [0.92] Smith et al. (2024) — Title A — 10.x/a
   "neural networks …" — CC-BY
2. [0.71] Doe (2023) — Title B — 10.x/b
   "convex optimization …" — CC-BY-NC
…
```

5. Offer the writer `/cite <n>` to insert the chosen paper into
   `refs/references.bib` + KGmemory via the existing citation flow.

**Notes:**

- This is a **suggestion** tool: nothing is inserted into the
  manuscript automatically. The writer always confirms via `/cite`.
- License tags in the output (CC-BY etc.) are informational; the
  citation insertion flow itself doesn't depend on them.
- If the KB is empty or the project's KB name isn't set in
  `refs/kb_manifest.json`, the command emits a clear error rather
  than calling Perspicacité.
```

- [ ] **Step 8: Commit**

```bash
cd /Users/holobiomicslab/git/Scriptorium
git add scriptorium/literature/passage_search.py tests/test_passage_search.py .claude/commands/find-related.md
git commit -m "feat(scriptorium): /find-related slash command for paragraph-level citation search

Wraps Perspicacité's new search_by_passage MCP tool. Writers can ask
for top-k KB papers similar to a sentence/paragraph during drafting;
confirmation flows through existing /cite to update refs/references.bib
and KGmemory."
```

---

## Task 10: Audit scenario coverage

**Files:**
- Locate or create: `/Users/holobiomicslab/git/research-tools-audit/scenarios/combined/08_extraction_tools.yaml` (verify directory name and format in Step 1)

- [ ] **Step 1: Read existing scenario format**

```bash
ls /Users/holobiomicslab/git/research-tools-audit/scenarios/
ls /Users/holobiomicslab/git/research-tools-audit/scenarios/combined/ 2>/dev/null
cat /Users/holobiomicslab/git/research-tools-audit/scenarios/combined/04_*.yaml 2>/dev/null | head -60
```

If the path is different (e.g., `scenarios/asb_persp/` instead of `combined/`), adapt accordingly. Note the YAML schema — particularly the keys for `tools_exercised`, `success_criteria`, `inputs`, and `latency_ms_max`.

- [ ] **Step 2: Add scenario 08**

Create the new YAML using the exact same structure as scenario 04. Template (adjust keys to match the actual format):

```yaml
id: 08_asb_extracts_with_mcp
description: |
  ASB enrichment exercises Perspicacité's structured-extraction MCP tools
  (extract_parameters_from_passages and extract_failure_modes_from_passages)
  end-to-end against a seeded KB fixture, asserting that the resulting
  parameters.json and failure_modes.jsonl entries carry provenance
  "perspicacite_mcp" rather than regex-mined values.

tools_exercised:
  - perspicacite:get_relevant_passages
  - perspicacite:extract_parameters_from_passages
  - perspicacite:extract_failure_modes_from_passages

inputs:
  skill_query: "cell-growth temperature optimization"
  parameter_families: ["temperature", "pH", "concentration"]

success_criteria:
  - tool_no_error: get_relevant_passages
  - tool_no_error: extract_parameters_from_passages
  - tool_no_error: extract_failure_modes_from_passages
  - parameter_count_min: 1
  - failure_mode_count_min: 0
  - provenance_marker_required: "perspicacite_mcp"

latency_ms_max:
  get_relevant_passages: 60000
  extract_parameters_from_passages: 90000
  extract_failure_modes_from_passages: 90000

llm_judge:
  rubric: extraction_quality
  threshold: 0.6
```

- [ ] **Step 3: Run audit suite (if applicable)**

```bash
cd /Users/holobiomicslab/git/research-tools-audit
make help 2>&1 | head -10
```

If `make audit` or a similar one-shot exists, run a dry-run / smoke pass to make sure the scenario YAML parses. Don't run the live audit (LLM-costly) unless explicitly requested.

- [ ] **Step 4: Commit**

```bash
cd /Users/holobiomicslab/git/research-tools-audit
git add scenarios/combined/08_extraction_tools.yaml
git commit -m "test(audit): scenario 08 — ASB exercises Perspicacité extraction tools end-to-end"
```

---

## Final verification

- [ ] **Step 1: Run all three repos' test suites**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI && uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -10
cd /Users/holobiomicslab/git/AgenticScienceBuilder && PYTHONPATH=src python3 -m unittest discover -s tests 2>&1 | tail -10
cd /Users/holobiomicslab/git/Scriptorium && uv run pytest tests/test_passage_search.py -v
```

Expected: all green.

- [ ] **Step 2: Lint Perspicacité**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
uv run ruff check src/perspicacite/retrieval/passage_search.py src/perspicacite/pipeline/extraction.py src/perspicacite/mcp/server.py
uv run mypy src/perspicacite/retrieval/passage_search.py src/perspicacite/pipeline/extraction.py 2>&1 | tail -10
```

Fix any reported issues inline.

- [ ] **Step 3: Manual live-server smoke (optional, prompted)**

If the user wants a live smoke test, start the Perspicacité server (`uv run perspicacite -c config.yml serve`) and:

```bash
# From a Python REPL or quick script
import requests, json
r = requests.post("http://localhost:8000/mcp", json={
    "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
})
tool_names = {t["name"] for t in r.json()["result"]["tools"]}
assert {"search_by_passage", "get_relevant_passages",
        "extract_parameters_from_passages",
        "extract_failure_modes_from_passages"}.issubset(tool_names)
print("New tools registered:", tool_names & {
    "search_by_passage", "get_relevant_passages",
    "extract_parameters_from_passages", "extract_failure_modes_from_passages"})
```

- [ ] **Step 4: Update AGENT_LOG.md and changelog entries**

Both Perspicacité and ASB have `AGENT_LOG.md` files. Add a one-line entry to each summarising the change. Do NOT commit the spec or plan markdown files (per user instruction).

---

## Self-review notes

This plan covers all spec sections except the deferred items (auto-suggest hook in Scriptorium P5 — explicitly out of scope per spec). Open Questions #1 (Haiku 4.5 default) and #2 (no server-side license enforcement for `search_by_passage`) are baked into the implementation. Open Question #3 (embedding-model parity per KB) is implicitly handled because the new tools route through the existing `MultiKBRetriever.check_embedding_compat` / `DynamicKnowledgeBase` machinery that already enforces this.

Per-task commits keep history readable. The plan deliberately leaves regex-extraction code paths intact for the local skill.md ingestion path (only the MCP path changes), which preserves the existing `extract_parameters_from_skill` / `extract_failure_modes_from_skill` contracts.
