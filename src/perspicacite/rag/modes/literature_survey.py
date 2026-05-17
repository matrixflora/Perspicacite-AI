"""Literature Survey RAG Mode - Systematic research field mapping.

This mode is designed for comprehensive literature surveys, not quick answers.
It systematically maps a research field by:
1. Broad search across multiple APIs
2. Abstract analysis in batches (50-100 papers)
3. Theme clustering and identification
4. AI recommendations for deep analysis
5. User-selected full-text analysis (up to 50 papers)
6. Structured survey report with PDF export
"""

import asyncio
import json
import re
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.rag import RAGMode, RAGRequest, RAGResponse, SourceReference
from perspicacite.provenance.context import get_collector
from perspicacite.rag.modes.base import BaseRAGMode
from perspicacite.retrieval.recency import apply_recency_weighting
from perspicacite.search.scilex_adapter import SciLExAdapter

logger = get_logger("perspicacite.rag.modes.literature_survey")


def _target_kb(request: Any) -> str:
    """Return the KB to use for any storage-targeting decisions.

    Literature Survey does NOT retrieve from a KB (it uses external SciLEx
    search), but the API still accepts ``request.kb_names`` for parity with
    other RAG modes. When multiple KBs are supplied, storage / provenance
    must converge on a single target — by convention the first entry.

    Falls back to ``request.kb_name`` when ``kb_names`` is None or empty.
    """
    names = getattr(request, "kb_names", None)
    if names:
        return names[0]
    return request.kb_name


def _apply_recency_to_candidates(
    candidates: list[Any],
    recency_weight: float | None,
    half_life_years: float | None,
) -> list[Any]:
    """Apply recency weighting to a list of PaperCandidate objects.

    PaperCandidate stores its score in ``relevance_score`` (not ``score`` /
    ``paper_score``), so we can't pass the objects directly to the generic
    helpers.  This wrapper converts each candidate to a plain dict with a
    ``_candidate`` back-reference, delegates to ``apply_recency_weighting``,
    writes the adjusted score back, and returns the re-sorted list.
    No-op when *recency_weight* is None or 0.
    """
    if not recency_weight or recency_weight <= 0 or not candidates:
        return candidates

    # Build proxy dicts that the recency helper understands, carrying a
    # reference to the original candidate so we can write the score back.
    proxies = [
        {"year": c.year, "score": float(c.relevance_score or 0.0), "_candidate": c}
        for c in candidates
    ]
    apply_recency_weighting(proxies, recency_weight, half_life_years)

    # Write adjusted scores back and return re-sorted candidates
    for proxy in proxies:
        proxy["_candidate"].relevance_score = proxy["score"]

    return [proxy["_candidate"] for proxy in proxies]


@dataclass
class Theme:
    """A research theme identified from papers."""
    name: str
    description: str
    papers: list[dict[str, Any]] = field(default_factory=list)
    key_insights: list[str] = field(default_factory=list)


@dataclass
class PaperCandidate:
    """A paper candidate for the survey."""
    id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    doi: str | None
    citation_count: int = 0
    relevance_score: float = 0.0
    themes: list[str] = field(default_factory=list)
    recommended: bool = False
    reason: str = ""  # Why recommended


@dataclass
class SurveySession:
    """Persistent session for literature survey."""
    session_id: str
    query: str
    papers: list[PaperCandidate] = field(default_factory=list)
    themes: list[Theme] = field(default_factory=list)
    selected_papers: list[str] = field(default_factory=list)  # Paper IDs
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert session to dict for persistence."""
        return {
            "session_id": self.session_id,
            "query": self.query,
            "papers_count": len(self.papers),
            "themes_count": len(self.themes),
            "selected_count": len(self.selected_papers),
            "created_at": self.created_at.isoformat(),
        }


class LiteratureSurveyRAGMode(BaseRAGMode):
    """
    Literature Survey RAG Mode - Systematic research field mapping.
    
    Key characteristics:
    - Comprehensive coverage (50-100 papers analyzed from abstracts)
    - Dynamic theme identification (3-8 themes)
    - AI recommendations for deep analysis
    - User selection (up to 50 papers)
    - Structured PDF output
    """

    def __init__(self, config: Any):
        super().__init__(config)

        # Configuration
        self.batch_size = 20  # Papers per batch for abstract analysis
        self.max_deep_analysis = 50  # Safety cap for full-text download
        self.relevance_threshold = 2.0  # Lower than agentic for broader coverage
        self.max_themes = 8
        self.min_themes = 3

        # SciLEx for multi-API search
        self.scilex_adapter = SciLExAdapter()

        # Session management
        self.sessions: dict[str, SurveySession] = {}

        # Injected by RAGEngine when a SessionStore is available.
        # Used by _store_references_to_all_kbs (Task 3) to write reference rows.
        # _prepare_kb_context (Task 2) and _store_references_to_all_kbs (Task 3)
        # are called from execute() / execute_stream() in Task 4.
        self.session_store: Any = None

    async def execute(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> RAGResponse:
        """
        Execute literature survey.
        
        This is a multi-phase process:
        1. Broad search
        2. Abstract analysis (batch by batch)
        3. Theme identification
        4. Recommendations
        5. User selection (handled via UI/API)
        6. Deep analysis
        7. Survey generation
        """
        session_id = str(uuid.uuid4())
        session = SurveySession(session_id=session_id, query=request.query)
        self.sessions[session_id] = session

        logger.info("literature_survey_start", query=request.query, session_id=session_id)

        # Prepare KB context: retrieve semantically similar papers from all
        # provided KBs and collect ALL known paper_ids for pre-filtering.
        kb_context_block, known_paper_ids = await self._prepare_kb_context(
            request, vector_store, embedding_provider
        )

        # Phase 1: Broad search
        logger.info("phase_1_search")
        papers = await self._broad_search(request.query, request.databases)

        # Pre-filter: remove papers already in any provided KB
        papers = self._filter_known_papers(papers, known_paper_ids)

        if not papers:
            return RAGResponse(
                answer="No papers found for this topic. Try broadening your search terms.",
                sources=[],
                mode=RAGMode.LITERATURE_SURVEY,
                metadata={"session_id": session_id, "phase": "search_failed"}
            )

        # Convert to candidates
        session.papers = self._convert_to_candidates(papers)
        logger.info("papers_found", count=len(session.papers))

        # Provenance: record broad search
        _c = get_collector()
        if _c is not None:
            _c.add_trace(
                "broad_search",
                detail={"count": len(session.papers), "kb_name": _target_kb(request)},
            )

        # Phase 2 & 3: Batch abstract analysis + theme identification
        logger.info("phase_2_3_analysis")
        session.themes = await self._analyze_abstracts_batch(
            session.papers, request.query, llm
        )
        logger.info("themes_identified", count=len(session.themes))

        # Apply recency weighting on candidates using relevance_score as the score field
        session.papers = _apply_recency_to_candidates(
            session.papers,
            request.recency_weight,
            getattr(request, "recency_half_life_years", None),
        )

        # Provenance: per-paper retrieval events after scoring
        if _c is not None:
            for rank, cand in enumerate(session.papers):
                _c.add_retrieval(
                    paper_id=cand.id,
                    doi=cand.doi,
                    title=cand.title,
                    score=float(cand.relevance_score or 0.0),
                    kb_name=None,
                    content_type=None,
                    pipeline_step=None,
                    rank=rank,
                    stage_label="survey.broad_search",
                )
            _c.add_trace("cluster", detail={"themes": len(session.themes)})

        # Phase 4: Generate recommendations
        logger.info("phase_4_recommendations")
        await self._generate_recommendations(session.papers, session.themes, llm)

        # Provenance: record recommendations stage
        if _c is not None:
            _c.add_trace("recommend")

        # Return interim response - user needs to select papers
        summary = self._generate_interim_summary(session, known_context=kb_context_block)

        # Store references to extra KBs (indices 1..n) for future re-ingestion
        all_kb_names = list(request.kb_names or [request.kb_name])
        recommended_papers = [p for p in session.papers if p.recommended]
        await self._store_references_to_all_kbs(
            recommended_papers, all_kb_names, request.query
        )

        return RAGResponse(
            answer=summary,
            sources=self._convert_to_sources(session.papers),
            mode=RAGMode.LITERATURE_SURVEY,
            metadata={
                "session_id": session_id,
                "phase": "awaiting_selection",
                "papers_count": len(session.papers),
                "themes_count": len(session.themes),
                "recommended_count": sum(1 for p in session.papers if p.recommended),
            }
        )

    async def execute_stream(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> AsyncGenerator[Any, None]:
        """Stream literature survey progress."""
        from perspicacite.models.rag import StreamEvent

        session_id = str(uuid.uuid4())
        session = SurveySession(session_id=session_id, query=request.query)
        self.sessions[session_id] = session

        # Prepare KB context
        kb_context_block, known_paper_ids = await self._prepare_kb_context(
            request, vector_store, embedding_provider
        )

        yield StreamEvent.status("Literature Survey: Initializing...")

        # Phase 1: Search
        yield StreamEvent.status("Literature Survey: Searching across academic databases...")
        papers = await self._broad_search(request.query, request.databases)

        # Pre-filter: remove papers already in any provided KB
        papers = self._filter_known_papers(papers, known_paper_ids)

        if not papers:
            yield StreamEvent.status("Literature Survey: No papers found")
            yield StreamEvent.content("No papers found for this topic. Try broadening your search terms.")
            yield StreamEvent.done(
                conversation_id=session_id,
                tokens_used=0,
                mode="literature_survey",
                iterations=1,
            )
            return

        session.papers = self._convert_to_candidates(papers)
        yield StreamEvent.status(f"Literature Survey: Found {len(session.papers)} papers")

        # Provenance: record broad search
        _c = get_collector()
        if _c is not None:
            _c.add_trace(
                "broad_search",
                detail={"count": len(session.papers), "kb_name": _target_kb(request)},
            )

        # Phase 2: Batch analysis
        yield StreamEvent.status("Literature Survey: Analyzing abstracts in batches...")
        session.themes = await self._analyze_abstracts_batch(
            session.papers, request.query, llm
        )
        yield StreamEvent.status(
            f"Literature Survey: Identified {len(session.themes)} research themes"
        )

        # Apply recency weighting on candidates using relevance_score as the score field
        session.papers = _apply_recency_to_candidates(
            session.papers,
            request.recency_weight,
            getattr(request, "recency_half_life_years", None),
        )

        # Provenance: per-paper retrieval events + cluster trace
        if _c is not None:
            for rank, cand in enumerate(session.papers):
                _c.add_retrieval(
                    paper_id=cand.id,
                    doi=cand.doi,
                    title=cand.title,
                    score=float(cand.relevance_score or 0.0),
                    kb_name=None,
                    content_type=None,
                    pipeline_step=None,
                    rank=rank,
                    stage_label="survey.broad_search",
                )
            _c.add_trace("cluster", detail={"themes": len(session.themes)})

        # Phase 3: Recommendations
        yield StreamEvent.status("Literature Survey: Generating recommendations...")
        await self._generate_recommendations(session.papers, session.themes, llm)

        # Provenance: record recommendations stage
        if _c is not None:
            _c.add_trace("recommend")

        # Emit summary
        summary = self._generate_interim_summary(session, known_context=kb_context_block)
        yield StreamEvent.content(summary)

        # Emit metadata for UI
        import json
        yield StreamEvent(
            event="status",
            data=json.dumps({
                "message": "Literature Survey: Complete",
                "session_id": session_id,
                "papers_count": len(session.papers),
                "themes_count": len(session.themes),
                "recommended_count": sum(1 for p in session.papers if p.recommended),
            })
        )

        # Store references to extra KBs
        all_kb_names = list(request.kb_names or [request.kb_name])
        recommended_papers = [p for p in session.papers if p.recommended]
        await self._store_references_to_all_kbs(
            recommended_papers, all_kb_names, request.query
        )

        yield StreamEvent.done(
            conversation_id=session_id,
            tokens_used=0,
            mode="literature_survey",
            iterations=1,
        )



    async def _broad_search(self, query: str, databases: list[str] | None = None) -> list[Any]:
        """
        Broad search across multiple APIs.
        
        Uses SciLEx to search across selected databases.
        """
        # Default databases if none specified
        if not databases:
            databases = ["semantic_scholar", "openalex", "pubmed"]

        try:
            papers = await self.scilex_adapter.search(
                query=query,
                max_results=100,  # Get more for comprehensive survey
                apis=databases,
            )
            return papers
        except Exception as e:
            logger.error("broad_search_failed", error=str(e))
            return []

    def _convert_to_candidates(self, papers: list[Any]) -> list[PaperCandidate]:
        """Convert SciLEx Paper models to candidates.
        
        Only includes papers with abstracts - these are required for
        AI relevance analysis and theme categorization.
        """
        candidates = []
        skipped_count = 0
        for p in papers:
            # Skip papers without abstracts - can't analyze relevance without content
            if not p.abstract or not p.abstract.strip():
                skipped_count += 1
                continue

            candidate = PaperCandidate(
                id=p.id or str(uuid.uuid4()),
                title=p.title or "Untitled",
                authors=[a.name for a in p.authors] if p.authors else [],
                year=p.year,
                abstract=p.abstract,
                doi=p.doi,
                citation_count=p.citation_count or 0,
            )
            candidates.append(candidate)

        if skipped_count > 0:
            logger.info("papers_without_abstracts_skipped", count=skipped_count)

        return candidates

    async def _prepare_kb_context(
        self,
        request: Any,
        vector_store: Any,
        embedding_provider: Any,
        top_k: int = 10,
    ) -> tuple[str, set[str]]:
        """Retrieve known papers from all provided KBs.

        Performs two operations:
        1. Fetches ALL paper_ids from every KB's ChromaDB collection (for
           pre-filtering broad search results).
        2. Runs a semantic top-K search across KBs (via _build_kb_retriever)
           and formats a human-readable context block for the survey summary.

        Returns:
            context_block: Formatted string listing known papers (for summary).
            all_known_ids: Full set of paper_ids/DOIs already in any provided KB.

        Both return values are empty if kb_names is absent or empty.
        Never raises — errors are caught and logged.
        """
        from perspicacite.models.kb import chroma_collection_name_for_kb

        kb_names: list[str] = list(getattr(request, "kb_names", None) or [])
        if not kb_names:
            return "", set()

        # ── A. Collect ALL paper_ids from ChromaDB across every KB ──────────────
        all_known_ids: set[str] = set()
        for kb_name in kb_names:
            col = chroma_collection_name_for_kb(kb_name)
            try:
                rows = await vector_store.list_paper_ids_in_collection(col)
                # rows: list[tuple[paper_id, title, chunk_count]]
                all_known_ids.update(pid for pid, _, _ in rows)
            except Exception as exc:
                logger.warning(
                    "survey_kb_id_fetch_error", kb=kb_name, error=str(exc)
                )

        # ── B. Semantic top-K retrieval for the context block ───────────────────
        context_block = ""
        try:
            retriever = self._build_kb_retriever(request, vector_store, embedding_provider)
            results = await retriever.search(request.query, top_k=top_k)
            if results:
                lines: list[str] = []
                seen_pids: set[str] = set()
                for r in results:
                    pid = r.get("paper_id") or ""
                    if pid and pid in seen_pids:
                        continue
                    if pid:
                        seen_pids.add(pid)
                    meta = r.get("metadata")
                    title = (getattr(meta, "title", None) or "Unknown title")
                    year = getattr(meta, "year", None) or ""
                    doi = getattr(meta, "doi", None) or ""
                    kb_tag = r.get("kb_name") or ""
                    line = f"- {title} ({year})"
                    if kb_tag:
                        line += f" [KB: {kb_tag}]"
                    if doi:
                        line += f" DOI: {doi}"
                    lines.append(line)
                if lines:
                    context_block = (
                        "Papers already in your knowledge base(s) — "
                        "excluded from new-paper analysis:\n"
                        + "\n".join(lines)
                    )
        except Exception as exc:
            logger.warning("survey_kb_context_retrieval_error", error=str(exc))

        logger.info(
            "survey_kb_context_prepared",
            known_ids_total=len(all_known_ids),
            context_lines=len(context_block.splitlines()),
            kb_names=kb_names,
        )
        return context_block, all_known_ids

    def _filter_known_papers(
        self,
        papers: list[Any],
        known_paper_ids: set[str],
    ) -> list[Any]:
        """Remove papers already present in any provided KB.

        A paper is excluded when its ``id`` or ``doi`` appears in
        ``known_paper_ids``.  Papers with no identifiers are kept.
        """
        if not known_paper_ids or not papers:
            return papers
        before_count = len(papers)
        filtered = [
            p for p in papers
            if (getattr(p, "id", None) not in known_paper_ids)
            and (not getattr(p, "doi", None) or getattr(p, "doi", None) not in known_paper_ids)
        ]
        filtered_count = before_count - len(filtered)
        if filtered_count:
            logger.info("survey_known_papers_filtered", count=filtered_count)
        return filtered

    async def _store_references_to_all_kbs(
        self,
        papers: list[Any],
        kb_names: list[str],
        survey_query: str,
    ) -> int:
        """Store reference rows in SQLite for every KB beyond the first.

        ``kb_names[0]`` (the primary KB) already receives full ingestion via the
        existing ``add_paper_to_kb`` path.  Indices 1..n receive a lightweight
        ``kb_paper_references`` row per paper so a future ``add_dois_to_kb`` /
        rebuild can fully ingest them.

        Only papers with a non-null ``doi`` are stored (papers without a DOI
        cannot be looked up by a future ingestion command anyway).

        Returns the total number of NEW rows written.
        Never raises.
        """
        if self.session_store is None or len(kb_names) < 2:
            return 0

        extra_kbs = kb_names[1:]
        total = 0
        query_snippet = str(survey_query)[:200]

        for kb_name in extra_kbs:
            for paper in papers:
                doi = getattr(paper, "doi", None)
                if not doi:
                    continue  # skip: no DOI means can't re-ingest via add_dois_to_kb
                try:
                    authors = [str(a) for a in (getattr(paper, "authors", []) or [])]
                    abstract_raw = getattr(paper, "abstract", None)
                    abstract = abstract_raw[:500] if abstract_raw else None
                    new = await self.session_store.store_paper_reference(
                        kb_name=kb_name,
                        doi=doi,
                        title=str(getattr(paper, "title", "") or "Untitled"),
                        authors=authors,
                        year=getattr(paper, "year", None),
                        abstract=abstract,
                        survey_query=query_snippet,
                    )
                    if new:
                        total += 1
                        logger.info(
                            "survey_reference_stored",
                            kb=kb_name,
                            doi=doi,
                        )
                except Exception as exc:
                    logger.warning(
                        "survey_reference_store_error",
                        kb=kb_name,
                        paper=str(getattr(paper, "title", "?"))[:50],
                        error=str(exc),
                    )

        logger.info(
            "survey_references_complete",
            extra_kbs=extra_kbs,
            total_new=total,
        )
        return total

    async def _analyze_abstracts_batch(
        self,
        papers: list[PaperCandidate],
        query: str,
        llm: Any,
    ) -> list[Theme]:
        """
        Analyze abstracts in batches and identify themes.
        
        Process:
        1. Score each paper's relevance (1-5)
        2. Accumulate insights across batches
        3. Identify themes from patterns
        """
        logger.info("theme_analysis_start", total_papers=len(papers))

        # Filter papers with abstracts
        papers_with_abstracts = [p for p in papers if p.abstract]

        logger.info("theme_analysis_papers_with_abstracts", count=len(papers_with_abstracts))

        if not papers_with_abstracts:
            logger.warning("no_abstracts_found")
            return []

        # Process in batches
        all_analyses = []
        total_batches = (len(papers_with_abstracts) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(papers_with_abstracts), self.batch_size):
            batch = papers_with_abstracts[i:i + self.batch_size]
            batch_num = i // self.batch_size + 1

            logger.info(f"Analyzing batch {batch_num}/{total_batches}")

            batch_analysis = await self._analyze_batch(batch, query, llm)
            all_analyses.extend(batch_analysis)

            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)

        # Update papers with scores
        logger.info("batch_analysis_complete", successful_analyses=len(all_analyses), total_papers=len(papers_with_abstracts))

        for analysis in all_analyses:
            for p in papers_with_abstracts:
                if p.id == analysis.get("paper_id"):
                    p.relevance_score = analysis.get("relevance_score", 0)
                    break

        # Identify themes from all analyses
        themes = await self._identify_themes(all_analyses, query, llm)
        logger.info("themes_identified", count=len(themes), theme_names=[t.name for t in themes])

        # Assign papers to themes (all papers have abstracts)
        await self._assign_papers_to_themes(papers_with_abstracts, themes, llm)

        # Log theme statistics
        for theme in themes:
            logger.info("theme_stats", name=theme.name, paper_count=len(theme.papers))

        return themes

    async def _analyze_batch(
        self,
        batch: list[PaperCandidate],
        query: str,
        llm: Any
    ) -> list[dict[str, Any]]:
        """Analyze a single batch of papers."""
        # Format papers for prompt (shorter abstracts to save tokens)
        papers_text = "\n\n".join([
            f"PAPER {i+1} (ID: {p.id}):\nTitle: {p.title}\nAbstract: {p.abstract[:300]}"
            for i, p in enumerate(batch)
        ])

        prompt = f"""Analyze these papers for: "{query}"

For each paper, return JSON with:
- paper_id: use the ID shown
- relevance_score: 1-5 (how relevant to query)
- key_concepts: list of main topics
- methodology: brief methods used
- contribution: main contribution

PAPERS:
{papers_text}

JSON ONLY (no other text):
{{
  "analyses": [
    {{"paper_id": "...", "relevance_score": 4, "key_concepts": ["..."], "methodology": "...", "contribution": "..."}}
  ]
}}"""

        try:
            messages = [{"role": "user", "content": prompt}]
            # Increased token limit to handle larger responses
            response = await llm.complete(
                messages, temperature=0.3, max_tokens=4000, stage="survey.cluster"
            )

            # Parse JSON with better error handling
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                # Try to fix common JSON issues
                json_str = self._fix_json(json_str)
                data = json.loads(json_str)
                return data.get("analyses", [])
            return []
        except Exception as e:
            logger.error("batch_analysis_failed", error=str(e), response_preview=response[:200] if 'response' in locals() else "N/A")
            return []

    def _fix_json(self, json_str: str) -> str:
        """Fix common JSON formatting issues from LLM responses."""
        # Remove trailing commas before closing brackets
        import re
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        # Remove any markdown code block markers
        json_str = json_str.replace("```json", "").replace("```", "")
        return json_str.strip()

    async def _identify_themes(
        self,
        analyses: list[dict[str, Any]],
        query: str,
        llm: Any
    ) -> list[Theme]:
        """Identify research themes from all analyses."""
        logger.info("identifying_themes", analyses_count=len(analyses))

        # If no analyses, create generic themes based on the query
        if not analyses:
            logger.warning("no_analyses_for_themes", creating_generic_themes=True)
            return [
                Theme(name=f"{query.title()} Research", description=f"Research related to {query}"),
                Theme(name="Methods and Approaches", description="Methodologies and techniques"),
                Theme(name="Applications", description="Practical applications and use cases"),
            ]

        # Aggregate key concepts
        all_concepts = []
        for a in analyses:
            all_concepts.extend(a.get("key_concepts", []))

        # If no concepts found, create generic themes
        if not all_concepts:
            logger.warning("no_concepts_found", creating_generic_themes=True)
            return [
                Theme(name=f"{query.title()} Research", description=f"Research related to {query}"),
                Theme(name="Related Topics", description="Related research areas"),
            ]

        concepts_text = ", ".join(set(all_concepts))
        logger.info("theme_concepts_aggregated", unique_concepts=len(set(all_concepts)))

        prompt = f"""Based on these research concepts from papers on "{query}",
identify the main research themes (3-8 themes).

CONCEPTS FOUND:
{concepts_text}

Respond in JSON format:
{{
    "themes": [
        {{
            "name": "Theme Name",
            "description": "Brief description of this research theme"
        }}
    ]
}}"""

        try:
            messages = [{"role": "user", "content": prompt}]
            response = await llm.complete(
                messages, temperature=0.3, max_tokens=2000, stage="survey.cluster"
            )

            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                theme_data = data.get("themes", [])
                themes = [Theme(name=t["name"], description=t["description"]) for t in theme_data]
                logger.info("themes_parsed_successfully", count=len(themes))
                return themes
            logger.warning("no_json_found_in_theme_response")
            return []
        except Exception as e:
            logger.error("theme_identification_failed", error=str(e))
            return []

    async def _assign_papers_to_themes(
        self,
        papers: list[PaperCandidate],
        themes: list[Theme],
        llm: Any
    ):
        """Assign papers to themes based on content.
        
        All papers passed to this method are expected to have abstracts.
        Papers without abstracts are filtered out during candidate conversion.
        """
        if not themes:
            logger.warning("no_themes_to_assign_papers")
            return

        theme_names = [t.name for t in themes]
        logger.info("assigning_papers_to_themes", papers_count=len(papers), themes=theme_names)

        assigned_count = 0

        for paper in papers:
            prompt = f"""Which theme(s) does this paper belong to?

THEMES: {', '.join(theme_names)}

PAPER: {paper.title}
ABSTRACT: {paper.abstract[:400]}

Respond with theme names separated by commas, or "None" if no match."""

            try:
                messages = [{"role": "user", "content": prompt}]
                response = await llm.complete(
                    messages, temperature=0.2, max_tokens=100, stage="survey.cluster"
                )

                if "None" not in response:
                    assigned = [t.strip() for t in response.split(",") if t.strip() in theme_names]
                    paper.themes = assigned
                    assigned_count += 1

                    # Add to theme's paper list
                    for theme_name in assigned:
                        for theme in themes:
                            if theme.name == theme_name:
                                theme.papers.append(paper.__dict__)
                                break
            except Exception as e:
                logger.warning("paper_theme_assignment_failed", paper=paper.title[:50], error=str(e))

        # If no papers were assigned, assign all to first theme as fallback
        if assigned_count == 0 and themes and papers:
            logger.warning("no_papers_assigned", using_fallback_assignment=True)
            for paper in papers:
                paper.themes = [themes[0].name]
                themes[0].papers.append(paper.__dict__)
            assigned_count = len(papers)

        logger.info("paper_theme_assignment_complete", assigned=assigned_count, total=len(papers))

    async def _generate_recommendations(
        self,
        papers: list[PaperCandidate],
        themes: list[Theme],
        llm: Any
    ):
        """Generate AI recommendations for deep analysis."""
        logger.info("generating_recommendations", total_papers=len(papers))

        # Ensure all papers have at least a minimum relevance score
        for p in papers:
            if p.relevance_score < 1.0:  # If no score assigned, give default
                p.relevance_score = 2.0  # Default to "somewhat relevant"

        # Filter to relevant papers (use lower threshold for more inclusive results)
        relevant_threshold = 1.5  # Slightly lower than default 2.0
        relevant_papers = [p for p in papers if p.relevance_score >= relevant_threshold]

        logger.info("relevant_papers_filtered", count=len(relevant_papers), threshold=relevant_threshold)

        # If still no relevant papers, use all papers
        if not relevant_papers:
            logger.warning("no_relevant_papers_using_all", total_papers=len(papers))
            relevant_papers = papers

        # Select diverse, high-impact papers
        # Criteria: citation count, theme representation, recency

        recommendations = []

        # 1. Highest cited from each theme (representative)
        for theme in themes:
            theme_papers = [p for p in relevant_papers if theme.name in p.themes]
            if theme_papers:
                top_cited = max(theme_papers, key=lambda p: p.citation_count)
                if top_cited not in recommendations:
                    recommendations.append(top_cited)
                    top_cited.recommended = True
                    top_cited.reason = f"Highly cited in theme: {theme.name}"

        # 2. Recent papers (last 3 years) with good relevance
        recent_papers = [
            p for p in relevant_papers
            if p.year and p.year >= datetime.now().year - 3 and p not in recommendations
        ]
        recent_papers.sort(key=lambda p: p.relevance_score, reverse=True)
        for p in recent_papers[:5]:
            p.recommended = True
            p.reason = "Recent advance in the field"
            recommendations.append(p)

        # 3. Fill remaining slots with high-relevance papers
        remaining = [p for p in relevant_papers if p not in recommendations]
        remaining.sort(key=lambda p: (p.relevance_score, p.citation_count), reverse=True)

        for p in remaining[:self.max_deep_analysis - len(recommendations)]:
            p.recommended = True
            p.reason = "Highly relevant to the topic"
            recommendations.append(p)

        logger.info("recommendations_complete", count=len(recommendations))

    def _generate_interim_summary(
        self, session: SurveySession, known_context: str = ""
    ) -> str:
        """Generate interim summary for user selection."""
        lines = [
            f"# Literature Survey: {session.query}",
            "",
            f"**Found {len(session.papers)} papers** across {len(session.themes)} research themes.",
            "",
            "## Identified Themes",
            "",
        ]

        for theme in session.themes:
            paper_count = len(theme.papers)
            lines.append(f"### {theme.name}")
            lines.append(f"{theme.description}")
            lines.append(f"*{paper_count} papers*")
            lines.append("")

        recommended = [p for p in session.papers if p.recommended]
        lines.extend([
            "## Recommendations",
            "",
            f"**{len(recommended)} papers recommended** for deep analysis (of {self.max_deep_analysis} max).",
            "",
            "The AI has selected papers based on:",
            "- Citation impact (seminal works)",
            "- Theme representation (diverse coverage)",
            "- Recency (recent advances)",
            "- Relevance to your query",
            "",
            "### Next Steps",
            "1. Review the recommended papers below",
            "2. Add/remove papers as needed",
            "3. Click 'Generate Survey' for full analysis",
            "",
            "---",
            "",
            "## Recommended Papers",
            "",
        ])

        for p in recommended[:20]:  # Show top 20
            lines.append(f"- **{p.title}** ({p.year})")
            lines.append(f"  - Authors: {', '.join(p.authors[:3])}")
            lines.append(f"  - Citations: {p.citation_count} | Relevance: {p.relevance_score}/5")
            lines.append(f"  - Why: {p.reason}")
            lines.append("")

        if known_context:
            lines.extend([
                "",
                "---",
                "",
                "## Already in Your Knowledge Base(s)",
                "",
                known_context,
            ])

        return "\n".join(lines)

    def _convert_to_sources(self, papers: list[PaperCandidate]) -> list[SourceReference]:
        """Convert papers to source references."""
        return [
            SourceReference(
                title=p.title,
                authors=", ".join(p.authors[:3]) if p.authors else None,
                year=p.year,
                doi=p.doi,
                relevance_score=p.relevance_score,
            )
            for p in papers
        ]

    # Public methods for API/UI integration

    def get_session(self, session_id: str) -> SurveySession | None:
        """Get a survey session by ID."""
        return self.sessions.get(session_id)

    def update_selection(self, session_id: str, selected_paper_ids: list[str]) -> bool:
        """Update user paper selection."""
        session = self.sessions.get(session_id)
        if not session:
            return False

        # Validate - don't exceed max
        if len(selected_paper_ids) > self.max_deep_analysis:
            selected_paper_ids = selected_paper_ids[:self.max_deep_analysis]

        session.selected_papers = selected_paper_ids
        return True

    async def generate_deep_analysis(
        self,
        session_id: str,
        llm: Any,
    ) -> RAGResponse:
        """
        Generate deep analysis for selected papers.
        
        This is Phase 2 - after user selection.
        """
        session = self.sessions.get(session_id)
        if not session:
            return RAGResponse(
                answer="Session not found.",
                sources=[],
                mode=RAGMode.LITERATURE_SURVEY,
            )

        # Get selected papers
        selected = [p for p in session.papers if p.id in session.selected_papers]

        if not selected:
            return RAGResponse(
                answer="No papers selected for analysis.",
                sources=[],
                mode=RAGMode.LITERATURE_SURVEY,
            )

        logger.info("deep_analysis_start", session_id=session_id, papers=len(selected))

        # TODO: Download full texts and analyze
        # For now, return structured summary

        survey_report = await self._generate_survey_report(session, selected, llm)

        return RAGResponse(
            answer=survey_report,
            sources=self._convert_to_sources(selected),
            mode=RAGMode.LITERATURE_SURVEY,
            metadata={
                "session_id": session_id,
                "phase": "completed",
                "papers_analyzed": len(selected),
                "themes": len(session.themes),
            }
        )

    async def _generate_survey_report(
        self,
        session: SurveySession,
        selected_papers: list[PaperCandidate],
        llm: Any
    ) -> str:
        """Generate final structured survey report."""
        # NOTE (Capsule Cycle B): No multimodal hook here. The survey report is
        # deterministic text aggregation; the LLM calls in this mode
        # (_analyze_batch, _identify_themes, _assign_papers_to_themes) are
        # intermediate paper-metadata processing, not final user-facing synthesis.
        # If/when a final-synthesis LLM call is added, wire via
        # perspicacite.rag.multimodal.wrap_messages_for_chunks here.
        # TODO: Full implementation with PDF export

        lines = [
            f"# Literature Survey Report: {session.query}",
            f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "\n---\n",
            "## Executive Summary",
            f"\nThis survey analyzed {len(selected_papers)} papers across {len(session.themes)} research themes.",
            "\n## Research Themes",
        ]

        for theme in session.themes:
            lines.append(f"\n### {theme.name}")
            lines.append(theme.description)

        lines.extend([
            "\n## Annotated Bibliography",
            "",
        ])

        for i, p in enumerate(selected_papers[:20], 1):
            lines.append(f"{i}. **{p.title}** ({p.year})")
            lines.append(f"   - {', '.join(p.authors[:3])}")
            lines.append(f"   - {p.abstract[:300]}...")
            lines.append("")

        return "\n".join(lines)
