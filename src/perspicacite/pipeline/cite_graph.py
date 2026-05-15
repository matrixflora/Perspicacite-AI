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

# Common English stopwords — minimal, just enough to strip filler from
# paper titles. Not a full NLTK list; we want fast and dependency-free.
_TITLE_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "via", "we", "were", "with", "without",
    "using", "use", "uses", "used", "their", "these", "those", "based",
    "new", "novel", "recent", "approach", "method", "methods",
    "paper", "study", "studies", "result", "results",
})


def tool_synonyms_from_seed(
    *,
    tool: Optional[str],
    seed_title: Optional[str],
) -> list[str]:
    """Build a synonyms list for cite-graph ranking.

    Includes the tool name plus content-word tokens from the seed
    paper's title (lowercased, deduped, stopword-filtered, short tokens
    dropped). Order: tool first, then unique title tokens in title order.
    """
    syns: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        t = token.lower().strip()
        if not t or len(t) < 3:
            return
        if t in _TITLE_STOPWORDS:
            return
        if t in seen:
            return
        seen.add(t)
        syns.append(t)

    if tool:
        _add(tool)
    if seed_title:
        for token in _WORD_RE.findall(seed_title):
            _add(token)
    return syns


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


# --- Orchestrator (Task 6) -----------------------------------------

from typing import Optional as _Optional


async def _resolve_and_fetch(
    *, tool: _Optional[str], doi: _Optional[str], kb_config,
) -> tuple[list[dict], _Optional[str]]:
    """Resolve the library to a seed DOI, then fetch OpenAlex citing works.

    Returns a (citing_works, seed_title) tuple. The title — when known —
    is used by the orchestrator to expand tool_synonyms for topic-aware
    scoring.
    """
    import httpx
    from perspicacite.pipeline.library_doi import resolve_library_paper
    from perspicacite.pipeline.snowball import fetch_cited_by_works, _fetch_seed_work

    seed_doi: _Optional[str] = doi
    if seed_doi is None:
        if not tool:
            return ([], None)
        paper = await resolve_library_paper(
            tool,
            bundle=None, github_repo=None,
            config_map=dict(kb_config.library_paper_map),
            readme_text=None,
        )
        if paper is None:
            return ([], None)
        seed_doi = paper.doi

    async with httpx.AsyncClient() as client:
        seed_work = await _fetch_seed_work(client, seed_doi, {})
        if seed_work is None:
            return ([], None)
        seed_title = seed_work.get("title")
        works = await fetch_cited_by_works(
            client, seed_work=seed_work,
            max_results=kb_config.cite_graph.max_papers * 4,
        )
        return (works, seed_title)


def _hit_from_oa_work(work: dict) -> _Optional["CiteHit"]:
    """Project an OpenAlex work dict into a CiteHit. Returns None when
    the work has no DOI."""
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    if not doi:
        doi = (work.get("ids") or {}).get("doi", "").replace("https://doi.org/", "")
    if not doi:
        return None
    title = work.get("title") or ""
    year = int(work.get("publication_year") or 0)
    cit = int(work.get("cited_by_count") or 0)
    oa = bool((work.get("open_access") or {}).get("is_oa"))
    venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
    # Reconstruct abstract from inverted index (best-effort).
    inv = work.get("abstract_inverted_index") or None
    abstract = None
    if isinstance(inv, dict) and inv:
        try:
            positions: list[tuple[int, str]] = []
            for word, idxs in inv.items():
                for i in idxs:
                    positions.append((i, word))
            positions.sort()
            abstract = " ".join(w for _, w in positions)
        except Exception:
            abstract = None
    return CiteHit(
        doi=doi, title=title, year=year, venue=venue,
        citation_count=cit, is_oa=oa, abstract=abstract,
    )


async def enrich_kb_from_cite_graph(
    *,
    tool: _Optional[str] = None,
    doi: _Optional[str] = None,
    kb_config,
    existing_dois: set[str],
    dry_run: bool = False,
    now_year: _Optional[int] = None,
) -> list[CiteHit]:
    """Resolve library/DOI → fetch citing works → filter+score → top-N.

    Returns the ranked CiteHit list (top max_papers). Synonyms for the
    abstract-match signal are expanded with content-word tokens from
    the seed paper's title via :func:`tool_synonyms_from_seed`.

    The ``dry_run`` parameter is reserved for future ingest plumbing —
    currently it has no effect on behaviour.
    """
    import datetime as _dt
    if now_year is None:
        now_year = _dt.datetime.now(_dt.UTC).year

    cfg = kb_config.cite_graph
    works, seed_title = await _resolve_and_fetch(tool=tool, doi=doi, kb_config=kb_config)
    raw_hits: list[CiteHit] = []
    for w in works:
        h = _hit_from_oa_work(w)
        if h is not None:
            raw_hits.append(h)

    filtered = apply_cite_graph_filters(
        raw_hits, config=cfg, existing_dois=existing_dois, now_year=now_year,
    )

    synonyms = tool_synonyms_from_seed(tool=tool, seed_title=seed_title)
    for h in filtered:
        score_cite_hit(h, synonyms, cfg, now_year=now_year)

    filtered.sort(key=lambda h: h.score, reverse=True)
    return filtered[: cfg.max_papers]
