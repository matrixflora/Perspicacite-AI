# Capsule on-disk schema (v0.1)

> **Status:** stable for Cycle B. Shared between **Perspicacité-AI** and **AgenticScienceBuilder (ASB)** — capsules produced by either tool are readable by both.

## What is a capsule?

A capsule is a per-paper, on-disk artifact bundling:
- Extracted figures + captions (deterministic, no LLM)
- Structured text blocks with provenance (section, page, char span, figure refs)
- Mined external-resource references (DOIs, GitHub repos, Zenodo records, data-archive accessions)

Capsules drive Perspicacité's multimodal RAG: retrieved chunks pull their referenced figures into the LLM call and into the answer UI.

## Directory layout

```
<data_root>/capsules/<paper_id_safe>/
├── metadata.json                  # capsule_version, producer, paper_id, title, …
├── figures/
│   ├── index.json                 # list[FigureRecord]
│   └── fig_p<page:03d>_i<idx:02d>.<ext>
├── text/
│   ├── blocks.jsonl               # one row per block
│   └── figure_mentions.jsonl      # one row per (block_id, figure_id) detected mention
├── resources.json                 # list[resource]  (V1 mining output)
├── supplementary/                 # SI manifest (PMC OA papers only)
│   └── index.json                 # {"items": [{label, caption, url, mime_type, …}], "source": "pmc_jats"}
├── external/                      # V2 — fetched resources (Cycle C)
│   ├── github/{owner}__{repo}/
│   ├── zenodo/{record_id}.json
│   ├── crossref/{doi_slug}.json
│   └── unpaywall/{doi_slug}.json
└── ro-crate-metadata.json         # optional, additive; readers tolerate absence
```

### `paper_id` convention

- DOI-bearing papers: `doi:<doi>` (e.g., `doi:10.1234/abc.def`).
- Other papers (local PDFs, no DOI): `local:<sha256-of-bytes>`.

### `paper_id_safe` (filesystem)

Sanitize by replacing `:` with `_` and `/` with `__`:

| paper_id | paper_id_safe |
|---|---|
| `doi:10.1234/abc` | `doi_10.1234__abc` |
| `local:abc123` | `local_abc123` |

### Figure filename + id conventions

- Filename: `fig_p<page:03d>_i<idx:02d>.<ext>` (e.g., `fig_p003_i02.png`).
- Figure id: `pdf_p<page>_i<idx>` (e.g., `pdf_p3_i2`). No zero-padding in the id.

The id is the LLM-facing token. The filename is the disk-facing artifact.

## File-by-file schema

### `metadata.json` (required)

```json
{
  "capsule_version": "0.1",
  "producer": "perspicacite",
  "producer_version": "<version>",
  "built_at": "2026-05-14T20:00:00Z",
  "paper_id": "doi:10.1234/abc",
  "title": "...",
  "authors": [{"family": "Doe", "given": "Jane"}, ...],
  "year": 2025,
  "doi": "10.1234/abc",
  "source": "zotero" | "bibtex" | "doi" | "local",
  "task_id": null
}
```

- `producer` ∈ {`perspicacite`, `asb`}.
- `task_id` is null when produced by Perspicacité; ASB sets it for SciTaskCard-driven builds.

### `figures/index.json` (optional but present whenever the PDF has figures)

```json
[
  {
    "source_pdf": "/path/to/paper.pdf",
    "page": 3,
    "index": 2,
    "width_px": 1200,
    "height_px": 800,
    "caption": "Figure 1. Schematic of the method.",
    "filename": "fig_p003_i02.png",
    "ext": "png",
    "figure_number": "1",
    "subcomponent_label": "",
    "bbox": [x0, y0, x1, y1],
    "panel_files": []
  }
]
```

Both producers use the **ASB vendored** `figures.py` extraction with identical filters (`_MIN_AREA_PX=50_000`, aspect-ratio ∈ [0.1, 10], min 1 KB after encoding, CMYK→RGB normalization).

### `text/blocks.jsonl` (optional; Perspicacité produces, ASB reads)

One JSON object per line:

```json
{
  "block_id": "p003_b02",
  "page": 3,
  "bbox": [x0, y0, x1, y1],
  "type": "paragraph" | "heading" | "caption",
  "content": "<block text>",
  "section": "abstract" | "intro" | "methods" | "results" | "discussion" | "supplementary" | "other",
  "figure_refs": ["pdf_p3_i2", ...],
  "table_refs": []
}
```

Reserved V2 block types: `table_latex`, `equation_latex`.

### `text/figure_mentions.jsonl` (optional)

```json
{"block_id": "p003_b02", "figure_id": "pdf_p3_i2"}
```

One row per detected in-text mention. Useful for building the `figure_refs` field on chunks at chunking time.

### `resources.json` (optional)

```json
[
  {
    "resource_id": "github:owner/repo",
    "kind": "github" | "doi" | "zenodo" | "massive" | "pride" | "metabolights" | "geo_series" | "bioproject" | "sra_run" | "url",
    "identifier": "owner/repo",
    "url": "https://github.com/owner/repo",
    "evidence_span": "...",
    "char_span": [12345, 12420],
    "page": 8,
    "block_id": "p008_b03"
  }
]
```

V1 (Cycle A) emits the `kind`, `identifier`, `url`, and `evidence_span` fields. The `char_span`, `page`, `block_id` fields are filled when block-level provenance is available.

### `external/` (Cycle C)

When `external_resources.fetch_on_demand` is enabled and the user fetches resources:

- `external/github/{owner}__{repo}/README.md`, `tree.json`, `docs/<path>.md`, `env/<file>`, `notebooks/<name>.ipynb`, `scripts/<name>.{py,R,r,jl}`, `data_manifest.json`.
- `external/zenodo/{record_id}.json` — metadata only (small text/code files with hard caps).
- `external/crossref/{doi_slug}.json`, `external/unpaywall/{doi_slug}.json` — citation lookup caches.

Notebook outputs are stripped (`_strip_notebook_outputs`).

### `ro-crate-metadata.json` (optional, additive)

Produced today only by ASB. Readers tolerate absence.

## Cross-producer compatibility (v0.1)

| File | Producer (today) | Reader (today) |
|---|---|---|
| `metadata.json` | both | both |
| `figures/index.json` + images | both | both |
| `text/blocks.jsonl` | Perspicacité | both (ASB later) |
| `text/figure_mentions.jsonl` | Perspicacité | both (ASB later) |
| `resources.json` | both | both |
| `ro-crate-metadata.json` | ASB | both, tolerated |
| `task_card.{md,json}` | ASB | ASB; Perspicacité ignores |
| `evidence/source_snippets.md` | ASB | both |
| `claims.jsonl` | ASB | Perspicacité ingests as text in V3 |
| `workflow.yaml` | ASB | Perspicacité ignores in V2 |
| `external/` subtree | both | both |

### `CapsuleReader` text-source priority (Cycle B)

When ingesting an ASB-shaped capsule, Perspicacité uses the first available:
1. `text/blocks.jsonl` (native; preserves section + figure_refs).
2. `evidence/source_snippets.md` (ASB-native; ingested as a single `other` section).
3. Neither → ingest yields zero chunks (logged warning).

## Linked specs

- `specs/2026-05-13-capsule-multimodal-rag-design.md` — full design spec for Cycles A + B + C.
- `plans/2026-05-13-capsule-cycle-a-core.md` — Cycle A plan (capsule core).
- `plans/2026-05-14-capsule-cycle-b-multimodal.md` — Cycle B plan (multimodal RAG + CapsuleReader).
