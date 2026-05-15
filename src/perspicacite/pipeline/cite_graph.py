"""Cite-graph enrichment orchestrator (2026-05-15 spec).

Given a library/tool name (or explicit DOI), resolves to a canonical
paper, walks the OpenAlex forward-citation graph, filters + scores
citing works, and (optionally) ingests survivors via the existing
DOI-ingest path.

This module owns:
- ``CiteHit`` (a citing-paper record)
- ``apply_cite_graph_filters`` (cheap drops)
- ``score_cite_hit`` (final ranking)
- ``enrich_kb_from_cite_graph`` (orchestrator — Task 6)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from perspicacite.config.schema import CiteGraphConfig


@dataclass
class CiteHit:
    """A citing paper record — built from an OpenAlex work."""
    doi: str
    title: str
    year: int
    venue: Optional[str]
    citation_count: int
    is_oa: bool
    abstract: Optional[str] = None
    github_url: Optional[str] = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)


def _normalize_citations(citations: int) -> float:
    if citations <= 0:
        return 0.0
    return min(math.log10(citations + 1) / 3.0, 1.0)


def _recency_score(year: int, *, now_year: int) -> float:
    age = max(now_year - year, 0)
    return 0.5 ** (age / 5.0)


_WORD_RE = re.compile(r"\w+")


def _keyword_match(text: Optional[str], synonyms: list[str]) -> float:
    """Score how well the abstract text matches the tool synonym list.

    Each synonym is matched either as a whole token or — for hyphenated
    names like ``openff-evaluator`` — by checking that all of its word
    parts appear in the text.
    """
    if not text or not synonyms:
        return 0.0
    text_lower = text.lower()
    tokens = {w.lower() for w in _WORD_RE.findall(text)}
    if not tokens:
        return 0.0
    hits = 0
    for syn in synonyms:
        if not syn:
            continue
        sl = syn.lower()
        # Exact word-token match
        if sl in tokens:
            hits += 1
        # Substring match (handles hyphenated names present verbatim)
        elif sl in text_lower:
            hits += 1
        else:
            # All word-parts of the synonym appear as tokens
            parts = _WORD_RE.findall(sl)
            if parts and all(p in tokens for p in parts):
                hits += 1
    return min(hits / max(len(synonyms), 1), 1.0)


def score_cite_hit(
    hit: CiteHit,
    tool_synonyms: list[str],
    config: CiteGraphConfig,
    *,
    now_year: int,
) -> float:
    """Compute hit.score from the four signal components."""
    cit = _normalize_citations(hit.citation_count)
    rec = _recency_score(hit.year, now_year=now_year)
    oa = 1.0 if hit.is_oa else 0.5
    match = _keyword_match(hit.abstract, tool_synonyms)
    s = (
        config.w_citations * cit
        + config.w_recency   * rec
        + config.w_oa        * oa
        + config.w_match     * match
    )
    hit.score = round(s, 4)
    hit.score_breakdown = {
        "citations": round(cit, 4),
        "recency": round(rec, 4),
        "oa": round(oa, 4),
        "match": round(match, 4),
    }
    return hit.score


def apply_cite_graph_filters(
    hits: list[CiteHit],
    *,
    config: CiteGraphConfig,
    existing_dois: set[str],
    now_year: int,
) -> list[CiteHit]:
    """Drop hits that fail cheap rejects (year, citations, denylist, dedup)."""
    min_year = now_year - config.min_year_offset
    deny = {v.lower() for v in config.venue_denylist}
    out: list[CiteHit] = []
    for h in hits:
        if h.year < min_year:
            continue
        if h.citation_count < config.min_citations:
            continue
        if h.doi in existing_dois:
            continue
        if h.venue and h.venue.lower() in deny:
            continue
        out.append(h)
    return out
