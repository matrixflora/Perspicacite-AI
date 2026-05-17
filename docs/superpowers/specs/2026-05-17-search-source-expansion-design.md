# Search Source Expansion — Design Spec
**Date:** 2026-05-17
**Status:** Approved, pending implementation plan

---

## Problem

Perspicacite's search layer today routes every query through SciLEx (which aggregates Semantic Scholar, OpenAlex, PubMed, arXiv, HAL, DBLP) and a stub Google Scholar provider that always returns `[]`. There are no domain-specialized sources: chemistry queries get no PubChem signal, physics queries miss INSPIRE-HEP, and the open-access long-tail is invisible to CORE. Europe PMC exists only as a *download* source, not a search source. The cite-graph layer (OpenAlex + Semantic Scholar) has no cross-validation path.

---

## Goal

Add domain-aware routing infrastructure and wire up six new providers across three implementation waves, without breaking existing behavior for current users.

---

## Architecture

### DomainClassifier (`search/domain_classifier.py`)

Lightweight keyword/regex heuristics — no LLM cost. Maps a query string to `list[str]` domain tags from:

```
{"biomedical", "chemistry", "cs", "physics", "astronomy", "general"}
```

Multi-label output: "computational drug discovery" → `["biomedical", "chemistry"]`.

`"general"` is a wildcard: providers tagged `general` are always included regardless of query domain.

Implementation: ordered regex rules checked against lowercased query. No ML model. Fast enough to run synchronously before provider dispatch.

### Extended `SearchProvider` Protocol (`search/protocols.py`)

Three new required attributes added to the existing protocol:

```python
domains: list[str]   # e.g. ["biomedical"], ["general"], ["chemistry"]
tier: str            # "reliable" | "external" | "flaky"
retry: int           # 0 = fail fast; 1-2 = retry with exponential backoff
```

Existing providers (SciLEx, PubMed) get `domains=["general", "biomedical"]`, `tier="reliable"`, `retry=0` — no behavioral change.

### DomainAwareAggregator (`search/domain_aggregator.py`)

Replaces `SearchAggregator`. Dispatch flow:

1. Classify query → domain tags via `DomainClassifier`
2. Include providers whose `domains` intersect query domains, or whose `domains` contains `"general"`
3. Skip providers absent from `config.search.enabled_providers` (if list is set)
4. Skip circuit-broken providers (see `ProviderHealthTracker` below)
5. Fan out concurrently with per-tier timeouts
6. Retry `external` / `flaky` providers per their `retry` count with exponential backoff (2 s, 5 s)
7. Merge: deduplicate by DOI (title-hash fallback for DOI-less records), preserve all source attributions in `Paper.metadata["sources"]`
8. Failed providers log at `warning` level and return `[]` — never block results

### ProviderHealthTracker (inline in `domain_aggregator.py`)

In-memory circuit breaker. If a provider accumulates 3 consecutive failures it is skipped for 5 minutes. Resets on first success. No persistence — resets on server restart. Prevents one flaky endpoint from adding latency to every query.

### Reliability tier policy

| Tier | Timeout | Retry | On failure |
|------|---------|-------|------------|
| `reliable` | `provider_timeout_s` (default 20 s) | 0 | log warning, return `[]` |
| `external` | `provider_timeout_s × 1.5` (default 30 s) | 1 | backoff 2 s, then `[]` |
| `flaky` | `provider_timeout_s × 2.25` (default 45 s) | 2 | backoff 2 s / 5 s, then `[]` |

---

## Config additions

New `search:` stanza in `config.example.yml` (and `config.yml`):

```yaml
search:
  provider_timeout_s: 20        # timeout for "reliable" tier; external = 1.5×, flaky = 2.25×
  max_results_per_provider: 25
  enabled_providers:            # omit to enable all registered providers
    - scilex
    - pubmed
    - europepmc
    - pubchem
    - core
    - inspire
    - ads
  core_api_key: ""              # optional; raises CORE rate limit
  ads_api_key: ""               # required for ADS; provider skipped if absent
```

Config schema extended in `config/schema.py` with a `SearchConfig` Pydantic model.

---

## Wave A — EuropePMC Search + PubChem

### `search/europepmc_search.py` — `EuropePMCSearchProvider`

- **API**: `https://www.ebi.ac.uk/europepmc/webservices/rest/search`
- **Params**: `query`, `resultType=core`, `pageSize`, `fromSearchDate`/`toSearchDate`
- **Returns**: title, authors, year, DOI, PMID, abstract, journal, OA flag
- **Paper source**: `PaperSource.EUROPE_PMC` (new enum value)
- **Domains**: `["biomedical"]`
- **Tier**: `"reliable"`, retry: `0`
- **Key**: none required; ~10 req/s public rate limit

### `search/pubchem_search.py` — `PubChemSearchProvider`

Two-hop flow:

1. **Resolve input → CID**: auto-detect input format (InChIKey pattern → `inchikey/` prefix; SMILES detected by presence of ring/bond chars → `smiles/` prefix; else name search). Endpoint: `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{type}/{input}/cids/JSON`. Takes top CID on multi-match.
2. **CID → PMIDs**: `https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/xrefs/PubMedID/JSON`
3. **PMIDs → Papers**: delegates to existing `PubMedSearchAdapter` — reuses all parsing logic.

- **Paper source**: `PaperSource.PUBCHEM` as discovery tag; actual paper data is from PubMed
- **Domains**: `["chemistry"]`
- **Tier**: `"external"`, retry: `1`
- **Key**: none required

For free-text queries that aren't compound identifiers, name search is attempted; if PubChem returns no CID hit, provider returns `[]` gracefully.

---

## Wave B — CORE, INSPIRE-HEP, ADS

### `search/core_search.py` — `CORESearchProvider`

- **API**: `https://api.core.ac.uk/v3/search/works`
- **Auth**: `Authorization: Bearer {core_api_key}` if configured; unauthenticated allowed at lower rate
- **Params**: `q`, `limit`, year filter via `filters: {yearPublished: {$gte: YYYY, $lte: YYYY}}`
- **Returns**: title, authors, year, DOI, abstract, download URL, journal
- **Paper source**: `PaperSource.CORE`
- **Domains**: `["general"]` (cross-domain OA aggregator — always included)
- **Tier**: `"reliable"`, retry: `0`

### `search/inspire_search.py` — `INSPIREHEPSearchProvider`

- **API**: `https://inspirehep.net/api/literature`
- **Params**: `q` (plain text or INSPIRE syntax), `size`, `sort=mostrecent`; date filter appended as `de YYYY--YYYY`
- **Returns**: title, authors, year, DOI, arXiv ID, abstract, texkey
- **Paper source**: `PaperSource.INSPIRE_HEP`
- **Domains**: `["physics"]`
- **Tier**: `"reliable"`, retry: `0`
- **Key**: none required

### `search/ads_search.py` — `ADSSearchProvider`

- **API**: `https://api.adsabs.harvard.edu/v1/search/query`
- **Auth**: `Authorization: Bearer {ads_api_key}` — required; provider skips with `ads_unavailable_no_key` log if absent
- **Params**: `q`, `fl=title,author,year,doi,abstract,bibcode,identifier`, year filter via `pubdate:[YYYY TO YYYY]`
- **Returns**: title, authors, year, DOI, bibcode, abstract
- **Paper source**: `PaperSource.ADS`
- **Domains**: `["astronomy"]`
- **Tier**: `"external"`, retry: `1`

---

## Wave C — OpenCitations COCI (Cite-Graph)

Not a keyword search source. Adds a third arm to the cite-graph enrichment orchestrator.

### `pipeline/download/opencitations.py` — `fetch_opencitations_citations`

- **API**: `https://opencitations.net/index/coci/api/v1/citations/{doi}`
- **Returns**: list of `{citing_doi, cited_doi, timespan, journal_sc}` records
- **Key**: none required; ~10 req/s
- Maps to minimal OpenAlex-like dicts (DOI + year extracted from `timespan`) consumed by `_paper_from_oa_work`

### Integration in `pipeline/cite_graph.py`

`enrich_kb_from_cite_graph` spawns three concurrent citation-graph fetches:

```
OpenAlex citations  ─┐
SS citations        ──┤ merge + dedup by DOI → apply_cite_graph_filters → score_cite_hit
COCI citations      ─┘
```

A new `multi_source_bonus: float = 0.15` is added to `score_cite_hit`: DOIs appearing in ≥2 of the three sources receive the bonus. This rewards high-confidence citations (cross-validated by independent indexes) without penalizing COCI's lower recall.

COCI only provides citing-paper DOIs + minimal metadata. Full paper resolution still goes through the existing `retrieve_paper_content()` pipeline — no new download logic.

---

## New `PaperSource` enum values

Added to `models/papers.py`:

```python
EUROPE_PMC = "europe_pmc"
PUBCHEM = "pubchem"
CORE = "core"
INSPIRE_HEP = "inspire_hep"
ADS = "ads"
OPENCITATIONS = "opencitations"
```

---

## MCP surface

No new MCP tools. The existing `search_literature` tool passes through to the search layer and inherits all new providers automatically. `SearchResult` already carries `source` provenance — new sources appear in results transparently.

---

## Tests

All unit tests in `tests/unit/`, marked `not live`, using mock HTTP responses:

| File | What it tests |
|------|--------------|
| `test_domain_classifier.py` | 15–20 parametrized cases: single-domain, multi-domain, general fallback, edge cases (empty query, numeric-only) |
| `test_domain_aggregator.py` | Routing logic, dedup by DOI, title-hash fallback, circuit-breaker (3 failures → skip), partial failure (one provider fails, others succeed) |
| `test_europepmc_search.py` | Mock REST response → Paper mapping, year filter, empty result |
| `test_pubchem_search.py` | CID resolution (name / InChIKey / SMILES), PMID fetch, PubMed delegation, no-hit graceful return |
| `test_core_search.py` | Authenticated + unauthenticated paths, year filter |
| `test_inspire_search.py` | Query construction, date filter appended, Paper mapping |
| `test_ads_search.py` | Missing key → skip, auth header, bibcode preserved in metadata |
| `test_opencitations.py` | DOI extraction from COCI response, multi-source score bonus applied |

Live integration tests under `tests/integration/` gated by env-var API keys (`CORE_API_KEY`, `ADS_API_KEY`, `NCBI_EMAIL`).

---

## Out of scope

- Google Scholar via Chrome MCP (tracked separately in `roadmap-2026-05-followups.md`)
- Crossref as a keyword search source (Crossref search quality is poor for literature discovery; its value is metadata enrichment, which already happens in the download pipeline)
- DBLP as a standalone provider (already covered by SciLEx aggregation)
- Multi-user / tenant isolation
- Persistent circuit-breaker state across restarts
