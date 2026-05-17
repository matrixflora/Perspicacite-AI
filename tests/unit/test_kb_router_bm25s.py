from __future__ import annotations

from perspicacite.rag.kb_router import _bm25_cache_clear, route_kbs


def _names(result):
    """Unwrap route_kbs result into a flat list of KB names regardless of shape."""
    if not result:
        return []
    first = result[0]
    if isinstance(first, tuple):
        return [n for n, _ in result]
    if isinstance(first, dict):
        return [r["name"] for r in result]
    if hasattr(first, "kb_name"):
        return [r.kb_name for r in result]
    return list(result)


def test_route_kbs_returns_relevant_kbs():
    _bm25_cache_clear()
    kb_contexts = {
        "biochem":    "alphafold protein structure prediction folding",
        "ml_general": "transformer attention language model gpt",
        "math":       "theorem proof lemma category topology",
    }
    chosen = _names(route_kbs(
        query="how does alphafold predict protein structure",
        kb_contexts=kb_contexts,
        top_k=2,
    ))
    assert "biochem" in chosen
    assert "math" not in chosen


def test_route_kbs_cache_reuses_index(monkeypatch):
    _bm25_cache_clear()
    kb_contexts = {
        "a": "alpha beta gamma",
        "b": "delta epsilon zeta",
    }
    calls = {"build": 0}

    import perspicacite.rag.kb_router as kr
    orig_build = kr._build_bm25_index

    def counting_build(corpus_tokens, *, fingerprint):
        calls["build"] += 1
        return orig_build(corpus_tokens, fingerprint=fingerprint)

    monkeypatch.setattr(kr, "_build_bm25_index", counting_build)

    route_kbs(query="alpha", kb_contexts=kb_contexts, top_k=1)
    route_kbs(query="delta", kb_contexts=kb_contexts, top_k=1)
    assert calls["build"] == 1, "same corpus must hit the cache"

    route_kbs(query="alpha", kb_contexts={"x": "alpha"}, top_k=1)
    assert calls["build"] == 2, "different corpus must rebuild"
