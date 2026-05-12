from perspicacite.retrieval.recency import apply_recency_weighting


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
