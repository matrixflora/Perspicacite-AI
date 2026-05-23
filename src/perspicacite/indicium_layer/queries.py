"""SPARQL templates + namespace constants for the claim graph.

Subplan A: namespace constants, graph-name helpers, and the single
``cito_edges_for_claim`` helper used by the typed-contradiction strategy.
Subplan B will extend this with the five typed-traversal queries
(claims_supporting / claims_disputing / evidence_trace / ...).
"""

from __future__ import annotations

from typing import Any

# ---------- Namespaces (single source of truth across the package) ----------

ASB_NS = "https://asb.holobiomics.org/ns/asb#"
CITO_NS = "http://purl.org/spar/cito/"
PROV_NS = "http://www.w3.org/ns/prov#"
FABIO_NS = "http://purl.org/spar/fabio/"
DOCO_NS = "http://purl.org/spar/doco/"
OA_NS = "http://www.w3.org/ns/oa#"
DCT_NS = "http://purl.org/dc/terms/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD_NS = "http://www.w3.org/2001/XMLSchema#"

SPARQL_PREFIXES = f"""
PREFIX asb: <{ASB_NS}>
PREFIX cito: <{CITO_NS}>
PREFIX prov: <{PROV_NS}>
PREFIX fabio: <{FABIO_NS}>
PREFIX doco: <{DOCO_NS}>
PREFIX oa: <{OA_NS}>
PREFIX dct: <{DCT_NS}>
PREFIX rdf: <{RDF_NS}>
PREFIX xsd: <{XSD_NS}>
"""

# ---------- Type IRIs (frequently referenced by builder + strategies) ----------

IRI_CLAIM = f"{ASB_NS}Claim"
IRI_EVIDENCE = f"{ASB_NS}Evidence"
IRI_TEXT_CHUNK = f"{DOCO_NS}TextChunk"
IRI_RESEARCH_PAPER = f"{FABIO_NS}ResearchPaper"
IRI_RUN_ACTIVITY = f"{PROV_NS}Activity"

IRI_CONTEXT = f"{ASB_NS}context"
IRI_SUBJECT = f"{ASB_NS}subject"
IRI_QUALIFIER = f"{ASB_NS}qualifier"
IRI_RELATION = f"{ASB_NS}relation"
IRI_OBJECT = f"{ASB_NS}object"
IRI_EVIDENCE_PROP = f"{ASB_NS}evidence"
IRI_EVIDENCE_TYPE = f"{ASB_NS}evidenceType"
IRI_DERIVED_FROM_PASSAGE = f"{ASB_NS}derivedFromPassage"
IRI_WAS_DERIVED_FROM = f"{PROV_NS}wasDerivedFrom"
IRI_WAS_GENERATED_BY = f"{PROV_NS}wasGeneratedBy"
IRI_CREATED = f"{DCT_NS}created"
IRI_RDF_TYPE = f"{RDF_NS}type"

# ---------- Named graph helpers ----------


def cito_graph_iri(kb_name: str) -> str:
    return f"kb://{kb_name}/graphs/cito"


def runs_graph_iri(kb_name: str) -> str:
    return f"kb://{kb_name}/graphs/runs"


# ---------- Edge readers ----------


def cito_edges_for_claim(store: Any, kb_name: str, claim_iri: str) -> list[dict[str, str]]:
    """Return CiTO edges where `claim_iri` is the subject.

    Each row: ``{"predicate": ..., "object": ..., "confidence": ..., "run": ...}``.
    """
    g = cito_graph_iri(kb_name)
    sparql = (
        SPARQL_PREFIXES
        + f"""
        SELECT ?predicate ?object ?confidence ?run
        WHERE {{
            GRAPH <{g}> {{
                ?meta rdf:subject <{claim_iri}> ;
                      rdf:predicate ?predicate ;
                      rdf:object ?object ;
                      asb:confidence ?confidence ;
                      prov:wasGeneratedBy ?run .
            }}
        }}
    """
    )
    return store.select(sparql)
