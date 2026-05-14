"""Auto-routing: pick the most-relevant KBs for a query.

Until now, multi-KB retrieval required the caller to enumerate `kb_names`
explicitly. This module lets the user point at "all KBs" (or a curated
subset) and have Perspicacité decide which ones to actually query, based
on each KB's description plus a sample of paper titles.

Two routing methods, picked by the caller:

- ``"bm25"`` (default) — fast, no LLM call. Builds a BM25 corpus over
  per-KB "context strings" (description + top-K paper titles) and scores
  the query against it. Sub-second.
- ``"llm"`` — one LLM call asks a cheap model to read all KB context
  strings and return JSON with per-KB scores + one-sentence reasons.
  More accurate on semantic mismatches; costs one LLM call regardless
  of KB count.

The router never *queries* a KB itself; it returns a ranked list of
``KBRouteHit`` entries. The caller (chat router, MCP tool) then passes
the selected names to whichever retrieval flow it normally uses
(``MultiKBRetriever`` / ``query_chunks_across_collections``).

The "context string" each KB is scored against blends:
  - the KB's own ``description`` field (when present)
  - up to ``sample_papers`` paper titles read from its Chroma collection

so even KBs created with a generic description like "Imported from
references.bib" get scored on real content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.rag.kb_router")


@dataclass
class KBRouteHit:
    kb_name: str
    score: float
    reason: str | None = None
    sampled_titles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Cap how many paper titles we sample per KB when building the context
# string. Higher = better routing on KBs with weak descriptions but more
# tokens for the LLM path. 12 keeps the prompt comfortable.
DEFAULT_SAMPLE_PAPERS = 12


async def _build_kb_context(
    kb: Any,
    *,
    vector_store: Any,
    sample_papers: int = DEFAULT_SAMPLE_PAPERS,
) -> tuple[str, int]:
    """Return ``(context_string, num_titles_used)`` for one KB.

    Format:
        <description sentence>
        Top papers:
        - <title 1>
        - <title 2>
        ...
    """
    parts: list[str] = []
    desc = (kb.description or "").strip()
    # Skip noisy generic descriptions — they're worse signal than nothing
    generic = {
        "imported from references.bib",
        "smoke test",
        "audit test kb",
        "test",
        "demo",
    }
    if desc and desc.lower() not in generic:
        parts.append(desc)
    titles_used = 0
    try:
        rows = await vector_store.list_paper_metadata(kb.collection_name)
    except Exception as exc:
        logger.info("kb_router_list_papers_failed", kb=kb.name, error=str(exc))
        rows = []
    titles: list[str] = []
    for r in rows[:sample_papers * 2]:
        t = (r.get("title") or "").strip()
        if t and t not in titles:
            titles.append(t)
        if len(titles) >= sample_papers:
            break
    if titles:
        parts.append("Top papers:")
        for t in titles:
            parts.append(f"- {t}")
        titles_used = len(titles)
    if not parts:
        # Fall back to the KB name (very weak signal but better than empty)
        parts.append(kb.name.replace("_", " "))
    return "\n".join(parts), titles_used


def _tokenize(text: str) -> list[str]:
    import re
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-]+", text) if len(w) > 1]


async def _route_bm25(
    *, query: str, kb_contexts: list[tuple[str, str, int]], top_k: int,
    score_threshold: float,
) -> list[KBRouteHit]:
    """BM25 routing. ``kb_contexts`` is [(kb_name, context_string, num_titles), ...]."""
    from rank_bm25 import BM25Okapi
    corpus_tokens = [_tokenize(c[1]) for c in kb_contexts]
    # Guard: BM25Okapi crashes on empty docs
    corpus_tokens = [t or ["__empty__"] for t in corpus_tokens]
    bm = BM25Okapi(corpus_tokens)
    q_tokens = _tokenize(query) or ["__empty__"]
    scores = bm.get_scores(q_tokens)
    # Normalize to [0, 1] for cross-method comparability
    max_s = max(scores) if len(scores) else 0.0
    if max_s > 0:
        normalized = [s / max_s for s in scores]
    else:
        normalized = [0.0] * len(scores)
    hits = [
        KBRouteHit(
            kb_name=name,
            score=float(norm),
            reason=None,
            sampled_titles=ntitles,
        )
        for (name, _ctx, ntitles), norm in zip(kb_contexts, normalized)
    ]
    hits.sort(key=lambda h: -h.score)
    return [h for h in hits if h.score >= score_threshold][:top_k]


_LLM_PROMPT = """You are routing a user's research question to the most relevant
knowledge bases. Below are knowledge bases, each with a short description
and a sample of paper titles. Score each KB from 0.0 (not relevant) to
1.0 (clearly relevant) for the question, and give a one-sentence reason.

Return JSON only, in this exact shape:
{{"hits": [{{"kb_name": "...", "score": 0.0-1.0, "reason": "..."}}, ...]}}

Question:
{query}

Knowledge bases:
{kbs}
"""


async def _route_llm(
    *,
    query: str,
    kb_contexts: list[tuple[str, str, int]],
    top_k: int,
    score_threshold: float,
    llm_client: Any,
    model: str,
    provider: str,
) -> list[KBRouteHit]:
    """LLM routing — one batched call scores every KB."""
    kbs_block = "\n\n".join(
        f"### {name}\n{ctx}"
        for name, ctx, _ in kb_contexts
    )
    prompt = _LLM_PROMPT.format(query=query, kbs=kbs_block)
    try:
        text = await llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            provider=provider,
            max_tokens=1500,
            temperature=0.2,
            stage="kb_router",
        )
    except Exception as exc:
        logger.warning("kb_router_llm_failed_falling_back_to_bm25", error=str(exc))
        return await _route_bm25(
            query=query, kb_contexts=kb_contexts, top_k=top_k,
            score_threshold=score_threshold,
        )
    # Strip code fences if the model added them
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        # ```json ... ``` or ``` ... ```
        cleaned = cleaned.split("```", 2)[1] if cleaned.count("```") >= 2 else cleaned
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.strip().rstrip("`")
    try:
        obj = json.loads(cleaned)
    except Exception as exc:
        logger.warning(
            "kb_router_llm_unparseable_falling_back_to_bm25",
            error=str(exc), sample=cleaned[:200],
        )
        return await _route_bm25(
            query=query, kb_contexts=kb_contexts, top_k=top_k,
            score_threshold=score_threshold,
        )
    # Map num_titles back from kb_contexts for the returned hits
    name_to_titles = {name: nt for name, _ctx, nt in kb_contexts}
    hits: list[KBRouteHit] = []
    for h in obj.get("hits") or []:
        name = h.get("kb_name")
        if not name or name not in name_to_titles:
            continue
        try:
            score = float(h.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        hits.append(KBRouteHit(
            kb_name=name,
            score=max(0.0, min(1.0, score)),
            reason=(h.get("reason") or None),
            sampled_titles=name_to_titles[name],
        ))
    hits.sort(key=lambda h: -h.score)
    return [h for h in hits if h.score >= score_threshold][:top_k]


async def auto_route_kbs(
    *,
    query: str,
    kb_metas: list[Any],
    vector_store: Any,
    method: str = "bm25",
    top_k: int = 3,
    score_threshold: float = 0.1,
    llm_client: Any = None,
    llm_model: str = "claude-haiku-4-5",
    llm_provider: str = "anthropic",
    sample_papers: int = DEFAULT_SAMPLE_PAPERS,
) -> list[KBRouteHit]:
    """Pick the top-N most relevant KBs for ``query``.

    Args:
        query: User research question.
        kb_metas: KnowledgeBase metas to consider. Caller supplies the
            candidate set (could be every KB, a curated subset, KBs the
            user has access to, etc.).
        vector_store: For sampling per-KB paper titles.
        method: ``"bm25"`` (default; no LLM) or ``"llm"``.
        top_k: Max KBs to return.
        score_threshold: Drop KBs whose normalized score < this. For
            ``bm25`` scores are normalized to [0,1] by dividing by the
            max in the batch.
        llm_client: Required when ``method="llm"``.
        llm_model / llm_provider: Override the default cheap router model.
        sample_papers: Per-KB paper-title sample size used to build the
            context string scored against the query.

    Returns:
        A ranked list of :class:`KBRouteHit`. Empty when no KB passed
        the threshold.
    """
    if not kb_metas:
        return []
    # Build per-KB context strings (description + sampled titles).
    kb_contexts: list[tuple[str, str, int]] = []
    for kb in kb_metas:
        ctx, ntitles = await _build_kb_context(
            kb, vector_store=vector_store, sample_papers=sample_papers,
        )
        kb_contexts.append((kb.name, ctx, ntitles))

    if method == "llm" and llm_client is not None:
        return await _route_llm(
            query=query, kb_contexts=kb_contexts, top_k=top_k,
            score_threshold=score_threshold, llm_client=llm_client,
            model=llm_model, provider=llm_provider,
        )
    return await _route_bm25(
        query=query, kb_contexts=kb_contexts, top_k=top_k,
        score_threshold=score_threshold,
    )
