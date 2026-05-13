# Multi-KB Expansion, Zotero-as-KB-Source, Local Docs, Smart Chunking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Each task commits directly to `main`** with one conventional commit (this is the user's documented preference for this repo).

**Goal:** Bring multi-KB query into the 4 remaining RAG modes, add Zotero-as-source-of-KB ingest, allow ingesting local documents into any KB, and add content-type-aware chunking.

**Architecture:** Three additive phases. P1 wires fan-out helpers into `advanced` / `profound` / `literature_survey` / `agentic`. P2 extends `ZoteroClient` with read methods and adds a plan-then-execute ingest flow (router + MCP tool + UI). P3 adds local-doc ingest (web + CLI + MCP) backed by a new content-type-aware chunking dispatch (`langchain-text-splitters` for code, regex for markdown headings).

**Tech Stack:** Python 3.12 + uv, FastAPI, ChromaDB, structlog, httpx, fastmcp, pytest. New dep: `langchain-text-splitters>=0.3` (pure-Python, no model weights).

**Spec:** `docs/superpowers/specs/2026-05-13-multi-kb-zotero-local-docs-design.md`

**Carried constraints (from spec §1):**
- Additive-only; existing single-KB callers keep working.
- `uv run pytest tests/unit/ -m "not live"` green from first task to last.
- No new ruff/mypy errors on touched lines (existing backlog explicitly out of scope).
- Per-task conventional commits, directly to `main`.
- New config keys go into `config.example.yml` in the same phase as the feature lands.
- UI verification: file-presence + DOM-shape tests; full click-through goes in `MANUAL_QA.md`.

---

## Phase 1 — Multi-KB query across the four modes

### Task 1: Add fan-out helpers to `retrieval/multi_kb.py`

**Files:**
- Modify: `src/perspicacite/retrieval/multi_kb.py`
- Test: `tests/unit/test_multi_kb_fanout.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_multi_kb_fanout.py`:

```python
"""Unit tests for multi-KB fan-out helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.retrieval.multi_kb import (
    get_chunks_by_paper_ids_across,
    query_chunks_across_collections,
)


class _FakeVS:
    def __init__(self, by_collection: dict[str, list[Any]]):
        self.by_collection = by_collection

    async def search(self, *, collection: str, query_embedding, top_k: int):
        return self.by_collection.get(collection, [])

    async def get_chunks_by_paper_ids(self, collection: str, paper_ids: list[str]):
        out = []
        for c in self.by_collection.get(collection, []):
            ch = c.chunk if hasattr(c, "chunk") else c
            if ch.metadata.paper_id in paper_ids:
                out.append(ch)
        return out


class _FakeEmb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]


def _chunk(paper_id: str, text: str, score: float, collection: str):
    md = ChunkMetadata(paper_id=paper_id, chunk_index=0, source=PaperSource.BIBTEX)
    ch = DocumentChunk(id=f"{collection}:{paper_id}", text=text, metadata=md)
    return SimpleNamespace(chunk=ch, score=score)


@pytest.mark.asyncio
async def test_query_chunks_across_collections_merges_and_tags_kb_name():
    vs = _FakeVS({
        "kb_a": [_chunk("p1", "from-a", 0.9, "kb_a"), _chunk("p2", "shared", 0.5, "kb_a")],
        "kb_b": [_chunk("p2", "shared-b", 0.8, "kb_b"), _chunk("p3", "from-b", 0.7, "kb_b")],
    })
    hits = await query_chunks_across_collections(
        vector_store=vs,
        embedding_service=_FakeEmb(),
        collection_names=["kb_a", "kb_b"],
        query="q",
        top_k=10,
    )
    paper_to_kb = {h["paper_id"]: h["kb_name"] for h in hits}
    assert paper_to_kb == {"p1": "kb_a", "p2": "kb_b", "p3": "kb_b"}
    # ordering: best score first
    assert [h["paper_id"] for h in hits[:3]] == ["p1", "p2", "p3"]


@pytest.mark.asyncio
async def test_get_chunks_by_paper_ids_across_fans_out():
    vs = _FakeVS({
        "kb_a": [_chunk("p1", "a1", 0.9, "kb_a")],
        "kb_b": [_chunk("p2", "b1", 0.8, "kb_b")],
    })
    chunks = await get_chunks_by_paper_ids_across(
        vs,
        collection_names=["kb_a", "kb_b"],
        paper_ids=["p1", "p2"],
    )
    ids = sorted(c.id for c in chunks)
    assert ids == ["kb_a:p1", "kb_b:p2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_multi_kb_fanout.py -v`
Expected: FAIL (`ImportError: cannot import name 'query_chunks_across_collections'`)

- [ ] **Step 3: Add the helpers**

Append to `src/perspicacite/retrieval/multi_kb.py`:

```python
import asyncio


async def query_chunks_across_collections(
    *,
    vector_store: Any,
    embedding_service: Any,
    collection_names: list[str],
    query: str,
    top_k: int,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    """Fan vector_store.search across collections, merge by paper_id (best score),
    tag each hit with kb_name (= collection_name), sort, return top_k."""
    if not collection_names:
        return []
    query_embedding = (await embedding_service.embed([query]))[0]

    async def _one(coll: str) -> list[Any]:
        try:
            return await vector_store.search(
                collection=coll, query_embedding=query_embedding, top_k=top_k * 2
            )
        except Exception as e:
            logger.warning("fanout_search_failed", collection=coll, error=str(e))
            return []

    per = await asyncio.gather(*(_one(c) for c in collection_names))
    merged: dict[str, dict[str, Any]] = {}
    orderless: list[dict[str, Any]] = []
    for coll, hits in zip(collection_names, per):
        for r in hits:
            chunk = getattr(r, "chunk", None)
            meta = getattr(chunk, "metadata", None) if chunk is not None else None
            pid = getattr(meta, "paper_id", None)
            score = float(getattr(r, "score", 0.0) or 0.0)
            if score < min_score:
                continue
            d = {
                "text": getattr(chunk, "text", "") if chunk is not None else "",
                "score": score,
                "paper_id": pid,
                "metadata": meta,
                "kb_name": coll,
            }
            if pid:
                prev = merged.get(pid)
                if prev is None or score > prev["score"]:
                    merged[pid] = d
            else:
                orderless.append(d)
    combined = list(merged.values()) + orderless
    combined.sort(key=lambda x: x["score"], reverse=True)
    return combined[:top_k]


async def get_chunks_by_paper_ids_across(
    vector_store: Any,
    *,
    collection_names: list[str],
    paper_ids: list[str],
) -> list[Any]:
    """Fan get_chunks_by_paper_ids across collections in parallel.
    Returns concatenated DocumentChunk list (caller dedups if needed)."""
    if not collection_names or not paper_ids:
        return []

    async def _one(coll: str) -> list[Any]:
        try:
            return await vector_store.get_chunks_by_paper_ids(coll, paper_ids)
        except Exception as e:
            logger.warning("fanout_get_chunks_failed", collection=coll, error=str(e))
            return []

    per = await asyncio.gather(*(_one(c) for c in collection_names))
    out: list[Any] = []
    for chunks in per:
        out.extend(chunks)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_multi_kb_fanout.py -v`
Expected: PASS, 2/2

- [ ] **Step 5: Verify the existing unit suite stays green**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/retrieval/multi_kb.py tests/unit/test_multi_kb_fanout.py
git commit -m "feat(retrieval): add query_chunks_across_collections and get_chunks_by_paper_ids_across"
```

---

### Task 2: Wire `advanced.py` to fan out across KBs

**Files:**
- Modify: `src/perspicacite/rag/modes/advanced.py`
- Test: `tests/unit/test_multi_kb_advanced.py`

Context: `_wrrf_retrieval` (≈line 235) calls `vector_store.search(collection=kb_collection, ...)` and `get_chunks_by_paper_ids(kb_collection, ids)`. Both call sites get replaced with the new helpers when `request.kb_names` has >1 entry. `SourceReference.kb_name` already exists and must be populated.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_multi_kb_advanced.py`:

```python
"""advanced.py honors request.kb_names — fans out and tags sources."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.models.rag import RAGMode, RAGRequest
from perspicacite.rag.modes.advanced import AdvancedRAGMode


def _chunk(paper_id: str, text: str, score: float, collection: str):
    md = ChunkMetadata(paper_id=paper_id, chunk_index=0, source=PaperSource.BIBTEX, title=paper_id)
    ch = DocumentChunk(id=f"{collection}:{paper_id}", text=text, metadata=md)
    return SimpleNamespace(chunk=ch, score=score)


class _FakeVS:
    def __init__(self, by_coll):
        self.by_coll = by_coll

    async def search(self, *, collection, query_embedding, top_k, **_):
        return self.by_coll.get(collection, [])

    async def get_chunks_by_paper_ids(self, collection, paper_ids):
        out = []
        for c in self.by_coll.get(collection, []):
            if c.chunk.metadata.paper_id in paper_ids:
                out.append(c.chunk)
        return out


class _FakeEmb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]


class _FakeLLM:
    async def complete(self, **_):
        return SimpleNamespace(content="answer")

    async def complete_stream(self, **_):
        async def gen():
            yield SimpleNamespace(content="answer")

        return gen()


@pytest.mark.asyncio
async def test_advanced_mode_fans_out_across_kb_names():
    cfg = Config()
    mode = AdvancedRAGMode(cfg)
    vs = _FakeVS({
        "perspicacite_kb_a": [_chunk("p1", "from-a", 0.9, "perspicacite_kb_a")],
        "perspicacite_kb_b": [_chunk("p2", "from-b", 0.8, "perspicacite_kb_b")],
    })
    request = RAGRequest(
        query="q",
        mode=RAGMode.ADVANCED,
        kb_names=["a", "b"],
        kb_name="a",
    )
    events = []
    async for ev in mode.execute_stream(request, _FakeLLM(), vs, _FakeEmb(), tools=None):
        events.append(ev)
    src_events = [e for e in events if getattr(e, "event", "") == "sources"]
    assert src_events, "expected at least one 'sources' event"
    kb_tags = {s.get("kb_name") for s in src_events[-1].data.get("sources", [])}
    assert kb_tags & {"a", "b"}, f"expected kb tags from both KBs, got {kb_tags}"
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `uv run pytest tests/unit/test_multi_kb_advanced.py -v`
Expected: FAIL (either no fan-out, or sources missing `kb_name`).

- [ ] **Step 3: Refactor `_wrrf_retrieval` to use fan-out helpers**

In `src/perspicacite/rag/modes/advanced.py`, at the top of `_wrrf_retrieval` (search for `kb_collection = chroma_collection_name_for_kb(request.kb_name)`), replace the single-collection variable with:

```python
from perspicacite.models.kb import chroma_collection_name_for_kb
from perspicacite.retrieval.multi_kb import (
    check_embedding_compat,
    get_chunks_by_paper_ids_across,
    query_chunks_across_collections,
)

kb_names = getattr(request, "kb_names", None) or [request.kb_name]
collection_names = [chroma_collection_name_for_kb(n) for n in kb_names]
```

Then replace every `await vector_store.search(collection=kb_collection, query_embedding=qe, top_k=k)`-style call inside this method with:

```python
hits = await query_chunks_across_collections(
    vector_store=vector_store,
    embedding_service=embedding_provider,
    collection_names=collection_names,
    query=expanded_query,
    top_k=k,
)
```

…and replace the two-pass `get_chunks_by_paper_ids(kb_collection, paper_ids)` call with:

```python
all_chunks = await get_chunks_by_paper_ids_across(
    vector_store,
    collection_names=collection_names,
    paper_ids=paper_ids,
)
```

In the loop that builds `SourceReference`s from results, pass `kb_name=hit.get("kb_name")` (the helper has already tagged each hit with the originating collection). Where the code currently passes `kb_name=request.kb_name` or `kb_name=kb_collection`, switch to the per-hit value.

Do the same in the streaming variant (`execute_stream`'s WRRF code path, ≈line 504).

- [ ] **Step 4: Add the embedding-compat preflight**

At the top of both `execute` and `execute_stream` in `advanced.py`, after parsing the request and before any retrieval:

```python
kb_names = getattr(request, "kb_names", None) or []
if len(kb_names) > 1 and getattr(app_state := None, "session_store", None) is None:
    # in tests we may not have app_state; skip compat check gracefully
    pass
else:
    if len(kb_names) > 1:
        from perspicacite.web.state import app_state as _state
        if getattr(_state, "session_store", None) is not None:
            metas = []
            for n in kb_names:
                m = await _state.session_store.get_kb_metadata(n)
                if m is not None:
                    metas.append(m)
            err = check_embedding_compat(metas)
            if err:
                yield StreamEvent(event="error", data={"error": err})
                return
```

(Match the existing pattern from `basic.py` / `contradiction.py` exactly — copy from there if a clean reference is already in tree.)

- [ ] **Step 5: Run the new test + unit suite**

```bash
uv run pytest tests/unit/test_multi_kb_advanced.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: both pass; suite green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/modes/advanced.py tests/unit/test_multi_kb_advanced.py
git commit -m "feat(rag/advanced): fan out across kb_names via multi-KB helpers"
```

---

### Task 3: Wire `profound.py` to fan out across KBs

**Files:**
- Modify: `src/perspicacite/rag/modes/profound.py`
- Test: `tests/unit/test_multi_kb_profound.py`

Profound has two retrieval surfaces: `_execute_step` (single-cycle search) and `_two_pass_retrieval` / `_enrich_with_full_text` (paper-level expansion). The latter needs to know which collection a paper came from to fetch its full-paper chunks — we propagate via a `paper_id_to_kb_name: dict[str, str]` built from the first-pass hits.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_multi_kb_profound.py`:

```python
"""profound.py honors request.kb_names — fan-out + two-pass kb_name tagging."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.models.rag import RAGMode, RAGRequest
from perspicacite.rag.modes.profound import ProfoundRAGMode


def _chunk(paper_id, text, score, collection):
    md = ChunkMetadata(paper_id=paper_id, chunk_index=0, source=PaperSource.BIBTEX, title=paper_id)
    ch = DocumentChunk(id=f"{collection}:{paper_id}", text=text, metadata=md)
    return SimpleNamespace(chunk=ch, score=score)


class _FakeVS:
    def __init__(self, by_coll):
        self.by_coll = by_coll

    async def search(self, *, collection, query_embedding, top_k, **_):
        return self.by_coll.get(collection, [])

    async def get_chunks_by_paper_ids(self, collection, paper_ids):
        return [c.chunk for c in self.by_coll.get(collection, []) if c.chunk.metadata.paper_id in paper_ids]


class _FakeEmb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]


class _FakeLLM:
    async def complete(self, **_):
        return SimpleNamespace(content="answer")

    async def complete_stream(self, **_):
        async def gen():
            yield SimpleNamespace(content="answer")

        return gen()


@pytest.mark.asyncio
async def test_profound_mode_fans_out_and_tags_kb():
    cfg = Config()
    mode = ProfoundRAGMode(cfg)
    vs = _FakeVS({
        "perspicacite_kb_a": [_chunk("p1", "from-a", 0.9, "perspicacite_kb_a")],
        "perspicacite_kb_b": [_chunk("p2", "from-b", 0.8, "perspicacite_kb_b")],
    })
    request = RAGRequest(
        query="q",
        mode=RAGMode.PROFOUND,
        kb_names=["a", "b"],
        kb_name="a",
    )
    events = []
    async for ev in mode.execute_stream(request, _FakeLLM(), vs, _FakeEmb(), tools=None):
        events.append(ev)
    srcs = [e for e in events if getattr(e, "event", "") == "sources"]
    assert srcs
    kbs = {s.get("kb_name") for s in srcs[-1].data.get("sources", [])}
    assert kbs & {"a", "b"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_multi_kb_profound.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `_execute_step` and `_two_pass_retrieval`**

Pattern matches Task 2:
1. At the top of `_execute_step`, build `kb_names`, `collection_names`, `paper_id_to_kb_name = {}`.
2. Replace `vector_store.search(collection=kb_name, ...)` with `query_chunks_across_collections(...)`. After getting hits, do `paper_id_to_kb_name.update({h["paper_id"]: h["kb_name"] for h in hits if h.get("paper_id")})`.
3. Replace `vector_store.get_chunks_by_paper_ids(kb_name, paper_ids)` with `get_chunks_by_paper_ids_across(vector_store, collection_names=collection_names, paper_ids=paper_ids)`.
4. In `_enrich_with_full_text`: same swap. The function signature gains an optional `paper_id_to_kb_name: dict[str, str] | None = None` parameter (defaults to None for back-compat). When set, `SourceReference.kb_name = paper_id_to_kb_name.get(paper_id)`; else use the existing fallback (`request.kb_name`).
5. Replicate in the streaming code path.

- [ ] **Step 4: Add embedding-compat preflight (same pattern as Task 2)**

Copy the preflight block from `advanced.py` to the top of `profound.py`'s `execute` and `execute_stream`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_multi_kb_profound.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/modes/profound.py tests/unit/test_multi_kb_profound.py
git commit -m "feat(rag/profound): fan out across kb_names; two-pass enrichment carries kb_name map"
```

---

### Task 4: Wire `literature_survey.py` to accept `kb_names`

**Files:**
- Modify: `src/perspicacite/rag/modes/literature_survey.py`
- Test: `tests/unit/test_multi_kb_literature_survey.py`

Literature_survey does no KB retrieval — it hits SciLEx. The change is API-contract only: accept `kb_names` without error; if survey decides to *store* discovered papers (via `request.store_in_kb=True` if set), store in `kb_names[0]` when present, else `kb_name`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_multi_kb_literature_survey.py`:

```python
"""literature_survey accepts kb_names without erroring."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest
from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode


class _FakeLLM:
    async def complete(self, **_):
        return SimpleNamespace(content="...")

    async def complete_stream(self, **_):
        async def gen():
            yield SimpleNamespace(content="...")

        return gen()


@pytest.mark.asyncio
async def test_literature_survey_accepts_kb_names(monkeypatch):
    cfg = Config()
    mode = LiteratureSurveyRAGMode(cfg)
    request = RAGRequest(
        query="microbiome",
        mode=RAGMode.LITERATURE_SURVEY,
        kb_names=["k1", "k2"],
        kb_name="k1",
    )
    events = []

    async def _empty_search(*a, **k):
        return []

    monkeypatch.setattr(mode, "_broad_search", _empty_search, raising=False)
    async for ev in mode.execute_stream(request, _FakeLLM(), vector_store=None,
                                        embedding_provider=None, tools=None):
        events.append(ev)
    error_events = [e for e in events if getattr(e, "event", "") == "error"]
    assert not error_events
```

- [ ] **Step 2: Run to confirm it fails or yields an error event**

Run: `uv run pytest tests/unit/test_multi_kb_literature_survey.py -v`
Expected: FAIL or unexpected error event.

- [ ] **Step 3: Plumb `kb_names` through `literature_survey.py`**

Find any line that reads `request.kb_name` for KB-targeting purposes. Wrap with:

```python
target_kb = (request.kb_names[0] if getattr(request, "kb_names", None) else request.kb_name)
```

Pass `target_kb` to the SciLEx-storage helper. Where source references get assembled, set `kb_name=target_kb` if the paper was stored, else `kb_name=None`.

If `request.kb_names` has >1 entry, log a `survey_multi_kb_storage` info event noting that only the first KB will be used for storage.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_multi_kb_literature_survey.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/modes/literature_survey.py tests/unit/test_multi_kb_literature_survey.py
git commit -m "feat(rag/literature_survey): accept kb_names; store discoveries in kb_names[0]"
```

---

### Task 5: Wire `agentic/orchestrator.py` KB_SEARCH step

**Files:**
- Modify: `src/perspicacite/rag/agentic/orchestrator.py`
- Test: `tests/unit/test_multi_kb_agentic.py`

The orchestrator's `KB_SEARCH` step builds a single-KB `DynamicKnowledgeBase` from `self.kb_name`. When called with >1 KB, build a `MultiKBRetriever` instead. The orchestrator already has `self.kb_metas`-style infrastructure; we add a `self.kb_names: list[str]` field.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_multi_kb_agentic.py`:

```python
"""agentic orchestrator uses MultiKBRetriever when kb_names has >1 entry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator


class _FakeVS:
    async def search(self, *, collection, query_embedding, top_k, **_):
        md = ChunkMetadata(paper_id=f"p:{collection}", chunk_index=0,
                           source=PaperSource.BIBTEX, title=collection)
        ch = DocumentChunk(id=collection, text=collection, metadata=md)
        return [SimpleNamespace(chunk=ch, score=0.9)]


class _FakeEmb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]


@pytest.mark.asyncio
async def test_agentic_kb_search_step_uses_multi_kb():
    cfg = Config()
    orch = AgenticOrchestrator(
        config=cfg,
        llm_client=None,
        vector_store=_FakeVS(),
        embedding_provider=_FakeEmb(),
        tools=None,
        kb_name="a",
    )
    orch.kb_names = ["a", "b"]  # set after construction; multi-KB activation
    kb = orch._build_kb_retriever()
    cls_name = type(kb).__name__
    assert cls_name == "MultiKBRetriever", f"expected MultiKBRetriever, got {cls_name}"
    hits = await kb.search("q", top_k=5)
    kbs = {h.get("kb_name") for h in hits}
    assert kbs == {"a", "b"}
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_multi_kb_agentic.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `_build_kb_retriever` to the orchestrator**

In `src/perspicacite/rag/agentic/orchestrator.py`:

```python
def _build_kb_retriever(self):
    """Single KB → DynamicKnowledgeBase; multi-KB → MultiKBRetriever."""
    from types import SimpleNamespace

    from perspicacite.models.kb import chroma_collection_name_for_kb
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
    from perspicacite.retrieval.multi_kb import MultiKBRetriever

    names = getattr(self, "kb_names", None) or [self.kb_name]
    if len(names) > 1:
        metas = [
            SimpleNamespace(
                name=n,
                collection_name=chroma_collection_name_for_kb(n),
                embedding_model=None,
            )
            for n in names
        ]
        return MultiKBRetriever(
            vector_store=self.vector_store,
            embedding_service=self.embedding_provider,
            kb_metas=metas,
        )
    dkb = DynamicKnowledgeBase(
        vector_store=self.vector_store,
        embedding_service=self.embedding_provider,
    )
    dkb.collection_name = chroma_collection_name_for_kb(names[0])
    dkb._initialized = True
    return dkb
```

Replace the existing single-KB construction in the `KB_SEARCH` step (around the line that does `DynamicKnowledgeBase(...)` directly) with `self._build_kb_retriever()`.

Add `self.kb_names: list[str] = []` to `AgenticOrchestrator.__init__`. Update the caller (the `agentic` RAG mode wrapper) to set `orch.kb_names = getattr(request, "kb_names", None) or []` before invoking.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_multi_kb_agentic.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/agentic/orchestrator.py tests/unit/test_multi_kb_agentic.py
git commit -m "feat(rag/agentic): KB_SEARCH step uses MultiKBRetriever when kb_names has >1 entry"
```

---

### Task 6: Verify MCP `generate_report` and `search_knowledge_base` propagate `kb_names`

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (only if pass-through is missing)
- Test: `tests/unit/test_mcp_multi_kb_passthrough.py`

- [ ] **Step 1: Write the test**

Create `tests/unit/test_mcp_multi_kb_passthrough.py`:

```python
"""MCP tools accept kb_names and pass through to RAGRequest."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_generate_report_passes_kb_names(monkeypatch):
    captured: dict = {}

    class _FakeEngine:
        async def execute(self, request):
            captured["kb_names"] = request.kb_names
            captured["mode"] = request.mode
            return SimpleNamespace(answer="ok", sources=[])

    fake_state = SimpleNamespace(
        config=SimpleNamespace(),
        rag_engine=_FakeEngine(),
        vector_store=None,
        embedding_provider=None,
        tool_registry=None,
        session_store=AsyncMock(),
    )
    monkeypatch.setattr(mcp_server, "mcp_state", fake_state)
    fn = mcp_server.generate_report
    if hasattr(fn, "fn"):
        fn = fn.fn
    await fn(
        query="q",
        mode="advanced",
        kb_names=["alpha", "beta"],
    )
    assert captured["kb_names"] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_search_knowledge_base_passes_kb_names(monkeypatch):
    seen: dict = {}

    async def _fake_retrieve(*, kb_names, **_):
        seen["kb_names"] = kb_names
        return []

    monkeypatch.setattr(mcp_server, "_search_knowledge_base_impl", _fake_retrieve, raising=False)
    fn = mcp_server.search_knowledge_base
    if hasattr(fn, "fn"):
        fn = fn.fn
    await fn(query="q", kb_names=["a", "b"])
    assert seen["kb_names"] == ["a", "b"]
```

- [ ] **Step 2: Run test, observe what's missing**

Run: `uv run pytest tests/unit/test_mcp_multi_kb_passthrough.py -v`
Expected: PASS if already wired (CLAUDE.md says it is); FAIL signals a gap.

- [ ] **Step 3: If a gap is found, add kb_names to the tool signatures**

In `src/perspicacite/mcp/server.py`, find the `generate_report` and `search_knowledge_base` tool definitions, ensure each takes `kb_names: list[str] | None = None` and threads it through to the engine call.

- [ ] **Step 4: Run unit suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 5: Commit (only if a change was needed)**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_multi_kb_passthrough.py
git commit -m "test(mcp): cover kb_names propagation through generate_report and search_knowledge_base"
```

(If no source change was needed, commit only the test file with the same message.)

---

### Task 7: Phase 1 MANUAL_QA update

**Files:**
- Modify: `MANUAL_QA.md`

- [ ] **Step 1: Append new section**

Append the following block to `MANUAL_QA.md`:

```markdown
## Multi-KB chat across all six modes (2026-05-13)

For each of `basic`, `advanced`, `profound`, `contradiction`, `literature_survey`, `agentic`:
1. Open the chat panel, multi-select two KBs that share an embedding model.
2. Enter a representative query.
3. Confirm the answer streams to completion (no error event).
4. Confirm source cards show `kb_name` tags from both KBs (visible in source-card chip).
5. Confirm provenance JSONL contains a `kb_names` field reflecting the selection.

Embedding-mismatch test:
- Multi-select two KBs with different embedding models.
- Confirm chat surfaces a clear error (no silent fallback).
```

- [ ] **Step 2: Commit**

```bash
git add MANUAL_QA.md
git commit -m "docs(manual-qa): multi-KB chat checklist across all six modes"
```

---

## Phase 2 — Zotero-as-source-of-KB

### Task 8: Extend `ZoteroClient` with read methods + HTML-to-text helper

**Files:**
- Modify: `src/perspicacite/integrations/zotero.py`
- Test: `tests/unit/test_zotero_client_read.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zotero_client_read.py`:

```python
"""ZoteroClient read methods + HTML-to-text helper."""

from __future__ import annotations

import httpx
import pytest

from perspicacite.integrations.zotero import ZoteroClient, _html_to_text


def test_html_to_text_strips_tags_keeps_text():
    html = "<p><b>Title</b></p><ul><li>one</li><li>two</li></ul>"
    out = _html_to_text(html)
    assert "Title" in out
    assert "one" in out
    assert "two" in out
    assert "<" not in out


@pytest.mark.asyncio
async def test_list_collections_paginates():
    pages = [
        [{"key": "C1", "data": {"name": "Coll1"}}],
        [{"key": "C2", "data": {"name": "Coll2"}}],
        [],
    ]
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[calls["i"]]
        calls["i"] += 1
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        out = await c.list_collections()
    assert [x["key"] for x in out] == ["C1", "C2"]


@pytest.mark.asyncio
async def test_download_attachment_bytes_returns_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PDFDATA")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        out = await c.download_attachment_bytes("ATT1")
    assert out == b"PDFDATA"


@pytest.mark.asyncio
async def test_get_item_notes_strips_html():
    children = [
        {"key": "N1", "data": {"itemType": "note", "note": "<p>Hello <b>world</b></p>"}},
        {"key": "A1", "data": {"itemType": "attachment"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=children)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        notes = await c.get_item_notes("PARENT")
    assert notes == ["Hello world"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_zotero_client_read.py -v`
Expected: FAIL (`_html_to_text` doesn't exist; new methods missing).

- [ ] **Step 3: Implement helpers + methods**

Add to `src/perspicacite/integrations/zotero.py`:

```python
from html.parser import HTMLParser


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data):
        self._chunks.append(data)

    def get_text(self):
        return " ".join("".join(self._chunks).split()).strip()


def _html_to_text(html: str) -> str:
    """Strip HTML tags; collapse whitespace."""
    p = _HTMLStripper()
    p.feed(html or "")
    return p.get_text()
```

Add methods to `ZoteroClient`:

```python
async def _paginated(self, path: str, params: dict | None = None) -> list[dict[str, Any]]:
    c = await self._client()
    out: list[dict[str, Any]] = []
    start = 0
    limit = 100
    while True:
        p = {"start": start, "limit": limit, "format": "json"}
        if params:
            p.update(params)
        r = await c.get(f"{self._base()}{path}", params=p, headers=self._headers())
        if r.status_code != 200:
            break
        page = r.json() or []
        if not page:
            break
        out.extend(page)
        if len(page) < limit:
            break
        start += limit
    return out


async def list_collections(self) -> list[dict[str, Any]]:
    return await self._paginated("/collections")


async def list_top_level_collections(self) -> list[dict[str, Any]]:
    return await self._paginated("/collections/top")


async def list_items_in_collection(
    self, coll_key: str, *, include_subcollections: bool = True
) -> list[dict[str, Any]]:
    items = await self._paginated(
        f"/collections/{coll_key}/items",
        params={"itemType": "-attachment || note"},
    )
    if include_subcollections:
        all_colls = await self.list_collections()
        descendants = [
            c["key"] for c in all_colls
            if (c.get("data") or {}).get("parentCollection") == coll_key
        ]
        for d in descendants:
            items.extend(await self.list_items_in_collection(d, include_subcollections=True))
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for it in items:
        k = it.get("key")
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq


async def list_top_level_items_without_collection(self) -> list[dict[str, Any]]:
    items = await self._paginated(
        "/items/top",
        params={"itemType": "-attachment || note"},
    )
    return [it for it in items if not ((it.get("data") or {}).get("collections") or [])]


async def get_item_attachments(self, item_key: str) -> list[dict[str, Any]]:
    c = await self._client()
    r = await c.get(
        f"{self._base()}/items/{item_key}/children",
        params={"format": "json"},
        headers=self._headers(),
    )
    if r.status_code != 200:
        return []
    return [
        it for it in (r.json() or [])
        if ((it.get("data") or {}).get("itemType")) == "attachment"
    ]


async def download_attachment_bytes(self, attachment_key: str) -> bytes | None:
    c = await self._client()
    try:
        r = await c.get(
            f"{self._base()}/items/{attachment_key}/file",
            headers=self._headers(),
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.content:
        return None
    return r.content


async def get_item_notes(self, item_key: str) -> list[str]:
    c = await self._client()
    r = await c.get(
        f"{self._base()}/items/{item_key}/children",
        params={"format": "json"},
        headers=self._headers(),
    )
    if r.status_code != 200:
        return []
    out: list[str] = []
    for it in r.json() or []:
        data = it.get("data") or {}
        if data.get("itemType") == "note":
            out.append(_html_to_text(data.get("note") or ""))
    return out
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_zotero_client_read.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/zotero.py tests/unit/test_zotero_client_read.py
git commit -m "feat(zotero): add read methods (collections, items, attachments, notes) + html-to-text"
```

---

### Task 9: Plan builder — `integrations/zotero_ingest.py`

**Files:**
- Create: `src/perspicacite/integrations/zotero_ingest.py`
- Test: `tests/unit/test_zotero_ingest_plan.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zotero_ingest_plan.py`:

```python
"""plan_kbs_from_zotero counts items, with-doi, with-pdf, with-notes correctly."""

from __future__ import annotations

import pytest

from perspicacite.integrations.zotero_ingest import (
    ZoteroKBPlanEntry,
    plan_kbs_from_zotero,
)


class _FakeClient:
    def __init__(self):
        self.collections_top = [{"key": "TOP1", "data": {"name": "Top1"}}]

    async def list_top_level_collections(self):
        return self.collections_top

    async def list_items_in_collection(self, key, *, include_subcollections=True):
        return [
            {"key": "I1", "data": {"DOI": "10.1/x", "title": "A"}},
            {"key": "I2", "data": {"title": "B"}},  # no DOI
        ]

    async def list_top_level_items_without_collection(self):
        return [{"key": "U1", "data": {"DOI": "10.2/u"}}]

    async def get_item_attachments(self, item_key):
        if item_key == "I1":
            return [{"key": "A1", "data": {"linkMode": "imported_file", "contentType": "application/pdf"}}]
        return []

    async def get_item_notes(self, item_key):
        if item_key == "I1":
            return ["a note"]
        return []


@pytest.mark.asyncio
async def test_plan_includes_top_level_and_unfiled():
    c = _FakeClient()
    plan = await plan_kbs_from_zotero(c, include_unfiled=True)
    by_name = {p.source_collection_name or "Unfiled": p for p in plan}
    assert set(by_name.keys()) == {"Top1", "Unfiled"}
    assert by_name["Top1"].item_count == 2
    assert by_name["Top1"].with_doi_count == 1
    assert by_name["Top1"].with_pdf_count == 1
    assert by_name["Top1"].with_notes_count == 1
    assert by_name["Unfiled"].item_count == 1
    assert by_name["Unfiled"].with_doi_count == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_zotero_ingest_plan.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Create `zotero_ingest.py` with plan builder**

Create `src/perspicacite/integrations/zotero_ingest.py`:

```python
"""Zotero → KB ingest: plan-then-execute. Worker drives the unified pipeline."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel

from perspicacite.integrations.zotero import ZoteroClient
from perspicacite.logging import get_logger

logger = get_logger("perspicacite.zotero_ingest")


class ZoteroKBPlanEntry(BaseModel):
    kb_name: str
    source_collection_key: str | None
    source_collection_name: str | None
    item_count: int
    with_doi_count: int
    with_pdf_count: int
    with_notes_count: int


def _slugify(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s or "kb"


async def _summarize_items(client: ZoteroClient, items: list[dict[str, Any]]) -> dict[str, int]:
    with_doi = sum(1 for it in items if (it.get("data") or {}).get("DOI"))

    async def _has_pdf(it):
        atts = await client.get_item_attachments(it["key"])
        return any(
            (a.get("data") or {}).get("contentType") == "application/pdf"
            and (a.get("data") or {}).get("linkMode") in {"imported_file", "imported_url"}
            for a in atts
        )

    async def _has_note(it):
        notes = await client.get_item_notes(it["key"])
        return any(n for n in notes)

    pdf_flags = await asyncio.gather(*(_has_pdf(it) for it in items))
    note_flags = await asyncio.gather(*(_has_note(it) for it in items))
    return {
        "with_doi": with_doi,
        "with_pdf": sum(1 for f in pdf_flags if f),
        "with_notes": sum(1 for f in note_flags if f),
    }


async def plan_kbs_from_zotero(
    client: ZoteroClient,
    *,
    top_level_collection_keys: list[str] | None = None,
    include_unfiled: bool = True,
    library_label: str = "Library",
) -> list[ZoteroKBPlanEntry]:
    """Return [ZoteroKBPlanEntry] — one per top-level collection (+ optional unfiled)."""
    out: list[ZoteroKBPlanEntry] = []
    tops = await client.list_top_level_collections()
    if top_level_collection_keys is not None:
        keys = set(top_level_collection_keys)
        tops = [c for c in tops if c.get("key") in keys]
    for c in tops:
        name = (c.get("data") or {}).get("name") or c["key"]
        items = await client.list_items_in_collection(c["key"], include_subcollections=True)
        summary = await _summarize_items(client, items)
        out.append(
            ZoteroKBPlanEntry(
                kb_name=f"{_slugify(library_label)}_{_slugify(name)}",
                source_collection_key=c["key"],
                source_collection_name=name,
                item_count=len(items),
                with_doi_count=summary["with_doi"],
                with_pdf_count=summary["with_pdf"],
                with_notes_count=summary["with_notes"],
            )
        )
    if include_unfiled:
        items = await client.list_top_level_items_without_collection()
        summary = await _summarize_items(client, items)
        if items:
            out.append(
                ZoteroKBPlanEntry(
                    kb_name=f"{_slugify(library_label)}_Unfiled",
                    source_collection_key=None,
                    source_collection_name=None,
                    item_count=len(items),
                    with_doi_count=summary["with_doi"],
                    with_pdf_count=summary["with_pdf"],
                    with_notes_count=summary["with_notes"],
                )
            )
    return out
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_zotero_ingest_plan.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/zotero_ingest.py tests/unit/test_zotero_ingest_plan.py
git commit -m "feat(zotero_ingest): plan builder — one ZoteroKBPlanEntry per top-level collection"
```

---

### Task 10: Worker — `build_kbs_from_zotero`

**Files:**
- Modify: `src/perspicacite/integrations/zotero_ingest.py`
- Test: `tests/unit/test_zotero_ingest_worker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zotero_ingest_worker.py`:

```python
"""Zotero ingest worker — dedups by DOI, attaches notes, handles missing PDFs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.integrations.zotero_ingest import (
    ZoteroKBPlanEntry,
    build_kbs_from_zotero,
)


class _Reg:
    def __init__(self):
        self.events = []
        self.finished = None
        self.failed = None

    async def publish(self, jid, ev):
        self.events.append(ev)

    async def finish(self, jid, res):
        self.finished = res

    async def fail(self, jid, err):
        self.failed = err


class _Client:
    async def list_items_in_collection(self, key, *, include_subcollections=True):
        return [{"key": "I1", "data": {"DOI": "10.1/x", "title": "T1"}}]

    async def list_top_level_items_without_collection(self):
        return []

    async def get_item_attachments(self, key):
        return []

    async def get_item_notes(self, key):
        return ["note text"]

    async def download_attachment_bytes(self, key):
        return None


@pytest.mark.asyncio
async def test_worker_dedups_by_doi_and_attaches_notes(monkeypatch):
    seen: dict = {}

    class _DKB:
        def __init__(self, **kw):
            pass

        async def add_papers(self, papers, include_full_text=True):
            seen["papers"] = papers
            return len(papers)

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.DynamicKnowledgeBase",
        _DKB,
    )

    async def _retrieve(doi, **_):
        return SimpleNamespace(success=True, full_text="full body", abstract=None, metadata={})

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.retrieve_paper_content",
        _retrieve,
    )

    fake_state = SimpleNamespace(
        config=SimpleNamespace(pdf_download=None),
        pdf_parser=None,
        vector_store=SimpleNamespace(paper_exists=AsyncMock(return_value=False)),
        embedding_provider=None,
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="perspicacite_TestKB",
                paper_count=0,
                chunk_count=0,
            )),
            create_kb_metadata=AsyncMock(),
            save_kb_metadata=AsyncMock(),
        ),
    )
    plan = [ZoteroKBPlanEntry(
        kb_name="TestKB",
        source_collection_key="C1",
        source_collection_name="Coll1",
        item_count=1, with_doi_count=1, with_pdf_count=0, with_notes_count=1,
    )]
    reg = _Reg()
    await build_kbs_from_zotero(
        _Client(), plan=plan, app_state=fake_state, registry=reg, job_id="J1",
    )
    assert reg.finished is not None
    paper = seen["papers"][0]
    assert "note text" in (paper.full_text or "")
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_zotero_ingest_worker.py -v`
Expected: FAIL (`build_kbs_from_zotero` not defined).

- [ ] **Step 3: Add the worker**

Append to `src/perspicacite/integrations/zotero_ingest.py`:

```python
from perspicacite.models.papers import Author, Paper, PaperSource
from perspicacite.pipeline.download import retrieve_paper_content
from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase


def _item_to_paper(item: dict[str, Any]) -> Paper:
    data = item.get("data") or {}
    creators = data.get("creators") or []
    authors = []
    for cr in creators:
        first = cr.get("firstName") or ""
        last = cr.get("lastName") or cr.get("name") or ""
        full = (first + " " + last).strip() or last or first
        if full:
            authors.append(Author(name=full))
    doi = data.get("DOI") or None
    return Paper(
        id=doi or item.get("key") or "zotero:unknown",
        title=data.get("title") or "Untitled",
        authors=authors,
        doi=doi,
        year=int(str(data.get("date") or "")[:4]) if (data.get("date") or "")[:4].isdigit() else None,
        journal=data.get("publicationTitle") or None,
        abstract=data.get("abstractNote") or None,
        source=PaperSource.BIBTEX,  # closest existing source — zotero items most often have DOIs
    )


async def _ensure_kb(*, name: str, app_state) -> Any:
    kb = await app_state.session_store.get_kb_metadata(name)
    if kb is not None:
        return kb
    # Create a fresh KB metadata row using the standard create_kb_metadata path.
    return await app_state.session_store.create_kb_metadata(name=name)


async def build_kbs_from_zotero(
    client: ZoteroClient,
    *,
    plan: list[ZoteroKBPlanEntry],
    app_state,
    registry,
    job_id: str,
) -> dict[str, Any]:
    """Execute the plan; emit progress; return summary."""
    summary_per_kb: list[dict[str, Any]] = []
    try:
        for entry in plan:
            kb = await _ensure_kb(name=entry.kb_name, app_state=app_state)
            if entry.source_collection_key is None:
                items = await client.list_top_level_items_without_collection()
            else:
                items = await client.list_items_in_collection(
                    entry.source_collection_key, include_subcollections=True
                )
            papers: list[Paper] = []
            skipped = 0
            for idx, it in enumerate(items):
                paper = _item_to_paper(it)
                pid = paper.doi or paper.id
                if await app_state.vector_store.paper_exists(kb.collection_name, pid):
                    skipped += 1
                    await registry.publish(job_id, {
                        "type": "progress", "kb": entry.kb_name,
                        "done": idx + 1, "status": "skipped",
                    })
                    continue
                # Fetch full text via unified pipeline (uses DOI)
                if paper.doi and app_state.pdf_parser:
                    res = await retrieve_paper_content(paper.doi, pdf_parser=app_state.pdf_parser)
                    if res.success and res.full_text:
                        paper.full_text = res.full_text
                # Fall back to attached Zotero PDF
                if not paper.full_text:
                    atts = await client.get_item_attachments(it["key"])
                    for a in atts:
                        if (a.get("data") or {}).get("contentType") != "application/pdf":
                            continue
                        blob = await client.download_attachment_bytes(a["key"])
                        if not blob or app_state.pdf_parser is None:
                            continue
                        # Write to a tmpfile and parse
                        import tempfile
                        from pathlib import Path
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                            f.write(blob)
                            tmp = Path(f.name)
                        try:
                            parsed = await app_state.pdf_parser.parse(tmp)
                            if parsed.text:
                                paper.full_text = parsed.text
                                break
                        finally:
                            tmp.unlink(missing_ok=True)
                # Attach notes
                notes = await client.get_item_notes(it["key"])
                if notes:
                    note_block = "\n\n# Notes\n\n" + "\n\n".join(notes)
                    paper.full_text = (paper.full_text or "") + note_block
                papers.append(paper)
                await registry.publish(job_id, {
                    "type": "progress", "kb": entry.kb_name,
                    "done": idx + 1, "status": "embedded",
                })
            added_chunks = 0
            if papers:
                dkb = DynamicKnowledgeBase(
                    vector_store=app_state.vector_store,
                    embedding_service=app_state.embedding_provider,
                )
                dkb.collection_name = kb.collection_name
                dkb._initialized = True
                added_chunks = await dkb.add_papers(papers, include_full_text=True)
                kb.paper_count += len(papers)
                kb.chunk_count += added_chunks
                await app_state.session_store.save_kb_metadata(kb)
            summary_per_kb.append({
                "kb_name": entry.kb_name,
                "added_papers": len(papers),
                "added_chunks": added_chunks,
                "skipped": skipped,
            })
        result = {"per_kb": summary_per_kb}
        await registry.finish(job_id, result)
        return result
    except Exception as exc:
        logger.error("zotero_ingest_worker_failed", error=str(exc))
        await registry.fail(job_id, str(exc))
        raise
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_zotero_ingest_worker.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/zotero_ingest.py tests/unit/test_zotero_ingest_worker.py
git commit -m "feat(zotero_ingest): worker — ingest items into KB with DOI dedup and note attachment"
```

---

### Task 11: Endpoints `/api/zotero/plan` and `/api/zotero/build-kbs/async`

**Files:**
- Create: `src/perspicacite/web/routers/zotero_ingest.py`
- Modify: `src/perspicacite/web/app.py`
- Test: `tests/unit/test_zotero_ingest_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zotero_ingest_router.py`:

```python
"""Zotero ingest router: /plan + /build-kbs/async."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from perspicacite.web import app as web_app


def _fake_state(zotero_enabled: bool):
    return SimpleNamespace(
        config=SimpleNamespace(
            zotero=SimpleNamespace(
                enabled=zotero_enabled,
                api_key="k" if zotero_enabled else "",
                library_id="42" if zotero_enabled else "",
                library_type="user",
                collection_key="",
            )
        ),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
    )


def test_plan_returns_503_when_disabled(monkeypatch):
    monkeypatch.setattr("perspicacite.web.state.app_state", _fake_state(False))
    client = TestClient(web_app.app)
    r = client.get("/api/zotero/plan")
    assert r.status_code == 503


def test_build_kbs_async_returns_job_id(monkeypatch):
    monkeypatch.setattr("perspicacite.web.state.app_state", _fake_state(True))
    client = TestClient(web_app.app)
    body = {"plan": [{
        "kb_name": "TestKB",
        "source_collection_key": "C1",
        "source_collection_name": "Coll1",
        "item_count": 1, "with_doi_count": 1,
        "with_pdf_count": 0, "with_notes_count": 0,
    }]}
    r = client.post("/api/zotero/build-kbs/async", json=body)
    assert r.status_code in (200, 202)
    assert "job_id" in r.json()
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_zotero_ingest_router.py -v`
Expected: FAIL (router module missing or routes not registered).

- [ ] **Step 3: Create the router**

Create `src/perspicacite/web/routers/zotero_ingest.py`:

```python
"""Zotero → KB ingest endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from perspicacite.integrations.zotero import ZoteroClient
from perspicacite.integrations.zotero_ingest import (
    ZoteroKBPlanEntry,
    build_kbs_from_zotero,
    plan_kbs_from_zotero,
)
from perspicacite.web.state import app_state

router = APIRouter(prefix="/api/zotero", tags=["zotero-ingest"])

_ingest_tasks: set[asyncio.Task] = set()


class BuildKBsRequest(BaseModel):
    plan: list[ZoteroKBPlanEntry]


def _client_from_state() -> ZoteroClient:
    cfg = getattr(getattr(app_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.api_key and cfg.library_id):
        raise HTTPException(status_code=503, detail="Zotero not configured")
    return ZoteroClient(
        api_key=cfg.api_key,
        library_id=cfg.library_id,
        library_type=cfg.library_type,
        collection_key=cfg.collection_key,
    )


@router.get("/plan")
async def get_plan() -> dict:
    client = _client_from_state()
    plan = await plan_kbs_from_zotero(client, include_unfiled=True)
    return {"library_name": "Library", "plan": [p.model_dump() for p in plan]}


@router.post("/build-kbs/async")
async def build_kbs_async(payload: BuildKBsRequest) -> dict:
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    client = _client_from_state()
    job_id = await app_state.job_registry.create("zotero_ingest", total=len(payload.plan))
    task = asyncio.create_task(
        build_kbs_from_zotero(
            client,
            plan=payload.plan,
            app_state=app_state,
            registry=app_state.job_registry,
            job_id=job_id,
        )
    )
    _ingest_tasks.add(task)
    task.add_done_callback(_ingest_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}
```

Register it in `src/perspicacite/web/app.py`:

```python
from perspicacite.web.routers import zotero_ingest as zotero_ingest_router
...
app.include_router(zotero_ingest_router.router)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_zotero_ingest_router.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/zotero_ingest.py src/perspicacite/web/app.py tests/unit/test_zotero_ingest_router.py
git commit -m "feat(web/zotero_ingest): /plan and /build-kbs/async endpoints"
```

---

### Task 12: MCP tool `build_kbs_from_zotero`

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_mcp_zotero_ingest_tool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_zotero_ingest_tool.py`:

```python
"""MCP build_kbs_from_zotero tool."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_build_kbs_from_zotero_plan_only(monkeypatch):
    async def _plan(*a, **k):
        return [SimpleNamespace(model_dump=lambda: {
            "kb_name": "X",
            "source_collection_key": None,
            "source_collection_name": None,
            "item_count": 1,
            "with_doi_count": 1,
            "with_pdf_count": 0,
            "with_notes_count": 0,
        })]

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.plan_kbs_from_zotero", _plan,
    )
    fake_state = SimpleNamespace(
        config=SimpleNamespace(zotero=SimpleNamespace(
            enabled=True, api_key="k", library_id="42",
            library_type="user", collection_key="",
        )),
    )
    monkeypatch.setattr(mcp_server, "mcp_state", fake_state)
    fn = mcp_server.build_kbs_from_zotero
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(plan_only=True)
    assert "plan" in out
    assert out["plan"][0]["kb_name"] == "X"


def test_get_info_lists_twelve_tools():
    info = mcp_server.get_info()
    assert info["tool_count"] >= 12
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_mcp_zotero_ingest_tool.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the MCP tool**

In `src/perspicacite/mcp/server.py`, near the other tool definitions:

```python
@mcp.tool()
async def build_kbs_from_zotero(
    top_level_collection_keys: list[str] | None = None,
    include_unfiled: bool = True,
    plan_only: bool = False,
) -> dict:
    """Build one KB per Zotero top-level collection.

    plan_only=True: return preview only.
    plan_only=False: execute the full plan inline (blocking) and return summary.
    Requires zotero.enabled = true and credentials in config.yml.
    """
    from perspicacite.integrations.zotero import ZoteroClient
    from perspicacite.integrations.zotero_ingest import (
        build_kbs_from_zotero as _build,
        plan_kbs_from_zotero,
    )

    cfg = getattr(getattr(mcp_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled and cfg.api_key and cfg.library_id):
        return {"error": "Zotero not configured"}

    client = ZoteroClient(
        api_key=cfg.api_key,
        library_id=cfg.library_id,
        library_type=cfg.library_type,
        collection_key=cfg.collection_key,
    )
    plan = await plan_kbs_from_zotero(
        client,
        top_level_collection_keys=top_level_collection_keys,
        include_unfiled=include_unfiled,
    )
    if plan_only:
        return {"plan": [p.model_dump() for p in plan]}

    # Inline execution — MCP can wait, since it's already async.
    class _InlineRegistry:
        async def publish(self, jid, ev): pass
        async def finish(self, jid, res): self._res = res
        async def fail(self, jid, err): self._err = err

    reg = _InlineRegistry()
    await _build(client, plan=plan, app_state=mcp_state, registry=reg, job_id="mcp-inline")
    return getattr(reg, "_res", {"per_kb": []})
```

Update `get_info()`:

```python
def get_info() -> dict:
    return {
        ...
        "tool_count": 12,  # bump from 11
        ...
    }
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_mcp_zotero_ingest_tool.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_zotero_ingest_tool.py
git commit -m "feat(mcp): add build_kbs_from_zotero tool (12 tools)"
```

---

### Task 13: UI — "Build KBs from Zotero" button and modal

**Files:**
- Modify: `templates/index.html`
- Modify: `static/js/kb.js`
- Modify: `static/css/main.css` (or whichever existing CSS file the KB panel uses)
- Test: `tests/unit/test_zotero_ui_assets.py`

- [ ] **Step 1: Write the asset-presence test**

Create `tests/unit/test_zotero_ui_assets.py`:

```python
"""Smoke test: Zotero ingest UI assets are present."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_html_has_zotero_button():
    html = (ROOT / "templates/index.html").read_text()
    assert "data-testid=\"build-kbs-from-zotero\"" in html


def test_kb_js_subscribes_to_zotero_plan():
    js = (ROOT / "static/js/kb.js").read_text()
    assert "/api/zotero/plan" in js
    assert "/api/zotero/build-kbs/async" in js
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_zotero_ui_assets.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the button + modal scaffolding**

In `templates/index.html`, inside the KB panel header (search for the existing "New KB" button or similar):

```html
<button id="build-kbs-from-zotero-btn"
        data-testid="build-kbs-from-zotero"
        class="btn btn-secondary"
        title="Build KBs from your Zotero library">
  Build KBs from Zotero
</button>
<div id="zotero-build-modal" class="modal hidden">
  <div class="modal-content">
    <h3>Build KBs from Zotero</h3>
    <div id="zotero-plan-loading">Loading plan…</div>
    <table id="zotero-plan-table" class="hidden">
      <thead>
        <tr><th></th><th>KB name</th><th>Source</th><th>Items</th><th>w/ DOI</th><th>w/ PDF</th><th>w/ Notes</th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <div id="zotero-progress" class="hidden"></div>
    <div class="modal-actions">
      <button id="zotero-build-execute" class="btn btn-primary">Execute</button>
      <button id="zotero-build-cancel" class="btn btn-secondary">Cancel</button>
    </div>
  </div>
</div>
```

In `static/js/kb.js`, append:

```javascript
async function openZoteroBuildModal() {
  const modal = document.getElementById("zotero-build-modal");
  modal.classList.remove("hidden");
  document.getElementById("zotero-plan-loading").classList.remove("hidden");
  document.getElementById("zotero-plan-table").classList.add("hidden");
  document.getElementById("zotero-progress").classList.add("hidden");

  let plan = [];
  try {
    const r = await fetch("/api/zotero/plan");
    if (r.status === 503) {
      document.getElementById("zotero-plan-loading").textContent =
        "Zotero is not configured (set zotero.enabled in config.yml).";
      return;
    }
    const body = await r.json();
    plan = body.plan || [];
  } catch (e) {
    document.getElementById("zotero-plan-loading").textContent = `Error: ${e}`;
    return;
  }

  const tbody = document.querySelector("#zotero-plan-table tbody");
  tbody.innerHTML = "";
  plan.forEach((p, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="checkbox" data-i="${i}" checked /></td>
      <td><input type="text" data-name="${i}" value="${p.kb_name}" /></td>
      <td>${p.source_collection_name || "Unfiled"}</td>
      <td>${p.item_count}</td>
      <td>${p.with_doi_count}</td>
      <td>${p.with_pdf_count}</td>
      <td>${p.with_notes_count}</td>
    `;
    tbody.appendChild(tr);
  });
  document.getElementById("zotero-plan-loading").classList.add("hidden");
  document.getElementById("zotero-plan-table").classList.remove("hidden");

  document.getElementById("zotero-build-execute").onclick = async () => {
    const selected = [];
    document.querySelectorAll("#zotero-plan-table tbody tr").forEach((tr, i) => {
      const cb = tr.querySelector('input[type="checkbox"]');
      const nameIn = tr.querySelector('input[type="text"]');
      if (cb.checked) {
        selected.push({ ...plan[i], kb_name: nameIn.value });
      }
    });
    const r = await fetch("/api/zotero/build-kbs/async", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ plan: selected }),
    });
    const body = await r.json();
    document.getElementById("zotero-progress").classList.remove("hidden");
    document.getElementById("zotero-plan-table").classList.add("hidden");
    const ev = new EventSource(body.sse_url);
    ev.onmessage = (m) => {
      document.getElementById("zotero-progress").textContent += m.data + "\n";
    };
    ev.addEventListener("done", () => {
      ev.close();
      document.getElementById("zotero-progress").textContent +=
        "\nDone. KB list refreshed.";
      if (typeof refreshKBList === "function") refreshKBList();
    });
  };
  document.getElementById("zotero-build-cancel").onclick = () =>
    modal.classList.add("hidden");
}

document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("build-kbs-from-zotero-btn");
  if (btn) btn.addEventListener("click", openZoteroBuildModal);
});
```

Add minimal modal CSS to an existing `static/css/*.css` (e.g., `main.css`):

```css
.modal.hidden { display: none; }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
.modal-content { background: var(--surface, white); padding: 1.5rem; border-radius: 8px; max-width: 80vw; max-height: 80vh; overflow: auto; }
#zotero-plan-table { width: 100%; border-collapse: collapse; }
#zotero-plan-table th, #zotero-plan-table td { padding: 0.5rem; border-bottom: 1px solid var(--border, #eee); }
#zotero-progress { white-space: pre-wrap; font-family: monospace; padding: 0.5rem; max-height: 50vh; overflow: auto; }
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_zotero_ui_assets.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html static/js/kb.js static/css/main.css tests/unit/test_zotero_ui_assets.py
git commit -m "feat(web/ui): 'Build KBs from Zotero' button + plan modal + SSE progress"
```

---

### Task 14: Phase 2 docs (MANUAL_QA + config.example.yml)

**Files:**
- Modify: `MANUAL_QA.md`
- Modify: `config.example.yml`

- [ ] **Step 1: Append MANUAL_QA section**

Append to `MANUAL_QA.md`:

```markdown
## Zotero → KB ingest (2026-05-13)

Prereqs: set `zotero.enabled: true`, `zotero.api_key`, `zotero.library_id` in `config.yml`.

1. Click "Build KBs from Zotero" in the KB panel header.
2. Confirm modal loads a plan table with rows per top-level collection + "Unfiled".
3. Rename one target KB; uncheck another row.
4. Click Execute. Confirm a progress pane appears with per-item lines.
5. After "Done", confirm new KBs are in the KB list with non-zero paper/chunk counts.
6. Verify DOI dedup: re-run the same plan. Expect skips (no duplicates added).

MCP path:
- Call `build_kbs_from_zotero(plan_only=True)` from an MCP client; confirm plan returned.
- Call with `plan_only=False`; confirm result has `per_kb` summary.
```

- [ ] **Step 2: Append Zotero docs to `config.example.yml`**

In the `zotero:` block, ensure the keys are documented; nothing new to add (existing block already covers `enabled`, `api_key`, `library_id`, `library_type`, `collection_key`). Add a comment:

```yaml
zotero:
  enabled: false       # set true to enable Zotero → KB ingest
  api_key: ""
  library_id: ""
  library_type: "user" # "user" or "group"
  collection_key: ""   # default target collection for PUSH; unused for ingest
```

- [ ] **Step 3: Commit**

```bash
git add MANUAL_QA.md config.example.yml
git commit -m "docs(zotero): MANUAL_QA checklist + config notes for Zotero ingest"
```

---

## Phase 3 — Local docs + content-type-aware chunking

### Task 15: `PaperSource.LOCAL` + extended `ChunkMetadata`

**Files:**
- Modify: `src/perspicacite/models/papers.py`
- Modify: `src/perspicacite/models/documents.py`
- Test: `tests/unit/test_models_local_extensions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_models_local_extensions.py`:

```python
"""PaperSource.LOCAL and ChunkMetadata local-doc fields."""

from __future__ import annotations

from perspicacite.models.documents import ChunkMetadata
from perspicacite.models.papers import PaperSource


def test_paper_source_local_exists():
    assert PaperSource.LOCAL == "local"


def test_chunk_metadata_local_fields():
    md = ChunkMetadata(
        paper_id="local:abc",
        chunk_index=0,
        source=PaperSource.LOCAL,
        content_type="markdown",
        language=None,
        heading_path=["Intro", "Setup"],
        source_file_path="/abs/path.md",
    )
    assert md.content_type == "markdown"
    assert md.heading_path == ["Intro", "Setup"]
    assert md.source_file_path == "/abs/path.md"


def test_chunk_metadata_back_compat_without_new_fields():
    md = ChunkMetadata(paper_id="p1", chunk_index=0)
    assert md.content_type is None
    assert md.language is None
    assert md.heading_path is None
    assert md.source_file_path is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_models_local_extensions.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `PaperSource.LOCAL`**

In `src/perspicacite/models/papers.py`:

```python
class PaperSource(str, Enum):
    BIBTEX = "bibtex"
    SCILEX = "scilex"
    WEB_SEARCH = "web_search"
    USER_UPLOAD = "user_upload"
    CITATION_FOLLOW = "citation_follow"
    LOCAL = "local"
```

- [ ] **Step 4: Extend `ChunkMetadata`**

In `src/perspicacite/models/documents.py`:

```python
class ChunkMetadata(BaseModel):
    model_config = {"frozen": True}

    paper_id: str
    chunk_index: int
    section: Optional[str] = None
    page_number: Optional[int] = None
    source: PaperSource = PaperSource.BIBTEX
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    # Local-doc / smart-chunking extensions (all optional):
    content_type: Optional[str] = None       # "pdf" | "markdown" | "code" | "text"
    language: Optional[str] = None           # python | typescript | ...
    heading_path: Optional[list[str]] = None # markdown heading stack
    source_file_path: Optional[str] = None   # absolute path for local files
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_models_local_extensions.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/papers.py src/perspicacite/models/documents.py tests/unit/test_models_local_extensions.py
git commit -m "feat(models): add PaperSource.LOCAL and optional smart-chunking ChunkMetadata fields"
```

---

### Task 16: `LocalDocsConfig` + `KnowledgeBaseConfig` flags

**Files:**
- Modify: `src/perspicacite/config/schema.py`
- Test: `tests/unit/test_local_docs_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_local_docs_config.py`:

```python
"""LocalDocsConfig + KnowledgeBaseConfig new flags."""

from __future__ import annotations

from pathlib import Path

from perspicacite.config.schema import Config, KnowledgeBaseConfig, LocalDocsConfig


def test_local_docs_config_default_empty():
    c = LocalDocsConfig()
    assert c.allowed_roots == []


def test_local_docs_config_with_roots():
    c = LocalDocsConfig(allowed_roots=[Path("/tmp/docs"), Path("/var/data")])
    assert len(c.allowed_roots) == 2


def test_kb_config_has_smart_chunk_flags():
    kb = KnowledgeBaseConfig()
    assert kb.markdown_heading_aware is True
    assert kb.code_language_aware is True


def test_main_config_has_local_docs():
    c = Config()
    assert isinstance(c.local_docs, LocalDocsConfig)
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_local_docs_config.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `config/schema.py`**

Extend `KnowledgeBaseConfig`:

```python
class KnowledgeBaseConfig(BaseModel):
    embedding_model: str = "text-embedding-3-small"
    chunk_size: int = Field(default=1000, ge=100, le=10000)
    chunk_overlap: int = Field(default=200, ge=0, le=1000)
    chunking_method: Literal["token", "semantic", "agentic"] = "token"
    default_top_k: int = Field(default=10, ge=1, le=100)
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    use_two_pass: bool = Field(default=True)
    markdown_heading_aware: bool = True
    code_language_aware: bool = True
```

Add `LocalDocsConfig`:

```python
class LocalDocsConfig(BaseModel):
    """Server-side local-doc ingestion configuration."""
    allowed_roots: list[Path] = Field(default_factory=list)

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def _expand(cls, v):
        if v is None:
            return []
        return [Path(p).expanduser().resolve() for p in v]
```

Wire it into `Config`:

```python
class Config(BaseModel):
    ...
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    local_docs: LocalDocsConfig = Field(default_factory=LocalDocsConfig)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_local_docs_config.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_local_docs_config.py
git commit -m "feat(config): add LocalDocsConfig.allowed_roots and KB smart-chunking flags"
```

---

### Task 17: Add `langchain-text-splitters` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency**

In `pyproject.toml`, under `[project.dependencies]`, add:

```toml
"langchain-text-splitters>=0.3.0",
```

- [ ] **Step 2: Sync and verify import**

Run:
```bash
uv sync --dev
uv run python -c "from langchain_text_splitters import RecursiveCharacterTextSplitter, Language; print(Language.PYTHON)"
```

Expected: prints `Language.PYTHON`.

- [ ] **Step 3: Run unit suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add langchain-text-splitters>=0.3 for code/markdown chunking"
```

---

### Task 18: `pipeline/chunking_dispatch.py` — infer + markdown + code

**Files:**
- Create: `src/perspicacite/pipeline/chunking_dispatch.py`
- Test: `tests/unit/test_chunking_dispatch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chunking_dispatch.py`:

```python
"""chunking_dispatch.infer_content_type and chunk_document."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.config.schema import KnowledgeBaseConfig
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import chunk_document, infer_content_type


def test_infer_content_type_pdf():
    ct, lang = infer_content_type(Path("/a/b/file.pdf"))
    assert ct == "pdf" and lang is None


def test_infer_content_type_markdown():
    ct, lang = infer_content_type(Path("/a/b/file.md"))
    assert ct == "markdown" and lang is None


def test_infer_content_type_code_python():
    ct, lang = infer_content_type(Path("/a/b/foo.py"))
    assert ct == "code" and lang == "python"


def test_infer_content_type_code_typescript():
    ct, lang = infer_content_type(Path("/a/b/foo.ts"))
    assert ct == "code" and lang == "typescript"


def test_infer_content_type_fallback_text():
    ct, lang = infer_content_type(Path("/a/b/notes.unknown"))
    assert ct == "text" and lang is None


def _paper():
    return Paper(id="local:p1", title="t", source=PaperSource.LOCAL)


@pytest.mark.asyncio
async def test_chunk_markdown_keeps_heading_path():
    cfg = KnowledgeBaseConfig()
    text = "# Top\n\nIntro.\n\n## Sub\n\nDetail.\n\n### Sub2\n\nMore."
    chunks = await chunk_document(text, _paper(), content_type="markdown", language=None, config=cfg)
    assert chunks
    heading_paths = {tuple(c.metadata.heading_path or []) for c in chunks}
    assert ("Top",) in heading_paths
    assert ("Top", "Sub") in heading_paths
    for c in chunks:
        assert c.metadata.content_type == "markdown"


@pytest.mark.asyncio
async def test_chunk_markdown_atomic_code_fence():
    cfg = KnowledgeBaseConfig()
    text = (
        "# Top\n\nstart\n\n```python\n"
        "def foo():\n    return 1\n"
        "```\n\nafter."
    )
    chunks = await chunk_document(text, _paper(), content_type="markdown", language=None, config=cfg)
    fence_text = "\n".join(c.text for c in chunks if "```" in c.text)
    assert "def foo" in fence_text


@pytest.mark.asyncio
async def test_chunk_code_python_tagged_with_language():
    cfg = KnowledgeBaseConfig(chunk_size=200, chunk_overlap=20)
    code = "\n\n".join([f"def func_{i}():\n    return {i}" for i in range(20)])
    chunks = await chunk_document(code, _paper(), content_type="code", language="python", config=cfg)
    assert chunks
    for c in chunks:
        assert c.metadata.content_type == "code"
        assert c.metadata.language == "python"
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_chunking_dispatch.py -v`
Expected: FAIL.

- [ ] **Step 3: Create the module**

Create `src/perspicacite/pipeline/chunking_dispatch.py`:

```python
"""Content-type-aware chunking dispatcher for local docs (and reusable elsewhere)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Optional

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import Paper

_EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".cs": "csharp",
}

_LANG_TO_LC = {
    "python": Language.PYTHON,
    "javascript": Language.JS,
    "typescript": Language.TS,
    "go": Language.GO,
    "rust": Language.RUST,
    "java": Language.JAVA,
    "cpp": Language.CPP,
    "ruby": Language.RUBY,
    "swift": Language.SWIFT,
    "kotlin": Language.KOTLIN,
    "csharp": Language.CSHARP,
}


def infer_content_type(path: Path) -> tuple[str, Optional[str]]:
    """Return (content_type, language). content_type ∈ {pdf, markdown, code, text}."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return ("pdf", None)
    if ext in {".md", ".mdx"}:
        return ("markdown", None)
    if ext in _EXT_TO_LANG:
        return ("code", _EXT_TO_LANG[ext])
    return ("text", None)


def _local_paper_id_for(path: Path) -> str:
    h = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    return f"local:{h}"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_")[:64]


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```")


def _split_markdown_blocks(text: str) -> list[tuple[list[str], str]]:
    """Walk text line by line; track heading stack; emit (heading_path, block_text).
    Code fences (```...```) are kept atomic — never split across blocks."""
    lines = text.split("\n")
    stack: list[str] = []
    out: list[tuple[list[str], str]] = []
    buf: list[str] = []
    in_fence = False
    for ln in lines:
        if _FENCE_RE.match(ln.strip()):
            in_fence = not in_fence
            buf.append(ln)
            continue
        if in_fence:
            buf.append(ln)
            continue
        m = _HEADING_RE.match(ln)
        if m:
            if buf:
                out.append((list(stack), "\n".join(buf).strip()))
                buf = []
            depth = len(m.group(1))
            title = m.group(2).strip()
            stack = stack[: depth - 1]
            stack.append(title)
            continue
        buf.append(ln)
    if buf:
        out.append((list(stack), "\n".join(buf).strip()))
    return [(s, t) for s, t in out if t]


def _chunk_markdown(text: str, paper: Paper, config) -> list[DocumentChunk]:
    blocks = _split_markdown_blocks(text)
    base_id = paper.id
    chunks: list[DocumentChunk] = []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )
    idx = 0
    for heading_path, body in blocks:
        for piece in splitter.split_text(body):
            md = ChunkMetadata(
                paper_id=base_id,
                chunk_index=idx,
                section=heading_path[-1] if heading_path else None,
                source=paper.source,
                title=paper.title,
                content_type="markdown",
                heading_path=heading_path,
            )
            chunks.append(
                DocumentChunk(id=f"{base_id}_md_{idx}", text=piece, metadata=md)
            )
            idx += 1
    return chunks


def _chunk_code(text: str, paper: Paper, config, *, language: str) -> list[DocumentChunk]:
    lc_lang = _LANG_TO_LC.get(language)
    if lc_lang is None:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )
    else:
        splitter = RecursiveCharacterTextSplitter.from_language(
            lc_lang,
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )
    base_id = paper.id
    chunks: list[DocumentChunk] = []
    for i, piece in enumerate(splitter.split_text(text)):
        md = ChunkMetadata(
            paper_id=base_id,
            chunk_index=i,
            source=paper.source,
            title=paper.title,
            content_type="code",
            language=language,
        )
        chunks.append(DocumentChunk(id=f"{base_id}_code_{i}", text=piece, metadata=md))
    return chunks


async def chunk_document(
    text: str,
    paper: Paper,
    *,
    content_type: str,
    language: Optional[str],
    config,
) -> list[DocumentChunk]:
    """Dispatch to the right chunker. PDF/text routes to the existing token chunker."""
    if content_type == "markdown" and getattr(config, "markdown_heading_aware", True):
        return _chunk_markdown(text, paper, config)
    if content_type == "code" and getattr(config, "code_language_aware", True) and language:
        return _chunk_code(text, paper, config, language=language)
    # text / pdf / fallback — use existing token chunker
    from perspicacite.pipeline.chunking import chunk_text
    return await chunk_text(text, paper, config)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_chunking_dispatch.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/chunking_dispatch.py tests/unit/test_chunking_dispatch.py
git commit -m "feat(pipeline): chunking_dispatch — markdown heading-aware + code language-aware splitters"
```

---

### Task 19: `integrations/local_docs.py` — path validate + worker

**Files:**
- Create: `src/perspicacite/integrations/local_docs.py`
- Test: `tests/unit/test_local_docs_worker.py`
- Test: `tests/unit/test_local_docs_validate.py`

- [ ] **Step 1: Write the validator test**

Create `tests/unit/test_local_docs_validate.py`:

```python
"""validate_local_path rejects unsafe paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.integrations.local_docs import (
    LocalDocsDisabledError,
    LocalDocsValidationError,
    validate_local_path,
)


def test_rejects_relative_path(tmp_path):
    with pytest.raises(LocalDocsValidationError):
        validate_local_path("relative/path.md", allowed_roots=[tmp_path])


def test_rejects_dotdot(tmp_path):
    with pytest.raises(LocalDocsValidationError):
        validate_local_path(str(tmp_path / ".." / "x"), allowed_roots=[tmp_path])


def test_rejects_outside_allowed_roots(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "f.md").write_text("x")
    with pytest.raises(LocalDocsValidationError):
        validate_local_path(str(other / "f.md"), allowed_roots=[tmp_path / "inside"])


def test_raises_disabled_when_roots_empty(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    with pytest.raises(LocalDocsDisabledError):
        validate_local_path(str(p), allowed_roots=[])


def test_accepts_valid_path_under_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "doc.md"
    f.write_text("hi")
    out = validate_local_path(str(f), allowed_roots=[root])
    assert out == f.resolve()
```

- [ ] **Step 2: Write the worker test**

Create `tests/unit/test_local_docs_worker.py`:

```python
"""local_docs worker dispatches per content type."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.integrations.local_docs import _ingest_files


class _Reg:
    def __init__(self):
        self.events = []
        self.finished = None

    async def publish(self, jid, ev):
        self.events.append(ev)

    async def finish(self, jid, res):
        self.finished = res

    async def fail(self, jid, err):
        self.failed = err


class _Emb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]


class _VS:
    def __init__(self):
        self.added: list = []

    async def add_chunks(self, collection, chunks):
        self.added.extend(chunks)


@pytest.mark.asyncio
async def test_worker_ingests_markdown_and_code(tmp_path, monkeypatch):
    md = tmp_path / "notes.md"
    md.write_text("# Top\n\nIntro\n\n## Sub\n\nDetail")
    py = tmp_path / "lib.py"
    py.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")

    fake_state = SimpleNamespace(
        config=SimpleNamespace(knowledge_base=SimpleNamespace(
            chunk_size=1000, chunk_overlap=200,
            markdown_heading_aware=True, code_language_aware=True,
        )),
        embedding_provider=_Emb(),
        vector_store=_VS(),
        pdf_parser=None,
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="perspicacite_local", paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
    )
    reg = _Reg()
    await _ingest_files(
        kb_name="local",
        files=[md, py],
        app_state=fake_state,
        registry=reg,
        job_id="J1",
    )
    cts = {c.metadata.content_type for c in fake_state.vector_store.added}
    assert {"markdown", "code"} <= cts
    langs = {c.metadata.language for c in fake_state.vector_store.added if c.metadata.content_type == "code"}
    assert "python" in langs
    assert reg.finished is not None
```

- [ ] **Step 3: Run to confirm failures**

```bash
uv run pytest tests/unit/test_local_docs_validate.py tests/unit/test_local_docs_worker.py -v
```
Expected: FAIL (module missing).

- [ ] **Step 4: Create the module**

Create `src/perspicacite/integrations/local_docs.py`:

```python
"""Local-document ingestion: path validate + chunk dispatch + KB write."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import (
    _local_paper_id_for,
    chunk_document,
    infer_content_type,
)

logger = get_logger("perspicacite.local_docs")


class LocalDocsValidationError(ValueError):
    """Raised when a path fails validation."""


class LocalDocsDisabledError(RuntimeError):
    """Raised when local_docs.allowed_roots is empty (server-side path entry disabled)."""


def validate_local_path(raw_path: str, *, allowed_roots: list[Path]) -> Path:
    """Reject relative paths / '..' / outside allowed_roots. Return resolved Path."""
    if not allowed_roots:
        raise LocalDocsDisabledError(
            "local_docs.allowed_roots is empty — server-side path ingest is disabled"
        )
    if not os.path.isabs(raw_path):
        raise LocalDocsValidationError(f"path must be absolute: {raw_path}")
    if ".." in Path(raw_path).parts:
        raise LocalDocsValidationError(f"path must not contain '..': {raw_path}")
    p = Path(raw_path).resolve()
    if not p.exists():
        raise LocalDocsValidationError(f"path does not exist: {raw_path}")
    for root in allowed_roots:
        try:
            p.relative_to(root.resolve())
            return p
        except ValueError:
            continue
    raise LocalDocsValidationError(
        f"path {raw_path} is not under any local_docs.allowed_roots"
    )


def expand_paths(paths: list[Path], *, recursive: bool) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            if recursive:
                out.extend(f for f in p.rglob("*") if f.is_file())
        else:
            out.append(p)
    return out


def _paper_for_file(path: Path) -> Paper:
    return Paper(
        id=_local_paper_id_for(path),
        title=path.name,
        source=PaperSource.LOCAL,
    )


async def _read_text(path: Path, content_type: str, pdf_parser) -> str | None:
    if content_type == "pdf":
        if pdf_parser is None:
            return None
        parsed = await pdf_parser.parse(path)
        return parsed.text or None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("local_docs_read_failed", path=str(path), error=str(exc))
        return None


async def _ingest_files(
    *,
    kb_name: str,
    files: list[Path],
    app_state,
    registry,
    job_id: str,
) -> dict[str, Any]:
    try:
        kb = await app_state.session_store.get_kb_metadata(kb_name)
        if kb is None:
            await registry.fail(job_id, f"KB '{kb_name}' not found")
            return {}
        kb_cfg = app_state.config.knowledge_base
        total_chunks = 0
        for idx, fp in enumerate(files):
            content_type, language = infer_content_type(fp)
            paper = _paper_for_file(fp)
            text = await _read_text(fp, content_type, app_state.pdf_parser)
            if not text:
                await registry.publish(job_id, {
                    "type": "progress", "done": idx + 1, "file": str(fp), "status": "empty",
                })
                continue
            chunks = await chunk_document(
                text, paper,
                content_type=content_type, language=language, config=kb_cfg,
            )
            # tag with source_file_path
            for c in chunks:
                # ChunkMetadata is frozen — recreate
                from perspicacite.models.documents import ChunkMetadata as _CM
                new_md = _CM(
                    **{**c.metadata.model_dump(), "source_file_path": str(fp.resolve())}
                )
                c.metadata = new_md
            if chunks:
                # embed + write
                texts = [c.text for c in chunks]
                embeds = await app_state.embedding_provider.embed(texts)
                for c, e in zip(chunks, embeds):
                    c.embedding = e
                await app_state.vector_store.add_chunks(kb.collection_name, chunks)
                total_chunks += len(chunks)
            await registry.publish(job_id, {
                "type": "progress", "done": idx + 1, "file": str(fp),
                "status": "embedded", "chunks": len(chunks),
            })
        kb.chunk_count += total_chunks
        await app_state.session_store.save_kb_metadata(kb)
        result = {"added_chunks": total_chunks, "files": len(files)}
        await registry.finish(job_id, result)
        return result
    except Exception as exc:
        logger.error("local_docs_ingest_failed", error=str(exc))
        await registry.fail(job_id, str(exc))
        raise


async def ingest_local_documents(
    *,
    kb_name: str,
    paths: list[Path],
    app_state,
    registry,
    job_id: str,
    recursive: bool = True,
) -> dict[str, Any]:
    """Top-level entry used by routers, CLI, and MCP."""
    expanded = expand_paths(paths, recursive=recursive)
    return await _ingest_files(
        kb_name=kb_name, files=expanded, app_state=app_state,
        registry=registry, job_id=job_id,
    )
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_local_docs_validate.py tests/unit/test_local_docs_worker.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/integrations/local_docs.py tests/unit/test_local_docs_validate.py tests/unit/test_local_docs_worker.py
git commit -m "feat(local_docs): path validator + ingest worker (markdown/code/pdf dispatch)"
```

---

### Task 20: Routers `/api/kb/{name}/local-files` and `/local-paths`

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_local_docs_router.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_local_docs_router.py`:

```python
"""local-files and local-paths router endpoints."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from perspicacite.web import app as web_app


def _state(allowed_roots: list[Path] | None = None):
    return SimpleNamespace(
        config=SimpleNamespace(
            local_docs=SimpleNamespace(allowed_roots=allowed_roots or []),
            knowledge_base=SimpleNamespace(
                chunk_size=1000, chunk_overlap=200,
                markdown_heading_aware=True, code_language_aware=True,
            ),
        ),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
        session_store=SimpleNamespace(get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
            collection_name="perspicacite_local", paper_count=0, chunk_count=0,
        ))),
        vector_store=None,
        embedding_provider=None,
        pdf_parser=None,
    )


def test_local_paths_returns_503_when_allowed_roots_empty(monkeypatch):
    monkeypatch.setattr("perspicacite.web.state.app_state", _state(allowed_roots=[]))
    client = TestClient(web_app.app)
    r = client.post("/api/kb/local/local-paths", json={"paths": ["/etc/hosts"]})
    assert r.status_code == 503


def test_local_files_accepts_upload(monkeypatch):
    monkeypatch.setattr("perspicacite.web.state.app_state", _state())
    client = TestClient(web_app.app)
    files = {"files": ("notes.md", BytesIO(b"# Hi\n\nBody"), "text/markdown")}
    r = client.post("/api/kb/local/local-files", files=files)
    assert r.status_code in (200, 202)
    assert "job_id" in r.json()


def test_local_paths_accepts_valid_path(tmp_path, monkeypatch):
    f = tmp_path / "n.md"
    f.write_text("# t\n\nbody")
    monkeypatch.setattr("perspicacite.web.state.app_state", _state(allowed_roots=[tmp_path]))
    client = TestClient(web_app.app)
    r = client.post("/api/kb/local/local-paths", json={"paths": [str(f)], "recursive": False})
    assert r.status_code in (200, 202)
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_local_docs_router.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the endpoints**

Append to `src/perspicacite/web/routers/kb.py`:

```python
from fastapi import BackgroundTasks, File, UploadFile
from pydantic import BaseModel

from perspicacite.integrations.local_docs import (
    LocalDocsDisabledError,
    LocalDocsValidationError,
    expand_paths,
    ingest_local_documents,
    validate_local_path,
)


class AddLocalPathsRequest(BaseModel):
    paths: list[str]
    recursive: bool = True


_local_tasks: set[asyncio.Task] = set()  # ensure asyncio import is already in file


@router.post("/api/kb/{name}/local-files")
async def add_local_files(
    name: str,
    files: list[UploadFile] = File(...),
) -> dict:
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(prefix="perspicacite_upload_"))
    saved: list[Path] = []
    for uf in files:
        target = tmpdir / Path(uf.filename or "upload").name
        with target.open("wb") as out:
            while True:
                chunk = await uf.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
        saved.append(target)
    job_id = await app_state.job_registry.create("local_docs_upload", total=len(saved))
    task = asyncio.create_task(
        ingest_local_documents(
            kb_name=name, paths=saved, app_state=app_state,
            registry=app_state.job_registry, job_id=job_id, recursive=False,
        )
    )
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}


@router.post("/api/kb/{name}/local-paths")
async def add_local_paths(name: str, payload: AddLocalPathsRequest) -> dict:
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    allowed = list(getattr(app_state.config.local_docs, "allowed_roots", []) or [])
    validated: list[Path] = []
    for raw in payload.paths:
        try:
            validated.append(validate_local_path(raw, allowed_roots=allowed))
        except LocalDocsDisabledError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except LocalDocsValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    expanded = expand_paths(validated, recursive=payload.recursive)
    # re-validate every expanded file (covers symlink escapes)
    for f in expanded:
        try:
            validate_local_path(str(f), allowed_roots=allowed)
        except LocalDocsValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    job_id = await app_state.job_registry.create("local_docs_paths", total=len(expanded))
    task = asyncio.create_task(
        ingest_local_documents(
            kb_name=name, paths=expanded, app_state=app_state,
            registry=app_state.job_registry, job_id=job_id, recursive=False,
        )
    )
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}
```

(If `asyncio` is not already imported at the top of `kb.py`, add `import asyncio`.)

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_local_docs_router.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_local_docs_router.py
git commit -m "feat(web/kb): /local-files (multipart) and /local-paths (server-side, allow-listed)"
```

---

### Task 21: MCP tool `ingest_local_documents`

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_mcp_local_docs_tool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_local_docs_tool.py`:

```python
"""MCP ingest_local_documents tool — refuses without allow-list, works with one."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_refuses_without_allowed_roots(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", SimpleNamespace(
        config=SimpleNamespace(local_docs=SimpleNamespace(allowed_roots=[])),
    ))
    fn = mcp_server.ingest_local_documents
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(kb_name="x", paths=["/etc/hosts"])
    assert "error" in out


@pytest.mark.asyncio
async def test_works_with_allow_list(tmp_path, monkeypatch):
    f = tmp_path / "doc.md"
    f.write_text("# x")
    captured: dict = {}

    async def _ingest(**kwargs):
        captured.update(kwargs)
        return {"added_chunks": 1, "files": 1}

    monkeypatch.setattr("perspicacite.integrations.local_docs.ingest_local_documents", _ingest)
    monkeypatch.setattr(mcp_server, "mcp_state", SimpleNamespace(
        config=SimpleNamespace(local_docs=SimpleNamespace(allowed_roots=[tmp_path])),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
    ))
    fn = mcp_server.ingest_local_documents
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(kb_name="x", paths=[str(f)])
    assert out.get("added_chunks") == 1


def test_get_info_lists_thirteen_tools():
    info = mcp_server.get_info()
    assert info["tool_count"] >= 13
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_mcp_local_docs_tool.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the MCP tool**

In `src/perspicacite/mcp/server.py`:

```python
@mcp.tool()
async def ingest_local_documents(
    kb_name: str,
    paths: list[str],
    recursive: bool = True,
) -> dict:
    """Ingest local files or directories into a KB.

    Files must be absolute paths under one of `local_docs.allowed_roots`.
    If allowed_roots is empty, this tool refuses all calls.
    """
    from pathlib import Path

    from perspicacite.integrations.local_docs import (
        LocalDocsDisabledError, LocalDocsValidationError,
        ingest_local_documents as _ingest, validate_local_path,
    )

    allowed = list(getattr(mcp_state.config.local_docs, "allowed_roots", []) or [])
    validated: list[Path] = []
    try:
        for raw in paths:
            validated.append(validate_local_path(raw, allowed_roots=allowed))
    except LocalDocsDisabledError as exc:
        return {"error": str(exc)}
    except LocalDocsValidationError as exc:
        return {"error": str(exc)}

    class _Reg:
        async def publish(self, jid, ev): pass
        async def finish(self, jid, res): self._res = res
        async def fail(self, jid, err): self._err = err

    reg = _Reg()
    return await _ingest(
        kb_name=kb_name, paths=validated, app_state=mcp_state,
        registry=reg, job_id="mcp-inline", recursive=recursive,
    )
```

Bump `get_info()["tool_count"]` from 12 → 13.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_mcp_local_docs_tool.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_local_docs_tool.py
git commit -m "feat(mcp): add ingest_local_documents tool (13 tools)"
```

---

### Task 22: CLI subcommand `ingest-local`

**Files:**
- Modify: `src/perspicacite/cli.py`
- Test: `tests/unit/test_cli_ingest_local.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_ingest_local.py`:

```python
"""CLI: ingest-local subcommand calls worker."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from perspicacite.cli import app


def test_ingest_local_help():
    runner = CliRunner()
    r = runner.invoke(app, ["ingest-local", "--help"])
    assert r.exit_code == 0
    assert "--kb" in r.stdout
    assert "--path" in r.stdout


def test_ingest_local_calls_worker(tmp_path, monkeypatch):
    f = tmp_path / "x.md"
    f.write_text("# t\n\nb")
    called: dict = {}

    async def _ingest(**kwargs):
        called.update(kwargs)
        return {"added_chunks": 1, "files": 1}

    monkeypatch.setattr(
        "perspicacite.integrations.local_docs.ingest_local_documents", _ingest,
    )
    runner = CliRunner()
    r = runner.invoke(app, [
        "ingest-local", "--kb", "mykb", "--path", str(f),
    ])
    assert r.exit_code == 0, r.stdout
    assert called.get("kb_name") == "mykb"
    assert any(str(f) in str(p) for p in called.get("paths", []))
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_cli_ingest_local.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the subcommand**

In `src/perspicacite/cli.py`:

```python
@app.command("ingest-local")
def ingest_local(
    kb: str = typer.Option(..., "--kb", help="Target KB name"),
    path: list[Path] = typer.Option(..., "--path", help="File or directory; can repeat"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive"),
    config_path: Path = typer.Option(Path("config.yml"), "-c", "--config"),
):
    """Ingest local files/directories into a KB (no server needed)."""
    import asyncio

    from perspicacite.config.loader import load_config
    from perspicacite.integrations.local_docs import ingest_local_documents
    from perspicacite.web.state import AppState

    async def _run():
        cfg = load_config(config_path)
        state = AppState()
        await state.initialize(cfg)
        try:
            class _Reg:
                async def publish(self, jid, ev): pass
                async def finish(self, jid, res): self._res = res
                async def fail(self, jid, err): self._err = err
            reg = _Reg()
            result = await ingest_local_documents(
                kb_name=kb, paths=list(path), app_state=state,
                registry=reg, job_id="cli", recursive=recursive,
            )
            typer.echo(f"Done: {result}")
        finally:
            await state.shutdown()

    asyncio.run(_run())
```

(If `app = typer.Typer(...)` is already established, drop the helper just under the other subcommands.)

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli_ingest_local.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/cli.py tests/unit/test_cli_ingest_local.py
git commit -m "feat(cli): add ingest-local subcommand"
```

---

### Task 23: UI — drag-and-drop into KB panel

**Files:**
- Modify: `templates/index.html`
- Modify: `static/js/kb.js`
- Modify: `static/css/main.css`
- Test: `tests/unit/test_local_docs_ui_assets.py`

- [ ] **Step 1: Write the asset-presence test**

Create `tests/unit/test_local_docs_ui_assets.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_html_has_dropzone():
    html = (ROOT / "templates/index.html").read_text()
    assert "data-testid=\"kb-local-dropzone\"" in html


def test_kb_js_handles_local_files_post():
    js = (ROOT / "static/js/kb.js").read_text()
    assert "/api/kb/" in js and "/local-files" in js
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/unit/test_local_docs_ui_assets.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the drop-zone**

In `templates/index.html`, inside the KB detail panel:

```html
<div id="kb-local-dropzone" data-testid="kb-local-dropzone" class="kb-dropzone">
  <p>Drop files here, or <label for="kb-local-file-picker" class="link">click to choose</label></p>
  <input type="file" id="kb-local-file-picker" multiple class="hidden" />
  <div id="kb-local-progress" class="hidden"></div>
</div>
```

In `static/js/kb.js`:

```javascript
function wireKBDropzone() {
  const dz = document.getElementById("kb-local-dropzone");
  const picker = document.getElementById("kb-local-file-picker");
  if (!dz || !picker) return;

  async function upload(files) {
    const kbName = document.querySelector("[data-current-kb]")?.dataset.currentKb;
    if (!kbName) return alert("Select a KB first.");
    const fd = new FormData();
    [...files].forEach((f) => fd.append("files", f));
    const r = await fetch(`/api/kb/${encodeURIComponent(kbName)}/local-files`, {
      method: "POST",
      body: fd,
    });
    const body = await r.json();
    const prog = document.getElementById("kb-local-progress");
    prog.classList.remove("hidden");
    prog.textContent = "";
    const ev = new EventSource(body.sse_url);
    ev.onmessage = (m) => { prog.textContent += m.data + "\n"; };
    ev.addEventListener("done", () => {
      ev.close();
      prog.textContent += "\nDone.";
      if (typeof refreshKBList === "function") refreshKBList();
    });
  }
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("over");
    upload(e.dataTransfer.files);
  });
  picker.addEventListener("change", (e) => upload(e.target.files));
}
document.addEventListener("DOMContentLoaded", wireKBDropzone);
```

CSS in `static/css/main.css`:

```css
.kb-dropzone { border: 2px dashed var(--border, #ccc); padding: 1rem; border-radius: 8px; text-align: center; margin: 0.5rem 0; }
.kb-dropzone.over { background: var(--surface-alt, #f5f5f5); }
.link { cursor: pointer; text-decoration: underline; }
.hidden { display: none; }
#kb-local-progress { white-space: pre-wrap; font-family: monospace; max-height: 30vh; overflow: auto; }
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_local_docs_ui_assets.py -v
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html static/js/kb.js static/css/main.css tests/unit/test_local_docs_ui_assets.py
git commit -m "feat(web/ui): drag-and-drop local-doc upload zone in KB panel"
```

---

### Task 24: Phase 3 docs (MANUAL_QA + config.example.yml)

**Files:**
- Modify: `MANUAL_QA.md`
- Modify: `config.example.yml`

- [ ] **Step 1: Append MANUAL_QA section**

Append to `MANUAL_QA.md`:

```markdown
## Local documents → KB (2026-05-13)

Web upload:
1. Open KB detail.
2. Drag a PDF, a markdown file, and a Python file onto the drop zone.
3. Confirm per-file progress lines stream in.
4. After "Done", confirm chunk count went up.
5. Run a chat query that should hit the markdown file; confirm a chunk with `heading_path` appears in sources.

CLI:
- `uv run perspicacite ingest-local --kb mykb --path /abs/path/to/file.md`
- Confirm exit code 0 and "Done" output.

Server-side path:
- Without `local_docs.allowed_roots` set, `POST /api/kb/mykb/local-paths` returns 503.
- With one root set, posting a path under it returns a job_id; posting `/etc/hosts` returns 400.

MCP:
- `ingest_local_documents(kb_name="mykb", paths=["/etc/hosts"])` → `{"error": "..."}` when no allow-list.

Language tags in provenance:
- Open a conversation that retrieved a code chunk.
- Open the provenance JSONL sidecar; confirm the chunk row carries `language` and `content_type`.
```

- [ ] **Step 2: Update `config.example.yml`**

Add (near the bottom of the file):

```yaml
# Local-document ingest — paths server-side ingest will accept.
# If empty, /api/kb/{name}/local-paths returns 503 and the
# ingest_local_documents MCP tool refuses all calls.
# Web upload (multipart) is unaffected by this setting.
local_docs:
  allowed_roots: []
  # Example:
  # allowed_roots:
  #   - "/Users/me/Documents/research"
  #   - "/data/lab-notes"

# Inside the knowledge_base block:
knowledge_base:
  # ...existing fields...
  markdown_heading_aware: true   # heading-aware markdown chunking
  code_language_aware: true      # language-aware code chunking
```

- [ ] **Step 3: Commit**

```bash
git add MANUAL_QA.md config.example.yml
git commit -m "docs(local-docs): MANUAL_QA checklist + config notes for local-doc ingest"
```

---

### Task 25: CLAUDE.md / docs/rules updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/rules/rag_development.md` (if it exists; otherwise skip silently)

- [ ] **Step 1: Update CLAUDE.md "Multi-KB query" section**

In `CLAUDE.md`, find the paragraph that says "Currently wired into `basic` and `contradiction` modes". Replace with:

```markdown
**Multi-KB query:** All six RAG modes honor `RAGRequest.kb_names`. The chat router runs `check_embedding_compat` before streaming and emits an error SSE event on mismatch. The `generate_report`, `search_knowledge_base`, `build_kbs_from_zotero`, and `ingest_local_documents` MCP tools accept the corresponding multi-KB / local-doc parameters.
```

Add a new section:

```markdown
### Local Documents

Local files (PDF / markdown / code / text) can be added to any KB via:
- Web: drag-and-drop in the KB panel (`POST /api/kb/{name}/local-files`, multipart).
- CLI: `uv run perspicacite ingest-local --kb <name> --path <p>`.
- MCP: `ingest_local_documents(kb_name, paths, recursive)`.

Server-side path entries require `local_docs.allowed_roots` to be set; otherwise the endpoint returns 503 and the MCP tool refuses. Web upload is unaffected.

Chunking dispatches by content type via [src/perspicacite/pipeline/chunking_dispatch.py](src/perspicacite/pipeline/chunking_dispatch.py): markdown → heading-aware splitter, code → `langchain-text-splitters.RecursiveCharacterTextSplitter.from_language(...)`, PDF/text → existing token chunker.
```

- [ ] **Step 2: Update tool count + Zotero section**

Find "10 tools" and "10 tools)" mentions in CLAUDE.md and update to 13.

Add to the relevant section:

```markdown
### Zotero ingest

`/api/zotero/plan` and `/api/zotero/build-kbs/async` (router `web/routers/zotero_ingest.py`) and the MCP tool `build_kbs_from_zotero` build one KB per top-level Zotero collection, including DOIs, attached PDFs (via `ZoteroClient.download_attachment_bytes`), and notes (HTML-stripped). Worker drives the unified content pipeline; falls back to the attached PDF when DOI fetch returns nothing.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): document multi-KB across all modes, Zotero ingest, local docs"
```

---

### Task 26: Final review pass over the full cycle

**Files:**
- Modify: any (only as needed to close review findings)

- [ ] **Step 1: Run the full unit suite and lint checks**

```bash
uv run pytest tests/unit/ -m "not live" -q
uv run ruff check src/perspicacite/retrieval/multi_kb.py src/perspicacite/rag/modes/advanced.py src/perspicacite/rag/modes/profound.py src/perspicacite/rag/modes/literature_survey.py src/perspicacite/rag/agentic/orchestrator.py src/perspicacite/integrations/zotero.py src/perspicacite/integrations/zotero_ingest.py src/perspicacite/integrations/local_docs.py src/perspicacite/pipeline/chunking_dispatch.py src/perspicacite/web/routers/zotero_ingest.py src/perspicacite/web/routers/kb.py src/perspicacite/mcp/server.py src/perspicacite/cli.py src/perspicacite/config/schema.py src/perspicacite/models/papers.py src/perspicacite/models/documents.py
uv run mypy src/perspicacite/retrieval/multi_kb.py src/perspicacite/integrations/zotero_ingest.py src/perspicacite/integrations/local_docs.py src/perspicacite/pipeline/chunking_dispatch.py
```

Expected: green; only pre-existing lint/mypy errors on untouched lines remain (we do not fix those).

- [ ] **Step 2: Smoke-start the server**

```bash
uv run perspicacite -c config.yml serve &
sleep 3
curl -s http://localhost:8000/api/health | head
curl -s http://localhost:8000/api/zotero/plan | head  # expect 503 unless zotero configured
kill %1
```

Expected: server starts; endpoints respond.

- [ ] **Step 3: Verify tool count via MCP**

```bash
uv run perspicacite -c config.yml serve &
sleep 3
uv run python -c "
import asyncio, httpx
async def main():
    async with httpx.AsyncClient() as c:
        r = await c.get('http://localhost:8000/mcp')
        print(r.status_code)
asyncio.run(main())
"
kill %1
```

Expected: server responds.

- [ ] **Step 4: Confirm spec coverage**

Open `docs/superpowers/specs/2026-05-13-multi-kb-zotero-local-docs-design.md`. Walk every section; for each requirement, confirm a corresponding task in this plan landed. Note any gaps. Address before commit.

- [ ] **Step 5: Final commit (only if cleanup happened)**

If review surfaced anything:

```bash
git add -p  # stage the cleanup
git commit -m "chore(review): final cleanup for multi-KB/Zotero/local-docs cycle"
```

If nothing needed cleanup, no commit — just confirm and close.
