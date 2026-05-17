"""When chunks are embedded during ingest, each chunk's metadata
``embedding_model`` is updated to the actual model used (sub-project B)."""
from __future__ import annotations

from perspicacite.llm.embeddings import TypedEmbeddingProvider
from perspicacite.models.documents import ChunkMetadata, DocumentChunk


class _Stub:
    def __init__(self, name: str, dim: int = 4):
        self._name = name
        self._dim = dim

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]


def _chunk(ctype: str, paper_id: str = "p", idx: int = 0) -> DocumentChunk:
    return DocumentChunk(
        id=f"{paper_id}_{idx}",
        text=f"sample {ctype} text",
        metadata=ChunkMetadata(paper_id=paper_id, chunk_index=idx, content_type=ctype),
    )


def test_stamps_routed_model_per_chunk():
    from perspicacite.rag.dynamic_kb import stamp_embedding_models_on_chunks

    typed = TypedEmbeddingProvider(
        default=_Stub("text-embedding-3-small"),
        by_content_type={"code": _Stub("mistral/codestral-embed")},
    )
    chunks = [_chunk("text", "p1", 0), _chunk("code", "p1", 1)]
    out = stamp_embedding_models_on_chunks(chunks, embedder=typed)
    assert out[0].metadata.embedding_model == "text-embedding-3-small"
    assert out[1].metadata.embedding_model == "mistral/codestral-embed"


def test_stamps_single_model_when_provider_is_not_typed():
    from perspicacite.rag.dynamic_kb import stamp_embedding_models_on_chunks

    single = _Stub("text-embedding-3-small")
    chunks = [_chunk("text", "p1", 0), _chunk("code", "p1", 1)]
    out = stamp_embedding_models_on_chunks(chunks, embedder=single)
    assert all(c.metadata.embedding_model == "text-embedding-3-small" for c in out)


def test_unknown_content_type_falls_to_default_model_name():
    from perspicacite.rag.dynamic_kb import stamp_embedding_models_on_chunks

    typed = TypedEmbeddingProvider(
        default=_Stub("text-embedding-3-small"),
        by_content_type={"code": _Stub("mistral/codestral-embed")},
    )
    chunks = [_chunk("pdf", "p1", 0)]
    out = stamp_embedding_models_on_chunks(chunks, embedder=typed)
    assert out[0].metadata.embedding_model == "text-embedding-3-small"


def test_original_chunks_not_mutated():
    """ChunkMetadata is frozen — the helper must return new chunks, not
    mutate the inputs."""
    from perspicacite.rag.dynamic_kb import stamp_embedding_models_on_chunks

    single = _Stub("text-embedding-3-small")
    chunks = [_chunk("text", "p1", 0)]
    original_md = chunks[0].metadata
    out = stamp_embedding_models_on_chunks(chunks, embedder=single)
    assert original_md.embedding_model is None  # input untouched
    assert out[0].metadata.embedding_model == "text-embedding-3-small"
