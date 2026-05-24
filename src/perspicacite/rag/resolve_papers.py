"""Single canonical web-search → enrich → rerank pipeline.

Replaces three diverging implementations:
- basic/advanced :: _web_fallback_papers (full pipeline)
- profound       :: raw WebSearchTool.execute (no enrich, no rerank)
- literature_survey :: hand-rolled scilex + standalone fan-out (no enrich)
- new MCP web_search :: now also routes here

Returns ``list[Paper]``. Callers that need dict shape do the conversion
themselves. Telemetry events flow through the unified TelemetrySink.
"""
from __future__ import annotations

from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper

logger = get_logger("perspicacite.rag.resolve_papers")


async def resolve_papers_pipeline(
    *,
    query: str,
    databases: list[str] | None,
    max_docs: int,
    app_state: Any,
    telemetry: Any = None,
    enrich: bool = True,
    rerank: bool = True,
    min_relevance: float = 0.0,
    optimize_query: bool | None = None,
    context: str | None = None,
) -> list[Paper]:
    """Run aggregator → Crossref enrich → MiniLM rerank → relevance gate."""
    from perspicacite.rag.web_search import run_web_aggregator_search

    papers = await run_web_aggregator_search(
        keyword_query=query,
        context=context,
        optimize_enabled=optimize_query,
        databases=databases,
        max_docs=max_docs,
        app_state=app_state,
        telemetry=telemetry,
    )

    if enrich and papers:
        try:
            from perspicacite.pipeline.enrichment.crossref_enrich import enrich_papers
            papers = await enrich_papers(papers)
        except Exception as e:
            logger.warning("resolve_papers_enrich_failed", error=str(e))

    if rerank and papers and len(papers) > 1:
        try:
            from perspicacite.search.screening import screen_papers_rerank
            _reranker_model = getattr(
                getattr(getattr(app_state, "config", None), "rag_modes", None),
                "reranker_model",
                "cross-encoder/ms-marco-MiniLM-L-6-v2",
            )
            items = [
                {
                    "_paper": p,
                    "title": p.title or "",
                    "abstract": p.abstract or "",
                }
                for p in papers
            ]
            results = await screen_papers_rerank(
                items, query=query, threshold=min_relevance, model_name=_reranker_model,
            )
            # ScreenResult has .score (float) and .item (dict with "_paper" key)
            scored = sorted(
                ((r.score, r.item["_paper"]) for r in results),
                key=lambda kv: kv[0], reverse=True,
            )
            papers = [p for _, p in scored][:max_docs]
        except Exception as e:
            logger.warning("resolve_papers_rerank_failed", error=str(e))
            papers = papers[:max_docs]
    else:
        papers = papers[:max_docs]

    return papers
