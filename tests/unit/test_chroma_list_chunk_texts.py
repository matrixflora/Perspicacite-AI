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
