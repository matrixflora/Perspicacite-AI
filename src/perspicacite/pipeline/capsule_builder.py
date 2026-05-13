"""Per-paper capsule builder.

Orchestrates: PDF parse -> section split -> chunk per section -> tag -> embed ->
write Chroma + write capsule directory (metadata.json, figures/, text/,
resources.json). ASB-aligned schema; on-disk layout is byte-compatible with
ASB capsules (see docs/superpowers/specs/2026-05-13-capsule-multimodal-rag-design.md).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from perspicacite.models.papers import Paper

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
