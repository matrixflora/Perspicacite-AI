# Semantic Scholar Fallback Cite-Graph for arXiv — Design

**Status:** Approved 2026-05-15. Implementation plan: `docs/superpowers/plans/2026-05-15-ss-fallback-cite-graph.md` (to follow).

## Motivation

The 2026-05-15 audit measured a large gap between OpenAlex and Semantic Scholar forward-citation counts for arXiv-only preprints:

| Paper | OpenAlex `cited_by_count` | Semantic Scholar (community-reported) |
|---|---|---|
| RAG (`10.48550/arXiv.2005.11401`) | 18 | ~7,000 |
| Attention is All You Need (CrossRef DOI) | 6,538 | comparable |

The gap is specific to **arXiv-only preprints** — papers whose canonical DOI is `10.48550/arXiv.<id>` rather than a CrossRef-registered DOI. OpenAlex indexes citations via CrossRef relationships, so arXiv preprints lose most edges. Semantic Scholar indexes preprints natively and recovers them.

Today's `snowball_expand()` returns the underreported OpenAlex view for arXiv seeds. A user asking "what papers cite RAG?" gets 18 hits when the field reality is thousands.

## Goal

When the seed of a citation walk is an arXiv-only preprint, **also query Semantic Scholar's references/citations endpoints** and merge the results into the same `ExpansionHit` stream. Existing OpenAlex behavior unchanged for CrossRef-DOI seeds.

## Non-Goals

- Replacing OpenAlex as primary — its CrossRef-anchored data is higher quality for regular DOIs.
- Multi-source provenance modeling beyond a single string field on `ExpansionHit`. (If two providers report the same edge, dedup → one entry tagged `"both"`. No per-edge metadata about how each provider described it.)
- Caching SS responses. Defer to the existing `data/contextual_cache/` if measurable benefit emerges.
- Supporting seeds that resolve in SS but not in OpenAlex *at all*. The current `_arxiv_doi_to_seed_work()` chain (`OpenAlex DOI → arXiv title → OpenAlex title.search`) already covers arXiv preprints that *do* have OpenAlex Work IDs (typical case). Seeds where OpenAlex has no Work ID for the arXiv paper are rare and out of scope.
- A new config-schema entry. One bool kwarg on `snowball_expand` is enough until evidence shows a need for a project-wide default.

## Architecture

### Detection: `_seed_needs_ss_fallback`

```python
def _seed_needs_ss_fallback(seed_doi: str, seed_work: dict | None) -> bool:
    """True if this seed's citations are likely underreported by OpenAlex.

    Triggered when either:
      - the seed DOI is an arXiv DOI (10.48550/arxiv.*), OR
      - the seed_work resolved in OpenAlex but has no DOI of its own
        (rare; means OpenAlex stored the work via title.search but
         couldn't link it to a CrossRef record).
    """
```

Called once per seed at the top of the per-seed branch inside `snowball_expand`. The check is cheap (string compare + dict lookup), so even for batch jobs the overhead is negligible.

### Fetchers: in `src/perspicacite/search/semantic_scholar.py`

Two new functions, parallel to the existing `lookup_paper`:

```python
async def fetch_ss_references(
    paper_id: str,
    *,
    limit: int = 100,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Papers that the given paper cites (backward direction).

    Calls GET /paper/{paper_id}/references with `fields=...`. Returns
    a list of normalized dicts shaped like OpenAlex work records so
    they flow through the existing _paper_from_oa_work adapter.

    On 404 / 429 / network error: log and return [].
    """

async def fetch_ss_citations(
    paper_id: str,
    *,
    limit: int = 100,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Papers that cite the given paper (forward direction).

    Calls GET /paper/{paper_id}/citations with `fields=...`. Same
    return shape and same failure semantics.
    """
```

Each function performs at most one HTTP request. The fetcher's `limit` argument is passed through to the SS endpoint and is clamped internally to `[1, 1000]` (the SS API's maximum per page). `snowball_expand` passes its own `max_per_seed` through, so the fetcher returns at most `max_per_seed` records — symmetric with the OpenAlex forward/backward pagination cap. Pagination beyond the first page is deliberately omitted in v1; if a user later asks for the full 7,000-hit RAG cite-graph, we'll add cursor handling then.

The fetchers accept the same `paper_id` shape that `normalize_paper_id()` already produces (`DOI:...`, `ArXiv:...`, etc.). They reuse the existing `_get_api_key()` resolver, so `SEMANTIC_SCHOLAR_API_KEY` / `SCILEX_SEMANTIC_SCHOLAR_API_KEY` / config.yml all work unchanged.

### Adapter: SS paper-record → OpenAlex-like dict

S2's `/references` and `/citations` return records of shape:

```python
{
  "contextsWithIntent": [...],
  "isInfluential": bool,
  "citedPaper": {        # for /references; for /citations it's "citingPaper"
    "paperId": "...",
    "corpusId": ...,
    "externalIds": {"DOI": "...", "ArXiv": "..."},
    "title": "...",
    "abstract": "...",
    "authors": [{"name": "..."}],
    "year": int,
    "citationCount": int,
    "venue": "...",
  }
}
```

A small adapter `_ss_record_to_oa_like_work(record)` converts this to the dict shape `_paper_from_oa_work()` expects, so downstream `ExpansionHit` construction is uniform.

### Merge: dedup with provenance

`snowball_expand` builds a per-seed dedup key:
1. `doi` (normalized lowercase) if present
2. else `arxiv_id` (from `externalIds.ArXiv` or `metadata.arxiv_id`)
3. else `title_norm` — lowercased, whitespace-collapsed, first 120 chars

For each seed, after the OpenAlex pass collects hits, the SS pass collects more hits. If a key from SS already exists in the OpenAlex hits, the entry's `provenance` flips from `"openalex"` to `"both"`. New SS-only hits are appended with `provenance="semantic_scholar"`.

### `ExpansionHit.provenance`

```python
@dataclass
class ExpansionHit:
    seed_doi: str
    expanded_doi: str
    direction: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    journal: str | None = None
    citation_count: int | None = None
    provenance: str = "openalex"  # "openalex" | "semantic_scholar" | "both"
```

Existing call-sites that don't pass `provenance=` get the default. The Paper records built by `_papers_from_hits()` get `source=PaperSource.SEMANTIC_SCHOLAR` when `provenance` is `"semantic_scholar"` or `"both"`, `PaperSource.CITATION_FOLLOW` otherwise (preserves the current convention for OpenAlex-derived hits — the citation-walk semantics are still what matters).

Actually, the cleaner rule: any cite-graph–sourced Paper is `CITATION_FOLLOW` regardless of which provider supplied it. The `provenance` field on `ExpansionHit` tells you about the *graph edge*; `source` on `Paper` tells you about the *paper record*. These are different concepts. The plan will go with `CITATION_FOLLOW` for all snowball-derived Papers and keep provenance for the edge-level signal.

### `snowball_expand` flow

```
for each seed_doi:
    work = await _fetch_seed_work(...)        # unchanged
    if work is None: continue
    
    if direction in {backward, both}:
        # OpenAlex backward pass — unchanged
        oa_back_hits = collect_referenced_works(work)
    
    if direction in {forward, both}:
        # OpenAlex forward pass — unchanged
        oa_fwd_hits = await _fetch_forward_citations(...)
    
    # NEW: SS fallback if seed needs it AND flag enabled
    if include_semantic_scholar and _seed_needs_ss_fallback(seed_doi, work):
        ss_id = _ss_id_for_seed(seed_doi, work)   # arXiv:1234.5678 form
        if direction in {backward, both}:
            ss_back = await fetch_ss_references(ss_id, ...)
            merge_with_dedup(oa_back_hits, ss_back, key="seed_doi+expanded_id")
        if direction in {forward, both}:
            ss_fwd = await fetch_ss_citations(ss_id, ...)
            merge_with_dedup(oa_fwd_hits, ss_fwd, key="seed_doi+expanded_id")
```

The merge function is a small helper that mutates the existing OpenAlex hits' `provenance` field when a duplicate is found, and appends novel hits.

### Failure modes

| Failure | Behavior |
|---|---|
| SS endpoint returns 404 for the paper_id | Log `snowball_ss_paper_not_found`, return [] from fetcher, snowball continues with OpenAlex-only result |
| SS endpoint returns 429 (rate limit) | Log `snowball_ss_rate_limited`, return [] from fetcher, snowball continues |
| SS returns 5xx / network error | Log `snowball_ss_error error=...`, return [] from fetcher, snowball continues |
| SS API key absent | Use unauthenticated tier (~100 req/5min); same headers logic as existing `lookup_paper` |
| Caller sets `include_semantic_scholar=False` | Skip the fallback entirely; identical to today's behavior |
| Seed DOI is arXiv but `_arxiv_doi_to_seed_work` already failed | `seed_work` is None → skip the seed entirely (existing behavior); the SS branch is never reached |

Graceful degradation is the rule: SS issues never break the snowball; they only fail to enrich it.

## API

### Public surface

```python
async def snowball_expand(
    *,
    seed_dois: list[str],
    direction: str = "both",
    max_per_seed: int = 10,
    http_client: httpx.AsyncClient | None = None,
    mailto: str | None = None,
    include_semantic_scholar: bool = True,   # NEW
) -> list[ExpansionHit]:
```

All existing call-sites work unchanged (new kwarg has a default). Tests and rate-limit-sensitive batches can pass `include_semantic_scholar=False`.

### Internal additions

- `src/perspicacite/search/semantic_scholar.py`: `fetch_ss_references`, `fetch_ss_citations`, `_ss_record_to_oa_like_work` (private)
- `src/perspicacite/pipeline/snowball.py`: `_seed_needs_ss_fallback`, `_ss_id_for_seed`, `_merge_with_dedup` (private), `provenance` field on `ExpansionHit`

## Testing strategy

### Unit (offline, monkeypatch httpx)

1. `test_ss_fetch_references_returns_oa_like_dicts` — happy path; assert the adapter mapping
2. `test_ss_fetch_citations_returns_oa_like_dicts` — happy path
3. `test_ss_fetch_handles_404` — returns [], doesn't raise
4. `test_ss_fetch_handles_429` — returns [], logs the rate limit
5. `test_ss_fetch_handles_network_error` — returns [], logs the error
6. `test_seed_needs_ss_fallback_arxiv_doi` — `10.48550/arXiv.X` → True
7. `test_seed_needs_ss_fallback_arxiv_doi_lowercase` — `10.48550/arxiv.X` → True (case-insensitive)
8. `test_seed_needs_ss_fallback_crossref_doi` — `10.1145/foo` → False
9. `test_seed_needs_ss_fallback_missing_doi_work` — work has no DOI → True
10. `test_snowball_merges_ss_into_openalex_dedups_by_doi` — same DOI in both → one ExpansionHit, `provenance="both"`
11. `test_snowball_appends_ss_only_hits` — SS hit not in OpenAlex → appended with `provenance="semantic_scholar"`
12. `test_snowball_skips_ss_when_flag_disabled` — `include_semantic_scholar=False` → no SS HTTP calls
13. `test_snowball_skips_ss_for_crossref_seed` — non-arXiv DOI → no SS HTTP calls
14. `test_papers_from_hits_uses_citation_follow_regardless_of_provenance` — pin the source-vs-provenance distinction explicitly

### Live integration test (skipped without `SEMANTIC_SCHOLAR_API_KEY`)

1. `test_ss_forward_citations_for_rag_paper_returns_more_than_openalex` — seed RAG DOI, run `snowball_expand`, assert combined hits > pure-OpenAlex hits by at least 1 order of magnitude. (Pinning the audit finding so it can't silently regress.)

### Pin test extension

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
def test_snowball_ss_path_still_uses_citation_follow_enum():
    """Cite-graph hits — regardless of whether OpenAlex or SS sourced
    them — are CITATION_FOLLOW. provenance is the edge-source signal,
    source is the paper-record signal."""
    from perspicacite.pipeline.snowball import ExpansionHit, _papers_from_hits
    h = ExpansionHit(
        seed_doi="10.48550/arXiv.2005.11401",
        expanded_doi="10.1234/cited",
        direction="forward",
        title="A Cited Work",
        provenance="semantic_scholar",
    )
    papers = _papers_from_hits([h])
    assert papers[0].source is PaperSource.CITATION_FOLLOW
```

## Configuration

No config-schema entries in v1. The single `include_semantic_scholar` kwarg covers the only known knob. If a future requirement asks for project-wide opt-out or per-environment defaults, add `snowball.semantic_scholar_fallback: bool` to `config.yml`'s `snowball` section then.

## Estimates

- Production code: ~150 LOC (fetchers + adapter + detection + merge + provenance field)
- Tests: ~250 LOC (14 unit + 1 live + 1 pin)
- Wallclock: single-day; 6 tasks in the plan (fetchers, detection, ExpansionHit field, snowball integration, unit tests, live smoke test)

## Risks

- **SS rate limits.** Unauthenticated tier is ~100 req/5min. A snowball over 10 arXiv seeds at 100 hits per seed in both directions = 20 SS calls per snowball, plus pagination. Comfortable margin for interactive use. For bulk ingest, callers should pass `include_semantic_scholar=False` or supply an API key.
- **Schema drift in SS responses.** The adapter is the seam; if SS changes field names, the adapter breaks. Mitigated by unit tests with canned fixtures captured from the live API.
- **Citation-count semantic mismatch.** OpenAlex's `cited_by_count` and SS's `citationCount` count different things (SS includes preprint↔preprint, OpenAlex does not). When a hit appears in both with conflicting counts, the merge keeps the OpenAlex count (consistent with the rest of the codebase's OpenAlex anchoring). Documented in the merge helper's docstring.
