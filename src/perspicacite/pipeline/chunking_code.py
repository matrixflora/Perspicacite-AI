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
            start = node.lineno - 1
            end = getattr(node, "end_lineno", node.lineno) - 1
            body_text = "\n".join(lines[start : end + 1])
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
