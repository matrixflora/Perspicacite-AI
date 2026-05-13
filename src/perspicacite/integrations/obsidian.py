"""Obsidian-compatible Markdown vault export for a knowledge base."""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any


def _slug(doi: str | None) -> str:
    if not doi:
        return "untitled"
    return re.sub(r"[^a-zA-Z0-9]+", "-", doi).strip("-").lower() or "untitled"


def _slug_title(t: str | None) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", t or "untitled").strip("-").lower() or "untitled"


def _yaml(d: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for x in v:
                lines.append(f"  - {x}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _paper_note(paper: dict[str, Any]) -> str:
    front = _yaml({
        "doi": paper.get("doi") or "",
        "year": paper.get("year") or "",
        "journal": paper.get("journal") or "",
        "authors": paper.get("authors") or [],
        "source": paper.get("content_source") or "",
        "content_type": paper.get("content_type") or "",
        "tags": ["paper"],
    })
    body = f"\n\n# {paper.get('title') or paper.get('doi') or 'Untitled'}\n\n"
    if paper.get("abstract"):
        body += f"## Abstract\n\n{paper['abstract']}\n"
    return front + body


def _rewrite_wikilinks(text: str, doi_to_slug: dict[str, str]) -> str:
    out = text or ""
    # Replace longer DOIs first so prefixes don't shadow longer ones
    for doi in sorted(doi_to_slug, key=len, reverse=True):
        if doi:
            out = out.replace(doi, f"[[{doi_to_slug[doi]}]]")
    return out


def _conversation_note(conv: dict[str, Any], doi_to_slug: dict[str, str]) -> tuple[str, str]:
    title = conv.get("title") or "Untitled"
    filename = _slug_title(title) + ".md"
    parts = [f"# {title}\n"]
    for m in conv.get("messages") or []:
        role = m.get("role", "?")
        content = _rewrite_wikilinks(m.get("content", ""), doi_to_slug)
        parts.append(f"## {str(role).capitalize()}\n\n{content}\n")
    return filename, "\n".join(parts)


def build_obsidian_vault(
    *,
    kb: dict[str, Any],
    papers: list[dict[str, Any]],
    conversations: list[dict[str, Any]],
) -> bytes:
    """Build a zip archive with an Obsidian-compatible vault layout.

    Layout::

        <kb_name>/
          Index.md               — paper and conversation index
          Papers/<doi-slug>.md   — one note per paper (YAML frontmatter + abstract)
          Conversations/<title-slug>.md — one note per conversation (wikilinks for DOIs)

    Args:
        kb: KB metadata dict (must have ``name`` key; optional ``paper_count``,
            ``chunk_count``, ``embedding_model``).
        papers: List of paper dicts (``doi``, ``title``, ``year``, ``journal``,
            ``authors``, ``content_type``, ``content_source``, ``abstract``).
        conversations: List of conversation dicts (``id``, ``title``, ``messages``
            list of ``{role, content, sources}``).

    Returns:
        Raw bytes of a ZIP archive.
    """
    kb_name = kb.get("name") or "default"
    # Build DOI → slug map (only valid DOIs)
    doi_to_slug: dict[str, str] = {}
    for p in papers:
        doi = p.get("doi")
        if doi:
            doi_to_slug[doi] = _slug(doi)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in papers:
            doi = p.get("doi")
            slug = _slug(doi) if doi else _slug_title(p.get("title"))
            z.writestr(f"{kb_name}/Papers/{slug}.md", _paper_note(p))
        for c in conversations:
            fn, body = _conversation_note(c, doi_to_slug)
            z.writestr(f"{kb_name}/Conversations/{fn}", body)
        idx_lines = [f"# {kb_name}\n"]
        if kb.get("paper_count") is not None:
            idx_lines.append(f"Papers: {kb.get('paper_count')} · Chunks: {kb.get('chunk_count')}")
        if kb.get("embedding_model"):
            idx_lines.append(f"Embedding model: {kb['embedding_model']}")
        idx_lines.append("\n## Papers\n")
        for p in papers:
            slug = _slug(p.get("doi")) if p.get("doi") else _slug_title(p.get("title"))
            idx_lines.append(f"- [[{slug}]] {p.get('title') or ''}")
        idx_lines.append("\n## Conversations\n")
        for c in conversations:
            idx_lines.append(f"- [[{_slug_title(c.get('title'))}]]")
        z.writestr(f"{kb_name}/Index.md", "\n".join(idx_lines))
    return buf.getvalue()
