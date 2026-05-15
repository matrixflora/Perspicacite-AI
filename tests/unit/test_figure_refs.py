from __future__ import annotations

from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.figure_refs import collect_figure_refs


def _chunk_with_figs(paper_id: str, fig_ids: list[str]):
    md = ChunkMetadata(
        paper_id=paper_id, chunk_index=0, content_type="pdf",
        figure_refs=fig_ids,
    )
    return DocumentChunk(id=f"{paper_id}_0", text="...", metadata=md)


def test_collects_figure_ids_from_chunks():
    chunks = [_chunk_with_figs("p1", ["pdf_p3_i1", "pdf_p4_i2"]),
              _chunk_with_figs("p1", ["pdf_p3_i1"])]
    refs = collect_figure_refs(chunks, capsule_root=Path("/nonexistent"))
    ids = sorted(r.id for r in refs)
    assert ids == ["pdf_p3_i1", "pdf_p4_i2"]


def test_returns_empty_when_no_figure_refs():
    chunks = [DocumentChunk(
        id="x", text="t",
        metadata=ChunkMetadata(paper_id="p", chunk_index=0),
    )]
    refs = collect_figure_refs(chunks, capsule_root=Path("/nonexistent"))
    assert refs == []


def test_preserves_paper_id_on_each_ref():
    chunks = [_chunk_with_figs("paperA", ["fig1"]),
              _chunk_with_figs("paperB", ["fig2"])]
    refs = collect_figure_refs(chunks, capsule_root=Path("/nonexistent"))
    by_id = {r.id: r.paper_id for r in refs}
    assert by_id == {"fig1": "paperA", "fig2": "paperB"}
