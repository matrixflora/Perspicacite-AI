import pytest

from perspicacite.search.screening import (
    ScreenResult,
    screen_papers,
    screen_papers_llm,
    screen_papers_rerank,
)


def test_bm25_screening_ranks_relevant_higher():
    candidates = [
        {
            "title": "Deep learning for protein folding",
            "abstract": "neural networks predict protein structure from sequence",
        },
        {
            "title": "A history of Renaissance painting",
            "abstract": "oil on canvas in 15th century Florence",
        },
    ]
    refs = ["protein structure prediction with deep neural networks"]
    results = screen_papers(candidates, reference=refs, method="bm25", threshold=0.2)
    assert all(isinstance(r, ScreenResult) for r in results)
    by_title = {r.item.get("title"): r for r in results}
    assert (
        by_title["Deep learning for protein folding"].score
        > by_title["A history of Renaissance painting"].score
    )
    assert by_title["Deep learning for protein folding"].kept is True
    # results are sorted by score descending
    assert results[0].score >= results[-1].score


def test_bm25_screening_threshold_filters():
    candidates = [
        {"title": "alpha beta gamma", "abstract": ""},
        {"title": "completely unrelated words here", "abstract": ""},
    ]
    results = screen_papers(candidates, reference="alpha beta gamma", method="bm25", threshold=0.99)
    # only the near-exact match should be kept at a high threshold
    assert sum(r.kept for r in results) <= 1


def test_bm25_screening_empty_reference():
    results = screen_papers([{"title": "x"}], reference="", method="bm25", threshold=0.1)
    assert len(results) == 1 and results[0].kept is False and results[0].score == 0.0


def test_screen_papers_rejects_llm_method():
    with pytest.raises(ValueError):
        screen_papers([{"title": "x"}], reference="q", method="llm")


@pytest.mark.asyncio
async def test_llm_screening():
    class FakeLLM:
        async def complete(self, messages, **kw):
            # respond with a JSON array of {index, score, reason}
            return (
                '[{"index": 0, "score": 0.9, "reason": "on topic"},'
                ' {"index": 1, "score": 0.1, "reason": "unrelated"}]'
            )

    candidates = [{"title": "A", "abstract": "x"}, {"title": "B", "abstract": "y"}]
    results = await screen_papers_llm(candidates, query="topic query", llm=FakeLLM(), threshold=0.5)
    assert len(results) == 2
    by_title = {r.item["title"]: r for r in results}
    assert (
        by_title["A"].score == 0.9
        and by_title["A"].kept is True
        and by_title["A"].reason == "on topic"
    )
    assert by_title["B"].kept is False
    # sorted by score desc
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_rerank_screening_with_mocked_model(monkeypatch):
    """Tier B: cross-encoder rerank. We mock ``sentence_transformers.
    CrossEncoder`` so the test runs without downloading the model.
    The mock returns deterministic logits — positive for the relevant
    candidate, negative for the off-topic one — and we verify the
    sigmoid normalization gives the expected [0,1] split."""
    class FakeCrossEncoder:
        def __init__(self, *a, **kw):
            pass
        def predict(self, pairs):
            # First pair is the on-topic paper -> high positive logit
            # Second pair is the off-topic paper -> high negative logit
            return [20.0 if i == 0 else -20.0 for i in range(len(pairs))]

    import sys
    import types
    fake_mod = types.ModuleType("sentence_transformers")
    fake_mod.CrossEncoder = FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    candidates = [
        {"title": "On topic paper", "abstract": "matches the query"},
        {"title": "Off topic paper", "abstract": "totally unrelated"},
    ]
    results = await screen_papers_rerank(
        candidates, query="topic query", threshold=0.5,
    )
    by_title = {r.item["title"]: r for r in results}
    # sigmoid(5)  ≈ 0.993, sigmoid(-5) ≈ 0.007
    assert by_title["On topic paper"].score > 0.99
    assert by_title["Off topic paper"].score < 0.01
    assert by_title["On topic paper"].kept is True
    assert by_title["Off topic paper"].kept is False
    # Sorted by score desc
    assert results[0].score >= results[1].score


@pytest.mark.asyncio
async def test_rerank_screening_handles_empty_candidates():
    """Empty input is a no-op — must not load the model."""
    results = await screen_papers_rerank([], query="anything", threshold=0.3)
    assert results == []


@pytest.mark.asyncio
async def test_rerank_screening_raises_clearly_when_model_missing(monkeypatch):
    """If sentence-transformers isn't installed, surface a clear
    ImportError so the caller can fall back to tier A."""
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("not installed in this env")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="sentence-transformers"):
        await screen_papers_rerank(
            [{"title": "x"}], query="y", threshold=0.3,
        )


@pytest.mark.asyncio
async def test_llm_screening_bad_json_degrades():
    class FakeLLM:
        async def complete(self, messages, **kw):
            return "sorry I can't do that"

    results = await screen_papers_llm(
        [{"title": "A"}, {"title": "B"}], query="q", llm=FakeLLM(), threshold=0.5
    )
    assert len(results) == 2 and all(r.score == 0.0 and r.kept is False for r in results)
