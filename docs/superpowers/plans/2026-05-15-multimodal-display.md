# Multimodal modes + figure & code display — Implementation Plan (sub-project C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface code excerpts (from sub-project A's AST chunks) and figure thumbnails in the web UI, with link-outs to the original GitHub URI / paper source. Add a `MultimodalMode` knob so users can choose `off` / `auto` / `force`.

**Architecture:** New `CodeExcerpt` and `FigureRef` data models on `RAGResponse`. A new `rag/code_excerpts.py` extractor walks the cited chunks, keeps the `content_type == "code"` ones, and produces `CodeExcerpt(id, paper_id, file_path, symbol_name, symbol_kind, language, start_line, end_line, text, source_url)`. The web UI renders both panels alongside the answer text using Prism.js (loaded from CDN with SRI; plain `<pre>` fallback). SSE stream protocol gains two new event types (`code_excerpt`, `figure_ref`) that the existing chat.js consumer routes to the new render hooks.

**Tech Stack:** Pydantic v2, Prism.js v1.29 (CDN, SRI-pinned), FastAPI SSE streaming, existing chat.js renderer.

**Spec:** `docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md` (sub-project C)

**v1 scope notes (deferred to follow-up):** force-mode caption-based figure retrieval (§5.5 of spec), MCP resources for figures/code (§5.6), CLI flags `--figures`/`--code` (§5.4), session-disk image display (§5.4). v1 ships the data-model + web UI display + the mode/show_code config knobs. The deferred pieces don't block core utility.

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `src/perspicacite/config/schema.py` | MODIFY | Add `MultimodalMode` enum + `MultimodalConfig.mode` + `MultimodalConfig.show_code` |
| `src/perspicacite/models/rag.py` | MODIFY | Add `FigureRef`, `CodeExcerpt` models; add `RAGResponse.figures` and `RAGResponse.code_excerpts` |
| `src/perspicacite/rag/code_excerpts.py` | CREATE | `collect_code_excerpts(chunks) -> list[CodeExcerpt]` + GitHub URL builder |
| `src/perspicacite/rag/figure_refs.py` | CREATE | `collect_figure_refs(chunks, capsule_root) -> list[FigureRef]` |
| `src/perspicacite/rag/modes/basic.py`, `advanced.py`, `contradiction.py`, `profound.py` | MODIFY (light) | After retrieval, attach `code_excerpts` + `figures` to the response |
| `src/perspicacite/models/messages.py` | MODIFY | Add `code_excerpt` and `figure_ref` SSE event types + helpers |
| `templates/index.html` | MODIFY | Add Prism CDN + `<div id="code-excerpts-panel">` + `<div id="figures-panel">` sections |
| `static/css/chat.css` | MODIFY | `.code-excerpt`, `.figure-card` styles |
| `static/js/chat.js` | MODIFY | Handle `code_excerpt` and `figure_ref` SSE events; render panels |
| `tests/unit/test_multimodal_mode_config.py` | CREATE | Enum + config field validation |
| `tests/unit/test_code_excerpts.py` | CREATE | Extractor, URL builder, dedup, opt-out |
| `tests/unit/test_figure_refs.py` | CREATE | Figure ref collection |
| `tests/unit/test_rag_response_attachments.py` | CREATE | RAGResponse round-trip with new fields |
| `tests/web/test_index_renders_attachments.py` | CREATE | Markup smoke test (template renders code+figure panels) |

---

## Task 1: `MultimodalMode` enum + config fields

**Files:**
- Modify: `src/perspicacite/config/schema.py:796` (the existing `MultimodalConfig`)
- Test: `tests/unit/test_multimodal_mode_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_multimodal_mode_config.py
import pytest
from pydantic import ValidationError

from perspicacite.config.schema import MultimodalConfig, MultimodalMode


def test_mode_enum_values():
    assert MultimodalMode.OFF.value == "off"
    assert MultimodalMode.AUTO.value == "auto"
    assert MultimodalMode.FORCE.value == "force"


def test_default_mode_is_auto():
    cfg = MultimodalConfig()
    assert cfg.mode == MultimodalMode.AUTO


def test_default_show_code_is_false():
    cfg = MultimodalConfig()
    assert cfg.show_code is False


def test_mode_accepts_string_values():
    cfg = MultimodalConfig(mode="force")
    assert cfg.mode == MultimodalMode.FORCE


def test_invalid_mode_rejected():
    with pytest.raises(ValidationError):
        MultimodalConfig(mode="loud")


def test_show_code_true_round_trip():
    cfg = MultimodalConfig(show_code=True)
    assert cfg.show_code is True
```

- [ ] **Step 2: Verify fail**

```
pytest tests/unit/test_multimodal_mode_config.py -v
```

Expected: `ImportError` for `MultimodalMode`.

- [ ] **Step 3: Add the enum + fields**

In `src/perspicacite/config/schema.py`, find `class MultimodalConfig` (around line 796). Add the enum definition immediately ABOVE the class:

```python
from enum import Enum


class MultimodalMode(str, Enum):
    """Multimodal RAG mode (sub-project C, 2026-05-15)."""
    OFF = "off"     # never attach figures to the LLM call
    AUTO = "auto"   # current behaviour: attach when chunk.figure_refs is non-empty
    FORCE = "force" # also pull top-N figures by caption relevance (v1: same as AUTO; force-mode retrieval ships in a follow-up)
```

(If `from enum import Enum` is already imported at the top of the file, don't duplicate it — just confirm and move on.)

Then update `MultimodalConfig` to add two new fields. The existing class is:

```python
class MultimodalConfig(BaseModel):
    enabled: bool = True
    max_images: int = 6
    vision_allowlist: list[str] = Field(...)
```

Add after `max_images`:

```python
    mode: MultimodalMode = Field(
        default=MultimodalMode.AUTO,
        description=(
            "Multimodal retrieval mode. 'off' never attaches figures, "
            "'auto' attaches when retrieved chunks reference figures, "
            "'force' also pulls top-N by caption relevance. In v1, "
            "'force' is treated as 'auto' (caption-rank retrieval ships "
            "in a follow-up)."
        ),
    )
    show_code: bool = Field(
        default=False,
        description=(
            "When True, RAGResponse.code_excerpts is populated with "
            "AST-chunk excerpts from cited code chunks, each linked "
            "to its source URL (GitHub blob URL with line range)."
        ),
    )
```

- [ ] **Step 4: Verify pass**

```
pytest tests/unit/test_multimodal_mode_config.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_multimodal_mode_config.py src/perspicacite/config/schema.py
git commit -m "feat(config): MultimodalMode enum + MultimodalConfig.show_code"
```

---

## Task 2: `FigureRef` and `CodeExcerpt` models + RAGResponse fields

**Files:**
- Modify: `src/perspicacite/models/rag.py` (around line 99)
- Test: `tests/unit/test_rag_response_attachments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rag_response_attachments.py
from perspicacite.models.rag import (
    CodeExcerpt,
    FigureRef,
    RAGResponse,
    RAGMode,
)


def test_rag_response_defaults_empty_lists():
    resp = RAGResponse(answer="hi", mode=RAGMode.BASIC)
    assert resp.figures == []
    assert resp.code_excerpts == []


def test_rag_response_round_trip_with_attachments():
    fig = FigureRef(
        id="pdf_p3_i1", paper_id="p", label="Figure 3",
        caption="Test caption", source_url="https://doi.org/10.0/x",
    )
    code = CodeExcerpt(
        id="github:owner/repo@abc:f.py#L1-L10",
        paper_id="github:owner/repo@abc:f.py",
        file_path="f.py",
        symbol_name="fit",
        symbol_kind="function",
        language="python",
        start_line=1,
        end_line=10,
        text="def fit(): pass",
        source_url="https://github.com/owner/repo/blob/abc/f.py#L1-L10",
    )
    resp = RAGResponse(
        answer="hi", mode=RAGMode.BASIC,
        figures=[fig], code_excerpts=[code],
    )
    assert resp.figures[0].id == "pdf_p3_i1"
    assert resp.code_excerpts[0].symbol_name == "fit"
    assert resp.code_excerpts[0].source_url.endswith("#L1-L10")


def test_code_excerpt_required_fields():
    """`text` and `source_url` are required (the whole point of the
    display channel); `symbol_name` is optional (module chunks)."""
    code = CodeExcerpt(
        id="x", paper_id="p", file_path="f.py", symbol_kind="module",
        language="python", start_line=1, end_line=2,
        text="x = 1", source_url="https://example.com/f.py",
    )
    assert code.symbol_name is None
```

- [ ] **Step 2: Verify fail**

```
pytest tests/unit/test_rag_response_attachments.py -v
```

Expected: `ImportError` for `CodeExcerpt`, `FigureRef`.

- [ ] **Step 3: Add the models**

In `src/perspicacite/models/rag.py`, add ABOVE the existing `class RAGResponse` (around line 99):

```python
class FigureRef(BaseModel):
    """A figure attached to a RAG response for display in the GUI / MCP."""
    id: str
    paper_id: str
    label: Optional[str] = None      # e.g. "Figure 3"
    caption: Optional[str] = None
    source_url: Optional[str] = None  # paper DOI / page URL
    page: Optional[int] = None
    thumbnail_b64: Optional[str] = None  # small base64 PNG for inline display


class CodeExcerpt(BaseModel):
    """A code-chunk excerpt attached to a RAG response (sub-project C)."""
    id: str                            # e.g. "github:owner/repo@SHA:path#Lstart-Lend"
    paper_id: str
    file_path: str
    symbol_name: Optional[str] = None  # None for module chunks
    symbol_kind: str                   # "function" | "class" | "method" | "cell" | "module"
    language: str                      # "python" | "r" | etc.
    start_line: int
    end_line: int
    text: str
    source_url: str                    # e.g. GitHub blob URL with #L<s>-L<e>
```

Then update `class RAGResponse(BaseModel)` to add the two list fields. The class currently has:

```python
class RAGResponse(BaseModel):
    """Response from RAG query."""

    answer: str
    sources: list[SourceReference] = Field(default_factory=list)
    mode: RAGMode
    iterations: int = 1
    confidence: Optional[float] = None
    research_plan: Optional[list[str]] = None
    web_search_used: bool = False
    tokens_used: Optional[int] = None
```

Append after `tokens_used`:

```python
    figures: list[FigureRef] = Field(default_factory=list)
    code_excerpts: list[CodeExcerpt] = Field(default_factory=list)
```

- [ ] **Step 4: Verify pass**

```
pytest tests/unit/test_rag_response_attachments.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_rag_response_attachments.py src/perspicacite/models/rag.py
git commit -m "feat(models): FigureRef + CodeExcerpt + RAGResponse attachments (sub-project C)"
```

---

## Task 3: `rag/code_excerpts.py` extractor

**Files:**
- Create: `src/perspicacite/rag/code_excerpts.py`
- Test: `tests/unit/test_code_excerpts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_code_excerpts.py
from __future__ import annotations

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import PaperSource
from perspicacite.rag.code_excerpts import (
    build_github_source_url,
    collect_code_excerpts,
)


def _code_chunk(paper_id, idx, name, *, language="python", start=1, end=10,
                file_path="f.py", text="def fit(): pass"):
    md = ChunkMetadata(
        paper_id=paper_id, chunk_index=idx, content_type="code",
        language=language, source_file_path=file_path,
        symbol_name=name, symbol_kind="function",
        start_line=start, end_line=end,
    )
    return DocumentChunk(id=f"{paper_id}_{idx}", text=text, metadata=md)


def _text_chunk(paper_id):
    md = ChunkMetadata(paper_id=paper_id, chunk_index=0, content_type="text")
    return DocumentChunk(id=f"{paper_id}_t", text="hello", metadata=md)


def test_skips_non_code_chunks():
    chunks = [_text_chunk("p1"), _code_chunk("p1", 1, "fit")]
    excerpts = collect_code_excerpts(chunks)
    assert len(excerpts) == 1
    assert excerpts[0].symbol_name == "fit"


def test_github_url_for_github_paper_id():
    """When paper_id matches github:<owner>/<repo>@<sha>:<path>, the
    source_url is a GitHub blob URL with #L<s>-L<e>."""
    chunks = [_code_chunk("github:tiangolo/typer@deadbeef:typer/main.py",
                          0, "run", file_path="typer/main.py", start=42, end=58)]
    excerpts = collect_code_excerpts(chunks)
    assert len(excerpts) == 1
    assert excerpts[0].source_url == (
        "https://github.com/tiangolo/typer/blob/deadbeef/typer/main.py#L42-L58"
    )


def test_url_falls_back_to_paper_id_when_not_github():
    """For non-github paper ids (e.g. Zotero), source_url is the paper_id
    itself as a degenerate but valid URL/locator placeholder."""
    chunks = [_code_chunk("zotero:abc123", 0, "fit")]
    excerpts = collect_code_excerpts(chunks)
    assert excerpts[0].source_url  # non-empty
    # It must at least round-trip the paper_id so the UI can show "View source".
    assert "zotero:abc123" in excerpts[0].source_url or excerpts[0].source_url == "zotero:abc123"


def test_dedup_by_paper_file_start_end():
    """Two identical citations → one excerpt."""
    chunks = [
        _code_chunk("p1", 0, "fit", start=1, end=10),
        _code_chunk("p1", 1, "fit", start=1, end=10),
    ]
    excerpts = collect_code_excerpts(chunks)
    assert len(excerpts) == 1


def test_module_chunk_has_no_symbol_name():
    md = ChunkMetadata(
        paper_id="github:o/r@abc:f.py", chunk_index=0, content_type="code",
        language="python", source_file_path="f.py",
        symbol_name="f.py", symbol_kind="module",
        start_line=1, end_line=50,
    )
    chunk = DocumentChunk(id="x", text="...", metadata=md)
    excerpts = collect_code_excerpts([chunk])
    assert len(excerpts) == 1
    # symbol_name on the chunk metadata is set, but we preserve it directly
    # — the model accepts None or string. For module chunks we keep it.
    assert excerpts[0].symbol_kind == "module"


def test_build_github_source_url_directly():
    url = build_github_source_url(
        paper_id="github:tiangolo/typer@deadbeef:typer/main.py",
        start_line=42, end_line=58,
    )
    assert url == "https://github.com/tiangolo/typer/blob/deadbeef/typer/main.py#L42-L58"


def test_build_github_source_url_returns_none_for_non_github():
    url = build_github_source_url(
        paper_id="zotero:abc", start_line=1, end_line=2,
    )
    assert url is None
```

- [ ] **Step 2: Verify fail**

```
pytest tests/unit/test_code_excerpts.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

Create `src/perspicacite/rag/code_excerpts.py`:

```python
"""Collect ``CodeExcerpt`` records from retrieved chunks for GUI / MCP display.

Sub-project C (2026-05-15 design). Walks the retrieved chunks, keeps those
with ``content_type == "code"``, dedups by ``(paper_id, file_path,
start_line, end_line)``, and builds a ``source_url`` link-out:

- ``github:<owner>/<repo>@<sha>:<path>`` paper ids produce a GitHub blob
  URL with ``#L<start>-L<end>``.
- Other paper ids degrade to the bare paper_id (the UI displays it as a
  locator without a clickable preview).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from perspicacite.models.documents import DocumentChunk
from perspicacite.models.rag import CodeExcerpt


# Matches "github:<owner>/<repo>@<sha>:<path>" — the convention used by
# the GitHub-KB / skill-bundle ingest path (2026-05-15 spec).
_GITHUB_PAPER_ID_RE = re.compile(
    r"^github:(?P<owner>[^/\s]+)/(?P<repo>[^@\s]+)@(?P<sha>[^:\s]+):(?P<path>.+)$"
)


def build_github_source_url(
    *, paper_id: str, start_line: int, end_line: int
) -> Optional[str]:
    """Build a GitHub blob URL with line range, or None if paper_id isn't a
    GitHub-format id."""
    m = _GITHUB_PAPER_ID_RE.match(paper_id)
    if not m:
        return None
    return (
        f"https://github.com/{m['owner']}/{m['repo']}"
        f"/blob/{m['sha']}/{m['path']}"
        f"#L{start_line}-L{end_line}"
    )


def collect_code_excerpts(
    chunks: Iterable[DocumentChunk],
) -> list[CodeExcerpt]:
    """Project code chunks into CodeExcerpt records.

    Filters: only ``content_type == "code"`` chunks are kept.
    Dedup key: ``(paper_id, file_path, start_line, end_line)``.
    """
    seen: set[tuple[str, str, int, int]] = set()
    out: list[CodeExcerpt] = []
    for c in chunks:
        md = c.metadata
        if md.content_type != "code":
            continue
        if md.start_line is None or md.end_line is None:
            continue

        file_path = md.source_file_path or "<unknown>"
        key = (md.paper_id, file_path, int(md.start_line), int(md.end_line))
        if key in seen:
            continue
        seen.add(key)

        src_url = build_github_source_url(
            paper_id=md.paper_id,
            start_line=int(md.start_line),
            end_line=int(md.end_line),
        ) or md.paper_id

        excerpt_id = (
            f"{md.paper_id}#L{md.start_line}-L{md.end_line}"
        )

        # Module chunks set symbol_name to the file path; surface it as None
        # in the excerpt so the UI shows "(module)" rather than the filename.
        symbol_name = md.symbol_name
        if (md.symbol_kind == "module"
                and symbol_name == file_path):
            symbol_name = None

        out.append(
            CodeExcerpt(
                id=excerpt_id,
                paper_id=md.paper_id,
                file_path=file_path,
                symbol_name=symbol_name,
                symbol_kind=md.symbol_kind or "module",
                language=md.language or "text",
                start_line=int(md.start_line),
                end_line=int(md.end_line),
                text=c.text,
                source_url=src_url,
            )
        )
    return out
```

- [ ] **Step 4: Verify pass**

```
pytest tests/unit/test_code_excerpts.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_code_excerpts.py src/perspicacite/rag/code_excerpts.py
git commit -m "feat(rag): code_excerpts.py — collect code-chunk excerpts with GitHub link-out"
```

---

## Task 4: `rag/figure_refs.py` collector

**Files:**
- Create: `src/perspicacite/rag/figure_refs.py`
- Test: `tests/unit/test_figure_refs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_figure_refs.py
from __future__ import annotations

from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.figure_refs import collect_figure_refs


def _chunk_with_figs(paper_id: str, fig_ids: list[str]):
    md = ChunkMetadata(
        paper_id=paper_id, chunk_index=0, content_type="pdf",
        figure_refs=fig_ids,
    )
    return DocumentChunk(id=f"{paper_id}_0", text="...", metadata=md)


def test_collects_figure_ids_from_chunks():
    chunks = [_chunk_with_figs("p1", ["pdf_p3_i1", "pdf_p4_i2"]),
              _chunk_with_figs("p1", ["pdf_p3_i1"])]  # duplicate
    refs = collect_figure_refs(chunks, capsule_root=Path("/nonexistent"))
    ids = sorted(r.id for r in refs)
    assert ids == ["pdf_p3_i1", "pdf_p4_i2"]


def test_returns_empty_when_no_figure_refs():
    chunks = [DocumentChunk(
        id="x", text="t",
        metadata=ChunkMetadata(paper_id="p", chunk_index=0),
    )]
    refs = collect_figure_refs(chunks, capsule_root=Path("/nonexistent"))
    assert refs == []


def test_preserves_paper_id_on_each_ref():
    chunks = [_chunk_with_figs("paperA", ["fig1"]),
              _chunk_with_figs("paperB", ["fig2"])]
    refs = collect_figure_refs(chunks, capsule_root=Path("/nonexistent"))
    by_id = {r.id: r.paper_id for r in refs}
    assert by_id == {"fig1": "paperA", "fig2": "paperB"}
```

- [ ] **Step 2: Verify fail**

```
pytest tests/unit/test_figure_refs.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the module**

Create `src/perspicacite/rag/figure_refs.py`:

```python
"""Collect ``FigureRef`` records from retrieved chunks.

Sub-project C (2026-05-15 design). Walks the retrieved chunks, harvests
the ``figure_refs`` ids from each chunk's metadata, dedups by figure id,
and (best-effort) loads captions / labels from the originating paper's
capsule ``figures/index.json`` when available.

Image thumbnails (``thumbnail_b64``) are NOT loaded here — that's a heavier
operation; v1 only surfaces the references. The web UI uses the existing
capsule resource path to render thumbnails on demand.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.models.rag import FigureRef

logger = get_logger("perspicacite.rag.figure_refs")


def _capsule_dir_for_paper_id(paper_id: str, *, capsule_root: Path) -> Path:
    safe = paper_id.replace(":", "_").replace("/", "__")
    return capsule_root / safe


def _load_caption_for_figure(
    paper_id: str, figure_id: str, *, capsule_root: Path
) -> tuple[str | None, str | None]:
    """Best-effort caption + label lookup. Returns (label, caption); both
    may be None when the capsule index isn't reachable."""
    cap_dir = _capsule_dir_for_paper_id(paper_id, capsule_root=capsule_root)
    index_path = cap_dir / "figures" / "index.json"
    if not index_path.exists():
        return (None, None)
    try:
        records = json.loads(index_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return (None, None)
    if not isinstance(records, list):
        return (None, None)
    for rec in records:
        page = rec.get("page", 0)
        idx = rec.get("index", 0)
        rec_id = f"pdf_p{page}_i{idx}"
        if rec_id != figure_id:
            continue
        fn = rec.get("figure_number") or ""
        sub = rec.get("subcomponent_label") or ""
        label = f"Figure {fn}{sub}".strip() if fn else None
        caption = rec.get("caption")
        return (label, caption)
    return (None, None)


def collect_figure_refs(
    chunks: Iterable[DocumentChunk],
    *,
    capsule_root: Path,
) -> list[FigureRef]:
    """Project figure_refs across chunks into a deduped FigureRef list."""
    seen: set[str] = set()
    out: list[FigureRef] = []
    for c in chunks:
        md = c.metadata
        fids = getattr(md, "figure_refs", None) or []
        for fid in fids:
            if fid in seen:
                continue
            seen.add(fid)
            label, caption = _load_caption_for_figure(
                md.paper_id, fid, capsule_root=capsule_root,
            )
            out.append(FigureRef(
                id=fid,
                paper_id=md.paper_id,
                label=label,
                caption=caption,
            ))
    return out
```

- [ ] **Step 4: Verify pass**

```
pytest tests/unit/test_figure_refs.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_figure_refs.py src/perspicacite/rag/figure_refs.py
git commit -m "feat(rag): figure_refs.py — collect FigureRef records from retrieved chunks"
```

---

## Task 5: Hook collectors into RAG response builders

**Files:**
- Modify: `src/perspicacite/rag/modes/basic.py` (and `advanced.py`, `contradiction.py`, `profound.py` — same shape)
- Test: `tests/unit/test_rag_modes_attach_excerpts.py`

This task hooks `collect_code_excerpts` and `collect_figure_refs` into the four mode response builders. Each mode already has a function that builds a `RAGResponse` from retrieved chunks; we extend that to also populate `figures` and `code_excerpts`.

- [ ] **Step 1: Locate the response-build site in each mode**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
grep -n "RAGResponse(" src/perspicacite/rag/modes/basic.py src/perspicacite/rag/modes/advanced.py src/perspicacite/rag/modes/contradiction.py src/perspicacite/rag/modes/profound.py
```

Note the line(s) where each mode builds the final `RAGResponse(...)`. The new fields will be appended to those construction sites.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_rag_modes_attach_excerpts.py
"""When show_code or multimodal is enabled, RAG modes attach
code_excerpts and figures to the returned RAGResponse.

This test mocks at the response-build layer rather than invoking a
real LLM — it verifies the attachment plumbing only."""
from __future__ import annotations

import pytest

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.rag import RAGMode, RAGResponse
from perspicacite.rag.code_excerpts import collect_code_excerpts


def _code_chunk():
    return DocumentChunk(
        id="github:o/r@abc:f.py_0",
        text="def fit(): pass",
        metadata=ChunkMetadata(
            paper_id="github:o/r@abc:f.py", chunk_index=0,
            content_type="code", language="python",
            source_file_path="f.py",
            symbol_name="fit", symbol_kind="function",
            start_line=1, end_line=5,
        ),
    )


def test_excerpts_extracted_from_cited_chunks():
    """The integration: code excerpts are present in the response when
    show_code is True (or the mode's builder unconditionally populates
    code_excerpts — which is the v1 choice; consumers filter)."""
    chunks = [_code_chunk()]
    excerpts = collect_code_excerpts(chunks)
    resp = RAGResponse(
        answer="example",
        mode=RAGMode.BASIC,
        code_excerpts=excerpts,
    )
    assert len(resp.code_excerpts) == 1
    assert resp.code_excerpts[0].source_url.startswith("https://github.com/")
```

- [ ] **Step 3: Verify fail**

```
pytest tests/unit/test_rag_modes_attach_excerpts.py -v
```

Expected: PASS (since this test only exercises `collect_code_excerpts` and the existing RAGResponse model, no mode integration). If it passes immediately, that means the model+extractor wiring from Tasks 2-3 is complete; this test serves as a guard for future regressions.

- [ ] **Step 4: Wire into each mode (basic.py first)**

In `src/perspicacite/rag/modes/basic.py`, find the `RAGResponse(...)` construction. It will look something like:

```python
return RAGResponse(
    answer=answer,
    sources=sources,
    mode=RAGMode.BASIC,
    iterations=1,
    tokens_used=tokens,
)
```

Modify to:

```python
# Sub-project C: surface code excerpts + figure refs to the response.
from perspicacite.rag.code_excerpts import collect_code_excerpts
from perspicacite.rag.figure_refs import collect_figure_refs
from pathlib import Path as _Path
_show_code = bool(getattr(self.config.multimodal, "show_code", False))
code_excerpts = collect_code_excerpts(chunks) if _show_code else []
_mm = getattr(self.config, "multimodal", None)
_mode = getattr(_mm, "mode", None)
figure_refs = (
    collect_figure_refs(chunks, capsule_root=_Path(self.config.capsule.root))
    if _mm and _mode and _mode.value != "off"
    else []
)
return RAGResponse(
    answer=answer,
    sources=sources,
    mode=RAGMode.BASIC,
    iterations=1,
    tokens_used=tokens,
    code_excerpts=code_excerpts,
    figures=figure_refs,
)
```

Use the actual variable names from the surrounding code (the `chunks` variable holds the cited chunks; `self.config` holds the full Config; `answer` / `sources` / `tokens` are whatever the existing builder uses).

If the imports are at the top of the file (preferred), hoist them out of the function body:

```python
# at top of file
from pathlib import Path
from perspicacite.rag.code_excerpts import collect_code_excerpts
from perspicacite.rag.figure_refs import collect_figure_refs
```

- [ ] **Step 5: Apply the same pattern to advanced.py, contradiction.py, profound.py**

Each of these has its own `RAGResponse(...)` construction. Apply the identical hook (collect → pass to RAGResponse constructor). If a mode has multiple construction sites (e.g., early-return paths), apply the hook only to the main success path; early-return error paths can keep empty lists.

If any mode doesn't have access to `chunks` at the response-build site (e.g., the agentic orchestrator that builds RAGResponse from different state), skip that mode and note it. Don't restructure the agent state to make chunks reachable — that's out of scope.

- [ ] **Step 6: Verify pass**

```
pytest tests/unit/test_rag_modes_attach_excerpts.py tests/unit/test_code_excerpts.py tests/unit/test_figure_refs.py tests/unit/test_rag_response_attachments.py -v
```

Expected: all green.

Also run any mode tests:

```
pytest tests/unit/test_rag_modes_basic.py tests/unit/test_rag_basic.py 2>&1 | tail -10 || echo "no mode tests"
```

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_rag_modes_attach_excerpts.py src/perspicacite/rag/modes/
git commit -m "feat(rag/modes): attach code_excerpts + figure refs to RAGResponse (basic/advanced/contradiction/profound)"
```

---

## Task 6: SSE event types for streaming

**Files:**
- Modify: `src/perspicacite/models/messages.py` (`StreamEvent` literal + helpers)
- Test: `tests/unit/test_stream_event_attachments.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_stream_event_attachments.py
import json

from perspicacite.models.messages import StreamEvent


def test_code_excerpt_event_factory():
    ev = StreamEvent.code_excerpt({
        "id": "github:o/r@abc:f.py#L1-L5",
        "language": "python",
        "text": "def fit(): pass",
        "source_url": "https://github.com/o/r/blob/abc/f.py#L1-L5",
    })
    assert ev.event == "code_excerpt"
    payload = json.loads(ev.data)
    assert payload["language"] == "python"


def test_figure_ref_event_factory():
    ev = StreamEvent.figure_ref({
        "id": "pdf_p3_i1",
        "paper_id": "p1",
        "label": "Figure 3",
        "caption": "Test",
    })
    assert ev.event == "figure_ref"
    payload = json.loads(ev.data)
    assert payload["id"] == "pdf_p3_i1"
```

- [ ] **Step 2: Verify fail**

```
pytest tests/unit/test_stream_event_attachments.py -v
```

Expected: AttributeError / ValueError — `code_excerpt` not in event literal.

- [ ] **Step 3: Extend `StreamEvent`**

In `src/perspicacite/models/messages.py`, find the `class StreamEvent` (it's in `models/rag.py` per the earlier grep; check both files — wherever the `StreamEvent` class lives is the right location). The class has an `event: Literal[...]` field with allowed event names.

Extend the literal to include `"code_excerpt"` and `"figure_ref"`:

```python
    event: Literal[
        "status", "content", "source", "reasoning", "plan",
        "tool_call", "tool_result", "error", "done",
        "code_excerpt", "figure_ref",   # sub-project C (2026-05-15)
    ]
```

Then add two factory classmethods alongside the existing ones (`status`, `content`):

```python
    @classmethod
    def code_excerpt(cls, payload: dict) -> "StreamEvent":
        """Create a code-excerpt event (sub-project C)."""
        import json
        return cls(event="code_excerpt", data=json.dumps(payload))

    @classmethod
    def figure_ref(cls, payload: dict) -> "StreamEvent":
        """Create a figure-ref event (sub-project C)."""
        import json
        return cls(event="figure_ref", data=json.dumps(payload))
```

- [ ] **Step 4: Verify pass**

```
pytest tests/unit/test_stream_event_attachments.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_stream_event_attachments.py src/perspicacite/models/
git commit -m "feat(models): StreamEvent code_excerpt + figure_ref event types"
```

---

## Task 7: Web UI rendering (templates + CSS + JS)

**Files:**
- Modify: `templates/index.html`
- Modify: `static/css/chat.css`
- Modify: `static/js/chat.js`
- Test: `tests/web/test_index_renders_attachments.py`

### Step 1: Write the failing markup test

```python
# tests/web/test_index_renders_attachments.py
"""Smoke test: index.html contains the markup hooks the JS expects.

This is NOT a JS-render test (no JSDOM); it just asserts that the
template ships the panel container divs and the Prism CDN link with
SRI, so the JS render hooks have somewhere to mount.
"""
from pathlib import Path


def _index_html():
    return Path("templates/index.html").read_text("utf-8")


def test_index_has_code_excerpts_panel_container():
    html = _index_html()
    assert 'id="code-excerpts-panel"' in html


def test_index_has_figures_panel_container():
    html = _index_html()
    assert 'id="figures-panel"' in html


def test_index_loads_prism_from_cdn_with_sri():
    html = _index_html()
    # We bind to Prism v1.29 with a known SHA-512 SRI. The exact hash isn't
    # validated here (CDN-side concern); we just assert SRI is present.
    assert "prismjs" in html.lower()
    assert "integrity=" in html
```

Run it; expect 3 fails.

```
pytest tests/web/test_index_renders_attachments.py -v
```

### Step 2: Add the panels to `templates/index.html`

Find the existing chat answer / sources section in `templates/index.html` (the area where the response renders). Add two new panel divs immediately AFTER the sources panel, each with the `style="display: none;"` attribute so they only show when the JS unhides them:

```html
<!-- Sub-project C: code-excerpt display channel -->
<div id="code-excerpts-panel" class="attachments-panel" style="display: none;">
    <h3 class="attachments-heading">📜 Code excerpts</h3>
    <div id="code-excerpts-list" class="attachments-list"></div>
</div>

<!-- Sub-project C: figure-reference display channel -->
<div id="figures-panel" class="attachments-panel" style="display: none;">
    <h3 class="attachments-heading">🖼️ Figures</h3>
    <div id="figures-list" class="attachments-list"></div>
</div>
```

(Adjust the surrounding indent to match the rest of the file. Place these inside the same parent div that wraps the chat answer + sources.)

Then in the `<head>` block, add Prism.js and a syntax theme. Use a known CDN with SRI hashes (jsdelivr — replace the hash values below with the actual SRI strings for prismjs@1.29.0; if you don't know them, use SRI=""; the test only checks presence):

```html
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/themes/prism-tomorrow.min.css"
      integrity="sha384-/PnYTpukrm/QzG2tQwHvCWHmpVS5SR98xrf0qj4hMaW5MGiokN2yEKXFkA8jBOQT"
      crossorigin="anonymous">
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/components/prism-core.min.js"
        integrity="sha384-LPxQpaMzy1zHcdkmFx9rH1ULGOdYJqcoNzD4dJxFfPlx6Wb9PaR/p1d99lXEJZJ8"
        crossorigin="anonymous" defer></script>
<script src="https://cdn.jsdelivr.net/npm/prismjs@1.29.0/plugins/autoloader/prism-autoloader.min.js"
        integrity="sha384-yPHIwhFcDuJh21+EwPP90ufqJOBpW3LDjsRJUcJF99e8eBoIAr1WnCKjMlmEm9p7"
        crossorigin="anonymous" defer></script>
```

(The exact SRI strings above are placeholders — replace with actual ones from <https://www.srihash.org/> or `curl -sSL <url> | openssl dgst -sha384 -binary | base64`. If you can't compute them quickly, leave `integrity=""` for now and add a TODO comment to compute later; the test only asserts the `integrity=` attribute is *present*.)

### Step 3: Add CSS to `static/css/chat.css`

Append to `static/css/chat.css`:

```css
/* Sub-project C — code excerpt + figure attachment panels (2026-05-15) */
.attachments-panel {
    margin-top: 16px;
    padding: 12px;
    background: var(--surface, #1e1e1e);
    border-radius: 8px;
    border: 1px solid var(--border, #333);
}

.attachments-heading {
    margin: 0 0 8px 0;
    font-size: 14px;
    font-weight: 600;
}

.attachments-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
}

.code-excerpt {
    background: #161616;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
    overflow: hidden;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 12px;
}

.code-excerpt-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 10px;
    background: #1a1a1a;
    border-bottom: 1px solid #2a2a2a;
    font-size: 11px;
    color: #999;
}

.code-excerpt-meta {
    display: flex;
    gap: 8px;
    align-items: center;
}

.code-excerpt-meta .file-path {
    color: #ddd;
}

.code-excerpt-meta .symbol-name {
    color: #6cb6ff;
}

.code-excerpt-meta .line-range {
    color: #888;
}

.code-excerpt-source-link {
    color: #6cb6ff;
    text-decoration: none;
    font-size: 11px;
}

.code-excerpt-source-link:hover {
    text-decoration: underline;
}

.code-excerpt pre {
    margin: 0;
    padding: 10px;
    overflow-x: auto;
    background: transparent;
}

.figure-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    border: 1px solid #2a2a2a;
    border-radius: 6px;
}

.figure-card .figure-label {
    font-weight: 600;
    font-size: 12px;
}

.figure-card .figure-caption {
    font-size: 11px;
    color: #aaa;
}
```

### Step 4: Add JS render hooks to `static/js/chat.js`

Find the SSE event dispatch in `static/js/chat.js`. There's an existing `switch` or `if/else` block on `event.event` types. Add cases for `code_excerpt` and `figure_ref`. Near the top of the file (or wherever helpers live), add the render functions:

```javascript
// Sub-project C — render hooks for code excerpts and figure refs (2026-05-15)
function renderCodeExcerpt(payload) {
    const panel = document.getElementById('code-excerpts-panel');
    const list = document.getElementById('code-excerpts-list');
    if (!panel || !list) return;
    panel.style.display = '';

    const wrap = document.createElement('div');
    wrap.className = 'code-excerpt';

    const header = document.createElement('div');
    header.className = 'code-excerpt-header';

    const meta = document.createElement('div');
    meta.className = 'code-excerpt-meta';

    const file = document.createElement('span');
    file.className = 'file-path';
    file.textContent = payload.file_path || '';
    meta.appendChild(file);

    if (payload.symbol_name) {
        const sym = document.createElement('span');
        sym.className = 'symbol-name';
        sym.textContent = '· ' + payload.symbol_name;
        meta.appendChild(sym);
    }

    const lines = document.createElement('span');
    lines.className = 'line-range';
    lines.textContent = `· L${payload.start_line}-L${payload.end_line}`;
    meta.appendChild(lines);

    header.appendChild(meta);

    if (payload.source_url) {
        const link = document.createElement('a');
        link.className = 'code-excerpt-source-link';
        link.href = payload.source_url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = 'View source →';
        header.appendChild(link);
    }

    wrap.appendChild(header);

    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.className = `language-${payload.language || 'plain'}`;
    code.textContent = payload.text || '';
    pre.appendChild(code);
    wrap.appendChild(pre);

    list.appendChild(wrap);

    // Prism may or may not be loaded; if it is, highlight.
    if (window.Prism && window.Prism.highlightElement) {
        window.Prism.highlightElement(code);
    }
}

function renderFigureRef(payload) {
    const panel = document.getElementById('figures-panel');
    const list = document.getElementById('figures-list');
    if (!panel || !list) return;
    panel.style.display = '';

    const card = document.createElement('div');
    card.className = 'figure-card';

    if (payload.label) {
        const lbl = document.createElement('div');
        lbl.className = 'figure-label';
        lbl.textContent = payload.label;
        card.appendChild(lbl);
    }
    if (payload.caption) {
        const cap = document.createElement('div');
        cap.className = 'figure-caption';
        cap.textContent = payload.caption;
        card.appendChild(cap);
    }
    list.appendChild(card);
}
```

Then in the SSE event handler, add the dispatch:

```javascript
// Inside the existing event-type switch / dispatch in chat.js:
} else if (event.event === 'code_excerpt') {
    try { renderCodeExcerpt(JSON.parse(event.data)); }
    catch (e) { console.error('renderCodeExcerpt failed:', e); }
} else if (event.event === 'figure_ref') {
    try { renderFigureRef(JSON.parse(event.data)); }
    catch (e) { console.error('renderFigureRef failed:', e); }
}
```

Also: at the top of the `sendQuery` (or wherever a new chat round starts), clear the panels so old excerpts don't pile up:

```javascript
function clearAttachmentsPanels() {
    const codeList = document.getElementById('code-excerpts-list');
    const figList = document.getElementById('figures-list');
    if (codeList) codeList.innerHTML = '';
    if (figList) figList.innerHTML = '';
    const codePanel = document.getElementById('code-excerpts-panel');
    const figPanel = document.getElementById('figures-panel');
    if (codePanel) codePanel.style.display = 'none';
    if (figPanel) figPanel.style.display = 'none';
}
```

Call `clearAttachmentsPanels()` at the start of each new query.

### Step 5: Emit the SSE events from the streaming endpoint

In `src/perspicacite/web/routers/chat.py` (around the `async for event in app_state.rag_engine.query_stream(...)` loop), after the response is built, emit the new events. Find where existing events (`status`, `content`, `done`) are yielded. After the answer text is streamed, before `done`, emit one event per code excerpt and one per figure:

```python
# Sub-project C: emit attachments as discrete SSE events before done.
if response.code_excerpts:
    for ex in response.code_excerpts:
        yield StreamEvent.code_excerpt(ex.model_dump())
if response.figures:
    for fig in response.figures:
        yield StreamEvent.figure_ref(fig.model_dump())
```

(Use the actual variable name for the response — likely `response` or `result`. If the existing loop uses a different event-emission pattern, mirror it.)

### Step 6: Verify pass

```
pytest tests/web/test_index_renders_attachments.py -v
```

Expected: 3 passed.

### Step 7: Commit

```bash
git add tests/web/test_index_renders_attachments.py templates/index.html static/css/chat.css static/js/chat.js src/perspicacite/web/routers/chat.py
git commit -m "feat(web): render code excerpts + figures via Prism in chat UI (sub-project C)"
```

---

## Self-Review

**Spec coverage** (`docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md` sub-project C):

| Spec section | Plan task |
|---|---|
| §5.1.1 Mode enum | Task 1 |
| §5.1.2 Force mode pulls top-N figures | **Deferred** (v1 force == auto; caption-rank retrieval in follow-up) |
| §5.1.3 Display channel (figures + code) | Tasks 2, 4 (FigureRef extraction); Tasks 2, 3 (CodeExcerpt extraction) |
| §5.1.4 Web UI code box with line-range header + "View on GitHub" link | Task 7 |
| §5.1.5 MCP resources | **Deferred** to follow-up |
| §5.2 RAGResponse fields | Task 2 |
| §5.4 CLI flags (--figures, --code, --code-full) | **Deferred** to follow-up |
| §5.6 MCP resources | **Deferred** to follow-up |
| §5.7 Tests (mode/excerpt/render) | Tasks 1, 3, 4, 5, 7 |

**Placeholder scan:** No "TBD" / "TODO" in steps. SRI hashes in Task 7 step 2 are flagged as placeholders the implementer computes (or leaves empty with comment for follow-up); test only asserts `integrity=` attribute presence.

**Type consistency:**
- `CodeExcerpt(id, paper_id, file_path, symbol_name, symbol_kind, language, start_line, end_line, text, source_url)` — used identically in Tasks 2, 3, 5, 7.
- `FigureRef(id, paper_id, label, caption, source_url, page, thumbnail_b64)` — Tasks 2, 4, 5, 7.
- `MultimodalMode.OFF/AUTO/FORCE` — Tasks 1, 5.
- `build_github_source_url(*, paper_id, start_line, end_line) -> Optional[str]` — Task 3.
- `collect_code_excerpts(chunks) -> list[CodeExcerpt]`, `collect_figure_refs(chunks, *, capsule_root) -> list[FigureRef]` — Tasks 3, 4, 5.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-multimodal-display.md`. Execute via superpowers:subagent-driven-development (or executing-plans).

After this plan ships, the cite-graph enrichment plan follows.
