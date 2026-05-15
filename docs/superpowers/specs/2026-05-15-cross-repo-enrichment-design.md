# Cross-repo enrichment — GitHub-search propagation for skill-bundle KBs

**Date:** 2026-05-15
**Status:** Draft, awaiting approval
**Builds on:**
- `docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`
  (provides `GitHubFetcher`, the `bundle.yml` schema with `tools`, and
  the KB-per-bundle ingest paths)
- `docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md`
  (sub-project A's `symbols.jsonl` sidecar — optional input for the
  symbol-level discovery mode)

**Companion plan:** `docs/superpowers/plans/2026-05-15-cross-repo-enrichment.md`
(to be generated after approval)

## 1. Motivation

Today a skill bundle ingests a fixed list of repos declared in its
`bundle.yml`. That list is whatever the bundle author thought of. The
user's reality is bigger: if a bundle is about "molecular dynamics
with OpenMM," the most useful sister repos are the ones that import
OpenMM and demonstrate non-trivial workflows — and those repos are
not enumerated anywhere in advance.

**Prior-art check (neither repo does this):**

- ASB uses GitHub REST for `/repos/{name}` (metadata, README, tree,
  contents) only — see `enrichment.py:386-586`, `list_parser.py:286`.
  It has a `SciSkill.related_tools: list[ToolUsage]` field
  (`llm_schemas.py:478`), but those entries are **LLM-extracted from
  each paper's text**, not discovered by GitHub search.
- Perspicacité's `pipeline/external/fetch_github.py` and today's
  GitHub-KB / skill-bundle ingest plan likewise call only the
  `/repos/{name}` family. No `/search/repositories` or `/search/code`
  call exists in either codebase.

So this spec introduces a genuinely new capability: **fan out from a
skill bundle's declared tools (and optionally its symbol index) to
GitHub-search for sister repos, filter, rank, then queue the matches
through the existing GitHub-KB ingest pipeline**.

## 2. Scope

This spec covers one capability: *cross-repo discovery + queued
ingest*. It is a strictly additive layer over the GitHub-KB ingest
infrastructure already specced. No changes to retrieval, no changes
to the KB schema beyond a single new metadata field.

**In scope:**

- A new entry point `ingest_skill_bundle(..., enrich_via_github=True)`
  and a standalone `enrich_kb_from_github(kb_name, ...)` command for
  enriching an existing skill-bundle KB.
- Two discovery modes (library and symbol; see §4).
- A scoring + filtering pipeline (stars, recency, license, topic,
  language).
- A propagation cap and a dry-run mode so the user sees what would be
  ingested before committing.
- A `source_via` provenance field on chunks so the user can tell
  bundle-declared chunks apart from enrichment-discovered ones.

**Out of scope:**

- No GitLab / Bitbucket / Codeberg support in v1. GitHub only. The
  fetcher protocol is left generic enough for a future PR.
- No automatic LLM-based ranking of candidate repos. Ranking is
  signal-based (stars, recency, README-relevance). An LLM rerank pass
  is straightforward to add later but not in v1.
- No graph build-out ("repos that depend on this repo's repo"). Just
  one hop from the bundle.
- No live web crawling. GitHub-search API only.
- No re-running enrichment when a bundle changes. The user
  re-triggers it manually (or via cron in their own infra).

## 3. Discovery flow at a glance

```
bundle.yml ──┬─► tools: [openmm, mdtraj]    ──► library_search()
             │                                       │
             │                                       ▼
             │                              candidate_repos: list[RepoHit]
             │
             └─► (optional) symbols.jsonl   ──► symbol_search()
                 (sub-project A output)             │
                                                    ▼
                                          candidate_repos: list[RepoHit]
                                                    │
                          dedup + filter + score    │
                                                    ▼
                                              top_N RepoHits
                                                    │
                          dry-run preview / confirm │
                                                    ▼
                              queue each through GitHubFetcher
                                                    │
                                                    ▼
                                ingest into the bundle's KB
                                (chunks tagged source_via="enrichment")
```

## 4. Discovery modes

### 4.1 Library mode (default)

For each tool in `bundle.tools`, build queries against the GitHub
Search API and dedup the hits.

**Repository search** (`GET /search/repositories`):

```
q = "{tool}" + " topic:{tool}" + " language:python" + " stars:>={min_stars}"
sort = stars
order = desc
per_page = 30
```

Run once per tool. Topic queries are stronger than name-only matches
because they require the repo author to have opted in via the
`topics` metadata.

**Code search** (`GET /search/code`):

```
q = "import {tool}" + " language:python" + " extension:py" + " path:/"
```

Only runs when authenticated (`GITHUB_TOKEN` set). Code search
requires auth and is rate-limited tighter (30 req/min). Skipped with
a structured warning when unauth'd.

The two query families are unioned, deduplicated by `full_name`, and
passed to the filter/scorer.

### 4.2 Symbol mode (opt-in)

When the bundle's KB has a `symbols.jsonl` sidecar (sub-project A
output) **and** the user passes `--enrich-symbols`, the system picks
the top-K most "distinctive" symbols and runs code search for them.

Distinctiveness heuristic: a symbol is more distinctive when its name
is longer, lowercase-rare-letter-frequency higher, and it appears in
fewer files within the source KB (TF-IDF over symbol names). Keeps
us from running searches on generic names like `run` or `main`.

```
q = "{symbol_name}" + " language:python" + " extension:py"
```

Cap defaults: K = 10 symbols, 30 hits per symbol. Total budget bounded
by §5.

### 4.3 Why both modes

Library mode finds *any* repo that uses the bundle's stack — broad.
Symbol mode finds repos that use specific functions / classes —
narrow, useful when the bundle author has written a niche library
and wants to find consumers. Both are off-by-default opt-ins for
`enrich_via_github=True`; the user picks via a `mode={library,symbol,both}`
arg.

## 5. Rate limits, auth, budget

GitHub Search limits:

| Endpoint | Unauth'd | With token |
|---|---|---|
| `/search/repositories` | 10 req/min | 30 req/min |
| `/search/code` | not allowed | 30 req/min |
| `/repos/{name}` | 60 req/hr | 5000 req/hr |

Plumbing:

- Reuse the existing `GITHUB_TOKEN` env var convention from the
  GitHub-KB spec. When unset, library-mode repo search runs at the
  unauth'd 10-req/min cap; code search is skipped with a structured
  warning.
- A new config block:
  ```yaml
  enrichment:
    max_repos_per_tool: 10
    max_total_repos: 50
    min_stars: 20
    max_age_months: 36     # last-push within this window
    require_license: true  # skip repos with no license
    languages: ["Python"]  # restrict by language
  ```
- A `--dry-run` flag prints the ranked candidates with score
  breakdown and exits without ingesting.

## 6. Scoring + filtering

Each `RepoHit` carries the raw GitHub fields. We compute a single
`score: float` and rank by it. Components:

```
score = (
    w_stars   * normalize_stars(stars)             # log-scaled
  + w_recency * recency_score(pushed_at)           # 0..1, half-life 18 mo
  + w_match   * keyword_match(readme, tools)       # 0..1, BM25-ish
  + w_topic   * (1.0 if any(tool in topics) else 0.0)
  + w_license * (1.0 if has_oss_license else 0.0)
)
```

Default weights: `(0.30, 0.20, 0.25, 0.15, 0.10)`. Tuneable from
config. The keyword-match component reuses the BM25 tokenizer we
already have in `retrieval/bm25.py`.

Filters (applied before scoring; cheap rejections):

- `stars < min_stars` → drop
- `pushed_at > max_age_months ago` → drop
- `license is null` and `require_license` → drop
- `language not in languages` → drop
- `full_name` already in this KB → drop (no duplicate ingest)
- `full_name` in a denylist (`enrichment.denylist: list[str]`) → drop

## 7. Data model

### 7.1 New chunk metadata field

```python
# ChunkMetadata (already extended in code+multimodal spec)
source_via: Literal["bundle", "enrichment"] = "bundle"
discovery_query: Optional[str] = None  # only when source_via=="enrichment"
discovery_score: Optional[float] = None
```

This lets `--code` excerpts (from sub-project C) render with a small
"discovered via enrichment" badge and the originating query, so the
user can see *why* a particular repo was pulled in.

### 7.2 RepoHit record

```python
@dataclass
class RepoHit:
    full_name: str         # owner/repo
    description: str
    stars: int
    pushed_at: datetime
    license_spdx: str | None
    language: str | None
    topics: list[str]
    html_url: str
    default_branch: str
    matched_via: Literal["repo_search", "code_search", "symbol_search"]
    matched_query: str     # the actual query string
    score: float
    score_breakdown: dict[str, float]
```

Persisted as JSONL under `<kb-dir>/enrichment_runs/<timestamp>.jsonl`
for audit (parallel to `kb_log.jsonl`).

## 8. File changes

```
src/perspicacite/pipeline/external/github_search.py    # NEW
   - GitHubSearchClient: /search/repositories and /search/code
   - rate-limit aware (X-RateLimit-* headers), pluggable backoff
src/perspicacite/pipeline/enrichment.py                 # NEW
   - discovery: library_search, symbol_search
   - scoring: score_hit, normalize_stars, recency_score, keyword_match
   - filtering: apply_filters
   - orchestration: enrich_kb_from_github(kb_name, *, mode, dry_run, ...)
src/perspicacite/pipeline/github_skill_bundle.py        # MODIFY
   - ingest_skill_bundle gains `enrich_via_github`, `enrichment_mode`
     params that call into the new orchestrator after the base ingest
src/perspicacite/cli/main.py                            # MODIFY
   - `perspicacite kb enrich <kb-name> [--mode ...] [--dry-run] [--enrich-symbols]`
src/perspicacite/mcp/server.py                          # MODIFY
   - new MCP tool: enrich_kb_from_github(kb_name, mode, dry_run)
src/perspicacite/config/schema.py                       # MODIFY
   - new `EnrichmentConfig`; nested under `KnowledgeBaseConfig.enrichment`
src/perspicacite/models/documents.py                    # MODIFY
   - ChunkMetadata.source_via / discovery_query / discovery_score
tests/unit/test_github_search_client.py                 # NEW
tests/unit/test_enrichment_scoring.py                   # NEW
tests/unit/test_enrichment_dry_run.py                   # NEW
tests/integration/test_enrichment_e2e.py                # NEW (live, opt-in)
docs/recipe-book-2026-05-15.md                          # MODIFY (one new recipe)
```

## 9. Tests

Unit (mocked):

- `test_github_search_client.py`:
  - Builds the correct `q=` strings for repo and code search.
  - Honours `X-RateLimit-Remaining` / `Retry-After` (waits or fails
    fast based on config).
  - Code search is skipped with `enrichment.skipped_code_search`
    structured warning when no token.
  - Pagination across `per_page` boundaries.
- `test_enrichment_scoring.py`:
  - `normalize_stars(0)==0`, `normalize_stars(10_000)≤1.0`,
    monotonic.
  - `recency_score` half-life is 18 months (test exact boundary).
  - `keyword_match` returns 0.0 on an empty README, > 0.5 on a README
    that mentions every tool.
  - Final score is weighted sum; default weights sum to 1.0.
- `test_enrichment_dry_run.py`:
  - `enrich_kb_from_github(kb, mode="library", dry_run=True)` returns
    ranked `RepoHit`s without touching the KB or Chroma.
  - Denylist filters apply.
  - `max_total_repos` caps the output.
  - Dedup across library + symbol modes works (same repo found by
    both → one hit, `matched_via` reflects both).

Integration (live, opt-in via `PERSPICACITE_LIVE_ENRICHMENT=1`):

- `test_enrichment_e2e.py`: ingest a tiny synthetic bundle (`openff-evaluator`
  as the single tool), run enrichment with cap=3, confirm at least
  one real repo (e.g. `openforcefield/openff-evaluator-examples`)
  shows up in the dry-run listing.

## 10. Backward compatibility

- `ingest_skill_bundle` defaults `enrich_via_github=False`. Existing
  bundles ingest identically to today.
- `ChunkMetadata.source_via` defaults to `"bundle"`. Existing chunks
  load unchanged.
- The CLI `kb enrich` subcommand is additive.
- `EnrichmentConfig` defaults are conservative: 10 repos per tool,
  50 total, stars ≥ 20, 36-month freshness window — a typical
  enrichment of a 2-tool bundle ingests at most 50 small repos.

## 11. Risks

- **Quality drift.** Enrichment can pull in repos that look related
  but aren't useful. Mitigations: the scoring filter, the dry-run
  preview, the `source_via` tag (so the user can compare `bundle`
  vs `enrichment` answer quality), and a future opt-in LLM rerank
  pass.
- **API exhaustion.** Burning the 30-req/min code-search quota.
  Mitigations: per-run budget cap, structured retry with `Retry-After`
  honouring, default-off symbol mode.
- **License contamination.** A code-search hit may be in a
  proprietary or copyleft-dangerous repo. Mitigations:
  `require_license: true` default; SPDX allowlist (`enrichment.license_allowlist`)
  defaults to MIT / BSD / Apache-2.0 / MPL-2.0 / GPL-3.0 / LGPL-3.0;
  unknown licenses skipped.
- **Surprise data volumes.** A bundle naming `numpy` would match
  everything. Mitigations: the per-tool cap + total cap; the
  `min_stars` floor; and a documented "don't enrich on ubiquitous
  libraries" guidance in the recipe-book entry.

## 12. Success criteria

- Running `perspicacite kb enrich <mybundle> --mode library --dry-run`
  on the `openff-evaluator` bundle (which is in the GitHub-KB spec's
  example) prints a ranked list of ≥ 5 candidate repos with non-zero
  scores and visible breakdowns.
- Running the same command without `--dry-run` ingests those repos
  into the same KB, with chunks tagged `source_via="enrichment"`
  and `discovery_query` populated.
- Running a RAG query that should land on an enrichment-sourced
  chunk surfaces that chunk in the sources, and (with sub-project C
  shipped) the code-excerpt panel shows the "discovered via
  enrichment" badge with the originating query.
- All unit tests pass; live integration test passes when
  `GITHUB_TOKEN` and `PERSPICACITE_LIVE_ENRICHMENT=1` are set.

## 13. Decomposition

One implementation plan, four task groups:

1. `GitHubSearchClient` + unit tests (rate-limit handling, query
   builders, both endpoints)
2. Scoring / filtering pipeline + unit tests
3. `enrich_kb_from_github` orchestrator + `ChunkMetadata.source_via`
   plumbing + dry-run + audit JSONL
4. CLI command + MCP tool + recipe-book entry + live integration test

Sequencing: 1 → 2 → 3 → 4. Estimated 2.5–3 days.
