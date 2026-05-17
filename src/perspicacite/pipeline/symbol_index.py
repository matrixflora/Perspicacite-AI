"""Per-KB symbol index sidecar (one JSONL line per symbol).

Append-only; one record per top-level function / class / notebook cell
extracted by ``pipeline.chunking_code``. Read with ``iter_symbols`` for
agentic symbol lookup without going through dense retrieval.
"""
from __future__ import annotations

import fnmatch
import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from perspicacite.models.documents import DocumentChunk


@dataclass(frozen=True)
class SymbolRecord:
    paper_id: str
    symbol_name: str
    symbol_kind: str          # "function" | "class" | "method" | "cell" | "module"
    file_path: str
    start_line: int
    end_line: int
    signature: str            # e.g. "def fit(self, X, y=None)"
    docstring: str | None  # ≤500 chars, truncated
    imports: list[str]


_SIDECAR_NAME = "symbols.jsonl"


def symbols_from_chunks(chunks: Sequence[DocumentChunk]) -> list[SymbolRecord]:
    """Project code chunks (content_type=="code") into SymbolRecords.

    Chunks whose ``symbol_name`` is None or whose ``content_type`` is not
    "code" are skipped silently.
    """
    out: list[SymbolRecord] = []
    for c in chunks:
        md = c.metadata
        if md.content_type != "code" or not md.symbol_name:
            continue
        signature = _signature_of(c)
        out.append(
            SymbolRecord(
                paper_id=md.paper_id,
                symbol_name=md.symbol_name,
                symbol_kind=md.symbol_kind or "module",
                file_path=md.source_file_path or "",
                start_line=int(md.start_line or 0),
                end_line=int(md.end_line or 0),
                signature=signature,
                docstring=md.docstring,
                imports=list(md.imports or []),
            )
        )
    return out


def _signature_of(chunk: DocumentChunk) -> str:
    """First non-empty line of the chunk, truncated to 200 chars."""
    for ln in chunk.text.splitlines():
        s = ln.strip()
        if s:
            return s[:200]
    return ""


def append_symbols(kb_dir: Path, paper_id: str, symbols: Sequence[SymbolRecord]) -> int:
    """Append symbols to ``<kb_dir>/symbols.jsonl``. Returns count written.

    Best-effort: a single line is one JSON object. Caller is responsible
    for not double-writing the same paper_id; this writer does not dedup.
    """
    if not symbols:
        return 0
    kb_dir = Path(kb_dir)
    kb_dir.mkdir(parents=True, exist_ok=True)
    path = kb_dir / _SIDECAR_NAME
    with path.open("a", encoding="utf-8") as f:
        for s in symbols:
            f.write(json.dumps(asdict(s), ensure_ascii=False) + "\n")
    return len(symbols)


def iter_symbols(kb_dir: Path, *, name_glob: str | None = None) -> Iterator[SymbolRecord]:
    """Yield symbols from the sidecar, optionally filtered by fnmatch glob."""
    path = Path(kb_dir) / _SIDECAR_NAME
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if name_glob and not fnmatch.fnmatch(obj.get("symbol_name", ""), name_glob):
                continue
            yield SymbolRecord(**obj)


def write_chunks_symbols(*, kb_dir: Path, chunks: Sequence[DocumentChunk]) -> int:
    """Convenience wrapper: project chunks -> symbols -> append to sidecar.

    Groups by ``paper_id`` so all symbols for a paper share one append
    batch (still a single file in the end -- JSONL is one record per line).
    Returns total count written.
    """
    syms = symbols_from_chunks(chunks)
    if not syms:
        return 0
    by_paper: dict[str, list[SymbolRecord]] = {}
    for s in syms:
        by_paper.setdefault(s.paper_id, []).append(s)
    total = 0
    for paper_id, batch in by_paper.items():
        total += append_symbols(kb_dir, paper_id, batch)
    return total
