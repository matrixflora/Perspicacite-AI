# Session backlog — 2026-05-16

Items the autonomous loop deferred because they each require an explicit design decision
or substantial brainstorming before implementation can begin safely.

---

## P1 — Needs a design decision before implementation

### `cache_max_mb` eviction logic

**What:** The github-skill-bundle-ingest spec (`docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`)
specifies `github.cache_max_mb` (default 2 GB) to cap the tarball/clone cache at
`data/github_cache/`. The eviction strategy is deferred — the spec says "LRU eviction
when total size exceeds `cache_max_mb`" but does not define LRU vs LFU, how to
implement it (process-local, cron, pre-call check), or what the cleanup granularity is
(per-commit-SHA directory).

**Decision needed:**
1. LRU (evict oldest last-accessed) vs LFU (evict least-fetched) — LRU is simpler and
   the natural fit for a SHA-keyed tarball cache where each SHA is either needed or not.
2. When to trigger eviction: on every `ingest_github_repo` call (pre-call check with a
   size scan), or as a background cron. Pre-call is simpler; cron avoids latency spikes
   on large caches.
3. Granularity: evict whole `<sha>/` directories (correct — tarballs are single large files).

**Suggested direction (decide before writing the plan):**
- LRU via mtime of the directory (set on last access).
- Pre-call check: scan total size; if over limit, sort by mtime ascending, delete oldest
  SHA dirs until under 80% of the limit.
- Add a `perspicacite cache-cleanup` CLI command for manual triggering.

---

## P2 — Smaller / less-blocking, but each needs a decision

### Pathspec deprecation switch

The `include`/`exclude` glob syntax in `bundle.yml` (github-skill-bundle-ingest spec,
Section "Input formats") uses a simple glob pattern. The parent spec notes a follow-up:
when the bundle manifest ships a `content.pathspec_version: 2` field, the parser should
switch to gitignore-style pattern matching (negation, `**` semantics). The switch needs:
- A `pathspec_version` field on `BundleManifest` with a default of `1`.
- A flag in the walker to use `pathspec` (PyPI) instead of `fnmatch` when version >= 2.
- A deprecation warning logged when version is absent (for forward-compat).

### `ChunkMetadata.paper_metadata_json` size audit

Skill-bundle chunks (from the ASB ingest spec) carry a rich per-chunk metadata dict
including `tools`, `environment`, `parameters`, `evidence_spans[]`, etc. These fields
go into Chroma's metadata store, which has an undocumented per-document size limit
(approximately 64 KB in practice). The `evidence_spans[]` field from `tools.json` can
be large. Audit needed:
- Measure the actual metadata dict size on the MetLinkR fixture
  (`~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/`).
- If any chunk exceeds 50 KB of metadata, truncate `evidence_spans` to the first N items
  and add a `metadata_truncated: true` flag.
- Add an assertion in the skill-bundle ingester that metadata stays under 60 KB.

### Resolver-output dataclass

Multiple ingest paths (`ingest_dois_into_kb`, the new `zotero_ingest_collection_to_kb`,
the skill-bundle ingest) return a summary object with slightly different shapes. The
github-skill-bundle-ingest spec defines `IngestSummary` as a dataclass; the Zotero
ingest returns a job dict. Before more ingest paths are added, define a canonical
`IngestResult` union or protocol so callers can handle any ingest result uniformly.
Spec the protocol before the next batch of ingest tools.

### OpenAlex URL-encoding hardening

`src/perspicacite/search/openalex.py` builds filter URLs by string interpolation
(e.g., `?filter=doi:{doi}`). DOIs with special characters (slashes in suffixes like
`10.1126/science.abcd1234`, parentheses in older ACS DOIs) can produce malformed
filter strings that silently return zero results instead of raising. Fix: use
`urllib.parse.quote(doi, safe='')` for the DOI component, add a unit test with
adversarial DOIs, and add a regression test that the MetLinkR DOI
`10.1021/acs.jproteome.4c01051` round-trips correctly.

---

## P3 — Larger, needs brainstorm + spec

### Notebook execution capture

The github-skill-bundle-ingest spec explicitly defers: "Auto-running notebooks to
capture cell outputs." When a skill bundle contains `.ipynb` files, running them via
`nbexec` and storing the cell outputs alongside the source would enable RAG queries
like "what was the output of the QC step for this dataset?" The capsule infrastructure
(for figure storage) is the natural home for output storage. Needs a full brainstorm on:
safety (sandboxing, timeout, resource limits), what to store (output images → PNG +
caption; stdout → text chunk; stderr → discard or flag), and how to surface outputs
in retrieval.

### Code-symbol indexing

The github-skill-bundle-ingest spec defers symbol-aware retrieval: "Full code-symbol
indexing (a CTags-style index of every function / class)." Today code is chunked as
text; a tree-sitter-based index would enable "find me all functions named `*qc*`" or
"which skills define a function with this signature." Needs brainstorm on:
index format (symbols.jsonl per repo, or Chroma metadata), query surface (new MCP
tool vs SearchFilters extension), and how to handle multi-language repos (Python,
R, Julia are the main targets).

### GitHub Enterprise / GitLab adapters

The github-skill-bundle-ingest spec defers both. The `GitHubFetcher` abstraction is
the right template. Brainstorm needed on: auth differences (GHE uses the same API
surface but with a custom base URL; GitLab uses a different API entirely), how to
express the source in `PaperSource` / `ChunkMetadata.commit_sha`, and whether
to add a `VCS_HOST` enum or use URL-sniffing to dispatch.

### Watch mode

The spec defers "Watch mode: re-ingest on push." The clean implementation uses
GitHub webhooks → Perspicacité webhook endpoint → trigger `ingest_github_repo` with
`force_reingest=True` for changed files only. Needs brainstorm on: webhook delivery
reliability (queue vs direct), partial reingest (only changed files vs full repo),
and how to expose this to operators without adding a persistent daemon.

### ASB capsules + scenarios + graph-RAG

Three related P3 items from earlier sessions that still need brainstorming:
- **ASB capsules alignment:** ASB produces its own `capsules/` and `cards/` per-skill
  artifacts. Perspicacité has its own capsule format. v2 alignment (shared capsule
  store, compatible schema) needs a joint design session with the ASB team.
- **ASB scenarios:** ASB generates example scenarios. These are structured differently
  from `examples.jsonl` (which is already ingested). Scenarios may need their own
  content type and retrieval surface.
- **Graph-RAG:** The cite-graph enrichment spec (`docs/superpowers/specs/2026-05-15-cite-graph-enrichment-design.md`)
  ships citation edges into the KB metadata. Using those edges for graph-aware retrieval
  (PageRank-weighted reranking, multi-hop expansion) is a significant RAG upgrade that
  needs its own brainstorm + spec cycle.
