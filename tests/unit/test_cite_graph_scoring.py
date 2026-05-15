from __future__ import annotations

from perspicacite.config.schema import CiteGraphConfig
from perspicacite.pipeline.cite_graph import (
    CiteHit,
    apply_cite_graph_filters,
    score_cite_hit,
)


def _hit(**kwargs):
    base = dict(
        doi="10.0/x", title="t", year=2022, venue="J",
        citation_count=10, is_oa=True, abstract="example tool usage",
        github_url=None,
    )
    base.update(kwargs)
    return CiteHit(**base)


def test_score_in_zero_to_one_range():
    hit = _hit(citation_count=100, year=2024)
    cfg = CiteGraphConfig()
    s = score_cite_hit(hit, tool_synonyms=["example", "tool"], config=cfg, now_year=2026)
    assert 0.0 <= s <= 1.0


def test_score_monotonic_in_citation_count():
    cfg = CiteGraphConfig()
    a = score_cite_hit(_hit(citation_count=5), ["tool"], cfg, now_year=2026)
    b = score_cite_hit(_hit(citation_count=500), ["tool"], cfg, now_year=2026)
    assert b > a


def test_score_recency_boost():
    cfg = CiteGraphConfig()
    old = score_cite_hit(_hit(year=2018), ["tool"], cfg, now_year=2026)
    new = score_cite_hit(_hit(year=2025), ["tool"], cfg, now_year=2026)
    assert new > old


def test_score_oa_higher_than_non_oa():
    cfg = CiteGraphConfig()
    closed = score_cite_hit(_hit(is_oa=False), ["tool"], cfg, now_year=2026)
    oa = score_cite_hit(_hit(is_oa=True), ["tool"], cfg, now_year=2026)
    assert oa > closed


def test_score_match_when_abstract_mentions_synonym():
    cfg = CiteGraphConfig()
    no_match = score_cite_hit(_hit(abstract="unrelated content"), ["openff-evaluator"], cfg, now_year=2026)
    matched = score_cite_hit(_hit(abstract="we ran openff-evaluator on this dataset"), ["openff-evaluator"], cfg, now_year=2026)
    assert matched > no_match


def test_filter_drops_by_min_year():
    cfg = CiteGraphConfig(min_year_offset=5)
    hits = [_hit(year=2015), _hit(year=2024)]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois=set(), now_year=2026)
    assert [h.year for h in out] == [2024]


def test_filter_drops_by_min_citations():
    cfg = CiteGraphConfig(min_citations=5)
    hits = [_hit(citation_count=2), _hit(citation_count=10)]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois=set(), now_year=2026)
    assert [h.citation_count for h in out] == [10]


def test_filter_drops_duplicates_in_kb():
    cfg = CiteGraphConfig()
    hits = [_hit(doi="10.0/a"), _hit(doi="10.0/b")]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois={"10.0/a"}, now_year=2026)
    assert [h.doi for h in out] == ["10.0/b"]


def test_filter_respects_venue_denylist():
    cfg = CiteGraphConfig(venue_denylist=["Predatory J"])
    hits = [_hit(venue="Predatory J"), _hit(venue="Nature")]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois=set(), now_year=2026)
    assert [h.venue for h in out] == ["Nature"]
