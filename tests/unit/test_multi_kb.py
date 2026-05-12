import pytest

from perspicacite.retrieval.multi_kb import MultiKBRetriever, check_embedding_compat


class _KBMeta:
    def __init__(self, name, model="m1"):
        self.name = name
        self.collection_name = f"kb_{name}"
        self.embedding_model = model


def test_check_embedding_compat_ok():
    assert check_embedding_compat([_KBMeta("a"), _KBMeta("b")]) is None
    assert check_embedding_compat([_KBMeta("a")]) is None
    assert check_embedding_compat([]) is None


def test_check_embedding_compat_mismatch():
    msg = check_embedding_compat([_KBMeta("a", "m1"), _KBMeta("b", "m2")])
    assert msg and "m1" in msg and "m2" in msg


# --- fakes for MultiKBRetriever.search ---


class _ChunkMeta:
    def __init__(self, pid, year=2020):
        self.paper_id = pid
        self.title = f"Title {pid}"
        self.doi = f"10.1/{pid}"
        self.year = year


class _DocChunk:
    def __init__(self, pid):
        self.id = f"chunk_{pid}"
        self.text = f"text for {pid}"
        self.metadata = _ChunkMeta(pid)


class _RetrievedChunk:
    def __init__(self, pid, score):
        self.chunk = _DocChunk(pid)
        self.score = score
        self.retrieval_method = "vector"


class _FakeVectorStore:
    def __init__(self, by_collection):
        # by_collection: {collection_name: [(paper_id, score), ...]}
        self.by_collection = by_collection

    async def search(self, collection, query_embedding, top_k=10, filters=None):
        return [
            _RetrievedChunk(pid, score) for pid, score in self.by_collection.get(collection, [])
        ]


class _FakeEmbProvider:
    dimension = 3

    async def embed(self, texts):
        return [[0.0, 0.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_multi_kb_search_merges_and_dedups():
    vs = _FakeVectorStore(
        {
            "kb_a": [("p1", 0.9), ("p2", 0.5)],
            "kb_b": [("p1", 0.7), ("p3", 0.6)],
        }
    )
    r = MultiKBRetriever(
        vector_store=vs,
        embedding_service=_FakeEmbProvider(),
        kb_metas=[_KBMeta("a"), _KBMeta("b")],
    )
    out = await r.search("a query", top_k=10)
    # list[dict] like DynamicKnowledgeBase.search, plus a "kb_name" key
    pids = [d["paper_id"] for d in out]
    assert pids.count("p1") == 1  # deduped
    assert set(pids) == {"p1", "p2", "p3"}
    assert out[0]["paper_id"] == "p1"  # highest score (0.9 from kb_a) ranks first
    # the deduped p1 keeps the higher score / its source kb
    p1 = next(d for d in out if d["paper_id"] == "p1")
    assert p1["score"] == 0.9 and p1["kb_name"] == "a"
    assert all("kb_name" in d for d in out)
    assert all("text" in d and "metadata" in d for d in out)


@pytest.mark.asyncio
async def test_multi_kb_search_top_k_limit():
    vs = _FakeVectorStore({"kb_a": [(f"p{i}", 1.0 - i * 0.05) for i in range(20)]})
    r = MultiKBRetriever(
        vector_store=vs, embedding_service=_FakeEmbProvider(), kb_metas=[_KBMeta("a")]
    )
    out = await r.search("q", top_k=5)
    assert len(out) == 5
    assert [d["paper_id"] for d in out] == ["p0", "p1", "p2", "p3", "p4"]


@pytest.mark.asyncio
async def test_multi_kb_search_min_score_filter():
    vs = _FakeVectorStore({"kb_a": [("p1", 0.9), ("p2", 0.1)]})
    r = MultiKBRetriever(
        vector_store=vs, embedding_service=_FakeEmbProvider(), kb_metas=[_KBMeta("a")]
    )
    out = await r.search("q", top_k=10, min_score=0.5)
    assert [d["paper_id"] for d in out] == ["p1"]


@pytest.mark.asyncio
async def test_multi_kb_search_two_pass_delegates():
    vs = _FakeVectorStore({"kb_a": [("p1", 0.9)]})
    r = MultiKBRetriever(
        vector_store=vs, embedding_service=_FakeEmbProvider(), kb_metas=[_KBMeta("a")]
    )
    # search_two_pass should exist and (for v1) just delegate to search
    out = await r.search_two_pass("q", top_k=5)
    assert isinstance(out, list)
