from perspicacite.models.documents import ChunkMetadata


def test_embedding_model_defaults_to_none():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.embedding_model is None


def test_embedding_model_round_trip():
    md = ChunkMetadata(
        paper_id="p", chunk_index=0,
        embedding_model="mistral/codestral-embed",
    )
    assert md.embedding_model == "mistral/codestral-embed"
