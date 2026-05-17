# DBLP SPARQL Provider + `--ingest-mode` CLI Flag Design

**Date:** 2026-05-17
**Status:** Approved

---

## Goal

Add a `DBLPSPARQLSearchProvider` that surfaces citation-ranked, seminal CS papers
via DBLP's SPARQL endpoint (complementing the existing SciLEx DBLP keyword-search),
enriches results with abstracts from SemOpenAlex SPARQL, and expose an
`--ingest-mode` CLI flag on `create-kb` / `add-to-kb` so users can switch to
abstract-only ingest without editing `config.yml`.
A small CLAUDE.md correction removes two outdated "known gap" paragraphs.

## Architecture

Three independent changes delivered in a single plan:

1. **`DBLPSPARQLSearchProvider`** — new search adapter in
   `src/perspicacite/search/dblp_sparql_search.py`.
2. **`--ingest-mode` CLI flag** — `@click.option` on the `create-kb` and
   `add-to-kb` Click commands in `src/perspicacite/cli.py`.
3. **CLAUDE.md correction** — remove stale "not yet wired" notes for
   `recency_weight` and `kb_names` in advanced / profound / agentic modes.

## Tech Stack

- `httpx.AsyncClient` for async SPARQL POSTs (same HTTP client used by other
  search providers)
- DBLP QLever SPARQL endpoint: `https://sparql.dblp.org/sparql`
- SemOpenAlex SPARQL endpoint: `https://semopenalex.org/sparql`
- Click (already a dependency) for the CLI flag
- `pytest` + `unittest.mock` for unit tests

---

## Feature 1 — DBLPSPARQLSearchProvider

### File

`src/perspicacite/search/dblp_sparql_search.py` (new)

### Interface

Follows the existing `SearchProvider` protocol:

```python
class DBLPSPARQLSearchProvider:
    name = "dblp_sparql"
    tier = "external"      # 30 s timeout via DomainAwareAggregator
    retry = 1
    domains = ["general"]  # runs on every query

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]: ...
```

### Two-phase search

#### Phase 1 — DBLP SPARQL (citation-ranked title search)

POST to `https://sparql.dblp.org/sparql` with:

```http
Accept: application/qlever-results+json
Content-Type: application/x-www-form-urlencoded;charset=UTF-8
Body: query=<SPARQL>
```

SPARQL template (max 8 keywords extracted from `query`; stop-words filtered):

```sparql
PREFIX dblp: <https://dblp.org/rdf/schema#>
PREFIX cito: <http://purl.org/spar/cito/>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?label ?doi ?year (COUNT(?citation) AS ?cites) ?score WHERE {
  ?publ rdf:type dblp:Publication ;
        dblp:title ?title ;
        dblp:yearOfPublication ?year ;
        dblp:doi ?doi ;
        dblp:omid ?omid .
  ?publ rdfs:label ?label .
  BIND(LCASE(STR(?title)) AS ?lowerTitle)
  BIND(
    IF(CONTAINS(?lowerTitle, 'kw1'), 1, 0) +
    IF(CONTAINS(?lowerTitle, 'kw2'), 1, 0) +
    ... AS ?score
  )
  FILTER(?score >= 1)
  OPTIONAL {
    ?citation rdf:type cito:Citation ;
              cito:hasCitedEntity ?omid .
  }
  [YEAR_FILTER]
}
GROUP BY ?label ?doi ?year ?score
ORDER BY DESC(?score) DESC(?cites)
LIMIT {max_results}
```

`[YEAR_FILTER]` expands to `FILTER(?year >= year_min && ?year <= year_max)` when
either bound is set; omitted otherwise.

Keyword extraction: split `query` on whitespace and punctuation, lowercase,
drop tokens in a small English stop-word set
(`{"a","an","the","of","in","on","and","or","for","to","with","is","are","that"}`),
cap at 8 tokens.

Result parsing: `data["res"]` rows are `[label, doi, year, cites, score]`.
Strip angle brackets from DOI value (`doi[1:-1]`), replace `\_` → `_`.

#### Phase 2 — SemOpenAlex SPARQL (abstract enrichment)

After phase 1, issue a **single batch SPARQL** to
`https://semopenalex.org/sparql` for all DOIs at once:

```sparql
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX schema:  <https://schema.org/>

SELECT ?doi ?abstract WHERE {
  VALUES ?doi { "10.1234/a" "10.5678/b" ... }
  ?work schema:identifier ?doi ;
        dcterms:abstract ?abstract .
}
```

The exact predicate for DOI identifier (`schema:identifier` vs.
`dcterms:identifier`) is confirmed against the live endpoint during
implementation by issuing a test query. The implementation constant
`_SEMOA_DOI_PRED` is updated accordingly.

On success: add `abstract` field to matched `Paper` objects.

**Fallback:** if SemOpenAlex returns HTTP ≠ 200, times out, or returns no
abstract for a DOI, that `Paper` retains its phase-1 fields (title, doi, year,
citation_count) with `abstract=None`. Provider never raises — always returns
whatever it has. SemOpenAlex failures are logged
(`semopenalex_enrich_failed`) but do **not** trip the DBLP provider's
circuit breaker.

### Paper construction

```python
Paper(
    title=label,
    doi=doi,
    year=int(year),
    abstract=abstract_or_none,
    metadata={
        "sources": ["dblp_sparql"],
        "citation_count": int(cites),
    },
)
```

### Config

No new config stanza. Add to `config.example.yml` under
`search.enabled_providers`:

```yaml
# - dblp_sparql   # DBLP citation-ranked + SemOpenAlex abstracts (free, no key)
```

Add to `build_aggregator` in `domain_aggregator.py`:

```python
if "dblp_sparql" in enabled:
    try:
        from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider
        providers.append(DBLPSPARQLSearchProvider())
    except Exception as exc:
        logger.warning("build_aggregator_dblp_sparql_unavailable", error=str(exc))
```

### `__init__.py` export

Add `DBLPSPARQLSearchProvider` to
`src/perspicacite/search/__init__.py` exports.

### Tests

`tests/unit/test_dblp_sparql_search.py`:

| Test | What it covers |
|------|---------------|
| `test_build_sparql_query_keywords` | keyword tokenisation, stop-word removal, BIND clauses generated correctly |
| `test_build_sparql_query_year_filter` | year_min / year_max injects FILTER clause |
| `test_build_sparql_query_max_keywords` | > 8 keywords truncated to 8 |
| `test_parse_dblp_response` | `data["res"]` rows → Paper list; DOI stripping |
| `test_semoa_enrich_success` | SPARQL batch response maps DOIs → abstracts |
| `test_semoa_enrich_partial` | DOIs not in SemOpenAlex response retain `abstract=None` |
| `test_semoa_enrich_failure` | SemOpenAlex HTTP 500 → papers returned without abstracts, no exception |
| `test_search_full_pipeline` | mock both SPARQL calls end-to-end, assert Paper fields |
| `test_build_aggregator_dblp_sparql_included` | `dblp_sparql` in enabled_providers → provider present |
| `test_build_aggregator_dblp_sparql_excluded` | `dblp_sparql` not in enabled_providers → provider absent |

---

## Feature 2 — `--ingest-mode` CLI flag

### Files modified

`src/perspicacite/cli.py` — `create-kb` command and `add-to-kb` command.

### Change

Add to both commands:

```python
@click.option(
    "--ingest-mode",
    type=click.Choice(["auto", "full_text", "abstract_only"]),
    default=None,
    help=(
        "Override knowledge_base.ingest_mode for this run. "
        "abstract_only skips PDF download (~80% faster for large corpora)."
    ),
)
```

When `ingest_mode` is not None, mutate the loaded config before passing to the
pipeline:

```python
if ingest_mode is not None:
    config.knowledge_base.ingest_mode = ingest_mode
```

This change is made in both `create_kb()` and `add_to_kb()` Click command
functions, before the call to `_create_kb_from_bibtex` /
`_add_bibtex_to_existing_kb`.

### Usage

```bash
# Fast abstract-only ingest for a 500-DOI bibliography
uv run perspicacite add-to-kb my-kb --from-bibtex refs.bib --ingest-mode abstract_only

# Force full text for a small priority set
uv run perspicacite add-to-kb my-kb --from-bibtex priority.bib --ingest-mode full_text
```

### Tests

`tests/unit/test_cli_ingest_mode.py`:

| Test | What it covers |
|------|---------------|
| `test_add_to_kb_ingest_mode_override` | `--ingest-mode abstract_only` sets `config.knowledge_base.ingest_mode` before pipeline call |
| `test_add_to_kb_ingest_mode_default` | omitting flag leaves config value unchanged |
| `test_create_kb_ingest_mode_override` | same for `create-kb` command |

---

## Feature 3 — CLAUDE.md correction

### Change

Remove two paragraphs that incorrectly describe `recency_weight` and
`kb_names` as "not yet wired" into advanced / profound / agentic modes. Code
inspection confirms both are already active in all six RAG modes (with the
partial exception of `literature_survey`, which accepts `kb_names` but fans
retrieval only partially — tracked separately).

Replace with accurate one-line notes pointing readers to
`src/perspicacite/retrieval/recency.py` and
`src/perspicacite/retrieval/multi_kb.py` respectively.

---

## Error handling summary

| Failure | Behaviour |
|---------|-----------|
| DBLP SPARQL non-200 | Log warning, return `[]`; circuit breaker increments |
| DBLP SPARQL timeout | Caught by `asyncio.wait_for` in `DomainAwareAggregator._call_provider`; provider retried once (`retry=1`) |
| SemOpenAlex non-200 or timeout | Log `semopenalex_enrich_failed`; return DBLP-only papers with `abstract=None`; does not increment DBLP circuit breaker |
| SemOpenAlex missing DOI in result | Paper kept with `abstract=None` |
| Empty keyword list after stop-word filter | Fallback: use first 3 tokens of raw query without filtering |

---

## Files created / modified

| File | Action |
|------|--------|
| `src/perspicacite/search/dblp_sparql_search.py` | Create |
| `src/perspicacite/search/__init__.py` | Modify — add export |
| `src/perspicacite/search/domain_aggregator.py` | Modify — add `dblp_sparql` block in `build_aggregator` |
| `src/perspicacite/config/schema.py` | No change (no new config fields) |
| `config.example.yml` | Modify — add commented `dblp_sparql` line |
| `src/perspicacite/cli.py` | Modify — `--ingest-mode` on `create-kb` and `add-to-kb` |
| `CLAUDE.md` | Modify — correct stale recency_weight / multi-KB paragraphs |
| `tests/unit/test_dblp_sparql_search.py` | Create |
| `tests/unit/test_cli_ingest_mode.py` | Create |
