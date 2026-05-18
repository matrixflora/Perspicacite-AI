"""Shared web aggregator search helper for RAG modes."""

from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.rag.web_search")


async def run_web_aggregator_search(
    *,
    keyword_query: str,
    context: str | None,
    optimize_enabled: bool | None,
    databases: list[str] | None,
    max_docs: int,
    apis: list[str] | None = None,
    scilex_apis: list[str] | None = None,
    allowed_provider_names: set[str] | None = None,
    app_state: Any,
) -> list[Any]:
    """Run the shared web aggregator search with query optimization.

    Runs the shared query optimizer first (when enabled), then invokes
    the aggregator with the rewritten string. Optimizer failures fall
    back to ``keyword_query`` automatically — the aggregator call always
    happens with a valid query.

    ``scilex_apis`` and ``allowed_provider_names`` may be pre-computed by
    the caller (``_web_fallback_papers``).  When called directly (e.g. from
    tests), they are derived from ``databases`` on the fly.
    """
    # --- resolve provider sets when not pre-computed ---
    _apis = apis or (databases or ["semantic_scholar", "openalex", "pubmed"])
    if scilex_apis is None or allowed_provider_names is None:
        SCILEX_BACKED = {"semantic_scholar", "openalex", "pubmed", "arxiv"}
        PROVIDER_NAMES = {
            "europepmc": "europepmc",
            "core": "core",
            "inspire": "inspire",
            "pubchem": "pubchem",
            "google_scholar": "google_scholar",
            "dblp_sparql": "dblp_sparql",
        }
        _selected = {(d or "").lower() for d in (databases or [])}
        scilex_apis = [d for d in _selected if d in SCILEX_BACKED]
        extra_providers = {PROVIDER_NAMES[d] for d in _selected if d in PROVIDER_NAMES}
        if not scilex_apis and not extra_providers:
            scilex_apis = ["semantic_scholar", "openalex", "pubmed"]
        allowed_provider_names = set(extra_providers)
        if scilex_apis:
            allowed_provider_names.add("scilex")

    # --- query optimization ---
    # Fall back to the global web app_state when no explicit one was passed.
    # This covers the GUI chat path where app_state is threaded from chat.py.
    if app_state is None:
        try:
            from perspicacite.web.state import app_state as _global_app_state
            app_state = _global_app_state
        except Exception:
            pass

    effective_query = keyword_query
    if app_state is not None and getattr(app_state, "config", None) is not None:
        import perspicacite.search.query_optimizer as _qo_mod
        try:
            opt = await _qo_mod.optimize_query(
                query=keyword_query,
                context=context,
                app_state=app_state,
                optimize_enabled=optimize_enabled,
            )
            effective_query = opt.searched_query
            if opt.applied:
                logger.info(
                    "web_aggregator_query_rewritten",
                    original=keyword_query,
                    rewritten=effective_query,
                )
        except Exception as _opt_exc:
            logger.warning(
                "web_aggregator_optimizer_failed",
                error=str(_opt_exc),
            )
            # Fall through: use keyword_query unchanged.

    # --- aggregator / SciLEx call ---
    config = getattr(app_state, "config", None) if app_state is not None else None
    try:
        if config is not None:
            from perspicacite.search.domain_aggregator import build_aggregator

            aggregator = build_aggregator(config)
            providers_attr = getattr(aggregator, "_providers", [])
            kept_providers = []
            for p in providers_attr:
                name = (getattr(p, "name", "") or type(p).__name__).lower()
                if name in allowed_provider_names:
                    kept_providers.append(p)
            if kept_providers:
                aggregator._providers = kept_providers  # type: ignore[attr-defined]
                # The aggregator's built-in domain classifier filters
                # providers whose ``domains`` don't intersect the
                # classified domain of the query (e.g. EuropePMC is
                # tagged "biomedical" and gets dropped on a "general"
                # query). The user already explicitly picked these
                # providers — bypass the filter so their choice sticks.
                aggregator._select_providers = lambda _domains: list(  # type: ignore[attr-defined]
                    kept_providers
                )
            # ``apis`` here is the SciLEx fan-out list — only the SciLEx
            # provider reads it; standalone providers ignore it and run
            # against their own endpoints regardless.
            web_papers = await aggregator.search(
                query=effective_query,
                max_results=max_docs * 6,
                apis=scilex_apis or _apis,
            )
            logger.info(
                "web_aggregator_search_done",
                providers=[
                    getattr(p, "name", type(p).__name__)
                    for p in getattr(aggregator, "_providers", [])
                ],
                returned=len(web_papers),
            )
        else:
            from perspicacite.search.scilex_adapter import SciLExAdapter

            web_papers = await SciLExAdapter().search(
                query=effective_query, max_results=max_docs * 6, apis=_apis,
            )
    except Exception as e:
        logger.warning("web_aggregator_search_failed", error=str(e))
        return []

    return web_papers
