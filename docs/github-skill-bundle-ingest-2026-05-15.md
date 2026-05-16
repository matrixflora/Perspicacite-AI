# GitHub repos + skill-bundles as KB sources — operator guide

Spec: [`docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`](superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md)

## Overview

Perspicacité v2 can ingest a GitHub repository or an agentic-science-builder
skill-bundle directory into a normal KB. Markdown, Python docstrings, and
Jupyter notebooks are chunked; DOIs cited in `bundle.yml` (and inline in
README/docs) are routed through the existing DOI-ingest pipeline so prose +
papers land in the same KB.

## Quick start

The repo ships a fixture bundle at `tests/data/sample_bundle/`; every example
below runs against it without a network call.

```bash
# 1. Ingest a single GitHub repo (raw mode — does NOT auto-fetch linked papers)
perspicacite ingest-github-repo https://github.com/scverse/scanpy --kb-name scanpy

# 2. Ingest a single skill bundle from a local path (per-skill KB)
perspicacite ingest-skill-bundle tests/data/sample_bundle/

# 3. Batch-ingest a directory of bundles into ONE composite KB
perspicacite ingest-skill-bundles tests/data/ --into composite-genomics
```

## CLI reference

### `perspicacite ingest-github-repo URL`

| Flag | Description |
|---|---|
| `--kb-name <name>` (required) | Target KB; created if missing. |
| `--include <glob>` | Include glob, repeatable. Defaults to bundle defaults. |
| `--exclude <glob>` | Exclude glob, repeatable. Defaults to bundle defaults. |

Raw-repo mode does NOT auto-route linked papers — the bundle author's
`papers:` whitelist is the only trusted source in v1.

### `perspicacite ingest-skill-bundle SOURCE`

`SOURCE` is either a local directory or a GitHub URL pointing at a
directory containing `bundle.yml`.

| Flag | Description |
|---|---|
| `--kb-name <name>` | KB name. Default: bundle's `name` via `config.bundles.default_kb_name_template`. |
| `--no-linked-papers` | Skip the DOI-ingest step for papers cited in the bundle. |

### `perspicacite ingest-skill-bundles SOURCE_DIR`

Walks immediate subdirectories of `SOURCE_DIR`, treating each as a bundle.

| Flag | Description |
|---|---|
| `--into <kb-name>` | Composite mode: every bundle's chunks land in this KB. Omit for per-skill mode (one KB per bundle). |
| `--no-linked-papers` | Skip linked-paper ingest for every bundle in the batch. |

## MCP tools

Two tools are exposed on the FastMCP server. Both return the standard
`{"success": true, ...}` envelope (see [`docs/MCP.md`](MCP.md)).

```python
# Bundle-aware ingest — auto-routes manifest + README DOIs.
ingest_skill_bundle(
    source="/local/path/or/github/url",
    kb_name=None,                  # default: from config template
    ingest_linked_papers=True,
)

# Raw repo ingest — chunks code/docs only; no linked-paper routing.
ingest_github_repo(
    url="https://github.com/org/repo@ref",
    kb_name="target-kb",
    include=None,
    exclude=None,
)
```

Latency (from the tool docstrings in `src/perspicacite/mcp/server.py`):

- `ingest_github_repo`: 30–180s; tarball cache makes re-ingest fast.
  Client HTTP timeout >=240s.
- `ingest_skill_bundle`: 60–600s; each linked DOI runs the full
  PDF-resolve + embed pipeline. Client timeout >=600s.

## Configuration

`config.github.*`:

| Key | Default | What it does |
|---|---|---|
| `token_env_var` | `GITHUB_TOKEN` | Name of the env var the fetcher reads for auth. |
| `cache_dir` | `data/github_cache` | Where extracted tarballs live, keyed on commit SHA. |
| `cache_max_mb` | `2048` | LRU evicts above this size. |
| `default_branch` | `HEAD` | Used when the URL omits a ref. |
| `user_agent` | `Perspicacite/2.0` | Sent on every API call. |
| `api_base` | `https://api.github.com` | Override for GHE (untested in v1). |

`config.bundles.*`:

| Key | Default | What it does |
|---|---|---|
| `default_kb_name_template` | `{name}` | Per-skill KB name template; must contain `{name}`. |
| `composite_kb_name_template` | `composite-{domain}` | Composite KB template; must contain `{domain}`. |

Override these in `config.yaml` or the per-environment overlay your
deployment loads.

## Authentication

Private repos and high-volume runs require a GitHub token:

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
perspicacite ingest-github-repo https://github.com/private-org/repo --kb-name internal
```

GitHub rate-limits the REST API at **60 req/hr unauthenticated**, **5000
req/hr authenticated**. Unauthenticated tops out at ~5 repos/hr.

## Caching

Tarballs extract under `data/github_cache/<commit_sha>/`. A `.complete`
sentinel is written only after the extract finishes — entries without it
are treated as corrupt and refetched on the next ingest, so the cache is
safe to kill mid-run. LRU eviction caps total size at
`config.github.cache_max_mb`. Clear the cache with
`rm -rf data/github_cache/`.

## What gets chunked

The walker honours include/exclude globs (`bundle.yml:content.include` /
`exclude`, or defaults). For each surviving file:

| Extension | Chunk content | Notes |
|---|---|---|
| `.md` | Full text | H1 as title; DOI / arXiv / PMC IDs mined into metadata. |
| `.py` | Docstrings only | Module + class + function docstrings; **source bodies are NOT chunked in v1**. |
| `.ipynb` | Concatenated cell sources | `markdown` + `code` cells joined; outputs dropped. |
| `.yaml`, `.yml`, `.json`, `.toml`, `.txt` | Generic text fallback | Optional; surfaces if matched by an include glob. |
| Anything else | Skipped | Falls outside default include globs. |

Default globs (from `ContentSpec` in `pipeline/github/bundle.py`):

```
include: README*, *.md, docs/**/*.md, **/*.py, **/*.ipynb
exclude: tests/**, .git/**, node_modules/**, venv/**
```

## Linked-paper ingest

When `ingest_linked_papers=True` (bundle-mode default), DOIs in
`bundle.yml:papers` plus DOIs mined from README / docs are deduplicated
and routed through `ingest_dois_into_kb`, landing in the same KB with
`source_command="ingest_skill_bundle"` in the KB log (Wave 4.3).

arXiv and PMC IDs are NOT auto-ingested in v1 — they appear in the
summary as `linked_papers_skipped_non_doi` (`(kind, value)` tuples) for
manual routing. Raw-repo mode (`ingest-github-repo`) never auto-routes
DOIs even when found: the v1 trust model only resolves DOIs an author
signed off on via `papers:`.

## Per-skill vs composite KBs

- **Per-skill** (one KB per bundle): when each skill needs its own
  retrieval surface. Default for both single-bundle and batch ingest.
- **Composite** (many bundles → one KB): when a research domain spans
  overlapping skills you'd rather query at once. Trigger with
  `--into <kb-name>`; the `source_skill` metadata on each chunk lets
  you filter back to a single bundle.

## Followups (post-v1, NOT shipped)

- Full code-symbol indexing (CTags / tree-sitter) — index every function
  and class instead of docstrings only.
- Notebook execution to capture cell outputs.
- GitHub Issues / Pull Requests as content.
- Watch mode (re-ingest on push via webhooks).
- GitHub Enterprise auth.
- GitLab / Bitbucket adapters.
- arXiv / PMC auto-ingest in linked-papers.
- Embedding-model conflict detection at KB creation time.

## Troubleshooting

- **Rate-limited (403/429).** The fetcher falls back from the tarball API
  to a shallow `git clone`. Set `GITHUB_TOKEN` to lift 60/hr → 5000/hr.
- **404 on a private repo.** Repo doesn't exist or token can't see it.
  Confirm with `gh repo view <org>/<repo>`.
- **Partial linked-paper success.** Compare `linked_papers_added` vs
  `linked_papers_skipped_non_doi`; failures surface as Wave 4.3
  `paper_failed` events with a `reason` field.
- **Cache corruption.** Missing `.complete` sentinel triggers automatic
  refetch. Force a full refresh with `rm -rf data/github_cache/`.
