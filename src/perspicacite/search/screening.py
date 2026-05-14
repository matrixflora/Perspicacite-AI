from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rank_bm25 import BM25Plus

from perspicacite.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "that",
        "this",
        "it",
        "its",
        "as",
        "not",
        "no",
        "can",
        "will",
        "do",
        "did",
        "so",
        "if",
        "he",
        "she",
        "we",
        "they",
        "their",
        "our",
        "which",
        "who",
        "what",
        "all",
        "also",
        "more",
        "than",
        "up",
        "out",
        "about",
        "into",
        "such",
        "may",
        "each",
        "how",
        "when",
    }
)


def _tokenize(text: str) -> list[str]:
    """Lowercase, keep alphanumeric tokens, drop stopwords and single chars."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]


def _candidate_text(candidate: dict) -> str:
    title = candidate.get("title", "") or ""
    abstract = candidate.get("abstract", "") or ""
    return f"{title} {abstract}"


@dataclass
class ScreenResult:
    item: dict
    score: float
    kept: bool
    reason: str = field(default="")


def screen_papers(
    candidates: Sequence[dict],
    reference: str | Sequence[str],
    method: str = "bm25",
    threshold: float = 0.3,
) -> list[ScreenResult]:
    """Screen candidate papers against a reference query using BM25 text similarity.

    Args:
        candidates: List of paper dicts with at least 'title' and/or 'abstract'.
        reference: Reference query string or list of query strings.
        method: Must be 'bm25'. Use screen_papers_llm for LLM scoring.
        threshold: Normalised score threshold above which a paper is 'kept'.

    Returns:
        List of ScreenResult objects sorted by score descending.
    """
    if method != "bm25":
        raise ValueError(
            "screen_papers only supports method='bm25'; use screen_papers_llm for LLM scoring"
        )

    refs: list[str] = [reference] if isinstance(reference, str) else list(reference)

    # Tokenize reference strings and drop empty ones
    ref_token_lists = [_tokenize(r) for r in refs]
    ref_token_lists = [t for t in ref_token_lists if t]

    if not ref_token_lists:
        return [ScreenResult(item=c, score=0.0, kept=False) for c in candidates]

    # Tokenize candidate documents
    candidate_tokens = [_tokenize(_candidate_text(c)) for c in candidates]

    # BM25Okapi crashes on empty token lists — substitute a sentinel
    safe_tokens = [t if t else ["__empty__"] for t in candidate_tokens]
    bm25 = BM25Plus(safe_tokens)

    # Score each candidate as the max score across all reference queries
    raw_scores = [0.0] * len(candidates)
    for ref_tokens in ref_token_lists:
        scores = bm25.get_scores(ref_tokens)
        for i, s in enumerate(scores):
            if s > raw_scores[i]:
                raw_scores[i] = float(s)

    max_score = max(raw_scores) if raw_scores else 0.0
    normalizer = max_score if max_score > 0.0 else 1.0
    norm_scores = [s / normalizer for s in raw_scores]

    results = [
        ScreenResult(
            item=candidates[i],
            score=norm_scores[i],
            kept=norm_scores[i] >= threshold,
        )
        for i in range(len(candidates))
    ]
    results.sort(key=lambda r: r.score, reverse=True)

    kept_count = sum(r.kept for r in results)
    logger.info(
        "screen_papers_bm25",
        n=len(candidates),
        kept=kept_count,
        threshold=threshold,
    )
    return results


async def screen_papers_llm(
    candidates: Sequence[dict],
    query: str,
    llm: Any,
    threshold: float = 0.5,
    batch_size: int = 20,
    model: str | None = None,
    provider: str | None = None,
) -> list[ScreenResult]:
    """Screen candidate papers against a query using LLM 0-1 relevance scoring.

    Args:
        candidates: List of paper dicts with at least 'title' and/or 'abstract'.
        query: Research query to score papers against.
        llm: AsyncLLMClient (or compatible) with async complete(messages) method.
        threshold: Score >= threshold means kept=True.
        batch_size: Number of papers per LLM call.

    Returns:
        List of ScreenResult objects sorted by score descending.
    """
    candidates = list(candidates)
    results: list[ScreenResult] = []

    system_msg = (
        "You rate how relevant each paper is to a research query. "
        "Respond ONLY with a JSON array of objects "
        '{"index": int, "score": float in [0,1], "reason": short string}.'
    )

    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start : batch_start + batch_size]
        lines = []
        for local_i, c in enumerate(batch):
            title = c.get("title", "") or ""
            abstract = (c.get("abstract", "") or "")[:400]
            lines.append(f"{local_i}. {title} — {abstract}")
        listing = "\n".join(lines)

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Query: {query}\n\nPapers:\n{listing}"},
        ]

        try:
            kw: dict[str, Any] = {"messages": messages, "stage": "screening"}
            if model is not None:
                kw["model"] = model
            if provider is not None:
                kw["provider"] = provider
            raw = await llm.complete(**kw)
            text: str = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
            match = re.search(r"\[.*\]", text, re.S)
            if match is None:
                raise ValueError("No JSON array found in response")
            parsed = json.loads(match.group())
        except Exception:
            logger.warning("screen_papers_llm_parse_failed", batch_start=batch_start)
            parsed = []

        # Build index -> object map for items that have a valid index key
        index_map: dict[int, dict] = {}
        for obj in parsed:
            if not isinstance(obj, dict):
                continue
            try:
                idx = int(obj["index"])
                index_map[idx] = obj
            except (KeyError, TypeError, ValueError):
                continue

        for local_i, candidate in enumerate(batch):
            obj = index_map.get(local_i, {})
            score = float(obj.get("score", 0.0) or 0.0)
            reason = str(obj.get("reason", ""))
            results.append(
                ScreenResult(
                    item=candidate,
                    score=score,
                    kept=score >= threshold,
                    reason=reason,
                )
            )

    results.sort(key=lambda r: r.score, reverse=True)

    kept_count = sum(r.kept for r in results)
    logger.info("screen_papers_llm", n=len(candidates), kept=kept_count)
    return results
