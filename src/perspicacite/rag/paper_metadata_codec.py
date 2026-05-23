"""Decoder for the JSON-encoded ``paper.metadata`` dict that round-trips
through ``ChunkMetadata.paper_metadata_json``.

Used by ``DynamicKnowledgeBase`` (retrieval) and the RAG modes (source
emission) — one canonical decoder, no inline duplication.
"""

from __future__ import annotations

import json
from typing import Any


def decode_paper_metadata_json(meta: Any) -> dict | None:
    """Decode the ``paper_metadata_json`` field from a ChunkMetadata-like
    object OR a dict-like row.

    Returns ``None`` if the field is absent, empty, or not valid JSON.
    Never raises.
    """
    if meta is None:
        return None
    if isinstance(meta, dict):
        blob = meta.get("paper_metadata_json")
    else:
        blob = getattr(meta, "paper_metadata_json", None)
    if not blob:
        return None
    try:
        return json.loads(blob)
    except (TypeError, ValueError):  # json.JSONDecodeError is a ValueError
        return None
