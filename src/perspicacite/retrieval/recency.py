"""Recency re-weighting of retrieved chunks (post-scoring re-rank)."""

from __future__ import annotations

import contextlib
import datetime as _dt
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_HALF_LIFE_YEARS = 8.0


def _year_of(chunk: Any) -> int | None:
    """Extract publication year from a chunk (object or dict)."""
    if isinstance(chunk, dict):
        # Paper-level dict: year is a top-level key
        y = chunk.get("year") or chunk.get("paper_year")
        if y is None:
            # Chunk-level dict: year may be inside metadata
            md = chunk.get("metadata") or {}
            y = md.get("year") if isinstance(md, dict) else getattr(md, "year", None)
    else:
        md = getattr(chunk, "metadata", None) or {}
        y = md.get("year") if isinstance(md, dict) else getattr(md, "year", None)
    try:
        return int(y) if y else None
    except (TypeError, ValueError):
        return None


def _get_score(chunk: Any) -> float:
    """Extract score from a chunk (object or dict)."""
    if isinstance(chunk, dict):
        return float(chunk.get("score") or chunk.get("paper_score") or 0.0)
    return float(getattr(chunk, "score", 0.0) or 0.0)


def _set_score(chunk: Any, value: float) -> None:
    """Set score on a chunk (object or dict), silently skipping if not possible."""
    if isinstance(chunk, dict):
        if "score" in chunk:
            chunk["score"] = value
        elif "paper_score" in chunk:
            chunk["paper_score"] = value
        return
    with contextlib.suppress(Exception):
        chunk.score = value


def apply_recency_weighting(
    chunks: Sequence[Any],
    recency_weight: float | None,
    half_life_years: float | None = None,
    current_year: int | None = None,
) -> list[Any]:
    """Blend each chunk's score with an exponential-decay recency factor, then re-sort desc.

    new_score = old_score * (1 - w + w * recency_factor)
    recency_factor = 0.5 ** (max(0, current_year - paper_year) / half_life)

    Papers with no year get factor 1.0 (neutral). w<=0 or None -> no-op (input returned
    unchanged). Handles both chunk objects (with .score / .metadata) and plain dicts
    (with "score"/"paper_score" and "year" keys).
    """
    chunks = list(chunks)
    if not recency_weight or recency_weight <= 0:
        return chunks
    w = min(1.0, float(recency_weight))
    hl = float(half_life_years or DEFAULT_HALF_LIFE_YEARS)
    if hl <= 0:
        hl = DEFAULT_HALF_LIFE_YEARS
    cy = int(current_year or _dt.date.today().year)
    for c in chunks:
        y = _year_of(c)
        factor = 1.0 if y is None else 0.5 ** (max(0, cy - y) / hl)
        old = _get_score(c)
        _set_score(c, old * (1.0 - w + w * factor))
    chunks.sort(key=lambda c: _get_score(c), reverse=True)
    return chunks


def apply_recency_weighting_to_papers(
    papers: list[dict[str, Any]],
    recency_weight: float | None,
    half_life_years: float | None = None,
    current_year: int | None = None,
) -> list[dict[str, Any]]:
    """Paper-dict variant of apply_recency_weighting.

    Expects papers shaped like {doi, year, paper_score|score, ...}. Operates
    in place AND returns the same list re-sorted by adjusted score desc.
    No-op when recency_weight is None or 0. Missing year => factor 1.0.
    """
    if not recency_weight or recency_weight <= 0:
        return papers
    w = min(1.0, float(recency_weight))
    hl = float(half_life_years or DEFAULT_HALF_LIFE_YEARS)
    if hl <= 0:
        hl = DEFAULT_HALF_LIFE_YEARS
    cy = int(current_year or _dt.date.today().year)
    score_key = "paper_score" if (papers and "paper_score" in papers[0]) else "score"
    for p in papers:
        y = p.get("year")
        try:
            y = int(y) if y else None
        except (TypeError, ValueError):
            y = None
        factor = 1.0 if y is None else 0.5 ** (max(0, cy - y) / hl)
        old = float(p.get(score_key, 0.0) or 0.0)
        p[score_key] = old * (1.0 - w + w * factor)
    papers.sort(key=lambda p: float(p.get(score_key, 0.0) or 0.0), reverse=True)
    return papers
