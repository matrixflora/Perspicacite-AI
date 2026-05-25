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

    # Absolute saturation function: s / (s + k). With k≈3 this gives a
    # threshold of 0.5 at BM25 score ≈3, matching where ms-marco rerank
    # and LLM-judge produce "borderline relevant" papers in practice.
    # The 'score' field is comparable across BM25/rerank/LLM tiers and
    # the threshold has consistent semantics. Tie-broken at score ties by
    # raw BM25 to preserve in-batch ordering.
    _K = 3.0
    norm_scores = [float(s) / (float(s) + _K) for s in raw_scores]

    results = [
        ScreenResult(
            item=candidates[i],
            score=norm_scores[i],
            kept=norm_scores[i] >= threshold,
            reason=f"bm25_raw={raw_scores[i]:.2f}",
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


async def screen_papers_rerank(
    candidates: Sequence[dict],
    query: str,
    threshold: float = 0.3,
    model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
) -> list[ScreenResult]:
    """Cross-encoder rerank screening (tier B).

    More accurate than BM25 (tier A) because the cross-encoder sees the
    query + paper jointly and can score semantic similarity beyond
    keyword overlap — catches "wrong domain entirely" hits that share
    surface keywords (e.g. a "graph neural network" query that BM25
    matches against combinatorial graph theory).

    Requires ``sentence-transformers`` (already a project dep). The
    default ``ms-marco-MiniLM-L-6-v2`` model is small (~80MB) and runs
    on CPU at ~5ms per pair. First call loads + caches the model.

    Raw model scores are logit-shaped (roughly -10..+10 for the
    ms-marco family); we apply a sigmoid to normalize them to [0,1]
    so the ``threshold`` parameter is consistent with the BM25 and LLM
    paths.

    Args:
        candidates: List of paper dicts with at least ``title`` and/or ``abstract``.
        query: Free-text relevance query.
        threshold: Normalized score >= threshold means kept=True.
        model_name: Override the default cross-encoder.

    Returns:
        List of ``ScreenResult`` sorted by score descending.
    """
    candidates_list = list(candidates)
    if not candidates_list:
        return []

    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise ImportError(
            "sentence-transformers required for rerank screening. "
            "Install with: pip install sentence-transformers"
        ) from exc

    import asyncio
    import math

    loop = asyncio.get_running_loop()
    # Try the cache first. CrossEncoder's default load does a HEAD against
    # huggingface.co to check for updates, which 429s under modest load and
    # then sleeps 31 s × 5 retries — wedging the whole RAG path. When the
    # model is already cached locally (the common case), local_files_only
    # skips the network entirely. Fall back to a full load only when the
    # cache miss raises.
    def _load_offline_or_fallback() -> "CrossEncoder":
        try:
            return CrossEncoder(model_name, local_files_only=True)
        except Exception:
            return CrossEncoder(model_name)

    model = await loop.run_in_executor(None, _load_offline_or_fallback)

    pairs = [
        (query, _candidate_text(c)[:2000])  # cap each input for throughput
        for c in candidates_list
    ]
    raw_scores = await loop.run_in_executor(None, lambda: model.predict(pairs))

    # Tempered sigmoid -> [0,1]. The ms-marco family emits logits roughly
    # -10..+10; a plain sigmoid saturates above ~10 so every "good" hit
    # collapses to 1.0 and the threshold loses resolution. Dividing by
    # T=4.0 keeps the active band wide enough to distinguish a logit-of-6
    # hit (≈0.82) from a logit-of-10 hit (≈0.92), so the returned score
    # is actually informative as a ranking signal.
    _T = 4.0
    norm_scores = [1.0 / (1.0 + math.exp(-float(s) / _T)) for s in raw_scores]

    results = [
        ScreenResult(
            item=candidates_list[i],
            score=norm_scores[i],
            kept=norm_scores[i] >= threshold,
            reason=f"rerank_logit={float(raw_scores[i]):.2f}",
        )
        for i in range(len(candidates_list))
    ]
    results.sort(key=lambda r: r.score, reverse=True)

    kept_count = sum(r.kept for r in results)
    logger.info(
        "screen_papers_rerank",
        n=len(candidates_list),
        kept=kept_count,
        threshold=threshold,
        model=model_name,
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
