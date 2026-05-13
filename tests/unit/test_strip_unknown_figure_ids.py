from perspicacite.rag.multimodal import strip_unknown_figure_ids


def test_keeps_known_strips_unknown():
    text = "See pdf_p1_i0 and pdf_p99_i99 in context."
    out = strip_unknown_figure_ids(text, known={"pdf_p1_i0"})
    assert "pdf_p1_i0" in out
    assert "pdf_p99_i99" not in out


def test_no_known_strips_all_pdf_ids():
    text = "ref pdf_p2_i3"
    out = strip_unknown_figure_ids(text, known=set())
    assert "pdf_p2_i3" not in out


def test_preserves_surrounding_text():
    text = "Important finding (pdf_p1_i0): the result holds."
    out = strip_unknown_figure_ids(text, known={"pdf_p1_i0"})
    assert "Important finding" in out and "result holds" in out


def test_empty_text_returns_empty():
    assert strip_unknown_figure_ids("", known=set()) == ""


def test_handles_none_safely():
    # Mirrors the ``text or ""`` defensive pattern in the impl.
    assert strip_unknown_figure_ids(None, known=set()) == ""  # type: ignore[arg-type]
