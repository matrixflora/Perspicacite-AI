"""DBLP SPARQL + SemOpenAlex search provider.

Phase 1: POST to DBLP QLever SPARQL endpoint — citation-ranked title search.
Phase 2: POST to SemOpenAlex GraphDB SPARQL endpoint — batch abstract enrichment.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper, PaperSource

logger = get_logger("perspicacite.search.dblp_sparql")

_DBLP_SPARQL_URL = "https://sparql.dblp.org/sparql"
_SEMOA_SPARQL_URL = "https://semopenalex.org/sparql"

# SemOpenAlex SPARQL predicates. If queries return empty results, run the
# schema discovery query below and update these constants:
#
#   curl -s -X POST "https://semopenalex.org/sparql" \
#     -H "Accept: application/sparql-results+json" \
#     -H "Content-Type: application/x-www-form-urlencoded" \
#     --data-urlencode 'query=PREFIX schema: <https://schema.org/>
#   SELECT ?p ?o WHERE {
#     ?work schema:identifier <https://doi.org/10.1038/s41586-021-03819-2> .
#     ?work ?p ?o .
#   } LIMIT 20'
_SEMOA_DOI_PRED = "schema:identifier"   # DOI stored as IRI <https://doi.org/10.xxx>
_SEMOA_ABS_PRED = "dcterms:abstract"    # plain-text abstract literal

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "in", "on", "and", "or", "for",
    "to", "with", "is", "are", "that", "this", "it", "its",
    "at", "by", "from", "as", "be", "was", "were", "has",
})

_MAX_KEYWORDS = 8

_DOI_URI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)


def _tokenise_query(query: str) -> list[str]:
    """Extract up to _MAX_KEYWORDS lowercase alpha tokens, excluding stop words.

    Falls back to first 3 raw non-empty tokens when all tokens are stop words
    or too short (≤ 2 chars).
    """
    tokens = re.split(r"[\s\W]+", query.lower())
    filtered = [t for t in tokens if t and t not in _STOP_WORDS and len(t) > 2]
    if filtered:
        return filtered[:_MAX_KEYWORDS]
    # Fallback: all-stop-word or all-short input
    raw = [t for t in tokens if t]
    return raw[:3] if raw else [query.lower()[:20]]


def _clean_literal(val: str) -> str:
    """Strip SPARQL literal delimiters from a QLever result value.

    QLever returns string literals as '"value"' or '"value"^^xsd:type',
    and IRIs as '<iri>'. Plain strings (e.g. numeric counts) are returned as-is.
    """
    val = val.strip()
    if val.startswith('"'):
        # Find closing quote; ignore ^^type suffix
        end = val.find('"', 1)
        if end > 0:
            return val[1:end]
    if val.startswith("<") and val.endswith(">"):
        return val[1:-1]
    return val


def _build_dblp_sparql(
    keywords: list[str],
    max_results: int,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Build the DBLP QLever SPARQL query string."""
    score_expr = " +\n    ".join(
        f"IF(CONTAINS(?lowerTitle, '{kw}'), 1, 0)" for kw in keywords
    )
    year_filter = ""
    if year_min is not None or year_max is not None:
        lo = year_min if year_min is not None else 1800
        hi = year_max if year_max is not None else 2100
        year_filter = f"\n  FILTER(?year >= {lo} && ?year <= {hi})"

    return f"""PREFIX dblp: <https://dblp.org/rdf/schema#>
PREFIX cito: <http://purl.org/spar/cito/>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?label ?doi ?year (COUNT(?citation) AS ?cites) ?score WHERE {{
  ?publ rdf:type dblp:Publication ;
        dblp:title ?title ;
        dblp:yearOfPublication ?year ;
        dblp:doi ?doi ;
        dblp:omid ?omid .
  ?publ rdfs:label ?label .
  BIND(LCASE(STR(?title)) AS ?lowerTitle)
  BIND(
    {score_expr}
    AS ?score
  )
  FILTER(?score >= 1){year_filter}
  OPTIONAL {{
    ?citation rdf:type cito:Citation ;
              cito:hasCitedEntity ?omid .
  }}
}}
GROUP BY ?label ?doi ?year ?score
ORDER BY DESC(?score) DESC(?cites)
LIMIT {max_results}"""


def _parse_dblp_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse QLever JSON response (application/qlever-results+json) into records.

    Each record: {"title": str, "doi": str, "year": int | None, "cites": int}
    Row order: [label, doi, year, cites, score]
    """
    results: list[dict[str, Any]] = []
    for row in data.get("res", []):
        if len(row) < 5:
            continue
        try:
            title = _clean_literal(str(row[0]))
            doi_raw = _clean_literal(str(row[1])).replace("\\_", "_").strip()
            for prefix in _DOI_URI_PREFIXES:
                if doi_raw.startswith(prefix):
                    doi_raw = doi_raw[len(prefix):]
                    break
            year_str = _clean_literal(str(row[2]))
            year = int(year_str) if year_str.isdigit() else None
            cites_str = _clean_literal(str(row[3]))
            cites = int(cites_str) if cites_str.isdigit() else 0
        except (ValueError, TypeError, IndexError):
            continue
        if title and doi_raw:
            results.append({"title": title, "doi": doi_raw, "year": year, "cites": cites})
    return results


def _build_semoa_sparql(dois: list[str]) -> str:
    """Build a VALUES-based batch SPARQL to fetch abstracts from SemOpenAlex.

    Uses IRI form <https://doi.org/10.xxx> for DOI matching.
    Predicates configured via _SEMOA_DOI_PRED / _SEMOA_ABS_PRED constants.
    """
    doi_iris = " ".join(f"<https://doi.org/{doi}>" for doi in dois)
    return f"""PREFIX schema:  <https://schema.org/>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?doiUri ?abstract WHERE {{
  VALUES ?doiUri {{ {doi_iris} }}
  ?work {_SEMOA_DOI_PRED} ?doiUri .
  OPTIONAL {{ ?work {_SEMOA_ABS_PRED} ?abstract . }}
}}"""


def _parse_semoa_response(data: dict[str, Any]) -> dict[str, str]:
    """Parse standard SPARQL JSON response into {lowercase_doi: abstract} mapping."""
    doi_to_abstract: dict[str, str] = {}
    for binding in data.get("results", {}).get("bindings", []):
        doi_uri = binding.get("doiUri", {}).get("value", "")
        abstract = binding.get("abstract", {}).get("value", "")
        if not doi_uri or not abstract:
            continue
        doi = doi_uri
        for prefix in _DOI_URI_PREFIXES:
            if doi_uri.startswith(prefix):
                doi = doi_uri[len(prefix):]
                break
        doi_to_abstract[doi.lower()] = abstract
    return doi_to_abstract


async def _query_dblp(sparql: str) -> list[dict[str, Any]]:
    """POST SPARQL to DBLP QLever endpoint; return parsed records or [] on error.

    Retries once on httpx.ReadTimeout — QLever's first call can hit a cold cache
    (~25s+) and then respond in <500ms on retry.
    """
    async with httpx.AsyncClient(timeout=25.0) as client:
        for attempt in range(2):
            try:
                resp = await client.post(
                    _DBLP_SPARQL_URL,
                    headers={
                        "Accept": "application/qlever-results+json",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    },
                    data={"query": sparql},
                )
                resp.raise_for_status()
                return _parse_dblp_response(resp.json())
            except httpx.ReadTimeout as exc:
                if attempt == 0:
                    logger.info(
                        "dblp_sparql_query_retry",
                        error=f"{type(exc).__name__}: {exc!r}",
                        timeout_s=25.0,
                    )
                    continue
                logger.warning(
                    "dblp_sparql_query_error",
                    error=f"{type(exc).__name__}: {exc!r}",
                    timeout_s=25.0,
                )
                return []
            except Exception as exc:
                logger.warning(
                    "dblp_sparql_query_error",
                    error=f"{type(exc).__name__}: {exc!r}",
                    timeout_s=25.0,
                )
                return []
        return []


async def _enrich_semoa(dois: list[str]) -> dict[str, str]:
    """POST batch VALUES SPARQL to SemOpenAlex; return {doi: abstract} or {} on error."""
    if not dois:
        return {}
    sparql = _build_semoa_sparql(dois)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(
                _SEMOA_SPARQL_URL,
                headers={
                    "Accept": "application/sparql-results+json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                data={"query": sparql},
            )
            resp.raise_for_status()
            return _parse_semoa_response(resp.json())
        except Exception as exc:
            logger.warning(
                "semopenalex_enrich_failed",
                error=f"{type(exc).__name__}: {exc!r}",
                n_dois=len(dois),
            )
            return {}


class DBLPSPARQLSearchProvider:
    """Search DBLP via SPARQL (citation-ranked) with SemOpenAlex abstract enrichment.

    Phase 1: POST to sparql.dblp.org — returns papers whose titles match
             query keywords, ordered by keyword score x citation count.
    Phase 2: POST to semopenalex.org/sparql — batch-fetches abstracts for
             the DOIs from phase 1. Failures degrade gracefully (no abstract).
    """

    name: ClassVar[str] = "dblp_sparql"
    description: ClassVar[str] = (
        "DBLP citation-ranked title search + SemOpenAlex abstract enrichment (free SPARQL)"
    )
    domains: ClassVar[list[str]] = ["general"]
    tier: ClassVar[str] = "external"   # 30 s timeout via DomainAwareAggregator
    retry: ClassVar[int] = 1

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        keywords = _tokenise_query(query)
        sparql = _build_dblp_sparql(keywords, max_results, year_min, year_max)

        # Phase 1 — DBLP SPARQL
        records = await _query_dblp(sparql)
        if not records:
            return []

        # Phase 2 — SemOpenAlex abstract enrichment (best-effort)
        dois = [r["doi"] for r in records]
        abstracts = await _enrich_semoa(dois)

        papers: list[Paper] = []
        for rec in records:
            doi = rec["doi"]
            papers.append(
                Paper(
                    id=doi,
                    title=rec["title"],
                    doi=doi,
                    year=rec.get("year"),
                    abstract=abstracts.get(doi.lower()),
                    source=PaperSource.DBLP_SPARQL,
                    metadata={
                        "sources": ["dblp_sparql"],
                        "citation_count": rec.get("cites", 0),
                    },
                )
            )

        logger.info("dblp_sparql_search", query=query[:80], results=len(papers))
        return papers
