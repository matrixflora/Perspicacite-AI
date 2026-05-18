"""Basic RAG Mode - Exact implementation from release package.

Basic RAG performs simple retrieval and generation:
- Single query (no rephrasing)
- Vector similarity search with optional hybrid retrieval
- Basic document selection
- Direct response generation (no refinement)
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from perspicacite.config.schema import MultimodalMode
from perspicacite.logging import get_logger
from perspicacite.models.rag import RAGMode, RAGRequest, RAGResponse, SourceReference, StreamEvent
from perspicacite.provenance.context import get_collector
from perspicacite.rag.code_excerpts import collect_code_excerpts
from perspicacite.rag.conversation_helpers import (
    build_user_message_with_history,
    compute_retrieval_query,
    format_conversation_block,
)
from perspicacite.rag.figure_refs import collect_figure_refs
from perspicacite.rag.modes.base import BaseRAGMode
from perspicacite.rag.multimodal import wrap_messages_for_chunks
from perspicacite.rag.query_scope import resolve_paper_scope_for_query
from perspicacite.rag.utils import (
    flatten_paper_results_to_chunks,
    format_documents_for_prompt,
    format_paper_results_for_prompt,
    format_references,
    get_doc_citation,
    get_system_prompt,
)

logger = get_logger("perspicacite.rag.modes.basic")


def _clean_query_for_keyword_search(q: str) -> str:
    # SciLEx / SemanticScholar wrap the query in quotes for exact-phrase
    # matching. That kills natural-language questions and hyphenated terms
    # (e.g. ``"feature-based molecular networking (FBMN)"`` matches nothing
    # while ``feature based molecular networking FBMN`` finds the canonical
    # papers). Normalize the query for keyword search:
    #   * strip leading interrogatives / imperatives
    #   * drop trailing punctuation
    #   * replace hyphens, parens, and other punctuation with spaces
    import re as _re

    s = (q or "").strip()
    s = _re.sub(
        r"^(what(?:'s| is| are)?|how(?: does| do| can| would)?|why(?: is| are)?|"
        r"when(?: is| are)?|where(?: is| are)?|who(?: is| are)?|"
        r"can you|could you|please|"
        r"summari[sz]e|summary of|describe|explain|tell me (?:about|the)|"
        r"compare|list|find|show me|give me|provide)\b\s*",
        "", s, flags=_re.IGNORECASE,
    )
    # Hyphens and parens defeat exact-phrase match — replace with spaces.
    s = _re.sub(r"[-/(),:;?!*\"'`]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s or q


async def _canonicalize_candidates_from_crossref(
    candidates: list[dict[str, Any]],
) -> None:
    # In-place: replace title / authors / year / journal on every
    # candidate that has a DOI with Crossref's values. Crossref is the
    # canonical DOI registrar — its records are far cleaner than what
    # Google Scholar / SciLEx / OpenAlex scrapes produce. Calls run in
    # parallel; per-DOI failures are silent (we keep the original fields).
    import asyncio

    try:
        import httpx
        from perspicacite.pipeline.download.crossref import enrich_from_crossref
    except Exception as e:
        logger.warning("crossref_canonicalize_import_failed", error=str(e))
        return

    targets = [c for c in candidates if c.get("doi")]
    if not targets:
        return

    async with httpx.AsyncClient(timeout=10.0) as http:
        async def _one(c: dict[str, Any]) -> None:
            try:
                # base_metadata={} forces every field to be considered
                # missing, so Crossref returns its full canonical patch.
                patch = await enrich_from_crossref(
                    c["doi"], http_client=http, base_metadata={},
                )
            except Exception as e:
                logger.debug("crossref_one_failed", doi=c.get("doi"), error=str(e))
                return
            if not patch:
                return
            for k in ("title", "authors", "year", "journal"):
                if patch.get(k):
                    c[k] = patch[k]
            # Crossref doesn't return citation count; keep whatever the
            # provider already had.

        await asyncio.gather(*[_one(c) for c in targets], return_exceptions=True)
    logger.info(
        "crossref_canonicalized",
        attempted=len(targets), candidates=len(candidates),
    )


async def _web_fallback_papers(
    *,
    query: str,
    databases: list[str] | None,
    max_docs: int,
    config: Any = None,
    min_relevance: float = 0.25,
    context: str | None = None,
    optimize_query: bool | None = None,
    app_state: Any = None,
) -> list[dict[str, Any]]:
    # Live literature search used when the KB returns nothing. Filters
    # raw provider hits through a cross-encoder (MiniLM) rerank so we
    # rank by semantic relevance rather than keyword overlap; this
    # catches papers whose abstract doesn't share many surface tokens
    # with the question but is on-topic, and demotes lexical false
    # positives like phenomenology / interferon papers for an FBMN
    # mass-spec question. BM25 is kept as a fallback if the model
    # can't be loaded (offline / first-run download failures).
    try:
        from perspicacite.search.screening import (
            screen_papers,
            screen_papers_rerank,
        )
    except Exception as e:
        logger.warning("basic_web_fallback_import_failed", error=str(e))
        return []

    apis = databases or ["semantic_scholar", "openalex", "pubmed"]
    keyword_query = _clean_query_for_keyword_search(query)
    if keyword_query != query:
        logger.info(
            "basic_web_fallback_query_cleaned",
            original=query, cleaned=keyword_query,
        )

    # Map UI database choices to actual aggregator providers. SciLEx
    # internally fans out to Semantic Scholar / OpenAlex / PubMed / arXiv,
    # so any of those UI ticks means "include the scilex provider with
    # that API in its api-list". Every other UI tick maps 1:1 to a
    # standalone provider in the aggregator.
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
    # If user picked nothing or only non-mapped names, fall back to a
    # sensible default rather than running zero providers.
    if not scilex_apis and not extra_providers:
        scilex_apis = ["semantic_scholar", "openalex", "pubmed"]

    allowed_provider_names = set(extra_providers)
    if scilex_apis:
        allowed_provider_names.add("scilex")

    # Resolve effective app_state: prefer explicit arg, fall back to config
    # wrapper so legacy call sites (config-only) still work.
    effective_app_state = app_state
    if effective_app_state is None and config is not None:
        # Minimal shim so _run_web_aggregator_search can read config.
        class _AppStateShim:
            def __init__(self, cfg: Any) -> None:
                self.config = cfg
                self.llm_client = None
        effective_app_state = _AppStateShim(config)

    web_papers = await _run_web_aggregator_search(
        keyword_query=keyword_query,
        context=context,
        optimize_enabled=optimize_query,
        databases=databases,
        max_docs=max_docs,
        apis=apis,
        scilex_apis=scilex_apis,
        allowed_provider_names=allowed_provider_names,
        app_state=effective_app_state,
    )

    # SciLEx is a meta-provider fanning to SS/OpenAlex/PubMed/arXiv but
    # tags every returned paper with PaperSource.SCILEX — the underlying
    # API is lost. When only one backend was queried, label by that
    # backend (e.g. "pubmed") since SciLEx is purely a transport.
    # Otherwise call it "scilex (multi-DB)" and pass the full api list
    # through ``source_apis`` for the FE tooltip.
    if len(scilex_apis) == 1:
        scilex_relabel = scilex_apis[0]
    elif len(scilex_apis) > 1:
        scilex_relabel = "scilex (multi-DB)"
    else:
        scilex_relabel = "scilex"

    candidates: list[dict[str, Any]] = []
    for p in web_papers:
        if not p.title or not (p.abstract or "").strip():
            continue
        # Resolve the *originating* database. ``Paper.source`` for SciLEx
        # records is ``PaperSource.SCILEX`` (a wrapper, useless to display);
        # ``metadata.sources`` carries the real provider names (e.g.
        # ``["openalex"]`` or ``["google_scholar", "scilex"]``). Prefer
        # the first non-scilex entry from metadata, else fall back to the
        # enum; finally relabel the bare "scilex" label.
        src_str: str | None = None
        meta_sources = (getattr(p, "metadata", None) or {}).get("sources")
        if isinstance(meta_sources, list):
            for s in meta_sources:
                if s and str(s).lower() != "scilex":
                    src_str = str(s).lower()
                    break
        if not src_str:
            src_obj = getattr(p, "source", None)
            src_str = getattr(src_obj, "value", None) or (
                str(src_obj).replace("PaperSource.", "").lower() if src_obj else None
            )
        if src_str == "scilex":
            src_str = scilex_relabel
        # Best-effort URL for the "open in source" link.
        best_url = (
            getattr(p, "url", None)
            or getattr(p, "pdf_url", None)
            or (f"https://doi.org/{p.doi}" if p.doi else None)
        )
        candidates.append({
            "paper_id": p.id or p.doi or p.title,
            "title": p.title,
            "abstract": p.abstract or "",
            "authors": [a.name for a in (p.authors or [])],
            "year": p.year,
            "journal": getattr(p, "journal", None),
            "doi": p.doi,
            "url": best_url,
            "source": src_str,
            # When ``source`` is the SciLEx wrapper label, this carries
            # the underlying APIs that were queried so the UI can show
            # them in a hover tooltip on the chip.
            "source_apis": list(scilex_apis) if src_str and src_str.startswith("scilex") else None,
            "citation_count": getattr(p, "citation_count", None),
            "full_text": p.abstract or "",
            "kb_name": None,
        })
    if not candidates:
        logger.info("basic_web_fallback_empty_candidates", query=query, apis=apis)
        return []

    # Canonicalize citation metadata via Crossref for any candidate that
    # carries a DOI. Provider scrapes (especially Google Scholar) often
    # mangle author lists ("LF Nothias … - Nature, 2020"), drop the
    # journal, or supply abbreviated titles. Crossref is the authoritative
    # registrar so we trust its values when available.
    await _canonicalize_candidates_from_crossref(candidates)

    screen_method = "rerank+bm25+citations"
    rerank_by_id: dict[int, float] = {}
    try:
        # MiniLM cross-encoder: primary semantic relevance signal.
        rerank_results = await screen_papers_rerank(
            candidates, query=keyword_query, threshold=0.0,
        )
        for r in rerank_results:
            rerank_by_id[id(r.item)] = float(r.score)
    except Exception as e:
        logger.warning(
            "basic_web_fallback_rerank_failed_fallback_bm25", error=str(e),
        )
        screen_method = "bm25+citations"

    bm25_by_id: dict[int, float] = {}
    try:
        # Always compute BM25 too — it's cheap and contributes the lowest-
        # weight lexical signal in the final blend.
        bm25_results = screen_papers(
            candidates, reference=keyword_query, threshold=0.0,
        )
        for r in bm25_results:
            bm25_by_id[id(r.item)] = float(r.score)
    except Exception as e:
        logger.warning("basic_web_fallback_bm25_failed", error=str(e))

    if not rerank_by_id and not bm25_by_id:
        out = candidates[:max_docs]
        for r in out:
            r["paper_score"] = 1.0
        return out

    import math as _math

    # 3-signal blend: MiniLM (primary) × citation count (secondary) ×
    # BM25 (tertiary). Weights sum to 1.0 — if the cross-encoder failed
    # to load, its share is redistributed to BM25 so the ranking still
    # uses the strongest signal available.
    if rerank_by_id:
        W_RERANK, W_CITES, W_BM25 = 0.6, 0.25, 0.15
    else:
        W_RERANK, W_CITES, W_BM25 = 0.0, 0.3, 0.7

    raw: list[tuple[float, float, dict[str, Any]]] = []
    for c in candidates:
        item = dict(c)
        if item.get("abstract"):
            item.setdefault("chunk_text", item["abstract"])
        item.pop("abstract", None)

        rerank_s = rerank_by_id.get(id(c), 0.0)
        bm25_s = bm25_by_id.get(id(c), 0.0)
        cites = item.get("citation_count") or 0
        cite_score = min(_math.log1p(max(0, int(cites))) / 8.0, 1.0)

        blended = round(
            W_RERANK * rerank_s + W_CITES * cite_score + W_BM25 * bm25_s,
            4,
        )
        # Drop hits with effectively no signal in any channel.
        if blended < min_relevance and rerank_s < min_relevance:
            continue
        item["paper_score"] = blended
        item["_rerank_score"] = round(rerank_s, 4)
        item["_bm25_score"] = round(bm25_s, 4)
        item["_citation_score"] = round(cite_score, 4)
        raw.append((blended, rerank_s, item))

    raw.sort(key=lambda t: t[0], reverse=True)
    kept = [t[2] for t in raw[:max_docs]]

    logger.info(
        "basic_web_fallback",
        query=query,
        scilex_apis=scilex_apis,
        extra_providers=sorted(extra_providers),
        candidates=len(candidates), kept=len(kept),
        threshold=min_relevance,
        screen_method=screen_method,
        sources_kept=[k.get("source") for k in kept],
    )
    return kept


async def _run_web_aggregator_search(
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
    """Run the web aggregator search for basic RAG mode.

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
                    "basic_web_aggregator_query_rewritten",
                    original=keyword_query,
                    rewritten=effective_query,
                )
        except Exception as _opt_exc:
            logger.warning(
                "basic_web_aggregator_optimizer_failed",
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
                "basic_web_fallback_aggregator",
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
        logger.warning("basic_web_fallback_search_failed", error=str(e))
        return []

    return web_papers


async def _apply_copyright_filter(
    *,
    answer: str,
    paper_results: list[dict[str, Any]],
    llm: Any,
    config: Any,
) -> str:
    """Defense-in-depth copyright check on synthesis output.

    Called from each RAG-mode synthesis path. Resolves the
    ``copyright_filter`` section from the runtime Config (when wired
    through ``self.config``), builds source dicts from the paper-result
    list (using ``full_text`` as the comparison body), and runs the
    configured action (log / quote / strip / rewrite).

    No-op when sources are empty or the answer is empty.
    """
    if not answer or not paper_results:
        return answer
    try:
        from perspicacite.rag.copyright_filter import CopyrightFilter
        cf_cfg = getattr(config, "copyright_filter", None)
        if cf_cfg is None or not getattr(cf_cfg, "enabled", True):
            return answer
        sources = [
            {
                "text": p.get("full_text") or "",
                "title": p.get("title"),
            }
            for p in paper_results
        ]
        cf = CopyrightFilter(
            enabled=cf_cfg.enabled,
            action=getattr(cf_cfg, "action", "log"),
            min_ngram=getattr(cf_cfg, "min_ngram", 8),
            llm_client=llm,
            rewrite_model=getattr(cf_cfg, "rewrite_model", "claude-haiku-4-5"),
            rewrite_provider=getattr(cf_cfg, "rewrite_provider", "anthropic"),
        )
        return await cf.apply(answer, sources)
    except Exception as exc:
        # Filter is best-effort; never break the synthesis flow.
        logger.warning("copyright_filter_failed", error=str(exc))
        return answer


class BasicRAGMode(BaseRAGMode):
    """
    Basic RAG Mode - Exact port from release package core/core.py

    Characteristics:
    - Single query retrieval (no query expansion)
    - Vector-based similarity search with optional hybrid retrieval
    - No response refinement
    - Fastest mode, suitable for simple factual queries
    """

    def __init__(self, config: Any):
        super().__init__(config)
        self.initial_docs = config.knowledge_base.default_top_k * 3  # 30 default
        self.final_max_docs = 5
        self.max_docs_per_source = 2

        # Enable hybrid retrieval by default for better retrieval quality
        rag_settings = getattr(config.rag_modes, "basic", None)
        if rag_settings is None:
            rag_settings = {}
        elif hasattr(rag_settings, "model_dump"):
            rag_settings = rag_settings.model_dump()
        elif hasattr(rag_settings, "dict"):
            rag_settings = rag_settings.dict()

        self.use_hybrid = rag_settings.get("use_hybrid", True)
        self.use_two_pass = getattr(config.knowledge_base, "use_two_pass", True)

    async def execute(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> RAGResponse:
        """
        Execute Basic RAG - single query, direct retrieval, no refinement.

        Ported from: core/core.py::retrieve_documents() and get_response()
        """
        logger.info(
            "basic_rag_start",
            query=request.query,
            use_hybrid=self.use_hybrid,
            use_two_pass=self.use_two_pass,
        )

        dkb = self._build_kb_retriever(request, vector_store, embedding_provider)
        collection = dkb.collection_name
        retrieval_query, refined = await compute_retrieval_query(request, llm)
        if refined:
            request.refined_query = refined  # type: ignore[misc]
        scope = await resolve_paper_scope_for_query(
            retrieval_query,
            collection,
            vector_store,
            max_papers_override=getattr(request, "max_papers_retrieval", None),
        )
        cap = max(1, getattr(request, "max_papers_retrieval", None) or self.final_max_docs)

        if self.use_two_pass:
            # Two-pass retrieval — identify papers, then fetch all their chunks
            paper_results = await dkb.search_two_pass(
                retrieval_query,
                top_k=cap,
                paper_scope=scope,
                max_papers_cap=cap,
            )
            logger.info("basic_two_pass", papers=len(paper_results))
        else:
            # Legacy chunk-level retrieval (no two-pass)
            chunk_results = await dkb.search(retrieval_query, top_k=cap)
            paper_results = []
            for r in chunk_results:
                meta = r.get("metadata")
                paper_results.append(
                    {
                        "paper_id": getattr(meta, "paper_id", None) if meta else None,
                        "paper_score": r.get("score", 0.0),
                        "title": getattr(meta, "title", None) if meta else None,
                        "authors": getattr(meta, "authors", None) if meta else None,
                        "year": getattr(meta, "year", None) if meta else None,
                        "doi": getattr(meta, "doi", None) if meta else None,
                        "full_text": r.get("text", ""),
                        "kb_name": r.get("kb_name"),
                    }
                )
            logger.info("basic_chunk_retrieval", chunks=len(chunk_results))

        # Web-search fallback when the KB returned nothing (no KB selected,
        # KB doesn't exist, or KB has no relevant docs). Mirrors the same
        # logic in execute_stream so non-streaming callers behave the same.
        web_fallback_used = False
        if not paper_results:
            paper_results = await _web_fallback_papers(
                query=retrieval_query,
                databases=request.databases,
                max_docs=cap,
                config=getattr(self, "config", None),
                app_state=getattr(request, "app_state", None),
            )
            web_fallback_used = True

        # Apply optional recency weighting
        if getattr(request, "recency_weight", None):
            from perspicacite.retrieval.recency import apply_recency_weighting

            paper_results = apply_recency_weighting(
                paper_results,
                request.recency_weight,
                getattr(request, "recency_half_life_years", None),
            )

        # Provenance: record retrieval events
        retrieval_source = "web_search" if web_fallback_used else (request.kb_name or "default")
        _c = get_collector()
        if _c is not None:
            _c.add_trace(
                "retrieve",
                detail={
                    "kb_name": retrieval_source,
                    "count": len(paper_results),
                    "web_fallback": web_fallback_used,
                },
            )
            for rank, p in enumerate(paper_results):
                _c.add_retrieval(
                    paper_id=p.get("paper_id"),
                    doi=p.get("doi"),
                    title=p.get("title"),
                    score=float(p.get("paper_score", 0.0) or 0.0),
                    kb_name=p.get("kb_name"),
                    content_type=None,
                    pipeline_step=None,
                    rank=rank,
                    stage_label="basic.retrieve",
                )

        # Build sources from paper results
        sources = []
        for p in paper_results:
            sources.append(
                SourceReference(
                    title=p.get("title") or "Untitled",
                    authors=p.get("authors"),
                    year=p.get("year"),
                    journal=p.get("journal"),
                    doi=p.get("doi"),
                    url=p.get("url"),
                    source=p.get("source"),
                    source_apis=p.get("source_apis"),
                    relevance_score=p.get("paper_score", 0.0),
                    kb_name=p.get("kb_name"),
                    chunk_text=p.get("chunk_text"),
                )
            )

        # Step 2: Generate response using full paper context
        if paper_results:
            context = format_paper_results_for_prompt(paper_results, max_chars_per_paper=4000)
        else:
            context = "No relevant papers found."

        answer = await self._generate_response_from_context(
            query=request.query,
            context=context,
            llm=llm,
            request=request,
            num_papers=len(paper_results),
            preamble=scope.scope_note,
        )

        # Defense-in-depth copyright filter: detect any verbatim copying
        # from the source chunks into the LLM's answer. Config-driven
        # action (log / quote / strip / rewrite). Sources are the
        # paper-result dicts whose ``full_text`` field carries the
        # exact text the LLM had access to.
        answer = await _apply_copyright_filter(
            answer=answer, paper_results=paper_results, llm=llm,
            config=self.config,
        )

        # Step 6: Append references section to answer using utility function
        if sources:
            references = format_references(sources)
            answer = answer.strip() + "\n\n" + references

        logger.info("basic_rag_complete", sources=len(sources))

        # Sub-project C (2026-05-15): attach code excerpts + figure refs.
        _mm = getattr(self.config, "multimodal", None)
        _show_code = bool(getattr(_mm, "show_code", False)) if _mm else False
        _mode = getattr(_mm, "mode", None) if _mm else None
        _dc_chunks = flatten_paper_results_to_chunks(paper_results)
        _code_excerpts = collect_code_excerpts(_dc_chunks) if _show_code else []
        _figure_refs = (
            collect_figure_refs(_dc_chunks, capsule_root=Path(self.config.capsule.root))
            if _mode is not None and _mode != MultimodalMode.OFF
            else []
        )

        return RAGResponse(
            answer=answer,
            sources=sources,
            mode=RAGMode.BASIC,
            iterations=1,
            web_search_used=False,
            code_excerpts=_code_excerpts,
            figures=_figure_refs,
        )

    async def execute_stream(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Execute Basic RAG with true streaming output."""
        yield StreamEvent.status("Basic RAG: Retrieving documents...")

        dkb = self._build_kb_retriever(request, vector_store, embedding_provider)
        collection = dkb.collection_name
        retrieval_query, refined = await compute_retrieval_query(request, llm)
        if refined:
            request.refined_query = refined  # type: ignore[misc]
        scope = await resolve_paper_scope_for_query(
            retrieval_query,
            collection,
            vector_store,
            max_papers_override=getattr(request, "max_papers_retrieval", None),
        )
        cap = max(1, getattr(request, "max_papers_retrieval", None) or self.final_max_docs)

        if self.use_two_pass:
            paper_results = await dkb.search_two_pass(
                retrieval_query,
                top_k=cap,
                paper_scope=scope,
                max_papers_cap=cap,
            )
        else:
            chunk_results = await dkb.search(retrieval_query, top_k=cap)
            paper_results = []
            for r in chunk_results:
                meta = r.get("metadata")
                paper_results.append(
                    {
                        "paper_id": getattr(meta, "paper_id", None) if meta else None,
                        "paper_score": r.get("score", 0.0),
                        "title": getattr(meta, "title", None) if meta else None,
                        "authors": getattr(meta, "authors", None) if meta else None,
                        "year": getattr(meta, "year", None) if meta else None,
                        "doi": getattr(meta, "doi", None) if meta else None,
                        "full_text": r.get("text", ""),
                        "kb_name": r.get("kb_name"),
                    }
                )

        # Web-search fallback: if the KB query produced no documents (no KB
        # selected, KB doesn't exist, or KB is empty), do a live literature
        # search using the user-selected database providers. This delivers
        # the welcome-screen promise that we "fall back to web literature
        # search when your KB is insufficient" — historically only wired in
        # agentic / literature_survey modes.
        web_fallback_used = False
        if not paper_results:
            yield StreamEvent.status(
                "No KB results — falling back to web literature search…"
            )
            paper_results = await _web_fallback_papers(
                query=retrieval_query,
                databases=request.databases,
                max_docs=cap,
                config=getattr(self, "config", None),
                app_state=getattr(request, "app_state", None),
            )
            web_fallback_used = True
            if paper_results:
                yield StreamEvent.status(
                    f"Web search returned {len(paper_results)} relevant paper(s)."
                )
            else:
                yield StreamEvent.status(
                    "Web search returned no relevant papers."
                )

        # Apply optional recency weighting
        if getattr(request, "recency_weight", None):
            from perspicacite.retrieval.recency import apply_recency_weighting

            paper_results = apply_recency_weighting(
                paper_results,
                request.recency_weight,
                getattr(request, "recency_half_life_years", None),
            )

        # Provenance: record retrieval events. Source is web_search when
        # the fallback ran so the UI footer doesn't keep claiming kb=default.
        retrieval_source = "web_search" if web_fallback_used else (request.kb_name or "default")
        _c = get_collector()
        if _c is not None:
            _c.add_trace(
                "retrieve",
                detail={
                    "kb_name": retrieval_source,
                    "count": len(paper_results),
                    "web_fallback": web_fallback_used,
                },
            )
            for rank, p in enumerate(paper_results):
                _c.add_retrieval(
                    paper_id=p.get("paper_id"),
                    doi=p.get("doi"),
                    title=p.get("title"),
                    score=float(p.get("paper_score", 0.0) or 0.0),
                    kb_name=p.get("kb_name") or (
                        "web_search" if web_fallback_used else None
                    ),
                    content_type=None,
                    # Surface the originating provider (e.g. "google_scholar",
                    # "openalex") in the provenance Source column.
                    pipeline_step=p.get("source") or (
                        "web_search" if web_fallback_used else None
                    ),
                    rank=rank,
                    stage_label="basic.web_search" if web_fallback_used else "basic.retrieve",
                )

        # Prepare sources
        sources = []
        for p in paper_results:
            sources.append(
                SourceReference(
                    title=p.get("title") or "Untitled",
                    authors=p.get("authors"),
                    year=p.get("year"),
                    journal=p.get("journal"),
                    doi=p.get("doi"),
                    url=p.get("url"),
                    source=p.get("source"),
                    source_apis=p.get("source_apis"),
                    relevance_score=p.get("paper_score", 0.0),
                    kb_name=p.get("kb_name"),
                    chunk_text=p.get("chunk_text"),
                )
            )
        for source in sources:
            yield StreamEvent.source(source)

        if not paper_results:
            msg = (
                "No relevant documents found in your KB, and the web "
                "literature search returned no results either."
                if web_fallback_used
                else "No relevant documents found to answer your question."
            )
            yield StreamEvent.content(msg)
            yield StreamEvent.done(
                conversation_id="",
                tokens_used=0,
                mode="basic",
                iterations=1,
            )
            return

        yield StreamEvent.status("Basic RAG: Generating response...")

        context = format_paper_results_for_prompt(paper_results, max_chars_per_paper=4000)
        hist = format_conversation_block(getattr(request, "conversation_history", None))
        user_body = f"Documents:\n{context}\n\nQuestion: {request.query}"
        if scope.scope_note:
            user_body = f"{scope.scope_note}\n\n{user_body}"
        user_content = build_user_message_with_history(history_block=hist, body=user_body)
        messages = [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": user_content},
        ]

        # Stream the LLM response
        full_response = ""
        try:
            async for chunk in llm.stream(
                messages=messages,
                model=request.model,
                provider=request.provider,
                max_tokens=2000,
                temperature=0.3,
                stage="basic.answer",
            ):
                full_response += chunk
                yield StreamEvent.content(chunk)
        except Exception as e:
            logger.error("basic_streaming_error", error=str(e))
            # Fall back to non-streaming
            answer = await self._generate_response_from_context(
                query=request.query,
                context=context,
                llm=llm,
                request=request,
                num_papers=len(paper_results),
                preamble=scope.scope_note,
            )
            yield StreamEvent.content(answer)
            full_response = answer

        # Defense-in-depth copyright filter on the full streamed
        # response. For action="log" we just emit a warning log; for
        # quote/strip/rewrite we emit a "revision" event after the
        # answer with the corrected text — clients may render it or
        # ignore. Does not retract the already-streamed content.
        try:
            revised = await _apply_copyright_filter(
                answer=full_response, paper_results=paper_results, llm=llm,
                config=self.config,
            )
            if revised != full_response:
                yield StreamEvent(
                    event="revision",
                    data=json.dumps({
                        "kind": "copyright_filter",
                        "revised_content": revised,
                    }),
                )
        except Exception as exc:
            logger.warning("copyright_filter_stream_failed", error=str(exc))

        # Append references section after streaming completes
        if sources:
            references = format_references(sources)
            yield StreamEvent.content("\n\n" + references)

        yield StreamEvent.done(
            conversation_id="",
            tokens_used=0,
            mode="basic",
            iterations=1,
        )

    async def _generate_response(
        self,
        query: str,
        documents: list[Any],
        llm: Any,
        request: RAGRequest,
    ) -> str:
        """Generate response without refinement (Basic mode)."""

        if not documents:
            return "No relevant documents found to answer your question."

        # Format context using utility function
        context = format_documents_for_prompt(documents)

        # Build user prompt with context
        template = f"""Based on the following research documents, please answer this question:

Question: {query}

Documents:
{context}

---

Instructions:
- Provide a comprehensive answer with clear sections
- Use markdown formatting (headings ##, bullet points -, minimal bold **)
- Base your answer on the documents provided
- Number of documents: {len(documents)}
- Unique sources: {len(set(get_doc_citation(d) for d in documents))}"""

        try:
            base_messages = [
                {"role": "system", "content": get_system_prompt()},
                {"role": "user", "content": template},
            ]
            messages = wrap_messages_for_chunks(
                base_messages=base_messages,
                chunks=documents,
                model=request.model,
                config=self.config,
            )
            response = await llm.complete(
                messages=messages,
                model=request.model,
                provider=request.provider,
                max_tokens=2000,
                temperature=0.3,
            )
            return response
        except Exception as e:
            logger.error("basic_response_generation_error", error=str(e))
            return f"Error generating response: {e}"

    async def _generate_response_from_context(
        self,
        query: str,
        context: str,
        llm: Any,
        request: RAGRequest,
        num_papers: int = 0,
        *,
        preamble: str | None = None,
    ) -> str:
        """Generate response from pre-formatted paper context."""
        template = f"""Based on the following research papers, please answer this question:

Question: {query}

{context}

---

Instructions:
- Provide a comprehensive answer with clear sections
- Use markdown formatting (headings ##, bullet points -, minimal bold **)
- Base your answer on the papers provided
- Number of papers: {num_papers}
- IMPORTANT: Extract ALL specific names, tools, methods, chemicals, organisms, or other entities mentioned in the papers that are relevant to the question. Do NOT say "specific names are not listed" if the papers contain them.
- Be specific and concrete — cite specific tools, software, methods, or findings by name rather than giving vague generalizations."""
        hist = format_conversation_block(getattr(request, "conversation_history", None))
        body = template
        if preamble:
            body = f"{preamble}\n\n{body}"
        user_content = build_user_message_with_history(history_block=hist, body=body)

        try:
            response = await llm.complete(
                messages=[
                    {"role": "system", "content": get_system_prompt()},
                    {"role": "user", "content": user_content},
                ],
                model=request.model,
                provider=request.provider,
                max_tokens=2000,
                temperature=0.15,
            )
            return response
        except Exception as e:
            logger.error("basic_response_generation_error", error=str(e))
            return f"Error generating response: {e}"
