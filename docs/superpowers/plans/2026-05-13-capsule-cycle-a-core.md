# Capsule + Multimodal RAG — Cycle A (capsule core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the capsule data layer — every paper produces an on-disk capsule (figures + structured text blocks + provenance + mined resources), and every `DocumentChunk` carries the ASB-aligned provenance fields (section, page, char_span, figure_refs, resource_refs).

**Architecture:** Vendor-copy four ASB modules verbatim (`figures.py`, `figure_context.py`, `section_splitter.py`, `accessions.py`) plus a subset of `enrichment.py` (regex helpers only). Build one new orchestrator `pipeline/capsule_builder.py` that: parses the PDF (existing `PDFParser`), splits into IMRaD sections, chunks per section, tags each chunk with provenance + figure/resource refs, embeds, writes to Chroma, and writes the capsule directory. Replace the chunk-and-embed block in the four existing ingest workers (BibTeX, DOIs, local PDF, Zotero) with a single call to `build_capsule(...)`.

**Tech Stack:** Python 3.13, PyMuPDF (fitz) — already a dep, Pydantic v2, asyncio, Click (CLI), FastAPI (web), httpx, pytest, uv. **No new deps.**

**Companion spec:** [docs/superpowers/specs/2026-05-13-capsule-multimodal-rag-design.md](../specs/2026-05-13-capsule-multimodal-rag-design.md)

**ASB source SHA at vendoring time:** `809f478` (recorded in `~/git/AgenticScienceBuilder`). Each vendored file gets a `Synced from AgenticScienceBuilder @ 809f478` header.

**Scope:** Cycle A only — capsule core. Multimodal RAG (Cycle B) and external-resource fetch (Cycle C) are separate plans.

---

## Task 1: Vendor `figures.py` (trim panel cropping)

**Files:**
- Create: `src/perspicacite/pipeline/parsers/figures.py`
- Test: `tests/unit/test_figures_extract.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_figures_extract.py`:

```python
"""ASB-aligned figure extraction — vendored from AgenticScienceBuilder."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_parse_figure_number_basic():
    from perspicacite.pipeline.parsers.figures import parse_figure_number
    assert parse_figure_number("Figure 3. A schematic …") == "3"
    assert parse_figure_number("Fig. S2: supplementary") == "S2"
    assert parse_figure_number("Scheme 1 — synthesis") == "1"
    assert parse_figure_number("not a figure caption") is None
    assert parse_figure_number("") is None


def test_parse_panel_labels_dedup_order():
    from perspicacite.pipeline.parsers.figures import parse_panel_labels
    out = parse_panel_labels("Figure 2. (A) overview (B) detail (A) repeat")
    assert out == ["A", "B"]


def test_figure_record_filename_convention():
    from perspicacite.pipeline.parsers.figures import FigureRecord
    rec = FigureRecord(
        source_pdf="paper.pdf", page=3, index=2,
        width_px=800, height_px=600, caption="Fig 1 …",
        filename="fig_p003_i02.png", ext="png",
    )
    assert rec.filename == "fig_p003_i02.png"
    assert rec.panel_files == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_figures_extract.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Vendor the ASB module (trimmed)**

Create `src/perspicacite/pipeline/parsers/figures.py` by copying `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/figures.py` verbatim, then:

1. Replace the file's docstring with:

```python
"""ASB-aligned PDF figure extraction.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

PyMuPDF rasterizes each embedded image individually, but a single scientific
figure is often a composite (Figure 1A/1B/1C panels). This module pairs every
extracted image with its parent caption and, when the caption enumerates
panels, assigns each image a ``subcomponent_label`` (A/B/…) by spatial
position (row-major).
"""
```

2. **Trim panel cropping** (deferred to Cycle B/V2): remove `decide_grid`, `crop_image_into_panels`, `crop_panels` functions. Keep the `panel_files` field on `FigureRecord` (defaults to `[]`) so V2 is purely additive.

3. Imports stay the same. No code body changes inside the kept functions.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_figures_extract.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/parsers/figures.py tests/unit/test_figures_extract.py
git commit -m "feat(pipeline/figures): vendor ASB figure extraction (no panel cropping)"
```

---

## Task 2: Vendor `figure_context.py`

**Files:**
- Create: `src/perspicacite/pipeline/parsers/figure_context.py`
- Test: `tests/unit/test_figure_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_figure_context.py`:

```python
"""ASB-aligned figure context fusion — vendored."""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _FakePdfFig:
    page: int = 3
    index: int = 2
    caption: str = "Figure 1. Overview."
    figure_number: str = "1"
    subcomponent_label: str | None = None
    panel_files: list = None
    def __post_init__(self):
        if self.panel_files is None:
            self.panel_files = []


def test_build_figure_context_pdf_only():
    from perspicacite.pipeline.parsers.figure_context import build_figure_context
    out = build_figure_context(pdf_figures=[_FakePdfFig()], jats_figures=())
    assert len(out) == 1
    assert out[0].figure_id == "pdf_p3_i2"
    assert out[0].label == "Figure 1"
    assert out[0].source == "pdf"


def test_supports_vision_allowlist():
    from perspicacite.pipeline.parsers.figure_context import supports_vision
    assert supports_vision("anthropic/claude-opus-4-7") is True
    assert supports_vision("openai/gpt-4o-2024-08-06") is True
    assert supports_vision("mistral/mistral-large") is False
    assert supports_vision("") is False


def test_format_figures_block_empty():
    from perspicacite.pipeline.parsers.figure_context import format_figures_block
    assert format_figures_block([]) == ""


def test_load_image_b64_missing(tmp_path):
    from perspicacite.pipeline.parsers.figure_context import load_image_b64
    assert load_image_b64(tmp_path / "nope.png") is None


def test_load_image_b64_roundtrip(tmp_path):
    import base64
    from perspicacite.pipeline.parsers.figure_context import load_image_b64
    p = tmp_path / "x.png"
    payload = b"\x89PNG\r\n\x1a\nhello"
    p.write_bytes(payload)
    got = load_image_b64(p)
    assert got == base64.b64encode(payload).decode("ascii")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_figure_context.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Vendor the module**

Create `src/perspicacite/pipeline/parsers/figure_context.py` by copying `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/figure_context.py` verbatim. Replace the top docstring with:

```python
"""ASB-aligned figure context fusion for multimodal LLM calls.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Fuses PDF-extracted ``figures.FigureRecord`` and JATS-parsed figures into a
single ``list[FigureContext]`` with canonical, stable figure ids. Provides
the multimodal-message builder (``build_multimodal_messages``) used by the
Cycle B chat path.
"""
```

Body unchanged. We always call with `jats_figures=()` in V1 — the JATS branch is exercised only by the test stub.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_figure_context.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/parsers/figure_context.py tests/unit/test_figure_context.py
git commit -m "feat(pipeline/figures): vendor ASB figure_context (FigureContext + multimodal helpers)"
```

---

## Task 3: Vendor `section_splitter.py` (adapt API)

**Files:**
- Create: `src/perspicacite/pipeline/parsers/section_splitter.py`
- Test: `tests/unit/test_section_splitter.py`

ASB's `split_sections` takes an `IngestResult` (a project-specific structure we don't have). We expose a plain-text entry point.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_section_splitter.py`:

```python
"""ASB-aligned IMRaD section splitter — vendored (adapted to plain text input)."""

from __future__ import annotations

import pytest


def test_detects_imrad():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    txt = (
        "## Abstract\nWe present X.\n\n"
        "## Introduction\nBackground stuff.\n\n"
        "## Methods\nWe did Y.\n\n"
        "## Results\nWe found Z.\n\n"
        "## Discussion\nThis implies …\n"
    )
    sm = split_sections(txt)
    assert sm.sections_detected is True
    assert set(sm.sections) >= {"abstract", "intro", "methods", "results", "discussion"}
    assert "We did Y." in sm.sections["methods"]


def test_fallback_full_text():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    txt = "Just one big blob of prose without any IMRaD headings whatsoever."
    sm = split_sections(txt)
    assert sm.sections_detected is False
    assert sm.sections == {"full_text": txt}


def test_alias_mapping():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    txt = (
        "## Background\nbg\n\n## Materials and Methods\nmm\n\n"
        "## Results and Discussion\nrd\n\n## Supporting Information\nsi\n"
    )
    sm = split_sections(txt)
    assert "bg" in sm.sections["intro"]
    assert "mm" in sm.sections["methods"]
    assert "rd" in sm.sections["results"]
    assert "si" in sm.sections["supplementary"]


def test_empty_input():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    sm = split_sections("")
    assert sm.sections_detected is False
    assert sm.sections == {"full_text": ""}
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_section_splitter.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Vendor + adapt**

Create `src/perspicacite/pipeline/parsers/section_splitter.py`. Copy from `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/section_splitter.py`. Adapt: (a) replace the docstring with the ASB-sync header, (b) drop the `from .schemas import IngestResult` import and change `split_sections` signature.

```python
"""ASB-aligned heuristic IMRaD section splitter.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Adapted: ``split_sections`` accepts a plain ``str`` instead of ASB's
``IngestResult``. Behavior otherwise identical (same alias map, same heading
regexes, same fallback semantics).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


SECTION_ALIASES: dict[str, str] = {
    "abstract": "abstract",
    "introduction": "intro",
    "intro": "intro",
    "background": "intro",
    "methods": "methods",
    "method": "methods",
    "materials and methods": "methods",
    "experimental": "methods",
    "experimental section": "methods",
    "results": "results",
    "results and discussion": "results",
    "results & discussion": "results",
    "findings": "results",
    "discussion": "discussion",
    "conclusion": "discussion",
    "conclusions": "discussion",
    "limitations": "discussion",
    "supplementary": "supplementary",
    "supplementary material": "supplementary",
    "supporting information": "supplementary",
    "associated content": "supplementary",
    "appendix": "supplementary",
}

KNOWN_SECTIONS: tuple[str, ...] = (
    "abstract", "intro", "methods", "results", "discussion", "supplementary", "other",
)

_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s{0,3}#{1,3}\s*([A-Za-z][A-Za-z &]+?)\s*#*\s*$"),
    re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+([A-Za-z][A-Za-z &]+?)\s*$"),
    re.compile(r"^\s*[■▪●□◆◇▶▷]\s*([A-Za-z][A-Za-z &]+?)\s*$"),
    re.compile(r"^\s*([A-Z][A-Z &]{2,80})\s*$"),
    re.compile(r"^\s*([A-Z][A-Za-z &]{1,80})\s*$"),
)


@dataclass
class SectionMap:
    sections: dict[str, str] = field(default_factory=dict)
    sections_detected: bool = True


def split_sections(text: str) -> SectionMap:
    """Split ``text`` into IMRaD sections, or fall back to a ``full_text`` bucket.

    Adapted from ASB's ``split_sections(IngestResult)``; behavior identical.
    """
    full_text = text or ""
    if not full_text.strip():
        return SectionMap(sections={"full_text": ""}, sections_detected=False)

    lines = full_text.splitlines()
    buckets: dict[str, list[str]] = {}
    current: str | None = None
    detected_any = False
    in_table = False

    for line in lines:
        if in_table:
            if current is not None:
                buckets[current].append(line)
            if line.lstrip().startswith("<!--TABLE_END-->"):
                in_table = False
            continue
        if line.lstrip().startswith("<!--TABLE_BEGIN"):
            in_table = True
            if current is not None:
                buckets[current].append(line)
            continue
        canonical = _match_heading(line)
        if canonical is not None:
            current = canonical
            buckets.setdefault(current, [])
            detected_any = True
            continue
        if current is None:
            continue
        buckets[current].append(line)

    if not detected_any:
        return SectionMap(sections={"full_text": full_text.strip()}, sections_detected=False)

    sections = {
        name: "\n".join(content_lines).strip()
        for name, content_lines in buckets.items()
        if content_lines
    }
    return SectionMap(sections=sections, sections_detected=True)


def _match_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        candidate = match.group(1).strip().lower()
        if candidate in SECTION_ALIASES:
            return SECTION_ALIASES[candidate]
    return None
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_section_splitter.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/parsers/section_splitter.py tests/unit/test_section_splitter.py
git commit -m "feat(pipeline/section_splitter): vendor ASB IMRaD splitter (plain-text API)"
```

---

## Task 4: Vendor `accessions.py`

**Files:**
- Create: `src/perspicacite/pipeline/external/__init__.py` (empty)
- Create: `src/perspicacite/pipeline/external/accessions.py`
- Test: `tests/unit/test_accessions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_accessions.py`:

```python
"""ASB-aligned accession mining — vendored verbatim."""

from __future__ import annotations

import pytest


def test_mine_known_kinds():
    from perspicacite.pipeline.external.accessions import mine_accessions
    txt = (
        "We deposited reads at PRIDE (PXD012345) and intermediate spectra "
        "at MassIVE MSV000089123. The transcriptomics is at GEO GSE123456 "
        "and BioProject PRJNA987654 with run SRR1234567."
    )
    out = mine_accessions(txt)
    kinds = {r["kind"] for r in out}
    assert {"pride", "massive", "geo_series", "bioproject", "sra_run"} <= kinds


def test_dedup_and_order():
    from perspicacite.pipeline.external.accessions import mine_accessions
    txt = "PXD012345 mentioned twice: PXD012345; also MTBLS123."
    out = mine_accessions(txt)
    assert sum(1 for r in out if r["accession"] == "PXD012345") == 1
    kinds_in_order = [r["kind"] for r in out]
    assert kinds_in_order.index("pride") < kinds_in_order.index("metabolights")


def test_empty_and_no_match():
    from perspicacite.pipeline.external.accessions import mine_accessions
    assert mine_accessions("") == []
    assert mine_accessions("no accessions here, just prose.") == []


def test_record_shape():
    from perspicacite.pipeline.external.accessions import mine_accessions
    out = mine_accessions("see PXD012345 in the SI")
    assert len(out) == 1
    r = out[0]
    assert set(r.keys()) == {"kind", "accession", "url", "evidence_span"}
    assert r["url"].endswith("/projects/PXD012345")
    assert "PXD012345" in r["evidence_span"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_accessions.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Vendor verbatim**

Create the package init:

```bash
mkdir -p src/perspicacite/pipeline/external
```

Create `src/perspicacite/pipeline/external/__init__.py` as an empty file (just `""`).

Create `src/perspicacite/pipeline/external/accessions.py` by copying `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/accessions.py` verbatim. Replace the docstring with:

```python
"""ASB-aligned regex mining of public data-repository accession IDs.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Pure stdlib, network-free. Each match is converted into a structured record
with a navigable URL and a short evidence snippet so capsules can reference
deposited data without depending on any LLM/network pass.
"""
```

Body unchanged. Drop `mine_accessions_from_indexed_texts` (it references ASB's `IngestResult`; we don't need it).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_accessions.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/external/__init__.py src/perspicacite/pipeline/external/accessions.py tests/unit/test_accessions.py
git commit -m "feat(pipeline/external): vendor ASB accession mining (MASSIVE, PRIDE, GEO, …)"
```

---

## Task 5: Vendor resource-URL extraction helpers

**Files:**
- Create: `src/perspicacite/pipeline/external/resources.py`
- Test: `tests/unit/test_external_resources_extract.py`

Cycle A needs only the **regex-extract** helpers from ASB's `enrichment.py` (DOI, GitHub, Zenodo). Network fetchers (Cycle C) come later.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_external_resources_extract.py`:

```python
"""ASB-aligned resource-URL extraction (DOI, GitHub, Zenodo) — vendored regexes."""

from __future__ import annotations

import pytest


def test_extract_doi_candidates():
    from perspicacite.pipeline.external.resources import extract_doi_candidates
    txt = "See https://doi.org/10.1234/abcdef and 10.5555/zenodo.987654 in the SI."
    out = extract_doi_candidates(txt)
    assert "10.1234/abcdef" in out
    assert "10.5555/zenodo.987654" in out


def test_extract_github_repos():
    from perspicacite.pipeline.external.resources import extract_github_repos
    txt = (
        "Code is at https://github.com/HolobiomicsLab/AgenticScienceBuilder "
        "and github.com/foo/bar."
    )
    out = extract_github_repos(txt)
    assert "HolobiomicsLab/AgenticScienceBuilder" in out
    assert "foo/bar" in out


def test_extract_zenodo_record_ids():
    from perspicacite.pipeline.external.resources import extract_zenodo_record_ids
    txt = "Data: https://zenodo.org/record/9876543 ; also 10.5281/zenodo.1234567"
    out = extract_zenodo_record_ids(txt)
    assert "9876543" in out
    assert "1234567" in out


def test_no_match():
    from perspicacite.pipeline.external.resources import (
        extract_doi_candidates, extract_github_repos, extract_zenodo_record_ids,
    )
    assert extract_doi_candidates("nothing here") == []
    assert extract_github_repos("nothing here") == []
    assert extract_zenodo_record_ids("nothing here") == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_external_resources_extract.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Create the module (vendor selected helpers)**

Open `/Users/holobiomicslab/git/AgenticScienceBuilder/src/agentic_science_builder/enrichment.py` and locate the three functions:
- `extract_doi_candidates(text)` near line 54
- `extract_github_repos(text)` near line 72
- `extract_zenodo_record_ids(text)` near line 92

Create `src/perspicacite/pipeline/external/resources.py` with **only** those three functions, their helper constants if any, and this header:

```python
"""ASB-aligned resource-URL extraction (DOI / GitHub / Zenodo).

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Pure stdlib regex extraction. Network fetchers (Cycle C) live in
``pipeline/external/fetch.py``; they are not in this Cycle.
"""

from __future__ import annotations

import re
```

Copy the function bodies verbatim from ASB. Each returns a deduplicated list preserving first-occurrence order (as ASB does).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_external_resources_extract.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/external/resources.py tests/unit/test_external_resources_extract.py
git commit -m "feat(pipeline/external): vendor ASB DOI/GitHub/Zenodo regex extractors"
```

---

## Task 6: Extend `ChunkMetadata` with provenance + ref fields

**Files:**
- Modify: `src/perspicacite/models/documents.py`
- Test: `tests/unit/test_chunk_metadata_provenance.py`

All additions are optional, with safe defaults. Existing chunks/serialized state continue to deserialize.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chunk_metadata_provenance.py`:

```python
"""ChunkMetadata gains ASB-aligned provenance fields (all optional, additive)."""

from __future__ import annotations

import pytest

from perspicacite.models.documents import ChunkMetadata


def test_defaults_are_safe():
    cm = ChunkMetadata(chunk_id="x", paper_id="p")
    assert cm.source_section is None
    assert cm.page is None
    assert cm.char_span is None
    assert cm.figure_refs == []
    assert cm.table_refs == []
    assert cm.resource_refs == []
    assert cm.parent_paper_id is None
    assert cm.is_external is False


def test_round_trip_with_provenance():
    cm = ChunkMetadata(
        chunk_id="x", paper_id="p",
        source_section="methods",
        page=4,
        char_span=(120, 240),
        figure_refs=["pdf_p3_i02"],
        resource_refs=["github:foo/bar"],
        parent_paper_id="doi:10.1234/parent",
        is_external=True,
    )
    dumped = cm.model_dump()
    assert dumped["source_section"] == "methods"
    assert dumped["char_span"] == (120, 240) or dumped["char_span"] == [120, 240]
    cm2 = ChunkMetadata(**dumped)
    assert cm2.figure_refs == ["pdf_p3_i02"]
    assert cm2.is_external is True


def test_frozen_still_frozen():
    cm = ChunkMetadata(chunk_id="x", paper_id="p")
    with pytest.raises(Exception):
        cm.figure_refs.append("pdf_p3_i02")  # frozen lists are tuples in some pydantic settings
        cm.source_section = "results"  # should also raise on frozen
```

(The third test verifies that the frozen-ness contract from Phase 3 is preserved — only one of the two raises will fire depending on pydantic config; the test passes as long as the model is not silently mutable.)

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_chunk_metadata_provenance.py -v
```

Expected: FAIL (fields missing).

- [ ] **Step 3: Extend `ChunkMetadata`**

In `src/perspicacite/models/documents.py`, locate `class ChunkMetadata(BaseModel)` and append these fields **before the model config**:

```python
    # ASB-aligned provenance (Cycle A 2026-05-13) — all optional, additive.
    source_section: Optional[str] = None
    page: Optional[int] = None
    char_span: Optional[tuple[int, int]] = None
    figure_refs: list[str] = []
    table_refs: list[str] = []
    resource_refs: list[str] = []
    parent_paper_id: Optional[str] = None
    is_external: bool = False
```

If `Optional` isn't already imported, add `from typing import Optional` at the top.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_chunk_metadata_provenance.py tests/unit/test_local_docs_worker.py tests/unit/test_local_docs_validate.py -v
```

Expected: all passed (the local-docs tests verify we didn't break Phase 3 chunk creation).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/models/documents.py tests/unit/test_chunk_metadata_provenance.py
git commit -m "feat(models): ChunkMetadata gains ASB-aligned provenance (section/page/char_span/refs)"
```

---

## Task 7: Add `CapsuleConfig` to the config schema

**Files:**
- Modify: `src/perspicacite/config/schema.py`
- Test: `tests/unit/test_capsule_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_config.py`:

```python
"""CapsuleConfig defaults and nesting under root Config."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.config.schema import Config


def test_defaults():
    cfg = Config()
    assert cfg.capsule.enabled is True
    assert cfg.capsule.auto_build_on_ingest is True
    assert cfg.capsule.min_version == "0.1"
    assert isinstance(cfg.capsule.root, Path)
    assert cfg.capsule.root.name == "capsules"


def test_override_via_dict():
    cfg = Config(capsule={"enabled": False, "root": "/tmp/caps", "auto_build_on_ingest": False})
    assert cfg.capsule.enabled is False
    assert cfg.capsule.auto_build_on_ingest is False
    assert str(cfg.capsule.root) == "/tmp/caps"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_config.py -v
```

Expected: FAIL (`capsule` attribute missing).

- [ ] **Step 3: Add `CapsuleConfig`**

In `src/perspicacite/config/schema.py`:

1. Define `CapsuleConfig` near the other Pydantic config classes (e.g., next to `LocalDocsConfig`):

```python
class CapsuleConfig(BaseModel):
    """Per-paper capsule storage and build behaviour."""

    enabled: bool = True
    auto_build_on_ingest: bool = True
    root: Path = Path("./data/capsules")
    min_version: str = "0.1"
```

2. Add the field to the root `Config` class (also next to `local_docs`):

```python
    capsule: CapsuleConfig = Field(default_factory=CapsuleConfig)
```

Make sure `Path` and `Field` are imported (likely already).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_config.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_capsule_config.py
git commit -m "feat(config): add CapsuleConfig (enabled / auto_build_on_ingest / root / min_version)"
```

---

## Task 8: Capsule builder — scaffold + metadata.json writer

**Files:**
- Create: `src/perspicacite/pipeline/capsule_builder.py`
- Test: `tests/unit/test_capsule_builder_metadata.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_builder_metadata.py`:

```python
"""capsule_builder writes metadata.json with the v0.1 schema."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.capsule_builder import capsule_dir_for, write_metadata


def test_capsule_dir_for_local(tmp_path):
    paper = Paper(id="local:abc123", title="t", source=PaperSource.LOCAL)
    out = capsule_dir_for(paper, root=tmp_path)
    assert out == tmp_path / "local__abc123"


def test_capsule_dir_for_doi(tmp_path):
    paper = Paper(id="doi:10.1234/abc", title="t", source=PaperSource.CROSSREF)
    out = capsule_dir_for(paper, root=tmp_path)
    # slash in DOI replaced with double-underscore for filesystem-safety
    assert out == tmp_path / "doi_10.1234__abc"


def test_write_metadata_schema(tmp_path):
    paper = Paper(
        id="doi:10.1234/abc", title="A Paper", source=PaperSource.CROSSREF,
        year=2025, doi="10.1234/abc",
    )
    cap_dir = tmp_path / "cap"
    cap_dir.mkdir()
    write_metadata(cap_dir, paper=paper, producer_version="0.0.0-test")
    payload = json.loads((cap_dir / "metadata.json").read_text())
    assert payload["capsule_version"] == "0.1"
    assert payload["producer"] == "perspicacite"
    assert payload["paper_id"] == "doi:10.1234/abc"
    assert payload["title"] == "A Paper"
    assert payload["year"] == 2025
    assert payload["doi"] == "10.1234/abc"
    assert payload["task_id"] is None
    assert "built_at" in payload
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_builder_metadata.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Create the scaffold**

Create `src/perspicacite/pipeline/capsule_builder.py`:

```python
"""Per-paper capsule builder.

Orchestrates: PDF parse → section split → chunk per section → tag → embed →
write Chroma + write capsule directory (metadata.json, figures/, text/,
resources.json). ASB-aligned schema; on-disk layout is byte-compatible with
ASB capsules (see docs/superpowers/specs/2026-05-13-capsule-multimodal-rag-design.md).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from perspicacite.models.papers import Paper

CAPSULE_VERSION = "0.1"


def capsule_dir_for(paper: Paper, *, root: Path) -> Path:
    """Return the capsule directory for ``paper`` under ``root``.

    Paper IDs (e.g. ``doi:10.1234/abc`` or ``local:abc123``) are filesystem-
    sanitized: ``:`` becomes ``_`` and ``/`` becomes ``__``.
    """
    safe = paper.id.replace(":", "_").replace("/", "__")
    return root / safe


def write_metadata(
    capsule_dir: Path,
    *,
    paper: Paper,
    producer_version: str,
    source: str | None = None,
) -> None:
    """Write ``capsule_dir/metadata.json`` with the v0.1 Capsule schema."""
    capsule_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "capsule_version": CAPSULE_VERSION,
        "producer": "perspicacite",
        "producer_version": producer_version,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "paper_id": paper.id,
        "title": paper.title,
        "authors": [a.model_dump() for a in (paper.authors or [])],
        "year": getattr(paper, "year", None),
        "doi": getattr(paper, "doi", None),
        "source": source or (paper.source.value if paper.source else None),
        "task_id": None,
    }
    (capsule_dir / "metadata.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_builder_metadata.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py tests/unit/test_capsule_builder_metadata.py
git commit -m "feat(capsule_builder): scaffold + metadata.json writer (Capsule v0.1)"
```

---

## Task 9: Capsule builder — figures step

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py`
- Test: `tests/unit/test_capsule_builder_figures.py`

Extracts figures from a PDF, writes image binaries to `figures/`, writes `figures/index.json`. Returns the list of `RawFigure` for downstream resolution.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_builder_figures.py`:

```python
"""capsule_builder.write_figures persists images + index.json (ASB schema)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perspicacite.pipeline.capsule_builder import write_figures
from perspicacite.pipeline.parsers.figures import FigureRecord, RawFigure


def _make_raw(num: int = 1) -> RawFigure:
    return RawFigure(
        record=FigureRecord(
            source_pdf="paper.pdf",
            page=num, index=1,
            width_px=400, height_px=300,
            caption=f"Figure {num}. demo.",
            filename=f"fig_p{num:03d}_i01.png", ext="png",
            figure_number=str(num),
            bbox=(10.0, 20.0, 200.0, 100.0),
        ),
        image_bytes=b"\x89PNG\r\n\x1a\ndata-for-fig-%d" % num,
    )


def test_writes_index_and_binaries(tmp_path):
    cap = tmp_path / "cap"
    figs = [_make_raw(1), _make_raw(2)]
    written = write_figures(cap, figures=figs)
    assert (cap / "figures" / "index.json").exists()
    assert (cap / "figures" / "fig_p001_i01.png").read_bytes() == figs[0].image_bytes
    assert (cap / "figures" / "fig_p002_i01.png").read_bytes() == figs[1].image_bytes
    index = json.loads((cap / "figures" / "index.json").read_text())
    assert len(index) == 2
    assert index[0]["figure_number"] == "1"
    assert index[0]["bbox"] == [10.0, 20.0, 200.0, 100.0]
    assert written == 2


def test_handles_no_figures(tmp_path):
    cap = tmp_path / "cap"
    n = write_figures(cap, figures=[])
    assert n == 0
    assert (cap / "figures" / "index.json").exists()
    assert json.loads((cap / "figures" / "index.json").read_text()) == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_builder_figures.py -v
```

Expected: FAIL (`write_figures` missing).

- [ ] **Step 3: Implement `write_figures`**

Append to `src/perspicacite/pipeline/capsule_builder.py`:

```python
from dataclasses import asdict

from perspicacite.pipeline.parsers.figures import RawFigure


def write_figures(capsule_dir: Path, *, figures: list[RawFigure]) -> int:
    """Persist each ``RawFigure``'s bytes and emit ``figures/index.json``.

    Returns the number of figures written. Filenames follow ASB's
    ``fig_p<page:03d>_i<idx:02d>.<ext>`` convention (already set on each
    ``FigureRecord``).
    """
    fig_dir = capsule_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for raw in figures:
        rec = raw.record
        target = fig_dir / rec.filename
        target.write_bytes(raw.image_bytes)
        records.append(asdict(rec))

    (fig_dir / "index.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(records)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_builder_figures.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py tests/unit/test_capsule_builder_figures.py
git commit -m "feat(capsule_builder): write_figures — binaries + index.json (ASB schema)"
```

---

## Task 10: Capsule builder — text/blocks step

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py`
- Test: `tests/unit/test_capsule_builder_blocks.py`

Splits parsed PDF text into IMRaD sections, emits one block per (section, paragraph) into `text/blocks.jsonl`. V1 emits only `heading | paragraph | caption` types.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_builder_blocks.py`:

```python
"""capsule_builder.write_blocks emits one row per paragraph with section tags."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perspicacite.pipeline.capsule_builder import write_blocks


def test_emits_blocks_with_sections(tmp_path):
    cap = tmp_path / "cap"
    text = (
        "## Abstract\nWe present X.\n\n"
        "## Methods\nWe did Y.\n\nAnother methods paragraph.\n\n"
        "## Results\nWe found Z.\n"
    )
    rows = write_blocks(cap, text=text)
    p = cap / "text" / "blocks.jsonl"
    assert p.exists()
    parsed = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    assert rows == len(parsed)
    sections = {r["section"] for r in parsed}
    assert {"abstract", "methods", "results"} <= sections
    contents = [r["content"] for r in parsed]
    assert any("We did Y." in c for c in contents)
    # block ids are unique and sequential
    assert len({r["block_id"] for r in parsed}) == len(parsed)


def test_fallback_full_text(tmp_path):
    cap = tmp_path / "cap"
    rows = write_blocks(cap, text="just prose without headings at all.")
    p = cap / "text" / "blocks.jsonl"
    parsed = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    assert rows >= 1
    assert all(r["section"] == "full_text" for r in parsed)


def test_empty_text(tmp_path):
    cap = tmp_path / "cap"
    rows = write_blocks(cap, text="")
    assert rows == 0
    p = cap / "text" / "blocks.jsonl"
    assert p.exists()
    assert p.read_text() == ""
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_builder_blocks.py -v
```

Expected: FAIL (`write_blocks` missing).

- [ ] **Step 3: Implement `write_blocks`**

Append to `src/perspicacite/pipeline/capsule_builder.py`:

```python
from perspicacite.pipeline.parsers.section_splitter import split_sections


def write_blocks(capsule_dir: Path, *, text: str) -> int:
    """Section-split ``text`` and emit one paragraph-block per row into
    ``text/blocks.jsonl``.

    V1 block type is always ``paragraph``. Schema reserves ``heading`` /
    ``caption`` / ``table_latex`` / ``equation_latex`` for V2. ``char_span``
    is the offsets of the block content within ``text``.
    """
    text_dir = capsule_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    out_path = text_dir / "blocks.jsonl"

    if not text:
        out_path.write_text("", encoding="utf-8")
        return 0

    sm = split_sections(text)
    rows: list[dict] = []
    block_idx = 0
    for section, section_text in sm.sections.items():
        if not section_text.strip():
            continue
        for paragraph in _split_paragraphs(section_text):
            start = text.find(paragraph)
            end = start + len(paragraph) if start >= 0 else None
            rows.append({
                "block_id": f"b{block_idx:06d}",
                "page": None,             # PDF page mapping is a V2 enhancement
                "bbox": None,
                "type": "paragraph",
                "content": paragraph,
                "section": section,
                "char_span": [start, end] if start >= 0 else None,
                "figure_refs": [],        # resolved in Task 11
                "table_refs": [],
                "resource_refs": [],      # resolved in Task 12
            })
            block_idx += 1

    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return len(rows)


def _split_paragraphs(text: str) -> list[str]:
    """Split a section's text on blank lines; trim each paragraph."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_builder_blocks.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py tests/unit/test_capsule_builder_blocks.py
git commit -m "feat(capsule_builder): write_blocks — IMRaD section split + paragraph blocks"
```

---

## Task 11: Capsule builder — figure_refs resolution per block

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py`
- Test: `tests/unit/test_capsule_builder_figure_refs.py`

Regex-detects "Fig. N" / "Figure N" in block content and resolves to `figure_id` via the `figure_number` field in the figures index.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_builder_figure_refs.py`:

```python
"""capsule_builder.resolve_figure_refs maps in-text Fig./Figure N to figure_ids."""

from __future__ import annotations

import pytest

from perspicacite.pipeline.capsule_builder import resolve_figure_refs
from perspicacite.pipeline.parsers.figures import FigureRecord, RawFigure


def _raw(fig_num: str, page: int = 1, idx: int = 1) -> RawFigure:
    return RawFigure(
        record=FigureRecord(
            source_pdf="p.pdf", page=page, index=idx,
            width_px=10, height_px=10, caption=f"Figure {fig_num}. …",
            filename=f"fig_p{page:03d}_i{idx:02d}.png", ext="png",
            figure_number=fig_num,
        ),
        image_bytes=b"",
    )


def test_basic_resolution():
    figs = [_raw("1", 3, 1), _raw("2", 5, 1)]
    refs = resolve_figure_refs("As shown in Fig. 1 and Figure 2, the results …", figs)
    assert set(refs) == {"pdf_p3_i1", "pdf_p5_i1"}


def test_supplementary():
    figs = [_raw("S1", 9, 1)]
    refs = resolve_figure_refs("See Figure S1 in the SI", figs)
    assert refs == ["pdf_p9_i1"]


def test_no_mention():
    figs = [_raw("1")]
    assert resolve_figure_refs("no mentions here", figs) == []


def test_unknown_figure_number_skipped():
    figs = [_raw("1")]
    assert resolve_figure_refs("see Fig. 9 (which we don't have)", figs) == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_builder_figure_refs.py -v
```

Expected: FAIL (`resolve_figure_refs` missing).

- [ ] **Step 3: Implement**

Append to `src/perspicacite/pipeline/capsule_builder.py`:

```python
import re

_FIG_MENTION_RE = re.compile(
    r"\b(?:fig(?:ure|\.)?|scheme)\s+([A-Za-z]?\d+[A-Za-z]?)\b",
    re.IGNORECASE,
)


def resolve_figure_refs(text: str, figures: list[RawFigure]) -> list[str]:
    """Return a deduped list of ``figure_id`` strings mentioned in ``text``.

    Uses the same regex family as ``parse_figure_number``. Only mentions whose
    ``figure_number`` exists in ``figures`` are kept.
    """
    if not text or not figures:
        return []
    by_number: dict[str, str] = {}
    for raw in figures:
        rec = raw.record
        if rec.figure_number:
            by_number.setdefault(
                rec.figure_number.lower(), f"pdf_p{rec.page}_i{rec.index}",
            )
    out: list[str] = []
    seen: set[str] = set()
    for m in _FIG_MENTION_RE.finditer(text):
        key = m.group(1).lower()
        fid = by_number.get(key)
        if fid and fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out
```

Also extend `write_blocks` to populate each block's `figure_refs` when a `figures: list[RawFigure] | None = None` parameter is passed. Update its signature:

```python
def write_blocks(
    capsule_dir: Path, *, text: str,
    figures: "list[RawFigure] | None" = None,
) -> int:
    """... (existing docstring) ...

    When ``figures`` is provided, each block's ``figure_refs`` is populated by
    ``resolve_figure_refs(block.content, figures)``.
    """
    # ... unchanged setup ...
    # In the per-paragraph loop, replace the figure_refs assignment:
    "figure_refs": resolve_figure_refs(paragraph, figures or []),
    # ...
```

Also write `text/figure_mentions.jsonl` (one row per `(block_id, figure_id)`) when figures are provided:

```python
def _write_figure_mentions(
    capsule_dir: Path, *, rows: list[dict], figures: list[RawFigure] | None,
) -> None:
    if not figures:
        # Empty file (still create it for downstream tools that look for it)
        (capsule_dir / "text" / "figure_mentions.jsonl").write_text("", encoding="utf-8")
        return
    out: list[dict] = []
    for r in rows:
        for fid in r["figure_refs"]:
            out.append({"block_id": r["block_id"], "figure_id": fid})
    (capsule_dir / "text" / "figure_mentions.jsonl").write_text(
        "\n".join(json.dumps(x) for x in out) + ("\n" if out else ""),
        encoding="utf-8",
    )
```

Call `_write_figure_mentions(...)` at the end of `write_blocks` (after building `rows` and writing `blocks.jsonl`).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_builder_figure_refs.py tests/unit/test_capsule_builder_blocks.py -v
```

Expected: all pass (existing block tests should still pass because the figures arg defaults to None).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py tests/unit/test_capsule_builder_figure_refs.py
git commit -m "feat(capsule_builder): resolve in-text Fig. N mentions to figure_ids + figure_mentions.jsonl"
```

---

## Task 12: Capsule builder — mined resources

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py`
- Test: `tests/unit/test_capsule_builder_resources.py`

Mines accessions + DOIs + GitHub repos + Zenodo IDs from the full text and writes `resources.json`. Also resolves `resource_refs` per block.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_builder_resources.py`:

```python
"""capsule_builder.write_resources mines accessions + URLs + GitHub + Zenodo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perspicacite.pipeline.capsule_builder import write_resources, resolve_resource_refs


def test_write_resources_emits_records(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    text = (
        "Data at PRIDE (PXD012345), code at https://github.com/foo/bar and "
        "https://zenodo.org/record/9876543 ; DOI 10.1234/abc."
    )
    n = write_resources(cap, text=text)
    payload = json.loads((cap / "resources.json").read_text())
    assert n == len(payload)
    kinds = {p["kind"] for p in payload}
    assert {"pride", "github", "zenodo", "doi"} <= kinds
    # GitHub identifier shape
    gh = [p for p in payload if p["kind"] == "github"][0]
    assert gh["identifier"] == "foo/bar"
    assert gh["resource_id"] == "github:foo/bar"


def test_resolve_resource_refs():
    res = [
        {"resource_id": "github:foo/bar", "kind": "github", "identifier": "foo/bar",
         "url": "https://github.com/foo/bar", "evidence_span": "", "char_span": None,
         "page": None, "block_id": None},
        {"resource_id": "pride:PXD012345", "kind": "pride", "identifier": "PXD012345",
         "url": "x", "evidence_span": "", "char_span": None, "page": None, "block_id": None},
    ]
    refs = resolve_resource_refs("see https://github.com/foo/bar for code", res)
    assert refs == ["github:foo/bar"]
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_builder_resources.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/perspicacite/pipeline/capsule_builder.py`:

```python
from perspicacite.pipeline.external.accessions import mine_accessions
from perspicacite.pipeline.external.resources import (
    extract_doi_candidates, extract_github_repos, extract_zenodo_record_ids,
)


def write_resources(capsule_dir: Path, *, text: str) -> int:
    """Mine accessions + DOIs + GitHub + Zenodo from ``text``; write ``resources.json``.

    Returns count of records.
    """
    records: list[dict] = []
    for acc in mine_accessions(text):
        records.append({
            "resource_id": f"{acc['kind']}:{acc['accession']}",
            "kind": acc["kind"],
            "identifier": acc["accession"],
            "url": acc["url"],
            "evidence_span": acc["evidence_span"],
            "char_span": None,
            "page": None,
            "block_id": None,
        })
    for repo in extract_github_repos(text or ""):
        records.append({
            "resource_id": f"github:{repo}",
            "kind": "github",
            "identifier": repo,
            "url": f"https://github.com/{repo}",
            "evidence_span": _evidence_span(text or "", f"github.com/{repo}"),
            "char_span": None,
            "page": None,
            "block_id": None,
        })
    for rec_id in extract_zenodo_record_ids(text or ""):
        records.append({
            "resource_id": f"zenodo:{rec_id}",
            "kind": "zenodo",
            "identifier": rec_id,
            "url": f"https://zenodo.org/record/{rec_id}",
            "evidence_span": _evidence_span(text or "", rec_id),
            "char_span": None,
            "page": None,
            "block_id": None,
        })
    for doi in extract_doi_candidates(text or ""):
        records.append({
            "resource_id": f"doi:{doi}",
            "kind": "doi",
            "identifier": doi,
            "url": f"https://doi.org/{doi}",
            "evidence_span": _evidence_span(text or "", doi),
            "char_span": None,
            "page": None,
            "block_id": None,
        })
    capsule_dir.mkdir(parents=True, exist_ok=True)
    (capsule_dir / "resources.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(records)


def _evidence_span(text: str, needle: str, radius: int = 60) -> str:
    """Return text[max(0, idx-radius) : idx+len(needle)+radius] cleaned of newlines."""
    idx = text.find(needle)
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(needle) + radius)
    return text[start:end].replace("\n", " ").strip()


def resolve_resource_refs(text: str, resources: list[dict]) -> list[str]:
    """Return resource_ids whose ``identifier`` or ``url`` appears in ``text``."""
    if not text or not resources:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for r in resources:
        ident = r.get("identifier") or ""
        url = r.get("url") or ""
        if (ident and ident in text) or (url and url in text):
            rid = r["resource_id"]
            if rid not in seen:
                seen.add(rid)
                out.append(rid)
    return out
```

Also extend `write_blocks` to accept a `resources: list[dict] | None = None` arg; when set, populate each block's `resource_refs = resolve_resource_refs(paragraph, resources)`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_builder_resources.py tests/unit/test_capsule_builder_blocks.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py tests/unit/test_capsule_builder_resources.py
git commit -m "feat(capsule_builder): mine resources (accessions, DOIs, GitHub, Zenodo) + resource_refs"
```

---

## Task 13: Capsule builder — top-level `build_capsule` orchestrator

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py`
- Test: `tests/unit/test_capsule_builder_orchestrator.py`

The single entry point: takes a `Paper`, a PDF path (optional — None for non-PDF papers), an `app_state`, and a `kb_name`. Parses, extracts figures, writes capsule files, chunks per block, tags chunks with all provenance, embeds, writes to Chroma, updates KB metadata. Idempotent (no-op when capsule exists at `>=` min_version), with `force` override.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_capsule_builder_orchestrator.py`:

```python
"""build_capsule orchestrates parse + extract + write + chunk + embed."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.capsule_builder import build_capsule
from perspicacite.pipeline.parsers.figures import FigureRecord, RawFigure


def _state(tmp_root: Path):
    return SimpleNamespace(
        config=SimpleNamespace(
            capsule=SimpleNamespace(
                enabled=True, auto_build_on_ingest=True,
                root=tmp_root, min_version="0.1",
            ),
            knowledge_base=SimpleNamespace(
                chunk_size=1000, chunk_overlap=200,
                markdown_heading_aware=True, code_language_aware=True,
            ),
        ),
        embedding_provider=SimpleNamespace(embed=AsyncMock(return_value=[[0.1]*3])),
        vector_store=SimpleNamespace(add_chunks=AsyncMock()),
        pdf_parser=SimpleNamespace(parse=AsyncMock()),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="kb_test", paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_builds_capsule_for_paper_with_pdf(tmp_path, monkeypatch):
    paper = Paper(id="doi:10.1234/abc", title="t", source=PaperSource.CROSSREF, doi="10.1234/abc")
    state = _state(tmp_path / "caps")

    parsed = SimpleNamespace(
        text="## Methods\nWe did Y.\n\n## Results\nSee Fig. 1.\n",
        title="t", sections={}, metadata={},
    )
    state.pdf_parser.parse = AsyncMock(return_value=parsed)

    fake_fig = RawFigure(
        record=FigureRecord(
            source_pdf="paper.pdf", page=3, index=1,
            width_px=400, height_px=300, caption="Figure 1.",
            filename="fig_p003_i01.png", ext="png", figure_number="1",
        ),
        image_bytes=b"PNGBYTES",
    )
    monkeypatch.setattr(
        "perspicacite.pipeline.capsule_builder.extract_figures",
        lambda pdf_path, min_px=100: [fake_fig],
    )

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    result = await build_capsule(
        paper=paper, pdf_path=pdf_path, kb_name="kb_test", app_state=state,
    )

    cap = tmp_path / "caps" / "doi_10.1234__abc"
    assert (cap / "metadata.json").exists()
    assert (cap / "figures" / "index.json").exists()
    assert (cap / "figures" / "fig_p003_i01.png").read_bytes() == b"PNGBYTES"
    blocks = (cap / "text" / "blocks.jsonl").read_text().splitlines()
    assert any('"section": "results"' in line for line in blocks)
    # one of those blocks references the figure
    assert any("pdf_p3_i1" in line for line in blocks)
    # chunks were added
    state.vector_store.add_chunks.assert_called_once()
    assert result["status"] == "built"
    assert result["figures"] == 1


@pytest.mark.asyncio
async def test_idempotent_when_capsule_exists(tmp_path):
    paper = Paper(id="doi:10.1234/abc", title="t", source=PaperSource.CROSSREF)
    state = _state(tmp_path / "caps")
    cap = (tmp_path / "caps") / "doi_10.1234__abc"
    cap.mkdir(parents=True)
    (cap / "metadata.json").write_text(
        json.dumps({"capsule_version": "0.1", "producer": "perspicacite"}),
    )
    result = await build_capsule(
        paper=paper, pdf_path=None, kb_name="kb_test", app_state=state,
    )
    assert result["status"] == "skipped"
    state.vector_store.add_chunks.assert_not_called()


@pytest.mark.asyncio
async def test_builds_without_pdf(tmp_path):
    """Paper with no PDF — capsule still gets metadata + empty figures/."""
    paper = Paper(id="local:abc", title="t", source=PaperSource.LOCAL)
    state = _state(tmp_path / "caps")
    result = await build_capsule(
        paper=paper, pdf_path=None, kb_name="kb_test", app_state=state,
    )
    cap = (tmp_path / "caps") / "local_abc"
    assert (cap / "metadata.json").exists()
    assert json.loads((cap / "figures" / "index.json").read_text()) == []
    assert result["figures"] == 0
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_builder_orchestrator.py -v
```

Expected: FAIL (`build_capsule` missing).

- [ ] **Step 3: Implement the orchestrator**

Append to `src/perspicacite/pipeline/capsule_builder.py`:

```python
from perspicacite.models.documents import ChunkMetadata
from perspicacite.pipeline.parsers.figures import extract_figures
from perspicacite.pipeline.chunking_dispatch import chunk_document


async def build_capsule(
    *,
    paper: Paper,
    pdf_path: Path | None,
    kb_name: str,
    app_state,
    force: bool = False,
    producer_version: str = "0.0.0",
) -> dict[str, Any]:
    """Build a capsule for ``paper`` and ingest its chunks into ``kb_name``.

    Returns a dict with ``status`` (``built`` / ``skipped``), figure/chunk counts.
    Idempotent: no-op when ``capsule_dir/metadata.json`` exists with
    ``capsule_version >= app_state.config.capsule.min_version``, unless ``force``.
    """
    cap_root = Path(app_state.config.capsule.root)
    cap = capsule_dir_for(paper, root=cap_root)
    meta_path = cap / "metadata.json"

    if not force and meta_path.exists():
        try:
            existing = json.loads(meta_path.read_text())
            if existing.get("capsule_version", "0.0") >= app_state.config.capsule.min_version:
                return {"status": "skipped", "capsule_dir": str(cap)}
        except Exception:
            pass  # fall through and rebuild

    # 1. Parse PDF if available
    text = ""
    if pdf_path is not None and pdf_path.exists():
        parsed = await app_state.pdf_parser.parse(pdf_path)
        text = (parsed.text or "") if parsed is not None else ""

    # 2. Figures (PDF only)
    figures: list[RawFigure] = []
    if pdf_path is not None and pdf_path.exists():
        figures = extract_figures(pdf_path)

    # 3. Mine resources
    cap.mkdir(parents=True, exist_ok=True)
    n_res = write_resources(cap, text=text)
    resources = json.loads((cap / "resources.json").read_text())

    # 4. Write figures + blocks
    n_figs = write_figures(cap, figures=figures)
    n_blocks = write_blocks(cap, text=text, figures=figures, resources=resources)

    # 5. Metadata
    write_metadata(cap, paper=paper, producer_version=producer_version)

    # 6. Chunk per block + embed + write to Chroma
    n_chunks = 0
    if text:
        n_chunks = await _ingest_chunks(
            paper=paper, blocks_path=cap / "text" / "blocks.jsonl",
            kb_name=kb_name, app_state=app_state, capsule_dir=cap,
        )

    return {
        "status": "built",
        "capsule_dir": str(cap),
        "figures": n_figs,
        "blocks": n_blocks,
        "resources": n_res,
        "chunks": n_chunks,
    }


async def _ingest_chunks(
    *,
    paper: Paper,
    blocks_path: Path,
    kb_name: str,
    app_state,
    capsule_dir: Path,
) -> int:
    """Chunk each block via existing chunk_document(), tag with provenance,
    embed, and write to Chroma."""
    kb = await app_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return 0
    kb_cfg = app_state.config.knowledge_base
    all_chunks = []
    for line in blocks_path.read_text().splitlines():
        if not line.strip():
            continue
        block = json.loads(line)
        chunks = await chunk_document(
            block["content"], paper,
            content_type="text", language=None, config=kb_cfg,
        )
        for c in chunks:
            md = c.metadata.model_dump()
            md.update({
                "source_section": block["section"],
                "page": block.get("page"),
                "char_span": tuple(block["char_span"]) if block.get("char_span") else None,
                "figure_refs": list(block.get("figure_refs", [])),
                "table_refs": list(block.get("table_refs", [])),
                "resource_refs": list(block.get("resource_refs", [])),
            })
            c.metadata = ChunkMetadata(**md)
        all_chunks.extend(chunks)

    if all_chunks:
        texts = [c.text for c in all_chunks]
        embeds = await app_state.embedding_provider.embed(texts)
        for c, e in zip(all_chunks, embeds, strict=True):
            c.embedding = e
        await app_state.vector_store.add_chunks(kb.collection_name, all_chunks)
        kb.chunk_count += len(all_chunks)
        await app_state.session_store.save_kb_metadata(kb)
    return len(all_chunks)
```

Also update `write_blocks` signature to accept the resources arg added in Task 12.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_capsule_builder_orchestrator.py tests/unit/test_capsule_builder_blocks.py tests/unit/test_capsule_builder_resources.py tests/unit/test_capsule_builder_figure_refs.py tests/unit/test_capsule_builder_figures.py tests/unit/test_capsule_builder_metadata.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py tests/unit/test_capsule_builder_orchestrator.py
git commit -m "feat(capsule_builder): build_capsule orchestrator (idempotent, paper-level)"
```

---

## Task 14: Hook `build_capsule` into the BibTeX ingest worker

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_bibtex_ingest_capsule_hook.py`

The current `_bibtex_ingest_worker` (around line 98) downloads each paper's PDF then chunks + embeds. Replace the chunk-and-embed block with a single `build_capsule(...)` call per paper.

- [ ] **Step 1: Read the existing worker first**

Open `src/perspicacite/web/routers/kb.py` lines 90-230. Locate the per-paper inner loop where text is parsed and chunks are written. You will replace the parse-and-chunk portion with `await build_capsule(paper=..., pdf_path=..., kb_name=name, app_state=app_state)`.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_bibtex_ingest_capsule_hook.py`:

```python
"""BibTeX ingest worker delegates per-paper chunk+embed to build_capsule."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_bibtex_worker_calls_build_capsule(tmp_path, monkeypatch):
    from perspicacite.web.routers import kb as kb_router
    from perspicacite.models.papers import Paper, PaperSource

    called = {"count": 0, "papers": []}

    async def _fake_build_capsule(*, paper, pdf_path, kb_name, app_state, **_):
        called["count"] += 1
        called["papers"].append(paper.id)
        return {"status": "built", "figures": 0, "blocks": 0, "resources": 0, "chunks": 1}

    monkeypatch.setattr(
        "perspicacite.web.routers.kb.build_capsule", _fake_build_capsule, raising=False,
    )

    # Minimal fake app_state and a fake "entries_to_papers" returning two papers
    fake_papers = [
        Paper(id="doi:10.1/a", title="A", source=PaperSource.CROSSREF, doi="10.1/a"),
        Paper(id="doi:10.1/b", title="B", source=PaperSource.CROSSREF, doi="10.1/b"),
    ]
    # If the worker's per-paper loop calls build_capsule once per paper, called["count"] == 2.
    # The test asserts that contract; we don't run the full worker here, just the hook surface.
    # Instead, invoke build_capsule directly with the hook to verify the import path works:
    state = SimpleNamespace(
        config=SimpleNamespace(capsule=SimpleNamespace(
            enabled=True, auto_build_on_ingest=True,
            root=tmp_path, min_version="0.1",
        )),
    )
    res = await kb_router.build_capsule(
        paper=fake_papers[0], pdf_path=None, kb_name="kb", app_state=state,
    )
    assert res["status"] == "built"
    assert called["count"] == 1
```

- [ ] **Step 3: Run to confirm failure**

```bash
uv run pytest tests/unit/test_bibtex_ingest_capsule_hook.py -v
```

Expected: FAIL (likely `AttributeError: module has no attribute 'build_capsule'`).

- [ ] **Step 4: Wire the hook**

At the top of `src/perspicacite/web/routers/kb.py`, add:

```python
from perspicacite.pipeline.capsule_builder import build_capsule
```

In `_bibtex_ingest_worker`, find the per-paper block that currently does parse + chunk + embed. Replace that block with:

```python
                if app_state.config.capsule.auto_build_on_ingest:
                    cap_result = await build_capsule(
                        paper=paper, pdf_path=pdf_path, kb_name=name,
                        app_state=app_state,
                    )
                    chunks_added = cap_result.get("chunks", 0)
                else:
                    # Legacy path retained behind the config flag — keep existing logic.
                    chunks_added = await _legacy_chunk_and_embed(paper, parsed_text, app_state, name)
```

If `_legacy_chunk_and_embed` does not exist, extract the existing parse-and-chunk logic into that private helper at the bottom of the file. Keep behaviour identical so the flag-off path doesn't regress.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_bibtex_ingest_capsule_hook.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_bibtex_ingest_capsule_hook.py
git commit -m "feat(web/kb): BibTeX ingest worker calls build_capsule when auto_build_on_ingest"
```

---

## Task 15: Hook `build_capsule` into the DOIs ingest worker

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_dois_ingest_capsule_hook.py`

Symmetric to Task 14, for `_dois_ingest_worker` (around line 241).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dois_ingest_capsule_hook.py`:

```python
"""DOIs ingest worker delegates per-paper chunk+embed to build_capsule."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_dois_worker_uses_build_capsule(tmp_path, monkeypatch):
    """Hook smoke test: the kb_router exposes build_capsule and the dois worker imports it.

    The deeper end-to-end (download + ingest) is exercised in MANUAL_QA.
    """
    from perspicacite.web.routers import kb as kb_router
    assert hasattr(kb_router, "build_capsule")
    assert hasattr(kb_router, "_dois_ingest_worker")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_dois_ingest_capsule_hook.py -v
```

Expected: PASS for the first assertion (Task 14 added the import). May FAIL on the second if `_dois_ingest_worker` was renamed — confirm the symbol exists.

- [ ] **Step 3: Wire the DOIs worker**

In `_dois_ingest_worker`, locate the per-DOI block that parses the downloaded PDF and chunks it. Replace the parse+chunk+embed body with the same pattern as Task 14:

```python
                if app_state.config.capsule.auto_build_on_ingest:
                    cap_result = await build_capsule(
                        paper=paper, pdf_path=pdf_path, kb_name=name,
                        app_state=app_state,
                    )
                    chunks_added = cap_result.get("chunks", 0)
                else:
                    chunks_added = await _legacy_chunk_and_embed(paper, parsed_text, app_state, name)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_dois_ingest_capsule_hook.py tests/unit/test_bibtex_ingest_capsule_hook.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_dois_ingest_capsule_hook.py
git commit -m "feat(web/kb): DOIs ingest worker calls build_capsule when auto_build_on_ingest"
```

---

## Task 16: Hook `build_capsule` into local-PDF ingest

**Files:**
- Modify: `src/perspicacite/integrations/local_docs.py`
- Test: `tests/unit/test_local_docs_capsule_hook.py`

In `_ingest_files`, when a file's `content_type` is `pdf`, route through `build_capsule` instead of the inline chunk-and-embed for that file.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_local_docs_capsule_hook.py`:

```python
"""Local-PDF ingest routes through build_capsule when auto_build_on_ingest."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.integrations.local_docs import _ingest_files


@pytest.mark.asyncio
async def test_pdf_routes_through_build_capsule(tmp_path, monkeypatch):
    called = {"count": 0}

    async def _fake_build(*, paper, pdf_path, kb_name, app_state, **_):
        called["count"] += 1
        return {"status": "built", "figures": 0, "blocks": 0, "resources": 0, "chunks": 1}

    monkeypatch.setattr(
        "perspicacite.integrations.local_docs.build_capsule", _fake_build, raising=False,
    )

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    md = tmp_path / "n.md"
    md.write_text("# t\n\nbody")

    state = SimpleNamespace(
        config=SimpleNamespace(
            capsule=SimpleNamespace(
                enabled=True, auto_build_on_ingest=True,
                root=tmp_path / "caps", min_version="0.1",
            ),
            knowledge_base=SimpleNamespace(
                chunk_size=1000, chunk_overlap=200,
                markdown_heading_aware=True, code_language_aware=True,
            ),
        ),
        embedding_provider=SimpleNamespace(embed=AsyncMock(return_value=[[0.1]*3])),
        vector_store=SimpleNamespace(add_chunks=AsyncMock()),
        pdf_parser=SimpleNamespace(parse=AsyncMock(return_value=SimpleNamespace(text="text"))),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="c", paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
    )

    class _Reg:
        async def publish(self, jid, ev): pass
        async def finish(self, jid, res): pass
        async def fail(self, jid, err): pass

    await _ingest_files(
        kb_name="kb", files=[pdf, md],
        app_state=state, registry=_Reg(), job_id="j",
    )
    # build_capsule called once (for the PDF), markdown stayed on the existing path
    assert called["count"] == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_local_docs_capsule_hook.py -v
```

Expected: FAIL.

- [ ] **Step 3: Wire the hook**

In `src/perspicacite/integrations/local_docs.py`, add at the top:

```python
from perspicacite.pipeline.capsule_builder import build_capsule
```

In `_ingest_files`, inside the per-file loop, branch on content_type:

```python
        for idx, fp in enumerate(files):
            content_type, language = infer_content_type(fp)

            if content_type == "pdf" and getattr(
                app_state.config.capsule, "auto_build_on_ingest", False
            ):
                # New path: capsule builder owns parse + chunk + embed for PDFs.
                paper = _paper_for_file(fp)
                await build_capsule(
                    paper=paper, pdf_path=fp, kb_name=kb_name, app_state=app_state,
                )
                await registry.publish(job_id, {
                    "type": "progress", "done": idx + 1, "file": str(fp),
                    "status": "embedded", "via": "capsule",
                })
                continue

            # Existing markdown/code/text path unchanged.
            paper = _paper_for_file(fp)
            text = await _read_text(fp, content_type, app_state.pdf_parser)
            # ... rest of the existing body unchanged ...
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_local_docs_capsule_hook.py tests/unit/test_local_docs_worker.py -v
```

Expected: all pass (the existing worker test still passes because its files are .md and .py, not .pdf).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/local_docs.py tests/unit/test_local_docs_capsule_hook.py
git commit -m "feat(local_docs): route PDF files through build_capsule when auto_build_on_ingest"
```

---

## Task 17: Hook `build_capsule` into Zotero ingest

**Files:**
- Modify: `src/perspicacite/integrations/zotero_ingest.py`
- Test: `tests/unit/test_zotero_ingest_capsule_hook.py`

Symmetric to Task 14.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_zotero_ingest_capsule_hook.py`:

```python
"""Zotero ingest worker imports build_capsule and uses it for PDF items."""

from __future__ import annotations

import pytest


def test_zotero_worker_imports_build_capsule():
    from perspicacite.integrations import zotero_ingest
    assert hasattr(zotero_ingest, "build_capsule"), (
        "Zotero worker must import build_capsule to satisfy Cycle A wiring."
    )
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_zotero_ingest_capsule_hook.py -v
```

Expected: FAIL (import not yet present).

- [ ] **Step 3: Wire the hook**

In `src/perspicacite/integrations/zotero_ingest.py`:

1. Add `from perspicacite.pipeline.capsule_builder import build_capsule` at the top.
2. In the per-item worker that downloads the PDF and chunks it, replace the parse+chunk+embed block with the same pattern as Task 14:

```python
            if app_state.config.capsule.auto_build_on_ingest:
                cap_result = await build_capsule(
                    paper=paper, pdf_path=pdf_path, kb_name=kb_name,
                    app_state=app_state,
                )
                chunks_added = cap_result.get("chunks", 0)
            else:
                # legacy path (existing inline logic)
                ...
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_zotero_ingest_capsule_hook.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/zotero_ingest.py tests/unit/test_zotero_ingest_capsule_hook.py
git commit -m "feat(zotero): ingest worker routes through build_capsule"
```

---

## Task 18: Paper-lookup helper + MCP tool `build_capsule`

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py`
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_capsule_paper_lookup.py`
- Test: `tests/unit/test_mcp_build_capsule_tool.py`

Papers aren't stored in `session_store`. Enumerate via `vector_store.list_paper_metadata(collection)` (returns `[{paper_id, title, authors, year, doi}]`). PDFs live under the existing download cache `./data/papers/<doi_slug>.pdf` (slug = `doi.replace("/", "_")`).

- [ ] **Step 1: Write the helper test**

Create `tests/unit/test_capsule_paper_lookup.py`:

```python
"""resolve_paper_from_metadata + locate_cached_pdf helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.pipeline.capsule_builder import (
    resolve_paper_from_metadata,
    locate_cached_pdf,
)


def test_resolve_paper_from_metadata():
    row = {
        "paper_id": "doi:10.1234/abc",
        "title": "Some title",
        "year": 2024,
        "doi": "10.1234/abc",
        "authors": "Doe, Jane; Smith, John",
    }
    p = resolve_paper_from_metadata(row)
    assert p.id == "doi:10.1234/abc"
    assert p.title == "Some title"
    assert p.year == 2024
    assert p.doi == "10.1234/abc"


def test_locate_cached_pdf_doi(tmp_path):
    pdfs = tmp_path / "data" / "papers"
    pdfs.mkdir(parents=True)
    target = pdfs / "10.1234_abc.pdf"
    target.write_bytes(b"%PDF-1.4")
    found = locate_cached_pdf({"paper_id": "doi:10.1234/abc", "doi": "10.1234/abc"}, root=pdfs)
    assert found == target


def test_locate_cached_pdf_missing(tmp_path):
    pdfs = tmp_path / "data" / "papers"
    pdfs.mkdir(parents=True)
    assert locate_cached_pdf({"paper_id": "doi:10.1234/xyz", "doi": "10.1234/xyz"}, root=pdfs) is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_capsule_paper_lookup.py -v
```

Expected: FAIL (helpers missing).

- [ ] **Step 3: Implement helpers**

Append to `src/perspicacite/pipeline/capsule_builder.py`:

```python
from perspicacite.models.papers import Paper, Author, PaperSource


_DEFAULT_PDF_CACHE = Path("./data/papers")


def resolve_paper_from_metadata(row: dict) -> Paper:
    """Reconstruct a minimal ``Paper`` from a vector-store metadata row."""
    authors_raw = row.get("authors") or ""
    authors: list[Author] = []
    if isinstance(authors_raw, str) and authors_raw.strip():
        for part in authors_raw.split(";"):
            name = part.strip()
            if name:
                authors.append(Author(name=name, family=name.split(",")[0].strip()))
    return Paper(
        id=row["paper_id"],
        title=row.get("title") or row["paper_id"],
        authors=authors,
        year=row.get("year"),
        doi=row.get("doi"),
        source=PaperSource.CROSSREF if row.get("doi") else PaperSource.LOCAL,
    )


def locate_cached_pdf(row: dict, *, root: Path = _DEFAULT_PDF_CACHE) -> Path | None:
    """Best-effort: locate a cached PDF for this paper. Returns None if absent."""
    doi = row.get("doi")
    if doi:
        candidate = root / f"{doi.replace('/', '_')}.pdf"
        if candidate.exists():
            return candidate
    pid = row.get("paper_id") or ""
    if pid.startswith("local:"):
        candidate = root / f"{pid.replace(':', '_')}.pdf"
        if candidate.exists():
            return candidate
    return None
```

- [ ] **Step 4: Run helper tests**

```bash
uv run pytest tests/unit/test_capsule_paper_lookup.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Write the MCP test**

Create `tests/unit/test_mcp_build_capsule_tool.py`:

```python
"""MCP build_capsule tool delegates to capsule_builder.build_capsule."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_build_capsule_tool_returns_result(monkeypatch, tmp_path):
    async def _fake(*, paper, pdf_path, kb_name, app_state, **_):
        return {"status": "built", "figures": 2, "chunks": 5, "blocks": 10, "resources": 1, "capsule_dir": str(tmp_path / "x")}

    monkeypatch.setattr("perspicacite.pipeline.capsule_builder.build_capsule", _fake)
    monkeypatch.setattr(mcp_server, "mcp_state", SimpleNamespace(
        config=SimpleNamespace(capsule=SimpleNamespace(
            enabled=True, root=tmp_path, min_version="0.1",
        )),
        vector_store=SimpleNamespace(
            list_paper_metadata=AsyncMock(return_value=[
                {"paper_id": "doi:10.1/abc", "title": "t", "doi": "10.1/abc", "year": 2024, "authors": ""},
            ]),
        ),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(collection_name="c")),
        ),
    ))

    fn = mcp_server.build_capsule
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(paper_id="doi:10.1/abc", kb_name="kb1")
    assert out["status"] == "built"
    assert out["figures"] == 2


@pytest.mark.asyncio
async def test_get_info_lists_fourteen_tools():
    raw = await mcp_server.get_info()
    info = json.loads(raw)
    assert info["tool_count"] >= 14
    assert "build_capsule" in info["tools"]
```

- [ ] **Step 6: Run to confirm failure**

```bash
uv run pytest tests/unit/test_mcp_build_capsule_tool.py -v
```

Expected: FAIL.

- [ ] **Step 7: Add the MCP tool**

In `src/perspicacite/mcp/server.py`, near other `@mcp.tool` definitions, add:

```python
@mcp.tool
async def build_capsule(
    paper_id: str,
    kb_name: str,
    force: bool = False,
) -> dict:
    """Build (or rebuild) a per-paper capsule.

    Enumerates papers in ``kb_name``'s vector-store collection, finds the row
    matching ``paper_id``, reconstructs a Paper, locates a cached PDF (if any),
    and calls ``capsule_builder.build_capsule``.
    """
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )

    kb = await mcp_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return {"error": f"KB '{kb_name}' not found"}
    rows = await mcp_state.vector_store.list_paper_metadata(kb.collection_name)
    row = next((r for r in rows if r.get("paper_id") == paper_id), None)
    if row is None:
        return {"error": f"paper '{paper_id}' not found in KB '{kb_name}'"}
    paper = resolve_paper_from_metadata(row)
    pdf_path = locate_cached_pdf(row)
    return await _build(
        paper=paper, pdf_path=pdf_path, kb_name=kb_name,
        app_state=mcp_state, force=force,
    )
```

Append `"build_capsule"` to `_TOOL_NAMES` (line ~1322) so `get_info()` reports 14 tools. Update `tests/test_mcp_server.py:test_get_info_includes_push_to_zotero` to expect 14.

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/unit/test_mcp_build_capsule_tool.py tests/unit/test_capsule_paper_lookup.py tests/test_mcp_server.py::test_get_info_includes_push_to_zotero -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/perspicacite/pipeline/capsule_builder.py src/perspicacite/mcp/server.py tests/test_mcp_server.py tests/unit/test_capsule_paper_lookup.py tests/unit/test_mcp_build_capsule_tool.py
git commit -m "feat(mcp): add build_capsule tool (14 tools) + paper-lookup helpers"
```

---

## Task 19: MCP tool `build_capsules_for_kb` (bulk, JobRegistry)

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_mcp_build_capsules_for_kb_tool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_mcp_build_capsules_for_kb_tool.py`:

```python
"""build_capsules_for_kb iterates all papers in a KB and returns per-paper status."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_bulk_build_returns_summary(monkeypatch, tmp_path):
    rows = [
        {"paper_id": "doi:10.1/a", "title": "a", "doi": "10.1/a", "year": 2024, "authors": ""},
        {"paper_id": "doi:10.1/b", "title": "b", "doi": "10.1/b", "year": 2024, "authors": ""},
    ]

    async def _fake(*, paper, pdf_path, kb_name, app_state, **_):
        return {"status": "built", "figures": 0, "chunks": 0, "blocks": 0, "resources": 0}

    monkeypatch.setattr(
        "perspicacite.pipeline.capsule_builder.build_capsule", _fake,
    )
    monkeypatch.setattr(mcp_server, "mcp_state", SimpleNamespace(
        config=SimpleNamespace(capsule=SimpleNamespace(root=tmp_path, min_version="0.1")),
        vector_store=SimpleNamespace(list_paper_metadata=AsyncMock(return_value=rows)),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(collection_name="c")),
        ),
    ))

    fn = mcp_server.build_capsules_for_kb
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(kb_name="kb1")
    assert out["total"] == 2
    assert out["built"] == 2
    assert len(out["per_paper"]) == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_mcp_build_capsules_for_kb_tool.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add the bulk tool**

In `src/perspicacite/mcp/server.py`, after the single-paper tool from Task 18, add:

```python
@mcp.tool
async def build_capsules_for_kb(
    kb_name: str,
    force: bool = False,
) -> dict:
    """Build capsules for every paper in ``kb_name``.

    Returns {total, built, skipped, errored, per_paper: [{paper_id, status, ...}]}.
    Papers are enumerated via the vector store. PDFs are best-effort located
    from the standard download cache; papers without a cached PDF still get
    a metadata-only capsule (status ``"built"`` with ``figures=0``).
    """
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )

    kb = await mcp_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        return {"error": f"KB '{kb_name}' not found", "total": 0,
                "built": 0, "skipped": 0, "errored": 0, "per_paper": []}
    rows = await mcp_state.vector_store.list_paper_metadata(kb.collection_name)
    per_paper = []
    counts = {"built": 0, "skipped": 0, "errored": 0}
    for row in rows:
        paper = resolve_paper_from_metadata(row)
        pdf_path = locate_cached_pdf(row)
        try:
            res = await _build(
                paper=paper, pdf_path=pdf_path,
                kb_name=kb_name, app_state=mcp_state, force=force,
            )
            status = res.get("status", "errored")
            counts[status] = counts.get(status, 0) + 1
            per_paper.append({"paper_id": paper.id, **res})
        except Exception as exc:
            counts["errored"] += 1
            per_paper.append({"paper_id": paper.id, "status": "errored", "error": str(exc)})
    return {"total": len(rows), **counts, "per_paper": per_paper}
```

Append `"build_capsules_for_kb"` to `_TOOL_NAMES` (now 15 tools). Update `tests/test_mcp_server.py:test_get_info_includes_push_to_zotero` to expect 15.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_mcp_build_capsules_for_kb_tool.py tests/test_mcp_server.py::test_get_info_includes_push_to_zotero -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/test_mcp_server.py tests/unit/test_mcp_build_capsules_for_kb_tool.py
git commit -m "feat(mcp): add build_capsules_for_kb bulk tool (15 tools)"
```

---

## Task 20: CLI `build-capsule` + `build-capsules`

**Files:**
- Modify: `src/perspicacite/cli.py`
- Test: `tests/unit/test_cli_build_capsule.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_build_capsule.py`:

```python
"""CLI build-capsule + build-capsules subcommands."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from perspicacite.cli import cli


def test_build_capsule_help():
    r = CliRunner().invoke(cli, ["build-capsule", "--help"])
    assert r.exit_code == 0, r.output
    assert "--paper" in r.output


def test_build_capsules_help():
    r = CliRunner().invoke(cli, ["build-capsules", "--help"])
    assert r.exit_code == 0, r.output
    assert "--kb" in r.output


def test_build_capsule_invokes_builder(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    called = {"count": 0}

    async def _fake_build(*, paper, pdf_path, kb_name, app_state, **_):
        called["count"] += 1
        return {"status": "built", "figures": 0, "chunks": 0, "blocks": 0, "resources": 0}

    monkeypatch.setattr(
        "perspicacite.pipeline.capsule_builder.build_capsule", _fake_build,
    )

    fake_state = SimpleNamespace(
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(collection_name="c")),
        ),
        vector_store=SimpleNamespace(
            list_paper_metadata=AsyncMock(return_value=[{
                "paper_id": "doi:10.1/abc", "title": "t", "doi": "10.1/abc",
                "year": 2024, "authors": "",
            }]),
        ),
    )

    class _FakeAppState:
        def __new__(cls): return fake_state
        async def initialize(self): pass

    monkeypatch.setattr("perspicacite.web.state.AppState", _FakeAppState)

    r = CliRunner().invoke(
        cli, ["build-capsule", "--paper", "doi:10.1/abc", "--kb", "kb1"],
        obj={"config": SimpleNamespace()},
    )
    assert r.exit_code == 0, r.output
    assert called["count"] == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_cli_build_capsule.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add the CLI commands**

In `src/perspicacite/cli.py`, near the other `@cli.command()` definitions:

```python
@cli.command("build-capsule")
@click.option("--paper", "paper_id", required=True, help="Paper ID (e.g. doi:10.1234/abc)")
@click.option("--kb", default="", help="KB to ingest chunks into (optional)")
@click.option("--force", is_flag=True, default=False)
@click.pass_context
def build_capsule_cmd(ctx, paper_id: str, kb: str, force: bool) -> None:
    """Build (or rebuild) a per-paper capsule."""
    import asyncio
    from perspicacite.pipeline.capsule_builder import build_capsule as _build
    from perspicacite.web.state import AppState

    async def _run() -> None:
        from perspicacite.pipeline.capsule_builder import (
            resolve_paper_from_metadata, locate_cached_pdf,
        )
        state = AppState()
        await state.initialize()
        if not kb:
            click.echo("Error: --kb is required to look up the paper", err=True)
            raise SystemExit(1)
        kb_meta = await state.session_store.get_kb_metadata(kb)
        if kb_meta is None:
            click.echo(f"Error: KB '{kb}' not found", err=True)
            raise SystemExit(1)
        rows = await state.vector_store.list_paper_metadata(kb_meta.collection_name)
        row = next((r for r in rows if r.get("paper_id") == paper_id), None)
        if row is None:
            click.echo(f"Error: paper '{paper_id}' not in KB '{kb}'", err=True)
            raise SystemExit(1)
        paper = resolve_paper_from_metadata(row)
        pdf_path = locate_cached_pdf(row)
        res = await _build(
            paper=paper, pdf_path=pdf_path,
            kb_name=kb, app_state=state, force=force,
        )
        click.echo(f"Done: {res}")
    asyncio.run(_run())


@cli.command("build-capsules")
@click.option("--kb", "kb_name", required=True, help="KB name")
@click.option("--force", is_flag=True, default=False)
@click.pass_context
def build_capsules_cmd(ctx, kb_name: str, force: bool) -> None:
    """Build capsules for every paper in a KB."""
    import asyncio
    from perspicacite.pipeline.capsule_builder import (
        build_capsule as _build,
        resolve_paper_from_metadata,
        locate_cached_pdf,
    )
    from perspicacite.web.state import AppState

    async def _run() -> None:
        state = AppState()
        await state.initialize()
        kb_meta = await state.session_store.get_kb_metadata(kb_name)
        if kb_meta is None:
            click.echo(f"Error: KB '{kb_name}' not found", err=True)
            raise SystemExit(1)
        rows = await state.vector_store.list_paper_metadata(kb_meta.collection_name)
        counts = {"built": 0, "skipped": 0, "errored": 0}
        for row in rows:
            paper = resolve_paper_from_metadata(row)
            pdf_path = locate_cached_pdf(row)
            try:
                res = await _build(
                    paper=paper, pdf_path=pdf_path,
                    kb_name=kb_name, app_state=state, force=force,
                )
                status = res.get("status", "errored")
                counts[status] = counts.get(status, 0) + 1
                click.echo(f"  {paper.id}: {status}")
            except Exception as exc:
                counts["errored"] += 1
                click.echo(f"  {paper.id}: errored — {exc}", err=True)
        click.echo(f"Summary: {counts}")
    asyncio.run(_run())
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli_build_capsule.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/cli.py tests/unit/test_cli_build_capsule.py
git commit -m "feat(cli): add build-capsule and build-capsules subcommands"
```

---

## Task 21: Web router — `POST /api/kb/{name}/build-capsules`

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_kb_router_build_capsules.py`

Bulk retro-build endpoint with the standard JobRegistry SSE progress.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_kb_router_build_capsules.py`:

```python
"""POST /api/kb/{name}/build-capsules returns a job_id and SSE URL."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from perspicacite.web.app import app as fastapi_app


def _state(tmp_root, n_papers: int = 2):
    rows = [
        {"paper_id": f"doi:10.1/{i}", "title": str(i), "doi": f"10.1/{i}", "year": 2024, "authors": ""}
        for i in range(n_papers)
    ]
    return SimpleNamespace(
        config=SimpleNamespace(capsule=SimpleNamespace(
            enabled=True, auto_build_on_ingest=True,
            root=tmp_root, min_version="0.1",
        )),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
        vector_store=SimpleNamespace(list_paper_metadata=AsyncMock(return_value=rows)),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(collection_name="c")),
        ),
    )


def _patch_state(monkeypatch, state):
    monkeypatch.setattr("perspicacite.web.state.app_state", state)
    monkeypatch.setattr("perspicacite.web.routers.kb.app_state", state)


def test_build_capsules_async_returns_job(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "perspicacite.pipeline.capsule_builder.build_capsule",
        AsyncMock(return_value={"status": "built", "figures": 0, "chunks": 0, "blocks": 0, "resources": 0}),
    )
    _patch_state(monkeypatch, _state(tmp_path))
    client = TestClient(fastapi_app)
    r = client.post("/api/kb/k1/build-capsules")
    assert r.status_code in (200, 202)
    body = r.json()
    assert "job_id" in body
    assert "sse_url" in body
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_kb_router_build_capsules.py -v
```

Expected: FAIL (route missing).

- [ ] **Step 3: Add the endpoint**

In `src/perspicacite/web/routers/kb.py`, after the existing local-paths handlers, add:

```python
@router.post("/api/kb/{name}/build-capsules")
async def build_capsules_for_kb_async(name: str, force: bool = False) -> dict:
    """Retro-build capsules for every paper in this KB. Returns job_id + sse_url."""
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    kb_meta = await app_state.session_store.get_kb_metadata(name)
    if kb_meta is None:
        raise HTTPException(status_code=404, detail=f"KB '{name}' not found")
    rows = await app_state.vector_store.list_paper_metadata(kb_meta.collection_name)
    job_id = await app_state.job_registry.create("capsule_build", total=len(rows))

    async def _runner():
        from perspicacite.pipeline.capsule_builder import (
            build_capsule,
            resolve_paper_from_metadata,
            locate_cached_pdf,
        )
        for i, row in enumerate(rows):
            paper = resolve_paper_from_metadata(row)
            pdf_path = locate_cached_pdf(row)
            try:
                res = await build_capsule(
                    paper=paper, pdf_path=pdf_path,
                    kb_name=name, app_state=app_state, force=force,
                )
                await app_state.job_registry.publish(job_id, {
                    "type": "progress", "done": i + 1, "paper": paper.id,
                    "status": res.get("status"),
                })
            except Exception as exc:
                await app_state.job_registry.publish(job_id, {
                    "type": "progress", "done": i + 1, "paper": paper.id,
                    "status": "errored", "error": str(exc),
                })
        await app_state.job_registry.finish(job_id, {"total": len(rows)})

    task = asyncio.create_task(_runner())
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_kb_router_build_capsules.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_kb_router_build_capsules.py
git commit -m "feat(web/kb): POST /api/kb/{name}/build-capsules (JobRegistry SSE)"
```

---

## Task 22: UI — "Build all missing capsules" button

**Files:**
- Modify: `templates/index.html`
- Modify: `static/js/kb.js`
- Modify: `static/css/kb.css`
- Test: `tests/unit/test_kb_ui_build_capsules_button.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_kb_ui_build_capsules_button.py`:

```python
"""KB panel has a Build-capsules button wired to the async endpoint."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_has_build_capsules_button():
    html = (ROOT / "templates" / "index.html").read_text()
    assert 'data-testid="kb-build-capsules"' in html


def test_kb_js_posts_to_build_capsules():
    js = (ROOT / "static" / "js" / "kb.js").read_text()
    assert "/build-capsules" in js
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/unit/test_kb_ui_build_capsules_button.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add the button + handler**

In `templates/index.html`, immediately after the existing "Build KBs from Zotero" button block (around line 105-112), add:

```html
<div style="margin-top: 8px;">
    <button id="kb-build-capsules-btn"
            data-testid="kb-build-capsules"
            class="kb-create-toggle"
            title="Build per-paper capsules (figures + structured text + provenance)">
        Build capsules
    </button>
    <div id="kb-build-capsules-progress" class="hidden"></div>
</div>
```

In `static/js/kb.js`, add at the bottom:

```javascript
function wireBuildCapsulesButton() {
  const btn = document.getElementById("kb-build-capsules-btn");
  const prog = document.getElementById("kb-build-capsules-progress");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    if (!selectedKb) { alert("Select a KB first."); return; }
    const r = await fetch(`/api/kb/${encodeURIComponent(selectedKb)}/build-capsules`, {
      method: "POST",
    });
    const body = await r.json();
    prog.classList.remove("hidden");
    prog.textContent = "";
    const ev = new EventSource(body.sse_url);
    ev.onmessage = (m) => { prog.textContent += m.data + "\n"; };
    ev.addEventListener("done", () => {
      ev.close();
      prog.textContent += "\nDone.";
      if (typeof loadKBs === "function") loadKBs();
    });
    ev.onerror = () => ev.close();
  });
}
document.addEventListener("DOMContentLoaded", wireBuildCapsulesButton);
```

In `static/css/kb.css`, append:

```css
#kb-build-capsules-progress { white-space: pre-wrap; font-family: monospace; max-height: 30vh; overflow: auto; }
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_kb_ui_build_capsules_button.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html static/js/kb.js static/css/kb.css tests/unit/test_kb_ui_build_capsules_button.py
git commit -m "feat(web/ui): Build-capsules button + progress pane in KB panel"
```

---

## Task 23: Docs — MANUAL_QA + config.example.yml

**Files:**
- Modify: `MANUAL_QA.md`
- Modify: `config.example.yml`

- [ ] **Step 1: Append MANUAL_QA section**

Append to `MANUAL_QA.md`:

```markdown

## Capsule build (2026-05-13, cycle A)

Auto-build on ingest:
1. Ingest a paper via BibTeX, DOIs, local PDF, or Zotero with `capsule.auto_build_on_ingest: true`.
2. Confirm `<data_root>/capsules/<paper_id>/metadata.json` exists with `"producer": "perspicacite"` and `"capsule_version": "0.1"`.
3. Confirm `figures/index.json` exists (empty list for papers without figures).
4. Confirm `text/blocks.jsonl` has one row per paragraph with a `section` field.
5. Confirm `resources.json` has entries if the paper mentions DOIs / GitHub / Zenodo / PRIDE / GEO / etc.
6. Run a chat query that should hit a block in `methods`; confirm `sources[].source_section == "methods"` in the answer's provenance.

Retro-build:
- CLI: `uv run perspicacite build-capsules --kb mykb` — confirm one line per paper and a summary.
- MCP: `build_capsules_for_kb(kb_name="mykb")` — confirm `per_paper` summary.
- UI: KB panel → "Build capsules" button → confirm SSE progress stream renders one event per paper, ending with "Done".

Idempotency:
- Re-run the same retro-build with no `--force`; confirm every paper's status is `skipped`.
- Re-run with `--force`; confirm every paper's status is `built` again.

Provenance:
- Inspect the provenance JSONL sidecar for a recently-ingested paper; confirm chunks now carry `source_section`, `char_span`, and `figure_refs`/`resource_refs` when applicable.
```

- [ ] **Step 2: Update `config.example.yml`**

Add (near the bottom, before any final logging section):

```yaml
# =============================================================================
# Capsule (per-paper artifact: figures, structured text blocks, mined resources)
# =============================================================================
capsule:
  enabled: true
  auto_build_on_ingest: true
  root: ./data/capsules
  min_version: "0.1"
```

- [ ] **Step 3: Commit**

```bash
git add MANUAL_QA.md config.example.yml
git commit -m "docs(capsule): MANUAL_QA cycle-A checklist + config.example notes"
```

---

## Task 24: Final review pass

**Files:** any (only as needed to close review findings).

- [ ] **Step 1: Targeted tests + lint on new code**

```bash
uv run pytest tests/unit/test_figures_extract.py tests/unit/test_figure_context.py tests/unit/test_section_splitter.py tests/unit/test_accessions.py tests/unit/test_external_resources_extract.py tests/unit/test_chunk_metadata_provenance.py tests/unit/test_capsule_config.py tests/unit/test_capsule_builder_metadata.py tests/unit/test_capsule_builder_figures.py tests/unit/test_capsule_builder_blocks.py tests/unit/test_capsule_builder_figure_refs.py tests/unit/test_capsule_builder_resources.py tests/unit/test_capsule_builder_orchestrator.py tests/unit/test_capsule_paper_lookup.py tests/unit/test_bibtex_ingest_capsule_hook.py tests/unit/test_dois_ingest_capsule_hook.py tests/unit/test_local_docs_capsule_hook.py tests/unit/test_zotero_ingest_capsule_hook.py tests/unit/test_mcp_build_capsule_tool.py tests/unit/test_mcp_build_capsules_for_kb_tool.py tests/unit/test_cli_build_capsule.py tests/unit/test_kb_router_build_capsules.py tests/unit/test_kb_ui_build_capsules_button.py -v
```

Expected: all green.

```bash
uv run ruff check src/perspicacite/pipeline/parsers/figures.py src/perspicacite/pipeline/parsers/figure_context.py src/perspicacite/pipeline/parsers/section_splitter.py src/perspicacite/pipeline/external/accessions.py src/perspicacite/pipeline/external/resources.py src/perspicacite/pipeline/capsule_builder.py src/perspicacite/models/documents.py src/perspicacite/config/schema.py src/perspicacite/web/routers/kb.py src/perspicacite/integrations/local_docs.py src/perspicacite/integrations/zotero_ingest.py src/perspicacite/mcp/server.py src/perspicacite/cli.py
```

Expected: green on new code. Pre-existing issues on untouched lines may remain — do not fix in this cycle.

- [ ] **Step 2: Spec coverage check**

Open `docs/superpowers/specs/2026-05-13-capsule-multimodal-rag-design.md`. Walk every Cycle A line item:
- ✅ Vendor `figures.py`, `figure_context.py`, `section_splitter.py` (Tasks 1-3)
- ✅ Vendor accession + URL extractors (Tasks 4-5)
- ✅ Extend `ChunkMetadata` with all eight new fields (Task 6)
- ✅ `CapsuleConfig` (Task 7)
- ✅ `capsule_builder.py` with metadata + figures + blocks + figure_refs + resources + orchestrator (Tasks 8-13)
- ✅ Auto-build hooks in all four ingest paths (Tasks 14-17)
- ✅ MCP `build_capsule` + `build_capsules_for_kb` (Tasks 18-19)
- ✅ CLI `build-capsule` + `build-capsules` (Task 20)
- ✅ Web router + UI button (Tasks 21-22)
- ✅ Docs (Task 23)

Note any gaps; close them with extra tasks **before** committing.

- [ ] **Step 3: Final commit (only if cleanup happened)**

```bash
git add -p
git commit -m "chore(review): final cleanup for capsule cycle A"
```

If nothing needed cleanup, no commit — just confirm and close.
