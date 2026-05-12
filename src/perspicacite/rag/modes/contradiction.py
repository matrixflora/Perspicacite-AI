"""Contradiction-detection RAG mode.

Surfaces agreement / disagreement / open questions across papers by:
1. Retrieving chunks for the query.
2. Grouping chunks by paper.
3. Asking the LLM to summarise each paper's claims.
4. Clustering the summaries into consensus / disagreement / open buckets.
5. Streaming a structured three-section brief.

Degrades gracefully when fewer than MIN_PAPERS_FOR_ANALYSIS papers are found,
producing a note and a normal advanced-style answer instead.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from perspicacite.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
from perspicacite.models.kb import chroma_collection_name_for_kb
from perspicacite.models.rag import (
    RAGMode,
    RAGRequest,
    RAGResponse,
    SourceReference,
    StreamEvent,
)
from perspicacite.rag import prompts as _prompts
from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
from perspicacite.rag.modes.base import BaseRAGMode

logger = get_logger("perspicacite.rag.modes.contradiction")

MIN_PAPERS_FOR_ANALYSIS = 3
RETRIEVAL_TOP_K = 25


class ContradictionRAGMode(BaseRAGMode):
    """Contradiction-detection RAG mode."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.settings = config.rag_modes.contradiction

    # ------------------------------------------------------------------
    # Retrieval seam (monkeypatched in tests)
    # ------------------------------------------------------------------

    async def _retrieve(
        self,
        request: RAGRequest,
        vector_store: Any,
        embedding_provider: Any,
    ) -> list[dict[str, Any]]:
        """Retrieve chunks from the KB, returning raw dicts from dkb.search()."""
        dkb = DynamicKnowledgeBase(
            vector_store=vector_store,
            embedding_service=embedding_provider,
        )
        dkb.collection_name = chroma_collection_name_for_kb(request.kb_name)
        dkb._initialized = True
        results = await dkb.search(request.query, top_k=RETRIEVAL_TOP_K)
        if getattr(request, "recency_weight", None):
            from perspicacite.retrieval.recency import apply_recency_weighting

            results = apply_recency_weighting(
                results,
                request.recency_weight,
                getattr(request, "recency_half_life_years", None),
            )
        return results

    # ------------------------------------------------------------------
    # Grouping
    # ------------------------------------------------------------------

    def _group_by_paper(self, chunks: list[Any]) -> dict[str, list[Any]]:
        """Group chunks (dicts or objects) by paper_id."""
        out: dict[str, list[Any]] = {}
        for c in chunks:
            if isinstance(c, dict):
                meta = c.get("metadata") or {}
                if hasattr(meta, "paper_id"):
                    pid = meta.paper_id or meta.doi or "?"
                else:
                    pid = meta.get("paper_id") or meta.get("doi") or "?"
            else:
                meta = getattr(c, "metadata", {}) or {}
                pid = meta.get("paper_id") or meta.get("doi") or "?"
            out.setdefault(pid, []).append(c)
        return out

    def _chunk_text(self, chunk: Any) -> str:
        """Extract text from a chunk (dict or object)."""
        if isinstance(chunk, dict):
            return chunk.get("text", "") or ""
        return getattr(chunk, "text", "") or ""

    def _chunk_score(self, chunk: Any) -> float:
        """Extract relevance score from a chunk."""
        if isinstance(chunk, dict):
            return float(chunk.get("score", 0.0))
        return float(getattr(chunk, "score", 0.0))

    def _chunk_meta(self, chunk: Any) -> dict[str, Any]:
        """Extract metadata dict from a chunk."""
        if isinstance(chunk, dict):
            meta = chunk.get("metadata") or {}
        else:
            meta = getattr(chunk, "metadata", {}) or {}
        if hasattr(meta, "__dict__"):
            # Pydantic / dataclass object → convert to dict
            try:
                return meta.model_dump()
            except AttributeError:
                return dict(vars(meta))
        return dict(meta)

    # ------------------------------------------------------------------
    # Per-paper claim summarisation
    # ------------------------------------------------------------------

    async def _summarize_claims(
        self,
        by_paper: dict[str, list[Any]],
        query: str,
        llm: Any,
        cap: int,
    ) -> list[dict[str, Any]]:
        """Ask LLM for 2-4 bullet claims per paper (up to *cap* papers)."""
        summaries: list[dict[str, Any]] = []
        for pid in list(by_paper.keys())[:cap]:
            chunks = by_paper[pid]
            excerpts = "\n---\n".join(self._chunk_text(c)[:600] for c in chunks[:4])
            meta = self._chunk_meta(chunks[0])
            title = meta.get("title") or pid
            doi = meta.get("doi") or pid
            prompt = _prompts.CONTRADICTION_CLAIM_SUMMARY_PROMPT.format(
                query=query,
                title=title,
                excerpts=excerpts,
            )
            try:
                claims = await llm.complete(messages=[{"role": "user", "content": prompt}])
                if not isinstance(claims, str):
                    claims = str(claims)
            except Exception as exc:
                logger.warning("claim_summary_failed", paper_id=pid, error=str(exc))
                claims = "(summary unavailable)"
            summaries.append(
                {
                    "paper_id": pid,
                    "title": title,
                    "doi": doi,
                    "claims": claims,
                }
            )
        return summaries

    # ------------------------------------------------------------------
    # Cluster claims into buckets
    # ------------------------------------------------------------------

    async def _cluster_claims(
        self,
        query: str,
        paper_summaries: list[dict[str, Any]],
        llm: Any,
    ) -> dict[str, Any]:
        """Cluster per-paper summaries into consensus / disagreement / open."""
        summaries_text = "\n\n".join(
            f"Paper [{s['title']} | {s['doi']}]:\n{s['claims']}" for s in paper_summaries
        )
        prompt = _prompts.CONTRADICTION_CLUSTER_PROMPT.format(
            query=query,
            summaries=summaries_text,
        )
        empty: dict[str, Any] = {"consensus": [], "disagreement": [], "open": []}
        try:
            raw = await llm.complete(messages=[{"role": "user", "content": prompt}])
            if not isinstance(raw, str):
                raw = str(raw)
            # Strip markdown code fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```", 2)[-1].lstrip("json").strip()
                if raw.endswith("```"):
                    raw = raw[:-3].strip()
            clusters: dict[str, Any] = json.loads(raw)
            # Ensure expected keys exist
            clusters.setdefault("consensus", [])
            clusters.setdefault("disagreement", [])
            clusters.setdefault("open", [])
            return clusters
        except Exception as exc:
            logger.warning("cluster_claims_failed", error=str(exc))
            return empty

    # ------------------------------------------------------------------
    # Synthesis streaming
    # ------------------------------------------------------------------

    async def _synthesize_stream(
        self,
        query: str,
        clusters: dict[str, Any],
        paper_summaries: list[dict[str, Any]],
        llm: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Stream the structured three-section brief."""
        summaries_text = "\n\n".join(
            f"Paper [{s['title']} | {s['doi']}]:\n{s['claims']}" for s in paper_summaries
        )
        prompt = _prompts.CONTRADICTION_SYNTHESIS_PROMPT.format(
            query=query,
            clusters=json.dumps(clusters, indent=2),
            summaries=summaries_text,
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            async for chunk in llm.stream(messages=messages, max_tokens=2000, temperature=0.3):
                yield StreamEvent.content(chunk)
        except AttributeError:
            # LLM has no stream() method; fall back to one-shot complete()
            try:
                answer = await llm.complete(messages=messages)
                if not isinstance(answer, str):
                    answer = str(answer)
                yield StreamEvent.content(answer)
            except Exception as exc:
                logger.error("synthesis_failed", error=str(exc))
                yield StreamEvent.content(
                    f"(Synthesis failed: {exc}. See per-paper summaries above.)"
                )
        except Exception as exc:
            logger.error("synthesis_stream_failed", error=str(exc))
            # Try non-streaming fallback
            try:
                answer = await llm.complete(messages=messages)
                if not isinstance(answer, str):
                    answer = str(answer)
                yield StreamEvent.content(answer)
            except Exception as exc2:
                logger.error("synthesis_fallback_failed", error=str(exc2))
                yield StreamEvent.content(
                    f"(Synthesis failed: {exc2}. See per-paper summaries above.)"
                )

    # ------------------------------------------------------------------
    # Fallback (< MIN_PAPERS)
    # ------------------------------------------------------------------

    async def _fallback_answer_stream(
        self,
        request: RAGRequest,
        chunks: list[Any],
        llm: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Simple one-shot answer from raw chunks when there are too few papers."""
        if not chunks:
            yield StreamEvent.content("No relevant documents found.")
            return
        context = "\n\n---\n\n".join(self._chunk_text(c)[:800] for c in chunks[:8])
        messages = self._build_messages(
            query=request.query,
            context=context,
        )
        try:
            async for chunk in llm.stream(messages=messages, max_tokens=1500, temperature=0.3):
                yield StreamEvent.content(chunk)
        except AttributeError:
            try:
                answer = await llm.complete(messages=messages)
                if not isinstance(answer, str):
                    answer = str(answer)
                yield StreamEvent.content(answer)
            except Exception as exc:
                yield StreamEvent.content(f"(Answer generation failed: {exc})")
        except Exception:
            try:
                answer = await llm.complete(messages=messages)
                if not isinstance(answer, str):
                    answer = str(answer)
                yield StreamEvent.content(answer)
            except Exception as exc2:
                yield StreamEvent.content(f"(Answer generation failed: {exc2})")

    # ------------------------------------------------------------------
    # Build sources list
    # ------------------------------------------------------------------

    def _build_sources(
        self,
        by_paper: dict[str, list[Any]],
        real_papers: list[str],
    ) -> list[SourceReference]:
        sources: list[SourceReference] = []
        for pid in real_papers:
            chunks = by_paper[pid]
            meta = self._chunk_meta(chunks[0])
            sources.append(
                SourceReference(
                    title=meta.get("title") or pid,
                    authors=meta.get("authors"),
                    year=meta.get("year"),
                    doi=meta.get("doi"),
                    relevance_score=min(1.0, max(0.0, self._chunk_score(chunks[0]))),
                )
            )
        return sources

    # ------------------------------------------------------------------
    # execute_stream — main streaming entry point
    # ------------------------------------------------------------------

    async def execute_stream(  # type: ignore[override]
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Execute contradiction-detection RAG with streaming."""
        try:
            yield StreamEvent.status("Contradiction analysis: retrieving documents...")
            chunks = await self._retrieve(request, vector_store, embedding_provider)
            by_paper = self._group_by_paper(chunks)
            real_papers = [p for p in by_paper if p != "?"]
            n = len(real_papers)

            if n < MIN_PAPERS_FOR_ANALYSIS:
                yield StreamEvent.content(
                    f"_Note: contradiction analysis works best with at least "
                    f"{MIN_PAPERS_FOR_ANALYSIS} papers; "
                    f"only {n} found in this knowledge base. "
                    f"Answering normally instead._\n\n"
                )
                async for ev in self._fallback_answer_stream(request, chunks, llm):
                    yield ev
                # Emit source events for what we have
                for pid in real_papers:
                    meta = self._chunk_meta(by_paper[pid][0])
                    yield StreamEvent.source(
                        SourceReference(
                            title=meta.get("title") or pid,
                            authors=meta.get("authors"),
                            year=meta.get("year"),
                            doi=meta.get("doi"),
                            relevance_score=min(
                                1.0,
                                max(0.0, self._chunk_score(by_paper[pid][0])),
                            ),
                        )
                    )
                yield StreamEvent.done(
                    conversation_id="",
                    tokens_used=0,
                    mode="contradiction",
                    iterations=1,
                )
                return

            yield StreamEvent.status(
                f"Contradiction analysis: comparing claims across {n} papers..."
            )
            cap = getattr(self.settings, "map_reduce_max_papers", 8)
            paper_summaries = await self._summarize_claims(by_paper, request.query, llm, cap)
            clusters = await self._cluster_claims(request.query, paper_summaries, llm)

            async for ev in self._synthesize_stream(request.query, clusters, paper_summaries, llm):
                yield ev

            sources = self._build_sources(by_paper, real_papers)
            for source in sources:
                yield StreamEvent.source(source)

            yield StreamEvent.done(
                conversation_id="",
                tokens_used=0,
                mode="contradiction",
                iterations=1,
            )

        except Exception as exc:
            logger.error("contradiction_mode_error", error=str(exc))
            yield StreamEvent(event="error", data=json.dumps({"message": str(exc)}))

    # ------------------------------------------------------------------
    # execute — non-streaming entry point
    # ------------------------------------------------------------------

    async def execute(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> RAGResponse:
        """Execute contradiction-detection RAG, collecting stream into a RAGResponse."""
        answer_parts: list[str] = []
        sources: list[SourceReference] = []

        async for ev in self.execute_stream(
            request=request,
            llm=llm,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            tools=tools,
        ):
            if ev.event == "content":
                try:
                    data = json.loads(ev.data)
                    delta = data.get("delta", "") or data.get("content", "") or ""
                except Exception:
                    delta = ev.data
                answer_parts.append(delta)
            elif ev.event == "source":
                try:
                    source_data = json.loads(ev.data)
                    sources.append(SourceReference(**source_data))
                except Exception:
                    pass
            elif ev.event == "error":
                try:
                    msg = json.loads(ev.data).get("message", ev.data)
                except Exception:
                    msg = ev.data
                answer_parts.append(f"\n\n[Error: {msg}]")

        return RAGResponse(
            answer="".join(answer_parts),
            sources=sources,
            mode=RAGMode.CONTRADICTION,
            iterations=1,
            web_search_used=False,
        )
