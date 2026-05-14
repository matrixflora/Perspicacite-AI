# Multimodal PDF visual extraction — design spec

**Wave 4.1 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Extract figures, tables (as markdown), and formulas from
PDF pages by rendering each page and asking a multimodal LLM what's
on it. Returns structured `VisualExtract` records that the ingest
pipeline can turn into `DocumentChunk`s with `content_type=figure /
table / formula`.

## Why this approach

Inspired by the `agentic_science_builder` pattern: render → vision
LLM → structured JSON. Compared to layout-based extractors
(PyMuPDF figure detection, Camelot for tables), the multimodal-LLM
path:

- Handles modern scientific PDFs with mixed layouts.
- Returns *semantic* descriptions of figures (what the figure shows),
  not just bounding boxes.
- Produces tables in markdown form, ready for retrieval.
- Reuses the LLM routing we already have (anthropic, openai, agent_cli).

Costs are bounded by the Wave 2.1 disk cache (re-running an ingest
hits cache, not the API) and the Wave 2.4 budget tracker.

## Scope of v1

In scope:

- `MultimodalPDFExtractor` standalone class.
- PDF page rendering via PyMuPDF.
- Structured-JSON prompt + parser.
- Returns `list[VisualExtract]` (figure / table / formula).
- Per-page caching via the existing LLM disk cache.
- Two new chunk content types: `"figure"`, `"table"`. Existing
  `ChunkMetadata.content_type` already supports arbitrary strings.

Out of scope (deliberate followups):

- Wiring into `ingest_dois_into_kb` / the rest of the ingest pipeline.
  The extractor returns data; integration is a separate change.
- Formula transcription as LaTeX. The prompt asks for it but the
  v1 contract treats formulas as best-effort.
- Re-running on edited pages / incremental updates.
- OCR of scanned PDFs (text rendering passes through fine; pure-image
  scans are a separate quality concern).
- Image-storage decision: today we keep the markdown / description
  only. Raw PNG bytes are not retained — they're regenerated on
  demand from the source PDF.

## Architecture

```
MultimodalPDFExtractor(llm_client, model, provider)
  ├── extract_visuals(pdf_path, paper_id, page_range=None) -> list[VisualExtract]
  │     └── for each page in pdf:
  │           ├── render_png(page) -> bytes
  │           ├── llm_client.complete(messages=[{
  │           │       "role": "user",
  │           │       "content": [
  │           │           {"type": "image", "source": {...png base64...}},
  │           │           {"type": "text", "text": EXTRACTION_PROMPT},
  │           │       ]
  │           │   }], ...)
  │           └── parse_json_response() -> list[VisualExtract]
  └── to_chunks(extracts, paper_id) -> list[DocumentChunk]
```

The `extract_visuals` method does the heavy lifting and returns a
flat list. `to_chunks` is a small helper that wraps each
`VisualExtract` in a `DocumentChunk` with the right content_type +
page_number metadata.

## Prompt design

```text
You are extracting figures, tables, and formulas from a single page
of a scientific PDF. The image shows the page rendered at 150 DPI.

Return a JSON object with this exact shape:

{
  "visuals": [
    {
      "kind": "figure" | "table" | "formula",
      "page": <page-number-as-int>,
      "label": "<figure/table number e.g. 'Figure 3' or 'Table 1'>",
      "caption": "<full caption text>",
      "content": "<markdown for tables, plain-text description for figures, latex for formulas>"
    },
    ...
  ]
}

Rules:
- "figure": include a 1-3 sentence semantic description of what the
  figure shows (axes, trends, comparison being made). Don't include
  the caption text in "content" — that goes in "caption".
- "table": render as a markdown table in "content". Preserve column
  headers and row labels exactly. If the table is huge, summarise
  the structure rather than transcribe row-by-row.
- "formula": only include numbered display equations (Eq. 1, etc.) —
  not inline math. "content" is best-effort LaTeX.
- Return {"visuals": []} when the page has no figures, tables, or
  numbered formulas.
- Output valid JSON only — no markdown fences, no commentary.
```

## Components

| File | Responsibility |
|---|---|
| `src/perspicacite/pipeline/parsers/multimodal.py` (new) | `MultimodalPDFExtractor`, `VisualExtract` dataclass, PNG rendering, JSON parsing. |
| `src/perspicacite/config/schema.py` (modify) | Add `kb.visual_extraction_enabled: bool = False`, `kb.visual_extraction_model: str = "claude-sonnet-4-5"`, `kb.visual_extraction_provider: str = "anthropic"`, `kb.visual_extraction_dpi: int = 150`. |
| `tests/unit/test_multimodal_extractor.py` (new) | Mocked LLM response → parsed VisualExtracts; malformed JSON → empty list; page range; to_chunks shape. ~8 tests. |
| `docs/multimodal-extraction-2026-05-14.md` (new) | Operator guide. |

## VisualExtract shape

```python
@dataclass
class VisualExtract:
    kind: Literal["figure", "table", "formula"]
    page: int
    label: str          # "Figure 3", "Table 1", "Eq. 2"
    caption: str
    content: str        # markdown / description / latex
```

## DocumentChunk mapping

For each `VisualExtract`, emit one chunk with:

- `id` = `f"{paper_id}__visual_{page}_{idx}"`
- `text` = `f"{label}\n{caption}\n\n{content}"` (so retrieval matches
  on caption + content together)
- `metadata.paper_id` = `paper_id`
- `metadata.chunk_index` = continuous after the text chunks
- `metadata.page_number` = `extract.page`
- `metadata.content_type` = `extract.kind`
- `metadata.section` = `extract.label` (e.g. "Figure 3" — useful for
  retrieval display)

## Error handling

- PDF render failure for a page → log warning, skip that page,
  continue.
- LLM response not parseable as JSON → log warning, return `[]` for
  that page (don't fail the whole ingest).
- Empty `visuals` list from a page → silently skip.
- All pages failing → propagate the last exception so the caller
  knows the extractor itself is broken.

## Caching

The Wave 2.1 disk cache transparently keys on
`(provider, model, messages, ...)`. Since `messages` contains the
base64 PNG bytes, re-running the same PDF page yields the same key
and serves from cache. **Bonus**: switching from a 150 DPI render to
a 200 DPI render busts the cache automatically (different bytes).

## Budget interaction

Each `extract_visuals` call records token usage into the budget
tracker (Wave 2.4). A typical scientific PDF has 6-20 pages; at
Sonnet rates this is ~$0.02-$0.10 per paper for visuals. For
unattended runs, set `llm.budget.max_usd` higher than you would for
text-only ingest.

## Test plan

- `test_visual_extract_dataclass_shape`
- `test_extractor_parses_well_formed_json`
- `test_extractor_returns_empty_on_malformed_json`
- `test_extractor_filters_empty_visuals_array`
- `test_to_chunks_builds_correct_metadata`
- `test_to_chunks_chunk_index_continues_from_offset`
- `test_extract_visuals_respects_page_range`
- `test_render_failure_for_one_page_doesnt_kill_run`
- `test_image_content_block_for_anthropic`

## Followups

- Wire into ingest pipelines (`Paper.full_text` → text chunks +
  `MultimodalPDFExtractor` → visual chunks → one final chunk list).
- Two-pass refinement (low-resolution sweep + high-resolution on
  pages with detected visuals).
- Per-page section-context prefix in the prompt for better captions.
- Configurable prompt + structured-output schema validation.
- OCR fallback for scanned PDFs.
- Side-channel image storage (write the rendered PNG to
  `data/visuals/<paper_id>__page<N>.png` for downstream display).
