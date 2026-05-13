# Capsule + Multimodal RAG — design spec

**Date:** 2026-05-13
**Status:** approved (brainstorm), ready for implementation plan
**Companion plan:** `docs/superpowers/plans/2026-05-13-capsule-multimodal-rag.md` (to be written)

## Overview

Add an ASB-aligned **Capsule** layer to Perspicacité: a per-paper, on-disk artifact that bundles extracted figures, structured text blocks with provenance, and mined external-resource references. Use the capsule to drive a **multimodal RAG** path where retrieved chunks pull their referenced figures into the LLM call (vision-capable models) and into answer rendering (UI inline thumbnails). Mine and optionally fetch external resources (GitHub READMEs, scripts, notebooks, Zenodo metadata) so the same retrieval surface covers a paper's code and supplementary material.

Capsules are produced in a layout that is byte-compatible with AgenticScienceBuilder (ASB) capsules so the two tools can read each other's output without conversion.

## Goals

1. Extract figures + captions + panels from PDFs deterministically (no LLM) using ASB's heuristics (`figures.py` + `figure_context.py`).
2. Standardize per-block text provenance (canonical IMRaD section, page, char span, figure refs, resource refs) on every `DocumentChunk`.
3. Make figures first-class in chat answers: retrieved chunks pull their referenced figures into the LLM message (litellm `image_url` parts), and the UI renders inline thumbnails for the figure IDs the LLM mentions.
4. Mine external resources (GitHub repos, DOIs, Zenodo records, data-archive accessions) at capsule build, deterministically and free.
5. Fetch and ingest external code/docs/notebooks on demand, so the same RAG covers a paper's repo without manual download.
6. Produce a capsule directory that ASB can read and accept ASB-produced capsules into Perspicacité's KB via a shared schema.

## Non-goals (V1 / V2)

- Producing or running ASB-style **SciTaskCards**, claim ledgers, eval manifests, workflow scenarios.
- Adding any **LLM-based** extraction pass at capsule build. The only LLM in this design is the existing chat-time RAG model (now optionally multimodal).
- **CLIP-style image embeddings** indexed for retrieval (text-driven retrieval drives figure pickup via `figure_refs`).
- **RO-Crate-1.1 production** from Perspicacité (read-tolerant in V2; produce in V3).
- **Publisher-hosted SI PDFs** (per-publisher adapters out of scope).
- **Private GitHub repos** (auth flows out of scope).
- **Large data-archive blobs** — Zenodo (and similar) fetched as metadata-only by default; small text/code files with hard caps.

## Architecture mental model

```
Ingest a paper                  Chat with a KB
─────────────                   ─────────────
  PDF / Zotero / BibTeX            user query
        │                               │
        ▼                               ▼
  PDFParser (existing)            hybrid retrieval (existing)
        │                               │
        ▼                               ▼
  CapsuleBuilder (NEW)            chunks (with figure_refs/resource_refs)
   ├── extract_figures            │
   ├── split_sections             ▼
   ├── chunk per block            multimodal.build_messages (NEW, vendored)
   ├── mine_resources             ├── load_image_b64(fig)
   └── write capsule dir          ├── format_figures_block (prompt rule)
        │                         └── build_multimodal_messages (litellm)
        ▼                               │
  DocumentChunks (Chroma)               ▼
   + figure_refs                  vision-capable LLM
   + resource_refs                      │
   + source_section/page                ▼
   + char_span                    answer with figure_id tokens
                                        │
                                        ▼
                                  UI: thumbnail rewrite + click-to-expand
```

## Data model

Vendor-copied (with `"""Synced from AgenticScienceBuilder @ <sha>; keep API in sync."""` header):

**`src/perspicacite/pipeline/parsers/figures.py`** — verbatim mirror:
- `FigureRecord` dataclass: `source_pdf, page, index, width_px, height_px, caption, filename, ext, figure_number, subcomponent_label, bbox, panel_files`
- `RawFigure(record, image_bytes)`
- `extract_figures(pdf_path, min_px=100) -> list[RawFigure]` — PyMuPDF; size/aspect/area/byte filters identical to ASB (`_MIN_AREA_PX=50_000`, ratio ∈ [0.1, 10], min 1 KB after encoding); CMYK→RGB normalization
- `parse_figure_number`, `parse_panel_labels`, `assign_subcomponents` — same regexes
- Filename convention: `fig_p<page:03d>_i<idx:02d>.<ext>`
- Figure ID convention: `pdf_p<page>_i<idx>`
- **Out of V1:** `crop_panels()` — keep `panel_files` field on the record so V2 is additive.

**`src/perspicacite/pipeline/parsers/figure_context.py`** — verbatim mirror:
- `FigureContext` frozen dataclass: `figure_id, label, caption, source, panels, image_b64, filename`
- `load_image_b64(path)`, `supports_vision(model)`, `format_figures_block(figures)`, `build_multimodal_messages(prompt_text, figures, max_images)`
- `build_figure_context(*, pdf_figures, jats_figures)` — JATS branch retained, always called with `jats_figures=()` in V1 (no JATS parser yet)

**`src/perspicacite/pipeline/parsers/section_splitter.py`** — verbatim mirror:
- `SectionMap`, `split_sections(text) -> SectionMap`
- Canonical sections: `abstract | intro | methods | results | discussion | supplementary | other`
- Alias map and five heading regexes copied verbatim
- Table-block marker handling (`<!--TABLE_BEGIN-->`) preserved
- Fallback `full_text` bucket on miss

**`src/perspicacite/pipeline/external/accessions.py`** — verbatim mirror:
- `mine_accessions(text) -> list[dict]` — kinds: `massive, pride, metabolights, geo_series, bioproject, sra_run`
- Each match: `{kind, accession, url, evidence_span}`

**`src/perspicacite/pipeline/external/enrichment.py`** — partial mirror (extraction + fetch helpers):
- `extract_doi_candidates(text)`, `extract_github_repos(text)`, `extract_zenodo_record_ids(text)`
- `fetch_github_docs(owner, repo, ...)` — README + extra docs + notebooks + tree
- `_strip_notebook_outputs(raw)`
- `fetch_zenodo(record_id, ...)` — metadata only (matches ASB; no blob fetch)
- `fetch_crossref(doi, ...)`, `fetch_unpaywall(doi, ...)`, `fetch_pubmed`, `fetch_pmcid_for_doi`
- Cache layer mirroring ASB (`_cache_path`/`_cache_load`/`_cache_store`, `_http_get_*`) adapted to `httpx`
- **Extension allowlist widening (Perspicacité-specific):** add `.R`, `.r`, `.jl` to ASB's notebook/script set; add a hard `max_bytes_per_file` and `max_bytes_per_record` cap (see config)

## Storage layout

Per-paper capsule, mirroring ASB's directory shape:

```
<data_root>/capsules/<paper_id>/
├── metadata.json                  # capsule_version, producer, paper_id, title, authors, year, task_id (null for Perspicacité)
├── figures/
│   ├── index.json                 # list[FigureRecord]  (ASB schema)
│   └── fig_p003_i02.png           # ASB filename convention
├── text/
│   ├── blocks.jsonl               # one row per block: {block_id, page, bbox, type, content, section, figure_refs, table_refs}
│   │                              # block types emitted in V1: heading | paragraph | caption
│   │                              # reserved for V2 (schema only): table_latex | equation_latex
│   └── figure_mentions.jsonl      # one row per (block_id, figure_id) detected mention
├── resources.json                 # list[resource]  (V1 mining output)
├── external/                      # V2 — fetched resources
│   ├── github/{owner}__{repo}/
│   │   ├── README.md
│   │   ├── tree.json
│   │   ├── docs/<path>.md
│   │   ├── env/requirements.txt
│   │   ├── notebooks/<name>.ipynb # outputs stripped
│   │   ├── scripts/<name>.py
│   │   ├── scripts/<name>.R
│   │   └── data_manifest.json
│   ├── zenodo/{record_id}.json
│   ├── crossref/{doi_slug}.json
│   └── unpaywall/{doi_slug}.json
└── ro-crate-metadata.json         # V3 — optional, additive; readers tolerate absence
```

**`paper_id` convention** (shared with ASB): `doi:<doi>` if available, else `local:<sha256-of-bytes>`.

**`metadata.json`** schema (Capsule v0.1):

```json
{
  "capsule_version": "0.1",
  "producer": "perspicacite",
  "producer_version": "<perspicacite_version>",
  "built_at": "2026-05-13T20:00:00Z",
  "paper_id": "doi:10.1234/abc",
  "title": "...",
  "authors": [{"family": "Doe", "given": "Jane"}, ...],
  "year": 2025,
  "doi": "10.1234/abc",
  "source": "zotero" | "bibtex" | "doi" | "local",
  "task_id": null
}
```

**`resources.json`** schema (V1 mining output):

```json
[
  {
    "resource_id": "github:owner/repo",
    "kind": "github" | "doi" | "zenodo" | "massive" | "pride" | "metabolights" | "geo_series" | "bioproject" | "sra_run" | "url",
    "identifier": "owner/repo",
    "url": "https://github.com/owner/repo",
    "evidence_span": "…we deposited reads at PRIDE (PXD012345) and code at github.com/foo/bar…",
    "char_span": [12345, 12420],
    "page": 8,
    "block_id": "p008_b03"
  }
]
```

## ChunkMetadata extension (additive, all optional)

```python
class ChunkMetadata(BaseModel):
    # ... existing fields ...
    source_section: str | None = None          # canonical IMRaD label
    page: int | None = None                    # PDF page (1-indexed)
    char_span: tuple[int, int] | None = None   # char offsets in source
    figure_refs: list[str] = []                # figure_ids mentioned in this chunk
    table_refs: list[str] = []                 # table_ids mentioned in this chunk
    resource_refs: list[str] = []              # resource_ids mentioned in this chunk
    parent_paper_id: str | None = None         # set for external-content chunks
    is_external: bool = False                  # True for fetched repo/doc content
```

No breaking change; existing chunks just default to `None` / `[]` / `False`. Field names mirror ASB's `SourceAnchor` (flat rather than nested for query convenience).

## Text chunking pipeline

```
read_text(path) ──► split_sections ──► for each (section, text):
                                          chunk_document(section_text, content_type, language)
                                          ─► chunks
                                          tag chunks with:
                                            source_section = section
                                            page (from PDF parser block info, when available)
                                            char_span = verbatim find() in source
                                            figure_refs = regex_match("Fig\\.?\\s*N") → resolve via figures/index.json
                                            resource_refs = regex_match(...) → resolve via resources.json
```

**Verbatim guarantee:** `char_span` is computed by `str.find(chunk.text, source_text)` where `source_text` is the exact string the chunker received. Stored as `(start, end)` offsets into that string. `None` when the chunk text cannot be located verbatim (e.g., after content-type-specific normalization that mutates the chunk). Used for highlight-in-source UI and for downstream KG/claim work.

**`figure_refs` resolution:** in-text "Fig. N" mentions are matched against `figure_number` in `figures/index.json`. JATS-style `figure_id` (when present) wins. Multi-panel mentions ("Fig. 2A") attempt panel-label lookup.

## Capsule schema convergence (Perspicacité ↔ ASB)

The on-disk layout is byte-compatible across both producers. Capsule v0.1 contract:

| File | Required | Producer (today) | Reader (today) |
|---|---|---|---|
| `metadata.json` | yes | both | both |
| `figures/index.json` + image files | optional | both | both |
| `text/blocks.jsonl` | optional | Perspicacité | both (ASB later) |
| `resources.json` | optional | both | both |
| `ro-crate-metadata.json` | optional | ASB (P. in V3) | both, tolerated |
| `task_card.{md,json}` | optional | ASB | ASB; Perspicacité ignores |
| `evidence/source_snippets.md` | optional | ASB | both |
| `claims.jsonl` | optional | ASB | Perspicacité ingests as text in V3 |
| `workflow.yaml` | optional | ASB | Perspicacité ignores in V2 |
| `external/` subtree | optional | both | both |

**`CapsuleReader`** (`src/perspicacite/integrations/capsule_reader.py`) — single entry, dispatches by inspection:

```
ingest_capsule(capsule_dir, *, kb_name, app_state, registry, job_id) -> dict
  1. Read metadata.json → producer, paper_id, capsule_version
  2. Load figures/index.json (if present) — same schema regardless of producer
  3. Text source priority:
       a. text/blocks.jsonl                    (Perspicacité native)
       b. evidence/source_snippets.md           (ASB)
       c. <referenced source PDF, if present>   (re-parse)
  4. Chunk via existing chunk_document(), per section
  5. Tag chunks with parent_paper_id, producer, capsule_dir
  6. resources.json → resource_refs on chunks
  7. claims.jsonl → optional text ingest (off by default; V3 default-on)
  8. Embed + write to Chroma
```

`ingest_local_documents` routes to `CapsuleReader` when the input directory contains `metadata.json` with `capsule_version`; otherwise stays on the existing path.

## Multimodal RAG path

**At ingest:** chunks gain `figure_refs`. No change at chat time yet (Cycle A only).

**At chat time** (Cycle B):

1. Hybrid retrieval returns top-K chunks.
2. Collect `figure_refs` across the top-K. Dedup by `figure_id`. Record the highest-scoring source chunk per figure (used for retrieval-score tiebreak in step 3).
3. **Selection** (cap = `multimodal.max_images`, default 6):
   - Non-supplementary figures first (label not starting with "S").
   - Within tier, sort by source-chunk retrieval score (desc).
   - Final tiebreak by figure_number (asc).
   - Mirrors ASB's policy + retrieval-score awareness.
4. **Load**: `load_image_b64(<capsule>/figures/<filename>)`. Silently skip on missing file.
5. **Vision-model check**: `supports_vision(model)`. If False but flag requested, stderr warning + text-only fallback. (Mirrors ASB exactly.)
6. **Message construction**: `build_multimodal_messages(prompt_text, figures, max_images)` — exact ASB litellm shape.
7. **Prompt augmentation**: prepend `format_figures_block(figures)` to system prompt; add rule: *"When a finding rests on a figure, cite it by `figure_id` (e.g., `pdf_p3_i02`). Do not invent figure IDs."*
8. **Answer post-pass**: UI rewrites each `figure_id` token to an inline thumbnail (click-to-expand). `strip_unknown_figure_ids` filter removes hallucinated refs (also mirrors ASB).

**Per-mode wiring**: every RAG mode that emits a final user-facing LLM call (`basic`, `advanced`, `profound`, `agentic`, `literature_survey`, `contradiction`) gains a single hook: *if any retrieved chunk has `figure_refs` AND model supports vision AND `multimodal.enabled`, route through `multimodal.build_messages`.* One shared helper, six 2-line call-site changes.

## External resources (V1 + V2)

**V1 — mining at capsule build** (deterministic, no network, no LLM):
- Run accession + DOI + GitHub + Zenodo regexes against the full text.
- Emit `resources.json`. Chunks get `resource_refs`.

**V2 — fetch on demand** (network, no LLM):
- Vendor `fetch_github_docs`, `fetch_zenodo`, `fetch_crossref`, `fetch_unpaywall`, `fetch_pubmed`, `fetch_pmcid_for_doi`.
- For GitHub: `extra_docs=True` fetches README + docs/notebooks/scripts/env files + tree. Widen the extension filter to include `.R`/`.r`/`.jl`. Strip notebook outputs.
- For Zenodo: metadata only by default; optionally fetch small text/script files with hard caps (`max_bytes_per_file=500_000`, `max_bytes_per_record=5_000_000`); extension allowlist (`.md, .txt, .rst, .py, .R, .r, .jl, .ipynb, .yml, .yaml, .toml, .json, .csv`); no archive extraction.
- Cache layout: `<data_root>/cache/<api>/<query_hash>.json`. TTL default 30 days. `.extra_fetched` sentinel in GitHub repo dirs prevents duplicate calls.
- **Ingest**: fetched text-like files routed through the existing `ingest_local_documents` worker. Notebooks pre-processed (`_strip_notebook_outputs` → flat markdown with code fences). Chunks tagged `is_external=True`, `parent_paper_id`, `resource_id`.
- **Triggers**:
  - MCP: `fetch_paper_resources(paper_id, kinds=["github","zenodo"], ingest=True)`
  - CLI: `perspicacite fetch-resources --paper <id> [--ingest] [--include github,zenodo]`
  - UI: per-resource "Fetch & Ingest" button + per-paper "Fetch all" with JobRegistry SSE progress
- **Answer surfacing**: fetched chunks have `content_type="code"` / `language=<lang>`, so they render in answers as code fences with provenance "Paper P → Repo R → file path Z".

## Lifecycle, config, phasing

**Auto-build on ingest** (Cycle A): every paper ingest path (BibTeX, DOIs, local PDF, Zotero) calls `build_capsule(paper_id, pdf_path, kb_name)` after PDF download. Reuses existing `PDFParser`. Idempotent: no-op if `<capsule>/metadata.json` exists with `capsule_version ≥ config.capsule.min_version`. `--force` to override.

**Retro-build**:
- MCP: `build_capsule(paper_id)` and `build_capsules_for_kb(kb_name)`
- CLI: `perspicacite build-capsule --paper <id>` and `perspicacite build-capsules --kb <name>`
- UI: paper detail panel "Build capsule" button when missing; KB panel "Build all missing capsules" with JobRegistry SSE progress (same UX as Phase 2/3 ingest streams)

**Config:**

```yaml
capsule:
  enabled: true
  auto_build_on_ingest: true
  root: ./data/capsules
  min_version: "0.1"

multimodal:
  enabled: true
  max_images: 6
  vision_allowlist:
    - "anthropic/claude-"
    - "claude-"
    - "openai/gpt-4o"
    - "gpt-4o"

external_resources:
  mine: true                              # V1 — always-on
  fetch_on_demand: true                   # V2 — gated by user/MCP action
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

**Phasing** — three implementation cycles, each independently shippable. **Each cycle gets its own implementation plan and its own merge to main**; we do not write a single combined plan for all three. The cycles below are sized for one plan each (~20-26 tasks, matching the Phase 2 / Phase 3 precedent).

- **Cycle A — capsule core** (~25 tasks):
  - Vendor `figures.py`, `figure_context.py`, `section_splitter.py`.
  - Build `pipeline/capsule_builder.py` (figures + text/blocks + provenance + resources V1 mining).
  - Extend `ChunkMetadata` with `figure_refs`/`resource_refs`/`source_section`/`page`/`char_span`/`table_refs`/`parent_paper_id`/`is_external`.
  - Auto-build on ingest in all four paths (BibTeX, DOIs, local PDF, Zotero).
  - MCP: `build_capsule`, `build_capsules_for_kb`. CLI subcommands. UI buttons.
  - Tests + docs (MANUAL_QA, config.example.yml).
  - Net effect: capsules exist; chunks carry refs; no chat-time change yet.

- **Cycle B — multimodal RAG + capsule reader** (~22 tasks):
  - `rag/multimodal.py` (selection + message builder).
  - Per-mode wiring (6 modes × 1 hook).
  - UI thumbnail-rendering post-pass (+ `strip_unknown_figure_ids`).
  - `integrations/capsule_reader.py` for ASB-capsule ingest.
  - Shared-schema docs (`docs/capsule_schema.md`).
  - Tests + manual QA against a real ASB capsule.
  - Net effect: figures appear in answers; ASB capsules are first-class KB content.

- **Cycle C — external resources V1 + V2** (~20 tasks):
  - V1: `pipeline/external/accessions.py`, `enrichment.extract_*` mining wired into capsule builder.
  - V2: vendor `fetch_*` helpers; cache layer adapted to httpx; size guards for Zenodo; widened extension allowlist.
  - Ingest fetched files via existing `ingest_local_documents` (notebooks pre-processed).
  - MCP `fetch_paper_resources`; CLI `fetch-resources`; UI per-resource buttons + JobRegistry progress.
  - Tests + docs.
  - Net effect: external code/docs become first-class RAG content; chat answers cite repo files.

## Open questions

None blocking. Items deferred to later cycles are listed under Non-goals.

## References

- AgenticScienceBuilder repo: `~/git/AgenticScienceBuilder`
- ASB modules mirrored (commit at time of writing — verify `git rev-parse HEAD` in `~/git/AgenticScienceBuilder` before vendoring):
  - `src/agentic_science_builder/figures.py`
  - `src/agentic_science_builder/figure_context.py`
  - `src/agentic_science_builder/section_splitter.py`
  - `src/agentic_science_builder/accessions.py`
  - `src/agentic_science_builder/enrichment.py` (selected helpers)
- Perspicacité Phase 3 (local-docs cycle) — completed 2026-05-13, sets the precedent for chunking_dispatch and ingest worker patterns reused here.
