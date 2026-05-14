"""SciLEx-driven KB build / enrich pipeline.

Glues three existing pieces together so the user can grow a KB from a
plain-text query:

1. :class:`perspicacite.search.scilex_adapter.SciLExAdapter` — multi-DB
   academic search (Semantic Scholar, OpenAlex, PubMed, arXiv, …).
2. Light client-side filters (year, citations, abstract presence, DOI
   presence) so we don't waste a PDF fetch on garbage hits.
3. The same DOI → PDF → chunk → embed pipeline that ``add_dois_to_kb``
   already uses.

The big win over "search then manually paste DOIs into add_dois_to_kb"
is that this is one tool, callable from MCP or CLI, with the de-dup
and KB-auto-create UX baked in. Claude Code (or any agent) can call
``build_kb_from_search`` after running its own exploratory queries to
spin up a focused KB before doing real RAG over it.

Public surface:

- :func:`run_search` — query SciLEx, return raw :class:`Paper` list.
- :func:`apply_filters` — pure function over :class:`Paper` list.
- :func:`ingest_dois_into_kb` — shared "add these DOIs to this KB"
  implementation reused by MCP and CLI.
- :func:`search_filter_and_ingest` — the one-shot orchestrator that
  ties the three together.

Nothing here owns any state; the caller passes ``app_state`` (the
already-initialized :class:`perspicacite.web.state.AppState`) so we
share its vector store / embedding provider / session store / PDF
parser. That keeps test wiring trivial and avoids re-initializing
expensive resources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.search_to_kb")


@dataclass
class SearchFilter:
    """Client-side filters applied to a SciLEx hit list.

    All filters are AND-combined; ``None`` means "no constraint". The
    intent is to drop obvious non-fits before paying for a PDF fetch,
    not to do real screening — call ``screen_papers`` separately for
    LLM-based relevance grading.
    """

    min_year: int | None = None
    max_year: int | None = None
    min_citations: int | None = None
    require_doi: bool = True
    require_abstract: bool = False


@dataclass
class IngestReport:
    """Result of one end-to-end search→ingest run."""

    query: str
    kb_name: str
    kb_created: bool = False
    searched: int = 0
    filtered_out: int = 0
    candidates: int = 0
    added_papers: int = 0
    added_chunks: int = 0
    skipped_duplicates: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)
    pdf_download: dict[str, int] = field(default_factory=dict)
    selected_dois: list[str] = field(default_factory=list)
    filter_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


async def run_search(
    *,
    query: str,
    max_results: int,
    databases: list[str] | None,
    year_min: int | None,
    year_max: int | None,
    article_type: str | None = None,
) -> list[Any]:
    """Run a SciLEx multi-DB search.

    Returns an empty list when SciLEx isn't installed (the caller can
    distinguish "0 hits" from "no backend" via the empty list + a log
    line; the MCP tool also surfaces a structured error).
    """
    from perspicacite.search.scilex_adapter import SciLExAdapter

    adapter = SciLExAdapter()
    if not adapter.available:
        logger.warning(
            "search_to_kb_scilex_missing",
            advice="install with: uv pip install -e \".[scilex]\"",
        )
        return []
    papers = await adapter.search(
        query=query,
        max_results=max_results,
        year_min=year_min,
        year_max=year_max,
        apis=databases or ["semantic_scholar", "openalex", "pubmed"],
        article_type=article_type,
    )
    logger.info("search_to_kb_search", query=query, hits=len(papers))
    return list(papers)


def apply_filters(
    papers: list[Any],
    flt: SearchFilter,
) -> tuple[list[Any], dict[str, int]]:
    """Drop papers that fail any active filter. Returns (kept, reasons).

    ``reasons`` is a histogram (``{reason: count}``) suitable for
    surfacing back to the user — they often want to know "why was my
    18-hit search reduced to 3 candidates?".
    """
    kept: list[Any] = []
    reasons: dict[str, int] = {}
    seen_dois: set[str] = set()
    for p in papers:
        doi = (getattr(p, "doi", None) or "").lower().strip()
        if flt.require_doi and not doi:
            reasons["no_doi"] = reasons.get("no_doi", 0) + 1
            continue
        if doi and doi in seen_dois:
            reasons["duplicate_doi"] = reasons.get("duplicate_doi", 0) + 1
            continue
        year = getattr(p, "year", None)
        if flt.min_year is not None and (year is None or year < flt.min_year):
            reasons["below_min_year"] = reasons.get("below_min_year", 0) + 1
            continue
        if flt.max_year is not None and (year is None or year > flt.max_year):
            reasons["above_max_year"] = reasons.get("above_max_year", 0) + 1
            continue
        if flt.min_citations is not None:
            cites = getattr(p, "citation_count", None) or 0
            if cites < flt.min_citations:
                reasons["below_min_citations"] = (
                    reasons.get("below_min_citations", 0) + 1
                )
                continue
        if flt.require_abstract and not (getattr(p, "abstract", None) or "").strip():
            reasons["no_abstract"] = reasons.get("no_abstract", 0) + 1
            continue
        if doi:
            seen_dois.add(doi)
        kept.append(p)
    return kept, reasons


async def _create_kb_if_missing(
    app_state: Any,
    kb_name: str,
    description: str | None,
) -> tuple[Any, bool]:
    """Return (kb_meta, created). Mirrors the create_knowledge_base
    MCP tool's logic so callers don't have to."""
    from perspicacite.models.kb import (
        ChunkConfig,
        KnowledgeBase,
        chroma_collection_name_for_kb,
    )

    existing = await app_state.session_store.get_kb_metadata(kb_name)
    if existing:
        return existing, False
    collection_name = chroma_collection_name_for_kb(kb_name)
    await app_state.vector_store.create_collection(collection_name)
    kb = KnowledgeBase(
        name=kb_name,
        description=description or f"Built from SciLEx search via search_to_kb",
        collection_name=collection_name,
        embedding_model=app_state.embedding_provider.model_name,
        chunk_config=ChunkConfig(
            chunk_size=app_state.config.knowledge_base.chunk_size,
            chunk_overlap=app_state.config.knowledge_base.chunk_overlap,
        ),
    )
    await app_state.session_store.save_kb_metadata(kb)
    return kb, True


async def ingest_dois_into_kb(
    app_state: Any,
    kb_name: str,
    dois: list[str],
) -> dict[str, Any]:
    """Add each DOI's full-text paper to ``kb_name``.

    Mirrors the body of the ``add_dois_to_kb`` MCP tool — both call
    sites build a per-DOI :class:`Paper`, hand it to
    :class:`DynamicKnowledgeBase`, and update KB metadata counts.
    """
    from perspicacite.models.kb import chroma_collection_name_for_kb
    from perspicacite.models.papers import Author, Paper, PaperSource
    from perspicacite.pipeline.download import retrieve_paper_content
    from perspicacite.pipeline.download.cookies import build_authenticated_client
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase

    kb_meta = await app_state.session_store.get_kb_metadata(kb_name)
    if not kb_meta:
        raise ValueError(f"Knowledge base '{kb_name}' not found")
    collection_name = chroma_collection_name_for_kb(kb_name)

    pdf_config = app_state.config.pdf_download
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
    failed: list[dict[str, str]] = []
    dl: dict[str, int] = {"attempted": 0, "success": 0, "failed": 0}

    async with build_authenticated_client(cookies_path=cookies_path) as client:
        for raw_doi in dois:
            doi = (raw_doi or "").strip().replace("https://doi.org/", "")
            if not doi:
                continue
            if await app_state.vector_store.paper_exists(collection_name, doi):
                skipped.append({"doi": doi})
                continue
            dl["attempted"] += 1
            try:
                result = await retrieve_paper_content(
                    doi,
                    http_client=client,
                    pdf_parser=app_state.pdf_parser,
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

    added_chunks = 0
    if papers_to_add:
        dkb = DynamicKnowledgeBase(
            app_state.vector_store,
            app_state.embedding_provider,
        )
        dkb.collection_name = collection_name
        dkb._initialized = True
        added_chunks = await dkb.add_papers(papers_to_add, include_full_text=True)
        kb_meta.paper_count = (kb_meta.paper_count or 0) + len(papers_to_add)
        kb_meta.chunk_count = (kb_meta.chunk_count or 0) + added_chunks
        await app_state.session_store.save_kb_metadata(kb_meta)

    return {
        "added_papers": len(papers_to_add),
        "added_chunks": added_chunks,
        "skipped_duplicates": len(skipped),
        "failed": failed,
        "pdf_download": dl,
    }


async def search_filter_and_ingest(
    *,
    app_state: Any,
    query: str,
    kb_name: str,
    max_results: int = 20,
    databases: list[str] | None = None,
    flt: SearchFilter | None = None,
    article_type: str | None = None,
    create_if_missing: bool = True,
    description: str | None = None,
    dry_run: bool = False,
) -> IngestReport:
    """End-to-end: SciLEx search → filter → optionally create KB → ingest.

    ``dry_run=True`` runs everything up to but not including PDF fetch —
    useful from the CLI when you want to see which DOIs would be added
    before paying for the download.
    """
    flt = flt or SearchFilter()
    report = IngestReport(query=query, kb_name=kb_name)

    papers = await run_search(
        query=query,
        max_results=max_results,
        databases=databases,
        year_min=flt.min_year,
        year_max=flt.max_year,
        article_type=article_type,
    )
    report.searched = len(papers)
    if not papers:
        return report

    kept, reasons = apply_filters(papers, flt)
    report.candidates = len(kept)
    report.filtered_out = report.searched - report.candidates
    report.filter_reasons = reasons
    report.selected_dois = [
        (getattr(p, "doi", None) or "").strip() for p in kept if getattr(p, "doi", None)
    ]
    if not kept or dry_run:
        return report

    # KB create-or-resolve
    if create_if_missing:
        _, created = await _create_kb_if_missing(
            app_state, kb_name, description=description,
        )
        report.kb_created = created
    else:
        existing = await app_state.session_store.get_kb_metadata(kb_name)
        if not existing:
            raise ValueError(
                f"KB '{kb_name}' not found and create_if_missing=False"
            )

    ingest = await ingest_dois_into_kb(
        app_state, kb_name, report.selected_dois,
    )
    report.added_papers = ingest["added_papers"]
    report.added_chunks = ingest["added_chunks"]
    report.skipped_duplicates = ingest["skipped_duplicates"]
    report.failed = ingest["failed"]
    report.pdf_download = ingest["pdf_download"]
    logger.info(
        "search_to_kb_done",
        query=query, kb_name=kb_name,
        searched=report.searched, candidates=report.candidates,
        added=report.added_papers, chunks=report.added_chunks,
    )
    return report
