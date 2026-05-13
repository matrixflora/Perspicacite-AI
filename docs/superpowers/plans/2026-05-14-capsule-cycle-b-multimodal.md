# Capsule Cycle B — Multimodal RAG + CapsuleReader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Light up the multimodal RAG path on top of Cycle A capsules — retrieved chunks' figure_refs pull images into the LLM call (vision-capable models), the UI inlines those figures into answer text, and ASB-shaped capsule directories become first-class ingest targets.

**Architecture:** A single `rag/multimodal.py` helper (1) collects `figure_refs` across retrieved chunks, (2) loads images from each chunk's capsule, (3) builds a litellm multimodal message via ASB's `build_multimodal_messages`. Each of the six RAG modes (`basic`, `advanced`, `profound`, `agentic`, `literature_survey`, `contradiction`) gets one 2-line hook around its final user-facing `llm.complete(...)` call. A new `GET /api/capsule/{paper_id}/figure/{fig_id}` endpoint serves PNG bytes from disk; `chat.js` post-processes assistant text to rewrite `pdf_p<page>_i<idx>` tokens into inline thumbnails (click-to-expand). A `CapsuleReader` integration handles ASB-style capsule directories at ingest time (detected via `metadata.json` containing `capsule_version`).

**Tech Stack:** Python 3.11+, litellm (already wired), pydantic v2, FastAPI, Click CLI, vanilla JS for chat UI.

---

## Pre-flight notes for implementer subagents

- All ASB-mirrored extraction helpers (`figures.py`, `figure_context.py`, `section_splitter.py`, `external/accessions.py`, `external/resources.py`) were vendored in Cycle A. Do not re-vendor.
- `build_multimodal_messages`, `load_image_b64`, `supports_vision`, `format_figures_block`, `FigureContext` already exist in `src/perspicacite/pipeline/parsers/figure_context.py`. Re-use them; do not duplicate.
- `ChunkMetadata` already exposes `figure_refs: list[str]`, `parent_paper_id: str | None`, `is_external: bool`, `source_section`, `page`, `char_span`, `table_refs`, `resource_refs`. See `src/perspicacite/models/documents.py:10-44`.
- Capsule layout: `<capsule_root>/<safe_paper_id>/figures/<filename>` where filename is `fig_p<page:03d>_i<idx:02d>.<ext>` and `<safe_paper_id>` is `paper.id.replace(":", "_").replace("/", "__")`. See `pipeline/capsule_builder.py:33`.
- Test memory pressure: full pytest collection OOMs (chromadb/torch). **Run only the new test file per task** with `pytest -xvs tests/unit/<new_file>.py`. Do NOT run the full suite per task.
- Commit per task. Do not amend.
- LLM client (`src/perspicacite/llm/client.py:90`) already accepts `messages: list[dict]` and passes through to litellm — content may be a list of `{type: "text"|"image_url", ...}` parts without any client change.

---

## File Structure

**New files:**
- `src/perspicacite/rag/multimodal.py` — figure-context collection + multimodal-message builder shim
- `src/perspicacite/integrations/capsule_reader.py` — load ASB-shaped capsule dirs into a KB
- `docs/capsule_schema.md` — shared on-disk schema reference (Perspicacité ↔ ASB)
- `tests/unit/test_multimodal_collect.py`
- `tests/unit/test_multimodal_message_build.py`
- `tests/unit/test_capsule_figure_endpoint.py`
- `tests/unit/test_basic_mode_multimodal_hook.py`
- `tests/unit/test_advanced_mode_multimodal_hook.py`
- `tests/unit/test_profound_mode_multimodal_hook.py`
- `tests/unit/test_agentic_mode_multimodal_hook.py`
- `tests/unit/test_literature_survey_multimodal_hook.py`
- `tests/unit/test_contradiction_mode_multimodal_hook.py`
- `tests/unit/test_strip_unknown_figure_ids.py`
- `tests/unit/test_capsule_reader_detect.py`
- `tests/unit/test_capsule_reader_ingest.py`
- `tests/unit/test_local_docs_capsule_reader_route.py`

**Modified files:**
- `src/perspicacite/config/schema.py` — add `MultimodalConfig`
- `src/perspicacite/rag/modes/basic.py` — wire hook around final `llm.complete`
- `src/perspicacite/rag/modes/advanced.py` — wire hook around final synthesis call
- `src/perspicacite/rag/modes/profound.py` — wire hook around final synthesis call
- `src/perspicacite/rag/modes/agentic.py` (or `rag/agentic/orchestrator.py`) — wire hook around final answer call
- `src/perspicacite/rag/modes/literature_survey.py` — wire hook around final synthesis call
- `src/perspicacite/rag/modes/contradiction.py` — wire hook around final synthesis call
- `src/perspicacite/web/routers/kb.py` — capsule figure-serving endpoint
- `src/perspicacite/integrations/local_docs.py` — branch to CapsuleReader when capsule dir detected
- `static/js/chat.js` — figure_id rewrite in `formatMessage`
- `static/css/chat.css` — inline thumbnail + lightbox styles
- `MANUAL_QA.md`
- `config.example.yml`

---

### Task 1: MultimodalConfig in config schema

**Files:**
- Modify: `src/perspicacite/config/schema.py:296-302` (after `CapsuleConfig`)
- Modify: `src/perspicacite/config/schema.py:392` (add to `Config`)
- Test: `tests/unit/test_multimodal_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_multimodal_config.py
from perspicacite.config.schema import Config, MultimodalConfig


def test_defaults():
    c = MultimodalConfig()
    assert c.enabled is True
    assert c.max_images == 6
    assert any(p.startswith("anthropic/claude-") for p in c.vision_allowlist)
    assert any(p.startswith("gpt-4o") for p in c.vision_allowlist)


def test_config_has_multimodal():
    cfg = Config()
    assert isinstance(cfg.multimodal, MultimodalConfig)
    assert cfg.multimodal.max_images == 6
```

- [ ] **Step 2: Run, expect ImportError**

`pytest -xvs tests/unit/test_multimodal_config.py`

- [ ] **Step 3: Add `MultimodalConfig`**

After `CapsuleConfig` in `src/perspicacite/config/schema.py`:

```python
class MultimodalConfig(BaseModel):
    """Multimodal RAG: figures-in-prompt + figures-in-answers."""

    enabled: bool = True
    max_images: int = 6
    vision_allowlist: list[str] = Field(
        default_factory=lambda: [
            "anthropic/claude-",
            "claude-",
            "openai/gpt-4o",
            "gpt-4o",
        ]
    )
```

And in `class Config`, after `capsule: CapsuleConfig = ...`:

```python
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_multimodal_config.py
git commit -m "feat(config): add MultimodalConfig (enabled, max_images, vision_allowlist)"
```

---

### Task 2: Multimodal figure-collection helper

**Files:**
- Create: `src/perspicacite/rag/multimodal.py`
- Test: `tests/unit/test_multimodal_collect.py`

The helper takes retrieved chunks + capsule root, collects `figure_refs` per chunk, resolves each ref against the chunk's capsule `figures/index.json`, loads image bytes, and returns a deduped `list[FigureContext]`.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_multimodal_collect.py
import base64
import json
from pathlib import Path

import pytest

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.multimodal import collect_figures_for_chunks


def _chunk(paper_id: str, figure_refs: list[str]) -> DocumentChunk:
    return DocumentChunk(
        id=f"c_{paper_id}_{'_'.join(figure_refs) or 'x'}",
        text="some text",
        metadata=ChunkMetadata(
            paper_id=paper_id,
            chunk_index=0,
            figure_refs=figure_refs,
        ),
    )


def _write_capsule(root: Path, paper_id: str, figures: list[dict], image_bytes: bytes) -> None:
    safe = paper_id.replace(":", "_").replace("/", "__")
    cap = root / safe
    (cap / "figures").mkdir(parents=True, exist_ok=True)
    (cap / "figures" / "index.json").write_text(json.dumps(figures))
    for f in figures:
        (cap / "figures" / f["filename"]).write_bytes(image_bytes)


def test_collect_loads_image_b64_for_matching_refs(tmp_path):
    img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p003_i02.png",
                "page": 3,
                "index": 2,
                "figure_number": "1",
                "caption": "Schematic of method.",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunks = [_chunk("doi:10.1/x", ["pdf_p3_i2"])]
    figures = collect_figures_for_chunks(chunks, capsule_root=tmp_path)
    assert len(figures) == 1
    f = figures[0]
    assert f.figure_id == "pdf_p3_i2"
    assert f.image_b64 == base64.b64encode(img).decode("ascii")
    assert "Schematic" in f.caption


def test_collect_skips_unknown_capsule(tmp_path):
    chunks = [_chunk("doi:10.1/missing", ["pdf_p1_i0"])]
    assert collect_figures_for_chunks(chunks, capsule_root=tmp_path) == []


def test_collect_dedupes_across_chunks(tmp_path):
    img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p001_i00.png",
                "page": 1,
                "index": 0,
                "figure_number": "1",
                "caption": "C1",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunks = [
        _chunk("doi:10.1/x", ["pdf_p1_i0"]),
        _chunk("doi:10.1/x", ["pdf_p1_i0"]),
    ]
    figures = collect_figures_for_chunks(chunks, capsule_root=tmp_path)
    assert len(figures) == 1


def test_collect_uses_parent_paper_id_for_external_chunks(tmp_path):
    img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    _write_capsule(
        tmp_path,
        "doi:10.1/x",
        [
            {
                "filename": "fig_p001_i00.png",
                "page": 1,
                "index": 0,
                "figure_number": "1",
                "caption": "C1",
                "subcomponent_label": "",
                "panel_files": [],
            }
        ],
        img,
    )
    chunk = DocumentChunk(
        id="cx",
        text="t",
        metadata=ChunkMetadata(
            paper_id="external:repo",
            chunk_index=0,
            figure_refs=["pdf_p1_i0"],
            parent_paper_id="doi:10.1/x",
            is_external=True,
        ),
    )
    figures = collect_figures_for_chunks([chunk], capsule_root=tmp_path)
    assert len(figures) == 1
```

- [ ] **Step 2: Run, expect import error**

`pytest -xvs tests/unit/test_multimodal_collect.py`

- [ ] **Step 3: Create `src/perspicacite/rag/multimodal.py`**

```python
"""Cycle B — multimodal RAG: pull figure_refs into LLM calls.

Collects ``figure_refs`` across retrieved chunks, resolves each id against
the originating paper's capsule ``figures/index.json``, loads image bytes,
and builds a litellm multimodal messages array via ASB's
``build_multimodal_messages``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.pipeline.capsule_builder import capsule_dir_for
from perspicacite.pipeline.parsers.figure_context import (
    FigureContext,
    build_multimodal_messages,
    format_figures_block,
    is_si_label,
    load_image_b64,
    supports_vision,
)
from perspicacite.models.papers import Paper, PaperSource

logger = get_logger("perspicacite.rag.multimodal")


def _capsule_dir_for_paper_id(paper_id: str, *, capsule_root: Path) -> Path:
    # Mirror capsule_builder.capsule_dir_for sanitization without needing a Paper.
    safe = paper_id.replace(":", "_").replace("/", "__")
    return capsule_root / safe


def _figure_id_for(rec: dict) -> str:
    page = rec.get("page", 0)
    idx = rec.get("index", 0)
    return f"pdf_p{page}_i{idx}"


def _label_for(rec: dict) -> str:
    fn = rec.get("figure_number") or ""
    sub = rec.get("subcomponent_label") or ""
    if fn:
        return f"Figure {fn}{sub}".strip()
    return f"Figure (p{rec.get('page', 0)} #{rec.get('index', 0)})"


def _paper_id_for_chunk(c: DocumentChunk) -> str | None:
    parent = getattr(c.metadata, "parent_paper_id", None)
    if parent:
        return parent
    return c.metadata.paper_id


def _load_capsule_figures(capsule_dir: Path) -> list[dict]:
    idx = capsule_dir / "figures" / "index.json"
    if not idx.is_file():
        return []
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("capsule_index_unreadable", path=str(idx), error=str(exc))
        return []


def collect_figures_for_chunks(
    chunks: Iterable[DocumentChunk], *, capsule_root: Path,
) -> list[FigureContext]:
    """Return deduped FigureContext list for figure_refs across chunks.

    Skips chunks with no figure_refs. Skips refs that don't resolve in the
    chunk's capsule. Silently drops figures whose image file is missing
    (image_b64 stays None and downstream filters them out).
    """
    seen: dict[tuple[str, str], FigureContext] = {}
    capsule_cache: dict[str, list[dict]] = {}

    for chunk in chunks:
        refs = getattr(chunk.metadata, "figure_refs", None) or []
        if not refs:
            continue
        paper_id = _paper_id_for_chunk(chunk)
        if not paper_id:
            continue
        cap_dir = _capsule_dir_for_paper_id(paper_id, capsule_root=capsule_root)
        if paper_id not in capsule_cache:
            capsule_cache[paper_id] = _load_capsule_figures(cap_dir)
        records = capsule_cache[paper_id]
        for fid in refs:
            key = (paper_id, fid)
            if key in seen:
                continue
            match = next((r for r in records if _figure_id_for(r) == fid), None)
            if match is None:
                continue
            filename = match.get("filename")
            image_b64 = (
                load_image_b64(cap_dir / "figures" / filename) if filename else None
            )
            label = _label_for(match)
            fc = FigureContext(
                figure_id=fid,
                label=label,
                caption=(match.get("caption") or "").strip(),
                source="pdf",
                panels=tuple(
                    p.get("label") for p in (match.get("panel_files") or []) if p.get("label")
                ),
                image_b64=image_b64,
                filename=filename,
                is_supplementary=is_si_label(label),
            )
            seen[key] = fc
    return list(seen.values())


def build_messages_with_figures(
    *,
    base_messages: list[dict[str, Any]],
    figures: list[FigureContext],
    model: str | None,
    config_enabled: bool,
    max_images: int,
) -> list[dict[str, Any]]:
    """Return either ``base_messages`` (text-only) or a multimodal variant.

    Falls through to ``base_messages`` unchanged when:
      - feature disabled, OR
      - model is None or doesn't pass ``supports_vision``, OR
      - no figures have ``image_b64`` loaded.

    On the multimodal path: prepends ``format_figures_block`` to the system
    prompt and rebuilds the final user turn via
    ``build_multimodal_messages`` so the image parts ride on the user role
    (litellm convention).
    """
    if not config_enabled:
        return base_messages
    if not model or not supports_vision(model):
        return base_messages
    eligible = [f for f in figures if f.image_b64]
    if not eligible:
        return base_messages

    figures_block = format_figures_block(eligible)
    rule = (
        "When a finding rests on a figure, cite it by figure_id "
        "(e.g., pdf_p3_i2). Do not invent figure IDs."
    )

    out: list[dict[str, Any]] = []
    user_idx = -1
    for i, m in enumerate(base_messages):
        if m.get("role") == "user":
            user_idx = i

    for i, m in enumerate(base_messages):
        if m.get("role") == "system":
            content = m.get("content", "")
            out.append({
                "role": "system",
                "content": (
                    f"{content}\n\n{figures_block}\n\n{rule}"
                    if isinstance(content, str) else content
                ),
            })
        elif i == user_idx:
            user_text = m.get("content", "")
            user_text = user_text if isinstance(user_text, str) else ""
            mm = build_multimodal_messages(
                prompt_text=user_text, figures=eligible, max_images=max_images,
            )
            out.extend(mm)
        else:
            out.append(m)
    return out
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/multimodal.py tests/unit/test_multimodal_collect.py
git commit -m "feat(rag): multimodal figure-collection from capsule figure_refs"
```

---

### Task 3: build_messages_with_figures tests

**Files:**
- Test: `tests/unit/test_multimodal_message_build.py`

- [ ] **Step 1: Write tests**

```python
# tests/unit/test_multimodal_message_build.py
from perspicacite.pipeline.parsers.figure_context import FigureContext
from perspicacite.rag.multimodal import build_messages_with_figures


def _fc(fid="pdf_p1_i0", b64="AAAA") -> FigureContext:
    return FigureContext(
        figure_id=fid, label="Figure 1", caption="cap", source="pdf",
        image_b64=b64, filename="fig_p001_i00.png",
    )


def test_disabled_returns_base():
    base = [{"role": "user", "content": "q"}]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc()], model="claude-3-5-sonnet",
        config_enabled=False, max_images=6,
    )
    assert out is base


def test_non_vision_model_returns_base():
    base = [{"role": "user", "content": "q"}]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc()], model="deepseek-chat",
        config_enabled=True, max_images=6,
    )
    assert out is base


def test_no_loaded_images_returns_base():
    base = [{"role": "user", "content": "q"}]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc(b64=None)], model="claude-3-5-sonnet",
        config_enabled=True, max_images=6,
    )
    assert out is base


def test_vision_path_injects_block_and_images():
    base = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Explain Figure 1."},
    ]
    out = build_messages_with_figures(
        base_messages=base, figures=[_fc()], model="claude-3-5-sonnet",
        config_enabled=True, max_images=6,
    )
    assert out[0]["role"] == "system"
    assert "Available figures" in out[0]["content"]
    assert "figure_id" in out[0]["content"]
    user_msg = out[-1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    types = [p["type"] for p in user_msg["content"]]
    assert "text" in types and "image_url" in types
```

- [ ] **Step 2: Run, expect PASS**

`pytest -xvs tests/unit/test_multimodal_message_build.py`

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_multimodal_message_build.py
git commit -m "test(rag): build_messages_with_figures gating + injection"
```

---

### Task 4: strip_unknown_figure_ids filter

**Files:**
- Modify: `src/perspicacite/rag/multimodal.py` (append)
- Test: `tests/unit/test_strip_unknown_figure_ids.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_strip_unknown_figure_ids.py
from perspicacite.rag.multimodal import strip_unknown_figure_ids


def test_keeps_known_strips_unknown():
    text = "See pdf_p1_i0 and pdf_p99_i99 in context."
    out = strip_unknown_figure_ids(text, known={"pdf_p1_i0"})
    assert "pdf_p1_i0" in out
    assert "pdf_p99_i99" not in out


def test_no_known_strips_all_pdf_ids():
    text = "ref pdf_p2_i3"
    out = strip_unknown_figure_ids(text, known=set())
    assert "pdf_p2_i3" not in out


def test_preserves_surrounding_text():
    text = "Important finding (pdf_p1_i0): the result holds."
    out = strip_unknown_figure_ids(text, known={"pdf_p1_i0"})
    assert "Important finding" in out and "result holds" in out
```

- [ ] **Step 2: Run, expect import error**

`pytest -xvs tests/unit/test_strip_unknown_figure_ids.py`

- [ ] **Step 3: Append to `src/perspicacite/rag/multimodal.py`**

```python
import re as _re

_FIG_ID_TOKEN_RE = _re.compile(r"\bpdf_p\d+_i\d+\b")


def strip_unknown_figure_ids(text: str, *, known: set[str]) -> str:
    """Remove ``pdf_p<page>_i<idx>`` tokens that aren't in ``known``.

    Mirrors ASB. Used at the answer-post stage so hallucinated figure IDs
    don't render as broken thumbnails in the UI.
    """
    def _repl(m):
        return m.group(0) if m.group(0) in known else ""
    return _FIG_ID_TOKEN_RE.sub(_repl, text or "")
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/multimodal.py tests/unit/test_strip_unknown_figure_ids.py
git commit -m "feat(rag): strip_unknown_figure_ids filter"
```

---

### Task 5: Capsule figure-serving HTTP endpoint

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py` (add new routes)
- Test: `tests/unit/test_capsule_figure_endpoint.py`

Two routes:
- `GET /api/capsule/{paper_id}/figures` → returns parsed `figures/index.json`
- `GET /api/capsule/{paper_id}/figure/{fig_id}` → returns PNG bytes for the matching figure

Paper IDs in URLs may contain `:` and `/`; clients encode them. Server uses the same sanitization (`replace(":", "_").replace("/", "__")`) when computing the capsule dir, but resolves URL-decoded values first.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_capsule_figure_endpoint.py
import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from perspicacite.config.schema import Config
    from perspicacite.web.routers import kb as kb_router

    cfg = Config()
    cfg.capsule.root = tmp_path / "capsules"
    cfg.capsule.root.mkdir(parents=True)

    class _State:
        config = cfg

    app = FastAPI()
    app.state.app_state = _State()
    app.include_router(kb_router.router)
    return TestClient(app), cfg.capsule.root


def _write_capsule(root: Path, paper_id: str) -> None:
    safe = paper_id.replace(":", "_").replace("/", "__")
    fig_dir = root / safe / "figures"
    fig_dir.mkdir(parents=True)
    (fig_dir / "index.json").write_text(json.dumps([
        {
            "filename": "fig_p001_i00.png", "page": 1, "index": 0,
            "figure_number": "1", "caption": "C", "subcomponent_label": "",
            "panel_files": [],
        }
    ]))
    (fig_dir / "fig_p001_i00.png").write_bytes(b"\x89PNGfake")


def test_list_figures(client):
    tc, root = client
    _write_capsule(root, "doi:10.1/x")
    r = tc.get(f"/api/capsule/{quote('doi:10.1/x', safe='')}/figures")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["filename"] == "fig_p001_i00.png"


def test_get_figure_bytes(client):
    tc, root = client
    _write_capsule(root, "doi:10.1/x")
    r = tc.get(
        f"/api/capsule/{quote('doi:10.1/x', safe='')}/figure/pdf_p1_i0"
    )
    assert r.status_code == 200
    assert r.content == b"\x89PNGfake"
    assert r.headers["content-type"].startswith("image/")


def test_unknown_figure_404(client):
    tc, root = client
    _write_capsule(root, "doi:10.1/x")
    r = tc.get(
        f"/api/capsule/{quote('doi:10.1/x', safe='')}/figure/pdf_p99_i99"
    )
    assert r.status_code == 404


def test_unknown_capsule_404(client):
    tc, _ = client
    r = tc.get(f"/api/capsule/{quote('doi:10.1/missing', safe='')}/figures")
    assert r.status_code == 404
```

- [ ] **Step 2: Run, expect 404 on all (routes not defined)**

`pytest -xvs tests/unit/test_capsule_figure_endpoint.py`

- [ ] **Step 3: Add routes to `src/perspicacite/web/routers/kb.py`**

Locate the router declaration (`router = APIRouter(...)`) and append at the bottom of the file:

```python
import json as _json
from fastapi import HTTPException, Request as _Request
from fastapi.responses import FileResponse, JSONResponse


def _capsule_dir_for_id(paper_id: str, *, capsule_root):
    safe = paper_id.replace(":", "_").replace("/", "__")
    return capsule_root / safe


@router.get("/api/capsule/{paper_id:path}/figures")
async def list_capsule_figures(paper_id: str, request: _Request):
    cfg = request.app.state.app_state.config
    cap = _capsule_dir_for_id(paper_id, capsule_root=cfg.capsule.root)
    idx = cap / "figures" / "index.json"
    if not idx.is_file():
        raise HTTPException(status_code=404, detail="capsule not found")
    try:
        return JSONResponse(_json.loads(idx.read_text(encoding="utf-8")))
    except (OSError, _json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/api/capsule/{paper_id:path}/figure/{fig_id}")
async def get_capsule_figure(paper_id: str, fig_id: str, request: _Request):
    cfg = request.app.state.app_state.config
    cap = _capsule_dir_for_id(paper_id, capsule_root=cfg.capsule.root)
    idx = cap / "figures" / "index.json"
    if not idx.is_file():
        raise HTTPException(status_code=404, detail="capsule not found")
    try:
        records = _json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    match = None
    for r in records:
        if f"pdf_p{r.get('page', 0)}_i{r.get('index', 0)}" == fig_id:
            match = r
            break
    if not match:
        raise HTTPException(status_code=404, detail="figure not found")
    path = cap / "figures" / match["filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="figure file missing")
    return FileResponse(path, media_type="image/png")
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_capsule_figure_endpoint.py
git commit -m "feat(web): capsule figure-serving endpoints"
```

---

### Task 6: Basic mode multimodal hook

**Files:**
- Modify: `src/perspicacite/rag/modes/basic.py:357-403` (`_generate_response`)
- Test: `tests/unit/test_basic_mode_multimodal_hook.py`

The hook collects figures from `documents` and wraps the messages before the final `llm.complete`. **Critical:** only wire the single call at line ~390 in `_generate_response`. The other `llm.complete` sites in basic.py are scope filtering / classification — text-only.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_basic_mode_multimodal_hook.py
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk


@pytest.mark.asyncio
async def test_basic_mode_routes_to_multimodal_when_figure_refs(tmp_path, monkeypatch):
    cfg = Config()
    cfg.capsule.root = tmp_path / "capsules"
    cfg.capsule.root.mkdir(parents=True)
    # Build a tiny capsule with one figure
    safe = "doi_10.1__x"
    fig_dir = cfg.capsule.root / safe / "figures"
    fig_dir.mkdir(parents=True)
    (fig_dir / "index.json").write_text(json.dumps([
        {"filename": "fig_p001_i00.png", "page": 1, "index": 0,
         "figure_number": "1", "caption": "C", "subcomponent_label": "", "panel_files": []}
    ]))
    (fig_dir / "fig_p001_i00.png").write_bytes(b"\x89PNGfake")

    from perspicacite.rag.modes import basic as basic_mode

    mode = basic_mode.BasicRAGMode(cfg)
    captured: dict = {}

    async def fake_complete(*, messages, model, provider, **kw):
        captured["messages"] = messages
        captured["model"] = model
        return "answer pdf_p1_i0"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)

    doc = DocumentChunk(
        id="c1", text="See Fig. 1.",
        metadata=ChunkMetadata(paper_id="doi:10.1/x", chunk_index=0, figure_refs=["pdf_p1_i0"]),
    )
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"

    await mode._generate_response("q", [doc], llm, request)

    user = [m for m in captured["messages"] if m["role"] == "user"][-1]
    assert isinstance(user["content"], list), "expected multimodal user content"
    assert any(p.get("type") == "image_url" for p in user["content"])
```

- [ ] **Step 2: Run, expect FAIL (hook not wired)**

`pytest -xvs tests/unit/test_basic_mode_multimodal_hook.py`

- [ ] **Step 3: Add a module-level helper used by all 6 modes**

RAG modes already receive the full `Config` in their constructor (`BasicRAGMode(config)` → `self.config`). The helper accepts it explicitly — no singleton.

In `src/perspicacite/rag/multimodal.py`, append:

```python
def wrap_messages_for_chunks(
    *,
    base_messages: list[dict],
    chunks: Iterable[DocumentChunk],
    model: str | None,
    config,
) -> list[dict]:
    """One-call entry point used by RAG mode hooks.

    ``config`` is the full Perspicacité ``Config`` (RAG modes already hold
    it as ``self.config``). Returns ``base_messages`` untouched when the
    feature is disabled, the model isn't vision-capable, or no figures
    resolve.
    """
    mm = config.multimodal
    if not mm.enabled:
        return base_messages
    figures = collect_figures_for_chunks(
        chunks, capsule_root=Path(config.capsule.root),
    )
    if not figures:
        return base_messages
    return build_messages_with_figures(
        base_messages=base_messages,
        figures=figures,
        model=model,
        config_enabled=mm.enabled,
        max_images=mm.max_images,
    )
```

- [ ] **Step 4: Wire `_generate_response` in basic.py**

Replace lines 390-400 (the `try: response = await llm.complete(...)`):

```python
        base_messages = [
            {"role": "system", "content": get_system_prompt()},
            {"role": "user", "content": template},
        ]
        try:
            from perspicacite.rag.multimodal import wrap_messages_for_chunks
            messages = wrap_messages_for_chunks(
                base_messages=base_messages,
                chunks=documents,
                model=request.model,
                config=self.config,
            )
            response = await llm.complete(
                messages=messages,
                model=request.model,
                provider=request.provider,
                max_tokens=2000,
                temperature=0.3,
            )
            return response
        except Exception as e:
            logger.error("basic_response_generation_error", error=str(e))
            return f"Error generating response: {e}"
```

- [ ] **Step 5: Run, expect PASS**

If the config singleton accessor differs from the assumed `get_config`, adapt `get_active_capsule_root_and_config` to whatever exists (look in `src/perspicacite/config/`).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/multimodal.py src/perspicacite/rag/modes/basic.py tests/unit/test_basic_mode_multimodal_hook.py
git commit -m "feat(rag): basic mode — multimodal hook around final completion"
```

---

### Task 7: Advanced mode multimodal hook

**Files:**
- Modify: `src/perspicacite/rag/modes/advanced.py` around line 771 (the **final synthesis** llm.complete call — NOT the intermediate refinement calls at 997, 1102, 1116)
- Test: `tests/unit/test_advanced_mode_multimodal_hook.py`

Identify the final-answer call site: it's the call that synthesizes the answer from the merged context with `selected_documents` available. Search for `await llm.complete` calls in `advanced.py` and pick the one immediately preceded by `format_documents_for_prompt(selected_documents)` (around line 375 builds context; line 771 emits the final answer using that context).

- [ ] **Step 1: Read advanced.py 700-790 and 940-1120** — identify the right call site.

- [ ] **Step 2: Write failing test mirroring Task 6's test** — use `AdvancedRAGMode._generate_response` or whichever method does the final synthesis. If the synthesis method is private and complex, target the **outermost** public entry point and run a single-document fixture; assert that `llm.complete` was called once with multimodal content.

- [ ] **Step 3: Wire the hook**

Before the final `await llm.complete(messages=[...], ...)`:

```python
from perspicacite.rag.multimodal import wrap_messages_for_chunks
base_messages = [
    {"role": "system", "content": <existing system content>},
    {"role": "user", "content": <existing user template>},
]
messages = wrap_messages_for_chunks(
    base_messages=base_messages, chunks=selected_documents, model=request.model,
)
response = await llm.complete(messages=messages, ...rest unchanged...)
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/modes/advanced.py tests/unit/test_advanced_mode_multimodal_hook.py
git commit -m "feat(rag): advanced mode — multimodal hook on final synthesis"
```

---

### Task 8: Profound mode multimodal hook

**Files:**
- Modify: `src/perspicacite/rag/modes/profound.py` final synthesis call around line 764
- Test: `tests/unit/test_profound_mode_multimodal_hook.py`

- [ ] **Step 1: Locate final synthesis call** (the one whose output becomes the user-facing answer; lines 206/265/818/865 are intermediate refinements — leave them alone)

- [ ] **Step 2: Write failing test** — same pattern as Task 6.

- [ ] **Step 3: Wire hook with `wrap_messages_for_chunks`**

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/modes/profound.py tests/unit/test_profound_mode_multimodal_hook.py
git commit -m "feat(rag): profound mode — multimodal hook on final synthesis"
```

---

### Task 9: Agentic mode multimodal hook

**Files:**
- Modify: `src/perspicacite/rag/agentic/orchestrator.py` (and/or `src/perspicacite/rag/modes/agentic.py`)
- Test: `tests/unit/test_agentic_mode_multimodal_hook.py`

Agentic mode uses a string `prompt` interface in the orchestrator (line 318: `await self.llm.complete(prompt=..., temperature=0.0, max_tokens=300)`). That intermediate call is a controller — leave it. Find the **final answer-emission** call (the one that takes accumulated context + retrieved chunks → user answer). It's usually in `modes/agentic.py` wrapping the orchestrator output.

- [ ] **Step 1: Read `rag/modes/agentic.py` and `rag/agentic/orchestrator.py`** to identify the final user-facing answer call. Look for the last `llm.complete` with `messages=` (not `prompt=`).

- [ ] **Step 2: If no `messages=`-style final call exists**, add one: wrap the orchestrator's final text response through a synthesis step that uses messages, gated by `multimodal.enabled`. Or: pass `figures` to the orchestrator's answer step. Pick the option with the smaller diff.

- [ ] **Step 3: Write failing test**

- [ ] **Step 4: Wire hook**

- [ ] **Step 5: Run, expect PASS**

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/agentic/orchestrator.py src/perspicacite/rag/modes/agentic.py tests/unit/test_agentic_mode_multimodal_hook.py
git commit -m "feat(rag): agentic mode — multimodal hook on final answer"
```

---

### Task 10: Literature-survey multimodal hook

**Files:**
- Modify: `src/perspicacite/rag/modes/literature_survey.py` final synthesis call around line 536 (or 614 / 663 — pick the one that emits the final user-facing answer)
- Test: `tests/unit/test_literature_survey_multimodal_hook.py`

- [ ] **Step 1: Identify final-answer call.** Look for the last `llm.complete(messages=...)` whose output is returned/yielded as the answer.

- [ ] **Step 2: Write failing test**

- [ ] **Step 3: Wire hook with `wrap_messages_for_chunks`**

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/modes/literature_survey.py tests/unit/test_literature_survey_multimodal_hook.py
git commit -m "feat(rag): literature_survey mode — multimodal hook on final synthesis"
```

---

### Task 11: Contradiction mode multimodal hook

**Files:**
- Modify: `src/perspicacite/rag/modes/contradiction.py` final synthesis call (lines 229/242/276 — pick the last user-facing one)
- Test: `tests/unit/test_contradiction_mode_multimodal_hook.py`

- [ ] **Step 1: Identify final-answer call.**

- [ ] **Step 2: Write failing test**

- [ ] **Step 3: Wire hook with `wrap_messages_for_chunks`**

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/modes/contradiction.py tests/unit/test_contradiction_mode_multimodal_hook.py
git commit -m "feat(rag): contradiction mode — multimodal hook on final synthesis"
```

---

### Task 12: Strip unknown figure_ids in chat router answer path

**Files:**
- Modify: `src/perspicacite/web/routers/chat.py` (apply `strip_unknown_figure_ids` to final assistant text where retrieved figure ids are known)
- Test: `tests/unit/test_chat_router_strip_figure_ids.py`

Pass the set of known figure ids (collected during retrieval) through to a tiny post-pass. If retrieval happens deep inside each mode and isn't exposed to the router, defer this to a per-mode pass-through and document the limitation. Minimum viable path: have each mode's stream wrap its final-emitted answer through `strip_unknown_figure_ids` using the set of `figure_refs` actually fed to the LLM.

- [ ] **Step 1: Decide injection point** — either (a) at the mode stream emit, or (b) in chat router after the mode finishes. Pick (a): smaller diff, exact known-set available.

- [ ] **Step 2: Add a stream-emit filter to each mode** — at the point each mode yields content, route through `strip_unknown_figure_ids(text, known=<set from figures>)`. The 6 modes already share `StreamEvent.content` emission patterns — locate the helper and wrap once.

- [ ] **Step 3: Write integration-style test that mocks LLM to emit a hallucinated id and asserts it's stripped**

- [ ] **Step 4: Wire**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/rag/modes/*.py src/perspicacite/web/routers/chat.py tests/unit/test_chat_router_strip_figure_ids.py
git commit -m "feat(rag): strip unknown figure_id tokens from streamed answers"
```

---

### Task 13: Chat UI thumbnail post-pass (JS)

**Files:**
- Modify: `static/js/chat.js:285` (`formatMessage`)
- Test: manual QA only (browser); add JS unit harness if one exists, otherwise skip

`formatMessage(content)` runs after markdown rendering. Add a regex pass that rewrites `pdf_p<page>_i<idx>` tokens into `<img>` elements pointing at `/api/capsule/<paper_id>/figure/<fig_id>`. The paper id isn't directly available in the token — pass it via a side channel: have the streamed event include a `figure_paper_map: {fig_id: paper_id}` payload, OR have the mode rewrite tokens server-side into `<<fig:paper_id:fig_id>>` and `formatMessage` rewrites those. Choose the side-channel approach: extend `StreamEvent.done` (or a new `StreamEvent.figures`) to ship the map, store on the window-level last assistant turn, then `formatMessage` looks up.

- [ ] **Step 1: Read `static/js/chat.js` 250-380** to understand the streaming + formatMessage interaction.

- [ ] **Step 2: Decide rewrite strategy:**
  - **Server-side prefix-rewrite** is simpler: change each mode's emit pipeline to replace bare `pdf_pN_iM` with `[fig:<paper_id>:pdf_pN_iM]` for tokens that map to a known capsule. Then `formatMessage` rewrites that exact form to `<img class="inline-figure" data-paper="..." data-fig="..." src="/api/capsule/.../figure/...">`.
  - This avoids needing a side-channel and keeps the answer text self-describing for copy-paste.

- [ ] **Step 3: Implement server-side rewrite in `multimodal.py`** — `annotate_figure_ids_for_ui(text, *, fig_to_paper: dict[str,str]) -> str`. Then thread it into each mode's emit.

- [ ] **Step 4: Implement JS rewrite in `formatMessage`** — replace `[fig:PAPER:FIG]` tokens with `<img>` tags. Sanitize PAPER/FIG (`/^[A-Za-z0-9_\-:.]+$/`).

- [ ] **Step 5: Add `static/css/chat.css` rule** for `.inline-figure` (max-width, click-to-expand handler).

- [ ] **Step 6: Test in browser** — start dev server, ingest a sample PDF with capsule, ask a query that should reference Fig. 1, confirm thumbnail renders.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/rag/multimodal.py src/perspicacite/rag/modes/*.py static/js/chat.js static/css/chat.css
git commit -m "feat(ui): inline capsule figure thumbnails in chat answers"
```

---

### Task 14: CapsuleReader — detect capsule directory

**Files:**
- Create: `src/perspicacite/integrations/capsule_reader.py`
- Test: `tests/unit/test_capsule_reader_detect.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_capsule_reader_detect.py
import json
from pathlib import Path

from perspicacite.integrations.capsule_reader import is_capsule_dir


def test_capsule_with_metadata_version_is_detected(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"capsule_version": "0.1", "paper_id": "p"}))
    assert is_capsule_dir(tmp_path) is True


def test_no_metadata_is_not_capsule(tmp_path):
    assert is_capsule_dir(tmp_path) is False


def test_metadata_without_version_not_capsule(tmp_path):
    (tmp_path / "metadata.json").write_text(json.dumps({"paper_id": "p"}))
    assert is_capsule_dir(tmp_path) is False
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Create `src/perspicacite/integrations/capsule_reader.py`**

```python
"""Read an ASB-shaped capsule directory into a Perspicacité KB."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.documents import ChunkMetadata
from perspicacite.models.papers import Author, Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import chunk_document

logger = get_logger("perspicacite.capsule_reader")


def is_capsule_dir(path: Path) -> bool:
    """Return True if ``path`` looks like a capsule (has metadata.json with capsule_version)."""
    meta = Path(path) / "metadata.json"
    if not meta.is_file():
        return False
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("capsule_version"))
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/capsule_reader.py tests/unit/test_capsule_reader_detect.py
git commit -m "feat(integrations): CapsuleReader — detect capsule dirs by metadata"
```

---

### Task 15: CapsuleReader — ingest capsule into KB

**Files:**
- Modify: `src/perspicacite/integrations/capsule_reader.py` (append `ingest_capsule`)
- Test: `tests/unit/test_capsule_reader_ingest.py`

Behavior:
- Read `metadata.json` → paper_id, title, authors, year, doi, source
- Build a `Paper` (use `PaperSource.LOCAL` as fallback)
- Text source priority: `text/blocks.jsonl` → `evidence/source_snippets.md` → fail with logger.warning + empty result
- For `blocks.jsonl`: group by `section`, concatenate paragraph content, chunk per section, tag chunks with `source_section`, `page`, `figure_refs`, `resource_refs` (from each block), and `parent_paper_id` only if metadata says producer != "perspicacite" AND there's a separate parent (not the common case; default unset).
- Load `figures/index.json` if present — these belong to the *paper itself* and are addressable via the capsule figure endpoint; nothing else to do at ingest.
- Load `resources.json` if present and union resource ids into chunk `resource_refs`.
- Embed + write to vector store using the same path as `_ingest_files`.

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_capsule_reader_ingest.py
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.integrations.capsule_reader import ingest_capsule


def _write_capsule(root, *, blocks):
    (root / "figures").mkdir(parents=True)
    (root / "figures" / "index.json").write_text("[]")
    (root / "metadata.json").write_text(json.dumps({
        "capsule_version": "0.1", "producer": "asb", "paper_id": "doi:10.1/x",
        "title": "Test", "authors": [], "year": 2025, "doi": "10.1/x", "source": "local",
    }))
    (root / "text").mkdir()
    (root / "text" / "blocks.jsonl").write_text("\n".join(json.dumps(b) for b in blocks))


@pytest.mark.asyncio
async def test_ingest_chunks_capsule_blocks(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    _write_capsule(cap, blocks=[
        {"block_id": "p001_b0", "section": "abstract", "page": 1, "content": "intro text " * 50,
         "figure_refs": [], "table_refs": []},
        {"block_id": "p002_b0", "section": "results", "page": 2, "content": "results text " * 50,
         "figure_refs": ["pdf_p2_i0"], "table_refs": []},
    ])

    app_state = MagicMock()
    app_state.session_store.get_kb_metadata = AsyncMock(return_value=MagicMock(
        collection_name="col1", chunk_count=0, name="kb1",
    ))
    app_state.session_store.save_kb_metadata = AsyncMock()
    app_state.embedding_provider.embed = AsyncMock(return_value=[[0.0] * 3 for _ in range(20)])
    app_state.vector_store.add_chunks = AsyncMock()
    app_state.config.knowledge_base.chunk_size = 500
    app_state.config.knowledge_base.chunk_overlap = 50

    registry = MagicMock()
    registry.publish = AsyncMock()
    registry.finish = AsyncMock()

    result = await ingest_capsule(
        capsule_dir=cap, kb_name="kb1",
        app_state=app_state, registry=registry, job_id="j1",
    )
    assert result["files"] == 1
    assert result["added_chunks"] > 0
    add_calls = app_state.vector_store.add_chunks.call_args_list
    assert add_calls
    # ensure figure_refs from blocks propagated to chunks
    all_chunks = [c for call in add_calls for c in call.args[1]]
    assert any("pdf_p2_i0" in (c.metadata.figure_refs or []) for c in all_chunks)
```

- [ ] **Step 2: Run, expect ImportError**

- [ ] **Step 3: Implement `ingest_capsule`**

Append to `capsule_reader.py`:

```python
async def ingest_capsule(
    *, capsule_dir: Path, kb_name: str, app_state, registry, job_id: str,
) -> dict[str, Any]:
    capsule_dir = Path(capsule_dir)
    meta_path = capsule_dir / "metadata.json"
    if not meta_path.is_file():
        await registry.fail(job_id, f"not a capsule directory: {capsule_dir}")
        return {}
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    paper_id = meta.get("paper_id") or f"local:{capsule_dir.name}"

    authors = [
        Author(family=a.get("family", ""), given=a.get("given", ""))
        for a in (meta.get("authors") or [])
    ]
    paper = Paper(
        id=paper_id,
        title=meta.get("title") or capsule_dir.name,
        authors=authors,
        year=meta.get("year"),
        doi=meta.get("doi"),
        source=PaperSource.LOCAL,
    )

    blocks_path = capsule_dir / "text" / "blocks.jsonl"
    snippets_path = capsule_dir / "evidence" / "source_snippets.md"

    section_to_text: dict[str, str] = {}
    block_meta: list[dict] = []
    if blocks_path.is_file():
        for line in blocks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                b = json.loads(line)
            except json.JSONDecodeError:
                continue
            sec = b.get("section") or "other"
            section_to_text[sec] = (section_to_text.get(sec, "") + "\n\n" + (b.get("content") or "")).strip()
            block_meta.append(b)
    elif snippets_path.is_file():
        section_to_text["other"] = snippets_path.read_text(encoding="utf-8")
    else:
        logger.warning("capsule_no_text_source", capsule=str(capsule_dir))
        await registry.finish(job_id, {"added_chunks": 0, "files": 1})
        return {"added_chunks": 0, "files": 1}

    resources = []
    res_path = capsule_dir / "resources.json"
    if res_path.is_file():
        try:
            resources = json.loads(res_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            resources = []
    all_resource_ids = [r.get("resource_id") for r in resources if r.get("resource_id")]

    kb = await app_state.session_store.get_kb_metadata(kb_name)
    if kb is None:
        await registry.fail(job_id, f"KB '{kb_name}' not found")
        return {}

    kb_cfg = app_state.config.knowledge_base
    total = 0
    all_chunks = []
    for section, text in section_to_text.items():
        if not text.strip():
            continue
        chunks = await chunk_document(text, paper, content_type="text", language=None, config=kb_cfg)
        # union figure_refs from blocks of this section
        sec_fig_refs: list[str] = []
        for b in block_meta:
            if b.get("section") == section:
                sec_fig_refs.extend(b.get("figure_refs") or [])
        sec_fig_refs = list(dict.fromkeys(sec_fig_refs))
        for c in chunks:
            c.metadata = ChunkMetadata(**{
                **c.metadata.model_dump(),
                "source_section": section,
                "figure_refs": sec_fig_refs,
                "resource_refs": all_resource_ids,
            })
        all_chunks.extend(chunks)

    if all_chunks:
        texts = [c.text for c in all_chunks]
        embeds = await app_state.embedding_provider.embed(texts)
        for c, e in zip(all_chunks, embeds, strict=True):
            c.embedding = e
        await app_state.vector_store.add_chunks(kb.collection_name, all_chunks)
        total += len(all_chunks)

    kb.chunk_count += total
    await app_state.session_store.save_kb_metadata(kb)
    result = {"added_chunks": total, "files": 1}
    await registry.publish(job_id, {"type": "progress", "done": 1, "file": str(capsule_dir),
                                    "status": "ingested", "chunks": total})
    await registry.finish(job_id, result)
    return result
```

- [ ] **Step 4: Run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/integrations/capsule_reader.py tests/unit/test_capsule_reader_ingest.py
git commit -m "feat(integrations): CapsuleReader — ingest capsule into KB"
```

---

### Task 16: Route capsule dirs through CapsuleReader in local_docs

**Files:**
- Modify: `src/perspicacite/integrations/local_docs.py:151-165` (`ingest_local_documents`)
- Test: `tests/unit/test_local_docs_capsule_reader_route.py`

Detection: if any input path is a directory and `is_capsule_dir(path)` → ingest via CapsuleReader (one capsule per such directory). Non-capsule paths fall through to the existing `_ingest_files`.

- [ ] **Step 1: Write failing test that passes a capsule dir + a regular PDF, asserts CapsuleReader was called once for the capsule and `_ingest_files` was called with only the PDF.**

- [ ] **Step 2: Modify `ingest_local_documents`**

```python
from perspicacite.integrations.capsule_reader import is_capsule_dir, ingest_capsule

async def ingest_local_documents(
    *, kb_name, paths, app_state, registry, job_id, recursive=True,
) -> dict[str, Any]:
    capsule_dirs = [p for p in paths if p.is_dir() and is_capsule_dir(p)]
    non_capsule_paths = [p for p in paths if p not in capsule_dirs]

    total = {"added_chunks": 0, "files": 0}
    for cap in capsule_dirs:
        r = await ingest_capsule(
            capsule_dir=cap, kb_name=kb_name, app_state=app_state,
            registry=registry, job_id=job_id,
        )
        total["added_chunks"] += r.get("added_chunks", 0)
        total["files"] += r.get("files", 0)

    if non_capsule_paths:
        expanded = expand_paths(non_capsule_paths, recursive=recursive)
        r2 = await _ingest_files(
            kb_name=kb_name, files=expanded, app_state=app_state,
            registry=registry, job_id=job_id,
        )
        total["added_chunks"] += r2.get("added_chunks", 0)
        total["files"] += r2.get("files", 0)

    return total
```

Note: both code paths call `registry.finish(job_id, ...)` today. Keep the existing `_ingest_files` finish call when no capsules were processed, and have the multi-capsule path finish once at the end. Adjust `ingest_capsule` to **not** call `registry.finish` when invoked from this multi-path branch (add a `finalize: bool = True` flag, pass `False` from `ingest_local_documents`).

- [ ] **Step 3: Run, expect PASS**

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/integrations/local_docs.py src/perspicacite/integrations/capsule_reader.py tests/unit/test_local_docs_capsule_reader_route.py
git commit -m "feat(local_docs): route capsule dirs through CapsuleReader"
```

---

### Task 17: docs/capsule_schema.md

**Files:**
- Create: `docs/capsule_schema.md`

Single-page reference: capsule version, on-disk layout, file-by-file schema, paper_id convention, figure_id convention, the contract table from the design spec. Cross-link to `2026-05-13-capsule-multimodal-rag-design.md`.

- [ ] **Step 1: Write the doc** (extract layout + schema sections verbatim from the design spec lines 99-237; add a "Cross-producer compatibility" section explaining Perspicacité reads ASB capsules via `CapsuleReader` and ASB reads Perspicacité capsules natively).

- [ ] **Step 2: Commit**

```bash
git add docs/capsule_schema.md
git commit -m "docs: shared capsule on-disk schema (Perspicacité ↔ ASB)"
```

---

### Task 18: MANUAL_QA additions for Cycle B

**Files:**
- Modify: `MANUAL_QA.md`

Add a "Capsule Cycle B" section. Use the same format as Cycle A's section:
1. Ingest a paper with capsule auto-build; confirm `figures/index.json` exists.
2. Ask a chat query in `basic` mode that requires Fig. 1 — confirm thumbnail renders inline in the answer.
3. Repeat for `advanced`, `profound`, `agentic`, `literature_survey`, `contradiction`.
4. Confirm hallucinated `pdf_p99_i99` tokens are stripped from the answer.
5. Drop an ASB-shaped capsule directory into a configured `local_docs.allowed_roots`; confirm `ingest_local_documents` ingests via CapsuleReader (chunks gain `source_section` and `figure_refs`).
6. Verify `/api/capsule/<doi>/figures` returns the index and `/api/capsule/<doi>/figure/<id>` serves PNGs.

- [ ] **Step 1: Append section**

- [ ] **Step 2: Commit**

```bash
git add MANUAL_QA.md
git commit -m "docs(qa): MANUAL_QA — Cycle B checklist"
```

---

### Task 19: config.example.yml update

**Files:**
- Modify: `config.example.yml`

Add a `multimodal:` block with the same defaults exposed in `MultimodalConfig`, plus inline comments explaining each field. Place between `capsule:` and any subsequent section.

- [ ] **Step 1: Read current `config.example.yml`**

- [ ] **Step 2: Insert block**

```yaml
multimodal:
  enabled: true              # Enable figures-in-prompt + inline thumbnails in answers
  max_images: 6              # Cap on images sent per LLM call
  vision_allowlist:          # Prefix-match against the model string (provider/model)
    - "anthropic/claude-"
    - "claude-"
    - "openai/gpt-4o"
    - "gpt-4o"
```

- [ ] **Step 3: Commit**

```bash
git add config.example.yml
git commit -m "docs(config): add multimodal block to config.example.yml"
```

---

### Task 20: Final integration walk-through

**Files:** (none — manual verification)

- [ ] **Step 1:** With a vision-capable model configured (`anthropic/claude-3-5-sonnet-20241022`), ingest a paper with figures via the `local-pdf` worker. Confirm capsule is built and chunks carry `figure_refs`.

- [ ] **Step 2:** Switch chat to `basic` mode. Ask a question that retrieves at least one chunk with `figure_refs`. Confirm via `/api/jobs/<id>` SSE that the streamed answer contains `[fig:<paper>:<id>]` tokens and the UI rewrites them to thumbnails.

- [ ] **Step 3:** Switch the LLM to a non-vision model (e.g., `deepseek/deepseek-chat`). Repeat the query. Confirm answer renders text-only with no broken thumbnails and the figures_block does not appear in the system prompt (no leakage).

- [ ] **Step 4:** Copy an ASB-produced capsule directory into a `local_docs.allowed_roots` location. Trigger `ingest_local_documents` with that directory. Confirm CapsuleReader path: chunks are written with `source_section`, `figure_refs`.

- [ ] **Step 5:** If anything in steps 1-4 fails, fix in a follow-up commit and re-verify.

- [ ] **Step 6:** No commit unless fixes were needed.

---

### Task 21: Final code review

- [ ] **Step 1:** Run a final code-review subagent over all commits since Cycle A merge.

- [ ] **Step 2:** Address any issues raised.

- [ ] **Step 3:** Use `superpowers:finishing-a-development-branch` workflow to merge to main (fast-forward, same as Cycle A).

---

## Risks & mitigations

- **Per-mode wiring is repetitive** — six near-identical 2-line diffs. Mitigation: shared `wrap_messages_for_chunks` helper means each mode's change is truly minimal.
- **Final-completion vs intermediate-completion misidentification** in `advanced` / `profound` / `agentic` — these modes have refinement loops. Mitigation: per-task spec says "final synthesis only"; reviewer must confirm by checking that the wrapped call's output becomes the user-facing answer (not a tool/scope/classification step).
- **JS post-pass with paper_id side-channel** — picked the server-side rewrite (`[fig:PAPER:FIG]`) to avoid streaming-state coupling. Mitigation: token is regex-sanitized in JS before `<img>` insertion to prevent injection.
- **CapsuleReader text source priority** — if neither `blocks.jsonl` nor `evidence/source_snippets.md` exists, ingest yields zero chunks. Mitigation: explicit `logger.warning` + empty result; not an error since some capsules may be figures-only.
- **Pytest memory** — same as Cycle A. Run only the new test file per task.
- **No config singleton needed** — RAG modes already hold the full `Config` as `self.config` (`BasicRAGMode.__init__(config)`). The helper accepts it explicitly. No global state.
