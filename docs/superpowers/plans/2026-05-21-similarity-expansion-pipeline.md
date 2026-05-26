# Similarity Expansion — Plan 2 of 3: Pipeline Orchestrator

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the two-phase similarity-expansion orchestrator on Plan 1's screening core — `score_expansion_candidates` (snowball → filter → score → histogram + calibration samples) and `commit_expansion` (cutoff → ingest) — plus storing paper abstracts in chunk metadata at ingest and a reference assembler that prefers abstracts and falls back to capped chunk texts.

**Architecture:** A new `pipeline/similarity_expansion.py` reuses the lower-level pieces `expand_kb_via_citations` already calls (`snowball_expand`, `_papers_from_hits`, `apply_filters`, `ingest_dois_into_kb`) and the Plan 1 scorers. The interactive flow calls the scorers **directly** — `screen_candidates`/`expand_kb_via_citations` are untouched. Abstracts are persisted in `ChunkMetadata` at ingest so the BM25/hybrid reference can use them; older KBs degrade to capped chunk texts.

**Tech Stack:** Python 3.12, `pytest` + `pytest-asyncio`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-21-similarity-expansion-design.md`. **Depends on:** Plan 1 (committed `ae9c5d4`).

> **Roadmap:** Plan 1 ✅ (screening core). Plan 2 (this) = ingest-abstract + orchestrator. Plan 3 = REST endpoints + frontend page.

> **WSL note:** `uv run pytest` has a slow (~minutes) import cost here. Each "run the test" step is correct; batch where sensible.

---

## File Structure

- **Modify:** `src/perspicacite/models/documents.py` — add `abstract` field to `ChunkMetadata`.
- **Modify:** `src/perspicacite/retrieval/chroma_store.py` — serialize/deserialize `abstract` (`_chunk_to_metadata`, `_metadata_to_chunk`), surface it in `list_paper_metadata`, and add `list_chunk_texts`.
- **Modify:** `src/perspicacite/rag/dynamic_kb.py` — populate `abstract=paper.abstract` on the chunk-0 metadata chunk.
- **Create:** `src/perspicacite/pipeline/similarity_expansion.py` — `get_kb_reference_texts`, `ExpansionScoreReport`, `_score_histogram`, `score_expansion_candidates`, `commit_expansion`.
- **Test:** `tests/unit/test_chunk_abstract_metadata.py`, `tests/unit/test_similarity_expansion.py`.

Reused unchanged: `snowball_expand`, `_papers_from_hits` (`pipeline/snowball.py`); `apply_filters`, `SearchFilter`, `ingest_dois_into_kb` (`pipeline/search_to_kb.py`); `chroma_collection_name_for_kb` (`models/kb.py`); Plan 1 scorers (`search/screening.py`).

---

### Task 1: Persist `abstract` in chunk metadata

**Files:**
- Modify: `src/perspicacite/models/documents.py`, `src/perspicacite/retrieval/chroma_store.py`, `src/perspicacite/rag/dynamic_kb.py`
- Test: `tests/unit/test_chunk_abstract_metadata.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chunk_abstract_metadata.py`:

```python
"""abstract round-trips through ChunkMetadata <-> Chroma metadata, and is
surfaced by list_paper_metadata."""

import pytest

from perspicacite.models.documents import ChunkMetadata
from perspicacite.retrieval.chroma_store import (
    ChromaVectorStore,
    _chunk_to_metadata,
    _metadata_to_chunk,
)


def test_abstract_round_trips_through_chroma_metadata():
    cm = ChunkMetadata(paper_id="10.1/x", chunk_index=0, abstract="A short abstract.")
    flat = _chunk_to_metadata(cm)
    assert flat["abstract"] == "A short abstract."
    back = _metadata_to_chunk(flat)
    assert back.abstract == "A short abstract."


def test_abstract_absent_is_omitted_not_none():
    cm = ChunkMetadata(paper_id="10.1/x", chunk_index=0)  # no abstract
    flat = _chunk_to_metadata(cm)
    assert "abstract" not in flat  # Chroma rejects None; absent is correct


class _StubColl:
    def get(self, **kwargs):
        return {
            "metadatas": [
                {"paper_id": "p1", "title": "T1", "doi": "10.1/p1", "abstract": "abs one"},
                {"paper_id": "p1", "chunk_index": 1},  # later chunk, no abstract
                {"paper_id": "p2", "title": "T2", "doi": "10.1/p2"},  # no abstract
            ]
        }


class _StubClient:
    def get_collection(self, name):
        return _StubColl()


@pytest.mark.asyncio
async def test_list_paper_metadata_surfaces_abstract():
    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = _StubClient()
    rows = await store.list_paper_metadata("kb")
    by_pid = {r["paper_id"]: r for r in rows}
    assert by_pid["p1"]["abstract"] == "abs one"
    assert by_pid["p2"].get("abstract") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_chunk_abstract_metadata.py -q`
Expected: FAIL — `_chunk_to_metadata` doesn't include `abstract` (KeyError / assertion fails).

- [ ] **Step 3a: Add the `abstract` field to `ChunkMetadata`**

In `src/perspicacite/models/documents.py`, inside `class ChunkMetadata`, add after the `url` field (line ~24):

```python
    abstract: str | None = None
```

- [ ] **Step 3b: Serialize + deserialize `abstract`**

In `src/perspicacite/retrieval/chroma_store.py`, in `_chunk_to_metadata`, add `"abstract"` to the `scalar_fields` tuple (so it's stored when present, omitted when None):

```python
    scalar_fields = (
        "section", "page_number", "title", "authors", "year", "doi", "url",
        "abstract",
        "content_type", "language", "source_file_path",
        "source_section", "page", "parent_paper_id",
        "symbol_name", "symbol_kind", "parent_class",
        "start_line", "end_line", "docstring",
        "embedding_model", "source_via", "cited_tool", "discovery_score",
    )
```

In `_metadata_to_chunk`, add `abstract` to the `ChunkMetadata(...)` constructor (next to `url=...`):

```python
        url=metadata.get("url"),
        abstract=metadata.get("abstract"),
```

- [ ] **Step 3c: Surface `abstract` in `list_paper_metadata`**

In `src/perspicacite/retrieval/chroma_store.py`, in `list_paper_metadata`, add `abstract` to the initial merged row and to the merge loop:

```python
            if cur is None:
                by_pid[pid] = {
                    "paper_id": pid,
                    "title": m.get("title"),
                    "authors": m.get("authors"),
                    "year": m.get("year"),
                    "doi": m.get("doi"),
                    "abstract": m.get("abstract"),
                }
            else:
                for k in ("title", "authors", "doi", "abstract"):
                    if m.get(k) and not cur.get(k):
                        cur[k] = m[k]
```

- [ ] **Step 3d: Populate `abstract` at ingest**

In `src/perspicacite/rag/dynamic_kb.py`, in the chunk-0 (`_metadata`) `ChunkMetadata(...)` (the block with `section="metadata"`), add `abstract=paper.abstract`:

```python
            metadata=ChunkMetadata(
                paper_id=paper.id,
                chunk_index=0,
                source=paper.source,
                title=paper.title,
                authors=authors_str,
                year=paper.year,
                doi=paper.doi,
                url=paper.url,
                abstract=paper.abstract,
                content_type=paper_content_type,
                section="metadata",
            ),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_chunk_abstract_metadata.py -q`
Expected: PASS (3 passed). The Step-3d ingest line isn't unit-tested (it needs a full embed/ingest); it's verified by the round-trip above + that the field now propagates. Re-ingesting a paper will store its abstract.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/models/documents.py src/perspicacite/retrieval/chroma_store.py \
  src/perspicacite/rag/dynamic_kb.py tests/unit/test_chunk_abstract_metadata.py
git commit -m "feat(kb): persist paper abstract in chunk metadata"
```

---

### Task 2: `ChromaVectorStore.list_chunk_texts` (fallback corpus)

**Files:**
- Modify: `src/perspicacite/retrieval/chroma_store.py` (add after `list_paper_metadata`)
- Test: `tests/unit/test_chroma_list_chunk_texts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chroma_list_chunk_texts.py`:

```python
"""ChromaVectorStore.list_chunk_texts — capped chunk-document reader."""

import pytest

from perspicacite.retrieval.chroma_store import ChromaVectorStore


class _StubColl:
    def __init__(self, docs):
        self._docs = docs
        self.kwargs = None

    def get(self, **kwargs):
        self.kwargs = kwargs
        return {"documents": self._docs}


class _StubClient:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, name):
        return self._coll


@pytest.mark.asyncio
async def test_list_chunk_texts_returns_nonempty_docs():
    coll = _StubColl(["chunk one", "chunk two", "", None])
    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = _StubClient(coll)
    out = await store.list_chunk_texts("kb_x", limit=50)
    assert out == ["chunk one", "chunk two"]
    assert coll.kwargs["limit"] == 50
    assert coll.kwargs["include"] == ["documents"]


@pytest.mark.asyncio
async def test_list_chunk_texts_missing_collection_returns_empty():
    class _Boom:
        def get_collection(self, name):
            raise RuntimeError("no such collection")

    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = _Boom()
    assert await store.list_chunk_texts("missing") == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_chroma_list_chunk_texts.py -q`
Expected: FAIL — `AttributeError: 'ChromaVectorStore' object has no attribute 'list_chunk_texts'`.

- [ ] **Step 3: Implement `list_chunk_texts`**

Add to the `ChromaVectorStore` class in `src/perspicacite/retrieval/chroma_store.py` (after `list_paper_metadata`):

```python
    async def list_chunk_texts(self, collection: str, limit: int = 2000) -> list[str]:
        """Return up to ``limit`` chunk documents from a collection.

        Fallback lexical (BM25) reference corpus for similarity screening when
        a KB has no stored abstracts. Empty docs dropped; missing collection
        / errors -> empty list.
        """
        try:
            coll = self.client.get_collection(name=collection)
            got = coll.get(limit=limit, include=["documents"])
        except Exception as e:
            logger.warning("list_chunk_texts_failed", collection=collection, error=str(e))
            return []
        return [d for d in (got.get("documents") or []) if d]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_chroma_list_chunk_texts.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/retrieval/chroma_store.py tests/unit/test_chroma_list_chunk_texts.py
git commit -m "feat(retrieval): add ChromaVectorStore.list_chunk_texts"
```

---

### Task 3: `get_kb_reference_texts` (abstracts first, chunk-text fallback)

**Files:**
- Create: `src/perspicacite/pipeline/similarity_expansion.py`
- Test: `tests/unit/test_similarity_expansion.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_similarity_expansion.py`:

```python
"""Two-phase similarity-expansion orchestrator + reference assembly."""

from types import SimpleNamespace

import pytest

import perspicacite.pipeline.similarity_expansion as se
from perspicacite.pipeline.similarity_expansion import get_kb_reference_texts


class _StoreWithAbstracts:
    async def list_paper_metadata(self, collection):
        return [
            {"paper_id": "p1", "abstract": "abstract one"},
            {"paper_id": "p2", "abstract": "abstract two"},
            {"paper_id": "p3", "abstract": None},
        ]

    async def list_chunk_texts(self, collection, limit=2000):
        raise AssertionError("must not fall back when abstracts exist")


class _StoreNoAbstracts:
    async def list_paper_metadata(self, collection):
        return [{"paper_id": "p1", "abstract": None}, {"paper_id": "p2"}]

    async def list_chunk_texts(self, collection, limit=2000):
        return ["chunk text a", "chunk text b"]


@pytest.mark.asyncio
async def test_reference_prefers_abstracts():
    out = await get_kb_reference_texts(_StoreWithAbstracts(), "kb")
    assert out == ["abstract one", "abstract two"]


@pytest.mark.asyncio
async def test_reference_falls_back_to_chunk_texts():
    out = await get_kb_reference_texts(_StoreNoAbstracts(), "kb")
    assert out == ["chunk text a", "chunk text b"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_expansion.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'perspicacite.pipeline.similarity_expansion'`.

- [ ] **Step 3: Create the module with `get_kb_reference_texts`**

Create `src/perspicacite/pipeline/similarity_expansion.py`:

```python
"""Two-phase similarity-based KB expansion.

Phase 1 (``score_expansion_candidates``): citation-snowball the KB's seeds,
drop already-ingested + gate-filtered papers, then score the survivors against
the KB by content similarity (Plan 1 scorers); return all scored candidates +
a score histogram + calibration samples for the interactive UI.

Phase 2 (``commit_expansion``): given a human-chosen cutoff, ingest the kept
candidates into the KB.

Reuses the lower-level pieces ``expand_kb_via_citations`` itself calls; the
interactive contract (score now, ingest later) is why this lives in its own
module rather than overloading that one-shot function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb
from perspicacite.pipeline.snowball import _papers_from_hits, snowball_expand

logger = get_logger("perspicacite.pipeline.similarity_expansion")


async def get_kb_reference_texts(
    vector_store: Any, collection: str, cap: int = 2000
) -> list[str]:
    """Reference corpus for set-BM25: the KB's per-paper abstracts when
    available, else (older KBs) up to ``cap`` chunk texts."""
    rows = await vector_store.list_paper_metadata(collection)
    abstracts = [r["abstract"] for r in rows if r.get("abstract")]
    if abstracts:
        return abstracts[:cap]
    return await vector_store.list_chunk_texts(collection, limit=cap)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_expansion.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/similarity_expansion.py tests/unit/test_similarity_expansion.py
git commit -m "feat(pipeline): similarity-expansion reference assembly (abstracts + fallback)"
```

---

### Task 4: `score_expansion_candidates` (phase 1) + histogram

**Files:**
- Modify: `src/perspicacite/pipeline/similarity_expansion.py`
- Test: `tests/unit/test_similarity_expansion.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_similarity_expansion.py`:

```python
from perspicacite.pipeline.similarity_expansion import (
    _score_histogram,
    score_expansion_candidates,
)


def _hit(doi, title, abstract):
    return SimpleNamespace(
        expanded_doi=doi, seed_doi="10.1/seed", direction="forward",
        title=title, year=2024, citation_count=1, abstract=abstract,
        authors=["A. Author"], journal="J", provenance="openalex",
    )


class _Embedder:
    async def embed(self, texts):
        return [[1.0, 0.0] if "relevant" in t.lower() else [0.0, 1.0] for t in texts]


class _OrchStore:
    async def search(self, collection, query_embedding, top_k=5, **kw):
        score = 0.9 if query_embedding[0] > query_embedding[1] else 0.2
        return [SimpleNamespace(score=score) for _ in range(top_k)]

    async def paper_exists(self, collection, doi):
        return doi == "10.1/already"

    async def list_paper_metadata(self, collection):
        return [{"doi": "10.1/seed"}]

    async def list_chunk_texts(self, collection, limit=2000):
        return ["graph neural networks"]


async def _kb_meta(name):
    return SimpleNamespace(collection_name="kb_collection", description="GNNs")


def _app_state():
    return SimpleNamespace(
        session_store=SimpleNamespace(get_kb_metadata=_kb_meta),
        vector_store=_OrchStore(),
        embedding_provider=_Embedder(),
        config=SimpleNamespace(pdf_download=SimpleNamespace(unpaywall_email="me@x.org")),
        llm_client=None,
    )


def test_score_histogram_buckets():
    h = _score_histogram([0.05, 0.15, 0.95, 0.96], bins=10)
    assert sum(b["count"] for b in h) == 4
    assert len(h) == 10
    assert h[0]["count"] == 1 and h[-1]["count"] == 2


@pytest.mark.asyncio
async def test_score_expansion_filters_existing_and_scores(monkeypatch):
    hits = [
        _hit("10.1/relevant", "Relevant", "relevant content"),
        _hit("10.1/offtopic", "Off", "tax accounting"),
        _hit("10.1/already", "Already", "relevant but present"),
    ]

    async def _fake_snowball(**kwargs):
        return hits

    monkeypatch.setattr(se, "snowball_expand", _fake_snowball)

    report = await score_expansion_candidates(
        app_state=_app_state(), kb_name="kb1", direction="forward", method="embedding",
    )
    dois = {c["doi"] for c in report.candidates}
    assert "10.1/already" not in dois  # dropped (already in KB)
    assert {"10.1/relevant", "10.1/offtopic"} <= dois
    rel = next(c for c in report.candidates if c["doi"] == "10.1/relevant")
    off = next(c for c in report.candidates if c["doi"] == "10.1/offtopic")
    assert rel["score"] > off["score"]
    assert report.seed_count == 1
    assert len(report.samples) == 2  # <= n -> all
    assert sum(b["count"] for b in report.histogram) == 2


@pytest.mark.asyncio
async def test_score_expansion_no_seeds(monkeypatch):
    app_state = _app_state()

    async def _no_rows(collection):
        return []

    app_state.vector_store.list_paper_metadata = _no_rows
    report = await score_expansion_candidates(app_state=app_state, kb_name="kb1", method="embedding")
    assert report.candidates == [] and report.seed_count == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_expansion.py -k "histogram or score_expansion" -q`
Expected: FAIL — `ImportError: cannot import name 'score_expansion_candidates'`.

- [ ] **Step 3: Implement the report, histogram, and phase 1**

Append to `src/perspicacite/pipeline/similarity_expansion.py`:

```python
@dataclass
class ExpansionScoreReport:
    kb_name: str
    direction: str = "both"
    method: str = "hybrid"
    seed_count: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    histogram: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)


def _score_histogram(scores: list[float], bins: int = 10) -> list[dict[str, Any]]:
    """Bucket 0-1 scores into ``bins`` equal-width buckets for the UI."""
    buckets = [{"lo": i / bins, "hi": (i + 1) / bins, "count": 0} for i in range(bins)]
    for s in scores:
        idx = min(bins - 1, max(0, int(s * bins)))
        buckets[idx]["count"] += 1
    return buckets


async def score_expansion_candidates(
    *,
    app_state: Any,
    kb_name: str,
    direction: str = "both",
    max_per_seed: int = 10,
    method: str = "hybrid",
    weights: tuple[float, float] = (0.5, 0.5),
    seed_dois: list[str] | None = None,
    flt: Any = None,
) -> ExpansionScoreReport:
    """Phase 1: snowball -> filter -> score against the KB. Returns ALL scored
    candidates (no cutoff) + histogram + calibration samples."""
    from perspicacite.models.kb import chroma_collection_name_for_kb
    from perspicacite.pipeline.search_to_kb import SearchFilter, apply_filters
    from perspicacite.search.screening import (
        screen_papers,
        screen_papers_embedding,
        screen_papers_hybrid,
        select_calibration_samples,
    )

    kb_meta = await app_state.session_store.get_kb_metadata(kb_name)
    if not kb_meta:
        raise ValueError(f"KB '{kb_name}' not found")
    collection = kb_meta.collection_name or chroma_collection_name_for_kb(kb_name)
    flt = flt or SearchFilter()
    pdf_cfg = app_state.config.pdf_download
    mailto = pdf_cfg.unpaywall_email if pdf_cfg else None

    if seed_dois is None:
        rows = await app_state.vector_store.list_paper_metadata(collection)
        seed_dois = [r["doi"] for r in rows if r.get("doi")]

    report = ExpansionScoreReport(
        kb_name=kb_name, direction=direction, method=method, seed_count=len(seed_dois)
    )
    if not seed_dois:
        return report

    hits = await snowball_expand(
        seed_dois=seed_dois, direction=direction, max_per_seed=max_per_seed, mailto=mailto
    )
    papers = _papers_from_hits(hits)

    novel = []
    for p in papers:
        if not await app_state.vector_store.paper_exists(collection, p.doi):
            novel.append(p)
    kept, _reasons = apply_filters(novel, flt)

    items = [
        {"doi": p.doi, "title": p.title or "", "abstract": getattr(p, "abstract", "") or ""}
        for p in kept
    ]
    if not items:
        return report

    if method == "embedding":
        results = await screen_papers_embedding(
            items, collection=collection,
            embedding_provider=app_state.embedding_provider,
            vector_store=app_state.vector_store, threshold=0.0,
        )
    elif method == "bm25":
        ref = await get_kb_reference_texts(app_state.vector_store, collection)
        results = screen_papers(items, reference=ref, method="bm25", threshold=0.0)
    else:  # hybrid (default)
        ref = await get_kb_reference_texts(app_state.vector_store, collection)
        results = await screen_papers_hybrid(
            items, reference_abstracts=ref, collection=collection,
            embedding_provider=app_state.embedding_provider,
            vector_store=app_state.vector_store, weights=weights, threshold=0.0,
        )

    report.candidates = [
        {"doi": r.item.get("doi"), "title": r.item.get("title"),
         "score": float(r.score), "reason": r.reason}
        for r in results
    ]
    report.histogram = _score_histogram([r.score for r in results])
    samples = select_calibration_samples(results, n=4)
    report.samples = [
        {"doi": r.item.get("doi"), "title": r.item.get("title"),
         "abstract": r.item.get("abstract"), "score": float(r.score)}
        for r in samples
    ]
    logger.info(
        "score_expansion_candidates",
        kb=kb_name, method=method, seeds=len(seed_dois), scored=len(results),
    )
    return report
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_expansion.py -q`
Expected: PASS (reference + histogram + phase-1 tests; `commit_expansion` import comes in Task 5).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/similarity_expansion.py tests/unit/test_similarity_expansion.py
git commit -m "feat(pipeline): similarity expansion phase 1 (score candidates vs KB)"
```

---

### Task 5: `commit_expansion` (phase 2)

**Files:**
- Modify: `src/perspicacite/pipeline/similarity_expansion.py`
- Test: `tests/unit/test_similarity_expansion.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_similarity_expansion.py`:

```python
from perspicacite.pipeline.similarity_expansion import commit_expansion


@pytest.mark.asyncio
async def test_commit_ingests_only_above_cutoff(monkeypatch):
    scored = [
        {"doi": "10.1/keep", "title": "K", "score": 0.8},
        {"doi": "10.1/drop", "title": "D", "score": 0.2},
        {"doi": None, "title": "no doi", "score": 0.9},
    ]
    captured: dict = {}

    async def _fake_ingest(app_state, kb_name, dois, **kw):
        captured["dois"] = dois
        return {"added_papers": len(dois), "added_chunks": 7, "failed": [], "pdf_download": {}}

    monkeypatch.setattr(se, "ingest_dois_into_kb", _fake_ingest)
    res = await commit_expansion(
        app_state=SimpleNamespace(), kb_name="kb1", scored=scored, cutoff=0.5
    )
    assert captured["dois"] == ["10.1/keep"]
    assert res["added_papers"] == 1 and res["kept"] == 1


@pytest.mark.asyncio
async def test_commit_nothing_above_cutoff_skips_ingest(monkeypatch):
    called = {"n": 0}

    async def _fake_ingest(app_state, kb_name, dois, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(se, "ingest_dois_into_kb", _fake_ingest)
    res = await commit_expansion(
        app_state=SimpleNamespace(), kb_name="kb1",
        scored=[{"doi": "10.1/x", "score": 0.1}], cutoff=0.5,
    )
    assert called["n"] == 0 and res["kept"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_expansion.py -k commit -q`
Expected: FAIL — `ImportError: cannot import name 'commit_expansion'`.

- [ ] **Step 3: Implement `commit_expansion`**

Append to `src/perspicacite/pipeline/similarity_expansion.py`:

```python
async def commit_expansion(
    *,
    app_state: Any,
    kb_name: str,
    scored: list[dict[str, Any]],
    cutoff: float,
) -> dict[str, Any]:
    """Phase 2: ingest candidates scoring at/above ``cutoff`` into the KB.

    ``scored`` is ``ExpansionScoreReport.candidates`` (each has ``doi`` +
    ``score``). Candidates without a DOI are skipped.
    """
    keep = [
        c["doi"]
        for c in scored
        if c.get("doi") and float(c.get("score", 0.0)) >= cutoff
    ]
    if not keep:
        return {"added_papers": 0, "added_chunks": 0, "failed": [], "kept": 0}
    res = await ingest_dois_into_kb(app_state, kb_name, keep)
    out = dict(res)
    out["kept"] = len(keep)
    logger.info("commit_expansion", kb=kb_name, kept=len(keep), cutoff=cutoff)
    return out
```

- [ ] **Step 4: Run the full file to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_expansion.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/perspicacite/pipeline/similarity_expansion.py tests/unit/test_similarity_expansion.py
git add src/perspicacite/pipeline/similarity_expansion.py tests/unit/test_similarity_expansion.py
git commit -m "feat(pipeline): similarity expansion phase 2 (commit kept candidates)"
```

(Fix ruff findings in the new code only; leave unrelated pre-existing findings.)

---

## Self-Review

**1. Spec coverage (this plan's slice):**
- Abstract persisted in chunk metadata at ingest → Task 1 ✅.
- Reference = abstracts-first with capped-chunk-text fallback → Task 2 (`list_chunk_texts`) + Task 3 (`get_kb_reference_texts`) ✅; existing KBs degrade to chunk texts (Task 3 test) ✅.
- Phase 1 (snowball → filter → score → all-scored + histogram + samples, no cutoff) → Task 4 ✅; scorer dispatch embedding/bm25/hybrid(default) reuses Plan 1 ✅.
- Phase 2 (cutoff → ingest) → Task 5 ✅.
- Edge cases: already-in-KB dropped (Task 4 test); no seeds → empty (Task 4 test); nothing above cutoff → skip ingest (Task 5 test); no-DOI candidate skipped (Task 5 test) ✅.
- `screen_candidates`/`expand_kb_via_citations` untouched (orchestrator calls scorers directly) — matches updated spec ✅.
- Deferred to Plan 3: REST endpoints + SSE + frontend; `cutoff_from_labels` (Plan 1) is invoked by the endpoint to turn sample labels into the `cutoff` `commit_expansion` consumes.

**2. Placeholder scan:** No TBD/TODO; every step has complete code + an exact command with expected output. ✅

**3. Type consistency:** `score_expansion_candidates` → `ExpansionScoreReport`; `.candidates` items `{doi,title,score,reason}` are exactly what `commit_expansion(scored=...)` reads. `get_kb_reference_texts(vector_store, collection)` returns `list[str]` fed to `screen_papers(reference=...)` / `screen_papers_hybrid(reference_abstracts=...)`. `snowball_expand(seed_dois=, direction=, max_per_seed=, mailto=)`, `_papers_from_hits`, `apply_filters(papers, flt)`, `ingest_dois_into_kb(app_state, kb_name, dois)`, `list_paper_metadata`/`list_chunk_texts` signatures all match the codebase. `ChunkMetadata.abstract` (Task 1) is read by `list_paper_metadata` → `get_kb_reference_texts`. ✅
