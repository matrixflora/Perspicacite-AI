# tests/unit/test_cite_graph_topic_aware.py
"""Topic-aware cite-graph re-ranking (P0 follow-up to the 2026-05-15 audit).

The naive scorer only had ``[tool_name]`` in tool_synonyms, so any
highly-cited paper that cited the seed would float to the top
regardless of topic. The fix expands synonyms with title tokens so
the abstract-match signal carries domain semantics."""
from __future__ import annotations

from perspicacite.config.schema import CiteGraphConfig
from perspicacite.pipeline.cite_graph import (
    CiteHit,
    score_cite_hit,
    tool_synonyms_from_seed,
)


def _hit(**kwargs):
    base = dict(
        doi="10.0/x", title="t", year=2024, venue="J",
        citation_count=10, is_oa=True, abstract="",
        github_url=None,
    )
    base.update(kwargs)
    return CiteHit(**base)


def test_tool_synonyms_from_seed_extracts_content_words():
    syns = tool_synonyms_from_seed(
        tool="alphafold",
        seed_title="Highly accurate protein structure prediction with AlphaFold",
    )
    syn_set = {s.lower() for s in syns}
    assert "alphafold" in syn_set
    # Content words from the title (post-stopword-filter):
    assert "protein" in syn_set
    assert "structure" in syn_set
    assert "prediction" in syn_set
    # Generic stopwords removed:
    assert "with" not in syn_set
    assert "the" not in syn_set


def test_tool_synonyms_from_seed_includes_only_tool_when_no_title():
    syns = tool_synonyms_from_seed(tool="alphafold", seed_title=None)
    assert syns == ["alphafold"]


def test_tool_synonyms_lowercased_deduped():
    syns = tool_synonyms_from_seed(
        tool="AlphaFold",
        seed_title="AlphaFold AlphaFold protein protein",
    )
    syn_set = set(syns)
    # No duplicates after dedup; all lowercased.
    assert len(syn_set) == len(syns)
    for s in syns:
        assert s == s.lower()


def test_topic_match_lifts_relevant_paper_over_unrelated_one():
    """Two papers: same citation_count and year, one mentions 'protein
    structure' in abstract, the other mentions 'theorem proving'.
    With expanded synonyms from a protein-structure seed title, the
    relevant paper must score higher."""
    cfg = CiteGraphConfig()
    synonyms = tool_synonyms_from_seed(
        tool="alphafold",
        seed_title="Highly accurate protein structure prediction with AlphaFold",
    )
    relevant = _hit(
        doi="10.0/rel", citation_count=1000, year=2023,
        abstract="We use accurate protein structure prediction to model the docking interface.",
    )
    unrelated = _hit(
        doi="10.0/unr", citation_count=1000, year=2023,
        abstract="A new theorem proving system based on Mizar.",
    )
    s_rel = score_cite_hit(relevant, synonyms, cfg, now_year=2026)
    s_unr = score_cite_hit(unrelated, synonyms, cfg, now_year=2026)
    assert s_rel > s_unr, (
        f"expected relevant hit to score higher: rel={s_rel:.3f}, unr={s_unr:.3f}"
    )


def test_score_breakdown_match_reflects_synonym_overlap():
    """When the abstract shares many tokens with the synonyms list,
    the match component should be > 0 and observable in the breakdown."""
    cfg = CiteGraphConfig()
    synonyms = ["alphafold", "protein", "structure", "prediction"]
    hit = _hit(abstract="protein structure prediction is hard")
    score_cite_hit(hit, synonyms, cfg, now_year=2026)
    assert hit.score_breakdown["match"] > 0.5
