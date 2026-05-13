"""ASB-aligned figure context fusion for multimodal LLM calls.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Fuses PDF-extracted ``figures.FigureRecord`` and JATS-parsed figures into a
single ``list[FigureContext]`` with canonical, stable figure ids. Provides
the multimodal-message builder (``build_multimodal_messages``) used by the
Cycle B chat path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


FIGURE_CAPTION_CHAR_CAP = 400


@dataclass(frozen=True)
class FigureContext:
    """Unified figure descriptor for prompt injection."""
    figure_id: str
    label: str
    caption: str
    source: str
    panels: tuple[str, ...] = ()
    image_b64: str | None = None
    filename: str | None = None
    is_supplementary: bool = False


import re as _re_sf8

_SI_LABEL_RE = _re_sf8.compile(
    r"^\s*(?:S\d|"
    r"SI(?:\s|\.|\d)|"
    r"Supp(?:\.|l\.|lementary))",
    _re_sf8.IGNORECASE,
)


def is_si_label(label: str) -> bool:
    """Return True for labels indicating supplementary status.

    Recognized: 'S<digit>' prefix ('S1', 'S12', 'S3a'); 'SI ' / 'SI.' /
    'SI<digit>'; 'Supp.' / 'Suppl.' / 'Supplementary'. Case-insensitive.
    """
    if not label:
        return False
    return bool(_SI_LABEL_RE.match(label))


def partition_main_vs_supplementary(
    figures: list["FigureContext"],
) -> tuple[list["FigureContext"], list["FigureContext"]]:
    """Split a flat figure list into (main, supplementary) preserving order."""
    main = [f for f in figures if not f.is_supplementary]
    si = [f for f in figures if f.is_supplementary]
    return main, si


def load_image_b64(path) -> str | None:
    """SF-7 — base64-encode the image bytes at ``path`` for litellm
    multimodal calls. Returns None when the path doesn't exist or can't
    be read; caller treats None as 'skip this figure'."""
    import base64
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.is_file():
        return None
    try:
        return base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError:
        return None


def _truncate_caption(text: str) -> str:
    s = (text or "").strip()
    if len(s) <= FIGURE_CAPTION_CHAR_CAP:
        return s
    return s[: FIGURE_CAPTION_CHAR_CAP - 2].rstrip() + " …"


def _pdf_label(fr) -> str:
    fn = getattr(fr, "figure_number", None)
    if fn:
        sub = getattr(fr, "subcomponent_label", None) or ""
        return f"Figure {fn}{sub}".strip()
    page = getattr(fr, "page", 0)
    idx = getattr(fr, "index", 0)
    return f"Figure (p{page} #{idx})"


def _pdf_id(fr) -> str:
    page = getattr(fr, "page", 0)
    idx = getattr(fr, "index", 0)
    return f"pdf_p{page}_i{idx}"


def _pdf_panels(fr) -> tuple[str, ...]:
    panels = getattr(fr, "panel_files", None) or []
    out = []
    for p in panels:
        if isinstance(p, dict):
            lab = p.get("label")
            if lab:
                out.append(str(lab))
    return tuple(out)


def build_figure_context(
    *, pdf_figures: Iterable, jats_figures: Iterable,
) -> list[FigureContext]:
    """Fuse PDF + JATS figures into a unified list, JATS first."""
    out: list[FigureContext] = []
    seen_numbers: set[str] = set()
    for jf in jats_figures or []:
        fid = getattr(jf, "figure_id", None) or ""
        if not fid:
            continue
        label = getattr(jf, "label", None) or fid
        caption = _truncate_caption(getattr(jf, "caption", "") or "")
        out.append(FigureContext(
            figure_id=str(fid),
            label=str(label),
            caption=caption,
            source="jats",
            panels=(),
            is_supplementary=is_si_label(str(label)),
        ))
        norm = str(label).lower().replace("figure", "").replace("fig.", "")
        norm = norm.replace("fig", "").strip()
        if norm:
            seen_numbers.add(norm)
    for fr in pdf_figures or []:
        fn = (getattr(fr, "figure_number", None) or "").strip().lower()
        if fn and fn in seen_numbers:
            continue
        out.append(FigureContext(
            figure_id=_pdf_id(fr),
            label=_pdf_label(fr),
            caption=_truncate_caption(getattr(fr, "caption", "") or ""),
            source="pdf",
            panels=_pdf_panels(fr),
            is_supplementary=is_si_label(_pdf_label(fr)) or is_si_label(fn or ""),
        ))
        if fn:
            seen_numbers.add(fn)
    return out


def format_figures_block(figures: list[FigureContext]) -> str:
    """Render the prompt-side text block. Returns '' when empty."""
    if not figures:
        return ""
    lines: list[str] = [
        "Available figures (cite by figure_id when a finding rests on a figure):"
    ]
    for f in figures:
        lines.append(f"- {f.figure_id} ({f.label}) [{f.source}]: {f.caption}")
        if f.panels:
            lines.append(f"  Panels: {', '.join(f.panels)}")
    return "\n".join(lines)


VISION_CAPABLE_PREFIXES = (
    "anthropic/claude-",
    "claude-",
    "openai/gpt-4o",
    "gpt-4o",
)


def supports_vision(model: str) -> bool:
    """SF-7 — return True when the model string starts with a prefix in
    the vision-capable allowlist (Anthropic Claude, OpenAI GPT-4o)."""
    if not model:
        return False
    return any(model.startswith(p) for p in VISION_CAPABLE_PREFIXES)


def build_multimodal_messages(
    *, prompt_text: str, figures: list[FigureContext], max_images: int,
) -> list[dict]:
    """SF-7 — build a litellm messages array with one text + N image parts.

    Cap policy: prefer non-supplementary figures (label not starting with
    'S') first; within a tier, lower figure numbers first. Figures
    without ``image_b64`` are silently skipped.
    """
    def _sort_key(f: FigureContext) -> tuple[int, str]:
        label = (f.label or "").replace("Figure ", "").replace(
            "Fig. ", "").strip()
        is_supp = 1 if label.upper().startswith("S") else 0
        return (is_supp, label)
    eligible = [f for f in figures if f.image_b64]
    eligible.sort(key=_sort_key)
    selected = eligible[:max_images]
    content: list[dict] = [{"type": "text", "text": prompt_text}]
    for f in selected:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{f.image_b64}",
            },
        })
    return [{"role": "user", "content": content}]
