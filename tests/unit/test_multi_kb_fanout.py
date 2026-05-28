"""Unit tests for multi-KB fan-out helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.retrieval.multi_kb import (
    get_chunks_by_paper_ids_across,
    query_chunks_across_collections,
)


class _FakeVS:
    def __init__(self, by_collection: dict[str, list[Any]]):
        self.by_collection = by_collection

    async def search(self, *, collection: str, query_embedding, top_k: int):
        return self.by_collection.get(collection, [])

    async def get_chunks_by_paper_ids(self, collection: str, paper_ids: list[str]):
        out = []
        for c in self.by_collection.get(collection, []):
            ch = c.chunk if hasattr(c, "chunk") else c
            if ch.metadata.paper_id in paper_ids:
                out.append(ch)
        return out


class _FakeEmb:
    async def embed(self, texts):
        return [[0.1] * 3 for _ in texts]

    async def embed_query(self, texts):
        return await self.embed(texts)


def _chunk(paper_id: str, text: str, score: float, collection: str):
    md = ChunkMetadata(paper_id=paper_id, chunk_index=0, source=PaperSource.BIBTEX)
    ch = DocumentChunk(id=f"{collection}:{paper_id}", text=text, metadata=md)
    return SimpleNamespace(chunk=ch, score=score)


@pytest.mark.asyncio
async def test_query_chunks_across_collections_merges_and_tags_kb_name():
    vs = _FakeVS({
        "kb_a": [_chunk("p1", "from-a", 0.9, "kb_a"), _chunk("p2", "shared", 0.5, "kb_a")],
        "kb_b": [_chunk("p2", "shared-b", 0.8, "kb_b"), _chunk("p3", "from-b", 0.7, "kb_b")],
    })
    hits = await query_chunks_across_collections(
        vector_store=vs,
        embedding_service=_FakeEmb(),
        collection_names=["kb_a", "kb_b"],
        query="q",
        top_k=10,
    )
    paper_to_kb = {h["paper_id"]: h["kb_name"] for h in hits}
    assert paper_to_kb == {"p1": "kb_a", "p2": "kb_b", "p3": "kb_b"}
    # ordering: best score first
    assert [h["paper_id"] for h in hits[:3]] == ["p1", "p2", "p3"]


@pytest.mark.asyncio
async def test_get_chunks_by_paper_ids_across_fans_out():
    vs = _FakeVS({
        "kb_a": [_chunk("p1", "a1", 0.9, "kb_a")],
        "kb_b": [_chunk("p2", "b1", 0.8, "kb_b")],
    })
    chunks = await get_chunks_by_paper_ids_across(
        vs,
        collection_names=["kb_a", "kb_b"],
        paper_ids=["p1", "p2"],
    )
    ids = sorted(c.id for c in chunks)
    assert ids == ["kb_a:p1", "kb_b:p2"]
