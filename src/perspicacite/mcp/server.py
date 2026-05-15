"""MCP server implementation for Perspicacité v2.

Exposes scientific literature tools via the Model Context Protocol
so external agent systems (e.g., Mimosa-AI) can discover and use them.

Tools exposed:
- search_literature: Search academic databases
- get_paper_content: Fetch full text + structured sections
- get_paper_references: Extract cited references from a paper
- list_knowledge_bases: List all KBs
- search_knowledge_base: Semantic search within a KB
- create_knowledge_base: Create a new KB
- add_papers_to_kb: Add papers to a KB
- generate_report: Synthesize a research report from a KB
- screen_papers: Score candidate papers by relevance to a query
- add_dois_to_kb: Bulk-add papers to a KB from a list of DOIs
- push_to_zotero: Push DOIs to the configured Zotero library
- build_kbs_from_zotero: Build one KB per Zotero top-level collection
- build_kb_from_search: Search SciLEx, filter, fetch PDFs, ingest into a KB
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.mcp.server")

try:
    from fastmcp import Context, FastMCP

    mcp = FastMCP("perspicacite")
except ImportError:
    mcp = None
    Context = Any  # type: ignore[misc, assignment]


# =============================================================================
# Shared State
# =============================================================================


class MCPState:
    """Shared state object set during server startup.

    Provides MCP tools with access to Perspicacité internals
    (session store, vector store, embedding provider, config, etc.)
    without coupling to the web app module.
    """

    def __init__(self) -> None:
        self.session_store: Any = None
        self.vector_store: Any = None
        self.embedding_provider: Any = None
        self.config: Any = None
        self.llm_client: Any = None
        self.pdf_parser: Any = None
        self.tool_registry: Any = None
        self.provenance_store: Any = None
        # MCP doesn't run with the FastAPI JobRegistry; tools that need
        # one (fetch_paper_resources) fall back to a synchronous inline
        # registry when this is None.
        self.job_registry: Any = None
        self.initialized: bool = False

    async def initialize(self, config: Any) -> None:
        """Initialize all components from config."""
        if self.initialized:
            return

        from perspicacite.llm import AsyncLLMClient, LiteLLMEmbeddingProvider
        from perspicacite.memory.session_store import SessionStore
        from perspicacite.pipeline.parsers.pdf import PDFParser
        from perspicacite.retrieval import ChromaVectorStore
        from pathlib import Path

        self.config = config

        # LLM client
        self.llm_client = AsyncLLMClient(config.llm)

        # Embedding provider
        self.embedding_provider = LiteLLMEmbeddingProvider(
            model=config.knowledge_base.embedding_model,
        )

        # Vector store
        self.vector_store = ChromaVectorStore(
            persist_dir="./chroma_db",
            embedding_provider=self.embedding_provider,
        )

        # Session store
        db_path = Path("./data/perspicacite.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_store = SessionStore(db_path)
        await self.session_store.init_db()

        # Provenance store (shares the same DB as the session store)
        from perspicacite.provenance.store import ProvenanceStore

        sidecar_dir = Path("./data/provenance")
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        self.provenance_store = ProvenanceStore(db_path=db_path, sidecar_dir=sidecar_dir)

        # PDF parser
        self.pdf_parser = PDFParser()

        # Tool registry for RAG engine
        from perspicacite.rag.tools import ToolRegistry

        self.tool_registry = ToolRegistry()

        self.initialized = True
        logger.info("mcp_state_initialized")


mcp_state = MCPState()


# =============================================================================
# Helper: JSON response builder
# =============================================================================


def _json_ok(data: dict[str, Any]) -> str:
    """Build a success JSON response."""
    return json.dumps({"success": True, **data}, ensure_ascii=False, default=str)


def _normalize_paper_id(paper_id: str) -> str:
    """Strip the ``doi:`` prefix some surfaces use, so lookups match the
    bare-DOI form stored in the KB. ``doi:10.1038/x`` → ``10.1038/x``.
    """
    return paper_id[4:] if paper_id.startswith("doi:") else paper_id


def _json_error(message: str, **extra: Any) -> str:
    """Build an error JSON response."""
    return json.dumps({"success": False, "error": message, **extra}, default=str)


def _require_state() -> MCPState | str:
    """Check that MCP state is initialized. Returns state or error string."""
    if not mcp_state.initialized:
        return _json_error("MCP server not initialized")
    return mcp_state


# =============================================================================
# Tool 1: search_literature
# =============================================================================


@mcp.tool()
async def search_literature(
    query: str,
    max_results: int = 20,
    year_min: int | None = None,
    year_max: int | None = None,
    article_type: str | None = None,
    databases: list[str] | None = None,
) -> str:
    """
    Search academic databases for scientific papers matching a query.

    Args:
        query: Search query (keywords, phrases, or natural language)
        max_results: Maximum number of results to return (1-50)
        year_min: Earliest publication year (inclusive)
        year_max: Latest publication year (inclusive)
        article_type: Filter by type ("review", "article", "conference")
        databases: Databases to search. Options: semantic_scholar, openalex, pubmed, arxiv

    Returns:
        JSON with list of papers including title, authors, year, doi, abstract.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    from perspicacite.search.scilex_adapter import SciLExAdapter

    try:
        adapter = SciLExAdapter()
        if not adapter.available:
            # SciLEx is an optional extra; without it, this tool has no
            # backend. Tell the caller how to install it instead of
            # silently returning zero results.
            return _json_error(
                "SciLEx (multi-DB search aggregator) is not installed. "
                "Install with: `uv pip install -e \".[scilex]\"` from the "
                "Perspicacité repo. Or skip and use search_knowledge_base / "
                "generate_report on a pre-ingested KB instead.",
                scilex_available=False,
            )
        papers = await adapter.search(
            query=query,
            max_results=max_results,
            year_min=year_min,
            year_max=year_max,
            apis=databases or ["semantic_scholar", "openalex", "pubmed"],
            article_type=article_type,
        )

        # Convert Paper models to dicts
        results = []
        for p in papers:
            pd = {
                "id": p.id,
                "title": p.title,
                "year": p.year,
                "doi": p.doi,
                "abstract": p.abstract,
                "journal": p.journal,
                "citation_count": p.citation_count,
                "source": str(p.source) if p.source else None,
                "url": p.url,
            }
            if p.authors:
                pd["authors"] = [
                    a.family if hasattr(a, "family") and a.family else str(a) for a in p.authors
                ]
            results.append(pd)

        logger.info("mcp_search_literature", query=query, results=len(results))
        return _json_ok({"query": query, "total_results": len(results), "papers": results})

    except Exception as e:
        logger.error("mcp_search_literature_error", error=str(e))
        return _json_error(f"Search failed: {e}")


# =============================================================================
# Tool 2: get_paper_content
# =============================================================================


@mcp.tool()
async def get_paper_content(
    doi: str,
    include_sections: bool = True,
) -> str:
    """
    Fetch full text and structured sections for a paper by DOI.

    Uses a unified pipeline: discovers sources via OpenAlex/Unpaywall, then
    tries PMC JATS XML, arXiv HTML, publisher PDF, and abstract in priority order.

    Args:
        doi: Paper DOI (e.g., "10.1234/example")
        include_sections: Whether to include section breakdowns

    Returns:
        JSON with content_type, full_text_length, sections, and references.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    import httpx
    from perspicacite.pipeline.download import retrieve_paper_content

    try:
        pdf_config = state.config.pdf_download
        pdf_kwargs: dict[str, Any] = {}
        if pdf_config:
            pdf_kwargs = {
                "unpaywall_email": pdf_config.unpaywall_email,
                "alternative_endpoint": pdf_config.alternative_endpoint,
                "wiley_tdm_token": pdf_config.wiley_tdm_token,
                "aaas_api_key": pdf_config.aaas_api_key,
                "rsc_api_key": pdf_config.rsc_api_key,
                "springer_api_key": pdf_config.springer_api_key,
            }

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            result = await retrieve_paper_content(
                doi,
                http_client=client,
                pdf_parser=state.pdf_parser,
                **pdf_kwargs,
            )

        if result.success and result.content_type in ("structured", "full_text"):
            resp: dict[str, Any] = {
                "doi": doi,
                "content_type": result.content_type,
                "content_source": result.content_source,
                "full_text_length": len(result.full_text or ""),
            }
            if include_sections and result.sections:
                resp["sections"] = result.sections
            if result.references:
                resp["references_count"] = len(result.references)
                resp["references"] = result.references
            return _json_ok(resp)

        if result.content_type == "abstract":
            return _json_ok(
                {
                    "doi": doi,
                    "content_type": "abstract",
                    "content_source": result.content_source,
                    "abstract": result.abstract,
                    "note": "Full text not available; returning abstract only",
                }
            )

        return _json_error(f"Could not retrieve content for DOI: {doi}")

    except Exception as e:
        logger.error("mcp_get_paper_content_error", doi=doi, error=str(e))
        return _json_error(f"Content retrieval failed: {e}")


# =============================================================================
# Tool 3: get_paper_references
# =============================================================================


@mcp.tool()
async def get_paper_references(
    doi: str,
) -> str:
    """
    Get the list of cited references from a paper.

    Extracts references from JATS XML when available via PMC Open Access.
    Falls back to discovery metadata for non-PMC papers.

    Args:
        doi: Paper DOI

    Returns:
        JSON with list of referenced papers (doi, title, authors, year).
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    import httpx

    try:
        from perspicacite.pipeline.download.unified import _load_cached_references

        # Try loading cached refs first (from a previous content fetch)
        refs = _load_cached_references(doi)
        if refs:
            return _json_ok({"doi": doi, "references": refs, "total": len(refs)})

        # No cached refs — fetch content through unified pipeline to populate cache
        from perspicacite.pipeline.download import retrieve_paper_content

        pdf_config = getattr(state.config, "pdf_download", None)
        pdf_kwargs = {}
        if pdf_config:
            for key in (
                "unpaywall_email",
                "wiley_tdm_token",
                "elsevier_api_key",
                "aaas_api_key",
                "rsc_api_key",
                "springer_api_key",
            ):
                val = getattr(pdf_config, key, None)
                if val:
                    pdf_kwargs[key] = val

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            await retrieve_paper_content(
                doi,
                http_client=client,
                pdf_parser=state.pdf_parser,
                **pdf_kwargs,
            )

        # Try cache again after content fetch
        refs = _load_cached_references(doi)
        if refs:
            return _json_ok({"doi": doi, "references": refs, "total": len(refs)})

        return _json_ok(
            {
                "doi": doi,
                "references": [],
                "total": 0,
                "note": "References not available — JATS XML extraction only works for PMC Open Access papers",
            }
        )

    except Exception as e:
        logger.error("mcp_get_paper_references_error", doi=doi, error=str(e))
        return _json_error(f"Reference retrieval failed: {e}")


# =============================================================================
# Tool 4: list_knowledge_bases (DEFERRED: get_citation_network)
# =============================================================================


@mcp.tool()
async def list_knowledge_bases() -> str:
    """
    List all available knowledge bases.

    Returns:
        JSON with list of KBs including name, description, paper/chunk counts.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        kbs = await state.session_store.list_kbs()
        result = []
        for kb in kbs:
            result.append(
                {
                    "name": kb.name,
                    "description": kb.description,
                    "paper_count": kb.paper_count,
                    "chunk_count": kb.chunk_count,
                    "created_at": str(kb.created_at) if hasattr(kb, "created_at") else None,
                }
            )
        return _json_ok({"knowledge_bases": result})
    except Exception as e:
        logger.error("mcp_list_kbs_error", error=str(e))
        return _json_error(f"Failed to list KBs: {e}")


# =============================================================================
# Tool 5: search_knowledge_base
# =============================================================================


@mcp.tool()
async def search_knowledge_base(
    query: str,
    kb_name: str = "default",
    top_k: int = 5,
    kb_names: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """
    Search within a specific knowledge base (or multiple KBs) using semantic similarity.

    Args:
        query: Search query
        kb_name: Knowledge base name (single-KB path)
        top_k: Number of top results to return
        kb_names: Optional list of KBs to query together. All KBs must share the same
            embedding model. When provided and len > 1, supersedes kb_name and returns
            chunks tagged with their source KB. When exactly 1 entry, treated as
            single KB via kb_name.
        year_min: Restrict to papers published in or after this year (inclusive).
        year_max: Restrict to papers published in or before this year (inclusive).

    Returns:
        JSON with matching chunks including paper_id, chunk_text, relevance_score,
        and (in multi-KB mode) kb_name per chunk.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        # Multi-KB path
        if kb_names and len(kb_names) > 1:
            if year_min is not None or year_max is not None:
                logger.warning(
                    "search_kb_multi_year_filter_ignored",
                    year_min=year_min, year_max=year_max,
                    note="multi-KB filter passthrough is a Wave 4.2 followup",
                )
            from perspicacite.retrieval.multi_kb import MultiKBRetriever, check_embedding_compat

            metas = [await state.session_store.get_kb_metadata(n) for n in kb_names]
            for i, meta in enumerate(metas):
                if meta is None:
                    return _json_error(f"Knowledge base not found: {kb_names[i]}")
            compat_msg = check_embedding_compat(metas)
            if compat_msg:
                return _json_error(compat_msg)

            retr = MultiKBRetriever(
                vector_store=state.vector_store,
                embedding_service=state.embedding_provider,
                kb_metas=metas,
            )
            results = await retr.search(query, top_k=top_k)

            chunks = []
            for r in results:
                meta_obj = r.get("metadata")
                meta_dict = meta_obj.__dict__ if hasattr(meta_obj, "__dict__") else (meta_obj or {})
                chunks.append(
                    {
                        "paper_id": r.get("paper_id"),
                        "title": meta_dict.get("title") if isinstance(meta_dict, dict) else None,
                        "section": meta_dict.get("section")
                        if isinstance(meta_dict, dict)
                        else None,
                        "chunk_text": r.get("text", ""),
                        "relevance_score": r.get("score"),
                        "doi": meta_dict.get("doi") if isinstance(meta_dict, dict) else None,
                        "kb_name": r.get("kb_name"),
                    }
                )

            return _json_ok(
                {
                    "query": query,
                    "kb_names": kb_names,
                    "results": chunks,
                }
            )

        # Single-KB path (original behaviour, unchanged)
        effective_kb_name = kb_names[0] if (kb_names and len(kb_names) == 1) else kb_name

        from perspicacite.models.kb import chroma_collection_name_for_kb
        from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig

        collection_name = chroma_collection_name_for_kb(effective_kb_name)

        # Verify KB exists
        kb_meta = await state.session_store.get_kb_metadata(effective_kb_name)
        if not kb_meta:
            return _json_error(f"Knowledge base '{effective_kb_name}' not found")

        dkb = DynamicKnowledgeBase(
            state.vector_store,
            state.embedding_provider,
            config=KnowledgeBaseConfig(
                vector_size=state.embedding_provider.dimension,
            ),
        )
        dkb.collection_name = collection_name
        dkb._initialized = True

        # Build year-bounded filters (Wave 4.2).
        from perspicacite.models.search import SearchFilters
        filters = None
        if year_min is not None or year_max is not None:
            filters = SearchFilters(year_min=year_min, year_max=year_max)

        results = await dkb.search(query, top_k=top_k, filters=filters)

        chunks = []
        for r in results:
            meta = r.metadata if hasattr(r, "metadata") else {}
            chunks.append(
                {
                    "paper_id": meta.get("paper_id"),
                    "title": meta.get("title"),
                    "section": meta.get("section"),
                    "chunk_text": r.text if hasattr(r, "text") else str(r),
                    "relevance_score": r.score if hasattr(r, "score") else None,
                    "doi": meta.get("doi"),
                }
            )

        return _json_ok(
            {
                "query": query,
                "kb_name": effective_kb_name,
                "results": chunks,
            }
        )

    except Exception as e:
        logger.error("mcp_search_kb_error", kb_name=kb_name, error=str(e))
        return _json_error(f"KB search failed: {e}")


# =============================================================================
# Tool 6: create_knowledge_base
# =============================================================================


@mcp.tool()
async def create_knowledge_base(
    name: str,
    description: str = "",
) -> str:
    """
    Create a new empty knowledge base.

    Args:
        name: KB name (alphanumeric, hyphens, underscores)
        description: Optional description

    Returns:
        JSON with created KB details.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        from perspicacite.models.kb import (
            ChunkConfig,
            KnowledgeBase,
            chroma_collection_name_for_kb,
        )

        collection_name = chroma_collection_name_for_kb(name)

        # Check for duplicate
        existing = await state.session_store.get_kb_metadata(name)
        if existing:
            return _json_error(f"Knowledge base '{name}' already exists")

        # Create ChromaDB collection
        await state.vector_store.create_collection(collection_name)

        # Save metadata
        kb = KnowledgeBase(
            name=name,
            description=description or f"Created via MCP",
            collection_name=collection_name,
            embedding_model=state.embedding_provider.model_name,
            chunk_config=ChunkConfig(
                chunk_size=state.config.knowledge_base.chunk_size,
                chunk_overlap=state.config.knowledge_base.chunk_overlap,
            ),
        )
        await state.session_store.save_kb_metadata(kb)

        logger.info("mcp_create_kb", name=name)
        return _json_ok(
            {
                "name": name,
                "description": kb.description,
                "collection_name": collection_name,
                "paper_count": 0,
                "chunk_count": 0,
            }
        )

    except Exception as e:
        logger.error("mcp_create_kb_error", name=name, error=str(e))
        return _json_error(f"KB creation failed: {e}")


# =============================================================================
# Tool 7: add_papers_to_kb
# =============================================================================


@mcp.tool()
async def add_papers_to_kb(
    kb_name: str,
    papers: list[dict],
) -> str:
    """
    Add papers to a knowledge base with automatic PDF download and indexing.

    Each paper dict should have at least 'title' and optionally 'doi', 'year',
    'authors', 'abstract', 'citations'.

    Args:
        kb_name: Target knowledge base name
        papers: List of paper dicts to add

    Returns:
        JSON with counts of added/skipped papers and PDF download stats.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    try:
        import hashlib
        import httpx
        from perspicacite.models.kb import chroma_collection_name_for_kb
        from perspicacite.models.papers import Author, Paper, PaperSource
        from perspicacite.pipeline.download.fallback import get_pdf_with_fallback
        from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig

        collection_name = chroma_collection_name_for_kb(kb_name)
        kb_meta = await state.session_store.get_kb_metadata(kb_name)
        if not kb_meta:
            return _json_error(f"Knowledge base '{kb_name}' not found")

        # Convert paper dicts to Paper models
        paper_models: list[Paper] = []
        for pd in papers:
            paper_id = pd.get("doi") or hashlib.md5(pd.get("title", "").encode()).hexdigest()[:12]

            authors = []
            for a in pd.get("authors", []):
                if isinstance(a, str):
                    authors.append(Author(family=a, given="", name=a))
                elif isinstance(a, dict):
                    authors.append(
                        Author(
                            family=a.get("family", ""),
                            given=a.get("given", ""),
                            name=a.get("name", ""),
                        )
                    )

            paper = Paper(
                id=paper_id,
                title=pd.get("title", ""),
                authors=authors,
                year=pd.get("year"),
                doi=pd.get("doi"),
                abstract=pd.get("abstract"),
                citation_count=pd.get("citations"),
                journal=pd.get("journal"),
                url=pd.get("url"),
                pdf_url=pd.get("pdf_url"),
                source=PaperSource.WEB_SEARCH,
                keywords=pd.get("keywords", []),
                metadata=pd.get("metadata", {}),
            )
            paper_models.append(paper)

        # Download PDFs
        pdf_stats = {"attempted": 0, "success": 0, "failed": 0}
        pdf_config = state.config.pdf_download
        pdf_kwargs: dict[str, Any] = {}
        if pdf_config:
            pdf_kwargs = {
                "unpaywall_email": pdf_config.unpaywall_email,
                "alternative_endpoint": pdf_config.alternative_endpoint,
                "wiley_tdm_token": pdf_config.wiley_tdm_token,
                "aaas_api_key": pdf_config.aaas_api_key,
                "rsc_api_key": pdf_config.rsc_api_key,
                "springer_api_key": pdf_config.springer_api_key,
            }

        from perspicacite.pipeline.download import retrieve_paper_content

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            for paper in paper_models:
                if not paper.doi:
                    continue
                pdf_stats["attempted"] += 1
                try:
                    result = await retrieve_paper_content(
                        paper.doi,
                        url=paper.url,
                        http_client=client,
                        pdf_parser=state.pdf_parser,
                        **pdf_kwargs,
                    )
                    if result.success and result.full_text:
                        paper.full_text = result.full_text
                        pdf_stats["success"] += 1
                        continue
                    pdf_stats["failed"] += 1
                except Exception:
                    pdf_stats["failed"] += 1

        # Add to vector store
        dkb_config = KnowledgeBaseConfig(
            vector_size=state.embedding_provider.dimension,
            chunk_size=state.config.knowledge_base.chunk_size,
            chunk_overlap=state.config.knowledge_base.chunk_overlap,
            chunking_method=state.config.knowledge_base.chunking_method,
        )
        dkb = DynamicKnowledgeBase(
            state.vector_store,
            state.embedding_provider,
            config=dkb_config,
        )
        dkb.collection_name = collection_name
        dkb._initialized = True

        chunks_added = await dkb.add_papers(paper_models, include_full_text=True)

        # Update metadata
        kb_meta.paper_count = (kb_meta.paper_count or 0) + len(paper_models)
        kb_meta.chunk_count = (kb_meta.chunk_count or 0) + chunks_added
        await state.session_store.save_kb_metadata(kb_meta)

        logger.info(
            "mcp_add_papers",
            kb_name=kb_name,
            papers=len(paper_models),
            chunks=chunks_added,
        )

        return _json_ok(
            {
                "kb_name": kb_name,
                "added_papers": len(paper_models),
                "added_chunks": chunks_added,
                "pdf_download": pdf_stats,
            }
        )

    except Exception as e:
        logger.error("mcp_add_papers_error", kb_name=kb_name, error=str(e))
        return _json_error(f"Failed to add papers: {e}")


# =============================================================================
# Tool 8: generate_report
# =============================================================================


@mcp.tool()
async def generate_report(
    query: str,
    kb_name: str = "default",
    mode: str = "advanced",
    max_papers: int = 10,
    recency_weight: float = 0.0,
    kb_names: list[str] | None = None,
    ctx: Context | None = None,
) -> str:
    """
    Generate a synthesized research report from a knowledge base.

    Uses Perspicacité's RAG pipeline (retrieval + LLM synthesis) to answer
    a research question using papers in the specified KB.

    Args:
        query: Research question to answer
        kb_name: Knowledge base to query (single-KB path)
        kb_names: Optional list of KBs to query together. All KBs must share the same
            embedding model. When provided and len > 1, supersedes kb_name.
            When exactly 1 entry, treated as single KB via kb_name.
        mode: RAG mode - "basic" (fast), "advanced" (query expansion), "profound" (multi-cycle),
            or "contradiction" (agreement/disagreement analysis)
        max_papers: Maximum papers to reference in the report
        recency_weight: Optional recency bias (0.0 = disabled, 1.0 = full recency). When > 0,
            retrieved chunks are re-scored toward more recent papers using exponential decay.

    Returns:
        JSON with the report text, cited sources, and metadata.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    # Bind ctx for any nested LLM call via sampling. We use the
    # contextvar token directly here (rather than the `with` form) to
    # avoid re-indenting this tool's large body.
    from perspicacite.llm.mcp_sampling import _mcp_ctx as _sampling_ctx
    _sampling_token = _sampling_ctx.set(ctx) if ctx is not None else None
    try:
        message_id = str(uuid.uuid4())

        from perspicacite.models.rag import RAGMode, RAGRequest
        from perspicacite.rag.engine import RAGEngine

        # Resolve effective kb_name / kb_names
        effective_kb_name = kb_name
        effective_kb_names: list[str] | None = None

        if kb_names and len(kb_names) > 1:
            from perspicacite.retrieval.multi_kb import check_embedding_compat

            metas = [await state.session_store.get_kb_metadata(n) for n in kb_names]
            for i, meta in enumerate(metas):
                if meta is None:
                    return _json_error(f"Knowledge base not found: {kb_names[i]}")
            compat_msg = check_embedding_compat(metas)
            if compat_msg:
                return _json_error(compat_msg)
            effective_kb_names = kb_names
        elif kb_names and len(kb_names) == 1:
            effective_kb_name = kb_names[0]

        if effective_kb_names is None:
            # Single-KB: verify it exists
            kb_meta = await state.session_store.get_kb_metadata(effective_kb_name)
            if not kb_meta:
                return _json_error(f"Knowledge base '{effective_kb_name}' not found")

        engine = RAGEngine(
            llm_client=state.llm_client,
            vector_store=state.vector_store,
            embedding_provider=state.embedding_provider,
            tool_registry=state.tool_registry,
            config=state.config,
        )
        engine.provenance_store = getattr(state, "provenance_store", None)

        # Collect full response from streaming generator
        report_text = ""
        sources: list[dict] = []

        mode_map = {
            "basic": RAGMode.BASIC,
            "advanced": RAGMode.ADVANCED,
            "profound": RAGMode.PROFOUND,
            "contradiction": RAGMode.CONTRADICTION,
        }
        rag_mode = mode_map.get(mode, RAGMode.ADVANCED)

        # Resolve provider/model from server-side config so MCP respects
        # llm.default_provider / llm.default_model rather than the
        # hard-coded RAGRequest defaults (deepseek). Matches the fix in
        # web/routers/chat.py::_stream_rag_mode.
        default_provider = "deepseek"
        default_model = "deepseek-chat"
        cfg_llm = getattr(state, "config", None)
        if cfg_llm is not None and getattr(cfg_llm, "llm", None) is not None:
            default_provider = cfg_llm.llm.default_provider or default_provider
            default_model = cfg_llm.llm.default_model or default_model

        rag_request = RAGRequest(
            query=query,
            kb_name=effective_kb_name,
            kb_names=effective_kb_names,
            mode=rag_mode,
            recency_weight=recency_weight if recency_weight > 0 else None,
            provider=default_provider,
            model=default_model,
        )

        async for event in engine.query_stream(rag_request, message_id=message_id):
            if event.event == "content":
                import json as _json

                payload = _json.loads(event.data)
                report_text += payload.get("delta", "")
            elif event.event == "source":
                import json as _json

                src = _json.loads(event.data)
                sources.append(
                    {
                        "title": src.get("title"),
                        "authors": src.get("authors"),
                        "year": src.get("year"),
                        "doi": src.get("doi"),
                        "relevance_score": src.get("relevance_score"),
                        "section": src.get("section"),
                        "kb_name": src.get("kb_name"),
                    }
                )

        logger.info("mcp_generate_report", query=query, kb_name=effective_kb_name, mode=mode)

        return _json_ok(
            {
                "query": query,
                "kb_name": effective_kb_name,
                "kb_names": effective_kb_names,
                "mode": mode,
                "report": report_text,
                "sources": sources,
                "papers_used": len(sources),
                "message_id": message_id,
            }
        )

    except Exception as e:
        logger.error("mcp_generate_report_error", query=query, error=str(e))
        return _json_error(f"Report generation failed: {e}")
    finally:
        if _sampling_token is not None:
            _sampling_ctx.reset(_sampling_token)


# =============================================================================
# Tool 9: screen_papers
# =============================================================================


@mcp.tool()
async def screen_papers(
    candidates: list[str] | list[dict],
    query: str,
    method: str = "bm25",
    threshold: float = 0.3,
    max_results: int = 50,
    ctx: Context | None = None,
) -> str:
    """Score candidate papers by relevance to a research query.

    Each item in ``candidates`` may be either:
      - A plain **string** — a DOI (e.g. "10.1038/nature12373") or a paper
        title. DOIs trigger an OA-content lookup so the abstract can be
        used in scoring.
      - A **dict** — {"doi": "...", "title": "...", "abstract": "..."}.
        Use this when you already have the abstract and want to skip the
        DOI lookup.

    Args:
        candidates: Strings (DOIs or titles) OR dicts with doi/title/abstract.
        query: The research query / topic to screen against.
        method: "bm25" (fast, no LLM) or "llm" (LLM-rated 0-1 with one-line reasons).
        threshold: Keep papers scoring >= this value (0..1).
        max_results: Cap on the number of returned items.

    Returns:
        JSON with keys: query, method, screened (list of
        doi/title/score/kept/reason).
    """
    state = _require_state()
    if isinstance(state, str):
        return state
    try:
        import httpx
        from perspicacite.pipeline.download import retrieve_paper_content
        from perspicacite.search.screening import screen_papers as _bm25
        from perspicacite.search.screening import screen_papers_llm as _llm

        items: list[dict] = []
        # Split dicts (already have metadata) from strings (need lookup).
        dicts_already = [c for c in candidates if isinstance(c, dict)]
        strings_only = [c for c in candidates if isinstance(c, str)]

        # Accept user-supplied metadata dicts as-is.
        for d in dicts_already:
            items.append({
                "doi": (d.get("doi") or "").strip().replace("https://doi.org/", "") or None,
                "title": d.get("title") or d.get("doi") or "(untitled)",
                "abstract": d.get("abstract") or "",
            })

        # Only spin up an HTTP client if at least one string looks like a DOI.
        doi_like = [c for c in strings_only if c.strip().lower().startswith("10.") or "doi.org/" in c]
        if doi_like:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                for c in strings_only:
                    if c.strip().lower().startswith("10.") or "doi.org/" in c:
                        doi = c.strip().replace("https://doi.org/", "")
                        try:
                            r = await retrieve_paper_content(
                                doi, http_client=client, pdf_parser=None
                            )
                            md = r.metadata or {}
                            items.append(
                                {
                                    "doi": doi,
                                    "title": md.get("title") or doi,
                                    "abstract": r.abstract or md.get("abstract") or "",
                                }
                            )
                        except Exception:
                            items.append({"doi": doi, "title": doi, "abstract": ""})
                    else:
                        items.append({"title": c, "abstract": ""})
        else:
            items.extend([{"title": c, "abstract": ""} for c in strings_only])

        if method == "llm":
            from perspicacite.llm.client import resolve_stage_model
            from perspicacite.llm.mcp_sampling import use_mcp_context
            sp, sm = resolve_stage_model(state.config, "screening")
            with use_mcp_context(ctx):
                results = await _llm(
                    items, query=query, llm=state.llm_client,
                    threshold=threshold, model=sm, provider=sp,
                )
        else:
            results = _bm25(items, reference=query, method="bm25", threshold=threshold)

        screened = []
        for r in results[:max_results]:
            entry: dict = {"score": r.score, "kept": r.kept, "reason": r.reason}
            if r.item.get("doi"):
                entry["doi"] = r.item["doi"]
            entry["title"] = r.item.get("title")
            screened.append(entry)
        logger.info(
            "mcp_screen_papers",
            n=len(candidates),
            method=method,
            kept=sum(e["kept"] for e in screened),
        )
        return _json_ok({"query": query, "method": method, "screened": screened})
    except Exception as e:
        logger.error("mcp_screen_papers_error", error=str(e))
        return _json_error(f"Screening failed: {e}")


# =============================================================================
# Tool 10: add_dois_to_kb
# =============================================================================


@mcp.tool()
async def add_dois_to_kb(
    kb_name: str,
    dois: list[str],
) -> str:
    """
    Bulk-add papers to a knowledge base from a list of DOIs.

    For each DOI the tool fetches full text via the unified download pipeline,
    deduplicates against existing KB content, and indexes the result.

    Args:
        kb_name: Target knowledge base name
        dois: List of DOIs to add (max 200 per call)

    Returns:
        JSON with added_papers, added_chunks, skipped_duplicates, failed, pdf_download stats.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    if len(dois) > 200:
        return _json_error("At most 200 DOIs per request")

    try:
        import httpx
        from perspicacite.models.kb import chroma_collection_name_for_kb
        from perspicacite.models.papers import Author, Paper, PaperSource
        from perspicacite.pipeline.download import retrieve_paper_content
        from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase

        kb_meta = await state.session_store.get_kb_metadata(kb_name)
        if not kb_meta:
            return _json_error(f"Knowledge base '{kb_name}' not found")

        collection_name = chroma_collection_name_for_kb(kb_name)

        pdf_config = state.config.pdf_download
        pdf_kwargs: dict[str, Any] = {}
        cookies_path: str | None = None
        if pdf_config:
            pdf_kwargs = {
                "unpaywall_email": pdf_config.unpaywall_email,
                "alternative_endpoint": pdf_config.alternative_endpoint,
                "wiley_tdm_token": pdf_config.wiley_tdm_token,
                "aaas_api_key": pdf_config.aaas_api_key,
                "rsc_api_key": pdf_config.rsc_api_key,
                "springer_api_key": pdf_config.springer_api_key,
            }
            if pdf_config.cache_pdfs:
                pdf_kwargs["pdf_cache_dir"] = pdf_config.cache_dir
            cookies_path = pdf_config.cookies_path

        papers_to_add: list[Paper] = []
        skipped: list[dict] = []
        failed: list[dict] = []
        dl: dict[str, int] = {"attempted": 0, "success": 0, "failed": 0}

        from perspicacite.pipeline.download.cookies import (
            build_authenticated_client,
        )

        async with build_authenticated_client(cookies_path=cookies_path) as client:
            for raw_doi in dois:
                doi = (raw_doi or "").strip().replace("https://doi.org/", "")
                if not doi:
                    continue

                if await state.vector_store.paper_exists(collection_name, doi):
                    skipped.append({"doi": doi})
                    continue

                dl["attempted"] += 1
                try:
                    result = await retrieve_paper_content(
                        doi,
                        http_client=client,
                        pdf_parser=state.pdf_parser,
                        **pdf_kwargs,
                    )
                except Exception as e:
                    failed.append({"doi": doi, "reason": str(e)})
                    dl["failed"] += 1
                    continue

                if not result or not result.success:
                    failed.append({"doi": doi, "reason": "no content"})
                    dl["failed"] += 1
                    continue

                md = result.metadata or {}
                paper = Paper(
                    id=doi,
                    title=md.get("title") or f"Reference {doi}",
                    authors=[Author(name=a) for a in (md.get("authors") or [])],
                    year=md.get("year"),
                    doi=doi,
                    abstract=result.abstract or md.get("abstract"),
                    journal=md.get("journal"),
                    source=PaperSource.WEB_SEARCH,
                )
                if result.full_text:
                    paper.full_text = result.full_text
                    dl["success"] += 1
                else:
                    dl["failed"] += 1
                papers_to_add.append(paper)

        if not papers_to_add:
            return _json_ok(
                {
                    "kb_name": kb_name,
                    "added_papers": 0,
                    "added_chunks": 0,
                    "skipped_duplicates": len(skipped),
                    "failed": failed,
                    "pdf_download": dl,
                }
            )

        dkb = DynamicKnowledgeBase(
            state.vector_store,
            state.embedding_provider,
        )
        dkb.collection_name = collection_name
        dkb._initialized = True

        chunks_added = await dkb.add_papers(papers_to_add, include_full_text=True)

        kb_meta.paper_count = (kb_meta.paper_count or 0) + len(papers_to_add)
        kb_meta.chunk_count = (kb_meta.chunk_count or 0) + chunks_added
        await state.session_store.save_kb_metadata(kb_meta)

        logger.info(
            "mcp_add_dois_to_kb",
            kb_name=kb_name,
            papers=len(papers_to_add),
            chunks=chunks_added,
        )

        return _json_ok(
            {
                "kb_name": kb_name,
                "added_papers": len(papers_to_add),
                "added_chunks": chunks_added,
                "skipped_duplicates": len(skipped),
                "failed": failed,
                "pdf_download": dl,
            }
        )

    except Exception as e:
        logger.error("mcp_add_dois_to_kb_error", kb_name=kb_name, error=str(e))
        return _json_error(f"Failed to add DOIs: {e}")


# =============================================================================
# Tool 11: push_to_zotero
# =============================================================================


@mcp.tool()
async def push_to_zotero(
    dois: list[str] | str,
    attach_pdf: bool = False,
    attach_supplementary: bool = False,
) -> str:
    """Push one or more DOIs to the configured Zotero library.

    Fetches metadata via the unified pipeline and calls
    :meth:`ZoteroClient.create_item` for each DOI. Skips duplicates
    automatically (ZoteroClient checks by DOI before creating).

    Optionally attaches the cached PDF and/or supplementary files. The
    PDF is sourced from ``pdf_download.cache_dir`` (see ``cache_pdfs``);
    if no cached PDF exists we trigger a fetch via the unified pipeline
    so the upload has something to attach. Supplementary attachment
    requires the paper to already have a capsule with downloaded SI.

    Cloud-only — the local desktop API rejects writes and attachment
    upload via the documented 3-step protocol. Group libraries also
    require the cloud API.

    Args:
        dois: A single DOI string or a list of DOIs (max 100 per call).
        attach_pdf: If True, upload the cached PDF as a child attachment.
        attach_supplementary: If True, upload any
            ``data/capsules/<paper_id>/supplementary/files/*`` as
            additional child attachments (only files already on disk).

    Returns:
        JSON ``{"created": [...], "skipped": [], "failed": [...]}`` where
        each ``created`` entry includes ``{"doi", "key",
        "attached_pdf"?, "attached_supplementary"?}``.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    cfg = getattr(state.config, "zotero", None)
    if cfg is None or not cfg.enabled or not cfg.api_key or not cfg.library_id:
        return _json_error("zotero_not_configured")

    if isinstance(dois, str):
        dois = [dois]
    if len(dois) > 100:
        return _json_error("at most 100 DOIs per call")

    pdf_config = state.config.pdf_download
    cache_dir = pdf_config.cache_dir if (pdf_config and pdf_config.cache_pdfs) else None
    cookies_path = pdf_config.cookies_path if pdf_config else None

    try:
        from perspicacite.integrations.zotero import ZoteroClient
        from perspicacite.pipeline.download import retrieve_paper_content
        from perspicacite.pipeline.download.cookies import (
            build_authenticated_client,
        )
        from perspicacite.pipeline.download.pdf_cache import (
            cached_pdf_path,
        )

        created: list[dict] = []
        failed: list[dict] = []

        async with build_authenticated_client(cookies_path=cookies_path) as http_client:
            zotero = ZoteroClient(
                api_key=cfg.api_key,
                library_id=cfg.library_id,
                library_type=cfg.library_type,
                collection_key=cfg.collection_key,
                base_url=getattr(cfg, "base_url", "") or None,
                http_client=http_client,
            )
            for raw_doi in dois:
                doi = (raw_doi or "").strip().replace("https://doi.org/", "")
                if not doi:
                    continue
                try:
                    # Step 1: metadata-only fetch to know what to write.
                    content = await retrieve_paper_content(
                        doi,
                        http_client=http_client,
                        pdf_parser=None,  # metadata-only here
                    )
                    paper: dict[str, Any] = dict(content.metadata or {})
                    paper["doi"] = doi
                    paper["abstract"] = content.abstract or paper.get("abstract")
                    key = await zotero.create_item(paper)
                    if not key:
                        failed.append({"doi": doi, "reason": "no key returned"})
                        continue
                    entry: dict[str, Any] = {"doi": doi, "key": key}

                    # Step 2 (optional): PDF attachment.
                    if attach_pdf:
                        pdf_path = (
                            cached_pdf_path(doi, cache_dir) if cache_dir else None
                        )
                        if pdf_path is None and state.pdf_parser is not None:
                            # Trigger a full fetch (which also populates the
                            # cache for next time) before uploading.
                            await retrieve_paper_content(
                                doi,
                                http_client=http_client,
                                pdf_parser=state.pdf_parser,
                                unpaywall_email=pdf_config.unpaywall_email,
                                wiley_tdm_token=pdf_config.wiley_tdm_token,
                                aaas_api_key=pdf_config.aaas_api_key,
                                rsc_api_key=pdf_config.rsc_api_key,
                                springer_api_key=pdf_config.springer_api_key,
                                pdf_cache_dir=cache_dir,
                            )
                            pdf_path = (
                                cached_pdf_path(doi, cache_dir) if cache_dir else None
                            )
                        if pdf_path is not None:
                            try:
                                att_key = await zotero.upload_attachment(
                                    parent_item_key=key,
                                    file_path=str(pdf_path),
                                    filename=pdf_path.name,
                                    content_type="application/pdf",
                                )
                                entry["attached_pdf"] = bool(att_key)
                            except Exception as exc:
                                entry["pdf_attach_error"] = str(exc)
                        else:
                            entry["attached_pdf"] = False
                            entry["pdf_attach_error"] = "no PDF available"

                    # Step 3 (optional): supplementary attachments from capsule.
                    if attach_supplementary:
                        from pathlib import Path
                        si_dir = (
                            Path(state.config.capsule.root)
                            / doi.replace("/", "_")
                            / "supplementary" / "files"
                        )
                        attached_si: list[str] = []
                        si_errors: list[dict] = []
                        if si_dir.exists():
                            for f in sorted(si_dir.glob("*")):
                                if not f.is_file():
                                    continue
                                try:
                                    att_key = await zotero.upload_attachment(
                                        parent_item_key=key,
                                        file_path=str(f),
                                        filename=f.name,
                                    )
                                    if att_key:
                                        attached_si.append(f.name)
                                except Exception as exc:
                                    si_errors.append(
                                        {"file": f.name, "error": str(exc)}
                                    )
                        entry["attached_supplementary"] = attached_si
                        if si_errors:
                            entry["si_attach_errors"] = si_errors

                    created.append(entry)
                except Exception as exc:
                    failed.append({"doi": doi, "reason": str(exc)})

        logger.info(
            "mcp_push_to_zotero",
            created=len(created), failed=len(failed),
            attach_pdf=attach_pdf, attach_supplementary=attach_supplementary,
        )
        return _json_ok({"created": created, "skipped": [], "failed": failed})

    except Exception as e:
        logger.error("mcp_push_to_zotero_error", error=str(e))
        return _json_error(f"Failed to push to Zotero: {e}")


# =============================================================================
# Tool 12: build_kbs_from_zotero
# =============================================================================


@mcp.tool()
async def build_kbs_from_zotero(
    top_level_collection_keys: list[str] | None = None,
    include_unfiled: bool = True,
    plan_only: bool = False,
    library_id: str | None = None,
    library_type: str | None = None,
) -> dict[str, Any]:
    """Build one KB per Zotero top-level collection.

    Args:
        top_level_collection_keys: Optional filter. None = all top-level collections.
        include_unfiled: Also include items not in any collection (default True).
        plan_only: If True, return the preview only without executing.
        library_id: Optional override for zotero.library_id from config.
            Pass this to switch libraries per-call without restarting.
            e.g. for BioMedOmicsAI → "5691738", MetaboLinkAI → "5453037".
        library_type: Optional override for zotero.library_type ("user" or
            "group"). Defaults to the configured value.

    Returns either {"plan": [...]} (plan-only) or {"per_kb": [...]} (executed).
    Requires zotero.enabled=true and credentials in config.yml.
    """
    from perspicacite.integrations import zotero_ingest
    from perspicacite.integrations.zotero import ZoteroClient

    cfg = getattr(getattr(mcp_state, "config", None), "zotero", None)
    if not (cfg and cfg.enabled):
        return {"error": "Zotero not configured (zotero.enabled)"}

    # Allow per-call overrides for library_id / library_type so an agent
    # can drive multiple libraries (e.g. BioMedOmicsAI + MetaboLinkAI)
    # without restarting the server.
    eff_library_id = library_id or cfg.library_id
    eff_library_type = library_type or cfg.library_type
    if not eff_library_id:
        return {"error": "library_id required (pass as argument or set zotero.library_id)"}

    base_url = getattr(cfg, "base_url", "") or None
    is_local = base_url and ("localhost" in base_url or "127.0.0.1" in base_url)
    if not cfg.api_key and not is_local:
        return {"error": "Zotero api_key required for non-local base_url"}

    client = ZoteroClient(
        api_key=cfg.api_key,
        library_id=eff_library_id,
        library_type=eff_library_type,
        collection_key=cfg.collection_key,
        base_url=base_url,
    )
    # Resolve real group name so KB names are scoped per-library.
    library_name = await client.get_library_name() or "Library"
    plan = await zotero_ingest.plan_kbs_from_zotero(
        client,
        top_level_collection_keys=top_level_collection_keys,
        include_unfiled=include_unfiled,
        library_label=library_name,
    )
    if plan_only:
        return {"library_name": library_name, "plan": [p.model_dump() for p in plan]}

    # Inline execution with a no-op registry — MCP returns the final summary.
    class _InlineRegistry:
        def __init__(self) -> None:
            self.result: dict[str, Any] | None = None
            self.err: str | None = None

        async def publish(self, jid: str, ev: dict[str, Any]) -> None:
            return None

        async def finish(self, jid: str, res: dict[str, Any]) -> None:
            self.result = res

        async def fail(self, jid: str, err: str) -> None:
            self.err = err

    reg = _InlineRegistry()
    try:
        await zotero_ingest.build_kbs_from_zotero(
            client,
            plan=plan,
            app_state=mcp_state,
            registry=reg,
            job_id="mcp-inline",
        )
    except Exception as exc:
        return {"error": str(exc)}
    if reg.err is not None:
        return {"error": reg.err}
    return reg.result or {"per_kb": []}


@mcp.tool()
async def ingest_local_documents(
    kb_name: str,
    paths: list[str],
    recursive: bool = True,
) -> dict:
    """Ingest local files or directories into a KB.

    Files must be absolute paths under one of `local_docs.allowed_roots`.
    If allowed_roots is empty, this tool refuses all calls.
    """
    from pathlib import Path

    from perspicacite.integrations.local_docs import (
        LocalDocsDisabledError,
        LocalDocsValidationError,
        ingest_local_documents as _ingest,
        validate_local_path,
    )

    allowed = list(getattr(mcp_state.config.local_docs, "allowed_roots", []) or [])
    validated: list[Path] = []
    try:
        for raw in paths:
            validated.append(validate_local_path(raw, allowed_roots=allowed))
    except LocalDocsDisabledError as exc:
        return {"error": str(exc)}
    except LocalDocsValidationError as exc:
        return {"error": str(exc)}

    class _Reg:
        async def publish(self, jid, ev): pass
        async def finish(self, jid, res): self._res = res
        async def fail(self, jid, err): self._err = err

    reg = _Reg()
    return await _ingest(
        kb_name=kb_name,
        paths=validated,
        app_state=mcp_state,
        registry=reg,
        job_id="mcp-inline",
        recursive=recursive,
    )


@mcp.tool()
async def build_capsule(
    paper_id: str,
    kb_name: str,
    force: bool = False,
) -> dict:
    """Build (or rebuild) a per-paper capsule.

    Enumerates papers in ``kb_name``'s vector-store collection, finds the row
    matching ``paper_id``, reconstructs a Paper, locates a cached PDF (if any),
    and calls ``capsule_builder.build_capsule``.
    """
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )

    kb = await mcp_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return {"error": f"KB '{kb_name}' not found"}
    rows = await mcp_state.vector_store.list_paper_metadata(kb.collection_name)
    row = next((r for r in rows if r.get("paper_id") == _normalize_paper_id(paper_id)), None)
    if row is None:
        return {"error": f"paper '{paper_id}' not found in KB '{kb_name}'"}
    paper = resolve_paper_from_metadata(row)
    pdf_path = locate_cached_pdf(row)
    return await _build(
        paper=paper, pdf_path=pdf_path, kb_name=kb_name,
        app_state=mcp_state, force=force,
    )


@mcp.tool()
async def build_capsules_for_kb(
    kb_name: str,
    force: bool = False,
) -> dict:
    """Build capsules for every paper in ``kb_name``.

    Returns ``{total, built, skipped, errored, per_paper: [...]}``.
    """
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )

    kb = await mcp_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return {"error": f"KB '{kb_name}' not found", "total": 0,
                "built": 0, "skipped": 0, "errored": 0, "per_paper": []}
    rows = await mcp_state.vector_store.list_paper_metadata(kb.collection_name)
    per_paper = []
    counts = {"built": 0, "skipped": 0, "errored": 0}
    for row in rows:
        paper = resolve_paper_from_metadata(row)
        pdf_path = locate_cached_pdf(row)
        try:
            res = await _build(
                paper=paper, pdf_path=pdf_path,
                kb_name=kb_name, app_state=mcp_state, force=force,
            )
            status = res.get("status", "errored")
            counts[status] = counts.get(status, 0) + 1
            per_paper.append({"paper_id": paper.id, **res})
        except Exception as exc:
            counts["errored"] += 1
            per_paper.append({"paper_id": paper.id, "status": "errored", "error": str(exc)})
    return {"total": len(rows), **counts, "per_paper": per_paper}


@mcp.tool()
async def fetch_paper_resources(
    kb_name: str,
    paper_id: str,
    kinds: list[str] | None = None,
    ingest: bool = True,
    force: bool = False,
) -> dict:
    """Fetch external resources mined into the paper's capsule resources.json.

    Resources fetched per ``kinds`` (default = all supported: github/zenodo/doi).
    With ``ingest=True``, fetched text-like files are routed into the KB as
    ``is_external=True`` chunks tagged with ``parent_paper_id=<paper_id>``.
    """
    from perspicacite.pipeline.capsule_builder import (
        capsule_dir_for,
        resolve_paper_from_metadata,
    )
    from perspicacite.pipeline.external.fetch_orchestrator import (
        fetch_paper_resources as _fetch,
    )

    kb = await mcp_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return {"error": f"KB '{kb_name}' not found"}
    rows = await mcp_state.vector_store.list_paper_metadata(kb.collection_name)
    row = next((r for r in rows if r.get("paper_id") == _normalize_paper_id(paper_id)), None)
    if row is None:
        return {"error": f"paper '{paper_id}' not found in KB '{kb_name}'"}
    paper = resolve_paper_from_metadata(row)
    cap_dir = capsule_dir_for(paper, root=mcp_state.config.capsule.root)
    setattr(paper, "_kb_name", kb_name)

    if mcp_state.job_registry is None:
        # Synchronous fallback registry — collects events into a list.
        class _LocalReg:
            def __init__(self):
                self.events: list[dict] = []
            async def publish(self, _job_id, payload):
                self.events.append(payload)
            async def finish(self, _job_id, payload):
                self.events.append({"type": "done", **payload})
            async def fail(self, _job_id, msg):
                self.events.append({"type": "error", "error": msg})
        reg = _LocalReg()
        result = await _fetch(
            paper=paper, capsule_dir=cap_dir, kinds=kinds,
            app_state=mcp_state, registry=reg, job_id="local",
            ingest=ingest, force=force,
        )
        return result
    job_id = await mcp_state.job_registry.create("external_fetch", total=0)
    result = await _fetch(
        paper=paper, capsule_dir=cap_dir, kinds=kinds,
        app_state=mcp_state, registry=mcp_state.job_registry, job_id=job_id,
        ingest=ingest, force=force,
    )
    return {"job_id": job_id, **result}


# =============================================================================
# Tool 17: fetch_supplementary
# =============================================================================


@mcp.tool()
async def fetch_supplementary(
    kb_name: str,
    paper_id: str,
    max_bytes_per_file: int = 50_000_000,
    max_bytes_per_record: int = 200_000_000,
    text_only: bool = False,
    force: bool = False,
) -> dict:
    """Download the Supplementary Information files listed in a paper's capsule.

    Reads ``<capsule>/supplementary/index.json`` (built during capsule
    creation via discover_supplementary — PMC JATS → Springer ESM → ACS),
    fetches each file, writes them to
    ``<capsule>/supplementary/files/<filename>``, and records a summary
    at ``<capsule>/supplementary/fetched.json``.

    Args:
        kb_name: Knowledge base containing the paper.
        paper_id: Paper DOI (with or without ``doi:`` prefix).
        max_bytes_per_file: Skip individual files larger than this.
            Default 50 MB. Raise this if you need to pull big SI archives.
        max_bytes_per_record: Stop the loop once cumulative bytes for
            this paper's SI exceed this. Default 200 MB.
        text_only: When True, skip mime types we can't easily chunk
            (zip, tar, mp4, octet-stream). PDFs/XLSX/CSV/TXT are kept.
        force: Re-download even if fetched.json already exists.

    Returns:
        {"fetched": [...], "skipped": [...], "bytes": int} or {"error": ...}.
    """
    from pathlib import Path
    from perspicacite.pipeline.capsule_builder import (
        capsule_dir_for,
        resolve_paper_from_metadata,
    )
    from perspicacite.pipeline.download.supplementary import (
        download_supplementary_to_capsule,
    )
    import json as _json

    kb = await mcp_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return {"error": f"KB '{kb_name}' not found"}
    rows = await mcp_state.vector_store.list_paper_metadata(kb.collection_name)
    norm_id = _normalize_paper_id(paper_id)
    row = next((r for r in rows if r.get("paper_id") == norm_id), None)
    if row is None:
        return {"error": f"paper '{paper_id}' not found in KB '{kb_name}'"}
    paper = resolve_paper_from_metadata(row)
    cap = capsule_dir_for(paper, root=mcp_state.config.capsule.root)
    index_path = cap / "supplementary" / "index.json"
    if not index_path.exists():
        return {
            "error": "no supplementary/index.json — build the capsule first "
                     "(build_capsule or build_capsules_for_kb)",
            "capsule_dir": str(cap),
        }
    fetched_path = cap / "supplementary" / "fetched.json"
    if fetched_path.exists() and not force:
        return {
            "skipped": "already_fetched",
            "summary": _json.loads(fetched_path.read_text(encoding="utf-8")),
            "capsule_dir": str(cap),
        }
    manifest = _json.loads(index_path.read_text(encoding="utf-8"))
    result = await download_supplementary_to_capsule(
        cap,
        manifest.get("items") or [],
        max_bytes_per_file=max_bytes_per_file,
        max_bytes_per_record=max_bytes_per_record,
        text_only=text_only,
    )
    return {"capsule_dir": str(cap), **result}


# =============================================================================
# Tool 18: route_kbs
# =============================================================================


@mcp.tool()
async def route_kbs(
    query: str,
    candidate_kbs: list[str] | None = None,
    method: str = "bm25",
    top_k: int = 3,
    score_threshold: float = 0.1,
    ctx: Context | None = None,
) -> dict:
    """Pick the most-relevant KBs for a query without actually running it.

    Useful for agents that want to decide where to look before
    committing to retrieval — pass the returned ``hits`` list as
    ``kb_names`` to ``generate_report`` / ``search_knowledge_base`` /
    ``/api/chat``.

    Args:
        query: The research question.
        candidate_kbs: Optional restricted list (KB names). ``None`` =
            consider every KB in the session store.
        method: ``"bm25"`` (default, no LLM call) or ``"llm"`` (one
            cheap LLM call scores every KB; better on semantic
            mismatches).
        top_k: Max KBs to return.
        score_threshold: Drop KBs whose normalized score is below this.

    Returns:
        ``{"hits": [{"kb_name", "score", "reason", "sampled_titles"}, …]}``
    """
    from perspicacite.rag.kb_router import auto_route_kbs

    state = _require_state()
    if isinstance(state, str):
        return {"error": state}

    all_kbs = await state.session_store.list_kbs()
    if candidate_kbs:
        wanted = set(candidate_kbs)
        all_kbs = [k for k in all_kbs if k.name in wanted]
    if not all_kbs:
        return {"hits": [], "note": "no candidate KBs"}

    from perspicacite.llm.client import resolve_stage_model
    from perspicacite.llm.mcp_sampling import use_mcp_context
    route_provider, route_model = resolve_stage_model(state.config, "routing")
    with use_mcp_context(ctx):
        hits = await auto_route_kbs(
            query=query,
            kb_metas=all_kbs,
            vector_store=state.vector_store,
            method=method,
            top_k=top_k,
            score_threshold=score_threshold,
            llm_client=state.llm_client,
            llm_model=route_model,
            llm_provider=route_provider,
        )
    return {"hits": [h.to_dict() for h in hits]}


# =============================================================================
# Tool 19: build_kb_from_search
# =============================================================================


@mcp.tool()
async def build_kb_from_search(
    query: str,
    kb_name: str,
    max_results: int = 20,
    databases: list[str] | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    min_citations: int | None = None,
    require_abstract: bool = False,
    article_type: str | None = None,
    create_if_missing: bool = True,
    description: str | None = None,
    dry_run: bool = False,
    screen_method: str | None = None,
    screen_threshold: float = 0.5,
    kb_aware: bool = False,
    kb_aware_terms: int = 8,
    rephrase: int = 0,
    ctx: Context | None = None,
) -> str:
    """Build or enrich a KB from a SciLEx multi-database search.

    Runs ``query`` against Semantic Scholar / OpenAlex / PubMed / arXiv
    (configurable), applies year / citation / abstract filters, then
    fetches PDFs and ingests them into ``kb_name``. Creates the KB
    when it doesn't exist (unless ``create_if_missing=False``).

    Use this when an agent wants to spin up a focused KB for a topic
    before doing real RAG over it — one tool call gets you from
    "query string" to "queryable KB" without manual DOI shuffling.

    Args:
        query: Free-text research question (used verbatim by SciLEx).
        kb_name: Target KB; created if missing and ``create_if_missing``.
        max_results: Max hits to pull from SciLEx (1–100).
        databases: SciLEx APIs to query (default: semantic_scholar,
            openalex, pubmed). Other options: arxiv, ieee, springer, dblp.
        min_year / max_year: Drop hits outside this range.
        min_citations: Drop hits below this citation count (uses
            citation_count when SciLEx provides it; treats missing as 0).
        require_abstract: Drop hits without an abstract.
        article_type: Optional "review" / "article" / "conference".
        create_if_missing: When False, error if KB doesn't already exist.
        description: KB description (used only when creating).
        dry_run: Return the filtered DOI list without fetching PDFs.

    Returns:
        JSON :class:`IngestReport` — search counts, filter reasons,
        added papers/chunks, PDF stats, list of selected DOIs.
    """
    state = _require_state()
    if isinstance(state, str):
        return state

    if max_results < 1 or max_results > 100:
        return _json_error("max_results must be 1..100")

    try:
        from perspicacite.pipeline.search_to_kb import (
            SearchFilter,
            search_filter_and_ingest,
        )
        from perspicacite.llm.mcp_sampling import use_mcp_context

        flt = SearchFilter(
            min_year=min_year,
            max_year=max_year,
            min_citations=min_citations,
            require_doi=True,
            require_abstract=require_abstract,
        )
        with use_mcp_context(ctx):
            report = await search_filter_and_ingest(
                app_state=state,
                query=query,
                kb_name=kb_name,
                max_results=max_results,
                databases=databases,
                flt=flt,
                article_type=article_type,
                create_if_missing=create_if_missing,
                description=description,
                dry_run=dry_run,
                screen_method=screen_method,
                screen_threshold=screen_threshold,
                kb_aware=kb_aware,
                kb_aware_terms=kb_aware_terms,
                rephrase=rephrase,
            )
        logger.info(
            "mcp_build_kb_from_search",
            query=query, kb=kb_name,
            searched=report.searched, candidates=report.candidates,
            added=report.added_papers,
        )
        return _json_ok(report.to_dict())
    except Exception as e:
        logger.error(
            "mcp_build_kb_from_search_error",
            query=query, kb=kb_name, error=str(e),
        )
        return _json_error(f"build_kb_from_search failed: {e}")


# =============================================================================
# Tool 20: export_kb
# =============================================================================


@mcp.tool()
async def export_kb(
    kb_name: str,
    out_dir: str,
    with_pdfs: bool = True,
    with_supplementary: bool = False,
    overwrite: bool = False,
) -> str:
    """Export a KB as BibTeX + cached-PDF folder for Zotero / ZotFile import.

    Produces ``<out_dir>/<kb_name>.bib`` plus optional
    ``<out_dir>/papers/<sanitized-doi>.pdf`` for every paper with a
    cached PDF. The BibTeX file references each PDF via the BetterBibTeX
    ``file`` field so Zotero attaches them on import. With
    ``with_supplementary=True``, also copies any
    ``data/capsules/<paper_id>/supplementary/files/*`` into
    ``<out_dir>/supplementary/<paper_id>/``.

    Use this for the citation-manager-agnostic preservation path —
    Zotero-free, filesystem-only, portable, git-friendly.

    Args:
        kb_name: KB to export.
        out_dir: Destination directory (created if missing).
        with_pdfs: Copy cached PDFs and reference them in the BibTeX file.
        with_supplementary: Copy supplementary files from capsules.
        overwrite: Replace an existing ``<kb_name>.bib`` if present.

    Returns:
        JSON :class:`ExportReport` with counts and paths.
    """
    state = _require_state()
    if isinstance(state, str):
        return state
    try:
        from perspicacite.pipeline.export_kb import export_kb as _export

        report = await _export(
            app_state=state,
            kb_name=kb_name,
            out_dir=out_dir,
            with_pdfs=with_pdfs,
            with_supplementary=with_supplementary,
            overwrite=overwrite,
        )
        logger.info(
            "mcp_export_kb", kb=kb_name, out=out_dir,
            papers=report.papers, pdfs=report.pdfs_copied,
        )
        return _json_ok(report.to_dict())
    except FileExistsError as exc:
        return _json_error(str(exc))
    except Exception as e:
        logger.error("mcp_export_kb_error", kb=kb_name, error=str(e))
        return _json_error(f"export_kb failed: {e}")


# =============================================================================
# Tool 21: expand_kb_via_citations
# =============================================================================


@mcp.tool()
async def expand_kb_via_citations(
    kb_name: str,
    direction: str = "both",
    max_per_seed: int = 10,
    seed_dois: list[str] | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    min_citations: int | None = None,
    require_abstract: bool = False,
    screen_method: str | None = None,
    screen_threshold: float = 0.5,
    query: str | None = None,
    dry_run: bool = False,
    ctx: Context | None = None,
) -> str:
    """Grow a KB by following citation edges from its existing papers.

    Forward snowball: papers that cite the seeds. Backward snowball:
    papers the seeds cite. Uses OpenAlex (no SciLEx dependency).
    Optionally screens candidates by BM25 / LLM relevance against
    ``query`` (or the KB description) before ingest.

    Args:
        kb_name: Target KB. Must already exist.
        direction: ``"forward"`` / ``"backward"`` / ``"both"``.
        max_per_seed: Cap on hits per seed per direction (max 25).
        seed_dois: Restrict to these seeds. ``None`` = every DOI in
            the KB.
        min_year / max_year / min_citations / require_abstract:
            Pre-screen filters.
        screen_method / screen_threshold: Optional LLM/BM25 relevance
            gate; ``query`` falls back to the KB description.
        dry_run: Skip PDF fetch + ingest; return the candidate DOIs only.

    Returns:
        JSON :class:`SnowballReport`.
    """
    state = _require_state()
    if isinstance(state, str):
        return state
    try:
        from perspicacite.pipeline.snowball import expand_kb_via_citations as _expand
        from perspicacite.pipeline.search_to_kb import SearchFilter
        from perspicacite.llm.mcp_sampling import use_mcp_context

        flt = SearchFilter(
            min_year=min_year, max_year=max_year,
            min_citations=min_citations, require_doi=True,
            require_abstract=require_abstract,
        )
        with use_mcp_context(ctx):
            report = await _expand(
                app_state=state, kb_name=kb_name,
                direction=direction, max_per_seed=max_per_seed,
                seed_dois=seed_dois, flt=flt,
                screen_method=screen_method, screen_threshold=screen_threshold,
                query=query, dry_run=dry_run,
            )
        logger.info(
            "mcp_expand_kb_via_citations",
            kb=kb_name, direction=direction,
            raw_hits=report.raw_hits, added=report.added_papers,
        )
        return _json_ok(report.to_dict())
    except Exception as e:
        logger.error("mcp_expand_kb_via_citations_error", kb=kb_name, error=str(e))
        return _json_error(f"expand_kb_via_citations failed: {e}")


# =============================================================================
# Tool 22: delete_knowledge_base
# =============================================================================


@mcp.tool()
async def delete_knowledge_base(
    name: str,
    keep_collection: bool = False,
) -> str:
    """Permanently delete a KB (metadata row + Chroma collection).

    Cached PDFs in ``pdf_download.cache_dir`` are NOT removed — they
    are per-DOI artefacts useful for future ingests. To purge them,
    delete the matching ``<doi>.pdf`` / ``<doi>.meta.json`` files
    by hand.

    Args:
        name: KB name.
        keep_collection: When True, drop only the metadata row and
            leave the underlying Chroma collection in place (orphans
            the embeddings; useful when re-attaching).

    Returns:
        JSON ``{"deleted": bool, "collection_dropped": bool, "name": ...}``.
    """
    state = _require_state()
    if isinstance(state, str):
        return state
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if not kb:
            return _json_error(f"KB '{name}' not found")
        collection_dropped = False
        collection_error: str | None = None
        if not keep_collection and kb.collection_name:
            try:
                await state.vector_store.delete_collection(kb.collection_name)
                collection_dropped = True
            except Exception as exc:
                collection_error = str(exc)
        deleted = await state.session_store.delete_kb_metadata(name)
        logger.info(
            "mcp_delete_kb", name=name, deleted=deleted,
            collection_dropped=collection_dropped,
        )
        return _json_ok({
            "name": name,
            "deleted": deleted,
            "collection_dropped": collection_dropped,
            "collection_error": collection_error,
        })
    except Exception as e:
        logger.error("mcp_delete_kb_error", name=name, error=str(e))
        return _json_error(f"delete_knowledge_base failed: {e}")


# =============================================================================
# Tool 23: enrich_kb_from_cite_graph_tool
# =============================================================================


@mcp.tool()
async def enrich_kb_from_cite_graph_tool(
    kb_name: str,
    tool: str | None = None,
    doi: str | None = None,
    max_papers: int | None = None,
    dry_run: bool = True,
) -> dict:
    """MCP tool: cite-graph enrichment preview.

    Resolves a library/tool name (or explicit DOI) to a canonical
    paper, fetches OpenAlex citing works, filters and scores them, and
    returns a ranked list of CiteHit records.

    v1: dry-run only. Returns ranked CiteHit records as dicts.

    Args:
        kb_name: Target KB name (used for context; no ingest in v1).
        tool: Library/tool name to resolve to its canonical DOI.
        doi: Skip the resolver and use this DOI directly as the seed.
        max_papers: Override the max_papers cap from config.
        dry_run: Preview only — no ingest (default True; v1 always behaves
            as dry-run regardless of this flag).

    Returns:
        Dict ``{"hits": [...]}`` where each hit contains doi, title,
        year, citation_count, is_oa, venue, score, score_breakdown.
    """
    from perspicacite.pipeline.cite_graph import enrich_kb_from_cite_graph

    cfg = mcp_state.config
    kb_cfg = cfg.knowledge_base
    if max_papers is not None:
        kb_cfg.cite_graph.max_papers = max_papers
    hits = await enrich_kb_from_cite_graph(
        tool=tool, doi=doi, kb_config=kb_cfg, existing_dois=set(),
        dry_run=dry_run,
    )
    return {"hits": [
        {
            "doi": h.doi, "title": h.title, "year": h.year,
            "citation_count": h.citation_count, "is_oa": h.is_oa,
            "venue": h.venue, "score": h.score,
            "score_breakdown": h.score_breakdown,
        }
        for h in hits
    ]}


# =============================================================================
# Resource
# =============================================================================


_TOOL_NAMES: list[str] = [
    "search_literature",
    "get_paper_content",
    "get_paper_references",
    "list_knowledge_bases",
    "search_knowledge_base",
    "create_knowledge_base",
    "add_papers_to_kb",
    "generate_report",
    "screen_papers",
    "add_dois_to_kb",
    "push_to_zotero",
    "build_kbs_from_zotero",
    "ingest_local_documents",
    "build_capsule",
    "fetch_supplementary",
    "build_capsules_for_kb",
    "fetch_paper_resources",
    "route_kbs",
    "build_kb_from_search",
    "export_kb",
    "expand_kb_via_citations",
    "delete_knowledge_base",
    "enrich_kb_from_cite_graph_tool",
]


@mcp.resource("perspicacite://info")
async def get_info() -> str:
    """Perspicacité capabilities and status."""
    return json.dumps(
        {
            "name": "Perspicacité v2",
            "description": "AI-powered scientific literature research assistant",
            "tools": _TOOL_NAMES,
            "tool_count": len(_TOOL_NAMES),
            "initialized": mcp_state.initialized,
        }
    )


# =============================================================================
# KB resources (Wave 5.1)
# =============================================================================

from perspicacite.mcp import resources as _resources  # noqa: E402


@mcp.resource("perspicacite://kbs")
async def _kbs_resource() -> str:
    """List all knowledge bases."""
    return await _resources.kbs_resource()


@mcp.resource("perspicacite://kb/{name}")
async def _kb_resource(name: str) -> str:
    """Metadata + sub-resource URIs for a single KB."""
    return await _resources.kb_resource(name)


@mcp.resource("perspicacite://kb/{name}/papers")
async def _kb_papers_resource(name: str) -> str:
    """Papers in a KB, sourced from the kb_log (fallback to Chroma)."""
    return await _resources.kb_papers_resource(name)


@mcp.resource("perspicacite://kb/{name}/log")
async def _kb_log_resource(name: str) -> str:
    """Most-recent KB-log events (bounded by kb.mcp_resource_max_events)."""
    return await _resources.kb_log_resource(name)


# =============================================================================
# Canned prompts (Wave 5.2)
# =============================================================================

from perspicacite.mcp import prompts as _prompts  # noqa: E402


@mcp.prompt()
def literature_review(
    topic: str, kb_name: str | None = None, max_papers: int = 30
) -> list[dict[str, Any]]:
    """Run a literature review on a topic, optionally against a KB."""
    return _prompts.literature_review(topic, kb_name, max_papers)


@mcp.prompt()
def compare_papers(
    paper_a: str, paper_b: str, kb_name: str | None = None
) -> list[dict[str, Any]]:
    """Compare two papers side-by-side."""
    return _prompts.compare_papers(paper_a, paper_b, kb_name)


@mcp.prompt()
def summarize_kb(kb_name: str, max_papers: int = 50) -> list[dict[str, Any]]:
    """Summarize an entire knowledge base in five paragraphs."""
    return _prompts.summarize_kb(kb_name, max_papers)


@mcp.prompt()
def ingest_dois(kb_name: str, dois: list[str]) -> list[dict[str, Any]]:
    """Ingest a list of DOIs into a KB."""
    return _prompts.ingest_dois(kb_name, dois)


@mcp.prompt()
def screen_topic(
    topic: str, kb_name: str, threshold: float = 0.6
) -> list[dict[str, Any]]:
    """Screen a KB for papers relevant to a topic."""
    return _prompts.screen_topic(topic, kb_name, threshold)
