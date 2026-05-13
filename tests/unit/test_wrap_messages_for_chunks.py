import json
from pathlib import Path
from unittest.mock import MagicMock

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.multimodal import wrap_messages_for_chunks


def _cfg(tmp_path, enabled=True, max_images=6):
    cfg = MagicMock()
    cfg.multimodal.enabled = enabled
    cfg.multimodal.max_images = max_images
    cfg.capsule.root = tmp_path
    return cfg


def _write_capsule(root: Path, paper_id: str, figures: list[dict], image_bytes: bytes) -> None:
    safe = paper_id.replace(":", "_").replace("/", "__")
    cap = root / safe
    (cap / "figures").mkdir(parents=True, exist_ok=True)
    (cap / "figures" / "index.json").write_text(json.dumps(figures))
    for f in figures:
        (cap / "figures" / f["filename"]).write_bytes(image_bytes)


def test_disabled_returns_base(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    base = [{"role": "user", "content": "q"}]
    chunk = DocumentChunk(id="c1", text="t",
        metadata=ChunkMetadata(paper_id="doi:10.1/x", chunk_index=0, figure_refs=["pdf_p1_i0"]))
    out = wrap_messages_for_chunks(
        base_messages=base, chunks=[chunk], model="claude-3-5-sonnet", config=cfg)
    assert out is base


def test_no_figures_returns_base(tmp_path):
    cfg = _cfg(tmp_path)
    base = [{"role": "user", "content": "q"}]
    chunk = DocumentChunk(id="c1", text="t",
        metadata=ChunkMetadata(paper_id="doi:10.1/x", chunk_index=0, figure_refs=[]))
    out = wrap_messages_for_chunks(
        base_messages=base, chunks=[chunk], model="claude-3-5-sonnet", config=cfg)
    assert out is base


def test_vision_path(tmp_path):
    cfg = _cfg(tmp_path)
    _write_capsule(tmp_path, "doi:10.1/x", [
        {"filename": "fig_p001_i00.png", "page": 1, "index": 0,
         "figure_number": "1", "caption": "C", "subcomponent_label": "", "panel_files": []}
    ], b"x" * 50)
    base = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
    ]
    chunk = DocumentChunk(id="c1", text="t",
        metadata=ChunkMetadata(paper_id="doi:10.1/x", chunk_index=0, figure_refs=["pdf_p1_i0"]))
    out = wrap_messages_for_chunks(
        base_messages=base, chunks=[chunk], model="claude-3-5-sonnet", config=cfg)
    assert out is not base
    assert isinstance(out[-1]["content"], list)
    assert any(p["type"] == "image_url" for p in out[-1]["content"])
