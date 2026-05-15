"""Code-aware chunking (sub-project A of the 2026-05-15 design).

Ports the AST/regex/notebook chunkers from AgenticScienceBuilder's
``script_linker.py`` and adds an optional Tree-sitter path for languages
without a Python ``ast`` equivalent. Each chunk carries symbol_name,
symbol_kind, line range, docstring, and module-level imports in metadata
so downstream symbol-index writes (``pipeline.symbol_index``) can derive
``SymbolRecord``s without re-parsing.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Optional

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.papers import Paper

_DOCSTRING_MAX = 500


def _chunk_python_ast(
    text: str,
    paper: Paper,
    *,
    file_path: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[DocumentChunk]:
    """AST-based Python chunker. One chunk per top-level def / class.

    Falls back to a single module chunk on SyntaxError or empty source
    (matches AgenticScienceBuilder/script_linker.py:55-128).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [_module_chunk(text, paper, file_path=file_path, imports=[])]

    lines = text.splitlines()

    # Collect top-level imports for annotation.
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])

    chunks: list[DocumentChunk] = []
    base_id = paper.id
    idx = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            body_text = "\n".join(lines[start - 1 : end])
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            ds = ast.get_docstring(node)
            md = ChunkMetadata(
                paper_id=base_id,
                chunk_index=idx,
                source=paper.source,
                title=paper.title,
                content_type="code",
                language="python",
                source_file_path=file_path,
                symbol_name=node.name,
                symbol_kind=kind,
                start_line=start,
                end_line=end,
                docstring=ds[:_DOCSTRING_MAX] if ds else None,
                imports=imports,
            )
            chunks.append(
                DocumentChunk(id=f"{base_id}_code_{idx}", text=body_text, metadata=md)
            )
            idx += 1

    if not chunks:
        return [_module_chunk(text, paper, file_path=file_path, imports=imports)]
    return chunks


def _module_chunk(
    text: str, paper: Paper, *, file_path: str, imports: list[str]
) -> DocumentChunk:
    lines = text.splitlines()
    md = ChunkMetadata(
        paper_id=paper.id,
        chunk_index=0,
        source=paper.source,
        title=paper.title,
        content_type="code",
        language="python",
        source_file_path=file_path,
        symbol_name=file_path or "module",
        symbol_kind="module",
        start_line=1,
        end_line=max(len(lines), 1),
        docstring=None,
        imports=imports,
    )
    return DocumentChunk(id=f"{paper.id}_code_0", text=text, metadata=md)


_R_FUNCTION_RE = re.compile(
    r"^(?P<name>[A-Za-z_.][A-Za-z0-9_.]*)\s*<-\s*function\s*\(",
    re.MULTILINE,
)


def _chunk_r_regex(text: str, paper: Paper, *, file_path: str) -> list[DocumentChunk]:
    """Chunk R/Rmd source by ``name <- function(`` pattern.

    Each match starts a chunk that runs until the next match or EOF.
    Falls back to a single module chunk when no functions are found.
    """
    matches = list(_R_FUNCTION_RE.finditer(text))
    if not matches:
        md = ChunkMetadata(
            paper_id=paper.id,
            chunk_index=0,
            source=paper.source,
            title=paper.title,
            content_type="code",
            language="r",
            source_file_path=file_path,
            symbol_name=file_path or "module",
            symbol_kind="module",
            start_line=1,
            end_line=max(text.count("\n") + 1, 1),
            docstring=None,
            imports=[],
        )
        return [DocumentChunk(id=f"{paper.id}_code_0", text=text, metadata=md)]

    chunks: list[DocumentChunk] = []
    for i, m in enumerate(matches):
        start_line = text[: m.start()].count("\n") + 1
        end_char = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        end_line = text[:end_char].count("\n") + 1
        body = text[m.start() : end_char].rstrip()
        md = ChunkMetadata(
            paper_id=paper.id,
            chunk_index=i,
            source=paper.source,
            title=paper.title,
            content_type="code",
            language="r",
            source_file_path=file_path,
            symbol_name=m.group("name"),
            symbol_kind="function",
            start_line=start_line,
            end_line=end_line,
            docstring=None,
            imports=[],
        )
        chunks.append(DocumentChunk(id=f"{paper.id}_code_{i}", text=body, metadata=md))
    return chunks


def _chunk_notebook(text: str, paper: Paper, *, file_path: str) -> list[DocumentChunk]:
    """Chunk a Jupyter notebook by code cell.

    Each code cell becomes a chunk. Cells containing function/class defs
    are sub-split via ``_chunk_python_ast`` so individual functions are
    addressable. Cell outputs are stripped before parse (this is what
    the chunker sees — the disk-side stripping happens at fetch time in
    AgenticScienceBuilder's enrichment.py:_strip_notebook_outputs).

    Falls back to a single module chunk on JSON error or no code cells.
    """
    try:
        nb = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        md = ChunkMetadata(
            paper_id=paper.id, chunk_index=0, source=paper.source, title=paper.title,
            content_type="code", language="python", source_file_path=file_path,
            symbol_name=file_path or "module", symbol_kind="module",
            start_line=1, end_line=max(text.count("\n") + 1, 1),
            docstring=None, imports=[],
        )
        return [DocumentChunk(id=f"{paper.id}_code_0", text=text, metadata=md)]

    cells = nb.get("cells", []) if isinstance(nb, dict) else []
    code_cells = [c for c in cells if c.get("cell_type") == "code"]
    if not code_cells:
        md = ChunkMetadata(
            paper_id=paper.id, chunk_index=0, source=paper.source, title=paper.title,
            content_type="code", language="python", source_file_path=file_path,
            symbol_name=file_path or "module", symbol_kind="module",
            start_line=1, end_line=1, docstring=None, imports=[],
        )
        return [DocumentChunk(id=f"{paper.id}_code_0", text="", metadata=md)]

    chunks: list[DocumentChunk] = []
    out_idx = 0
    for i, cell in enumerate(code_cells, start=1):
        src_lines = cell.get("source", [])
        cell_text = "".join(src_lines) if isinstance(src_lines, list) else str(src_lines)
        cell_name = f"{file_path}::cell_{i}"

        # Sub-chunk via AST if it has function/class defs.
        sub = _chunk_python_ast(cell_text, paper, file_path=cell_name,
                                chunk_size=1000, chunk_overlap=200)
        has_defs = any(c.metadata.symbol_kind in ("function", "class") for c in sub)
        if has_defs:
            # Re-index the sub-chunks so the parent paper's chunk_index is sequential.
            for s in sub:
                md = s.metadata.model_copy(update={"chunk_index": out_idx})
                chunks.append(DocumentChunk(id=f"{paper.id}_code_{out_idx}",
                                            text=s.text, metadata=md))
                out_idx += 1
        else:
            line_count = cell_text.count("\n") + 1
            md = ChunkMetadata(
                paper_id=paper.id, chunk_index=out_idx, source=paper.source,
                title=paper.title, content_type="code", language="python",
                source_file_path=file_path,
                symbol_name=cell_name, symbol_kind="cell",
                start_line=1, end_line=line_count, docstring=None, imports=[],
            )
            chunks.append(DocumentChunk(id=f"{paper.id}_code_{out_idx}",
                                        text=cell_text, metadata=md))
            out_idx += 1
    return chunks
