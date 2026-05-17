# GitHub + Skill-Bundle Ingest — Operator Guide (2026-05-15)

Wave 6 of the content-acquisition roadmap. Ingest GitHub repositories and
curated "skill bundles" (directories with a `bundle.yml` manifest) into
Perspicacité knowledge bases.

---

## What it does

Three new entry points (CLI + MCP + Python API) let you pull content from:

- A GitHub repo URL — fetched via the tarball API with SHA-based local caching
- A local directory — used directly (no network required)
- A "skill bundle" — a directory with an optional `bundle.yml` manifest
  describing papers, file-include patterns, and metadata

Content types handled:

| File type | Processing |
|-----------|-----------|
| `.md`, `.rst` | Full text → one `Paper` per file (`content_type="docs"`) |
| `.py` | Module/class/function docstrings + signatures only (no full source bodies) |
| `.ipynb` | Markdown cells + code cells stripped of outputs |

Linked papers (DOIs/arXiv IDs/PMC IDs declared in `bundle.yml` or mined
from README text) are also ingested via the existing `ingest_dois_into_kb`
pipeline.

---

## The `bundle.yml` manifest

All keys are optional except `name`:

```yaml
name: scrna-qc                            # required
description: "Single-cell RNA-seq QC"    # optional
version: "1.0.0"
domain: genomics

papers:
  - doi: 10.1038/s41587-020-00744-z
  - arxiv: "2204.12345"
  - pmc: "PMC9123456"

content:
  include:
    - "**/*.py"
    - "**/*.md"
    - "**/*.ipynb"
  exclude:
    - ".git/**"
    - "__pycache__/**"
    - "*.egg-info/**"
```

When `bundle.yml` is absent the directory is still usable — the manifest
falls back to README-only mode (directory name becomes the bundle name,
DOIs are mined from the README text).

---

## CLI usage

```bash
# Ingest a GitHub repo URL
uv run perspicacite ingest-github-repo https://github.com/deepmind/alphafold \
    --kb alphafold-kb -c config.yml

# Ingest a local skill bundle directory
uv run perspicacite ingest-skill-bundle ./bundles/scrna-qc \
    --kb scrna-qc-kb --no-linked-papers -c config.yml

# Ingest multiple bundles from a directory tree
uv run perspicacite ingest-skill-bundles ./bundles/scrna-qc ./bundles/cellranger \
    -c config.yml
```

`--no-linked-papers` skips DOI ingestion (faster; useful for code-only repos).

---

## MCP tool usage

Both tools are exposed at `/mcp` alongside the existing 10 tools:

```python
# Via the MCP client
await client.call_tool("ingest_github_repo", {
    "url_or_path": "https://github.com/deepmind/alphafold",
    "kb_name": "alphafold-kb",
    "ingest_linked_papers": True,
})

await client.call_tool("ingest_skill_bundle", {
    "path": "/data/bundles/scrna-qc",
    "kb_name": "scrna-qc-kb",
})
```

Both tools return:
```json
{
  "bundle_name": "scrna-qc",
  "files_added": 12,
  "chunks_added": 84,
  "linked_papers_added": 5,
  "errors": []
}
```

---

## Configuration (`config.yml`)

```yaml
github:
  token_env_var: GITHUB_TOKEN      # env var holding a personal access token
  cache_dir: data/github_cache     # SHA-keyed tarball cache
  cache_max_mb: 2048
  default_branch: HEAD
  user_agent: "Perspicacite/2.0"
  api_base: https://api.github.com

bundles:
  default_kb_name_template: "{name}"            # per-skill mode
  composite_kb_name_template: "composite-{domain}"
```

A GitHub token is optional but recommended — unauthenticated requests are
rate-limited to 60/hour; authenticated to 5000/hour.

---

## KB log events

Every `ingest_skill_bundle` run emits events to the KB's JSONL log
(`knowledge_base.log_dir/<kb_name>.jsonl`):

| `event` | When |
|---------|------|
| `paper_added` | A linked DOI was successfully fetched and embedded |
| `paper_skipped` | DOI already exists in the KB |
| `paper_failed` | Fetch / embed failed |
| `external_link` | Non-paper URL found in README (metadata only, not embedded) |

---

## Architecture

```
GitHub URL / local path
        │
        ▼
GitHubFetcher           (pipeline/github/fetcher.py)
  resolve_commit_sha
  fetch_tarball (SHA-cached)
  fetch_clone (fallback)
        │
        ▼
BundleManifest.from_directory   (pipeline/github/bundle.py)
  parse bundle.yml or README-only fallback
  collect_paper_refs()
  extract_links_from_text()
        │
        ▼
papers_from_directory   (pipeline/github/chunk_producer.py)
  walk_filtered (pathspec / fnmatch)
  .md / .rst  → full text
  .py         → docstrings + signatures (ast.parse)
  .ipynb      → cells stripped (nbformat)
        │
        ▼
DynamicKnowledgeBase.add_papers   (rag/dynamic_kb.py)
        │
        ▼
ingest_dois_into_kb               (pipeline/search_to_kb.py)
  linked papers from manifest + README
```

---

## Files

| File | Purpose |
|------|---------|
| `src/perspicacite/pipeline/github/__init__.py` | Package marker |
| `src/perspicacite/pipeline/github/fetcher.py` | GitHub API fetcher + SHA cache |
| `src/perspicacite/pipeline/github/bundle.py` | `bundle.yml` parser + link extractor |
| `src/perspicacite/pipeline/github/walk.py` | Glob-based file walker |
| `src/perspicacite/pipeline/github/chunk_producer.py` | File → Paper converter |
| `src/perspicacite/pipeline/github_kb.py` | Top-level orchestrator |
| `src/perspicacite/config/schema.py` | `GitHubConfig`, `BundlesConfig` |
| `src/perspicacite/models/search.py` | `SearchFilters.source_skill` |
| `src/perspicacite/cli.py` | `ingest-github-repo`, `ingest-skill-bundle[s]` commands |
| `src/perspicacite/mcp/server.py` | `ingest_github_repo`, `ingest_skill_bundle` tools |

---

## Caveats

- **Rate limits**: GitHub tarball downloads count against the REST API quota.
  Use a personal access token in production.
- **Large repos**: Tarballs for very large repos (>500 MB) may be slow.
  The SHA-based cache avoids re-downloading on repeat runs.
- **Notebook outputs**: All cell outputs are stripped before embedding.
  Base64 images are discarded. Only source text is embedded.
- **Python source**: Only docstrings and function/class signatures are
  embedded (not full source bodies). Full-source indexing is a followup.

---

## Followups (post-v1)

- Code-symbol indexing (function bodies, class attributes)
- Notebook execution in a sandbox → embed output + plots
- GitLab adapter (`gitlab.com/org/repo` URLs)
- `SearchFilters.source_skill` wiring into `MultiKBRetriever` queries
- Adaptive cache eviction respecting `github.cache_max_mb`
- GitHub Actions integration: auto-ingest on push events
