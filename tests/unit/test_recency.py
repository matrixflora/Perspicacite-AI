from perspicacite.retrieval.recency import (
    apply_recency_weighting,
    apply_recency_weighting_to_papers,
)


class _C:
    def __init__(self, year, score):
        self.metadata = {"year": year}
        self.score = score


def test_recency_reorders_toward_recent():
    chunks = [_C(2010, 0.80), _C(2024, 0.78)]
    out = apply_recency_weighting(
        list(chunks), recency_weight=0.6, half_life_years=5.0, current_year=2026
    )
    assert out[0].metadata["year"] == 2024  # recent paper now ranks first
    assert out == sorted(out, key=lambda c: c.score, reverse=True)


def test_recency_noop_when_zero():
    chunks = [_C(2010, 0.80), _C(2024, 0.10)]
    before = [(c.metadata["year"], c.score) for c in chunks]
    out = apply_recency_weighting(
        chunks, recency_weight=0.0, half_life_years=5.0, current_year=2026
    )
    assert [(c.metadata["year"], c.score) for c in out] == before


def test_recency_noop_when_none():
    chunks = [_C(2010, 0.80), _C(2024, 0.10)]
    before = [(c.metadata["year"], c.score) for c in chunks]
    out = apply_recency_weighting(
        chunks, recency_weight=None, half_life_years=None, current_year=2026
    )
    assert [(c.metadata["year"], c.score) for c in out] == before


def test_recency_handles_missing_year():
    c1 = _C(2024, 0.5)
    c2 = _C(None, 0.5)  # no year -> neutral factor (1.0), shouldn't crash
    c3 = type("X", (), {"score": 0.5})()  # no .metadata at all
    out = apply_recency_weighting(
        [c1, c2, c3], recency_weight=0.5, half_life_years=8.0, current_year=2026
    )
    assert len(out) == 3  # no crash


def test_recency_request_fields():
    from perspicacite.models.rag import RAGRequest

    assert RAGRequest(query="x").recency_weight is None
    assert RAGRequest(query="x").recency_half_life_years is None
    r = RAGRequest(query="x", recency_weight=0.5, recency_half_life_years=4.0)
    assert r.recency_weight == 0.5 and r.recency_half_life_years == 4.0


# Paper-dict variant tests (Task 3.1)


def test_apply_recency_to_papers_newer_outranks_older() -> None:
    papers = [
        {"doi": "10.1/a", "year": 2024, "paper_score": 0.9},
        {"doi": "10.1/b", "year": 2010, "paper_score": 0.9},
    ]
    out = apply_recency_weighting_to_papers(
        papers,
        recency_weight=1.0,
        half_life_years=8.0,
        current_year=2026,
    )
    assert out[0]["doi"] == "10.1/a"
    assert out[1]["doi"] == "10.1/b"


def test_apply_recency_to_papers_no_op_when_zero() -> None:
    papers = [{"doi": "x", "year": 2010, "paper_score": 0.5}]
    out = apply_recency_weighting_to_papers(papers, recency_weight=0.0)
    assert out == papers


def test_apply_recency_to_papers_no_op_when_none() -> None:
    papers = [{"doi": "x", "year": 2010, "paper_score": 0.5}]
    out = apply_recency_weighting_to_papers(papers, recency_weight=None)
    assert out == papers


def test_apply_recency_to_papers_missing_year_neutral() -> None:
    papers = [
        {"doi": "x", "paper_score": 0.5},  # no year
        {"doi": "y", "year": 2010, "paper_score": 0.5},
    ]
    out = apply_recency_weighting_to_papers(
        papers,
        recency_weight=1.0,
        half_life_years=4.0,
        current_year=2026,
    )
    # 'x' (no year, factor 1.0) should outrank 'y' (year 2010, heavily decayed)
    assert out[0]["doi"] == "x"


def test_apply_recency_uses_score_key_when_no_paper_score() -> None:
    papers = [
        {"doi": "a", "year": 2024, "score": 0.9},
        {"doi": "b", "year": 2010, "score": 0.9},
    ]
    out = apply_recency_weighting_to_papers(
        papers,
        recency_weight=1.0,
        half_life_years=8.0,
        current_year=2026,
    )
    assert out[0]["doi"] == "a"
