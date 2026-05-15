# Cite-graph enrichment — find citing articles for a library

**Date:** 2026-05-15
**Status:** Draft, awaiting approval
**Builds on:**
- `src/perspicacite/pipeline/snowball.py` — already supports forward
  citation traversal via OpenAlex `cited_by_api_url` (line 197+).
  We reuse this directly; no new traversal infrastructure.
- `docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`
  — provides `bundle.yml` parsing (the `tools` field) and the
  GitHub-KB ingest pipeline we reuse for optional code pull-in.
- `docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md`
  sub-project A — provides AST-aware code chunking when we do
  pull in scripts from citing papers.

**Companion plan:** `docs/superpowers/plans/2026-05-15-cite-graph-enrichment.md`
(to be generated after approval)

**Supersedes:** the earlier `cross-repo-enrichment` draft on this
file's git history. The library→repo discovery angle from that draft
moves upstream to ASB (see
`docs/upstream-prompts/2026-05-15-asb-library-repo-discovery.md`).

## 1. Motivation and scope boundary with ASB

The natural division of labor:

- **ASB (upstream)** curates skills. When ASB enriches a skill, it can
  GitHub-search for sister repos using the library/symbol names —
  search returns metadata + snippet, **no clone needed during the
  discovery phase**, so it is cheap to run as part of long-term skill
  maintenance. The existing ASB capsule builder already handles the
  download when a capsule is later built.
- **Perspicacité (downstream)** consumes skill bundles to build KBs.
  Its native angle on a tool / library is the **literature** side:
  *which articles cite this library?* That's a pure citation-graph
  query — same shape as the existing snowball forward traversal —
  and the resulting papers feed straight into the existing Zotero /
  DOI ingest pipeline.

This spec covers only the downstream side. The upstream side gets a
separate brief in `docs/upstream-prompts/` so it can be handed to
ASB owners verbatim.

## 2. What this builds

Given a tool name (from `bundle.tools`, the CLI, or an MCP tool), or
a DOI:

1. Resolve **library → canonical-paper DOI**. Three sources, tried in
   order:
   1. A curated config map (`KnowledgeBaseConfig.library_paper_map`).
   2. A `bundle.yml` field `tools[].paper_doi` if present.
   3. README-extraction (regex on "If you use … please cite …" /
      "Citation:" / `CITATION.cff` fields) when the bundle references
      a GitHub repo — uses the existing `fetch_github` reader.
4. Translate the DOI → OpenAlex work id via the existing snowball
   helpers.
5. Page through OpenAlex `cited_by_api_url` to collect citing works
   (DOI + abstract + year + venue + citation count + OA flag).
6. Filter and score the citing works (same shape as existing screening
   in `search/screening.py`).
7. Ingest the survivors as papers in the same KB, tagged with
   `source_via="cite_graph"` and `cited_tool=<tool>` in chunk
   metadata.
8. **Optionally** (off by default): for each citing paper that has a
   GitHub repo link in its full text / OpenAlex metadata, fetch a
   small set of scripts (≤ 3 files, by relevance to the tool's symbol
   set) and ingest them too via the code-aware chunker from
   sub-project A.

The optional code-fetch is "minimal ASB" in spirit — it's not skill
curation, just enough script context to ground "this paper used the
library to do X" in actual code.

## 3. Non-goals

- **No GitHub repo discovery for a library.** That moves to ASB.
  Perspicacité never calls `/search/repositories` or `/search/code`
  for library-name fan-out.
- **No backward citations.** The snowball backward pass exists but is
  out of scope here — we are looking at "who uses this library after
  publication," which is the forward direction.
- **No transitive citation walks.** One hop from the library's
  canonical paper. Multi-hop is a separate spec.
- **No live web crawling for citation lists.** OpenAlex only. (We
  already have it.)

## 4. Where this differs from snowball today

Today's `snowball.py` takes a *seed paper DOI* and grows a KB by
walking citations one or two hops. The cite-graph entry takes a
*library/tool name* and resolves it to a seed paper first. After
resolution the traversal is the same forward pass we already have.

```
library name        ┐
or skill bundle    ─┴─►  library → DOI resolver  ──►  snowball forward
or explicit DOI     ┘            (new)                  (existing)
```

## 5. Data flow

```
input: tool="openff-evaluator"          (or bundle.tools, or --doi)
        │
        ▼
resolve_library_paper(tool)              # NEW
        │
        ▼
canonical DOI: 10.1021/acs.jctc.8b00640
        │
        ▼
openalex_id_for_doi(doi)                 # existing in snowball
        │
        ▼
fetch_cited_by_works(openalex_id)        # existing in snowball (cited_by_api_url)
        │
        ▼
list[CiteHit]   (DOI, title, year, venue, citation_count, oa_url, github_url?)
        │
        ▼
filter + score + cap                     # NEW (small)
        │
        ▼
ingest_papers_into_kb(kb, hits)          # existing (DOI ingest path)
        │
        ▼ (optional, --include-scripts)
for each hit with a github_url:
    fetch ≤3 most-relevant scripts       # NEW thin wrapper around
                                         # the GitHub-KB ingest path
    code-aware chunk + ingest into KB    # existing (sub-project A)
```

## 6. Library → DOI resolver

`src/perspicacite/pipeline/library_doi.py` (new):

```python
@dataclass
class LibraryPaper:
    library: str           # canonical name, e.g. "openff-evaluator"
    doi: str               # 10.xxxx/...
    title: str
    source: Literal["config", "bundle", "readme"]
    confidence: float      # 1.0 for config/bundle, 0.5-0.8 for README

async def resolve_library_paper(
    library: str,
    *,
    bundle: dict | None = None,
    github_repo: str | None = None,
    config_map: dict[str, str] | None = None,
    fetcher: GitHubFetcher | None = None,
) -> LibraryPaper | None:
    """Try config map → bundle.yml → README extraction.
    Returns None when no DOI can be grounded."""
```

README extraction uses three patterns (case-insensitive):

```python
PATTERNS = [
    r"If you use\s+\S+\s+(?:in your|please).{0,200}?(10\.\d{4,9}/[\w./()\-:]+)",
    r"^\s*Citation\s*[:=]\s*.{0,200}?(10\.\d{4,9}/[\w./()\-:]+)",
    r"^doi\s*:\s*(10\.\d{4,9}/[\w./()\-:]+)",  # CITATION.cff
]
```

When `CITATION.cff` is present (standard GitHub citation file), it
overrides README scraping — parsed as YAML.

## 7. Filtering and scoring citing works

Lightweight; the goal is to keep junk out without an LLM call.

```python
@dataclass
class CiteHit:
    doi: str
    title: str
    year: int
    venue: str | None
    citation_count: int
    is_oa: bool
    abstract: str | None       # OpenAlex inverted-index → reconstructed
    github_url: str | None     # mined from OpenAlex `concepts`/`ids`/full_text
    score: float
    score_breakdown: dict[str, float]
```

Filters (cheap rejects):

- `year < min_year` → drop (default `min_year = current_year - 7`)
- `citation_count < min_citations` → drop (default 1, configurable)
- `venue` in `denylist` → drop (e.g. predatory journals — empty by
  default)
- DOI already present in the KB → drop

Score components:

```
score =
    w_citations * normalize_citations(citation_count)     # log-scaled
  + w_recency   * recency_score(year)                     # 0..1
  + w_oa        * (1.0 if is_oa else 0.5)                 # OA = better
  + w_match     * keyword_match(abstract, tool_synonyms)  # BM25-ish
```

Default weights: `(0.30, 0.20, 0.20, 0.30)`. `tool_synonyms` is
either a `bundle.tools[].synonyms` list or just `[tool]`.

## 8. Optional script pull-in (off by default)

When `--include-scripts` is passed (or `cite_graph.include_scripts: true`):

1. For each surviving `CiteHit`, look at OpenAlex `ids` /
   `primary_location.source.host_organization_name` / full-text-link
   fields for a `github.com/<owner>/<repo>` URL. The OpenAlex `ids`
   field exposes the paper's GitHub repo for ~15-25% of papers in
   modern compsci/chem venues (per snowball's existing observations).
2. If found, hand the repo URL to the existing GitHub-KB ingest path
   (`pipeline/github_skill_bundle.py`), restricted to **3 files**
   chosen by:
   - Heuristic relevance: file name / path contains the tool name or
     any of its synonyms → top.
   - Otherwise: largest Python file in `examples/` or `notebooks/` →
     fallback.
3. The chunks ingested are tagged `source_via="cite_graph_script"`
   and `cited_tool=<tool>` so they're clearly distinguishable from
   bundle-declared code chunks.

This is the "minimal ASB" piece: not skill curation, just enough code
to ground the paper-side answer in a real example.

## 9. New chunk metadata fields

```python
# Already extended in code+multimodal spec:
# source_via: Literal["bundle", "enrichment"] = "bundle"

# Widened here to:
source_via: Literal["bundle", "enrichment", "cite_graph", "cite_graph_script"] = "bundle"
cited_tool: Optional[str] = None
discovery_score: Optional[float] = None
```

All nullable / default. Old chunks load unchanged.

## 10. CLI and MCP entry points

```
perspicacite kb enrich-cite-graph <kb> \
    [--tool openff-evaluator]            # or take from bundle.tools
    [--doi 10.1021/acs.jctc.8b00640]     # bypass the resolver
    [--min-year 2018]                    # filter
    [--max-papers 50]                    # cap
    [--include-scripts]                  # also pull ≤3 scripts per paper
    [--dry-run]                          # preview without ingesting
```

MCP tool: `enrich_kb_from_cite_graph(kb_name, tool=None, doi=None, max_papers=50, include_scripts=False, dry_run=False)`.

## 11. File changes

```
src/perspicacite/pipeline/library_doi.py        # NEW — resolver
src/perspicacite/pipeline/cite_graph.py         # NEW — orchestrator
   - resolve → snowball-forward → filter+score → ingest
   - audit JSONL at <kb-dir>/cite_graph_runs/<ts>.jsonl
src/perspicacite/pipeline/snowball.py           # MODIFY (small)
   - extract two helpers as public:
     openalex_id_for_doi(doi) -> str | None
     fetch_cited_by_works(openalex_id, *, max_results, http) -> list[dict]
src/perspicacite/pipeline/github_skill_bundle.py # MODIFY
   - new param `restrict_to_files: list[str] | None` so the
     script-pull-in path can ingest only the chosen 3 files
src/perspicacite/cli.py                          # MODIFY — kb enrich-cite-graph
src/perspicacite/mcp/server.py                   # MODIFY — new MCP tool
src/perspicacite/config/schema.py                # MODIFY
   - KnowledgeBaseConfig.library_paper_map: dict[str, str]
   - KnowledgeBaseConfig.cite_graph: CiteGraphConfig
src/perspicacite/models/documents.py             # MODIFY
   - ChunkMetadata.source_via widened, cited_tool, discovery_score
tests/unit/test_library_doi_resolver.py          # NEW
tests/unit/test_cite_graph_scoring.py            # NEW
tests/unit/test_cite_graph_dry_run.py            # NEW
tests/integration/test_cite_graph_e2e.py         # NEW (live, opt-in)
docs/recipe-book-2026-05-15.md                   # MODIFY — one new recipe
```

## 12. Tests

Unit (mocked):

- `test_library_doi_resolver.py`:
  - Config map hit → returns `LibraryPaper(source="config", confidence=1.0)`.
  - Bundle `tools[].paper_doi` hit → returns `LibraryPaper(source="bundle")`.
  - README contains "Please cite [DOI]" → returns `LibraryPaper(source="readme")`.
  - `CITATION.cff` present and parseable → overrides README.
  - Nothing resolvable → returns `None`, structured warning logged.
- `test_cite_graph_scoring.py`:
  - `normalize_citations(0)==0`, monotonic, log-scaled, `≤1.0`.
  - `recency_score` matches the implementation conventions
    (half-life parameter exposed; default 5 yr).
  - Default weights sum to 1.0.
  - `keyword_match` falls back to 0.0 when abstract is missing.
- `test_cite_graph_dry_run.py`:
  - End-to-end with mocked OpenAlex client: resolver →
    `fetch_cited_by_works` returns 20 stubs → filter drops the
    < min_year ones → top 5 returned with breakdowns.
  - `--dry-run` does not call the ingest pipeline.

Integration (live, opt-in via `PERSPICACITE_LIVE_CITE_GRAPH=1`):

- `test_cite_graph_e2e.py`: real `openff-evaluator` tool, real
  OpenAlex call, cap at 5 papers. Asserts ≥ 1 paper is returned and
  has a non-empty abstract + year ≥ 2017.

## 13. Backward compatibility

- `source_via` defaults to `"bundle"`; widened literal keeps existing
  chunks valid (Pydantic Literal validation).
- Snowball helpers are made public (extracted, not changed). Existing
  snowball callers unaffected.
- New CLI subcommand and MCP tool are additive.
- `cite_graph.include_scripts` defaults `False`.

## 14. Risks

- **Wrong canonical paper.** README extraction can pick up a
  *referenced* paper rather than the *cite-this* paper. Mitigation:
  config-map and bundle.yml take precedence; README is last; only
  used when no other source resolved; `confidence` recorded on the
  `LibraryPaper`.
- **OpenAlex volume.** A widely-used library like `numpy` has > 100k
  citing works. Mitigation: `max_papers` cap (default 50), `min_year`
  default 7 years back, abort-on-budget pattern from snowball.
- **Script-pull-in surprises.** GitHub repos in citing papers vary
  wildly in quality. Mitigation: hard cap of 3 files per paper, off
  by default, and chunks are clearly tagged `cite_graph_script` so
  RAG answers can show their lineage.

## 15. Success criteria

- `perspicacite kb enrich-cite-graph mybundle --tool openff-evaluator --dry-run`
  prints ≥ 5 ranked citing works with non-zero scores and visible
  breakdowns.
- Without `--dry-run`, the same command ingests those papers into the
  KB; chunks carry `source_via="cite_graph"` and `cited_tool="openff-evaluator"`.
- A RAG query in the form "how do people use openff-evaluator for
  free-energy calculations?" surfaces cite-graph chunks in the
  sources, distinguishable from bundle-sourced chunks by their tag.
- With `--include-scripts`, at least one citing paper that has a
  GitHub repo also has 1-3 script chunks ingested, tagged
  `cite_graph_script`.

## 16. Decomposition

One plan, four task groups, sequential:

1. **Resolver** — `library_doi.py` + config field + tests
   (~0.5 day)
2. **Snowball helper extraction** — make `openalex_id_for_doi` and
   `fetch_cited_by_works` public + tests (~0.25 day)
3. **Orchestrator + scoring + audit JSONL** — `cite_graph.py` +
   tests + `ChunkMetadata` widening (~1 day)
4. **CLI + MCP + recipe + optional script pull-in** (~1 day)

Total ~2.75 days.
