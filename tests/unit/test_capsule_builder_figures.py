"""capsule_builder.write_figures persists images + index.json (ASB schema)."""

from __future__ import annotations

import json

from perspicacite.pipeline.capsule_builder import write_figures
from perspicacite.pipeline.parsers.figures import FigureRecord, RawFigure


def _make_raw(num: int = 1) -> RawFigure:
    return RawFigure(
        record=FigureRecord(
            source_pdf="paper.pdf",
            page=num, index=1,
            width_px=400, height_px=300,
            caption=f"Figure {num}. demo.",
            filename=f"fig_p{num:03d}_i01.png", ext="png",
            figure_number=str(num),
            bbox=(10.0, 20.0, 200.0, 100.0),
        ),
        image_bytes=b"\x89PNG\r\n\x1a\ndata-for-fig-%d" % num,
    )


def test_writes_index_and_binaries(tmp_path):
    cap = tmp_path / "cap"
    figs = [_make_raw(1), _make_raw(2)]
    written = write_figures(cap, figures=figs)
    assert (cap / "figures" / "index.json").exists()
    assert (cap / "figures" / "fig_p001_i01.png").read_bytes() == figs[0].image_bytes
    assert (cap / "figures" / "fig_p002_i01.png").read_bytes() == figs[1].image_bytes
    index = json.loads((cap / "figures" / "index.json").read_text())
    assert len(index) == 2
    assert index[0]["figure_number"] == "1"
    # bbox may serialize as list (JSON) — accept either
    assert list(index[0]["bbox"]) == [10.0, 20.0, 200.0, 100.0]
    assert written == 2


def test_handles_no_figures(tmp_path):
    cap = tmp_path / "cap"
    n = write_figures(cap, figures=[])
    assert n == 0
    assert (cap / "figures" / "index.json").exists()
    assert json.loads((cap / "figures" / "index.json").read_text()) == []
