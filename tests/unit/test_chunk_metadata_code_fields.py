from perspicacite.models.documents import ChunkMetadata


def test_code_fields_default_to_none_or_empty():
    md = ChunkMetadata(paper_id="p1", chunk_index=0)
    assert md.symbol_name is None
    assert md.symbol_kind is None
    assert md.start_line is None
    assert md.end_line is None
    assert md.docstring is None
    assert md.imports == []


def test_code_fields_round_trip():
    md = ChunkMetadata(
        paper_id="p1",
        chunk_index=0,
        symbol_name="fit_transform",
        symbol_kind="function",
        start_line=42,
        end_line=87,
        docstring="Fit and transform.",
        imports=["numpy", "scipy"],
    )
    assert md.symbol_name == "fit_transform"
    assert md.imports == ["numpy", "scipy"]
