"""build_capsule orchestrates parse + extract + write + chunk + embed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.capsule_builder import build_capsule
from perspicacite.pipeline.parsers.figures import FigureRecord, RawFigure


def _state(tmp_root: Path):
    return SimpleNamespace(
        config=SimpleNamespace(
            capsule=SimpleNamespace(
                enabled=True, auto_build_on_ingest=True,
                root=tmp_root, min_version="0.1",
            ),
            knowledge_base=SimpleNamespace(
                chunk_size=1000, chunk_overlap=200,
                markdown_heading_aware=True, code_language_aware=True,
            ),
        ),
        embedding_provider=SimpleNamespace(embed=AsyncMock(return_value=[[0.1]*3])),
        vector_store=SimpleNamespace(add_documents=AsyncMock()),
        pdf_parser=SimpleNamespace(parse=AsyncMock()),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="kb_test", paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_builds_capsule_for_paper_with_pdf(tmp_path, monkeypatch):
    paper = Paper(id="doi:10.1234/abc", title="t", source=PaperSource.USER_UPLOAD, doi="10.1234/abc")
    state = _state(tmp_path / "caps")

    parsed = SimpleNamespace(
        text="## Methods\nWe did Y.\n\n## Results\nSee Fig. 1.\n",
        title="t", sections={}, metadata={},
    )
    state.pdf_parser.parse = AsyncMock(return_value=parsed)

    # Make embedding return matching shape per chunk
    state.embedding_provider.embed = AsyncMock(side_effect=lambda texts: [[0.1] * 3 for _ in texts])

    fake_fig = RawFigure(
        record=FigureRecord(
            source_pdf="paper.pdf", page=3, index=1,
            width_px=400, height_px=300, caption="Figure 1.",
            filename="fig_p003_i01.png", ext="png", figure_number="1",
        ),
        image_bytes=b"PNGBYTES",
    )
    monkeypatch.setattr(
        "perspicacite.pipeline.capsule_builder.extract_figures",
        lambda pdf_path, min_px=100: [fake_fig],
    )

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    result = await build_capsule(
        paper=paper, pdf_path=pdf_path, kb_name="kb_test", app_state=state,
    )

    cap = tmp_path / "caps" / "doi_10.1234__abc"
    assert (cap / "metadata.json").exists()
    assert (cap / "figures" / "index.json").exists()
    assert (cap / "figures" / "fig_p003_i01.png").read_bytes() == b"PNGBYTES"
    blocks = (cap / "text" / "blocks.jsonl").read_text().splitlines()
    assert any('"section": "results"' in line for line in blocks)
    assert any("pdf_p3_i1" in line for line in blocks)
    state.vector_store.add_documents.assert_called_once()
    assert result["status"] == "built"
    assert result["figures"] == 1


@pytest.mark.asyncio
async def test_idempotent_when_capsule_exists(tmp_path):
    paper = Paper(id="doi:10.1234/abc", title="t", source=PaperSource.USER_UPLOAD)
    state = _state(tmp_path / "caps")
    cap = (tmp_path / "caps") / "doi_10.1234__abc"
    cap.mkdir(parents=True)
    (cap / "metadata.json").write_text(
        json.dumps({"capsule_version": "0.1", "producer": "perspicacite"}),
    )
    result = await build_capsule(
        paper=paper, pdf_path=None, kb_name="kb_test", app_state=state,
    )
    assert result["status"] == "skipped"
    state.vector_store.add_documents.assert_not_called()


@pytest.mark.asyncio
async def test_builds_without_pdf(tmp_path):
    """Paper with no PDF — capsule still gets metadata + empty figures/."""
    paper = Paper(id="local:abc", title="t", source=PaperSource.LOCAL)
    state = _state(tmp_path / "caps")
    result = await build_capsule(
        paper=paper, pdf_path=None, kb_name="kb_test", app_state=state,
    )
    cap = (tmp_path / "caps") / "local_abc"
    assert (cap / "metadata.json").exists()
    assert json.loads((cap / "figures" / "index.json").read_text()) == []
    assert result["figures"] == 0
