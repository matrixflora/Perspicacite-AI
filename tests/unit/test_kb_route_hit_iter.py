from __future__ import annotations

from perspicacite.rag.kb_router import KBRouteHit, _bm25_cache_clear, route_kbs


def test_kb_route_hit_destructures_into_name_and_score():
    hit = KBRouteHit(kb_name="biochem", score=0.75, reason=None, sampled_titles=3)
    name, score = hit
    assert name == "biochem"
    assert score == 0.75


def test_kb_route_hit_iter_yields_only_name_and_score():
    hit = KBRouteHit(kb_name="x", score=0.1)
    assert list(hit) == ["x", 0.1]


def test_route_kbs_results_destructure_in_a_loop():
    _bm25_cache_clear()
    contexts = {
        "biochem": "protein structure prediction alphafold",
        "ml_general": "transformers attention deep learning",
    }
    pairs = [(name, score) for name, score in
             route_kbs(query="alphafold protein", kb_contexts=contexts, top_k=2)]
    assert len(pairs) == 2
    # biochem ranks first for this query.
    assert pairs[0][0] == "biochem"
    assert isinstance(pairs[0][1], float)
