"""Audit #7: the abstract is embedded as its own clean chunk (section="abstract"),
separate from the metadata header — so passage similarity hits clean prose."""
import pytest

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig


class _StubVectorStore:
    def __init__(self, sink):
        self._sink = sink

    async def create_collection(self, name, embedding_dim=None):
        pass

    async def add_documents(self, collection, chunks, **kwargs):
        self._sink.extend(chunks)
        return len(chunks)


class _StubEmbeddingService:
    dimension = 768
    model_name = "stub-embed"

    async def embed(self, texts):
        return [[0.0] * 768 for _ in texts]


async def _ingest(paper) -> list:
    captured: list = []
    dkb = DynamicKnowledgeBase(
        vector_store=_StubVectorStore(captured),
        embedding_service=_StubEmbeddingService(),
        config=KnowledgeBaseConfig(),
    )
    dkb.collection_name = "c"
    dkb._initialized = True
    await dkb.add_papers([paper])
    return captured


@pytest.mark.asyncio
async def test_abstract_emitted_as_separate_clean_chunk():
    paper = Paper(
        id="doi:10.1/abs", title="GNN Paper", doi="10.1/abs",
        abstract="Graph neural networks for molecular property prediction.",
        source=PaperSource.OPENALEX, license="cc-by",
    )
    captured = await _ingest(paper)

    meta = next(c for c in captured if c.metadata.section == "metadata")
    abstract_chunks = [c for c in captured if c.metadata.section == "abstract"]

    # the abstract is its own clean-prose chunk, carrying paper metadata
    assert len(abstract_chunks) == 1
    ac = abstract_chunks[0]
    assert ac.text == "Graph neural networks for molecular property prediction."
    assert ac.id.endswith("_abstract")
    assert ac.metadata.license_id == "cc-by"
    assert ac.metadata.abstract == paper.abstract

    # the metadata chunk no longer embeds the abstract prose in its text
    assert "Abstract:" not in meta.text
    assert "Graph neural networks" not in meta.text


@pytest.mark.asyncio
async def test_no_abstract_chunk_when_paper_has_no_abstract():
    paper = Paper(
        id="doi:10.1/noabs", title="No Abstract", doi="10.1/noabs",
        source=PaperSource.OPENALEX,
    )
    captured = await _ingest(paper)
    assert [c for c in captured if c.metadata.section == "abstract"] == []
