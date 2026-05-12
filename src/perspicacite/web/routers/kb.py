"""Knowledge base CRUD + paper / bibtex / chunk routes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from perspicacite.models.kb import (
    ChunkConfig,
    KnowledgeBase,
    chroma_collection_name_for_kb,
)
from perspicacite.web.state import app_state


logger = logging.getLogger(__name__)


router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class KBCreateRequest(BaseModel):
    """Request to create a knowledge base."""

    name: str = Field(
        ...,
        pattern=r"^[a-zA-Z0-9 _-]+$",
        min_length=1,
        max_length=100,
        description="KB name (spaces will be converted to underscores)",
    )
    description: Optional[str] = None


class PaperData(BaseModel):
    """Paper data from chat results, for adding to KB."""

    title: str
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: Optional[str] = None
    citations: Optional[int] = None
    file: Optional[str] = Field(default=None, description="Local PDF path (Zotero/Mendeley export)")


class KBAddPapersRequest(BaseModel):
    """Request to add papers to a knowledge base."""

    papers: List[PaperData]


class KBAddDOIsRequest(BaseModel):
    """Request to bulk-add papers from a list of DOIs."""

    dois: List[str] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_pdf_fallback_kwargs(pdf_config) -> dict:
    """Build keyword args for retrieve_paper_content from PDFDownloadConfig."""
    if not pdf_config:
        return {}
    return {
        "alternative_endpoint": pdf_config.alternative_endpoint,
        "unpaywall_email": pdf_config.unpaywall_email,
        "wiley_tdm_token": pdf_config.wiley_tdm_token,
        "aaas_api_key": pdf_config.aaas_api_key,
        "rsc_api_key": pdf_config.rsc_api_key,
        "springer_api_key": pdf_config.springer_api_key,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/kb")
async def list_knowledge_bases():
    """List all knowledge bases."""
    if not app_state.session_store:
        return []
    kbs = await app_state.session_store.list_kbs()
    results = []
    for kb in kbs:
        stats = await app_state.vector_store.get_collection_stats(kb.collection_name)
        results.append(
            {
                "name": kb.name,
                "description": kb.description,
                "paper_count": stats.get("unique_papers", kb.paper_count),
                "chunk_count": stats.get("count", 0),
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
            }
        )
    return results


@router.post("/api/kb")
async def create_knowledge_base(request: KBCreateRequest):
    """Create a new knowledge base."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    # Sanitize KB name: replace spaces with underscores for storage
    kb_name = request.name.strip().replace(" ", "_")
    collection_name = chroma_collection_name_for_kb(kb_name)

    existing = await app_state.session_store.get_kb_metadata(kb_name)
    if existing:
        return {"error": f"Knowledge base '{kb_name}' already exists"}

    # Create collection (handles "already exists" gracefully)
    await app_state.vector_store.create_collection(collection_name)

    kb = KnowledgeBase(
        name=kb_name,
        description=request.description,
        collection_name=collection_name,
        embedding_model=app_state.embedding_provider.model_name,
        chunk_config=ChunkConfig(),
    )
    await app_state.session_store.save_kb_metadata(kb)
    logger.info(f"Created KB: {kb_name} (collection: {collection_name})")

    return {
        "name": kb.name,
        "description": kb.description,
        "collection_name": collection_name,
        "paper_count": 0,
        "chunk_count": 0,
    }


@router.get("/api/kb/{name}")
async def get_knowledge_base(name: str):
    """Get knowledge base details."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    stats = await app_state.vector_store.get_collection_stats(kb.collection_name)
    return {
        "name": kb.name,
        "description": kb.description,
        "paper_count": stats.get("unique_papers", kb.paper_count),
        "chunk_count": stats.get("count", 0),
        "embedding_model": kb.embedding_model,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


@router.delete("/api/kb/{name}")
async def delete_knowledge_base(name: str):
    """Delete a knowledge base."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    try:
        await app_state.vector_store.delete_collection(kb.collection_name)
    except Exception:
        pass  # Collection may not exist in ChromaDB

    # Delete metadata from SQLite
    import aiosqlite

    async with aiosqlite.connect(app_state.session_store.db_path) as db:
        await db.execute("DELETE FROM kb_metadata WHERE name = ?", (name,))
        await db.commit()

    logger.info(f"Deleted KB: {name}")
    return {"deleted": name}


@router.post("/api/kb/{name}/papers")
async def add_papers_to_kb(name: str, request: KBAddPapersRequest):
    """Add papers to a knowledge base with deduplication and optional PDF download."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    from perspicacite.models.papers import Paper, Author, PaperSource
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
    from perspicacite.pipeline.download import retrieve_paper_content

    # Convert PaperData dicts to Paper models with deduplication check
    papers_to_add = []
    skipped_duplicates = []
    download_stats = {"attempted": 0, "success": 0, "failed": 0}

    pdf_config = app_state.config.pdf_download if app_state.config else None
    pdf_kw = _get_pdf_fallback_kwargs(pdf_config)

    for pd in request.papers:
        import hashlib

        paper_id = (
            pd.doi if pd.doi else f"generated:{hashlib.md5(pd.title.encode()).hexdigest()[:12]}"
        )

        # Check if paper already exists in this KB
        exists = await app_state.vector_store.paper_exists(kb.collection_name, paper_id)
        if exists:
            skipped_duplicates.append(
                {
                    "title": pd.title,
                    "paper_id": paper_id,
                    "doi": pd.doi,
                }
            )
            continue

        authors = [Author(name=a) for a in pd.authors]
        paper = Paper(
            id=paper_id,
            title=pd.title,
            authors=authors,
            year=pd.year,
            doi=pd.doi,
            abstract=pd.abstract,
            citation_count=pd.citations,
            source=PaperSource.WEB_SEARCH,
        )

        # Try local PDF first (e.g. from Zotero/Mendeley export)
        full_text = None
        if pd.file:
            local_path = Path(pd.file)
            if local_path.suffix.lower() == ".pdf" and local_path.exists():
                try:
                    parsed = await app_state.pdf_parser.parse(local_path)
                    if parsed.text:
                        full_text = parsed.text
                        download_stats["success"] += 1
                        logger.info(f"Parsed local PDF for: {pd.title[:50]}...")
                except Exception as e:
                    logger.warning(f"Local PDF parse failed for {pd.title[:50]}: {e}")

        # Try to download full text if DOI available and no local PDF
        if full_text is None and pd.doi and app_state.pdf_downloader and app_state.pdf_parser:
            download_stats["attempted"] += 1
            try:
                result = await retrieve_paper_content(
                    pd.doi, pdf_parser=app_state.pdf_parser, **pdf_kw
                )
                if result.success and result.full_text:
                    full_text = result.full_text
                    download_stats["success"] += 1
                    # Enrich paper metadata from discovery if original was placeholder
                    meta = result.metadata or {}
                    if meta.get("title") and paper.title.startswith("Reference"):
                        paper.title = meta["title"]
                    if meta.get("authors") and not paper.authors:
                        from perspicacite.models.papers import Author

                        paper.authors = [Author(name=a) for a in meta["authors"]]
                    if result.abstract and not paper.abstract:
                        paper.abstract = result.abstract
                    logger.info(f"Downloaded full text for: {paper.title[:50]}...")
                else:
                    download_stats["failed"] += 1
            except Exception as e:
                logger.warning(f"PDF download failed for {paper.title[:50]}: {e}")
                download_stats["failed"] += 1

        paper.full_text = full_text
        papers_to_add.append(paper)

    if not papers_to_add:
        logger.info(f"All {len(skipped_duplicates)} papers already exist in KB '{name}'")
        return {
            "added_papers": 0,
            "added_chunks": 0,
            "skipped_duplicates": len(skipped_duplicates),
            "kb": name,
        }

    # Use DynamicKnowledgeBase to add papers to the collection
    dkb = DynamicKnowledgeBase(
        vector_store=app_state.vector_store,
        embedding_service=app_state.embedding_provider,
    )
    # Override with the real KB collection
    dkb.collection_name = kb.collection_name
    dkb._initialized = True

    # Add papers with full text if available
    added = await dkb.add_papers(papers_to_add, include_full_text=True)

    # Update metadata counts only for new papers
    kb.paper_count += len(papers_to_add)
    kb.chunk_count += added
    await app_state.session_store.save_kb_metadata(kb)

    logger.info(
        f"Added {len(papers_to_add)} papers ({added} chunks) to KB '{name}', skipped {len(skipped_duplicates)} duplicates. PDF stats: {download_stats}"
    )
    return {
        "added_papers": len(papers_to_add),
        "added_chunks": added,
        "skipped_duplicates": len(skipped_duplicates),
        "duplicates": skipped_duplicates,
        "pdf_download": download_stats,
        "kb": name,
    }


@router.get("/api/kb/{name}/stats")
async def get_kb_stats(name: str):
    """Aggregate statistics for a KB, computed from ChromaDB metadata + the SQLite KB record."""
    if not app_state.session_store:
        return {"error": "System not initialized"}
    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}
    scan_cap = 20000
    try:
        coll = app_state.vector_store.client.get_collection(name=kb.collection_name)
        total_chunks = coll.count()
        got = coll.get(
            limit=min(total_chunks, scan_cap) if total_chunks else 0, include=["metadatas"]
        )
        metas = got.get("metadatas") or []
    except Exception as e:
        logger.warning(f"kb stats: collection scan failed for {name}: {e}")
        metas, total_chunks = [], 0
    by_year: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_content_type: dict[str, int] = {}
    by_journal: dict[str, int] = {}
    seen: set = set()
    for m in metas:
        m = m or {}
        pid = m.get("paper_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        year_key = str(m.get("year") or "unknown")
        by_year[year_key] = by_year.get(year_key, 0) + 1
        source_key = str(m.get("source") or "unknown")
        by_source[source_key] = by_source.get(source_key, 0) + 1
        ct_key = str(m.get("content_type") or "unknown")
        by_content_type[ct_key] = by_content_type.get(ct_key, 0) + 1
        j = (m.get("journal") or "").strip()
        if j:
            by_journal[j] = by_journal.get(j, 0) + 1
    top_journals = sorted(by_journal.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "name": kb.name,
        "paper_count": len(seen) or kb.paper_count,
        "chunk_count": total_chunks,
        "by_year": dict(sorted(by_year.items())),
        "by_source": by_source,
        "by_content_type": by_content_type,
        "top_journals": [{"journal": j, "count": c} for j, c in top_journals],
        "embedding_model": kb.embedding_model,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "scanned_chunks": len(metas),
        "scan_capped": total_chunks > scan_cap if total_chunks else False,
    }


@router.get("/api/kb/{name}/chunks")
async def get_kb_chunks(
    name: str,
    limit: int = 20,
    offset: int = 0,
    paper_id: str | None = None,
):
    """Inspect raw chunks stored in a knowledge base (paginated)."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    limit = max(1, min(100, limit))
    offset = max(0, offset)

    coll = app_state.vector_store.client.get_collection(name=kb.collection_name)
    total = coll.count()

    where_filter = {"paper_id": paper_id} if paper_id else None
    result = coll.get(
        limit=limit,
        offset=offset,
        where=where_filter,
        include=["documents", "metadatas"],
    )

    chunks = []
    for i, chunk_id in enumerate(result["ids"]):
        meta = result["metadatas"][i] if result["metadatas"] else {}
        doc = result["documents"][i] if result["documents"] else ""
        chunks.append(
            {
                "id": chunk_id,
                "text": doc,
                "paper_id": meta.get("paper_id"),
                "chunk_index": meta.get("chunk_index"),
                "section": meta.get("section"),
                "title": meta.get("title"),
                "authors": meta.get("authors"),
                "year": meta.get("year"),
                "doi": meta.get("doi"),
                "source": meta.get("source"),
            }
        )

    return {
        "kb": name,
        "total_chunks": total,
        "offset": offset,
        "limit": limit,
        "returned": len(chunks),
        "chunks": chunks,
    }


@router.post("/api/kb/{name}/bibtex")
async def add_bibtex_to_kb(name: str, request: Request):
    """Upload a BibTeX file and add papers to a knowledge base."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    try:
        body = await request.json()
        bibtex_content = body.get("bibtex", "")
    except Exception:
        return {"error": "Invalid request body"}

    if not bibtex_content.strip():
        return {"error": "BibTeX content is empty"}

    # Parse BibTeX entries using bibtexparser (same as CLI)
    from perspicacite.models.papers import PaperSource
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
    from perspicacite.pipeline.download import retrieve_paper_content
    from perspicacite.pipeline.bibtex_kb import entries_to_papers
    import bibtexparser

    # Use bibtexparser to parse the BibTeX content
    try:
        db = bibtexparser.loads(bibtex_content)
        entries = db.entries
        papers = entries_to_papers(entries)
    except Exception as e:
        logger.error(f"BibTeX parsing failed: {e}")
        return {"error": f"Failed to parse BibTeX: {str(e)}"}

    if not papers:
        return {"error": "No valid paper entries found in BibTeX file"}

    # Process papers with deduplication and PDF download
    papers_to_add = []
    download_stats = {"attempted": 0, "success": 0, "failed": 0, "local_pdf": 0}

    pdf_config = app_state.config.pdf_download if app_state.config else None
    pdf_kw = _get_pdf_fallback_kwargs(pdf_config)

    for paper in papers:
        # Use DOI as ID if available, otherwise generate from title
        paper_id = paper.doi if paper.doi else paper.id

        # Check if paper already exists
        exists = await app_state.vector_store.paper_exists(kb.collection_name, paper_id)
        if exists:
            continue

        # Ensure source is set to BIBTEX
        paper.source = PaperSource.BIBTEX

        # Try local PDF first (BibTeX ``file`` field mapped to pdf_url)
        local_path = Path(paper.pdf_url) if paper.pdf_url else None
        if local_path and local_path.suffix.lower() == ".pdf" and local_path.exists():
            try:
                parsed = await app_state.pdf_parser.parse(local_path)
                if parsed.text:
                    paper.full_text = parsed.text
                    download_stats["local_pdf"] += 1
                    papers_to_add.append(paper)
                    continue
            except Exception as e:
                logger.warning(f"Local PDF parse failed for {paper.title[:50]}: {e}")

        # Try to download full text if DOI available
        if paper.doi and app_state.pdf_parser:
            download_stats["attempted"] += 1
            try:
                result = await retrieve_paper_content(
                    paper.doi, pdf_parser=app_state.pdf_parser, **pdf_kw
                )
                if result.success and result.full_text:
                    paper.full_text = result.full_text
                    download_stats["success"] += 1
                    # Enrich paper metadata from discovery
                    meta = result.metadata or {}
                    if meta.get("title") and not paper.title:
                        paper.title = meta["title"]
                    if meta.get("authors") and not paper.authors:
                        from perspicacite.models.papers import Author

                        paper.authors = [Author(name=a) for a in meta["authors"]]
                    if result.abstract and not paper.abstract:
                        paper.abstract = result.abstract
            except Exception as e:
                logger.warning(f"Content download failed for {paper.title[:50]}: {e}")
                download_stats["failed"] += 1

        papers_to_add.append(paper)

    if not papers_to_add:
        return {
            "message": "All papers already exist in KB",
            "added_papers": 0,
            "kb": name,
        }

    # Add papers to KB
    dkb = DynamicKnowledgeBase(
        vector_store=app_state.vector_store,
        embedding_service=app_state.embedding_provider,
    )
    dkb.collection_name = kb.collection_name
    dkb._initialized = True

    added = await dkb.add_papers(papers_to_add, include_full_text=True)

    # Update metadata
    kb.paper_count += len(papers_to_add)
    kb.chunk_count += added
    await app_state.session_store.save_kb_metadata(kb)

    logger.info(
        f"Added {len(papers_to_add)} papers from BibTeX ({added} chunks) to KB '{name}'. PDF stats: {download_stats}"
    )
    return {
        "added_papers": len(papers_to_add),
        "added_chunks": added,
        "pdf_download": download_stats,
        "kb": name,
    }


@router.post("/api/kb/{name}/dois")
async def add_dois_to_kb(name: str, request: KBAddDOIsRequest):
    """Bulk-add papers to a knowledge base from a list of DOIs (synchronous)."""
    if not app_state.session_store:
        return {"error": "System not initialized"}

    if len(request.dois) > 200:
        raise HTTPException(status_code=400, detail="At most 200 DOIs per request")

    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    from perspicacite.models.papers import Paper, Author, PaperSource
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
    from perspicacite.pipeline.download import retrieve_paper_content

    pdf_config = app_state.config.pdf_download if app_state.config else None
    pdf_kw = _get_pdf_fallback_kwargs(pdf_config)

    papers_to_add: list = []
    skipped: list = []
    failed: list = []
    dl = {"attempted": 0, "success": 0, "failed": 0}

    for raw_doi in request.dois:
        doi = (raw_doi or "").strip().replace("https://doi.org/", "")
        if not doi:
            continue

        if await app_state.vector_store.paper_exists(kb.collection_name, doi):
            skipped.append({"doi": doi})
            continue

        dl["attempted"] += 1
        try:
            result = await retrieve_paper_content(doi, pdf_parser=app_state.pdf_parser, **pdf_kw)
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
        return {
            "added_papers": 0,
            "added_chunks": 0,
            "skipped_duplicates": len(skipped),
            "failed": failed,
            "pdf_download": dl,
            "kb": name,
        }

    dkb = DynamicKnowledgeBase(
        vector_store=app_state.vector_store,
        embedding_service=app_state.embedding_provider,
    )
    dkb.collection_name = kb.collection_name
    dkb._initialized = True

    added = await dkb.add_papers(papers_to_add, include_full_text=True)

    kb.paper_count += len(papers_to_add)
    kb.chunk_count += added
    await app_state.session_store.save_kb_metadata(kb)

    logger.info(f"Added {len(papers_to_add)} papers from DOI list to KB '{name}' ({added} chunks)")
    return {
        "added_papers": len(papers_to_add),
        "added_chunks": added,
        "skipped_duplicates": len(skipped),
        "failed": failed,
        "pdf_download": dl,
        "kb": name,
    }


@router.get("/api/paper")
async def get_paper_detail(doi: str):
    """Discovery metadata + abstract + available content type for a DOI.

    Cheap path: live-fetch via the unified pipeline (no per-DOI PaperContent
    file cache is implemented yet — discovery + abstract is fast, ~1-2 s).
    """
    if not doi or not doi.strip():
        raise HTTPException(status_code=400, detail="doi query param required")
    doi = doi.strip().replace("https://doi.org/", "")
    from perspicacite.pipeline.download import retrieve_paper_content

    pdf_kw = _get_pdf_fallback_kwargs(app_state.config.pdf_download if app_state.config else None)
    try:
        result = await retrieve_paper_content(doi, pdf_parser=app_state.pdf_parser, **pdf_kw)
    except Exception as e:
        return {"doi": doi, "error": str(e), "content_type": "none"}
    md = result.metadata or {}
    return {
        "doi": doi,
        "title": md.get("title"),
        "authors": md.get("authors") or [],
        "year": md.get("year"),
        "journal": md.get("journal"),
        "abstract": result.abstract or md.get("abstract"),
        "content_type": result.content_type,
        "content_source": result.content_source,
        "has_full_text": bool(result.full_text),
        "references_count": len(result.references) if result.references else 0,
    }
