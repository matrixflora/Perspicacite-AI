# Multimodal PDF extraction — operator guide (2026-05-14)

Wave 4.1 of the framework-hardening roadmap. Vision-LLM extraction
of figures, tables, and formulas from PDF pages.

## What it does

`MultimodalPDFExtractor` renders each PDF page (PyMuPDF, 150 DPI by
default), encodes as PNG, and asks a vision-capable LLM to return a
JSON list of figures / tables / formulas on that page. Output is
wrapped as `DocumentChunk`s with:

- `content_type` ∈ `{"figure", "table", "formula"}`
- `page_number` set to the source page
- `section` = the figure/table label (e.g. `"Figure 3"`)
- `text` = `f"{label}\n{caption}\n\n{content}"`

## Config

```yaml
kb:
  visual_extraction_enabled: false           # opt-in
  visual_extraction_model: claude-sonnet-4-5  # vision-capable
  visual_extraction_provider: anthropic
  visual_extraction_dpi: 150                  # 72–300
```

## API

```python
from perspicacite.pipeline.parsers.multimodal import MultimodalPDFExtractor

extractor = MultimodalPDFExtractor(
    llm_client=client,
    model="claude-sonnet-4-5",
    provider="anthropic",
    dpi=150,
)
visuals = await extractor.extract_visuals(
    pdf_path=Path("paper.pdf"),
    paper_id="10.1234/example",
    page_range=None,           # or (start, end), 1-indexed inclusive
)
chunks = extractor.to_chunks(
    visuals,
    paper_id="10.1234/example",
    chunk_index_offset=len(text_chunks),
)
# Hand chunks to your KB / vector store.
```

## Behaviour contract

- Per-page LLM failure (rate limit / parse error) → logged, page
  skipped, run continues.
- Malformed JSON → returns `[]` for that page (no exception).
- Empty `visuals: []` from a page → silently skipped (no chunks).
- Unknown `kind` values in the response → dropped (not raised).

## Cost & caching

Each page = 1 LLM call. A 10-page paper at Sonnet rates is
~$0.05-$0.10 in visuals (varies with image complexity and response
length). The Wave 2.1 disk cache keys on the rendered PNG bytes, so:

- Re-running the same PDF/DPI hits cache.
- Changing DPI busts the cache automatically.
- Switching models invalidates only that model's entries.

The Wave 2.4 budget tracker accumulates these costs. For unattended
visual ingest, raise `llm.budget.max_usd` above your text-only budget.

## Provider notes

- **anthropic**: native multimodal support via Claude Sonnet/Opus.
  This is the recommended path.
- **openai**: `gpt-4o` and successors support image inputs through
  LiteLLM. The same content-block shape works.
- **gemini**: `gemini-1.5-pro` / `gemini-2.0-flash` accept images.
- **agent_cli (Claude Code / Codex)**: image inputs aren't surfaced
  through the CLI's `-p` mode today. Use a direct-API provider for
  visual extraction. Wave 4.1 doesn't try to plumb images through
  agent_cli — it's a documented limitation.

## What is NOT done

- **Ingest wiring**: this PR ships the extractor only. The decision
  on how to merge visual chunks with the existing text-chunk
  pipeline (alongside? extended chunk list? separate index?) is a
  follow-up sub-project. Today's `ingest_dois_into_kb` doesn't call
  the extractor; manual orchestration is required.
- **Formula transcription quality**: best-effort LaTeX. Display
  equations only. Inline math is not extracted.
- **OCR**: scanned PDFs (pure-image pages) may yield poor results —
  the prompt expects the LLM to read text from the rendered image,
  but performance varies by model.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/parsers/multimodal.py` | `MultimodalPDFExtractor`, `VisualExtract`, prompt |
| `src/perspicacite/config/schema.py` | `visual_extraction_*` fields on `KnowledgeBaseConfig` |

## Followups

- Wire the extractor into `ingest_dois_into_kb` and other ingest
  paths (separate sub-project).
- Two-pass refinement (low-resolution sweep + high-resolution on
  pages with detected visuals).
- Side-channel image storage (write rendered PNGs to disk for
  downstream display).
- OCR fallback for scanned PDFs.
- Per-section context prefix in the prompt for better captions.
