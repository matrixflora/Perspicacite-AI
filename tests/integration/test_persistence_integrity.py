"""Persistence + data-integrity tests (Wave 6.2).

These exercise the on-disk contracts of the framework:

  - Chroma collections survive a fresh PersistentClient over the same dir.
  - DynamicKnowledgeBase re-opens and queries still work.
  - KBLogWriter serialises concurrent async appends without torn lines.
  - SessionStore handles concurrent writes (SQLite WAL) and survives
    close/reopen.
  - CheckpointStore's atomic save (tmp + rename) means a partial .tmp
    can't corrupt the live file.
  - LLMResponseCache + EmbeddingCache survive close/reopen.
  - CachedEmbeddingProvider de-dupes across reopen — the inner provider
    is consulted only once even when the cache file is reopened.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

chromadb = pytest.importorskip("chromadb")
np = pytest.importorskip("numpy")

from perspicacite.models.papers import Author, Paper, PaperSource

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Local copies of e2e helpers (kept in-file so this module is independent of
# the e2e conftest — pytest only auto-loads conftest.py from ancestor dirs).
# ---------------------------------------------------------------------------

def _deterministic_vec(text: str, dim: int = 384) -> list[float]:
    h = hashlib.sha256(text.encode("utf-8")).digest()
    floats: list[float] = []
    while len(floats) < dim:
        for b in h:
            floats.append((b / 127.5) - 1.0)
            if len(floats) >= dim:
                break
        h = hashlib.sha256(h).digest()
    arr = np.asarray(floats, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


class _DetEmbedder:
    def __init__(self, dim: int = 384) -> None:
        self._dim = dim
        self.calls = 0

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "deterministic-mock"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [_deterministic_vec(t, self._dim) for t in texts]


def _make_paper(doi: str, title: str, abstract: str) -> Paper:
    return Paper(
        id=f"doi:{doi}",
        doi=doi,
        title=title,
        authors=[Author(name="Mock Author", family="Author")],
        year=2024,
        abstract=abstract,
        full_text=(title + ". " + abstract + " ") * 30,
        source=PaperSource.WEB_SEARCH,
    )


@pytest.fixture
def det_embedder() -> _DetEmbedder:
    return _DetEmbedder()


@pytest.fixture
def one_paper() -> Paper:
    return _make_paper(
        "10.1234/persist",
        "Persistence test paper",
        "We test that the knowledge base survives a close-and-reopen cycle.",
    )


@pytest.fixture
def five_papers() -> list[Paper]:
    return [
        _make_paper(f"10.1234/p{i}", f"Title {i}", f"Abstract {i} stellar protein.")
        for i in range(5)
    ]


# ---------------------------------------------------------------------------
# KB survives close + reopen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_kb_survives_close_reopen(
    tmp_path: Path, det_embedder: _DetEmbedder, one_paper: Paper,
) -> None:
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval.chroma_store import ChromaVectorStore

    persist_dir = str(tmp_path / "chroma")
    cfg = KnowledgeBaseConfig(
        vector_size=det_embedder.dimension, chunk_size=500, chunk_overlap=50,
        # Persistent name (don't randomize) so we can re-open it.
        collection_prefix="persistent_",
        top_k=5,
    )

    # Phase 1: build, ingest, drop.
    vs = ChromaVectorStore(persist_dir=persist_dir, embedding_provider=det_embedder)
    kb1 = DynamicKnowledgeBase(
        vector_store=vs, embedding_service=det_embedder, config=cfg,
    )
    await kb1.initialize()
    collection_name = kb1.collection_name
    added = await kb1.add_papers([one_paper])
    assert added >= 1
    del kb1, vs

    # Phase 2: fresh client, same persist_dir; search by reattaching to the
    # known collection_name.
    vs2 = ChromaVectorStore(persist_dir=persist_dir, embedding_provider=det_embedder)
    # ChromaVectorStore.search reattaches via client.get_collection.
    query_vec = (await det_embedder.embed(["persistence test query"]))[0]
    hits = await vs2.search(
        collection=collection_name, query_embedding=query_vec, top_k=5,
    )
    assert hits, "expected to retrieve chunks after reopen"
    paper_ids = {h.chunk.metadata.paper_id for h in hits}
    assert one_paper.id in paper_ids


# ---------------------------------------------------------------------------
# Chroma collection persists across PersistentClient instances
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chroma_collection_persists(
    tmp_path: Path, det_embedder: _DetEmbedder, five_papers: list[Paper],
) -> None:
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval.chroma_store import ChromaVectorStore

    persist_dir = str(tmp_path / "chroma")
    cfg = KnowledgeBaseConfig(
        vector_size=det_embedder.dimension, chunk_size=500, chunk_overlap=50,
        collection_prefix="persisted_",
    )

    vs = ChromaVectorStore(persist_dir=persist_dir, embedding_provider=det_embedder)
    kb = DynamicKnowledgeBase(
        vector_store=vs, embedding_service=det_embedder, config=cfg,
    )
    await kb.initialize()
    coll_name = kb.collection_name
    await kb.add_papers(five_papers)
    stats_before = await vs.get_collection_stats(coll_name)
    count_before = stats_before["count"]
    assert count_before > 0
    del kb, vs

    # Fresh client at the same dir — Chroma's WAL should have persisted.
    vs2 = ChromaVectorStore(persist_dir=persist_dir, embedding_provider=det_embedder)
    stats_after = await vs2.get_collection_stats(coll_name)
    assert stats_after["count"] == count_before


# ---------------------------------------------------------------------------
# Concurrent KB log appends — Wave 4.3 atomicity invariant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_kb_log_appends(tmp_path: Path) -> None:
    from perspicacite.pipeline.kb_log import KBEvent, KBLogWriter

    path = tmp_path / "log.jsonl"

    async def burst(tag: str, n: int) -> None:
        # Each task uses its own writer pointing at the same path —
        # mirrors how independent ingest workers behave.
        w = KBLogWriter(path=path)
        for i in range(n):
            w.append(KBEvent(
                event="paper_added", kb_name="concurrent",
                paper_id=f"{tag}-{i:03d}",
                title=f"paper {tag}-{i:03d}",
                chunks=1,
                source_command="concurrency_test",
            ))
            # Yield control so tasks interleave.
            if i % 10 == 0:
                await asyncio.sleep(0)

    await asyncio.gather(
        burst("a", 100), burst("b", 100), burst("c", 100), burst("d", 100),
    )

    events = KBLogWriter(path=path).read_all()
    assert len(events) == 400, f"expected 400 events; got {len(events)}"
    pids = {e.paper_id for e in events}
    assert len(pids) == 400, "every paper_id should be unique (no torn lines)"

    # And every raw line must still be valid JSON (no interleaved
    # partial writes).
    raw_lines = path.read_text().splitlines()
    assert len(raw_lines) == 400
    for line in raw_lines:
        # Will raise if any line is corrupt.
        json.loads(line)


# ---------------------------------------------------------------------------
# Concurrent SessionStore writes — SQLite WAL holds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_session_store_writes(tmp_path: Path) -> None:
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.models.kb import ChunkConfig, KnowledgeBase

    store = SessionStore(tmp_path / "s.db")
    await store.init_db()

    async def writer(tag: str, n: int) -> None:
        for i in range(n):
            kb = KnowledgeBase(
                name=f"{tag}-{i:03d}",
                description=f"writer {tag} row {i}",
                collection_name=f"coll_{tag}_{i}",
                embedding_model="mock",
                chunk_config=ChunkConfig(),
            )
            await store.save_kb_metadata(kb)
            if i % 5 == 0:
                await asyncio.sleep(0)

    await asyncio.gather(
        writer("a", 50), writer("b", 50), writer("c", 50), writer("d", 50),
    )

    rows = await store.list_kbs()
    assert len(rows) == 200, f"expected 200 KB rows; got {len(rows)}"


# ---------------------------------------------------------------------------
# SessionStore reopens cleanly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_store_reopen_preserves_rows(tmp_path: Path) -> None:
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.models.kb import ChunkConfig, KnowledgeBase

    db_path = tmp_path / "s.db"
    s1 = SessionStore(db_path)
    await s1.init_db()
    for i in range(5):
        await s1.save_kb_metadata(KnowledgeBase(
            name=f"kb_{i}",
            description=f"row {i}",
            collection_name=f"coll_{i}",
            embedding_model="mock",
            chunk_config=ChunkConfig(),
        ))
    rows_before = await s1.list_kbs()
    assert len(rows_before) == 5
    del s1

    s2 = SessionStore(db_path)
    await s2.init_db()  # idempotent — schema CREATE IF NOT EXISTS
    rows_after = await s2.list_kbs()
    assert len(rows_after) == 5
    names_after = {r.name for r in rows_after}
    assert names_after == {f"kb_{i}" for i in range(5)}


# ---------------------------------------------------------------------------
# Checkpoint atomic-save invariant
# ---------------------------------------------------------------------------

def test_checkpoint_survives_kill_mid_save(tmp_path: Path) -> None:
    from perspicacite.pipeline.checkpoint import CheckpointState, CheckpointStore

    ckpt_path = tmp_path / "ckpt.json"
    store = CheckpointStore(path=ckpt_path, kb_name="kb1", operation="ingest")
    state = CheckpointState(
        kb_name="kb1", operation="ingest",
        planned_ids=["a", "b", "c"],
    )
    state.record("a", "ok")
    store.save(state)

    # Simulate a SIGKILL mid-save: a partial tmp file is left around.
    (tmp_path / "ckpt.json.tmp").write_text('{"kb_name": "kb1", "operation"')

    # Re-open. The atomic save means the live file is intact.
    reopened = CheckpointStore(path=ckpt_path, kb_name="kb1", operation="ingest")
    loaded = reopened.load()
    assert loaded is not None
    assert loaded.kb_name == "kb1"
    assert loaded.operation == "ingest"
    assert loaded.planned_ids == ["a", "b", "c"]
    assert loaded.processed == {"a": "ok"}
    # Remaining work picks up where we left off.
    assert list(loaded.remaining_ids()) == ["b", "c"]


# ---------------------------------------------------------------------------
# LLM cache survives close + reopen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_cache_survives_reopen(tmp_path: Path) -> None:
    from perspicacite.llm.cache import LLMResponseCache, build_cache_key

    db = tmp_path / "llm_cache.db"
    c1 = LLMResponseCache(path=db, ttl_hours=24)
    key = build_cache_key(
        provider="anthropic",
        model="claude-test",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.0,
        max_tokens=100,
        extra_kwargs={},
    )
    await c1.put(
        key=key, provider="anthropic", model="claude-test",
        response="cached-output", latency_ms=12.3,
        input_tokens=4, output_tokens=2,
    )
    del c1

    c2 = LLMResponseCache(path=db, ttl_hours=24)
    got = await c2.get(key)
    assert got is not None
    assert got.response == "cached-output"
    assert got.provider == "anthropic"
    assert got.model == "claude-test"


# ---------------------------------------------------------------------------
# Embedding-cache dedup across reopens — Wave 2.2 invariant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embedding_cache_dedup_across_reopens(tmp_path: Path) -> None:
    from perspicacite.llm.embedding_cache import EmbeddingCache
    from perspicacite.llm.embeddings import CachedEmbeddingProvider

    class CountingInner:
        calls = 0  # class-level so survives wrap/unwrap

        @property
        def dimension(self) -> int:
            return 4

        @property
        def model_name(self) -> str:
            return "fake-counting"

        async def embed(self, texts: list[str]) -> list[list[float]]:
            CountingInner.calls += 1
            return [[1.0, 2.0, 3.0, 4.0] for _ in texts]

    CountingInner.calls = 0
    db = tmp_path / "emb.db"

    # First pass: cold cache → 1 inner call.
    cache_a = EmbeddingCache(db)
    inner_a = CountingInner()
    wrapper_a = CachedEmbeddingProvider(inner=inner_a, cache=cache_a)
    out1 = await wrapper_a.embed(["hello world"])
    assert CountingInner.calls == 1
    assert out1 == [[1.0, 2.0, 3.0, 4.0]]

    # Drop the wrapper + cache.
    del wrapper_a, cache_a, inner_a

    # Second pass: fresh cache, fresh wrapper, fresh inner — but the
    # cache file is unchanged so the embedding is reused and the inner
    # is NOT called.
    cache_b = EmbeddingCache(db)
    inner_b = CountingInner()
    wrapper_b = CachedEmbeddingProvider(inner=inner_b, cache=cache_b)
    out2 = await wrapper_b.embed(["hello world"])
    assert CountingInner.calls == 1, (
        f"inner should not have been called again; got {CountingInner.calls}"
    )
    assert out2 == out1
