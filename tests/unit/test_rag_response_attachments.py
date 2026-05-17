from perspicacite.models.rag import (
    CodeExcerpt,
    FigureRef,
    RAGMode,
    RAGResponse,
)


def test_rag_response_defaults_empty_lists():
    resp = RAGResponse(answer="hi", mode=RAGMode.BASIC)
    assert resp.figures == []
    assert resp.code_excerpts == []


def test_rag_response_round_trip_with_attachments():
    fig = FigureRef(
        id="pdf_p3_i1", paper_id="p", label="Figure 3",
        caption="Test caption", source_url="https://doi.org/10.0/x",
    )
    code = CodeExcerpt(
        id="github:owner/repo@abc:f.py#L1-L10",
        paper_id="github:owner/repo@abc:f.py",
        file_path="f.py",
        symbol_name="fit",
        symbol_kind="function",
        language="python",
        start_line=1,
        end_line=10,
        text="def fit(): pass",
        source_url="https://github.com/owner/repo/blob/abc/f.py#L1-L10",
    )
    resp = RAGResponse(
        answer="hi", mode=RAGMode.BASIC,
        figures=[fig], code_excerpts=[code],
    )
    assert resp.figures[0].id == "pdf_p3_i1"
    assert resp.code_excerpts[0].symbol_name == "fit"
    assert resp.code_excerpts[0].source_url.endswith("#L1-L10")


def test_code_excerpt_required_fields():
    code = CodeExcerpt(
        id="x", paper_id="p", file_path="f.py", symbol_kind="module",
        language="python", start_line=1, end_line=2,
        text="x = 1", source_url="https://example.com/f.py",
    )
    assert code.symbol_name is None
