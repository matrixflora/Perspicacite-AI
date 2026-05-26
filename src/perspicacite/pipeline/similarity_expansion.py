"""Two-phase similarity-based KB expansion.

Phase 1 (``score_expansion_candidates``): citation-snowball the KB's seeds,
drop already-ingested + gate-filtered papers, then score the survivors against
the KB by content similarity (Plan 1 scorers); return all scored candidates +
a score histogram + calibration samples for the interactive UI.

Phase 2 (``commit_expansion``): given a human-chosen cutoff, ingest the kept
candidates into the KB.

Reuses the lower-level pieces ``expand_kb_via_citations`` itself calls; the
interactive contract (score now, ingest later) is why this lives in its own
module rather than overloading that one-shot function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb
from perspicacite.pipeline.snowball import _papers_from_hits, snowball_expand

logger = get_logger("perspicacite.pipeline.similarity_expansion")


async def build_reference_papers(
    vector_store: Any, collection: str, max_per_paper: int = 20
) -> tuple[list[list[str]], int, int]:
    """One text-list per KB paper: ``[abstract]`` when present, else the paper's
    (capped) chunk texts. Returns ``(reference_papers, n_by_abstract,
    n_by_fallback)``. Chunks are fetched only if some paper lacks an abstract.
    """
    rows = await vector_store.list_paper_metadata(collection)
    needs_chunks = any(not (r.get("abstract") or "").strip() for r in rows)
    chunks = (
        await vector_store.list_paper_chunks(collection, max_per_paper=max_per_paper)
        if needs_chunks
        else {}
    )
    papers: list[list[str]] = []
    n_abs = n_fb = 0
    for r in rows:
        abstract = (r.get("abstract") or "").strip()
        if abstract:
            papers.append([abstract])
            n_abs += 1
        else:
            paper_chunks = chunks.get(r.get("paper_id", "")) or []
            if paper_chunks:
                papers.append(list(paper_chunks))
                n_fb += 1
    return papers, n_abs, n_fb


@dataclass
class ExpansionScoreReport:
    kb_name: str
    direction: str = "both"
    method: str = "hybrid"
    seed_count: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    histogram: list[dict[str, Any]] = field(default_factory=list)
    samples: list[dict[str, Any]] = field(default_factory=list)
    n_by_abstract: int = 0
    n_by_fallback: int = 0


def _score_histogram(scores: list[float], bins: int = 10) -> list[dict[str, Any]]:
    """Bucket 0-1 scores into ``bins`` equal-width buckets for the UI."""
    buckets = [{"lo": i / bins, "hi": (i + 1) / bins, "count": 0} for i in range(bins)]
    for s in scores:
        idx = min(bins - 1, max(0, int(s * bins)))
        buckets[idx]["count"] += 1
    return buckets


async def score_expansion_candidates(
    *,
    app_state: Any,
    kb_name: str,
    direction: str = "both",
    max_per_seed: int = 10,
    method: str = "hybrid",
    weights: tuple[float, float] = (0.25, 0.75),
    top_n: int = 5,
    seed_dois: list[str] | None = None,
    flt: Any = None,
) -> ExpansionScoreReport:
    """Phase 1: snowball -> filter -> score against the KB. Returns ALL scored
    candidates (no cutoff) + histogram + calibration samples."""
    from perspicacite.models.kb import chroma_collection_name_for_kb
    from perspicacite.pipeline.search_to_kb import SearchFilter, apply_filters
    from perspicacite.search.screening import (
        screen_papers_embedding,
        screen_papers_hybrid,
        screen_papers_setwise_bm25,
        select_calibration_samples,
    )

    kb_meta = await app_state.session_store.get_kb_metadata(kb_name)
    if not kb_meta:
        raise ValueError(f"KB '{kb_name}' not found")
    collection = kb_meta.collection_name or chroma_collection_name_for_kb(kb_name)
    flt = flt or SearchFilter()
    pdf_cfg = app_state.config.pdf_download
    mailto = pdf_cfg.unpaywall_email if pdf_cfg else None

    if seed_dois is None:
        rows = await app_state.vector_store.list_paper_metadata(collection)
        seed_dois = [r["doi"] for r in rows if r.get("doi")]

    report = ExpansionScoreReport(
        kb_name=kb_name, direction=direction, method=method, seed_count=len(seed_dois)
    )
    if not seed_dois:
        return report

    hits = await snowball_expand(
        seed_dois=seed_dois, direction=direction, max_per_seed=max_per_seed, mailto=mailto
    )
    papers, _dropped_fy = _papers_from_hits(hits)

    novel = []
    for p in papers:
        if not await app_state.vector_store.paper_exists(collection, p.doi):
            novel.append(p)
    kept, _reasons = apply_filters(novel, flt)

    items = [
        {"doi": p.doi, "title": p.title or "", "abstract": getattr(p, "abstract", "") or ""}
        for p in kept
    ]
    if not items:
        return report

    refs, n_abs, n_fb = await build_reference_papers(app_state.vector_store, collection)
    report.n_by_abstract = n_abs
    report.n_by_fallback = n_fb
    if not refs:
        return report

    if method == "embedding":
        results = await screen_papers_embedding(
            items, reference_papers=refs,
            embedding_provider=app_state.embedding_provider, top_n=top_n, threshold=0.0,
        )
    elif method == "bm25":
        results = screen_papers_setwise_bm25(
            items, reference_papers=refs, top_n=top_n, threshold=0.0
        )
    else:  # hybrid (default)
        results = await screen_papers_hybrid(
            items, reference_papers=refs, embedding_provider=app_state.embedding_provider,
            weights=weights, top_n=top_n, threshold=0.0,
        )

    report.candidates = [
        {
            "doi": r.item.get("doi"),
            "title": r.item.get("title"),
            "score": float(r.score),
            "reason": r.reason,
        }
        for r in results
    ]
    report.histogram = _score_histogram([r.score for r in results])
    samples = select_calibration_samples(results, n=4)
    report.samples = [
        {
            "doi": r.item.get("doi"),
            "title": r.item.get("title"),
            "abstract": r.item.get("abstract"),
            "score": float(r.score),
        }
        for r in samples
    ]
    logger.info(
        "score_expansion_candidates",
        kb=kb_name,
        method=method,
        seeds=len(seed_dois),
        scored=len(results),
    )
    return report


async def commit_expansion(
    *,
    app_state: Any,
    kb_name: str,
    scored: list[dict[str, Any]],
    cutoff: float,
) -> dict[str, Any]:
    """Phase 2: ingest candidates scoring at/above ``cutoff`` into the KB.

    ``scored`` is ``ExpansionScoreReport.candidates`` (each has ``doi`` +
    ``score``). Candidates without a DOI are skipped.
    """
    keep = [
        c["doi"]
        for c in scored
        if c.get("doi") and float(c.get("score", 0.0)) >= cutoff
    ]
    if not keep:
        return {"added_papers": 0, "added_chunks": 0, "failed": [], "kept": 0}
    res = await ingest_dois_into_kb(app_state, kb_name, keep)
    out = dict(res)
    out["kept"] = len(keep)
    logger.info("commit_expansion", kb=kb_name, kept=len(keep), cutoff=cutoff)
    return out
