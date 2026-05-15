# ASB → Perspicacité Bundle Ingest — Design (Addendum)

**Status:** Design accepted 2026-05-15. Not yet implemented. Complements the broader [`2026-05-15-github-skill-bundle-ingest-design.md`](2026-05-15-github-skill-bundle-ingest-design.md) — that spec defines the generic GitHub-repo / skill-bundle ingest scaffolding (fetcher, chunker, KB plumbing); this addendum specifies the **ASB-output-aware** entry point and the **`skill_kb.json` round-trip contract** that pairs ASB and Perspicacité through a small JSON file.

## Why a separate addendum

The base skill-bundle ingest spec was written before we observed real ASB run outputs. ASB doesn't ship a generic `bundle.yml`; it ships **two parallel artifact streams worth indexing**:

- **Skills** — per-skill directories with prose body + rich JSON sidecars (`tools.json`, `environments.json`, `parameters.json`, `papers.json`, `links.json`, …) and an integration slot (`skill_kb.json`) waiting for Perspicacité to populate. Capability-level granularity.
- **Workflows** — end-to-end SciTask Cards under `cards/task_NNN.{md,json}` composing skills + tools + parameters + datasets, plus a `workflow_dag.json` connecting them. Task-level granularity ("how do I do X end-to-end").

Skills and workflows are both content-rich and address different retrieval intents (capability discovery vs. end-to-end recipes). Indexing both is necessary; capsules — per-task RO-Crate execution containers — are heavy and deferred to v2.

This addendum captures the ASB-specific schema and the wiring, without re-litigating the general github-fetcher / chunker layers from the parent spec.

## Division of labor

- **ASB produces** structured skill bundles and workflow cards. Per skill, it extracts: prose body (with frontmatter), tool references (`tools.json`), environment requirements (`environments.json`), parameters (`parameters.json`), backing papers (`papers.json`), external links (`links.json`), examples, failure modes, and ontology refs. Per workflow, it extracts a SciTask Card (`task_NNN.md` + `task_NNN.json`) composing skills, tools, inputs, outputs, parameters, methodology, and evaluation strategy. It also emits a top-level `workflow_dag.json` connecting cards. It does **not** fetch repos or perform code embedding — `skill_kb.json` explicitly notes the unfetched repo URLs as the integration handoff.

- **Perspicacité consumes** the bundle directory. **For each skill** it: (a) ingests backing papers via the existing `ingest_dois_into_kb` path, (b) fetches repos listed in `links.json` (category=`repo_github`) via the github fetcher from the parent spec, (c) chunks `skill.md` with markdown discipline, (d) chunks fetched code with the existing **code-aware chunker** and routes through `TypedEmbeddingProvider` (text chunks → `text-embedding-3-small`; code chunks → `mistral/codestral-embed`), (e) lifts ASB's structured sidecars into per-chunk metadata so the auto-KB-routing payload carries tool/env/parameter requirements alongside the answer, (f) writes the inventory of ingested chunks back to `skill_kb.json` so ASB can confirm the integration completed. **For each workflow card** it: (g) chunks `task_NNN.md` with markdown discipline → `text-embedding-3-small`, lifting the structured fields from `task_NNN.json` (skills_used, tools_used, parameters, inputs, outputs, evaluation_strategy, domain facets) into per-chunk metadata. **At bundle level** it: (h) stores `workflow_dag.json` as KB-level metadata so the auto-KB-routing payload can surface downstream-task hops on a workflow hit.

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
├── cards/                                                    # ★ WORKFLOWS (SciTask Cards) ★
│   ├── task_001.md, task_001.json                            # per-task: prose + structured
│   └── ...                                                   # one card per workflow node
├── workflow_dag.json                                         # ★ DAG over task_NNN nodes ★
├── capsules/                                                 # per-task RO-Crate execution (deferred)
├── package_index.json, enriched_index.json
└── cache/, ledger/, logs/, validation/
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

## Workflows (SciTask Cards)

In addition to skills, ASB emits **workflows**: end-to-end SciTask Cards under `{run_dir}/cards/` plus a DAG (`workflow_dag.json`) connecting them. Each card is a richly-structured scientific task that composes one or more skills, tools, parameters, inputs, and outputs into an executable recipe. Cards are the natural retrieval unit for queries like "how do I benchmark a metabolomics tool against manual curation" — they answer "do task X end-to-end" rather than "what does skill Y do in isolation."

### Why both skills and workflows

- A **skill** is "the capability" (e.g., `metabolite-identifier-mapping`) — answers "what does this technique do?"
- A **workflow card** is "the recipe" (e.g., `task_001: Map metabolite identifiers (HMDB, KEGG, ...) to RefMet across 10 datasets`) — answers "how do I run this end-to-end?"

Both are needed. Skills without workflows give capability discovery without composition; workflows without skills hide the building blocks behind each step. A workflow hit links *back* to the relevant skills (via `skills_used[]`), so the agent can drill from recipe → step → tool implementation.

### Card schema

Each card ships as a `.md`/`.json` pair:

**`cards/task_NNN.md`** — ~6KB human-readable card with sections: research question, connected finding, task description, inputs, expected outputs, landmark outputs, tools, **skills**, workflow description (numbered steps), parameters table (with units, page, source citation), available artifacts, domain knowledge, uncertainty notes, evidence snippets, evaluation strategy, review questions, methodology summary, workflow ports (inputs/outputs).

**`cards/task_NNN.json`** — structured form of the same content (the source of truth for metadata):

```json
{
  "article_type": "software-tool",
  "task_id": "task_001",
  "title": "Map metabolite identifiers (HMDB, KEGG, ...) to RefMet across 10 datasets",
  "domain": "mass-spectrometry / metabolomics",
  "subdomains": ["computational-metabolomics", "clinical-metabolomics"],
  "techniques": ["metabolite-identification", "database-annotation"],
  "primary_domain": "metabolomics",
  "subtask_categories": ["data-processing", "benchmark-evaluation"],
  "crossref_doi": "10.1021/acs.jproteome.4c01051",
  "github": "ncats/MetLinkR",
  "tools": ["MetLinkR", "R"],
  "skills": ["metabolite-identifier-mapping", "refmet-standardization", "..."],
  "data_in": [{"description": "file1: 542 metabolites with HMDB, KEGG, ...", "...": "..."}],
  "data_out": [{"description": "Per-dataset mapping rate table", "...": "..."}],
  "expected_outputs": ["mapping_rates.csv"],
  "landmark_outputs": ["metlinkr_output_file1.csv", "..."],
  "parameters": [{"name": "file1_metabolite_count", "value": "542", "units": "...", "source": "..."}],
  "domain_knowledge": ["MetLinkR queries RefMet API with priority order...", "..."],
  "evaluation_strategy": {
    "direct_checks": [{"description": "verify file 'mapping_rate_table.csv' exists", "method": "file_exists"}, "..."],
    "expert_review": ["Assess whether mapping rates are plausible...", "..."]
  },
  "methodology_summary": ["Load each dataset file...", "..."],
  "workflow_ports": {
    "inputs": [{"name": "file1", "description": "..."}, "..."],
    "outputs": [{"name": "mapping_rate_table", "description": "..."}]
  },
  "schema_version": "0.17.0"
}
```

Cross-references that make cards retrieval-valuable:

- `skills[]` — slugs into `skills/{slug}/` (the composition link)
- `tools[]` — names into `tools/{slug}.json` (the registry built by ASB)
- `domain`, `subdomains`, `techniques`, `subtask_categories` — facets for filtering
- `crossref_doi`, `github` — provenance back to source paper + canonical implementation

### `workflow_dag.json`

Top-level DAG:

```json
{
  "nodes": ["task_001", "task_002", "task_003", "task_004", "task_005", "task_006"],
  "edges": [["task_001", "task_002"], ["task_002", "task_003"], "..."]
}
```

Treated as **bundle-level metadata**: the DAG is stored on the KB description and surfaced via the auto-KB-routing response so a calling agent can see how cards relate (task_001 → task_002 → … ). v1 does not index the DAG itself as chunks; relationships are kept as structured metadata.

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
| `cards/task_NNN.md` | Markdown chunker; **text** content type | One workflow-card body per task → `text-embedding-3-small` chunks |
| `cards/task_NNN.json` | Per-chunk metadata (structured form) | `task_id`, `task_card_title`, `domain`, `subdomains`, `techniques`, `tools_used`, `skills_used`, `paper_doi`, `paper_github`, `inputs[]`, `expected_outputs[]`, `parameters[]`, `evaluation_strategy{}` |
| `workflow_dag.json` | **KB-level** metadata | `workflow_dag: {nodes, edges}` on KB description; not chunked |
| `tools/{slug}.json` | Per-chunk metadata (reverse-index lookup at workflow-chunk time) | Enriches `tools_used[]` with `canonical_url`, `evidence_spans`, `related_skills[]` |
| `capsules/` (ASB side) | **Defer** — heavy per-task RO-Crate execution containers | v1 ignores; v2 may merge with Perspicacité's own capsule format |

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
content_kind: str | None          # "skill_body" | "skill_example" | "skill_failure_mode"
                                  # | "skill_repo_code" | "workflow_card"
# --- skill-sourced fields ---
skill_id: str | None              # e.g. "cross-identifier-reconciliation"
skill_name: str | None
skill_description: str | None
edam_operation: str | None        # EDAM ontology URI
edam_topics: list[str] | None
tools: list[dict] | None          # per the tools.json schema above
environment: list[dict] | None    # per environments.json
parameters: list[dict] | None     # per parameters.json
when_to_use_negative: list[str] | None
# --- workflow-card-sourced fields ---
task_id: str | None               # e.g. "task_001"
task_card_title: str | None       # human-readable card title
domain: str | None                # e.g. "mass-spectrometry / metabolomics"
subdomains: list[str] | None
techniques: list[str] | None
subtask_categories: list[str] | None
tools_used: list[str] | None      # tool slugs the card references
skills_used: list[str] | None     # skill slugs the card composes
paper_doi: str | None             # crossref_doi of source paper
paper_github: str | None
inputs: list[dict] | None         # data_in[] from task_NNN.json
expected_outputs: list[str] | None
evaluation_strategy: dict | None  # direct_checks + expert_review
# --- common ---
asb_task_ids: list[str] | None    # provenance back to ASB extraction tasks
schema_version: str | None        # e.g. "0.2.0" — for forward-compat
```

`source` is set to a new `PaperSource.SKILL_BUNDLE` enum value (the migration pattern from 2026-05-15 already established the precedent for adding sources when the origin matters). Paper-construction sites that build chunks from `skill.md`, fetched repos, or `cards/task_NNN.md` all use `PaperSource.SKILL_BUNDLE` (the repo and workflow cards are *part of* the bundle's content from a query-time perspective). Paper-construction sites that build chunks from `papers.json` DOIs continue to use the unified pipeline's `PaperSource.OPENALEX` / `.CROSSREF` (they're real papers, not skills).

## Auto-KB-routing response payload

When the auto-KB-routing returns hits from a skill-bundle KB, the response gains `skill_metadata` and `workflow_metadata` blocks alongside the existing chunk text + citations. `skill_metadata` is populated when any hit has `content_kind=skill_*`; `workflow_metadata` is populated when any hit has `content_kind=workflow_card`:

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
  ],
  "workflow_metadata": [
    {
      "task_id": "task_001",
      "title": "Map metabolite identifiers (HMDB, KEGG, ...) to RefMet across 10 datasets",
      "domain": "mass-spectrometry / metabolomics",
      "skills_used": ["metabolite-identifier-mapping", "refmet-standardization", "..."],
      "tools_used": ["MetLinkR", "R"],
      "parameters": [{"name": "file1_metabolite_count", "value": "542", "units": ""}, "..."],
      "expected_outputs": ["mapping_rates.csv"],
      "evaluation_strategy": {"direct_checks": [...], "expert_review": [...]},
      "paper_doi": "10.1021/acs.jproteome.4c01051",
      "paper_github": "ncats/MetLinkR",
      "downstream_tasks": ["task_002"],   // derived from workflow_dag.json edges
      "upstream_tasks": []                // derived from workflow_dag.json edges
    }
  ]
}
```

The calling agent can then decide whether to **run** the skill (via an ASB MCP server, if available), **follow the recipe** (by retrieving each `skills_used` skill and the workflow's `evaluation_strategy`), or just **read** the description.

## CLI / MCP surface

Extend the `perspicacite ingest-skill-bundles` command from the parent spec to recognize ASB output, accepting the **run-dir root** (not just `skills/`) so it can also pick up `cards/` and `workflow_dag.json`:

```bash
# Auto-detects ASB by checking for skills/_index.json + (optionally) cards/ + workflow_dag.json
perspicacite ingest-asb-run /path/to/{run_dir}/
perspicacite ingest-asb-run /path/to/{run_dir}/ --kb-name metlinkr_bundle
perspicacite ingest-asb-run /path/to/{run_dir}/ --include skills,workflows   # default: both
perspicacite ingest-asb-run /path/to/{run_dir}/ --include skills              # skills only
perspicacite ingest-asb-run /path/to/{run_dir}/ --include workflows           # workflows only
perspicacite ingest-asb-run /path/to/{run_dir}/ --per-skill                   # one KB per skill (workflows always composite)
perspicacite ingest-asb-run /path/to/{run_dir}/ --composite                   # one KB for all (default)
```

ASB-mode behavior differs from generic-bundle mode only at the parser layer; downstream (chunker, embedder, storage) is shared.

MCP tool (parent spec adds `ingest_skill_bundle`; this addendum specifies the ASB-run entry point):

```python
@mcp.tool()
async def ingest_asb_run(
    asb_run_dir: str,                          # path to a run dir (must contain skills/ and/or cards/)
    kb_name: str | None = None,                # default: derive from run-dir name
    include: list[str] = ("skills", "workflows"),  # which artifact streams to ingest
    mode: str = "composite",                   # "composite" | "per-skill"
    update_skill_kb_json: bool = True,         # write back the integration seam (skills only)
) -> dict:
    """Ingest an Agent Skill Bundle run into a Perspicacité KB.

    Returns: {kb_names: [...], skills_ingested: int, workflows_ingested: int,
              repos_fetched: int, papers_ingested: int, failed: [...],
              total_chunks: int, workflow_dag: {nodes, edges} | None}.
    """
```

## Out of scope (deferred)

1. **ASB capsules** — per-task RO-Crate execution containers under `capsules/{paper}__task_NNN/` carrying artifacts, figures, evidence, eval results, RO-Crate metadata. Heavy and per-paper-execution-specific. Perspicacité has its own capsule format. v1 ignores ASB capsules; v2 may align the formats so they share one capsule store. **Cards (`cards/task_NNN.{md,json}`) ARE in scope as workflows.**
2. **Bidirectional federation** — ASB invoking Perspicacité as a knowledge backend (the inverse direction). Out of scope unless ASB requests it.
3. **Live ASB MCP server in this repo** — Perspicacité's MCP server can federate to an ASB MCP server when one exists, but should not host one. Pair, don't absorb.
4. **`PaperSource.SKILL_BUNDLE` enum migration** — add to `papers.py`, extend the invariant test, deferred until the ingest plan executes.
5. **`schema_version` upgrade handling** — ASB ships `0.2.0` for skill bundles, `0.17.0` for cards, and `0.1.0` for `skill_kb.json`. The ingest path reads all three; if a future ASB ships `1.0.0` with incompatible fields, the parser fails closed rather than silently dropping data. Concrete migration logic deferred.
6. **Workflow DAG traversal as chunks** — `workflow_dag.json` is stored as KB-level structured metadata and surfaced via the response payload. Indexing edges as queryable graph nodes is deferred to a future graph-RAG extension.

## Testing

- **Unit (offline, fixture-driven):** copy a real `audit_2026-05-15_pdf2/metlinkr_full/` subset into `tests/fixtures/asb/`: one skill directory (`skills/cross-identifier-reconciliation/`), 2-3 cards (`cards/task_001.{md,json}`, `cards/task_002.{md,json}`), and `workflow_dag.json`. Tests cover skill parser, card parser, metadata extraction, chunk construction, `skill_kb.json` round-trip, and DAG passthrough. Use real ASB output, not synthetic — keeps the parser honest against schema drift.
- **Integration:** ingest the full MetLinkR run end-to-end against a temporary Chroma collection; assert skill counts, workflow-card counts, tool-metadata presence on a sampled skill chunk, `skills_used` presence on a sampled workflow chunk, and `skill_kb.json` was updated for each skill.
- **Regression:** the auto-KB-routing pin tests (when written) confirm `skill_metadata` is in the response payload for any hit with `content_kind=skill_*`, and `workflow_metadata` is in the response payload for any hit with `content_kind=workflow_card`.

## Effort estimate

- ASB-aware skill parser (~300 LOC): walks `skills/`, reads sidecars, validates schemas
- ASB-aware workflow-card parser (~200 LOC): walks `cards/`, parses `.json` + chunks `.md`, joins with `workflow_dag.json`
- `skill_kb.json` writer (~80 LOC): in-place update with backup
- Per-chunk metadata extension (~80 LOC): plumb the skill + workflow schema fields through the chunker + storage layer
- KB-level metadata extension (~40 LOC): store `workflow_dag` on KB description; surface in auto-KB-routing response
- MCP `ingest_asb_run` tool (~100 LOC): thin wrapper over the parser + existing ingest
- Tests with the real MetLinkR fixture (~350 LOC)
- Total: ~1150 LOC over ~5-6 tasks (skill parser, card parser, plumbing, KB-meta, MCP, tests). 1-2 days work after the parent skill-bundle ingest plan ships.

The parent plan's repo fetcher + code chunker + storage are the dependency; this addendum adds the ASB-shaped entry point + workflow-card pathway on top.
