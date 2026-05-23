"""Batched LLM CiTO classifier.

Takes candidate (claim_a, claim_b) pairs and asks the LLM to label each one
as one of {supports, disputes, qualifies, citesForInformation, none}. Returns
the surviving edges (label != "none" and confidence >= threshold) as dicts
ready for ``ClaimGraphStore.add_edge_with_confidence``.
"""

from __future__ import annotations

import json
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.indicium_layer.cito_classifier")

CITO_CONFIDENCE_THRESHOLD = 0.6
_VALID_LABELS = {"supports", "disputes", "qualifies", "citesForInformation"}

_PROMPT = """You classify pairs of scientific claims into CiTO relations.

For each pair, output one of:
  - "supports" — claim A backs claim B's conclusion
  - "disputes" — claim A contradicts claim B
  - "qualifies" — claim A scopes / refines claim B's applicability
  - "citesForInformation" — claim A merely cites B without taking a stance
  - "none" — no meaningful relation

Return a JSON array (no prose, no markdown fences):
[{{"pair_id": <int>, "label": "<label>", "confidence": <0..1>}}, ...]

Pairs:
{pairs}
"""


def _render_pair(idx: int, a: dict, b: dict) -> str:
    return (
        f"[{idx}] A: {a.get('context', '')} | {a.get('subject', '')} "
        f"{a.get('qualifier', '')} {a.get('relation', '')} {a.get('object', '')}\n"
        f"      B: {b.get('context', '')} | {b.get('subject', '')} "
        f"{b.get('qualifier', '')} {b.get('relation', '')} {b.get('object', '')}"
    )


def _parse_response(raw: str) -> list[dict[str, Any]]:
    if not isinstance(raw, str):
        raw = str(raw)
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[-1].lstrip("json").strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    try:
        data = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        logger.warning("cito_classifier_bad_json", preview=s[:120])
        return []
    return data if isinstance(data, list) else []


async def classify_pairs(
    pairs: list[tuple[dict, dict]],
    *,
    llm_client: Any,
    batch_size: int = 10,
    model: str | None = None,
    confidence_threshold: float = CITO_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Classify pairs into CiTO edges. Returns surviving edges only."""
    edges: list[dict[str, Any]] = []
    for batch_start in range(0, len(pairs), batch_size):
        batch = pairs[batch_start : batch_start + batch_size]
        rendered = "\n\n".join(_render_pair(i, a, b) for i, (a, b) in enumerate(batch))
        prompt = _PROMPT.format(pairs=rendered)
        messages = [{"role": "user", "content": prompt}]
        try:
            kwargs: dict[str, Any] = {"messages": messages, "stage": "cito_classifier"}
            if model is not None:
                kwargs["model"] = model
            raw = await llm_client.complete(**kwargs)
        except Exception as exc:
            logger.warning("cito_classifier_llm_error", error=str(exc))
            continue
        for item in _parse_response(raw):
            try:
                idx = int(item.get("pair_id", -1))
                label = item.get("label")
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(batch)):
                continue
            if label not in _VALID_LABELS:
                continue
            if confidence < confidence_threshold:
                continue
            a, b = batch[idx]
            edges.append({"from": a, "to": b, "label": label, "confidence": confidence})
    return edges
