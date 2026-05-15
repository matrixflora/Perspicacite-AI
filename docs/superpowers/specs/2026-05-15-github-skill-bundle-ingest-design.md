# GitHub repos + skill-bundles as KB sources — design spec

**Goal:** Extend Perspicacité ingest to accept a **GitHub repository URL**
or an **agentic-science-builder skill-bundle directory** as input.
Produce either a *per-skill KB* (one KB per repo/bundle) or a
*composite domain KB* (one KB aggregating many related skills). Add a
**batch mode** for ingesting a directory full of bundles in one shot.

## Today's KB inputs (for reference)

The framework already supports these ingest paths:

| Source | Entry point | Notes |
|---|---|---|
| DOI list | `pipeline.search_to_kb.ingest_dois_into_kb` | per-DOI fetch + chunk + embed; emits Wave 4.3 log events |
| Search query | `pipeline.search_to_kb.search_to_kb` | SciLEx → filter → DOI ingest |
| BibTeX | `pipeline.bibtex_kb` | parses .bib, resolves DOIs, ingests |
| Zotero | `web.routers.zotero_ingest`, MCP `build_kbs_from_zotero` | per-collection KBs; includes attached PDFs |
| Local PDFs/docs | `integrations.local_docs.ingest_local_documents` | folder of files |
| Snowball | `pipeline.snowball` | citation graph expansion |
| External resources | `pipeline.external.fetch_orchestrator` | mined links from a paper |

This proposal adds **two more entrypoints**: GitHub repos and
skill-bundles. They both produce normal Perspicacité KBs — same
Chroma collections, same metadata DB, same Wave 4.3 log format —
just with different *Paper*/*Document* fixtures on the ingest side.

## Why now

The agentic-science-builder community ships *skill-bundles* —
self-contained directories that describe a focused scientific
workflow (e.g. "single-cell RNA-seq QC", "metabolomics MS1 peak
picking"). Each bundle bundles:

- A README explaining the skill in prose.
- Code snippets, example notebooks, scripts.
- Inline links to **papers** (DOIs, arXiv IDs, PMC IDs) that motivate
  the recommended approach.
- Optional links to **datasets** and **tools**.

Researchers want to ingest a bundle and immediately query "what does
this skill do?", "which papers back the methodology?", "show me the
example script for X". Today that requires three separate pipelines
(local-docs for the README, BibTeX-style for the papers, manual code
chunking for the scripts). This proposal unifies them.

## Scope of v1

In scope:

1. **GitHub-repo fetcher.** Accept a URL like
   `https://github.com/org/repo` (optionally with `@branch` or
   `@commit`); clone or download a tarball; walk to extract chunks.
2. **Skill-bundle parser.** Accept a directory path *or* a GitHub
   URL pointing at a directory containing `bundle.yml`. Read the
   manifest, walk the file tree, extract embedded links.
3. **Link extraction → existing paper-ingest pipeline.** Mined DOIs /
   arXiv IDs / PMC IDs are deduplicated and routed through the
   existing `ingest_dois_into_kb` path. Other URLs (datasets, tools)
   become metadata-only KB rows, not chunked content.
4. **Two KB modes:**
   - **Per-skill**: one KB per repo/bundle. KB name derives from the
     repo / bundle (`<org>__<repo>` or `<bundle.yml:name>`).
   - **Composite**: one KB aggregates N skills. KB name supplied by
     the user. Each chunk carries `source_skill=<bundle>` metadata so
     queries can still filter by skill.
5. **Batch mode.** `perspicacite ingest-skill-bundles <dir>/` walks a
   directory of bundles and ingests each one (per-skill mode) or
   merges them into a composite (with `--into <kb_name>`).
6. **Authentication.** Optional `GITHUB_TOKEN` env var. Unauthenticated
   uses the public REST API at 60 req/hr. With a token: 5000 req/hr.
   Private repos require a token.
7. **Caching.** Repo tarballs cached locally (keyed on commit SHA) so
   re-ingest is free. Bundle parse cached on the YAML hash.

Out of scope (followups):

- Full code-symbol indexing (a CTags-style index of every function /
  class). Today's chunker handles code as text; symbol-aware retrieval
  is a separate proposal.
- Auto-running notebooks to capture cell outputs.
- GitHub Issues / Pull Requests as content (only repo file contents).
- Watch mode (re-ingest on push). Cron the CLI for now.
- Authenticating against GitHub Enterprise / GHE Server.
- GitLab / Bitbucket. Use the GitHub adapter as the template.

## Input formats

### GitHub repo URL

Accepted shapes:

```
https://github.com/<org>/<repo>
https://github.com/<org>/<repo>@<branch>
https://github.com/<org>/<repo>@<commit-sha>
https://github.com/<org>/<repo>/tree/<branch>
https://github.com/<org>/<repo>/tree/<branch>/<subpath>      # subdir-only
```

For private repos the user must set `GITHUB_TOKEN` in the env. The
adapter sends `Authorization: Bearer <token>` on all calls.

### Skill-bundle directory

Two intake modes:

1. **Local path**: `perspicacite ingest-skill-bundle /path/to/bundle/`
2. **GitHub URL** pointing at a directory containing `bundle.yml`:
   `perspicacite ingest-skill-bundle https://github.com/org/repo/tree/main/bundles/scrna-qc`

Either way, the adapter expects a `bundle.yml` at the directory root.
If `bundle.yml` is missing, the adapter falls back to **README-only**
mode: ingest the README as a single Paper with metadata-only links.

### `bundle.yml` minimal manifest (v1)

```yaml
# bundle.yml — agentic-science-builder skill manifest
name: scrna-qc                    # required; becomes KB suffix in per-skill mode
title: "Single-cell RNA-seq quality control"
description: "Recipes + recommended thresholds for QC of scRNA-seq counts."
version: 0.3.0
domain: ["genomics", "single-cell", "QC"]   # used for composite-KB grouping
papers:
  - doi: "10.1038/s41587-022-01505-2"
    note: "main methods paper"
  - arxiv: "2204.12345"
  - pmc: "PMC9123456"
datasets:                          # optional — link-only, not chunked
  - url: "https://figshare.com/articles/dataset/12345"
    name: "Example PBMC dataset"
tools:                             # optional — link-only
  - url: "https://github.com/scverse/scanpy"
    name: "scanpy"
content:                           # optional — explicit ingest roots
  include:
    - "README.md"
    - "docs/**/*.md"
    - "notebooks/**/*.ipynb"
    - "src/**/*.py"
  exclude:
    - "tests/**"
    - "**/.git/**"
```

All fields are optional except `name`. The parser silently ignores
unknown top-level keys (forward-compat). When `content.include` is
missing, defaults apply:

```
include: ["README*", "*.md", "docs/**/*.md", "**/*.py", "**/*.ipynb"]
exclude: ["tests/**", ".git/**", "node_modules/**", "venv/**"]
```

## Architecture

```
                        ┌────────────────────────────────┐
                        │  CLI / MCP entrypoint           │
                        │                                  │
                        │  ingest-github-repo   <url>      │
                        │  ingest-skill-bundle  <path|url> │
                        │  ingest-skill-bundles <dir>      │
                        └────────────┬─────────────────────┘
                                     │
                                     ▼
                        ┌────────────────────────────────┐
                        │  Bundle / Repo adapter           │
                        │   (pipeline/github_kb.py)        │
                        └─────┬───────────────┬────────────┘
                              │               │
                ┌─────────────▼──┐    ┌──────▼────────────┐
                │ GitHubFetcher  │    │ BundleManifest    │
                │ - tarball / clone│   │ - parses bundle.yml│
                │ - tree walk     │    │ - resolves include/exclude│
                │ - SHA cache    │    │ - link extraction │
                └────────┬───────┘    └──────┬────────────┘
                         │                   │
                         └─────────┬─────────┘
                                   ▼
                        ┌────────────────────────────────┐
                        │  Chunk producer                  │
                        │  - .md / .rst / .txt → text     │
                        │  - .py / .ts / .rs / etc. → code│
                        │  - .ipynb → strip cells + outputs│
                        │  - Python docstring extraction  │
                        │  - Paper objects with content_type│
                        │    in {"docs", "code", "notebook"}│
                        └────────────┬─────────────────────┘
                                     │
                                     ▼
                        ┌────────────────────────────────┐
                        │  DynamicKnowledgeBase.add_papers │
                        │  (same as today's local-docs path)│
                        └────────────┬─────────────────────┘
                                     │
                                     ▼
                        ┌────────────────────────────────┐
                        │  Link routing                    │
                        │  - DOI / arXiv / PMC →           │
                        │       ingest_dois_into_kb        │
                        │       (same KB)                  │
                        │  - Other URLs → KB-log only      │
                        └────────────────────────────────┘
```

## Components

| File | Responsibility |
|---|---|
| `src/perspicacite/pipeline/github_kb.py` (new) | Top-level orchestrator: `ingest_github_repo(...)`, `ingest_skill_bundle(...)`, `ingest_skill_bundles_batch(...)`. |
| `src/perspicacite/pipeline/github/fetcher.py` (new) | HTTP/Git fetcher. Two strategies: tarball download (default) and shallow git clone (fallback when tarball API is rate-limited). SHA-keyed disk cache. Handles `GITHUB_TOKEN`. |
| `src/perspicacite/pipeline/github/bundle.py` (new) | `BundleManifest.parse(yaml_path)`, validation against the minimal schema. |
| `src/perspicacite/pipeline/github/walk.py` (new) | Tree walker honouring include/exclude globs. |
| `src/perspicacite/pipeline/github/chunk_producer.py` (new) | Converts files → `Paper`-shaped fixtures with the right `content_type`. Notebook stripping, docstring extraction. |
| `src/perspicacite/pipeline/github/links.py` (new) | Regex-extract DOIs / arXiv / PMC / generic URLs from text. |
| `src/perspicacite/cli.py` | Add `ingest-github-repo`, `ingest-skill-bundle`, `ingest-skill-bundles` commands. |
| `src/perspicacite/mcp/server.py` | Add `ingest_github_repo` and `ingest_skill_bundle` tools. |
| `src/perspicacite/config/schema.py` | Add `github.token_env_var`, `github.cache_dir`, `github.default_branch`, `bundles.composite_kb_name_template`. |
| `tests/unit/test_github_fetcher.py` (new) | URL parsing, tarball download with mocked HTTP, SHA cache hit/miss. |
| `tests/unit/test_bundle_manifest.py` (new) | YAML parsing, defaults, link extraction. |
| `tests/integration/test_github_kb_e2e.py` (new) | End-to-end: a fixture skill-bundle directory under `tests/data/sample_bundle/` → ingest → KB has expected chunks + linked-DOI events. |
| `docs/github-skill-bundle-ingest-YYYY-MM-DD.md` (new) | Operator guide. |

## Public API

### Python

```python
from perspicacite.pipeline.github_kb import (
    ingest_github_repo,
    ingest_skill_bundle,
    ingest_skill_bundles_batch,
)

# 1. Ingest a single GitHub repo as a KB
await ingest_github_repo(
    repo_url="https://github.com/scverse/scanpy@v1.10.0",
    kb_name="scanpy",
    description="The scanpy single-cell analysis library docs + source.",
    *,
    config: KnowledgeBaseConfig,
    vector_store, embedding_service, session_store,
    token: str | None = None,           # falls back to GITHUB_TOKEN env
    include: list[str] | None = None,   # override defaults
    exclude: list[str] | None = None,
    ingest_linked_papers: bool = False, # default off for raw-repo mode
) -> IngestSummary

# 2. Ingest a single skill-bundle (per-skill mode)
await ingest_skill_bundle(
    source="/path/to/bundle/",            # or a GitHub URL
    kb_name=None,                         # default: bundle.yml's "name"
    *,
    composite_kb: str | None = None,      # if set, append to this KB
    ingest_linked_papers: bool = True,    # default on for bundles
    config, vector_store, embedding_service, session_store, token=None,
) -> IngestSummary

# 3. Batch
await ingest_skill_bundles_batch(
    bundles_dir="/path/to/bundles_root/",
    *,
    mode: Literal["per_skill", "composite"] = "per_skill",
    composite_kb_name: str | None = None, # required when mode="composite"
    parallelism: int = 4,
    config, vector_store, embedding_service, session_store, token=None,
) -> list[IngestSummary]
```

`IngestSummary` is the same dataclass already returned by
`ingest_dois_into_kb` plus three extras:

```python
@dataclass
class IngestSummary:
    kb_name: str
    files_added: int
    chunks_added: int
    linked_papers_added: int
    linked_papers_skipped: int
    linked_papers_failed: int
    metadata_only_links: list[str]
    elapsed_s: float
    bundle_name: str | None
    repo_url: str | None
    commit_sha: str | None
```

### CLI

```bash
perspicacite ingest-github-repo https://github.com/scverse/scanpy \
    --kb scanpy \
    --include "README*,docs/**/*.md,scanpy/**/*.py" \
    --exclude "tests/**" \
    --description "scanpy library docs + source"

perspicacite ingest-skill-bundle /path/to/scrna-qc/ --kb scrna-qc
perspicacite ingest-skill-bundle /path/to/scrna-qc/ --into composite-genomics

perspicacite ingest-skill-bundles /path/to/all-bundles/ --mode per-skill
perspicacite ingest-skill-bundles /path/to/all-bundles/ \
    --mode composite --kb-name genomics-bundles
```

### MCP

```
ingest_github_repo(repo_url, kb_name=None, description=None,
                   include=None, exclude=None,
                   ingest_linked_papers=False) -> JSON
ingest_skill_bundle(source, kb_name=None, composite_kb=None,
                    ingest_linked_papers=True) -> JSON
```

(Batch is intentionally CLI-only — it can run for minutes and is
better suited to a tmux session than an MCP timeout.)

## Link extraction

Regex set for `src/perspicacite/pipeline/github/links.py`:

```python
DOI_RE   = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.I)
PMC_RE   = re.compile(r"\bPMC(\d{6,8})\b")
URL_RE   = re.compile(r"https?://[^\s<>()\"']+", re.I)
```

Per file: dedup mined references, attach back to the chunk(s) that
contained them (so the KB can answer "which README mentioned this
paper?"). The resolved DOI list is then handed to
`ingest_dois_into_kb` if `ingest_linked_papers=True`.

Generic URLs that aren't DOI/arXiv/PMC become `metadata_only_links`
in the summary and an `external_link` row in the KB log (Wave 4.3).
Not chunked.

## KB layout

### Per-skill mode

- KB name = `bundle.yml:name` (or `<org>__<repo>` for raw repos).
- Description = `bundle.yml:description` (or first paragraph of README).
- Each chunk metadata carries:
  - `paper_id`  = `bundle:<name>:<relative_path>` (synthetic, stable).
  - `title`     = `<bundle> — <relative_path>`.
  - `content_type` ∈ `{"docs", "code", "notebook", "abstract"}`.
  - `source_skill` = `<name>`.
  - `source_path` = relative path inside the repo.
  - `commit_sha` = the resolved commit SHA at ingest time.
- Linked papers ingested via the normal DOI path keep their own
  `paper_id` (the DOI) and `source_command="ingest_skill_bundle"`.

### Composite mode

Same chunk metadata, but `source_skill` discriminates between bundles.
Queries can filter via the Wave 4.2 `SearchFilters.source_skill` field
(new; sibling of `content_type` / `year_min`).

## Caching

- `data/github_cache/<commit_sha>/...` — extracted tarball / clone.
  Reused on next ingest with the same SHA. Cleanup policy: LRU eviction
  when total size exceeds `github.cache_max_mb` (default 2 GB).
- Bundle parse cached on a `(yaml_path, yaml_sha256)` key. Avoids
  re-parsing when only the README changed.
- The Wave 2.1 LLM cache + Wave 2.2 embedding cache already handle
  per-chunk reuse. No new cache needed at the chunk layer.

## Error handling

- **Repo doesn't exist** → return `IngestSummary(...failed=1)` with
  `error="repo_not_found"`. Don't raise.
- **Rate-limited (HTTP 403/429)** → use the `X-RateLimit-Reset` header
  to surface a typed `GithubRateLimitError` (mirror of Wave 3.1
  `RateLimitError`). When the retry budget exhausts, abort cleanly and
  return a partial summary listing what *did* land.
- **Auth-required** → `GithubAuthError("set GITHUB_TOKEN env var")`.
- **bundle.yml malformed** → fall back to README-only mode + log a
  warning event.
- **Linked paper fails to ingest** → bundle still succeeds; the failed
  DOI is recorded in `linked_papers_failed` and in the KB log
  (Wave 4.3 `paper_failed` event with `reason`).
- **No content found (empty include glob)** → return summary with
  `files_added=0`; surface a warning, not an error.

## Concurrency & rate limits

- Single bundle: sequential file-walk + chunk → embed. Cheap enough to
  keep linear.
- Batch mode: `parallelism=4` (configurable). One bundle per worker.
  Each worker holds its own GitHubFetcher with shared cache.
- GitHub API budget: ~12 calls per repo (1 tarball + a few metadata
  reads). 60/hr unauthenticated → ~5 repos/hr max without token.
  Document the warning prominently. With token: 5000/hr → comfortably
  thousands of repos.

## Test plan

- `test_parse_repo_url_with_branch_and_commit`
- `test_parse_repo_url_with_subpath`
- `test_fetcher_uses_tarball_when_no_branch_specified`
- `test_fetcher_caches_by_commit_sha`
- `test_fetcher_falls_back_to_clone_on_tarball_429`
- `test_github_token_passed_in_authorization_header`
- `test_bundle_manifest_minimal_valid_yaml`
- `test_bundle_manifest_unknown_keys_ignored`
- `test_bundle_manifest_falls_back_to_readme_when_yaml_missing`
- `test_link_extractor_finds_doi_arxiv_pmc`
- `test_link_extractor_dedups`
- `test_per_skill_mode_creates_kb_named_after_bundle`
- `test_composite_mode_aggregates_with_source_skill_metadata`
- `test_batch_mode_per_skill_yields_n_kbs`
- `test_batch_mode_composite_yields_one_kb`
- `test_linked_dois_ingested_via_existing_pipeline`
- `test_kb_log_records_paper_added_with_source_command_ingest_skill_bundle`
- `test_repo_not_found_returns_failed_summary_not_raises`
- `test_rate_limit_surfaces_as_typed_error`

## Operator guide outline (doc deliverable)

`docs/github-skill-bundle-ingest-YYYY-MM-DD.md` will cover:

1. The two CLI verbs and their flags.
2. `bundle.yml` schema with an annotated example.
3. `GITHUB_TOKEN` setup.
4. Per-skill vs composite trade-offs (when to use each).
5. The batch workflow + parallelism knob.
6. Cache + cleanup instructions.
7. Cross-references to Waves 4.2 (filters), 4.3 (KB log), 2.4 (budget).

## Followups

- Code-symbol indexing (CTags / tree-sitter) for source files —
  enables "find me functions named `*qc*`" queries.
- Notebook execution capture (run nbexec on safe notebooks, store
  outputs alongside source).
- GitLab + Bitbucket adapters (reuse the fetcher abstraction).
- Watch mode: subscribe to repo webhooks → auto-reingest on push.
- Cross-bundle dedup: when two bundles cite the same paper, the
  linked-paper ingest should hit Chroma's existing-DOI guard and skip
  re-fetch (already works via `ingest_dois_into_kb` dedup, just need
  to verify it under the composite mode).
