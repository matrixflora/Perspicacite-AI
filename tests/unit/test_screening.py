import pytest

from perspicacite.search.screening import ScreenResult, screen_papers, screen_papers_llm


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
async def test_llm_screening_bad_json_degrades():
    class FakeLLM:
        async def complete(self, messages, **kw):
            return "sorry I can't do that"

    results = await screen_papers_llm(
        [{"title": "A"}, {"title": "B"}], query="q", llm=FakeLLM(), threshold=0.5
    )
    assert len(results) == 2 and all(r.score == 0.0 and r.kept is False for r in results)
