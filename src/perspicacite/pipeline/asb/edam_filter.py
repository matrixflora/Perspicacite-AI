"""EDAM IRI pre-filter for skill KB search.

Cuts the candidate chunk set before embedding ranking by matching EDAM
operation and topic IRIs stored in chunk metadata. Chunks with no EDAM
metadata always pass through (fail-open) to avoid over-filtering on
legacy chunks that predate EDAM annotation.

Used by the search_skill_kb MCP tool.
"""
from __future__ import annotations

from typing import Any


def edam_pre_filter(
    chunks: list[dict[str, Any]],
    *,
    edam_operation: str | None,
    edam_topics: list[str] | None,
) -> list[dict[str, Any]]:
    """Filter chunks by EDAM IRI overlap.

    A chunk passes when:
    - It has no EDAM metadata (fail-open / backward-compatible), OR
    - Its edam_operation matches the requested one (if given), AND
    - At least one of its edam_topics overlaps with the requested set (if given).

    When neither edam_operation nor edam_topics is given, all chunks pass.

    Args:
        chunks: List of chunk dicts as returned by search_knowledge_base.
            Each may have a ``metadata`` dict with ``edam_operation`` (str)
            and ``edam_topics`` (list[str]).
        edam_operation: Optional EDAM operation IRI to filter on.
        edam_topics: Optional list of EDAM topic IRIs; chunk passes if any overlap.

    Returns:
        Filtered list preserving original order.
    """
    # No criteria → short-circuit
    if not edam_operation and not edam_topics:
        return chunks

    target_topics: set[str] = set(edam_topics or [])
    result: list[dict[str, Any]] = []

    for chunk in chunks:
        meta = chunk.get("metadata") or {}

        # Detect whether this chunk has any EDAM annotation at all.
        # Distinction: a chunk with "edam_topics": [] has a topic key but
        # no matching topics — it is annotated (with an empty set) and
        # should be subject to filtering. A chunk with no "edam_operation"
        # or "edam_topics" keys at all is unannotated and passes through
        # (fail-open / backward-compat with legacy chunks).
        chunk_op: str | None = meta.get("edam_operation") or None
        has_op_key = "edam_operation" in meta
        has_topic_key = "edam_topics" in meta

        # Fail-open: no EDAM keys at all → pass through (backward-compat with
        # legacy chunks that predate EDAM annotation).
        if not has_op_key and not has_topic_key:
            result.append(chunk)
            continue

        # Check operation filter
        op_ok = True
        if edam_operation:
            op_ok = (chunk_op == edam_operation)

        # Check topic filter.  A chunk with "edam_topics": [] has the key but
        # no matching topics — it is annotated (empty set) and may be filtered.
        topic_ok = True
        if target_topics:
            chunk_topics: set[str] = set(meta.get("edam_topics") or [])
            topic_ok = bool(chunk_topics & target_topics)

        if op_ok and topic_ok:
            result.append(chunk)

    return result
