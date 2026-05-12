"""Unit tests for perspicacite.retrieval.hybrid module."""


def test_resolve_hybrid_weights_prefers_request():
    from perspicacite.models.rag import RAGRequest
    from perspicacite.retrieval.hybrid import resolve_hybrid_weights

    v, b = resolve_hybrid_weights(
        RAGRequest(query="x", vector_weight=0.8, bm25_weight=0.2), default=(0.5, 0.5)
    )
    assert (round(v, 6), round(b, 6)) == (0.8, 0.2)

    v, b = resolve_hybrid_weights(RAGRequest(query="x"), default=(0.5, 0.5))
    assert (v, b) == (0.5, 0.5)

    # only one side set -> complement
    v, b = resolve_hybrid_weights(RAGRequest(query="x", vector_weight=0.9), default=(0.5, 0.5))
    assert round(v + b, 6) == 1.0 and round(v, 6) == 0.9
