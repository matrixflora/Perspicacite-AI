"""ASB-aligned figure extraction — vendored from AgenticScienceBuilder."""

from __future__ import annotations


def test_parse_figure_number_basic():
    from perspicacite.pipeline.parsers.figures import parse_figure_number
    assert parse_figure_number("Figure 3. A schematic …") == "3"
    assert parse_figure_number("Fig. S2: supplementary") == "S2"
    assert parse_figure_number("Scheme 1 — synthesis") == "1"
    assert parse_figure_number("not a figure caption") is None
    assert parse_figure_number("") is None


def test_parse_panel_labels_dedup_order():
    from perspicacite.pipeline.parsers.figures import parse_panel_labels
    out = parse_panel_labels("Figure 2. (A) overview (B) detail (A) repeat")
    assert out == ["A", "B"]


def test_figure_record_filename_convention():
    from perspicacite.pipeline.parsers.figures import FigureRecord
    rec = FigureRecord(
        source_pdf="paper.pdf", page=3, index=2,
        width_px=800, height_px=600, caption="Fig 1 …",
        filename="fig_p003_i02.png", ext="png",
    )
    assert rec.filename == "fig_p003_i02.png"
    assert rec.panel_files == []
