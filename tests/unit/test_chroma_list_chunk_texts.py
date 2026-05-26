"""ChromaVectorStore.list_chunk_texts — capped chunk-document reader."""

import pytest

from perspicacite.retrieval.chroma_store import ChromaVectorStore


class _StubColl:
    def __init__(self, docs):
        self._docs = docs
        self.kwargs = None

    def get(self, **kwargs):
        self.kwargs = kwargs
        return {"documents": self._docs}


class _StubClient:
    def __init__(self, coll):
        self._coll = coll

    def get_collection(self, name):
        return self._coll


@pytest.mark.asyncio
async def test_list_chunk_texts_returns_nonempty_docs():
    coll = _StubColl(["chunk one", "chunk two", "", None])
    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = _StubClient(coll)
    out = await store.list_chunk_texts("kb_x", limit=50)
    assert out == ["chunk one", "chunk two"]
    assert coll.kwargs["limit"] == 50
    assert coll.kwargs["include"] == ["documents"]


@pytest.mark.asyncio
async def test_list_chunk_texts_missing_collection_returns_empty():
    class _Boom:
        def get_collection(self, name):
            raise RuntimeError("no such collection")

    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = _Boom()
    assert await store.list_chunk_texts("missing") == []


@pytest.mark.asyncio
async def test_list_paper_chunks_groups_and_caps(tmp_path):
    import chromadb

    store = ChromaVectorStore.__new__(ChromaVectorStore)
    store.client = chromadb.PersistentClient(path=str(tmp_path / "cdb"))
    coll = "kb_lpc"
    store.client.get_or_create_collection(name=coll)
    c = store.client.get_collection(name=coll)
    c.add(
        ids=["p1_0", "p1_1", "p1_2", "p2_0"],
        embeddings=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6], [0.7, 0.8]],
        documents=["a one", "a two", "a three", "b one"],
        metadatas=[{"paper_id": "p1"}, {"paper_id": "p1"}, {"paper_id": "p1"}, {"paper_id": "p2"}],
    )
    out = await store.list_paper_chunks(coll, max_per_paper=2)
    assert set(out) == {"p1", "p2"}
    assert out["p1"] == ["a one", "a two"]   # capped at 2 chunks
    assert out["p2"] == ["b one"]
