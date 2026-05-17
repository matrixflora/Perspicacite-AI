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
from collections.abc import Iterable

from perspicacite.models.documents import DocumentChunk
from perspicacite.models.rag import CodeExcerpt

_GITHUB_PAPER_ID_RE = re.compile(
    r"^github:(?P<owner>[^/\s]+)/(?P<repo>[^@\s]+)@(?P<sha>[^:\s]+):(?P<path>.+)$"
)


def build_github_source_url(
    *, paper_id: str, start_line: int, end_line: int
) -> str | None:
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
