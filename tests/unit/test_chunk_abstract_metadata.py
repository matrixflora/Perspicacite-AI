"""abstract round-trips through ChunkMetadata <-> Chroma metadata, and is
surfaced by list_paper_metadata."""

import pytest

from perspicacite.models.documents import ChunkMetadata
from perspicacite.retrieval.chroma_store import (
    ChromaVectorStore,
    _chunk_to_metadata,
    _metadata_to_chunk,
)


def test_abstract_round_trips_through_chroma_metadata():
    cm = ChunkMetadata(paper_id="10.1/x", chunk_index=0, abstract="A short abstract.")
    flat = _chunk_to_metadata(cm)
    assert flat["abstract"] == "A short abstract."
    back = _metadata_to_chunk(flat)
    assert back.abstract == "A short abstract."


def test_abstract_absent_is_omitted_not_none():
    cm = ChunkMetadata(paper_id="10.1/x", chunk_index=0)  # no abstract
    flat = _chunk_to_metadata(cm)
    assert "abstract" not in flat  # Chroma rejects None; absent is correct


class _StubColl:
    def get(self, **kwargs):
        return {
            "metadatas": [
                {"paper_id": "p1", "title": "T1", "doi": "10.1/p1", "abstract": "abs one"},
                {"paper_id": "p1", "chunk_index": 1},  # later chunk, no abstract
                {"paper_id": "p2", "title": "T2", "doi": "10.1/p2"},  # no abstract
            ]
        }


class _StubClient:
    def get_collection(self, name):
        return _StubColl()


@pytest.mark.asyncio
async def test_list_paper_metadata_surfaces_abstract():
    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = _StubClient()
    rows = await store.list_paper_metadata("kb")
    by_pid = {r["paper_id"]: r for r in rows}
    assert by_pid["p1"]["abstract"] == "abs one"
    assert by_pid["p2"].get("abstract") is None
