# Similarity Expansion — Plan 1 of 3: Screening Core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the new screening building blocks — a set-based **embedding** scorer, a **hybrid** (BM25+embedding) blend, and the **calibrate-by-example** threshold helpers — to `search/screening.py`, fully unit-tested in isolation.

**Architecture:** Pure functions added beside the existing `screen_papers` / `screen_papers_rerank` / `screen_papers_llm`. The embedding scorer takes the KB's vector `collection` + an injected `embedding_provider` and `vector_store` (the same DI style as `screen_papers_llm(... llm)`); hybrid blends it with the existing set-BM25; the threshold helpers operate on any scorer's `ScreenResult` list. No wiring, no endpoints, no frontend — those are Plans 2 and 3.

**Tech Stack:** Python 3.12, `rank_bm25` (already used), `pytest` + `pytest-asyncio` (already used). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-21-similarity-expansion-design.md`

> **Roadmap:** Plan 1 (this) = screening core. Plan 2 = `screen_candidates` reference-mode + `pipeline/similarity_expansion.py` orchestrator + two REST endpoints. Plan 3 = frontend page. Each plan produces working, tested software on its own.

---

## File Structure

- **Modify:** `src/perspicacite/search/screening.py` — append four functions after `screen_papers_llm`. Existing functions are untouched.
- **Create:** `tests/unit/test_similarity_screening.py` — unit tests with a stub embedding provider + stub vector store (no model load, no network).

Reused, unchanged: `ScreenResult` (dataclass: `item: dict`, `score: float`, `kept: bool`, `reason: str`), `_candidate_text(c)` (returns `"{title} {abstract}"`), `screen_papers(candidates, reference, method, threshold)` (set-BM25, returns `list[ScreenResult]` sorted desc). `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`. `ChromaVectorStore.search(collection, query_embedding, top_k) -> list[RetrievedChunk]` where each hit has a `.score` float in (0,1].

> **WSL note:** `uv run pytest` has a slow (~minutes) import cost on this machine. Each "run the test" step is still correct; just expect the wait.

---

### Task 1: Set-based embedding scorer

**Files:**
- Modify: `src/perspicacite/search/screening.py` (append after `screen_papers_llm`)
- Test: `tests/unit/test_similarity_screening.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_similarity_screening.py`:

```python
"""Unit tests for the similarity-expansion screening core.

Stubbed embedding provider + vector store — no model load, no network.
"""

from types import SimpleNamespace

import pytest

from perspicacite.search.screening import (
    ScreenResult,
    screen_papers_embedding,
)


class _StubEmbedder:
    """Embeds 'relevant' text near [1,0], everything else near [0,1]."""

    async def embed(self, texts):
        return [[1.0, 0.0] if "relevant" in t.lower() else [0.0, 1.0] for t in texts]


class _StubStore:
    """Returns top_k hits whose score is high when the query vector leans [1,0]."""

    async def search(self, collection, query_embedding, top_k=5, **kwargs):
        high = query_embedding[0] > query_embedding[1]
        score = 0.9 if high else 0.2
        return [SimpleNamespace(score=score) for _ in range(top_k)]


@pytest.mark.asyncio
async def test_embedding_scores_relevant_above_offtopic():
    cands = [
        {"title": "A", "abstract": "relevant content here"},
        {"title": "B", "abstract": "completely unrelated material"},
        {"title": "C", "abstract": ""},  # no abstract
    ]
    out = await screen_papers_embedding(
        cands,
        collection="kb_x",
        embedding_provider=_StubEmbedder(),
        vector_store=_StubStore(),
        top_k=3,
        threshold=0.5,
    )
    by_title = {r.item["title"]: r for r in out}
    assert by_title["A"].score > by_title["B"].score
    assert by_title["A"].kept is True
    assert by_title["B"].kept is False
    assert by_title["C"].score == 0.0
    assert by_title["C"].reason == "no abstract"
    # Result list is sorted by score descending.
    assert [r.score for r in out] == sorted((r.score for r in out), reverse=True)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_screening.py -q`
Expected: FAIL — `ImportError: cannot import name 'screen_papers_embedding'`.

- [ ] **Step 3: Implement `screen_papers_embedding`**

Append to `src/perspicacite/search/screening.py` (after `screen_papers_llm`):

```python
async def screen_papers_embedding(
    candidates: "Sequence[dict]",
    *,
    collection: str,
    embedding_provider: Any,
    vector_store: Any,
    top_k: int = 5,
    threshold: float = 0.3,
) -> list[ScreenResult]:
    """Score candidates by embedding similarity to a KB's vector collection.

    Each candidate's title+abstract is embedded with ``embedding_provider``
    (the same provider/model that built the KB, so the vectors share a
    space) and compared to the KB's stored vectors via
    ``vector_store.search``. The candidate's score is the mean of its top-k
    cosine hit scores (already normalised to (0,1] by the store). A
    candidate with no abstract scores 0.0. Errors degrade to 0.0 with a
    reason rather than raising.
    """
    candidates_list = list(candidates)
    if not candidates_list:
        return []

    results: list[ScreenResult] = []
    for c in candidates_list:
        if not (c.get("abstract") or "").strip():
            results.append(
                ScreenResult(item=c, score=0.0, kept=False, reason="no abstract")
            )
            continue
        try:
            embedding = (await embedding_provider.embed([_candidate_text(c)]))[0]
            hits = await vector_store.search(collection, embedding, top_k=top_k)
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash the screen
            results.append(
                ScreenResult(item=c, score=0.0, kept=False, reason=f"embedding_error: {exc}")
            )
            continue
        if not hits:
            results.append(
                ScreenResult(item=c, score=0.0, kept=False, reason="no_kb_hits")
            )
            continue
        top = [float(h.score) for h in hits[:top_k]]
        mean_score = sum(top) / len(top)
        results.append(
            ScreenResult(
                item=c,
                score=mean_score,
                kept=mean_score >= threshold,
                reason=f"embedding_top{len(top)}_mean={mean_score:.3f}",
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    logger.info(
        "screen_papers_embedding",
        n=len(candidates_list),
        kept=sum(r.kept for r in results),
        threshold=threshold,
    )
    return results
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_screening.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/screening.py tests/unit/test_similarity_screening.py
git commit -m "feat(screening): add set-based embedding scorer"
```

---

### Task 2: Hybrid (BM25 + embedding) blend

**Files:**
- Modify: `src/perspicacite/search/screening.py`
- Test: `tests/unit/test_similarity_screening.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_similarity_screening.py`:

```python
from perspicacite.search.screening import screen_papers_hybrid


@pytest.mark.asyncio
async def test_hybrid_blends_bm25_and_embedding():
    # Candidate A matches the reference text lexically; candidate B matches
    # semantically via the stub embedder ("relevant"); C matches neither.
    cands = [
        {"title": "graph neural networks", "abstract": "graph neural networks for molecules"},
        {"title": "B", "abstract": "relevant but lexically different wording"},
        {"title": "C", "abstract": "tax law and accounting"},
    ]
    reference_abstracts = ["graph neural networks applied to molecular property prediction"]
    out = await screen_papers_hybrid(
        cands,
        reference_abstracts=reference_abstracts,
        collection="kb_x",
        embedding_provider=_StubEmbedder(),
        vector_store=_StubStore(),
        weights=(0.5, 0.5),
        top_k=3,
        threshold=0.0,
    )
    by_title = {r.item["title"]: r for r in out}
    # A scores on BM25 (lexical overlap); B scores on embedding ("relevant").
    assert by_title["graph neural networks"].score > by_title["C"].score
    assert by_title["B"].score > by_title["C"].score
    # reason records both component scores.
    assert "bm25=" in by_title["B"].reason and "emb=" in by_title["B"].reason
    # Each blended score equals the weighted sum of its components (parse reason).
    for r in out:
        parts = dict(p.split("=") for p in r.reason.replace("hybrid ", "").split())
        expected = 0.5 * float(parts["bm25"]) + 0.5 * float(parts["emb"])
        assert abs(r.score - expected) < 1e-6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_screening.py::test_hybrid_blends_bm25_and_embedding -q`
Expected: FAIL — `ImportError: cannot import name 'screen_papers_hybrid'`.

- [ ] **Step 3: Implement `screen_papers_hybrid`**

Append to `src/perspicacite/search/screening.py`:

```python
async def screen_papers_hybrid(
    candidates: "Sequence[dict]",
    *,
    reference_abstracts: "Sequence[str]",
    collection: str,
    embedding_provider: Any,
    vector_store: Any,
    weights: tuple[float, float] = (0.5, 0.5),
    top_k: int = 5,
    threshold: float = 0.3,
) -> list[ScreenResult]:
    """Blend set-BM25 (vs ``reference_abstracts``) with set-embedding (vs the
    KB ``collection``). Both component scores are already in [0,1], so the
    final score is ``w_bm25 * bm25 + w_emb * emb``. Realignment is by object
    identity — the same candidate dicts flow through both scorers.
    """
    candidates_list = list(candidates)
    if not candidates_list:
        return []

    w_bm25, w_emb = weights
    bm25_results = screen_papers(
        candidates_list,
        reference=list(reference_abstracts),
        method="bm25",
        threshold=0.0,
    )
    emb_results = await screen_papers_embedding(
        candidates_list,
        collection=collection,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        top_k=top_k,
        threshold=0.0,
    )
    bm25_by_id = {id(r.item): r.score for r in bm25_results}
    emb_by_id = {id(r.item): r.score for r in emb_results}

    results: list[ScreenResult] = []
    for c in candidates_list:
        b = bm25_by_id.get(id(c), 0.0)
        e = emb_by_id.get(id(c), 0.0)
        score = w_bm25 * b + w_emb * e
        results.append(
            ScreenResult(
                item=c,
                score=score,
                kept=score >= threshold,
                reason=f"hybrid bm25={b:.3f} emb={e:.3f}",
            )
        )

    results.sort(key=lambda r: r.score, reverse=True)
    logger.info(
        "screen_papers_hybrid",
        n=len(candidates_list),
        kept=sum(r.kept for r in results),
        threshold=threshold,
        weights=list(weights),
    )
    return results
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_screening.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/screening.py tests/unit/test_similarity_screening.py
git commit -m "feat(screening): add hybrid bm25+embedding set scorer"
```

---

### Task 3: Calibration sample selection

**Files:**
- Modify: `src/perspicacite/search/screening.py`
- Test: `tests/unit/test_similarity_screening.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_similarity_screening.py`:

```python
from perspicacite.search.screening import select_calibration_samples


def _r(score, i=0):
    return ScreenResult(item={"i": i}, score=score, kept=False)


def test_select_samples_spans_distribution_and_dedups():
    results = [_r(i / 10, i) for i in range(11)]  # scores 0.0 .. 1.0
    picked = select_calibration_samples(results, n=4)
    assert len(picked) == 4
    assert len({id(r) for r in picked}) == 4  # no duplicates
    ps = sorted(r.score for r in picked)
    assert ps[0] < 0.4 and ps[-1] > 0.6  # genuinely spans low..high


def test_select_samples_small_pool_returns_all_sorted():
    results = [_r(0.1, 1), _r(0.9, 2), _r(0.5, 3)]
    picked = select_calibration_samples(results, n=4)
    assert [r.score for r in picked] == [0.9, 0.5, 0.1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_screening.py -k select_samples -q`
Expected: FAIL — `ImportError: cannot import name 'select_calibration_samples'`.

- [ ] **Step 3: Implement `select_calibration_samples`**

Append to `src/perspicacite/search/screening.py`:

```python
def select_calibration_samples(
    results: "Sequence[ScreenResult]", n: int = 4
) -> list[ScreenResult]:
    """Pick ``n`` samples spanning the score distribution, for human labeling.

    Targets ``n`` evenly-spaced points across the observed score range
    (high → low) and picks the nearest not-yet-chosen result to each. Returns
    all results (sorted descending) when there are <= ``n`` of them.
    """
    items = sorted(results, key=lambda r: r.score, reverse=True)
    if len(items) <= n:
        return items

    lo, hi = items[-1].score, items[0].score
    if hi == lo:
        step = len(items) / n
        return [items[min(len(items) - 1, int(i * step))] for i in range(n)]

    # Evenly spaced fractions, high to low: for n=4 -> 0.875, 0.625, 0.375, 0.125.
    fractions = [1.0 - (i + 0.5) / n for i in range(n)]
    picked: list[ScreenResult] = []
    seen: set[int] = set()
    for f in fractions:
        target = lo + f * (hi - lo)
        best = min(
            (r for r in items if id(r) not in seen),
            key=lambda r: abs(r.score - target),
            default=None,
        )
        if best is not None:
            seen.add(id(best))
            picked.append(best)
    return picked
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_screening.py -k select_samples -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/screening.py tests/unit/test_similarity_screening.py
git commit -m "feat(screening): add calibration sample selection"
```

---

### Task 4: Cutoff from labels

**Files:**
- Modify: `src/perspicacite/search/screening.py`
- Test: `tests/unit/test_similarity_screening.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_similarity_screening.py`:

```python
from perspicacite.search.screening import cutoff_from_labels


def test_cutoff_clean_monotonic():
    labels = [(_r(0.9), True), (_r(0.7), True), (_r(0.4), False), (_r(0.2), False)]
    cut = cutoff_from_labels(labels)
    # Keeps both relevant (>=0.7), drops both not (<0.7): cutoff in (0.4, 0.7].
    assert 0.4 < cut <= 0.7


def test_cutoff_non_monotonic_returns_best_fit():
    labels = [(_r(0.9), True), (_r(0.6), False), (_r(0.5), True), (_r(0.2), False)]
    cut = cutoff_from_labels(labels)
    assert 0.0 <= cut <= 1.0  # always a usable cutoff, no crash


def test_cutoff_empty_keeps_everything():
    assert cutoff_from_labels([]) == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_similarity_screening.py -k cutoff -q`
Expected: FAIL — `ImportError: cannot import name 'cutoff_from_labels'`.

- [ ] **Step 3: Implement `cutoff_from_labels`**

Append to `src/perspicacite/search/screening.py`:

```python
def cutoff_from_labels(
    labeled: "Sequence[tuple[ScreenResult, bool]]",
) -> float:
    """Return the score cutoff that best separates relevant (True) samples
    from not-relevant (False) ones.

    Tries every boundary (each sample score, plus just below the min and just
    above the max) and returns the one minimising misclassified samples — a
    'relevant' that falls below the cutoff, or a 'not-relevant' kept at/above
    it. Ties break toward the HIGHER cutoff (more conservative — keep fewer).
    Empty input returns 0.0 (keep everything).
    """
    labels = list(labeled)
    if not labels:
        return 0.0

    distinct = sorted({r.score for r, _ in labels})
    eps = 1e-6
    candidates = [distinct[0] - eps, *distinct, distinct[-1] + eps]

    best_cut = candidates[0]
    best_err: int | None = None
    for cut in candidates:  # ascending
        err = 0
        for r, is_relevant in labels:
            kept = r.score >= cut
            if kept != is_relevant:
                err += 1
        if best_err is None or err < best_err or (err == best_err and cut > best_cut):
            best_err = err
            best_cut = cut
    return float(max(0.0, best_cut))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_similarity_screening.py -q`
Expected: PASS (all tests in the file pass).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/perspicacite/search/screening.py tests/unit/test_similarity_screening.py
git add src/perspicacite/search/screening.py tests/unit/test_similarity_screening.py
git commit -m "feat(screening): add calibrate-by-example cutoff helper"
```

(If `ruff check` flags anything in the appended code, fix it before committing; do not touch pre-existing unrelated findings elsewhere in the file.)

---

## Self-Review

**1. Spec coverage (this plan's slice):**
- Scorer axis — `embedding` (set) → Task 1 ✅; `hybrid` (default blend) → Task 2 ✅; `bm25` (set) reuses existing `screen_papers` → no task needed ✅.
- Threshold axis — `select_calibration_samples` → Task 3 ✅; `cutoff_from_labels` (incl. non-monotonic best-fit) → Task 4 ✅.
- Edge cases from spec: no-abstract → 0.0/"no abstract" (Task 1) ✅; embedding/vector error → degrade to 0.0 + reason (Task 1) ✅; non-monotonic labels → best-fit (Task 4) ✅; empty inputs handled in every function ✅.
- Out of this plan (Plans 2/3): reference assembly from the KB, `screen_candidates` dispatch, orchestrator, endpoints, frontend. Reference is *injected* here (collection + abstract list), so the core is testable without them.

**2. Placeholder scan:** No TBD/TODO; every step has complete code and an exact command with expected output. ✅

**3. Type consistency:** All four functions return `list[ScreenResult]` or `float`/`list[ScreenResult]` as used in tests. `screen_papers_embedding` is awaited by `screen_papers_hybrid` with matching kwargs (`collection`, `embedding_provider`, `vector_store`, `top_k`). `screen_papers` is called with `reference=<list>, method="bm25", threshold=0.0` — matching its real signature (`reference: str | Sequence[str]`). `ScreenResult(item, score, kept, reason)` fields match the dataclass. `_candidate_text` and `logger` are module-level in `screening.py`. ✅
