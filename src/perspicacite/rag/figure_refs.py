"""Collect ``FigureRef`` records from retrieved chunks.

Sub-project C (2026-05-15 design). Walks the retrieved chunks, harvests
the ``figure_refs`` ids from each chunk's metadata, dedups by figure id,
and (best-effort) loads captions / labels from the originating paper's
capsule ``figures/index.json`` when available.

Image thumbnails (``thumbnail_b64``) are NOT loaded here — that's a heavier
operation; v1 only surfaces the references. The web UI uses the existing
capsule resource path to render thumbnails on demand.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from pathlib import Path

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.models.rag import FigureRef

logger = get_logger("perspicacite.rag.figure_refs")

_THUMBNAIL_MAX_BYTES = 200_000


def _load_thumbnail_b64(capsule_root, paper_id: str, fig_id: str):
    """Return base64-encoded PNG bytes from ``<capsule_root>/<paper_id>/figures/<fig_id>.png``, or None."""
    if not capsule_root or not paper_id or not fig_id:
        return None
    candidate = Path(capsule_root) / paper_id / "figures" / f"{fig_id}.png"
    try:
        if not candidate.exists():
            return None
        data = candidate.read_bytes()
    except OSError:
        return None
    if len(data) > _THUMBNAIL_MAX_BYTES:
        return None
    return base64.b64encode(data).decode("ascii")


def _capsule_dir_for_paper_id(paper_id: str, *, capsule_root: Path) -> Path:
    safe = paper_id.replace(":", "_").replace("/", "__")
    return capsule_root / safe


def _load_caption_for_figure(
    paper_id: str, figure_id: str, *, capsule_root: Path
) -> tuple[str | None, str | None]:
    """Best-effort caption + label lookup. Returns (label, caption); both
    may be None when the capsule index isn't reachable."""
    cap_dir = _capsule_dir_for_paper_id(paper_id, capsule_root=capsule_root)
    index_path = cap_dir / "figures" / "index.json"
    if not index_path.exists():
        return (None, None)
    try:
        records = json.loads(index_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return (None, None)
    if not isinstance(records, list):
        return (None, None)
    for rec in records:
        page = rec.get("page", 0)
        idx = rec.get("index", 0)
        rec_id = f"pdf_p{page}_i{idx}"
        if rec_id != figure_id:
            continue
        fn = rec.get("figure_number") or ""
        sub = rec.get("subcomponent_label") or ""
        label = f"Figure {fn}{sub}".strip() if fn else None
        caption = rec.get("caption")
        return (label, caption)
    return (None, None)


def collect_figure_refs(
    chunks: Iterable[DocumentChunk],
    *,
    capsule_root: Path,
) -> list[FigureRef]:
    """Project figure_refs across chunks into a deduped FigureRef list."""
    seen: set[str] = set()
    out: list[FigureRef] = []
    for c in chunks:
        md = c.metadata
        fids = getattr(md, "figure_refs", None) or []
        for fid in fids:
            if fid in seen:
                continue
            seen.add(fid)
            label, caption = _load_caption_for_figure(
                md.paper_id, fid, capsule_root=capsule_root,
            )
            out.append(FigureRef(
                id=fid,
                paper_id=md.paper_id,
                label=label,
                caption=caption,
                thumbnail_b64=_load_thumbnail_b64(capsule_root, md.paper_id, fid),
            ))
    return out
