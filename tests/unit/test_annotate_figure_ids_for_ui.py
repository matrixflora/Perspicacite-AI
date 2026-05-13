from perspicacite.rag.multimodal import annotate_figure_ids_for_ui


def test_rewrites_known_tokens():
    out = annotate_figure_ids_for_ui(
        "See pdf_p1_i0 and pdf_p99_i99.",
        fig_to_paper={"pdf_p1_i0": "doi:10.1/x"},
    )
    assert "[[fig:doi:10.1/x:pdf_p1_i0]]" in out
    assert "pdf_p99_i99" in out  # unknown, unchanged


def test_empty_mapping_keeps_text():
    out = annotate_figure_ids_for_ui("ref pdf_p2_i3", fig_to_paper={})
    assert out == "ref pdf_p2_i3"


def test_none_text_returns_empty():
    assert annotate_figure_ids_for_ui(None, fig_to_paper={"x": "p"}) == ""
