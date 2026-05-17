"""Cycle B — multimodal RAG: pull figure_refs into LLM calls.

Collects ``figure_refs`` across retrieved chunks, resolves each id against
the originating paper's capsule ``figures/index.json``, loads image bytes,
and exposes helpers used by per-mode RAG hooks.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from perspicacite.logging import get_logger
from perspicacite.models.documents import DocumentChunk
from perspicacite.pipeline.parsers.figure_context import (
    FigureContext,
    build_multimodal_messages,
    format_figures_block,
    is_si_label,
    load_image_b64,
    supports_vision,
)

logger = get_logger("perspicacite.rag.multimodal")


def _capsule_dir_for_paper_id(paper_id: str, *, capsule_root: Path) -> Path:
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


def _paper_id_for_chunk(c) -> str | None:
    meta = _chunk_metadata(c)
    if meta is None:
        return None
    parent = _meta_attr(meta, "parent_paper_id")
    if parent:
        return parent
    return _meta_attr(meta, "paper_id")


def _chunk_metadata(c):
    """Return the metadata object/dict for a chunk (dict or DocumentChunk-like)."""
    if isinstance(c, dict):
        return c.get("metadata")
    return getattr(c, "metadata", None)


def _meta_attr(meta, name):
    """Read an attribute from a metadata object that may be a dict or a model."""
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get(name)
    return getattr(meta, name, None)


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

    Skips chunks without figure_refs. Skips refs that don't resolve in the
    chunk's capsule. Figures whose image file is missing get ``image_b64=None``
    and are filtered downstream in ``build_messages_with_figures``.
    """
    seen: dict[tuple[str, str], FigureContext] = {}
    capsule_cache: dict[str, list[dict]] = {}

    for chunk in chunks:
        meta = _chunk_metadata(chunk)
        refs = _meta_attr(meta, "figure_refs") or []
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
                    p.get("label")
                    for p in (match.get("panel_files") or [])
                    if p.get("label")
                ),
                image_b64=image_b64,
                filename=filename,
                is_supplementary=is_si_label(label),
            )
            seen[key] = fc
    return list(seen.values())


def build_messages_with_figures(
    *,
    base_messages: list[dict],
    figures: list[FigureContext],
    model: str | None,
    config_enabled: bool,
    max_images: int,
) -> list[dict]:
    """Return either ``base_messages`` (text-only) or a multimodal variant.

    Falls through to ``base_messages`` unchanged when:
      - feature disabled, OR
      - model is None or doesn't pass ``supports_vision``, OR
      - no figures have ``image_b64`` loaded.

    On the multimodal path: prepends ``format_figures_block`` to the system
    prompt and rebuilds the last user turn via ``build_multimodal_messages``
    so the image parts ride on the user role (litellm convention).
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

    last_user_idx = -1
    for i, m in enumerate(base_messages):
        if m.get("role") == "user":
            last_user_idx = i

    out: list[dict] = []
    for i, m in enumerate(base_messages):
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, str):
                out.append({
                    "role": "system",
                    "content": f"{content}\n\n{figures_block}\n\n{rule}",
                })
            else:
                out.append(m)
        elif i == last_user_idx:
            user_text = m.get("content", "")
            user_text = user_text if isinstance(user_text, str) else ""
            mm = build_multimodal_messages(
                prompt_text=user_text, figures=eligible, max_images=max_images,
            )
            out.extend(mm)
        else:
            out.append(m)
    return out


def wrap_messages_for_chunks(
    *,
    base_messages: list[dict],
    chunks: Iterable[DocumentChunk],
    model: str | None,
    config,
) -> list[dict]:
    """One-call entry point used by RAG mode hooks.

    ``config`` is the full Perspicacité ``Config`` (RAG modes already hold
    it as ``self.config``). Returns ``base_messages`` unchanged when the
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


def annotate_figure_ids_for_ui(
    text: str, *, fig_to_paper: dict[str, str],
) -> str:
    """Rewrite each ``pdf_p<page>_i<idx>`` token to ``[[fig:<paper_id>:<token>]]``
    when ``token`` is in ``fig_to_paper``; leave unknown tokens unchanged.

    Future per-mode wiring can use this to emit a UI-renderable form. Until
    then, the UI does naive lookup against retrieved sources.
    """
    def _repl(m):
        token = m.group(0)
        paper = fig_to_paper.get(token)
        return f"[[fig:{paper}:{token}]]" if paper else token
    return _FIG_ID_TOKEN_RE.sub(_repl, text or "")
