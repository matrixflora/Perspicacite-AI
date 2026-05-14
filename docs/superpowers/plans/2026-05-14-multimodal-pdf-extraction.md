# Multimodal PDF extraction — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Standalone `MultimodalPDFExtractor` that renders PDF pages
and asks a vision-capable LLM what figures / tables / formulas appear.

**Spec:** `docs/superpowers/specs/2026-05-14-multimodal-pdf-extraction-design.md`

---

## Task 1: Config fields

**Files:**
- Modify: `src/perspicacite/config/schema.py` (`KnowledgeBaseConfig`)
- Test: `tests/unit/test_config_visual_extraction_fields.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config_visual_extraction_fields.py
"""Tests for visual-extraction fields on KnowledgeBaseConfig (Wave 4.1)."""
from perspicacite.config.schema import KnowledgeBaseConfig


def test_visual_defaults_off():
    kb = KnowledgeBaseConfig()
    assert kb.visual_extraction_enabled is False
    assert kb.visual_extraction_model == "claude-sonnet-4-5"
    assert kb.visual_extraction_provider == "anthropic"
    assert kb.visual_extraction_dpi == 150


def test_visual_can_enable():
    kb = KnowledgeBaseConfig(
        visual_extraction_enabled=True,
        visual_extraction_dpi=200,
        visual_extraction_model="gpt-4o",
        visual_extraction_provider="openai",
    )
    assert kb.visual_extraction_enabled is True
    assert kb.visual_extraction_dpi == 200
    assert kb.visual_extraction_model == "gpt-4o"
    assert kb.visual_extraction_provider == "openai"


def test_visual_dpi_bounded():
    """DPI must be at least 72 (legible) and at most 300 (image-size sanity)."""
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(visual_extraction_dpi=30)
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(visual_extraction_dpi=600)


import pytest  # noqa
```

(Move `import pytest` to the top — it's there for the validation test.)

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_config_visual_extraction_fields.py -v
```

- [ ] **Step 3: Add the fields**

In `src/perspicacite/config/schema.py`, in `KnowledgeBaseConfig`,
after the checkpoint_dir field (Wave 3.3), add:

```python
    # ---- multimodal visual extraction (Wave 4.1) -------------------
    # Render each PDF page and ask a vision-capable LLM to extract
    # figures / tables / formulas. Off by default — opt-in safety.
    # See docs/superpowers/specs/2026-05-14-multimodal-pdf-extraction-design.md.
    visual_extraction_enabled: bool = Field(
        default=False,
        description=(
            "When True, run MultimodalPDFExtractor on each ingested PDF "
            "to produce figure / table / formula chunks. Default off."
        ),
    )
    visual_extraction_model: str = Field(
        default="claude-sonnet-4-5",
        description="Vision-capable model used for extraction.",
    )
    visual_extraction_provider: str = Field(
        default="anthropic",
        description=(
            "Provider for the extraction model. Must support image "
            "content blocks (anthropic, openai, gemini, ...)."
        ),
    )
    visual_extraction_dpi: int = Field(
        default=150,
        ge=72,
        le=300,
        description=(
            "Page render DPI. Higher = clearer image, more tokens. "
            "150 is a good default for typical scientific PDFs."
        ),
    )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_config_visual_extraction_fields.py -v
pytest tests/integration/test_config_audit.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py \
        tests/unit/test_config_visual_extraction_fields.py
git commit -m "feat(config): visual_extraction_* fields on KnowledgeBaseConfig (Wave 4.1)"
```

---

## Task 2: MultimodalPDFExtractor class

**Files:**
- Create: `src/perspicacite/pipeline/parsers/multimodal.py`
- Test: `tests/unit/test_multimodal_extractor.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_multimodal_extractor.py
"""Tests for MultimodalPDFExtractor (Wave 4.1)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.parsers.multimodal import (
    MultimodalPDFExtractor,
    VisualExtract,
)


def _well_formed_response(page: int = 1) -> str:
    return json.dumps({
        "visuals": [
            {
                "kind": "figure",
                "page": page,
                "label": "Figure 3",
                "caption": "Comparison of methods A and B.",
                "content": "Bar chart showing method A outperforms B by 15%.",
            },
            {
                "kind": "table",
                "page": page,
                "label": "Table 1",
                "caption": "Summary statistics.",
                "content": "| Method | Acc |\n|---|---|\n| A | 0.91 |\n| B | 0.78 |",
            },
        ]
    })


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=_well_formed_response())
    return llm


def test_visual_extract_dataclass_shape():
    v = VisualExtract(
        kind="figure", page=2, label="Figure 1",
        caption="A figure.", content="Bars.",
    )
    assert v.kind == "figure"
    assert v.page == 2


def test_parses_well_formed_response(mock_llm):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="claude-sonnet-4-5", provider="anthropic",
    )
    visuals = extractor._parse_response(_well_formed_response())
    assert len(visuals) == 2
    assert visuals[0].kind == "figure"
    assert visuals[0].label == "Figure 3"
    assert visuals[1].kind == "table"
    assert "Acc" in visuals[1].content


def test_returns_empty_on_malformed_json(mock_llm):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    assert extractor._parse_response("not-json {{{") == []


def test_returns_empty_on_missing_visuals_key(mock_llm):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    assert extractor._parse_response('{"results": []}') == []


def test_filters_invalid_kind(mock_llm):
    """Unknown 'kind' values are dropped, not raised."""
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    bad = json.dumps({
        "visuals": [
            {"kind": "diagram", "page": 1, "label": "X",
             "caption": "y", "content": "z"},
            {"kind": "figure", "page": 1, "label": "Figure 1",
             "caption": "ok", "content": "ok"},
        ]
    })
    out = extractor._parse_response(bad)
    assert len(out) == 1
    assert out[0].kind == "figure"


def test_to_chunks_builds_correct_metadata(mock_llm):
    from perspicacite.models.documents import DocumentChunk
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    visuals = [
        VisualExtract(kind="figure", page=2, label="Figure 3",
                      caption="cap", content="desc"),
        VisualExtract(kind="table", page=3, label="Table 1",
                      caption="cap2", content="| x |"),
    ]
    chunks = extractor.to_chunks(visuals, paper_id="paper-1", chunk_index_offset=10)
    assert len(chunks) == 2
    assert all(isinstance(c, DocumentChunk) for c in chunks)
    assert chunks[0].metadata.paper_id == "paper-1"
    assert chunks[0].metadata.chunk_index == 10
    assert chunks[0].metadata.page_number == 2
    assert chunks[0].metadata.content_type == "figure"
    assert chunks[0].metadata.section == "Figure 3"
    assert "Figure 3" in chunks[0].text
    assert "cap" in chunks[0].text
    assert "desc" in chunks[0].text
    assert chunks[1].metadata.chunk_index == 11
    assert chunks[1].metadata.content_type == "table"


@pytest.mark.asyncio
async def test_render_failure_for_one_page_doesnt_kill_run(mock_llm, tmp_path):
    """If PyMuPDF chokes on page 2 of 3, we still extract pages 1 and 3."""
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )

    call_count = {"n": 0}

    def fake_render(page_num: int) -> bytes:
        call_count["n"] += 1
        if page_num == 2:
            raise RuntimeError("page 2 corrupt")
        return b"fake-png-bytes"

    with patch.object(extractor, "_render_png", side_effect=fake_render), \
         patch.object(extractor, "_page_count", return_value=3):
        result = await extractor.extract_visuals(
            pdf_path=tmp_path / "fake.pdf",
            paper_id="p1",
        )
    # 2 pages succeeded, each yielding 2 visuals from the mocked LLM.
    assert len(result) == 4


@pytest.mark.asyncio
async def test_image_content_block_anthropic_shape(mock_llm, tmp_path):
    """The messages we send must be a list with an image-type block."""
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="claude-sonnet-4-5", provider="anthropic",
    )
    with patch.object(extractor, "_render_png", return_value=b"png-bytes"), \
         patch.object(extractor, "_page_count", return_value=1):
        await extractor.extract_visuals(
            pdf_path=tmp_path / "fake.pdf",
            paper_id="p1",
        )
    args, kwargs = mock_llm.complete.call_args
    messages = kwargs.get("messages") or args[0]
    # Expect a user message with a list-content holding an image block.
    user_msg = next(m for m in messages if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    types_present = [b.get("type") for b in user_msg["content"]]
    assert "image" in types_present
    assert "text" in types_present


@pytest.mark.asyncio
async def test_extract_visuals_respects_page_range(mock_llm, tmp_path):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    with patch.object(extractor, "_render_png", return_value=b"png"), \
         patch.object(extractor, "_page_count", return_value=10):
        await extractor.extract_visuals(
            pdf_path=tmp_path / "fake.pdf",
            paper_id="p1",
            page_range=(3, 5),  # pages 3, 4, 5 (1-indexed inclusive)
        )
    # 3 LLM calls — one per page in the range.
    assert mock_llm.complete.call_count == 3
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_multimodal_extractor.py -v
```

- [ ] **Step 3: Implement the extractor**

Create `src/perspicacite/pipeline/parsers/multimodal.py`:

```python
"""Multimodal PDF visual extractor — figures, tables, formulas (Wave 4.1).

Renders each PDF page as PNG via PyMuPDF, sends to a vision-capable
LLM, and parses the structured JSON response into VisualExtract
records.

See docs/superpowers/specs/2026-05-14-multimodal-pdf-extraction-design.md.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.parsers.multimodal")


_VALID_KINDS: frozenset[str] = frozenset({"figure", "table", "formula"})


_EXTRACTION_PROMPT = """\
You are extracting figures, tables, and formulas from a single page of a
scientific PDF. The image shows the page rendered at the requested DPI.

Return a JSON object with this exact shape:

{
  "visuals": [
    {
      "kind": "figure" | "table" | "formula",
      "page": <page-number-as-int>,
      "label": "<figure/table number e.g. 'Figure 3' or 'Table 1'>",
      "caption": "<full caption text>",
      "content": "<markdown for tables, plain-text description for figures, latex for formulas>"
    }
  ]
}

Rules:
- "figure": include a 1-3 sentence semantic description of what the figure
  shows (axes, trends, comparison). Don't repeat the caption text in
  "content" — that goes in "caption".
- "table": render as a markdown table in "content". Preserve column headers
  and row labels exactly. For huge tables, summarise the structure.
- "formula": only include numbered display equations. "content" is
  best-effort LaTeX.
- Return {"visuals": []} when the page has no figures, tables, or numbered
  formulas.
- Output VALID JSON only. No markdown fences, no commentary.
"""


@dataclass
class VisualExtract:
    """One figure/table/formula extracted from a PDF page."""

    kind: Literal["figure", "table", "formula"]
    page: int
    label: str
    caption: str
    content: str


class MultimodalPDFExtractor:
    """Render PDF pages and ask a multimodal LLM what's on them.

    Stateless — one instance can process many PDFs. The LLM disk cache
    (Wave 2.1) keys on the rendered PNG bytes, so re-running on the
    same PDF / DPI hits cache.
    """

    def __init__(
        self,
        *,
        llm_client: Any,
        model: str,
        provider: str,
        dpi: int = 150,
    ):
        self.llm_client = llm_client
        self.model = model
        self.provider = provider
        self.dpi = dpi

    # ---- internals: rendering -----------------------------------------

    def _open_pdf(self, pdf_path: Path) -> Any:
        try:
            import fitz
        except ImportError as exc:
            raise ImportError(
                "PyMuPDF is required for multimodal extraction. "
                "Install with: pip install pymupdf"
            ) from exc
        return fitz.open(str(pdf_path))

    def _page_count(self, pdf_path: Path) -> int:
        doc = self._open_pdf(pdf_path)
        try:
            return doc.page_count
        finally:
            doc.close()

    def _render_png(self, page_num: int, pdf_path: Path | None = None) -> bytes:
        """Render a single page (1-indexed) as PNG bytes.

        ``pdf_path`` is optional in production (we open it once per
        extract_visuals call), but the test suite mocks this method
        directly and doesn't pass it. The signature accommodates both.
        """
        if pdf_path is None:
            raise ValueError("pdf_path required when not mocked")
        doc = self._open_pdf(pdf_path)
        try:
            page = doc.load_page(page_num - 1)  # fitz is 0-indexed
            pix = page.get_pixmap(dpi=self.dpi)
            return pix.tobytes("png")
        finally:
            doc.close()

    # ---- internals: response parsing ----------------------------------

    def _parse_response(self, raw: str) -> list[VisualExtract]:
        """Parse the LLM's JSON response into VisualExtract records.

        Robust against malformed JSON, missing keys, and unknown
        ``kind`` values. Returns ``[]`` on any parse failure rather
        than raising — one bad page shouldn't kill a multi-page run.
        """
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("multimodal_parse_json_decode_failed",
                           preview=raw[:200])
            return []
        if not isinstance(payload, dict):
            return []
        visuals_raw = payload.get("visuals")
        if not isinstance(visuals_raw, list):
            return []
        out: list[VisualExtract] = []
        for item in visuals_raw:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind not in _VALID_KINDS:
                continue
            try:
                out.append(VisualExtract(
                    kind=kind,
                    page=int(item.get("page", 0)),
                    label=str(item.get("label", "")),
                    caption=str(item.get("caption", "")),
                    content=str(item.get("content", "")),
                ))
            except (TypeError, ValueError):
                continue
        return out

    # ---- public API ---------------------------------------------------

    async def extract_visuals(
        self,
        *,
        pdf_path: Path,
        paper_id: str,
        page_range: tuple[int, int] | None = None,
    ) -> list[VisualExtract]:
        """Extract all visuals from a PDF.

        ``page_range`` is ``(start, end)`` inclusive, 1-indexed.
        ``None`` means all pages.

        Per-page failures are logged + skipped — the run continues on
        the next page so a single corrupt page doesn't lose the rest.
        """
        n = self._page_count(pdf_path)
        if page_range is None:
            page_nums = list(range(1, n + 1))
        else:
            start, end = page_range
            page_nums = list(range(max(1, start), min(n, end) + 1))

        all_visuals: list[VisualExtract] = []
        for page_num in page_nums:
            try:
                png_bytes = self._render_png(page_num, pdf_path=pdf_path)
            except Exception as e:
                logger.warning(
                    "multimodal_render_failed",
                    paper_id=paper_id, page=page_num, error=str(e),
                )
                continue
            page_visuals = await self._extract_one_page(
                png_bytes=png_bytes,
                page_num=page_num,
                paper_id=paper_id,
            )
            all_visuals.extend(page_visuals)
        return all_visuals

    async def _extract_one_page(
        self,
        *,
        png_bytes: bytes,
        page_num: int,
        paper_id: str,
    ) -> list[VisualExtract]:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        # Anthropic-style image content block; OpenAI accepts a similar
        # shape via litellm's drop_params. For agent_cli providers
        # without multimodal support, this call will fail — caller is
        # expected to route to a vision-capable provider.
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            _EXTRACTION_PROMPT
                            + f"\n\n(This is page {page_num} of paper "
                            + f"`{paper_id}`.)"
                        ),
                    },
                ],
            },
        ]
        try:
            response = await self.llm_client.complete(
                messages=messages,
                model=self.model,
                provider=self.provider,
                temperature=0.0,
                max_tokens=4000,
                stage="multimodal_extraction",
            )
        except Exception as e:
            logger.warning(
                "multimodal_llm_failed",
                paper_id=paper_id, page=page_num,
                error=str(e), error_type=type(e).__name__,
            )
            return []
        return self._parse_response(response)

    def to_chunks(
        self,
        visuals: list[VisualExtract],
        *,
        paper_id: str,
        chunk_index_offset: int = 0,
    ) -> list[Any]:
        """Wrap each VisualExtract in a DocumentChunk.

        ``chunk_index_offset`` lets callers continue the index numbering
        after the text chunks in the same paper.
        """
        from perspicacite.models.documents import ChunkMetadata, DocumentChunk
        chunks: list[Any] = []
        for i, v in enumerate(visuals):
            idx = chunk_index_offset + i
            text = f"{v.label}\n{v.caption}\n\n{v.content}".strip()
            md = ChunkMetadata(
                paper_id=paper_id,
                chunk_index=idx,
                section=v.label,
                page_number=v.page,
                content_type=v.kind,
            )
            chunks.append(DocumentChunk(
                id=f"{paper_id}__visual_{v.page}_{i}",
                text=text,
                metadata=md,
            ))
        return chunks
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_multimodal_extractor.py -v
```

Expected: 9 PASSED.

Also re-run the broader suite to make sure nothing regressed:

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/parsers/multimodal.py \
        tests/unit/test_multimodal_extractor.py
git commit -m "feat(multimodal): MultimodalPDFExtractor — vision LLM extracts figures/tables (Wave 4.1)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/multimodal-extraction-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
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
- `text` = `f"{label}\\n{caption}\\n\\n{content}"`

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
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/multimodal-extraction-*.md` to `.gitignore` after
`!docs/error-modes-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/multimodal-extraction-2026-05-14.md .gitignore
git commit -m "docs(multimodal): operator guide (Wave 4.1)"
```

---

## Done

After Task 3:

- Four new config fields on `KnowledgeBaseConfig`.
- New `MultimodalPDFExtractor` module (~200 LoC).
- 13 new tests covering parsing, error handling, page range, content
  block shape, and chunk wrapping.
- Operator doc landed.
- Ingest wiring is a documented follow-up sub-project.
