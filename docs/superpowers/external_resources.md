# External resources (Capsule Cycle C)

> **Status:** Cycle C — V2 fetch-on-demand for paper-referenced external resources. V1 mining shipped in Cycle A.

## Overview

A paper's text typically references external artifacts: GitHub repos hosting analysis code, Zenodo records with datasets, related publications (DOIs), data-archive accessions (MASSIVE, PRIDE, MetaboLights, GEO, BioProject, SRA). Cycle A **mines** these references at capsule-build time and writes them to `<capsule>/resources.json` (V1). Cycle C **fetches** the underlying content on demand and routes text-like results into the KB as `is_external=True` chunks (V2).

## What gets mined (V1, Cycle A)

`<capsule>/resources.json` — list of records. Each record:

```json
{
  "resource_id": "github:owner/repo",
  "kind": "github" | "doi" | "zenodo" | "massive" | "pride" | "metabolights"
        | "geo_series" | "bioproject" | "sra_run" | "url",
  "identifier": "owner/repo",
  "url": "https://github.com/owner/repo",
  "evidence_span": "…we deposited reads at PRIDE (PXD012345) and code at github.com/foo/bar…",
  "char_span": null,
  "page": null,
  "block_id": null
}
```

Mining is deterministic and free (regex + URL parsing — no LLM, no network). See `pipeline/external/accessions.py` and `pipeline/external/resources.py`.

## What gets fetched (V2, Cycle C)

`SUPPORTED_KINDS` for on-demand fetch:

| kind | Fetched | Stored under |
|---|---|---|
| `github` | README + `docs/**/*.{md,rst,txt}` + env files (`requirements.txt`, `pyproject.toml`, …) + `notebooks/**/*.ipynb` (outputs stripped) + scripts in `scripts/` (`.py`/`.R`/`.r`/`.jl`) + a `data_manifest.json` listing files in `data/`-like subdirs (no blob download) + `tree.json` | `<capsule>/external/github/<owner>__<repo>/` |
| `zenodo` | Record metadata JSON (always). Optional small text/code files from `record.files` — gated by extension allowlist + per-file + per-record byte caps. Archives (`.zip`, `.tar.gz`, …) are **always skipped, never extracted**. | `<capsule>/external/zenodo/<id>.json` + `<capsule>/external/zenodo/<id>/files/<name>` |
| `doi` | Crossref metadata + Unpaywall lookup (best-OA URL). | `<capsule>/external/crossref/<doi_slug>.json`, `<capsule>/external/unpaywall/<doi_slug>.json` |

`pubmed` (by PMID) and `pmcid` (DOI → PMCID) helpers exist in `pipeline/external/fetch_doi.py` but are not yet dispatched by the orchestrator — reserved for future wiring when papers carry PMID metadata.

## Caps and guards

- **Zenodo per-file cap** (`zenodo_max_bytes_per_file`, default 500 KB) — skip oversize.
- **Zenodo per-record cap** (`zenodo_max_bytes_per_record`, default 5 MB) — stop fetching once cumulative crosses cap.
- **Archive skip** — `.zip`, `.tar`, `.tar.gz`, `.tgz`, `.7z`, `.rar` are never downloaded.
- **GitHub per-file cap** (80 KB by default) and **run budget** (300 KB across docs/env/notebooks/scripts per repo).
- **Notebook output stripping** — `.ipynb` files have `outputs` + `execution_count` cleared before disk write AND before chunking.

## Cache layout

`<config.external_resources.cache_dir>/<api>__<sha256-of-query[:32]>.json`. TTL default 30 days (`cache_ttl_days`). Payload wrapped as `{"_cached_at": <epoch>, "data": <value>}` for TTL enforcement.

Bytes responses are base64-encoded inside the cache JSON wrapper so the round-trip stays JSON-compatible. Legacy unwrapped payloads are accepted on read for forward compatibility with ASB-style caches.

APIs used: `crossref`, `unpaywall`, `pubmed`, `pmcid_for_doi`, `zenodo`, `zenodo_blob`, `github`, `github_readme`, `github_tree`, `github_blob`.

## Ingesting fetched content

When the orchestrator runs with `ingest=True`, fetched text-like paths are routed through `ingest_local_documents(..., external_metadata={"parent_paper_id": paper.id})`. Resulting chunks carry:

- `is_external = True`
- `parent_paper_id = "doi:..."` (the paper that referenced the resource)
- `resource_refs` includes the originating `resource_id` when provided

`.ipynb` content is stripped via `strip_notebook_outputs` before chunking so image blobs / stderr don't pollute the KB.

This means a question like *"What does the analysis code do?"* against a KB that ingested both the paper and its GitHub repo can pull from repo-file chunks while still attributing them to the paper.

## Configuration

```yaml
external_resources:
  mine: true                          # V1 — Cycle A, always-on
  fetch_on_demand: true               # V2 — gate for the fetch helpers
  cache_dir: ./data/cache
  cache_ttl_days: 30
  zenodo_max_bytes_per_file: 500_000
  zenodo_max_bytes_per_record: 5_000_000
  text_file_extensions:
    - .md
    - .rst
    - .txt
    - .py
    - .R
    - .r
    - .jl
    - .ipynb
    - .yml
    - .yaml
    - .toml
    - .json
    - .csv
```

## GitHub rate limits

Unauthenticated GitHub API: 60 requests/hour per IP. With aggressive caching (30-day TTL by default) a typical research session stays well under. Authenticated: 5000/hour. Set `GITHUB_TOKEN` in the environment to lift the limit:

```bash
export GITHUB_TOKEN=ghp_...
```

Stored only in the request `Authorization: Bearer ...` header; never written to disk.

## Cross-references

- Cycle A V1 mining: `pipeline/capsule_builder.py::write_resources`
- Cycle B `is_external` / `parent_paper_id` chunk metadata: `models/documents.py::ChunkMetadata`
- Cycle B `CapsuleReader.ingest_capsule` (independent path): `integrations/capsule_reader.py`
- Spec: `specs/2026-05-13-capsule-multimodal-rag-design.md`
- Cycle C plan: `plans/2026-05-14-capsule-cycle-c-external-fetch.md`
- ASB source helpers we vendored from: `~/git/AgenticScienceBuilder/src/agentic_science_builder/enrichment.py` @ `a10eced`
