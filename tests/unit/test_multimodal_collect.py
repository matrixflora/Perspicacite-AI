import base64
import json
from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.multimodal import collect_figures_for_chunks


def _chunk(paper_id: str, figure_refs: list[str], parent: str | None = None) -> DocumentChunk:
    md = ChunkMetadata(
        paper_id=paper_id,
        chunk_index=0,
        figure_refs=figure_refs,
        parent_paper_id=parent,
    )
    return DocumentChunk(id=f"c_{paper_id}_{','.join(figure_refs)}", text="t", metadata=md)


def _write_capsule(root: Path, paper_id: str, figures: list[dict], image_bytes: bytes) -> None:
    safe = paper_id.replace(":", "_").replace("/", "__")
    cap = root / safe
    (cap / "figures").mkdir(parents=True, exist_ok=True)
    (cap / "figures" / "index.json").write_text(json.dumps(figures))
    for f in figures:
        (cap / "figures" / f["filename"]).write_bytes(image_bytes)


def test_collect_loads_image_b64_for_matching_refs(tmp_path):
    img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p003_i02.png",
                "page": 3,
                "index": 2,
                "figure_number": "1",
                "caption": "Schematic of method.",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunks = [_chunk("doi:10.1/x", ["pdf_p3_i2"])]
    figures = collect_figures_for_chunks(chunks, capsule_root=tmp_path)
    assert len(figures) == 1
    f = figures[0]
    assert f.figure_id == "pdf_p3_i2"
    assert f.image_b64 == base64.b64encode(img).decode("ascii")
    assert "Schematic" in f.caption
    assert f.label.startswith("Figure 1")


def test_collect_skips_unknown_capsule(tmp_path):
    chunks = [_chunk("doi:10.1/missing", ["pdf_p1_i0"])]
    assert collect_figures_for_chunks(chunks, capsule_root=tmp_path) == []


def test_collect_skips_unknown_ref(tmp_path):
    img = b"x" * 20
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p001_i00.png",
                "page": 1,
                "index": 0,
                "figure_number": "1",
                "caption": "C1",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunks = [_chunk("doi:10.1/x", ["pdf_p99_i99"])]
    assert collect_figures_for_chunks(chunks, capsule_root=tmp_path) == []


def test_collect_dedupes_across_chunks(tmp_path):
    img = b"x" * 20
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p001_i00.png",
                "page": 1,
                "index": 0,
                "figure_number": "1",
                "caption": "C1",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunks = [
        _chunk("doi:10.1/x", ["pdf_p1_i0"]),
        _chunk("doi:10.1/x", ["pdf_p1_i0"]),
    ]
    assert len(collect_figures_for_chunks(chunks, capsule_root=tmp_path)) == 1


def test_collect_uses_parent_paper_id_for_external_chunks(tmp_path):
    img = b"x" * 20
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p001_i00.png",
                "page": 1,
                "index": 0,
                "figure_number": "1",
                "caption": "C1",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunks = [_chunk("external:repo", ["pdf_p1_i0"], parent="doi:10.1/x")]
    figures = collect_figures_for_chunks(chunks, capsule_root=tmp_path)
    assert len(figures) == 1
    assert figures[0].figure_id == "pdf_p1_i0"
