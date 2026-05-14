"""Snowball / citation-graph KB expansion.

Forward + backward citation traversal over OpenAlex, used to grow a KB
from a seed paper set without going back to keyword search. Two
common patterns:

- **Forward snowball** (``direction="forward"``) — find papers that
  cite the seeds. Surfaces newer follow-up work that built on a
  classic. Good for "what's the state of the art descended from
  this 2015 method paper?"
- **Backward snowball** (``direction="backward"``) — fetch the
  papers the seeds cite. Surfaces the intellectual lineage. Good for
  "this review cites a lot — pull in everything it depends on."
- ``direction="both"`` — union of the two.

We use OpenAlex directly (not the SciLEx-wrapped
``_get_citation_network`` in agentic/orchestrator.py) because:

1. OpenAlex is always available; SciLEx is an optional extra.
2. OpenAlex returns referenced_works inline + supports batch
   ``filter=openalex:`` queries for up to 100 IDs at once, so a
   backward snowball costs O(seeds) + O(refs/100) requests instead
   of O(seeds × refs).
3. The discovery cache already uses OpenAlex (data/papers/<doi>_discovery.json)
   so seeds we've ingested before don't re-hit the API.

The expansion output is a flat list of :class:`ExpansionHit`
(seed_doi → expanded_doi) suitable for piping through
``search_to_kb.apply_filters`` + ``ingest_dois_into_kb`` or just
``screen_candidates`` for relevance grading before ingestion.

This module owns no state; callers pass ``app_state`` so we share
the existing vector store + session store + LLM client + PDF cache.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.pipeline.snowball")

OPENALEX_BASE = "https://api.openalex.org"
# OpenAlex enforces a politeness rate cap — they ask you to send a
# mailto so they can throttle gracefully instead of 429ing. We grab
# pdf_download.unpaywall_email when available; falls back to a generic
# UA.
DEFAULT_PER_SEED_CAP = 25


@dataclass
class ExpansionHit:
    seed_doi: str
    expanded_doi: str
    direction: str  # "forward" | "backward"
    title: str | None = None
    year: int | None = None
    citation_count: int | None = None
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    journal: str | None = None


@dataclass
class SnowballReport:
    seed_dois: list[str]
    direction: str
    raw_hits: int = 0
    unique_dois: int = 0
    dropped_existing: int = 0
    dropped_filtered: int = 0
    dropped_screened: int = 0
    added_papers: int = 0
    added_chunks: int = 0
    pdf_download: dict[str, int] = field(default_factory=dict)
    ingested_dois: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _oa_headers(mailto: str | None) -> dict[str, str]:
    """OpenAlex 'polite pool' UA — gets you better rate limits."""
    if mailto:
        return {"User-Agent": f"Perspicacite/2.0 (mailto:{mailto})"}
    return {"User-Agent": "Perspicacite/2.0"}


def _reconstruct_abstract(inv_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as inverted indices (word → positions).
    Walk them in position order to rebuild the prose."""
    if not inv_index:
        return None
    positioned: list[tuple[int, str]] = []
    for word, positions in inv_index.items():
        for p in positions:
            positioned.append((p, word))
    positioned.sort()
    return " ".join(w for _, w in positioned) or None


def _paper_from_oa_work(work: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Pull the DOI + minimum metadata fields we use downstream.

    Returns ``(doi, fields)`` with ``doi`` lower-cased & DOI-prefix-stripped,
    or ``(None, {})`` when the work has no DOI (OpenAlex IDs without DOIs
    are unfetchable through the PDF pipeline).
    """
    raw_doi = work.get("doi") or ""
    if not raw_doi:
        return None, {}
    doi = raw_doi.lower().replace("https://doi.org/", "").strip()
    authors_objs = work.get("authorships") or []
    authors = [
        (a.get("author") or {}).get("display_name") or ""
        for a in authors_objs
    ]
    authors = [a for a in authors if a]
    primary_loc = work.get("primary_location") or {}
    source = (primary_loc.get("source") or {}) if primary_loc else {}
    fields = {
        "title": work.get("title") or work.get("display_name"),
        "year": work.get("publication_year"),
        "citation_count": work.get("cited_by_count"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "authors": authors,
        "journal": source.get("display_name"),
    }
    return doi, fields


async def _fetch_seed_work(
    client: httpx.AsyncClient, doi: str, headers: dict[str, str],
) -> dict[str, Any] | None:
    """One OpenAlex work record for a single DOI, or None on miss."""
    url = f"{OPENALEX_BASE}/works/doi:{doi}"
    try:
        r = await client.get(url, headers=headers, timeout=20.0)
        if r.status_code != 200:
            logger.info("snowball_oa_seed_miss", doi=doi, status=r.status_code)
            return None
        return r.json()
    except httpx.HTTPError as exc:
        logger.warning("snowball_oa_seed_error", doi=doi, error=str(exc))
        return None


async def _batch_get_works(
    client: httpx.AsyncClient,
    oa_ids: list[str],
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Resolve a batch of OpenAlex work IDs to full records. OpenAlex
    supports up to 100 IDs per ``filter=openalex:id1|id2|…`` query."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(oa_ids), 100):
        chunk = oa_ids[i:i + 100]
        # Normalize — accept either bare IDs (W123…) or URL form.
        normalized = []
        for x in chunk:
            if isinstance(x, str):
                normalized.append(x.rsplit("/", 1)[-1])
        if not normalized:
            continue
        url = f"{OPENALEX_BASE}/works"
        params = {
            "filter": "openalex:" + "|".join(normalized),
            "per-page": "100",
        }
        try:
            r = await client.get(url, params=params, headers=headers, timeout=30.0)
            if r.status_code != 200:
                logger.warning(
                    "snowball_oa_batch_failed",
                    status=r.status_code, batch=len(normalized),
                )
                continue
            results = (r.json() or {}).get("results") or []
            out.extend(results)
        except httpx.HTTPError as exc:
            logger.warning("snowball_oa_batch_error", error=str(exc))
    return out


async def _fetch_forward_citations(
    client: httpx.AsyncClient,
    seed_work: dict[str, Any],
    max_results: int,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Papers that cite the seed. OpenAlex returns a ``cited_by_api_url``
    on each work — we hit it, optionally paginated."""
    url = seed_work.get("cited_by_api_url")
    if not url:
        return []
    out: list[dict[str, Any]] = []
    per_page = min(max(max_results, 25), 100)
    cursor = "*"
    while len(out) < max_results:
        try:
            r = await client.get(
                url,
                params={"per-page": str(per_page), "cursor": cursor},
                headers=headers, timeout=30.0,
            )
            if r.status_code != 200:
                logger.info(
                    "snowball_oa_forward_status", status=r.status_code,
                )
                break
            body = r.json() or {}
            out.extend(body.get("results") or [])
            meta = body.get("meta") or {}
            cursor = meta.get("next_cursor")
            if not cursor or not body.get("results"):
                break
        except httpx.HTTPError as exc:
            logger.warning("snowball_oa_forward_error", error=str(exc))
            break
    return out[:max_results]


async def snowball_expand(
    *,
    seed_dois: list[str],
    direction: str = "both",
    max_per_seed: int = 10,
    http_client: httpx.AsyncClient | None = None,
    mailto: str | None = None,
) -> list[ExpansionHit]:
    """Walk one citation hop from each seed DOI.

    Args:
        seed_dois: DOIs to expand from. Duplicates are ignored.
        direction: ``"forward"`` (papers that cite the seeds),
            ``"backward"`` (papers the seeds cite), or ``"both"``.
        max_per_seed: Cap on hits per seed per direction. Stops you
            from pulling 1000 forward citations for a famous review.
        http_client: Reuse caller's client when present (preserves
            cookies, connection pool). When None we open + close
            our own.
        mailto: Email for OpenAlex polite-pool UA. Pass
            ``pdf_download.unpaywall_email``.

    Returns:
        Flat list of :class:`ExpansionHit` — caller dedups + filters
        + ingests as needed.
    """
    direction = direction.lower()
    if direction not in {"forward", "backward", "both"}:
        raise ValueError(f"direction must be forward|backward|both, got {direction}")
    if max_per_seed < 1:
        return []
    seed_dois = list(dict.fromkeys(  # preserve order, dedup
        (d or "").lower().replace("https://doi.org/", "").strip()
        for d in seed_dois if d
    ))
    if not seed_dois:
        return []
    if max_per_seed > DEFAULT_PER_SEED_CAP:
        # Sanity cap — a 100-per-seed forward query for a 50-paper KB
        # is 5000 results; that's almost always not what the user wants.
        logger.info("snowball_clamping_max_per_seed", requested=max_per_seed,
                    capped=DEFAULT_PER_SEED_CAP)
        max_per_seed = DEFAULT_PER_SEED_CAP

    headers = _oa_headers(mailto)
    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    hits: list[ExpansionHit] = []
    try:
        for seed in seed_dois:
            work = await _fetch_seed_work(client, seed, headers)
            if not work:
                continue
            # Backward — referenced_works is a list of OpenAlex IDs.
            if direction in {"backward", "both"}:
                refs = work.get("referenced_works") or []
                refs = refs[:max_per_seed]
                if refs:
                    ref_works = await _batch_get_works(client, refs, headers)
                    for rw in ref_works:
                        doi, fields = _paper_from_oa_work(rw)
                        if not doi:
                            continue
                        hits.append(ExpansionHit(
                            seed_doi=seed, expanded_doi=doi,
                            direction="backward", **fields,
                        ))
            # Forward — paginate cited_by_api_url.
            if direction in {"forward", "both"}:
                forward_works = await _fetch_forward_citations(
                    client, work, max_per_seed, headers,
                )
                for fw in forward_works:
                    doi, fields = _paper_from_oa_work(fw)
                    if not doi:
                        continue
                    hits.append(ExpansionHit(
                        seed_doi=seed, expanded_doi=doi,
                        direction="forward", **fields,
                    ))
    finally:
        if own_client:
            await client.aclose()
    logger.info(
        "snowball_expand_done",
        seeds=len(seed_dois), direction=direction, hits=len(hits),
    )
    return hits


def _papers_from_hits(hits: list[ExpansionHit]) -> list[Paper]:
    """Coerce ExpansionHit → Paper so we can run apply_filters /
    screen_candidates / ingest_dois_into_kb on the same shapes the
    SciLEx path uses."""
    seen: set[str] = set()
    out: list[Paper] = []
    for h in hits:
        if h.expanded_doi in seen:
            continue
        seen.add(h.expanded_doi)
        out.append(Paper(
            id=h.expanded_doi,
            title=h.title or f"Reference {h.expanded_doi}",
            authors=[Author(name=a) for a in (h.authors or [])],
            year=h.year,
            doi=h.expanded_doi,
            abstract=h.abstract,
            journal=h.journal,
            citation_count=h.citation_count,
            source=PaperSource.WEB_SEARCH,
        ))
    return out


async def expand_kb_via_citations(
    *,
    app_state: Any,
    kb_name: str,
    direction: str = "both",
    max_per_seed: int = 10,
    seed_dois: list[str] | None = None,
    flt: Any = None,  # SearchFilter | None — typed loosely to avoid circular import
    screen_method: str | None = None,
    screen_threshold: float = 0.5,
    query: str | None = None,
    dry_run: bool = False,
) -> SnowballReport:
    """Grow ``kb_name`` by following citation edges from its existing papers.

    Seeds default to every DOI already in the KB; pass ``seed_dois``
    to restrict to a sub-set (e.g. only papers a user starred).

    The expanded DOIs flow through the same post-search machinery as
    ``search_filter_and_ingest``:

      1. snowball_expand → ExpansionHit list
      2. _papers_from_hits → Paper objects (with dedup-by-DOI)
      3. apply_filters → year/citation/abstract gates
      4. screen_candidates (optional) → BM25/LLM relevance vs ``query``
         (or vs the KB description when no query supplied)
      5. ingest_dois_into_kb → fetch PDFs, chunk, embed, store

    Args:
        kb_name: Target KB. Must already exist.
        direction: forward / backward / both.
        max_per_seed: Hits per seed per direction (caps at 25 in
            :func:`snowball_expand`).
        seed_dois: Override seed set; defaults to all KB papers.
        flt: :class:`SearchFilter` for year/citation/abstract gates.
        screen_method: ``"bm25"`` / ``"llm"`` / ``None``. Uses
            ``query`` for relevance (KB description if no query).
        screen_threshold: Drop below this score.
        query: Relevance prompt for the screen pass. Defaults to the
            KB's description.
        dry_run: Skip PDF fetch + ingest; just return the candidate
            DOI list.
    """
    from perspicacite.pipeline.search_to_kb import (
        SearchFilter, apply_filters, screen_candidates,
        ingest_dois_into_kb,
    )

    kb_meta = await app_state.session_store.get_kb_metadata(kb_name)
    if not kb_meta:
        raise ValueError(f"KB '{kb_name}' not found")

    flt = flt or SearchFilter()
    pdf_cfg = app_state.config.pdf_download
    mailto = pdf_cfg.unpaywall_email if pdf_cfg else None

    # Default seeds: every DOI in the KB.
    if seed_dois is None:
        from perspicacite.models.kb import chroma_collection_name_for_kb
        collection = kb_meta.collection_name or chroma_collection_name_for_kb(kb_name)
        rows = await app_state.vector_store.list_paper_metadata(collection)
        seed_dois = [r["doi"] for r in rows if r.get("doi")]

    report = SnowballReport(
        seed_dois=list(seed_dois), direction=direction,
    )
    if not seed_dois:
        return report

    hits = await snowball_expand(
        seed_dois=seed_dois, direction=direction,
        max_per_seed=max_per_seed, mailto=mailto,
    )
    report.raw_hits = len(hits)

    papers = _papers_from_hits(hits)
    report.unique_dois = len(papers)

    # Drop expanded DOIs already in the KB (no point re-ingesting).
    from perspicacite.models.kb import chroma_collection_name_for_kb
    collection = kb_meta.collection_name or chroma_collection_name_for_kb(kb_name)
    existing: list[Any] = []
    novel: list[Any] = []
    for p in papers:
        if await app_state.vector_store.paper_exists(collection, p.doi):
            existing.append(p)
        else:
            novel.append(p)
    report.dropped_existing = len(existing)

    # Year / citation / abstract gates.
    kept, _reasons = apply_filters(novel, flt)
    report.dropped_filtered = len(novel) - len(kept)

    # Optional relevance screen — defaults to the KB's description so
    # an unsupervised snowball still grows along the KB's topic.
    if screen_method and kept:
        effective_query = query or (kb_meta.description or kb_name)
        survivors, _scores = await screen_candidates(
            kept, query=effective_query,
            method=screen_method, threshold=screen_threshold,
            llm_client=app_state.llm_client,
        )
        report.dropped_screened = len(kept) - len(survivors)
        kept = survivors

    selected_dois = [p.doi for p in kept if p.doi]
    report.ingested_dois = selected_dois
    if dry_run or not selected_dois:
        return report

    res = await ingest_dois_into_kb(app_state, kb_name, selected_dois)
    report.added_papers = res["added_papers"]
    report.added_chunks = res["added_chunks"]
    report.failed = res["failed"]
    report.pdf_download = res["pdf_download"]
    logger.info(
        "expand_kb_via_citations_done",
        kb=kb_name, seeds=len(seed_dois),
        added=report.added_papers, dropped_existing=report.dropped_existing,
    )
    return report
