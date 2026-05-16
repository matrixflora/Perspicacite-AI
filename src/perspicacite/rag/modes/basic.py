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

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.models.rag import RAGMode, RAGRequest, RAGResponse, SourceReference, StreamEvent
from perspicacite.models.kb import chroma_collection_name_for_kb
from perspicacite.provenance.context import get_collector
from perspicacite.config.schema import MultimodalMode
from perspicacite.rag.code_excerpts import collect_code_excerpts
from perspicacite.rag.figure_refs import collect_figure_refs
from perspicacite.rag.utils import flatten_paper_results_to_chunks
from perspicacite.rag.modes.base import BaseRAGMode
from perspicacite.rag.multimodal import wrap_messages_for_chunks
from perspicacite.rag.prompts import (
    DEFAULT_SYSTEM_PROMPT,
)
from perspicacite.retrieval.hybrid import hybrid_retrieval
from perspicacite.rag.conversation_helpers import (
    build_user_message_with_history,
    compute_retrieval_query,
    format_conversation_block,
)
from perspicacite.rag.query_scope import resolve_paper_scope_for_query
from perspicacite.rag.utils import (
    format_references,
    prepare_sources,
    get_doc_citation,
    format_documents_for_prompt,
    format_paper_results_for_prompt,
    get_system_prompt,
)

logger = get_logger("perspicacite.rag.modes.basic")


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
        cap = min(5, getattr(request, "max_papers_retrieval", None) or 5)

        if self.use_two_pass:
            # Two-pass retrieval — identify papers, then fetch all their chunks
            paper_results = await dkb.search_two_pass(
                retrieval_query,
                top_k=self.final_max_docs,
                paper_scope=scope,
                max_papers_cap=cap,
            )
            logger.info("basic_two_pass", papers=len(paper_results))
        else:
            # Legacy chunk-level retrieval (no two-pass)
            chunk_results = await dkb.search(retrieval_query, top_k=self.final_max_docs)
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

        # Apply optional recency weighting
        if getattr(request, "recency_weight", None):
            from perspicacite.retrieval.recency import apply_recency_weighting

            paper_results = apply_recency_weighting(
                paper_results,
                request.recency_weight,
                getattr(request, "recency_half_life_years", None),
            )

        # Provenance: record retrieval events
        _c = get_collector()
        if _c is not None:
            _c.add_trace("retrieve", detail={"kb_name": request.kb_name, "count": len(paper_results)})
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
                    doi=p.get("doi"),
                    relevance_score=p.get("paper_score", 0.0),
                    kb_name=p.get("kb_name"),
                    metadata=p.get("paper_metadata"),
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
        cap = min(5, getattr(request, "max_papers_retrieval", None) or 5)

        if self.use_two_pass:
            paper_results = await dkb.search_two_pass(
                retrieval_query,
                top_k=self.final_max_docs,
                paper_scope=scope,
                max_papers_cap=cap,
            )
        else:
            chunk_results = await dkb.search(retrieval_query, top_k=self.final_max_docs)
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

        # Apply optional recency weighting
        if getattr(request, "recency_weight", None):
            from perspicacite.retrieval.recency import apply_recency_weighting

            paper_results = apply_recency_weighting(
                paper_results,
                request.recency_weight,
                getattr(request, "recency_half_life_years", None),
            )

        # Provenance: record retrieval events
        _c = get_collector()
        if _c is not None:
            _c.add_trace("retrieve", detail={"kb_name": request.kb_name, "count": len(paper_results)})
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

        # Prepare sources
        sources = []
        for p in paper_results:
            sources.append(
                SourceReference(
                    title=p.get("title") or "Untitled",
                    authors=p.get("authors"),
                    year=p.get("year"),
                    doi=p.get("doi"),
                    relevance_score=p.get("paper_score", 0.0),
                    kb_name=p.get("kb_name"),
                    metadata=p.get("paper_metadata"),
                )
            )
        for source in sources:
            yield StreamEvent.source(source)

        if not paper_results:
            yield StreamEvent.content("No relevant documents found to answer your question.")
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
        except Exception as exc:  # noqa: BLE001
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
