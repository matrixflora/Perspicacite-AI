"""Per-paper capsule builder.

Orchestrates: PDF parse -> section split -> chunk per section -> tag -> embed ->
write Chroma + write capsule directory (metadata.json, figures/, text/,
resources.json). ASB-aligned schema; on-disk layout is byte-compatible with
ASB capsules (see docs/superpowers/specs/2026-05-13-capsule-multimodal-rag-design.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from perspicacite.models.papers import Paper
from perspicacite.pipeline.parsers.figures import RawFigure
from perspicacite.pipeline.parsers.section_splitter import split_sections

CAPSULE_VERSION = "0.1"


def capsule_dir_for(paper: Paper, *, root: Path) -> Path:
    """Return the capsule directory for ``paper`` under ``root``.

    Paper IDs (e.g. ``doi:10.1234/abc`` or ``local:abc123``) are filesystem-
    sanitized: ``:`` becomes ``_`` and ``/`` becomes ``__``.
    """
    safe = paper.id.replace(":", "_").replace("/", "__")
    return root / safe


def write_metadata(
    capsule_dir: Path,
    *,
    paper: Paper,
    producer_version: str,
    source: str | None = None,
) -> None:
    """Write ``capsule_dir/metadata.json`` with the v0.1 Capsule schema."""
    capsule_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "capsule_version": CAPSULE_VERSION,
        "producer": "perspicacite",
        "producer_version": producer_version,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "paper_id": paper.id,
        "title": paper.title,
        "authors": [a.model_dump() for a in (paper.authors or [])],
        "year": getattr(paper, "year", None),
        "doi": getattr(paper, "doi", None),
        "source": source or (paper.source.value if paper.source else None),
        "task_id": None,
    }
    (capsule_dir / "metadata.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_figures(capsule_dir: Path, *, figures: list[RawFigure]) -> int:
    """Persist each ``RawFigure``'s bytes and emit ``figures/index.json``.

    Returns the number of figures written. Filenames follow ASB's
    ``fig_p<page:03d>_i<idx:02d>.<ext>`` convention (already set on each
    ``FigureRecord``).
    """
    fig_dir = capsule_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for raw in figures:
        rec = raw.record
        target = fig_dir / rec.filename
        target.write_bytes(raw.image_bytes)
        records.append(asdict(rec))

    (fig_dir / "index.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(records)


_FIG_MENTION_RE = re.compile(
    r"\b(?:fig(?:ure|\.)?|scheme)\s+([A-Za-z]?\d+[A-Za-z]?)\b",
    re.IGNORECASE,
)


def resolve_figure_refs(text: str, figures: list[RawFigure]) -> list[str]:
    """Return a deduped list of ``figure_id`` strings mentioned in ``text``.

    Only mentions whose ``figure_number`` exists in ``figures`` are kept.
    """
    if not text or not figures:
        return []
    by_number: dict[str, str] = {}
    for raw in figures:
        rec = raw.record
        if rec.figure_number:
            by_number.setdefault(
                rec.figure_number.lower(), f"pdf_p{rec.page}_i{rec.index}",
            )
    out: list[str] = []
    seen: set[str] = set()
    for m in _FIG_MENTION_RE.finditer(text):
        key = m.group(1).lower()
        fid = by_number.get(key)
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


def write_blocks(
    capsule_dir: Path, *, text: str,
    figures: "list[RawFigure] | None" = None,
) -> int:
    """Section-split ``text`` and emit one paragraph-block per row into
    ``text/blocks.jsonl``.

    V1 block type is always ``paragraph``. Schema reserves ``heading`` /
    ``caption`` / ``table_latex`` / ``equation_latex`` for V2. ``char_span``
    is the offsets of the block content within ``text``.
    """
    text_dir = capsule_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    out_path = text_dir / "blocks.jsonl"

    if not text:
        out_path.write_text("", encoding="utf-8")
        return 0

    sm = split_sections(text)
    rows: list[dict] = []
    block_idx = 0
    for section, section_text in sm.sections.items():
        if not section_text.strip():
            continue
        for paragraph in _split_paragraphs(section_text):
            start = text.find(paragraph)
            end = start + len(paragraph) if start >= 0 else None
            rows.append({
                "block_id": f"b{block_idx:06d}",
                "page": None,
                "bbox": None,
                "type": "paragraph",
                "content": paragraph,
                "section": section,
                "char_span": [start, end] if start >= 0 else None,
                "figure_refs": resolve_figure_refs(paragraph, figures or []),
                "table_refs": [],
                "resource_refs": [],
            })
            block_idx += 1

    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )

    # figure_mentions.jsonl — one row per (block_id, figure_id) pair
    mentions: list[dict] = []
    for r in rows:
        for fid in r.get("figure_refs", []):
            mentions.append({"block_id": r["block_id"], "figure_id": fid})
    (text_dir / "figure_mentions.jsonl").write_text(
        "\n".join(json.dumps(x) for x in mentions) + ("\n" if mentions else ""),
        encoding="utf-8",
    )

    return len(rows)


def _split_paragraphs(text: str) -> list[str]:
    """Split a section's text on blank lines; trim each paragraph."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]
