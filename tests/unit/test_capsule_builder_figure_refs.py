"""capsule_builder.resolve_figure_refs maps in-text Fig./Figure N to figure_ids."""

from __future__ import annotations

import pytest

from perspicacite.pipeline.capsule_builder import resolve_figure_refs
from perspicacite.pipeline.parsers.figures import FigureRecord, RawFigure


def _raw(fig_num: str, page: int = 1, idx: int = 1) -> RawFigure:
    return RawFigure(
        record=FigureRecord(
            source_pdf="p.pdf", page=page, index=idx,
            width_px=10, height_px=10, caption=f"Figure {fig_num}. …",
            filename=f"fig_p{page:03d}_i{idx:02d}.png", ext="png",
            figure_number=fig_num,
        ),
        image_bytes=b"",
    )


def test_basic_resolution():
    figs = [_raw("1", 3, 1), _raw("2", 5, 1)]
    refs = resolve_figure_refs("As shown in Fig. 1 and Figure 2, the results …", figs)
    assert set(refs) == {"pdf_p3_i1", "pdf_p5_i1"}


def test_supplementary():
    figs = [_raw("S1", 9, 1)]
    refs = resolve_figure_refs("See Figure S1 in the SI", figs)
    assert refs == ["pdf_p9_i1"]


def test_no_mention():
    figs = [_raw("1")]
    assert resolve_figure_refs("no mentions here", figs) == []


def test_unknown_figure_number_skipped():
    figs = [_raw("1")]
    assert resolve_figure_refs("see Fig. 9 (which we don't have)", figs) == []
