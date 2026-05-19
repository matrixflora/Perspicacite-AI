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


async def test_search_passages_filters_by_min_score():
    retriever = _FakeRetriever(
        results=[
            {"text": "high", "score": 0.9, "paper_id": "a", "metadata": {}, "kb_name": "kb"},
            {"text": "low", "score": 0.1, "paper_id": "b", "metadata": {}, "kb_name": "kb"},
        ],
    )

    out = await search_passages(retriever, text="x", k=5, min_score=0.5)

    assert [m.chunk_text for m in out] == ["high"]


async def test_search_passages_rejects_empty_text():
    with pytest.raises(ValueError, match="empty"):
        await search_passages(_FakeRetriever([]), text="", k=5)


async def test_search_passages_rejects_oversized_text():
    big = "x" * 4001
    with pytest.raises(ValueError, match="4000"):
        await search_passages(_FakeRetriever([]), text=big, k=5)


async def test_search_passages_clamps_k_to_max():
    retriever = _FakeRetriever([])
    await search_passages(retriever, text="x", k=999)
    assert retriever.calls[0][1] == 50  # MAX_K
