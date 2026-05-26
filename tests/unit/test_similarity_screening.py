"""Unit tests for the similarity-expansion screening core.

Stubbed embedding provider + vector store — no model load, no network.
"""

from types import SimpleNamespace

import pytest

from perspicacite.search.screening import (
    ScreenResult,
    cutoff_from_labels,
    screen_papers_embedding,
    screen_papers_hybrid,
    select_calibration_samples,
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


def _r(score, i=0):
    return ScreenResult(item={"i": i}, score=score, kept=False)


# ---- Task 1: embedding scorer ----


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
    assert [r.score for r in out] == sorted((r.score for r in out), reverse=True)


# ---- Task 2: hybrid blend ----


@pytest.mark.asyncio
async def test_hybrid_blends_bm25_and_embedding():
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
    assert by_title["graph neural networks"].score > by_title["C"].score
    assert by_title["B"].score > by_title["C"].score
    assert "bm25=" in by_title["B"].reason and "emb=" in by_title["B"].reason
    for r in out:
        parts = dict(p.split("=") for p in r.reason.replace("hybrid ", "").split())
        expected = 0.5 * float(parts["bm25"]) + 0.5 * float(parts["emb"])
        # reason rounds each component to 3 decimals, so allow that slack.
        assert abs(r.score - expected) < 2e-3


# ---- Task 3: calibration sample selection ----


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


# ---- Task 4: cutoff from labels ----


def test_cutoff_clean_monotonic():
    labels = [(_r(0.9), True), (_r(0.7), True), (_r(0.4), False), (_r(0.2), False)]
    cut = cutoff_from_labels(labels)
    assert 0.4 < cut <= 0.7


def test_cutoff_non_monotonic_returns_best_fit():
    labels = [(_r(0.9), True), (_r(0.6), False), (_r(0.5), True), (_r(0.2), False)]
    cut = cutoff_from_labels(labels)
    assert 0.0 <= cut <= 1.0


def test_cutoff_empty_keeps_everything():
    assert cutoff_from_labels([]) == 0.0
