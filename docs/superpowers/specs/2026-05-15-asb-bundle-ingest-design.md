# ASB → Perspicacité Bundle Ingest — Design (Addendum)

**Status:** Design accepted 2026-05-15. Not yet implemented. Complements the broader [`2026-05-15-github-skill-bundle-ingest-design.md`](2026-05-15-github-skill-bundle-ingest-design.md) — that spec defines the generic GitHub-repo / skill-bundle ingest scaffolding (fetcher, chunker, KB plumbing); this addendum specifies the **ASB-output-aware** entry point and the **`skill_kb.json` round-trip contract** that pairs ASB and Perspicacité through a small JSON file.

## Why a separate addendum

The base skill-bundle ingest spec was written before we observed real ASB run outputs. ASB doesn't ship a generic `bundle.yml`; it ships a structured per-skill directory with rich JSON sidecars (`tools.json`, `environments.json`, `parameters.json`, `papers.json`, `links.json`, …) and an explicit integration slot (`skill_kb.json`) that's waiting for Perspicacité to populate.

This addendum captures the ASB-specific schema and the wiring, without re-litigating the general github-fetcher / chunker layers from the parent spec.

## Division of labor

- **ASB produces** structured skill bundles. For each skill it extracts: prose body (with frontmatter), tool references (`tools.json`), environment requirements (`environments.json`), parameters (`parameters.json`), backing papers (`papers.json`), external links (`links.json`), examples, failure modes, and ontology refs. It does **not** fetch repos or perform code embedding — `skill_kb.json` explicitly notes the unfetched repo URLs as the integration handoff.

- **Perspicacité consumes** the bundle directory. For each skill it: (a) ingests backing papers via the existing `ingest_dois_into_kb` path, (b) fetches repos listed in `links.json` (category=`repo_github`) via the github fetcher from the parent spec, (c) chunks `skill.md` with markdown discipline, (d) chunks fetched code with the existing **code-aware chunker** and routes through `TypedEmbeddingProvider` (text chunks → `text-embedding-3-small`; code chunks → `mistral/codestral-embed`), (e) lifts ASB's structured sidecars into per-chunk metadata so the auto-KB-routing payload carries tool/env/parameter requirements alongside the answer, (f) writes the inventory of ingested chunks back to `skill_kb.json` so ASB can confirm the integration completed.

Pair, don't absorb — ASB is the structured-extraction system; Perspicacité is the long-tail fetch-embed-search system. Neither owns the other.

## ASB output schema (as observed 2026-05-15)

A representative run: `<ASB_repo>/outputs/audit_2026-05-15_pdf2/metlinkr_full/`. Top-level layout:

```
{run_dir}/
├── audit_report.md, run_summary.md, build_manifest.json     # run metadata
├── skills/
│   ├── _index.json                                           # catalog of all skills
│   └── {skill-slug}/                                         # per-skill bundle
│       ├── skill.md                                          # YAML frontmatter + markdown body
│       ├── README.md
│       ├── tools.json                                        # per-skill tool sidecar
│       ├── environments.json                                 # env requirements
│       ├── parameters.json                                   # tunables with provenance
│       ├── papers.json                                       # backing literature (DOIs)
│       ├── links.json                                        # extracted URLs by category
│       ├── ontology_refs.json                                # EDAM refs
│       ├── examples.jsonl, failure_modes.jsonl
│       ├── artifact_provenance.json
│       ├── skill_kb.json                                     # ★ INTEGRATION SEAM ★
│       └── docs/
├── tools/                                                    # consolidated tool registry
│   ├── _index.json
│   └── {tool-slug}.json                                      # reverse index: related_skills[]
├── capsules/, cards/                                         # ASB-side capsule + card outputs
├── package_index.json, enriched_index.json
└── cache/, ledger/, logs/, validation/, workflow_dag.json
```

### Key file schemas

**`skills/_index.json`** — the catalog Perspicacité reads to discover what to ingest:

```json
{
  "skills": [
    {
      "slug": "cross-identifier-reconciliation",
      "name": "cross-identifier-reconciliation",
      "description": "Cross-identifier reconciliation maps multiple per-metabolite identifiers ...",
      "edam_operation": "http://edamontology.org/operation_0224",
      "schema_version": "0.2.0",
      "body_path": "skills/cross-identifier-reconciliation/skill.md"
    }
  ]
}
```

**`skills/{slug}/skill.md`** frontmatter (the prose body follows after `---`):

```yaml
---
name: cross-identifier-reconciliation
description: "..."
when_to_use_negative:
  - "When the dataset contains only a single identifier type per metabolite..."
edam_operation: "http://edamontology.org/operation_0224"
edam_topics: ["http://edamontology.org/topic_3172", ...]
tools:
  - name: "MetLinkR"
    role: "Primary identifier-mapping and conflict-flagging engine; ..."
    repo: "https://github.com/ncats/MetLinkR"
provenance:
  source_task_ids: [task_005]
  source_papers:
    - doi: "10.1021/acs.jproteome.4c01051"
      title: "metLinkR: Facilitating Metaanalysis ..."
schema_version: "0.2.0"
---
```

**`skills/{slug}/tools.json`** — the rich per-tool record (the YAML frontmatter is a summary; this is the source of truth):

```json
{
  "tools": [
    {
      "slug": "metlinkr",
      "name": "MetLinkR",
      "canonical_url": "https://github.com/ncats/MetLinkR",
      "install": null,
      "related_skills": ["cross-identifier-reconciliation", "..."],
      "source_task_ids": ["task_001", "..."],
      "source_paper_doi": "10.1021/acs.jproteome.4c01051",
      "source_paper_title": "metLinkR: ...",
      "evidence_spans": ["MetLinkR tracks instances where multiple IDs..."],
      "version_used": null,
      "resolution_attempts": []
    }
  ]
}
```

**`skills/{slug}/environments.json`**:

```json
[{"language": "R", "version": null, "packages": [], "dockerfile_hint": null}]
```

**`skills/{slug}/parameters.json`**:

```json
[{
  "name": "threshold", "type": "numeric", "typical": "85.3",
  "min": null, "max": null, "units": "%",
  "source_citation": "Recall against manual curator benchmark should approach 85.3%...",
  "source_doi": null
}]
```

**`skills/{slug}/papers.json`** — feeds directly into Perspicacité's existing DOI ingest:

```json
[{
  "doi": "10.1021/acs.jproteome.4c01051",
  "title": "metLinkR: Facilitating Metaanalysis of Human Metabolomics Data...",
  "year": 2025,
  "role": "method"
}]
```

**`skills/{slug}/links.json`** — categorized URLs Perspicacité acts on:

```json
[
  {"url": "https://github.com/ncats/MetLinkR", "category": "repo_github", "source": "frontmatter:tools[MetLinkR].repo", "surrounding_text": "MetLinkR"},
  {"url": "http://edamontology.org/operation_0224", "category": "ontology_edam", "source": "frontmatter:edam_operation"}
]
```

**`skills/{slug}/skill_kb.json`** — the integration seam, pre-populated by ASB but with `entries: []` until Perspicacité fills it:

```json
{
  "schema_version": "0.1.0",
  "skill_id": "cross-identifier-reconciliation",
  "bundle_dir": "skills/cross-identifier-reconciliation",
  "entries": [],
  "total_bytes": 0,
  "truncated": false,
  "notes": "no repo URLs in skill tools — nothing to fetch (tools present: [MetLinkR, R])"
}
```

## Wiring: ASB output → Perspicacité primitives

| ASB output | Perspicacité primitive | Resulting KB state |
|---|---|---|
| `skills/_index.json` | KB metadata seed | KB description + skill list (the "domain catalog") |
| `skill.md` body | Markdown chunker; **text** content type | `text-embedding-3-small` chunks |
| `skill.md` frontmatter | Per-chunk metadata | `name`, `description`, `edam_operation`, `when_to_use_negative`, `tools[]` (summary form) |
| `tools.json` + `tools/{slug}.json` | Per-chunk metadata (rich tool registry) | `tool_requirements: [{slug, name, canonical_url, install, source_paper_doi, evidence_spans}]` |
| `environments.json` | Per-chunk metadata | `environment: [{language, version, packages, dockerfile_hint}]` |
| `parameters.json` | Per-chunk metadata | `parameters: [...]` with full source citations |
| `papers.json` | **Existing** `ingest_dois_into_kb` | Backing papers ingested into the same KB (one Paper per DOI, normal full-text pipeline) |
| `links.json[category=repo_github]` | **NEW** github fetcher (parent spec, Task 2) | Clone repo → code-aware chunker → `mistral/codestral-embed`; emit chunk records into `skill_kb.json.entries[]` |
| `links.json[category=ontology_edam]` | Per-chunk metadata (link only, no chunking) | `ontology_refs: [...]` |
| `links.json[other]` | Per-chunk metadata | `external_links: [...]` |
| `examples.jsonl` | Small structured chunks tagged `content_type=example` | Retrievable via "show me examples of X" |
| `failure_modes.jsonl` | Small structured chunks tagged `content_type=failure_mode` | Retrievable for negative-case queries |
| `ontology_refs.json` | Per-chunk metadata | (redundant with `links.json[ontology_edam]`; deduped) |
| `artifact_provenance.json` | Per-chunk metadata | `provenance.asb_task_ids: [...]` |
| `capsules/` (ASB side) | **Defer** — Perspicacité has its own capsule format | v1 ignores; v2 may merge |

## The `skill_kb.json` round-trip contract

**ASB writes** (during bundle generation):

- `schema_version`, `skill_id`, `bundle_dir` — identifiers
- `entries: []` — empty, awaiting Perspicacité
- `total_bytes: 0`, `truncated: false` — empty bookkeeping
- `notes` — human-readable summary of what *should* be fetched (e.g., "no repo URLs — nothing to fetch" or "repos to fetch: [url1, url2]")

**Perspicacité writes** (after ingest completes, in-place update):

- `entries[]` — one record per ingested chunk-group:
  ```json
  {
    "kind": "github_repo" | "doi_paper" | "example" | "failure_mode" | "skill_body",
    "source_url": "https://github.com/ncats/MetLinkR",  // or DOI, or path-relative
    "kb_name": "metlinkr_skills",                       // Perspicacité KB this landed in
    "chunk_ids": ["chunk_abc", "chunk_def", ...],       // Chroma chunk IDs
    "chunk_count": 42,
    "bytes": 158234,
    "content_type": "code" | "text" | "example",        // which embedding model used
    "embedding_model": "mistral/codestral-embed",       // resolved by TypedEmbeddingProvider
    "ingested_at": "2026-05-15T20:30:00Z"
  }
  ```
- `total_bytes` — sum of `bytes` across all entries
- `truncated` — true if any source was clipped (file-size cap, rate limit, etc.)
- `notes` — append `"perspicacite_ingest_completed=<timestamp>"` (preserve ASB's original notes)

**Failure modes:**

- Perspicacité unable to fetch a repo (404 / private / network) → entry with `kind=github_repo` and `error: "404"`; `entries[]` still gets the partial result so the rest of the skill stays usable.
- Perspicacité starts an ingest, then crashes mid-way → no partial `skill_kb.json` update; ASB sees the unchanged file on next run and Perspicacité re-attempts (idempotent on chunk IDs derived from `(source_url, sha)`).
- ASB regenerates the bundle and overwrites `skill_kb.json` → Perspicacité notices `entries` reset to `[]` and re-ingests on next run. To avoid silent staleness, both sides include `schema_version` and compare.

## Per-chunk metadata schema

Perspicacité's KB chunks already carry `paper_id`, `chunk_index`, `section`, `title`, `authors`, `year`, `doi`, `source` (the migrated `PaperSource` enum). Skill-bundle ingest adds these fields onto each chunk's metadata dict, all optional and nullable:

```python
# fields added to KB chunk metadata for skill-bundle-sourced chunks
skill_id: str | None              # e.g. "cross-identifier-reconciliation"
skill_name: str | None
skill_description: str | None
edam_operation: str | None        # EDAM ontology URI
edam_topics: list[str] | None
tools: list[dict] | None          # per the tools.json schema above
environment: list[dict] | None    # per environments.json
parameters: list[dict] | None     # per parameters.json
when_to_use_negative: list[str] | None
asb_task_ids: list[str] | None    # provenance back to ASB extraction tasks
schema_version: str | None        # e.g. "0.2.0" — for forward-compat
```

`source` is set to a new `PaperSource.SKILL_BUNDLE` enum value (the migration pattern from 2026-05-15 already established the precedent for adding sources when the origin matters). Paper-construction sites that build chunks from `skill.md` use `PaperSource.SKILL_BUNDLE`; paper-construction sites that build chunks from fetched repos use `PaperSource.SKILL_BUNDLE` as well (the repo is *part of* the skill's content from a query-time perspective); paper-construction sites that build chunks from `papers.json` DOIs continue to use the unified pipeline's `PaperSource.OPENALEX` / `.CROSSREF` (they're real papers, not skills).

## Auto-KB-routing response payload

When the auto-KB-routing returns hits from a skill-bundle KB, the response gains a `skill_metadata` block alongside the existing chunk text + citations:

```json
{
  "answer": "...",
  "sources": [...],
  "skill_metadata": [
    {
      "skill_id": "cross-identifier-reconciliation",
      "tool_requirements": [
        {"name": "MetLinkR", "canonical_url": "https://github.com/ncats/MetLinkR", "install": null},
        {"name": "R", "canonical_url": null, "install": null}
      ],
      "environment": [{"language": "R"}],
      "parameters": [{"name": "threshold", "typical": "85.3", "units": "%"}],
      "executable": false,  // true iff every tool has install + canonical_url resolved
      "asb_mcp_hint": "asb://skill/cross-identifier-reconciliation"  // optional — points to paired ASB MCP server if one is registered
    }
  ]
}
```

The calling agent can then decide whether to **run** the skill (via an ASB MCP server, if available) or just **read** the description.

## CLI / MCP surface

Extend the `perspicacite ingest-skill-bundles` command from the parent spec to recognize ASB output:

```bash
# Auto-detects ASB by checking for skills/_index.json + per-skill JSON sidecars
perspicacite ingest-skill-bundles /path/to/{run_dir}/skills/
perspicacite ingest-skill-bundles /path/to/{run_dir}/skills/ --kb-name metlinkr_skills
perspicacite ingest-skill-bundles /path/to/{run_dir}/skills/ --per-skill   # one KB per skill
perspicacite ingest-skill-bundles /path/to/{run_dir}/skills/ --composite   # one KB for all (default)
```

ASB-mode behavior differs from generic-bundle mode only at the parser layer; downstream (chunker, embedder, storage) is shared.

MCP tool addition (parent spec already adds `ingest_skill_bundle`; this addendum specifies the ASB-mode args):

```python
@mcp.tool()
async def ingest_asb_skills(
    asb_run_dir: str,                          # path to a run dir or its skills/ subdir
    kb_name: str | None = None,                # default: derive from run-dir name
    mode: str = "composite",                   # "composite" | "per-skill"
    update_skill_kb_json: bool = True,         # write back the integration seam
) -> dict:
    """Ingest an Agent Skill Bundle run into a Perspicacité KB.

    Returns: {kb_names: [...], skills_ingested: int, repos_fetched: int,
              papers_ingested: int, failed: [...], total_chunks: int}.
    """
```

## Out of scope (deferred)

1. **ASB capsules / cards** — ASB produces its own `capsules/` and `cards/` per-skill artifacts. Perspicacité has its own capsule format. v1 ignores ASB capsules; v2 may align the formats so they share one capsule store.
2. **Bidirectional federation** — ASB invoking Perspicacité as a knowledge backend (the inverse direction). Out of scope unless ASB requests it.
3. **Live ASB MCP server in this repo** — Perspicacité's MCP server can federate to an ASB MCP server when one exists, but should not host one. Pair, don't absorb.
4. **`PaperSource.SKILL_BUNDLE` enum migration** — add to `papers.py`, extend the invariant test, deferred until the ingest plan executes.
5. **`schema_version` upgrade handling** — ASB ships `0.2.0` for skill bundles and `0.1.0` for `skill_kb.json`. The ingest path reads both; if a future ASB ships `1.0.0` with incompatible fields, the parser fails closed rather than silently dropping data. Concrete migration logic deferred.

## Testing

- **Unit (offline, fixture-driven):** copy a real `audit_2026-05-15_pdf2/metlinkr_full/skills/cross-identifier-reconciliation/` directory tree into `tests/fixtures/asb/`. Tests cover parser → metadata extraction → chunk construction → `skill_kb.json` round-trip. Use real ASB output, not synthetic — keeps the parser honest against schema drift.
- **Integration:** ingest the full MetLinkR run end-to-end against a temporary Chroma collection; assert chunk counts, tool-metadata presence on a sampled chunk, and `skill_kb.json` was updated.
- **Regression:** the auto-KB-routing pin test (when written) confirms `skill_metadata` is in the response payload for any hit sourced from a skill-bundle KB.

## Effort estimate

- ASB-aware parser (~300 LOC): walks `skills/`, reads sidecars, validates schemas
- `skill_kb.json` writer (~80 LOC): in-place update with backup
- Per-chunk metadata extension (~50 LOC): plumb the schema fields through the chunker + storage layer
- MCP `ingest_asb_skills` tool (~80 LOC): thin wrapper over the parser + existing ingest
- Tests with the real MetLinkR fixture (~250 LOC)
- Total: ~750 LOC over ~3-4 tasks (parser, plumbing, MCP, tests). Single-day work after the parent skill-bundle ingest plan ships.

The parent plan's repo fetcher + code chunker + storage are the dependency; this addendum adds the ASB-shaped entry point on top.
