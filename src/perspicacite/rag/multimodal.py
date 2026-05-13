"""Cycle B — multimodal RAG: pull figure_refs into LLM calls.

Collects ``figure_refs`` across retrieved chunks, resolves each id against
the originating paper's capsule ``figures/index.json``, loads image bytes,
and exposes helpers used by per-mode RAG hooks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.pipeline.parsers.figure_context import (
    FigureContext,
    is_si_label,
    load_image_b64,
)

logger = get_logger("perspicacite.rag.multimodal")


def _capsule_dir_for_paper_id(paper_id: str, *, capsule_root: Path) -> Path:
    safe = paper_id.replace(":", "_").replace("/", "__")
    return capsule_root / safe


def _figure_id_for(rec: dict) -> str:
    page = rec.get("page", 0)
    idx = rec.get("index", 0)
    return f"pdf_p{page}_i{idx}"


def _label_for(rec: dict) -> str:
    fn = rec.get("figure_number") or ""
    sub = rec.get("subcomponent_label") or ""
    if fn:
        return f"Figure {fn}{sub}".strip()
    return f"Figure (p{rec.get('page', 0)} #{rec.get('index', 0)})"


def _paper_id_for_chunk(c: DocumentChunk) -> str | None:
    parent = getattr(c.metadata, "parent_paper_id", None)
    if parent:
        return parent
    return c.metadata.paper_id


def _load_capsule_figures(capsule_dir: Path) -> list[dict]:
    idx = capsule_dir / "figures" / "index.json"
    if not idx.is_file():
        return []
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("capsule_index_unreadable", path=str(idx), error=str(exc))
        return []


def collect_figures_for_chunks(
    chunks: Iterable[DocumentChunk], *, capsule_root: Path,
) -> list[FigureContext]:
    """Return deduped FigureContext list for figure_refs across chunks.

    Skips chunks without figure_refs. Skips refs that don't resolve in the
    chunk's capsule. Figures whose image file is missing get ``image_b64=None``
    and are filtered downstream in ``build_messages_with_figures``.
    """
    seen: dict[tuple[str, str], FigureContext] = {}
    capsule_cache: dict[str, list[dict]] = {}

    for chunk in chunks:
        refs = getattr(chunk.metadata, "figure_refs", None) or []
        if not refs:
            continue
        paper_id = _paper_id_for_chunk(chunk)
        if not paper_id:
            continue
        cap_dir = _capsule_dir_for_paper_id(paper_id, capsule_root=capsule_root)
        if paper_id not in capsule_cache:
            capsule_cache[paper_id] = _load_capsule_figures(cap_dir)
        records = capsule_cache[paper_id]
        for fid in refs:
            key = (paper_id, fid)
            if key in seen:
                continue
            match = next((r for r in records if _figure_id_for(r) == fid), None)
            if match is None:
                continue
            filename = match.get("filename")
            image_b64 = (
                load_image_b64(cap_dir / "figures" / filename) if filename else None
            )
            label = _label_for(match)
            fc = FigureContext(
                figure_id=fid,
                label=label,
                caption=(match.get("caption") or "").strip(),
                source="pdf",
                panels=tuple(
                    p.get("label")
                    for p in (match.get("panel_files") or [])
                    if p.get("label")
                ),
                image_b64=image_b64,
                filename=filename,
                is_supplementary=is_si_label(label),
            )
            seen[key] = fc
    return list(seen.values())
