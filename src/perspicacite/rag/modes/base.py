"""Base class for RAG modes."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGRequest, RAGResponse, StreamEvent


class BaseRAGMode(ABC):
    """Base class for RAG modes."""

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    async def execute(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> RAGResponse:
        """Execute RAG query."""
        pass

    @abstractmethod
    async def execute_stream(
        self,
        request: RAGRequest,
        llm: Any,
        vector_store: Any,
        embedding_provider: Any,
        tools: Any,
    ) -> AsyncIterator[StreamEvent]:
        """Execute RAG query with streaming."""
        pass

    def _build_kb_retriever(
        self,
        request: RAGRequest,
        vector_store: Any,
        embedding_provider: Any,
    ) -> Any:
        """Return a DynamicKnowledgeBase for a single KB, or a MultiKBRetriever when
        request.kb_names has >1 entry.

        Both expose .search(query, top_k, min_score) -> list[dict] and tolerate
        .collection_name / ._initialized being assigned on the instance.

        Note: advanced.py / profound.py / agentic / literature_survey are NOT
        wired through this helper — they use WRRF / two-pass / orchestrator
        retrieval paths that need separate multi-KB treatment (DONE_WITH_CONCERNS).
        """
        from perspicacite.models.kb import chroma_collection_name_for_kb

        kb_names = getattr(request, "kb_names", None)
        if kb_names and len(kb_names) > 1:
            from types import SimpleNamespace

            from perspicacite.retrieval.multi_kb import MultiKBRetriever

            metas = [
                SimpleNamespace(
                    name=n,
                    collection_name=chroma_collection_name_for_kb(n),
                    embedding_model=None,
                )
                for n in kb_names
            ]
            return MultiKBRetriever(
                vector_store=vector_store,
                embedding_service=embedding_provider,
                kb_metas=metas,
            )

        from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase

        single_name = (kb_names[0] if kb_names else None) or getattr(request, "kb_name", "default")
        dkb = DynamicKnowledgeBase(
            vector_store=vector_store,
            embedding_service=embedding_provider,
        )
        dkb.collection_name = chroma_collection_name_for_kb(single_name)
        dkb._initialized = True
        return dkb

    def _build_messages(
        self,
        query: str,
        context: str,
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """Build message list for LLM."""
        system = system_prompt or "Answer based on the provided documents."
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Documents:\n{context}\n\nQuestion: {query}"},
        ]
