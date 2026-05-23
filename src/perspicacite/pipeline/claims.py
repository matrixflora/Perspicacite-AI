"""Extract typed indicium claims (5-slot SuperPattern + ECO-typed evidence)
from retrieved passages, via the project LLM client."""
from __future__ import annotations

import json
import uuid
from typing import Any

_QUALIFIERS = {
    "causes", "prevents", "inhibits", "activates", "increases", "decreases",
    "correlates_with", "is_associated_with", "predicts", "interacts_with",
    "produces", "requires", "enables", "treats", "enhances", "reduces",
    "has_property", "is_part_of", "is_a", "consistent_with",
}
_EVIDENCE_TYPES = {"data", "citation", "knowledge", "inference", "speculation"}
_SOURCE_TYPES = {"text", "figure", "table", "image", "code", "data"}

_PROMPT = """You extract structured scientific claims from passages.
For each well-supported claim, output the 5-slot pattern:
context, subject, qualifier (one of: {qualifiers}), relation, object;
plus claim_type (explicit|implicit), evidence_type (one of: {evidence_types}),
source_type (one of: {source_types}), an exact quote, and source_doi.
Context: {context}
Passages:
{passages}
Return JSON: {{"claims": [{{...}}]}}"""


async def extract_claims(
    *, llm_client: Any, passages: list[dict], context: str | None = None,
    model: str | None = None,
) -> list[dict]:
    rendered = "\n\n".join(
        f"[{i}] doi={p.get('source', {}).get('doi')}: {p.get('chunk_text', '')}"
        for i, p in enumerate(passages)
    )
    prompt = _PROMPT.format(
        qualifiers=", ".join(sorted(_QUALIFIERS)),
        evidence_types=", ".join(sorted(_EVIDENCE_TYPES)),
        source_types=", ".join(sorted(_SOURCE_TYPES)),
        context=context or "(none)", passages=rendered,
    )
    messages = [{"role": "user", "content": prompt}]
    raw = await (llm_client.complete(messages=messages, model=model) if model
                 else llm_client.complete(messages=messages))
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[dict] = []
    for raw_claim in data.get("claims", []):
        claim = _coerce_claim(raw_claim)
        if claim is not None:
            out.append(claim)
    return out


def _coerce_claim(c: dict) -> dict | None:
    required = ("context", "subject", "qualifier", "relation", "object")
    if not all(c.get(k) for k in required):
        return None
    if c["qualifier"] not in _QUALIFIERS:
        return None  # out-of-vocabulary qualifier => drop (don't void the run)
    ev_type = c.get("evidence_type")
    if ev_type is not None and ev_type not in _EVIDENCE_TYPES:
        ev_type = None
    src_type = c.get("source_type")
    if src_type is not None and src_type not in _SOURCE_TYPES:
        src_type = None
    # Mint a stable id so every claim dict is Indicium-adapter-compatible.
    # Callers may override by setting claim["id"] after coercion if they have
    # a stable identifier from the upstream source (e.g. a DOI-scoped hash).
    claim: dict = {"id": f"perspicacite:{uuid.uuid4().hex[:8]}"}
    claim.update({k: c[k] for k in required})
    if c.get("claim_type") in {"explicit", "implicit"}:
        claim["claim_type"] = c["claim_type"]
    evidence: dict = {}
    if c.get("source_doi"):
        evidence["doi"] = c["source_doi"]
    if c.get("quote"):
        evidence["quote"] = c["quote"]
    if ev_type:
        evidence["evidence_type"] = ev_type
    if src_type:
        evidence["source_type"] = src_type
    if evidence:
        claim["evidence"] = [evidence]
    return claim


_ASB = "https://asb.holobiomics.org/ns/asb#"


def claims_to_graph(claims: list[dict]):
    """Serialize claim dicts to an rdflib Graph using the asb: vocabulary that
    indicium's SHACL targets (a asb:Claim with asb:context/subject/...)."""
    import rdflib

    g = rdflib.Graph()
    asb = rdflib.Namespace(_ASB)
    for i, c in enumerate(claims):
        cid = c.get("id") or f"pos:{i}"
        node = rdflib.URIRef(f"urn:perspicacite:claim:{cid}")
        g.add((node, rdflib.RDF.type, asb.Claim))
        for slot in ("context", "subject", "qualifier", "relation", "object"):
            if c.get(slot):
                g.add((node, asb[slot], rdflib.Literal(c[slot])))
    return g


def validate_claims(claims: list[dict]) -> tuple[bool, str]:
    """SHACL-validate claims against indicium's shapes. Returns (conforms, report)."""
    import indicium

    return indicium.validate_graph(claims_to_graph(claims))
