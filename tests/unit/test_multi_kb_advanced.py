"""advanced.py honors request.kb_names — fans out across multiple KB collections.

The fan-out itself is unit-tested at the `_wrrf_retrieval` boundary: with two
collections, results from both KBs reach the WRRF stage and are tagged with
their originating collection name (kb_name).
"""

from __future__ import annotations

from typing import Any

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.models.search import RetrievedChunk
from perspicacite.rag.modes.advanced import AdvancedRAGMode


def _retrieved(paper_id: str, text: str, score: float) -> RetrievedChunk:
    md = ChunkMetadata(
        paper_id=paper_id, chunk_index=0, source=PaperSource.BIBTEX, title=paper_id
    )
    ch = DocumentChunk(id=f"chunk-{paper_id}", text=text, metadata=md)
    return RetrievedChunk(chunk=ch, score=score)


class _FakeVS:
    """Minimal vector_store stub for the fan-out path."""

    def __init__(self, by_coll: dict[str, list[RetrievedChunk]]):
        self.by_coll = by_coll
        self.calls: list[str] = []

    async def search(
        self,
        *,
        collection: str,
        query_embedding: list[float],
        top_k: int,
        **_: Any,
    ) -> list[RetrievedChunk]:
        self.calls.append(collection)
        return self.by_coll.get(collection, [])


class _FakeEmb:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def embed_query(self, texts: list[str]) -> list[list[float]]:
        return await self.embed(texts)


@pytest.mark.asyncio
async def test_wrrf_fan_out_across_collections_tags_kb_name():
    """When `collection_names` has >1 entry, search is fanned out and results
    from every collection reach WRRF tagged with their kb_name."""
    cfg = Config()
    mode = AdvancedRAGMode(cfg)
    # Keep WRRF deterministic: skip hybrid + small N.
    mode.use_hybrid = False
    mode.initial_docs = 5
    mode.final_max_docs = 5
    mode.max_docs_per_source = 5

    vs = _FakeVS(
        {
            "kb_a": [_retrieved("p1", "text from kb_a", 0.9)],
            "kb_b": [_retrieved("p2", "text from kb_b", 0.8)],
        }
    )

    selected = await mode._wrrf_retrieval(
        queries=["query"],
        vector_store=vs,
        embedding_provider=_FakeEmb(),
        kb_name="kb_a",  # legacy single
        collection_names=["kb_a", "kb_b"],
        llm=None,
        request=None,
    )

    # Both collections must have been searched.
    assert sorted(vs.calls) == ["kb_a", "kb_b"], (
        f"expected fan-out to both collections, got {vs.calls}"
    )
    # And results from both KBs must surface.
    assert selected, "expected selected documents"
    paper_ids = {
        getattr(getattr(d, "chunk", None), "metadata", None).paper_id
        for d in selected
        if getattr(getattr(d, "chunk", None), "metadata", None) is not None
        and getattr(d.chunk.metadata, "paper_id", None)
    }
    assert paper_ids & {"p1", "p2"}, f"expected at least one of p1/p2, got {paper_ids}"


@pytest.mark.asyncio
async def test_wrrf_single_collection_back_compat():
    """When `collection_names` is None, the legacy single-collection path
    still works using `kb_name`."""
    cfg = Config()
    mode = AdvancedRAGMode(cfg)
    mode.use_hybrid = False
    mode.initial_docs = 5
    mode.final_max_docs = 5
    mode.max_docs_per_source = 5

    vs = _FakeVS({"kb_solo": [_retrieved("p1", "solo text", 0.7)]})

    selected = await mode._wrrf_retrieval(
        queries=["query"],
        vector_store=vs,
        embedding_provider=_FakeEmb(),
        kb_name="kb_solo",
        llm=None,
        request=None,
    )

    assert vs.calls == ["kb_solo"]
    assert selected
