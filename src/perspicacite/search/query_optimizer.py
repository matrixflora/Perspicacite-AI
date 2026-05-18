"""Shared LLM-assisted query rewrite for scientific literature search.

One small Haiku call rewrites the user's query into a clean scientific
phrasing before the aggregator fan-out. Used by both the MCP `search_literature`
tool and the GUI's RAG-mode aggregator call sites.

Failure modes (LLM error, unparseable JSON, timeout) silently fall back to
the verbatim query and set ``fallback_reason``. Search must never break
because rewriting failed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


_PROMPT = """You rewrite scientific literature search queries to maximize recall on academic
databases (Semantic Scholar, OpenAlex, PubMed, arXiv, Europe PMC).

The QUERY is what the user is asking now. It is authoritative.
The CONTEXT is optional background from the conversation. Use it ONLY to
disambiguate ambiguous terms in the query (e.g., expanding an acronym, fixing
a vague pronoun). If the context conflicts with or extends beyond the query's
topic, ignore the context entirely.

Produce ONE rewritten query that:
- Preserves the user's scientific intent exactly. Do not narrow, broaden, or
  drift the topic.
- Uses concise scientific phrasing (3-12 words). Strip chatty words ("can you
  find", "I'm looking for", "papers about"). Keep proper nouns, gene/drug/
  species names, and units verbatim.
- Prefers established terminology where the query is vague (e.g., "heart
  attack" -> "myocardial infarction"), but does NOT replace specific terms the
  user already chose.
- Does NOT add boolean operators, field qualifiers, or quote marks. Plain text
  only — the downstream adapters handle DB-specific syntax.

If the query is already a clean scientific phrase, return it unchanged.
If you cannot improve it confidently, return it unchanged.

Return JSON only:
{{"searched_query": "..."}}

QUERY:
{query}

CONTEXT (may be empty, may be irrelevant — ignore if it conflicts with QUERY):
{context}
"""


@dataclass
class OptimizationResult:
    """Outcome of one optimize_query call.

    - ``enabled`` is the resolved effective flag (per-call arg merged with
      config default). ``False`` means the optimizer short-circuited without
      calling Haiku.
    - ``applied`` is True only when the LLM produced a query that differs
      from the input. ``False`` covers three cases: optimizer disabled, model
      no-op, or fallback.
    - ``fallback_reason`` is None on success, on no-op, AND when disabled.
      Otherwise one of ``llm_error`` / ``unparseable`` / ``timeout``.
    """

    searched_query: str
    enabled: bool
    applied: bool
    context_used: bool
    fallback_reason: str | None


def _strip_code_fence(text: str) -> str:
    """Remove leading/trailing markdown code fences. Mirrors the helper used
    by `rephrase_query` in `pipeline/search_to_kb.py`."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```", 2)
        cleaned = parts[1] if len(parts) >= 2 else cleaned
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.strip().rstrip("`")
    return cleaned


async def optimize_query(
    *,
    query: str,
    context: str | None,
    app_state: Any,
    optimize_enabled: bool | None = None,
) -> OptimizationResult:
    """Rewrite ``query`` into a clean scientific phrasing via one Haiku call.

    Args:
        query: The user's raw query string.
        context: Optional grounding excerpt. Truncated head-keep to the
            ``search.query_optimization.max_context_chars`` cap before being
            inserted into the prompt. ``None`` or empty string means "no
            context".
        app_state: The application state object. Must expose ``config`` and
            ``llm_client`` attributes; ``config.search.query_optimization``
            holds the runtime settings.
        optimize_enabled: Per-call override. ``True`` forces on, ``False``
            forces off, ``None`` falls back to
            ``config.search.query_optimization.enabled``.

    Returns:
        ``OptimizationResult`` — always returns a usable ``searched_query``;
        callers don't need to handle failure separately.
    """
    qo_cfg = app_state.config.search.query_optimization

    enabled = qo_cfg.enabled if optimize_enabled is None else optimize_enabled
    if not enabled:
        return OptimizationResult(
            searched_query=query, enabled=False, applied=False,
            context_used=False, fallback_reason=None,
        )

    # Truncate context head-keep.
    ctx_str = context or ""
    if len(ctx_str) > qo_cfg.max_context_chars:
        ctx_str = ctx_str[: qo_cfg.max_context_chars]
    context_used = bool(ctx_str.strip())

    # Resolve model/provider via the existing stage-routing helper.
    from perspicacite.llm.client import resolve_stage_model
    provider, model = resolve_stage_model(app_state.config, "search_optimize")

    prompt = _PROMPT.format(query=query, context=ctx_str)
    try:
        text = await asyncio.wait_for(
            app_state.llm_client.complete(
                messages=[{"role": "user", "content": prompt}],
                model=model,
                provider=provider,
                temperature=0.2,
                max_tokens=120,
                stage="search_optimize",
            ),
            timeout=qo_cfg.timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning("query_optimizer_timeout", extra={"query": query[:80]})
        return OptimizationResult(
            searched_query=query, enabled=True, applied=False,
            context_used=context_used, fallback_reason="timeout",
        )
    except Exception as exc:
        logger.warning("query_optimizer_llm_error", extra={"error": str(exc), "query": query[:80]})
        return OptimizationResult(
            searched_query=query, enabled=True, applied=False,
            context_used=context_used, fallback_reason="llm_error",
        )

    cleaned = _strip_code_fence(text)
    try:
        obj = json.loads(cleaned)
    except Exception as exc:
        logger.warning(
            "query_optimizer_unparseable",
            extra={"error": str(exc), "sample": cleaned[:200]},
        )
        return OptimizationResult(
            searched_query=query, enabled=True, applied=False,
            context_used=context_used, fallback_reason="unparseable",
        )

    rewritten = obj.get("searched_query")
    if not isinstance(rewritten, str) or not rewritten.strip():
        return OptimizationResult(
            searched_query=query, enabled=True, applied=False,
            context_used=context_used, fallback_reason="unparseable",
        )

    rewritten = rewritten.strip()
    applied = rewritten != query.strip()
    return OptimizationResult(
        searched_query=rewritten, enabled=True, applied=applied,
        context_used=context_used, fallback_reason=None,
    )
