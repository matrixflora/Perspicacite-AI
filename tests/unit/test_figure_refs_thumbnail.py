from __future__ import annotations

import base64
from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.figure_refs import collect_figure_refs


def _png_bytes() -> bytes:
    # Minimal valid 1x1 PNG.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6300010000000500010d0a2db4000000004945"
        "4e44ae426082"
    )


def _chunk_with_fig(paper_id: str, fig_id: str) -> DocumentChunk:
    md = ChunkMetadata(
        paper_id=paper_id,
        chunk_index=0,
        content_type="text",
        figure_refs=[fig_id],
    )
    return DocumentChunk(id=f"{paper_id}_0", text="...", metadata=md)


def test_collect_figure_refs_loads_thumbnail_when_present(tmp_path: Path):
    paper_id = "p1"
    fig_id = "f1"
    capsule_root = tmp_path / "capsule"
    fig_dir = capsule_root / paper_id / "figures"
    fig_dir.mkdir(parents=True)
    (fig_dir / f"{fig_id}.png").write_bytes(_png_bytes())

    refs = collect_figure_refs(
        [_chunk_with_fig(paper_id, fig_id)], capsule_root=capsule_root,
    )
    assert len(refs) == 1
    assert refs[0].thumbnail_b64 is not None
    decoded = base64.b64decode(refs[0].thumbnail_b64)
    assert decoded == _png_bytes()


def test_collect_figure_refs_thumbnail_none_when_missing(tmp_path: Path):
    refs = collect_figure_refs(
        [_chunk_with_fig("p1", "f-missing")], capsule_root=tmp_path / "capsule",
    )
    assert len(refs) == 1
    assert refs[0].thumbnail_b64 is None
