# Code-aware chunking + symbol index + notebooks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace splitter-only code chunking with AST-aware Python chunking, regex R chunking, cell-aware notebook chunking, and an optional Tree-sitter path; emit a per-KB symbol index sidecar.

**Architecture:** A new `pipeline/chunking_code.py` module ports AgenticScienceBuilder's `script_linker.py` shape (`~/git/AgenticScienceBuilder/src/agentic_science_builder/script_linker.py:55-263`). `pipeline/chunking_dispatch.py` routes code content there when `KnowledgeBaseConfig.code_chunking != "splitter"`. Each chunk carries `symbol_name`, `symbol_kind`, `start_line`, `end_line`, `docstring`, and `imports` in metadata. A new `pipeline/symbol_index.py` derives `SymbolRecord`s from chunks and appends them to `<kb-dir>/symbols.jsonl`. Optional Tree-sitter path activates only when `tree_sitter_languages` is importable.

**Tech Stack:** Python `ast` (stdlib), `langchain_text_splitters` (already present), optional `tree-sitter` + `tree-sitter-languages` (new optional extra), pytest-asyncio (already configured).

**Spec:** `docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md` (sub-project A)

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `src/perspicacite/models/documents.py` | MODIFY | Add 5 optional `ChunkMetadata` fields |
| `src/perspicacite/pipeline/symbol_index.py` | CREATE | `SymbolRecord`, `symbols_from_chunks`, `append_symbols`, `iter_symbols` |
| `src/perspicacite/pipeline/chunking_code.py` | CREATE | AST/regex/notebook/Tree-sitter backends + `chunk_code` entry |
| `src/perspicacite/pipeline/chunking_dispatch.py` | MODIFY | Route code content through `chunk_code` |
| `src/perspicacite/config/schema.py` | MODIFY | Add `KnowledgeBaseConfig.code_chunking: Literal["auto","ast","splitter"]` |
| `src/perspicacite/pipeline/capsule_builder.py` | MODIFY | After chunking code blocks, write symbols sidecar |
| `src/perspicacite/integrations/local_docs.py` | MODIFY | Same hook |
| `src/perspicacite/integrations/capsule_reader.py` | MODIFY | Same hook |
| `pyproject.toml` | MODIFY | Add optional `code-parsing` extra (`tree-sitter`, `tree-sitter-languages`) |
| `tests/unit/test_chunking_code_ast.py` | CREATE | Python AST chunker tests |
| `tests/unit/test_chunking_code_r.py` | CREATE | R regex chunker tests |
| `tests/unit/test_chunking_code_notebook.py` | CREATE | Notebook chunker tests |
| `tests/unit/test_chunking_code_treesitter.py` | CREATE | Tree-sitter optional path tests |
| `tests/unit/test_symbol_index.py` | CREATE | Sidecar reader/writer tests |
| `tests/unit/test_chunking_dispatch_code.py` | CREATE | Dispatch routing tests |
| `tests/integration/test_chunking_code_e2e.py` | CREATE | Live, opt-in: ingest small real repo |

---

## Task 1: Extend `ChunkMetadata` with code-aware fields

**Files:**
- Modify: `src/perspicacite/models/documents.py:10-35`
- Test: `tests/unit/test_chunk_metadata_code_fields.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunk_metadata_code_fields.py
from perspicacite.models.documents import ChunkMetadata


def test_code_fields_default_to_none_or_empty():
    md = ChunkMetadata(paper_id="p1", chunk_index=0)
    assert md.symbol_name is None
    assert md.symbol_kind is None
    assert md.start_line is None
    assert md.end_line is None
    assert md.docstring is None
    assert md.imports == []


def test_code_fields_round_trip():
    md = ChunkMetadata(
        paper_id="p1",
        chunk_index=0,
        symbol_name="fit_transform",
        symbol_kind="function",
        start_line=42,
        end_line=87,
        docstring="Fit and transform.",
        imports=["numpy", "scipy"],
    )
    assert md.symbol_name == "fit_transform"
    assert md.imports == ["numpy", "scipy"]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunk_metadata_code_fields.py -v
```

Expected: FAIL with `AttributeError: 'ChunkMetadata' object has no attribute 'symbol_name'`.

- [ ] **Step 3: Add the fields**

Append inside `class ChunkMetadata(BaseModel):` in `src/perspicacite/models/documents.py` (after the existing fields, before the closing of the class body):

```python
    # Sub-project A (code-aware chunking) extensions — all optional.
    symbol_name: Optional[str] = None
    symbol_kind: Optional[str] = None  # "function" | "class" | "method" | "cell" | "module"
    start_line: Optional[int] = None   # 1-indexed inclusive
    end_line: Optional[int] = None     # 1-indexed inclusive
    docstring: Optional[str] = None    # ≤500 chars, truncated
    imports: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_chunk_metadata_code_fields.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_chunk_metadata_code_fields.py src/perspicacite/models/documents.py
git commit -m "feat(models): code-aware ChunkMetadata fields (symbol_name/kind/lines/docstring/imports)"
```

---

## Task 2: Symbol-index sidecar module

**Files:**
- Create: `src/perspicacite/pipeline/symbol_index.py`
- Test: `tests/unit/test_symbol_index.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_symbol_index.py
from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.pipeline.symbol_index import (
    SymbolRecord,
    append_symbols,
    iter_symbols,
    symbols_from_chunks,
)


def _make_chunk(symbol_name, kind, start, end, **kwargs):
    md = ChunkMetadata(
        paper_id="github:owner/repo@abc:file.py",
        chunk_index=0,
        content_type="code",
        language="python",
        symbol_name=symbol_name,
        symbol_kind=kind,
        start_line=start,
        end_line=end,
        source_file_path=kwargs.get("file_path", "file.py"),
        docstring=kwargs.get("docstring"),
        imports=kwargs.get("imports", []),
    )
    return DocumentChunk(id=f"c_{symbol_name}", text="def x(): pass", metadata=md)


def test_symbols_from_chunks_extracts_code_only():
    code = _make_chunk("fit", "function", 1, 10)
    text_md = ChunkMetadata(paper_id="p", chunk_index=1, content_type="text")
    text_chunk = DocumentChunk(id="t", text="hello", metadata=text_md)
    syms = symbols_from_chunks([code, text_chunk])
    assert len(syms) == 1
    assert syms[0].symbol_name == "fit"


def test_append_and_iter_round_trip(tmp_path: Path):
    sym = SymbolRecord(
        paper_id="p1",
        symbol_name="fit",
        symbol_kind="function",
        file_path="file.py",
        start_line=1,
        end_line=10,
        signature="def fit()",
        docstring=None,
        imports=["numpy"],
    )
    append_symbols(tmp_path, "p1", [sym])
    out = list(iter_symbols(tmp_path))
    assert len(out) == 1
    assert out[0].symbol_name == "fit"
    assert out[0].imports == ["numpy"]


def test_iter_symbols_name_glob_filter(tmp_path: Path):
    a = SymbolRecord(paper_id="p", symbol_name="fit_transform", symbol_kind="function",
                     file_path="a.py", start_line=1, end_line=2, signature="def fit_transform()",
                     docstring=None, imports=[])
    b = SymbolRecord(paper_id="p", symbol_name="predict", symbol_kind="function",
                     file_path="a.py", start_line=10, end_line=11, signature="def predict()",
                     docstring=None, imports=[])
    append_symbols(tmp_path, "p", [a, b])
    out = list(iter_symbols(tmp_path, name_glob="fit_*"))
    assert [s.symbol_name for s in out] == ["fit_transform"]


def test_append_is_append_only(tmp_path: Path):
    s1 = SymbolRecord(paper_id="p1", symbol_name="a", symbol_kind="function",
                      file_path="x.py", start_line=1, end_line=2, signature="def a()",
                      docstring=None, imports=[])
    s2 = SymbolRecord(paper_id="p2", symbol_name="b", symbol_kind="function",
                      file_path="y.py", start_line=1, end_line=2, signature="def b()",
                      docstring=None, imports=[])
    append_symbols(tmp_path, "p1", [s1])
    append_symbols(tmp_path, "p2", [s2])
    out = list(iter_symbols(tmp_path))
    assert {s.paper_id for s in out} == {"p1", "p2"}
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_symbol_index.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'perspicacite.pipeline.symbol_index'`.

- [ ] **Step 3: Implement the module**

Create `src/perspicacite/pipeline/symbol_index.py`:

```python
"""Per-KB symbol index sidecar (one JSONL line per symbol).

Append-only; one record per top-level function / class / notebook cell
extracted by ``pipeline.chunking_code``. Read with ``iter_symbols`` for
agentic symbol lookup without going through dense retrieval.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Optional, Sequence

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
    docstring: Optional[str]  # ≤500 chars, truncated
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


def iter_symbols(kb_dir: Path, *, name_glob: Optional[str] = None) -> Iterator[SymbolRecord]:
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_symbol_index.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_symbol_index.py src/perspicacite/pipeline/symbol_index.py
git commit -m "feat(pipeline): symbol_index sidecar (SymbolRecord, append_symbols, iter_symbols)"
```

---

## Task 3: AST Python chunker

**Files:**
- Create: `src/perspicacite/pipeline/chunking_code.py`
- Test: `tests/unit/test_chunking_code_ast.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunking_code_ast.py
from __future__ import annotations

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_python_ast


def _paper():
    return Paper(
        id="github:o/r@abc:f.py",
        title="t",
        abstract="",
        source=PaperSource.BIBTEX,
    )


def test_single_function_yields_one_function_chunk():
    src = (
        "import numpy\n"
        "import scipy.stats as st\n\n"
        "def fit(x):\n"
        '    """Fit a model."""\n'
        "    return x\n"
    )
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    md = chunks[0].metadata
    assert md.symbol_name == "fit"
    assert md.symbol_kind == "function"
    assert md.start_line == 3
    assert md.end_line == 5
    assert md.docstring == "Fit a model."
    assert "numpy" in md.imports
    assert "scipy" in md.imports
    assert md.language == "python"
    assert md.content_type == "code"


def test_async_function_marked_function():
    src = "async def go():\n    pass\n"
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "function"
    assert chunks[0].metadata.symbol_name == "go"


def test_class_yields_single_class_chunk_not_methods():
    src = (
        "class Pipeline:\n"
        "    def step_a(self):\n"
        "        pass\n"
        "    def step_b(self):\n"
        "        pass\n"
    )
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    # ASB convention: top-level only — one class chunk, not two methods.
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "class"
    assert chunks[0].metadata.symbol_name == "Pipeline"


def test_syntax_error_falls_back_to_module():
    src = "def f(:\n  bad\n"
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"


def test_no_top_level_defs_falls_back_to_module():
    src = "x = 1\ny = 2\n"
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"


def test_docstring_truncated_to_500_chars():
    long = "x " * 400  # 800 chars
    src = f'def f():\n    """{long}"""\n    pass\n'
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert chunks[0].metadata.docstring is not None
    assert len(chunks[0].metadata.docstring) <= 500
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunking_code_ast.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'perspicacite.pipeline.chunking_code'`.

- [ ] **Step 3: Implement the AST chunker**

Create `src/perspicacite/pipeline/chunking_code.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_chunking_code_ast.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_chunking_code_ast.py src/perspicacite/pipeline/chunking_code.py
git commit -m "feat(pipeline): AST-aware Python chunking (ports ASB script_linker chunk_python)"
```

---

## Task 4: R / Rmd regex chunker

**Files:**
- Modify: `src/perspicacite/pipeline/chunking_code.py`
- Test: `tests/unit/test_chunking_code_r.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunking_code_r.py
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_r_regex


def _paper():
    return Paper(id="github:o/r@abc:f.R", title="t", abstract="", source=PaperSource.BIBTEX)


def test_two_functions_two_chunks():
    src = (
        "library(dplyr)\n\n"
        "foo <- function(x) {\n"
        "  x + 1\n"
        "}\n\n"
        "bar.baz <- function(y, z) {\n"
        "  y * z\n"
        "}\n"
    )
    chunks = _chunk_r_regex(src, _paper(), file_path="f.R")
    names = [c.metadata.symbol_name for c in chunks]
    assert names == ["foo", "bar.baz"]
    assert all(c.metadata.symbol_kind == "function" for c in chunks)
    assert all(c.metadata.content_type == "code" for c in chunks)
    assert all(c.metadata.language == "r" for c in chunks)


def test_no_functions_falls_back_to_module():
    src = "x <- 1\ny <- 2\n"
    chunks = _chunk_r_regex(src, _paper(), file_path="f.R")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunking_code_r.py -v
```

Expected: FAIL with `ImportError: cannot import name '_chunk_r_regex'`.

- [ ] **Step 3: Add the R regex chunker**

Append to `src/perspicacite/pipeline/chunking_code.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_chunking_code_r.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_chunking_code_r.py src/perspicacite/pipeline/chunking_code.py
git commit -m "feat(pipeline): R/Rmd regex chunker (ports ASB script_linker chunk_r)"
```

---

## Task 5: Notebook (`.ipynb`) chunker

**Files:**
- Modify: `src/perspicacite/pipeline/chunking_code.py`
- Test: `tests/unit/test_chunking_code_notebook.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunking_code_notebook.py
import json

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_notebook


def _paper():
    return Paper(id="github:o/r@abc:nb.ipynb", title="t", abstract="", source=PaperSource.BIBTEX)


def _nb(cells):
    return json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


def test_one_code_cell_one_chunk():
    src = _nb([
        {"cell_type": "markdown", "source": ["# Title\n"]},
        {"cell_type": "code", "source": ["x = 1\n", "y = 2\n"], "outputs": [{"output_type": "stream"}]},
    ])
    chunks = _chunk_notebook(src, _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "cell"
    assert chunks[0].metadata.symbol_name == "nb.ipynb::cell_1"
    # Cell outputs must be stripped from the chunk text.
    assert "output_type" not in chunks[0].text


def test_cell_with_function_def_yields_function_chunk():
    src = _nb([
        {"cell_type": "code", "source": ["def hello():\n", "    return 1\n"]},
    ])
    chunks = _chunk_notebook(src, _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "function"
    assert chunks[0].metadata.symbol_name == "hello"


def test_malformed_json_falls_back_to_module():
    chunks = _chunk_notebook("not json {", _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"


def test_no_code_cells_yields_empty_module():
    src = _nb([{"cell_type": "markdown", "source": ["# x"]}])
    chunks = _chunk_notebook(src, _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunking_code_notebook.py -v
```

Expected: FAIL with `ImportError: cannot import name '_chunk_notebook'`.

- [ ] **Step 3: Add the notebook chunker**

Append to `src/perspicacite/pipeline/chunking_code.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_chunking_code_notebook.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_chunking_code_notebook.py src/perspicacite/pipeline/chunking_code.py
git commit -m "feat(pipeline): notebook (.ipynb) chunker with cell→AST sub-split"
```

---

## Task 6: Optional Tree-sitter path

**Files:**
- Modify: `src/perspicacite/pipeline/chunking_code.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_chunking_code_treesitter.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunking_code_treesitter.py
import importlib.util

import pytest

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import (
    HAS_TREE_SITTER,
    _chunk_treesitter,
)


def _paper():
    return Paper(id="github:o/r@abc:f.go", title="t", abstract="", source=PaperSource.BIBTEX)


def test_constant_is_false_when_dep_missing():
    # Treat as a runtime probe — the constant equals importability.
    expected = importlib.util.find_spec("tree_sitter_languages") is not None
    assert HAS_TREE_SITTER == expected


@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree_sitter_languages not installed")
def test_go_function_extracted():
    src = (
        "package main\n\n"
        "func Hello(name string) string {\n"
        "    return \"hi \" + name\n"
        "}\n"
    )
    chunks = _chunk_treesitter(src, _paper(), file_path="f.go", language="go")
    assert chunks is not None
    names = [c.metadata.symbol_name for c in chunks]
    assert "Hello" in names


def test_returns_none_when_dep_unavailable_and_caller_falls_back():
    # When the dep isn't installed, _chunk_treesitter must return None
    # so the dispatcher can fall through to the splitter.
    if HAS_TREE_SITTER:
        pytest.skip("dep present; this guard is only meaningful when absent")
    out = _chunk_treesitter("func F() {}\n", _paper(), file_path="f.go", language="go")
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunking_code_treesitter.py -v
```

Expected: FAIL with `ImportError: cannot import name 'HAS_TREE_SITTER'`.

- [ ] **Step 3: Add the Tree-sitter path**

Append to `src/perspicacite/pipeline/chunking_code.py`:

```python
# Optional Tree-sitter path. Activates when `tree_sitter_languages` is
# importable; otherwise _chunk_treesitter returns None and the dispatcher
# falls back to the LangChain splitter for the same content.
try:
    import importlib

    importlib.import_module("tree_sitter_languages")  # noqa: F401
    HAS_TREE_SITTER = True
except Exception:
    HAS_TREE_SITTER = False


_TS_NODE_TYPES = {
    # node_type → symbol_kind. Language-specific names are unified here.
    "function_declaration": "function",
    "function_definition": "function",
    "method_definition": "method",
    "method_declaration": "method",
    "class_declaration": "class",
    "class_definition": "class",
    "struct_specifier": "class",
    "struct_item": "class",
    "type_declaration": "class",
}


def _chunk_treesitter(
    text: str, paper: Paper, *, file_path: str, language: str
) -> Optional[list[DocumentChunk]]:
    """Tree-sitter-backed chunker for non-Python languages.

    Returns None when the optional dep isn't installed OR when the parser
    cannot be obtained for ``language``; the caller falls back to the
    splitter. Never raises.
    """
    if not HAS_TREE_SITTER:
        return None
    try:
        from tree_sitter_languages import get_parser  # type: ignore
    except Exception:
        return None
    try:
        parser = get_parser(language)
    except Exception:
        return None
    try:
        tree = parser.parse(text.encode("utf-8"))
    except Exception:
        return None

    lines = text.splitlines()
    chunks: list[DocumentChunk] = []
    idx = 0

    def _walk(node: Any) -> None:
        nonlocal idx
        for child in node.children:
            kind = _TS_NODE_TYPES.get(child.type)
            if kind is not None:
                start_row = child.start_point[0] + 1
                end_row = child.end_point[0] + 1
                body = "\n".join(lines[start_row - 1 : end_row])
                name = _ts_extract_name(child) or f"{kind}_{idx}"
                md = ChunkMetadata(
                    paper_id=paper.id,
                    chunk_index=idx,
                    source=paper.source,
                    title=paper.title,
                    content_type="code",
                    language=language,
                    source_file_path=file_path,
                    symbol_name=name,
                    symbol_kind=kind,
                    start_line=start_row,
                    end_line=end_row,
                    docstring=None,
                    imports=[],
                )
                chunks.append(DocumentChunk(id=f"{paper.id}_code_{idx}",
                                            text=body, metadata=md))
                idx += 1
            else:
                _walk(child)

    _walk(tree.root_node)

    if not chunks:
        md = ChunkMetadata(
            paper_id=paper.id, chunk_index=0, source=paper.source, title=paper.title,
            content_type="code", language=language, source_file_path=file_path,
            symbol_name=file_path or "module", symbol_kind="module",
            start_line=1, end_line=max(len(lines), 1), docstring=None, imports=[],
        )
        return [DocumentChunk(id=f"{paper.id}_code_0", text=text, metadata=md)]
    return chunks


def _ts_extract_name(node: Any) -> Optional[str]:
    """Best-effort name extraction: scan children for an ``identifier`` /
    ``type_identifier`` / ``name`` node and use its text."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "name"):
            try:
                return child.text.decode("utf-8")
            except Exception:
                return None
    # Some grammars nest the name one level deeper (e.g. function_declarator).
    for child in node.children:
        nested = _ts_extract_name(child)
        if nested:
            return nested
    return None
```

- [ ] **Step 4: Add the optional dep group**

Edit `pyproject.toml`. Locate the `[project.optional-dependencies]` table (create if absent) and add:

```toml
code-parsing = [
  "tree-sitter>=0.21",
  "tree-sitter-languages>=1.10",
]
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/unit/test_chunking_code_treesitter.py -v
```

Expected: the constant-probe test passes. The Go test is skipped when the dep is absent (current local env) and passes when installed (`pip install perspicacite[code-parsing]`).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_chunking_code_treesitter.py src/perspicacite/pipeline/chunking_code.py pyproject.toml
git commit -m "feat(pipeline): optional Tree-sitter chunker for non-Python languages"
```

---

## Task 7: `KnowledgeBaseConfig.code_chunking` field

**Files:**
- Modify: `src/perspicacite/config/schema.py:50-70`
- Test: `tests/unit/test_kb_config_code_chunking.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_kb_config_code_chunking.py
import pytest
from pydantic import ValidationError

from perspicacite.config.schema import KnowledgeBaseConfig


def test_default_is_auto():
    cfg = KnowledgeBaseConfig()
    assert cfg.code_chunking == "auto"


def test_explicit_values_accepted():
    for v in ("auto", "ast", "splitter"):
        cfg = KnowledgeBaseConfig(code_chunking=v)
        assert cfg.code_chunking == v


def test_invalid_value_rejected():
    with pytest.raises(ValidationError):
        KnowledgeBaseConfig(code_chunking="treesitter")  # not in literal


def test_legacy_code_language_aware_still_present():
    cfg = KnowledgeBaseConfig()
    assert cfg.code_language_aware is True  # back-compat
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_kb_config_code_chunking.py -v
```

Expected: FAIL — `test_default_is_auto` raises `AttributeError`.

- [ ] **Step 3: Add the field**

In `src/perspicacite/config/schema.py`, immediately after the `code_language_aware` field (around line 65), add:

```python
    code_chunking: Literal["auto", "ast", "splitter"] = Field(
        default="auto",
        description=(
            "Code-chunking strategy. 'auto' prefers AST/Tree-sitter and "
            "falls back to the splitter. 'ast' fails loud (logs and falls "
            "back) when AST/TS is unavailable. 'splitter' keeps today's "
            "language-aware splitter behaviour."
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_kb_config_code_chunking.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_kb_config_code_chunking.py src/perspicacite/config/schema.py
git commit -m "feat(config): KnowledgeBaseConfig.code_chunking (auto|ast|splitter)"
```

---

## Task 8: Wire dispatch + add `chunk_code` entry point

**Files:**
- Modify: `src/perspicacite/pipeline/chunking_code.py` (add public `chunk_code`)
- Modify: `src/perspicacite/pipeline/chunking_dispatch.py:161-229`
- Test: `tests/unit/test_chunking_dispatch_code.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunking_dispatch_code.py
import pytest

from perspicacite.config.schema import KnowledgeBaseConfig
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import chunk_document


def _paper():
    return Paper(id="github:o/r@abc:f.py", title="t", abstract="", source=PaperSource.BIBTEX)


@pytest.mark.asyncio
async def test_auto_routes_python_to_ast():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    src = "def foo():\n    return 1\n\nclass Bar:\n    pass\n"
    chunks = await chunk_document(src, _paper(), content_type="code",
                                  language="python", config=cfg)
    kinds = sorted({c.metadata.symbol_kind for c in chunks})
    assert kinds == ["class", "function"]


@pytest.mark.asyncio
async def test_splitter_preserves_today_behaviour():
    cfg = KnowledgeBaseConfig(code_chunking="splitter")
    src = "def foo():\n    return 1\n"
    chunks = await chunk_document(src, _paper(), content_type="code",
                                  language="python", config=cfg)
    # Splitter path does not set symbol_name.
    assert all(c.metadata.symbol_name is None for c in chunks)


@pytest.mark.asyncio
async def test_r_routes_to_regex():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    src = "foo <- function(x) x + 1\n"
    paper = Paper(id="github:o/r@abc:f.R", title="t", abstract="",
                  source=PaperSource.BIBTEX)
    chunks = await chunk_document(src, paper, content_type="code",
                                  language="r", config=cfg)
    assert [c.metadata.symbol_name for c in chunks] == ["foo"]


@pytest.mark.asyncio
async def test_ipynb_routes_to_notebook():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    paper = Paper(id="github:o/r@abc:nb.ipynb", title="t", abstract="",
                  source=PaperSource.BIBTEX)
    nb = (
        '{"cells":[{"cell_type":"code","source":["def x():\\n","    return 1\\n"]}],'
        '"metadata":{},"nbformat":4,"nbformat_minor":5}'
    )
    chunks = await chunk_document(nb, paper, content_type="code",
                                  language="ipynb", config=cfg)
    assert any(c.metadata.symbol_name == "x" for c in chunks)


@pytest.mark.asyncio
async def test_text_content_type_unchanged():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    paper = Paper(id="p", title="t", abstract="", source=PaperSource.BIBTEX)
    chunks = await chunk_document("just plain text " * 200, paper,
                                  content_type="text", language=None, config=cfg)
    # Plain text never touches the code chunker.
    assert all(c.metadata.symbol_name is None for c in chunks)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunking_dispatch_code.py -v
```

Expected: FAIL — auto routing not yet wired.

- [ ] **Step 3: Add `chunk_code` public entry to `chunking_code.py`**

Append to `src/perspicacite/pipeline/chunking_code.py`:

```python
def chunk_code(
    text: str,
    paper: Paper,
    *,
    language: str,
    file_path: Optional[str],
    chunk_size: int,
    chunk_overlap: int,
) -> Optional[list[DocumentChunk]]:
    """Dispatch entry. Returns None when no backend applies (caller falls
    back to the splitter)."""
    fp = file_path or ""
    lang = (language or "").lower()
    if lang == "python":
        return _chunk_python_ast(text, paper, file_path=fp,
                                 chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if lang in ("r", "rmd"):
        return _chunk_r_regex(text, paper, file_path=fp)
    if lang == "ipynb":
        return _chunk_notebook(text, paper, file_path=fp)
    ts_langs = {"javascript", "typescript", "go", "rust", "java", "cpp",
                "ruby", "swift", "kotlin", "csharp"}
    if lang in ts_langs:
        return _chunk_treesitter(text, paper, file_path=fp, language=lang)
    return None
```

- [ ] **Step 4: Wire `chunking_dispatch.chunk_document`**

In `src/perspicacite/pipeline/chunking_dispatch.py`, replace the body of `_chunk_code` (lines 161-187) and the routing in `chunk_document` (lines 206-229) with:

```python
def _chunk_code(text: str, paper: Paper, config: Any, *, language: str) -> list[DocumentChunk]:
    """Splitter-based code chunker (legacy fallback)."""
    chunk_size, chunk_overlap = _chunk_size_overlap(config)
    lc_lang = _LANG_TO_LC.get(language)
    if lc_lang is None:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )
    else:
        splitter = RecursiveCharacterTextSplitter.from_language(
            lc_lang, chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        )
    base_id = paper.id
    chunks: list[DocumentChunk] = []
    for i, piece in enumerate(splitter.split_text(text)):
        md = ChunkMetadata(
            paper_id=base_id, chunk_index=i, source=paper.source,
            title=paper.title, content_type="code", language=language,
        )
        chunks.append(DocumentChunk(id=f"{base_id}_code_{i}", text=piece, metadata=md))
    return chunks


async def chunk_document(
    text: str,
    paper: Paper,
    *,
    content_type: str,
    language: Optional[str],
    config: Any,
) -> list[DocumentChunk]:
    """Dispatch chunking by content type.

    Routing:
    - markdown + ``markdown_heading_aware``  → heading-stack splitter
    - code     + ``code_chunking != 'splitter'`` → ``chunking_code.chunk_code``
                                                    (falls back to splitter on None)
    - everything else → ``chunk_text``
    """
    if content_type == "markdown" and getattr(config, "markdown_heading_aware", True):
        return _chunk_markdown(text, paper, config)

    if content_type == "code" and language:
        mode = getattr(config, "code_chunking", "auto")
        if mode != "splitter":
            from perspicacite.pipeline.chunking_code import chunk_code
            cs, co = _chunk_size_overlap(config)
            result = chunk_code(text, paper, language=language,
                                file_path=getattr(paper, "source_file_path", None),
                                chunk_size=cs, chunk_overlap=co)
            if result is not None:
                return result
            # mode == "ast" and backend unavailable → splitter fallback with a log.
            from perspicacite.logging import get_logger
            get_logger("perspicacite.pipeline.chunking_dispatch").warning(
                "code_chunking_ast_unavailable",
                extra={"language": language, "paper_id": paper.id, "mode": mode},
            )
        return _chunk_code(text, paper, config, language=language)

    # Fallback: token chunker.
    from perspicacite.pipeline.chunking import chunk_text
    return await chunk_text(text, paper, _to_chunk_config(config))
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/unit/test_chunking_dispatch_code.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Run the broader chunking test suite to confirm no regression**

```
pytest tests/unit/test_chunking_code_ast.py tests/unit/test_chunking_code_r.py \
       tests/unit/test_chunking_code_notebook.py tests/unit/test_chunking_dispatch_code.py \
       tests/unit/test_chunk_metadata_code_fields.py tests/unit/test_symbol_index.py -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_chunking_dispatch_code.py \
        src/perspicacite/pipeline/chunking_code.py \
        src/perspicacite/pipeline/chunking_dispatch.py
git commit -m "feat(pipeline): chunking_dispatch routes code to AST/TS backends with splitter fallback"
```

---

## Task 9: Plumb symbol-index writes at the ingest call sites

**Files:**
- Modify: `src/perspicacite/pipeline/capsule_builder.py:395-415`
- Modify: `src/perspicacite/integrations/local_docs.py:120-135`
- Modify: `src/perspicacite/integrations/capsule_reader.py:165-180`
- Test: `tests/unit/test_symbol_index_ingest_hook.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_symbol_index_ingest_hook.py
from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.pipeline.symbol_index import write_chunks_symbols, iter_symbols


def _code_chunk(paper_id: str, idx: int, name: str) -> DocumentChunk:
    md = ChunkMetadata(
        paper_id=paper_id,
        chunk_index=idx,
        content_type="code",
        language="python",
        source_file_path="f.py",
        symbol_name=name,
        symbol_kind="function",
        start_line=1,
        end_line=5,
        imports=["numpy"],
    )
    return DocumentChunk(id=f"{paper_id}_{idx}", text=f"def {name}(): pass\n", metadata=md)


def test_writes_one_record_per_code_chunk(tmp_path: Path):
    chunks = [_code_chunk("p1", 0, "fit"), _code_chunk("p1", 1, "predict")]
    n = write_chunks_symbols(kb_dir=tmp_path, chunks=chunks)
    assert n == 2
    out = list(iter_symbols(tmp_path))
    assert {s.symbol_name for s in out} == {"fit", "predict"}


def test_skips_non_code(tmp_path: Path):
    md = ChunkMetadata(paper_id="p1", chunk_index=0, content_type="text")
    text_chunk = DocumentChunk(id="t", text="hello", metadata=md)
    n = write_chunks_symbols(kb_dir=tmp_path, chunks=[text_chunk])
    assert n == 0
    assert list(iter_symbols(tmp_path)) == []
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_symbol_index_ingest_hook.py -v
```

Expected: FAIL — `write_chunks_symbols` not exported.

- [ ] **Step 3: Expose the helper**

Append to `src/perspicacite/pipeline/symbol_index.py`:

```python
def write_chunks_symbols(*, kb_dir: Path, chunks: Sequence[DocumentChunk]) -> int:
    """Convenience wrapper: project chunks → symbols → append to sidecar.

    Groups by ``paper_id`` so all symbols for a paper share one append
    batch (still a single file in the end — JSONL is one record per line).
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
```

- [ ] **Step 4: Hook into capsule_builder**

In `src/perspicacite/pipeline/capsule_builder.py`, find the loop that chunks blocks and writes them to the KB (around line 395-415). Add **after** the existing `chunks = await chunk_document(...)` and any code that persists chunks, the following:

```python
        # Sub-project A: symbol-index sidecar
        from perspicacite.pipeline.symbol_index import write_chunks_symbols
        try:
            kb_dir = getattr(self, "kb_dir", None) or kwargs.get("kb_dir")
            if kb_dir is not None:
                write_chunks_symbols(kb_dir=kb_dir, chunks=chunks)
        except Exception as exc:  # never break ingest on sidecar failure
            from perspicacite.logging import get_logger
            get_logger("perspicacite.pipeline.capsule_builder").warning(
                "symbol_index_write_failed",
                extra={"error": str(exc)[:200]},
            )
```

If `kb_dir` is not already reachable from this scope, locate the surrounding function signature (likely `chunk_capsule_blocks(... ,kb_dir: Path | None = None)`) and add `kb_dir` as a keyword argument with default `None`. The caller (likely in `capsule_builder.build_capsule`) should already have access to the KB directory; thread it through.

- [ ] **Step 5: Hook into `local_docs.py`**

In `src/perspicacite/integrations/local_docs.py`, near line 127 where `chunk_document` is called, add the same symbol-index write hook right after the chunks list is returned, threading `kb_dir` from the caller.

- [ ] **Step 6: Hook into `capsule_reader.py`**

In `src/perspicacite/integrations/capsule_reader.py`, near line 170 where `chunk_document` is called, add the same hook.

- [ ] **Step 7: Run test to verify it passes**

```
pytest tests/unit/test_symbol_index_ingest_hook.py -v
```

Expected: 2 passed.

- [ ] **Step 8: Run all sub-project A unit tests**

```
pytest tests/unit/test_chunk_metadata_code_fields.py \
       tests/unit/test_symbol_index.py \
       tests/unit/test_chunking_code_ast.py \
       tests/unit/test_chunking_code_r.py \
       tests/unit/test_chunking_code_notebook.py \
       tests/unit/test_chunking_code_treesitter.py \
       tests/unit/test_chunking_dispatch_code.py \
       tests/unit/test_symbol_index_ingest_hook.py \
       tests/unit/test_kb_config_code_chunking.py -v
```

Expected: all green; tree-sitter Go test skipped when dep absent.

- [ ] **Step 9: Commit**

```bash
git add tests/unit/test_symbol_index_ingest_hook.py \
        src/perspicacite/pipeline/symbol_index.py \
        src/perspicacite/pipeline/capsule_builder.py \
        src/perspicacite/integrations/local_docs.py \
        src/perspicacite/integrations/capsule_reader.py
git commit -m "feat(pipeline): write symbols.jsonl sidecar from ingest call sites"
```

---

## Task 10: Live E2E test (opt-in)

**Files:**
- Create: `tests/integration/test_chunking_code_e2e.py`

- [ ] **Step 1: Write the live test**

```python
# tests/integration/test_chunking_code_e2e.py
"""Live integration test for sub-project A: ingest a tiny real GitHub
repo and assert AST chunking + symbol index land correctly.

Marked ``live + slow`` — only runs when explicitly selected.
Knob: PERSPICACITE_LIVE_CODE_CHUNKING=1 to opt in.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.slow]


SKIP = os.environ.get("PERSPICACITE_LIVE_CODE_CHUNKING") != "1"


@pytest.mark.skipif(SKIP, reason="set PERSPICACITE_LIVE_CODE_CHUNKING=1 to run")
@pytest.mark.asyncio
async def test_ingest_small_repo_produces_ast_chunks_and_symbols(tmp_path: Path):
    """Use the existing GitHub-KB ingest path on a small fixed repo,
    then verify the symbol index has the expected functions."""
    from perspicacite.pipeline.github_skill_bundle import ingest_github_repo  # type: ignore
    from perspicacite.pipeline.symbol_index import iter_symbols

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()

    # Tiny known repo: tiangolo/typer — has top-level Python with clear funcs.
    # Restrict to a single small file to keep test under 60s.
    await ingest_github_repo(
        repo_url="https://github.com/tiangolo/typer",
        kb_dir=kb_dir,
        restrict_to_files=["typer/__init__.py"],
    )

    syms = list(iter_symbols(kb_dir))
    assert len(syms) >= 1, "expected at least one symbol from typer/__init__.py"
    assert any(s.symbol_kind in ("function", "class", "module") for s in syms)
    assert all(s.start_line >= 1 for s in syms)
    assert all(s.end_line >= s.start_line for s in syms)
```

- [ ] **Step 2: Run with the gate off (default)**

```
pytest tests/integration/test_chunking_code_e2e.py -v
```

Expected: 1 skipped (gate off).

- [ ] **Step 3: Run with the gate on**

```
PERSPICACITE_LIVE_CODE_CHUNKING=1 pytest tests/integration/test_chunking_code_e2e.py -v -s
```

Expected: 1 passed in ≤ 60s. (Skipped if `ingest_github_repo` doesn't yet support `restrict_to_files`; this is a known parameter from the GitHub-KB ingest spec.)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_chunking_code_e2e.py
git commit -m "test(integration): live E2E for code-aware chunking (opt-in via env var)"
```

---

## Self-Review

**Spec coverage** (`docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md` sub-project A):

| Spec section | Task |
|---|---|
| §3.1.1 AST Python | Task 3 |
| §3.1.2 Notebook | Task 5 |
| §3.1.3 R/Rmd | Task 4 |
| §3.1.4 Tree-sitter | Task 6 |
| §3.1.5 Module imports attached | Task 3 (collected via `ast.walk`) |
| §3.1.6 Docstring boost | Task 3 (`ast.get_docstring`, ≤500 chars in Task 1) |
| §3.1.7 Symbol index sidecar | Tasks 2, 9 |
| §3.3 File map | Matches "File Map" table above |
| §3.4 ChunkMetadata extensions | Task 1 |
| §3.4 `symbols.jsonl` format | Task 2 |
| §3.6 `KnowledgeBaseConfig.code_chunking` | Task 7 |
| §3.7 Tests (AST / notebook / R / symbol-index / E2E) | Tasks 3, 4, 5, 2, 10 |
| §3.7 Tree-sitter optional test | Task 6 |
| §3.7 Async function → "function" kind | Task 3 (test_async_function_marked_function) |
| §3.7 SyntaxError → module fallback | Task 3 |

No gaps.

**Placeholder scan:** No "TBD"/"TODO"/vague instructions; every code-touching step contains the actual code to write or the exact regex/literal/signature.

**Type consistency:**
- `ChunkMetadata` field names: `symbol_name`, `symbol_kind`, `start_line`, `end_line`, `docstring`, `imports` — used identically across Tasks 1, 3, 4, 5, 6, 9 and tests.
- `SymbolRecord` constructor signature in Task 2 matches the calls in `symbols_from_chunks` (Task 2) and `write_chunks_symbols` (Task 9).
- `chunk_code(text, paper, *, language, file_path, chunk_size, chunk_overlap)` (Task 8 step 3) matches the call site in `chunk_document` (Task 8 step 4).
- `_chunk_python_ast(text, paper, *, file_path, chunk_size, chunk_overlap)` signature consistent between Task 3, Task 5 (notebook sub-call), and Task 8 dispatcher.
- `HAS_TREE_SITTER` (Task 6) imported in tests only — never used as a control-flow gate in dispatch (Task 8 returns `None` from `_chunk_treesitter` instead, which the dispatcher already handles).

**Decomposition order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. Each task is testable on its own. Task 9 depends on Tasks 1–8 having landed (it relies on chunks carrying the new metadata, which only happens once dispatch routes through `chunk_code`).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-code-aware-chunking.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review (spec compliance then code quality) between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session via executing-plans with checkpoints.

Which approach?

(After this plan is finalised, three more plans are queued from the same brainstorm: sub-project B per-type embeddings, sub-project C figure/code display, and the cite-graph enrichment spec. I can generate them now or after sub-project A ships — your call.)
