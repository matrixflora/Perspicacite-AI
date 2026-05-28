"""agentic orchestrator uses MultiKBRetriever when kb_names has >1 entry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator


class _FakeVS:
    async def search(self, *, collection, query_embedding, top_k, **_):
        md = ChunkMetadata(
            paper_id=f"p:{collection}",
            chunk_index=0,
            source=PaperSource.BIBTEX,
            title=collection,
        )
        ch = DocumentChunk(id=collection, text=collection, metadata=md)
        return [SimpleNamespace(chunk=ch, score=0.9)]


class _FakeEmb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]

    async def embed_query(self, texts):
        return await self.embed(texts)


def _make_orch() -> AgenticOrchestrator:
    return AgenticOrchestrator(
        llm_client=None,
        tool_registry=None,
        embedding_provider=_FakeEmb(),
        vector_store=_FakeVS(),
    )


@pytest.mark.asyncio
async def test_agentic_kb_search_step_uses_multi_kb():
    """When orchestrator.kb_names has >1 entry, _build_kb_retriever returns
    MultiKBRetriever and search fans across collections."""
    orch = _make_orch()
    orch.kb_names = ["a", "b"]
    kb = orch._build_kb_retriever(default_kb_name="a")
    assert type(kb).__name__ == "MultiKBRetriever"
    hits = await kb.search("q", top_k=5)
    kbs = {h.get("kb_name") for h in hits}
    # Both KBs should have produced hits.
    assert kbs == {"a", "b"}


@pytest.mark.asyncio
async def test_agentic_kb_search_step_single_kb_uses_dynamic_kb():
    """Single KB → DynamicKnowledgeBase (backward compat)."""
    orch = _make_orch()
    # kb_names empty, default_kb_name supplied → single-KB path
    kb = orch._build_kb_retriever(default_kb_name="solo")
    assert type(kb).__name__ == "DynamicKnowledgeBase"


@pytest.mark.asyncio
async def test_agentic_kb_search_step_single_kb_names_uses_dynamic_kb():
    """kb_names with exactly one entry → DynamicKnowledgeBase (single-KB)."""
    orch = _make_orch()
    orch.kb_names = ["only"]
    kb = orch._build_kb_retriever(default_kb_name=None)
    assert type(kb).__name__ == "DynamicKnowledgeBase"
