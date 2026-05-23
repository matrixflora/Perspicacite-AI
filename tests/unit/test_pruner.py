"""Unit tests for indicium_layer.pruner.build_candidate_pairs."""

from perspicacite.indicium_layer.pruner import build_candidate_pairs


def _claim(cid: str, subject: str, obj: str, paper: str = "p"):
    return {
        "_local_id": cid,
        "context": "ctx",
        "subject": subject,
        "qualifier": "inhibits",
        "relation": "affects",
        "object": obj,
        "_paper_id": paper,
    }


def test_pairs_share_subject_lemma():
    claims = [
        _claim("a", "Compound X", "Enzyme Y"),
        _claim("b", "compound X", "Pathway Z"),
        _claim("c", "Unrelated W", "Cell V"),
    ]
    pairs = build_candidate_pairs(claims, max_pairs_per_claim=20)
    pair_ids = {
        (min(p[0]["_local_id"], p[1]["_local_id"]), max(p[0]["_local_id"], p[1]["_local_id"]))
        for p in pairs
    }
    assert ("a", "b") in pair_ids
    assert ("a", "c") not in pair_ids


def test_pairs_share_object_lemma():
    claims = [
        _claim("a", "X", "shared Y"),
        _claim("b", "Z", "Shared Y"),
    ]
    pairs = build_candidate_pairs(claims, max_pairs_per_claim=20)
    assert len(pairs) == 1


def test_paper_neighborhood_pairs():
    claims = [
        _claim("a", "X", "Y", paper="p1"),
        _claim("b", "U", "V", paper="p1"),
        _claim("c", "M", "N", paper="p2"),
    ]
    pairs = build_candidate_pairs(claims, max_pairs_per_claim=20)
    pair_ids = {
        (min(p[0]["_local_id"], p[1]["_local_id"]), max(p[0]["_local_id"], p[1]["_local_id"]))
        for p in pairs
    }
    assert ("a", "b") in pair_ids
    assert ("a", "c") not in pair_ids


def test_cap_per_claim():
    claims = [_claim("hub", "Hub", "Hub")] + [_claim(f"n{i}", "Hub", f"X{i}") for i in range(50)]
    pairs = build_candidate_pairs(claims, max_pairs_per_claim=3)
    hub_pairs = [p for p in pairs if "hub" in (p[0]["_local_id"], p[1]["_local_id"])]
    assert len(hub_pairs) <= 3


def test_no_self_pairs():
    claims = [_claim("a", "X", "Y")]
    pairs = build_candidate_pairs(claims, max_pairs_per_claim=20)
    assert pairs == []
