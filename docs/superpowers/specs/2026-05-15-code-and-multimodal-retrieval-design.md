# Code-aware ingestion + per-type embeddings + multimodal retrieval modes — design spec

**Date:** 2026-05-15
**Status:** Draft, awaiting approval
**Companion plan:** `docs/superpowers/plans/2026-05-15-code-and-multimodal-retrieval.md` (to be generated after approval)

## 1. Motivation

The current Perspicacité-AI pipeline can ingest code and Jupyter notebooks
(via the GitHub-KB / skill-bundle path landed earlier today), but the
indexing of that code is shallow:

- `pipeline/chunking_dispatch._chunk_code` uses LangChain's
  `RecursiveCharacterTextSplitter.from_language` for 12 languages. The
  splitter is separator-aware (`def`, `class`, blank lines) but has no
  AST/Tree-sitter understanding. There is no symbol index, no docstring
  boost, no notebook-cell awareness, and no R/Snakefile support.
- All content types share a single embedder
  (`AsyncEmbeddingProvider` selects one model — typically
  `text-embedding-3-small` or `all-MiniLM-L6-v2`). Code embeddings
  benefit substantially from code-specialised models (Voyage code-3,
  Mistral `codestral-embed`).
- Multimodal RAG today auto-attaches figure bytes only when a retrieved
  chunk explicitly carries `figure_refs`. There is no "force figures",
  no figure-display channel for the CLI / MCP / web UI, and no way for
  the user to ask "show me Figure 3 from each paper that mentioned
  XYZ".

The sister project **AgenticScienceBuilder** (`~/git/AgenticScienceBuilder/src/agentic_science_builder/script_linker.py`)
already does most of the code-side work — AST chunking for Python, regex
chunking for R, cell-aware chunking for `.ipynb`, hybrid BM25+dense
retrieval over chunks with reciprocal-rank fusion, and an LLM reranker.
Where the designs diverge, we will follow ASB's shape so that the two
codebases can share idioms and (potentially) a small helper module
later.

## 2. Scope and sub-project decomposition

This spec covers three sub-projects that are independent enough to ship
separately but related enough to share a single design doc:

- **A. Code-aware chunking + symbol index + notebook support**
- **B. Per-content-type embedding routing**
- **C. Multimodal retrieval modes (force / display)**

A single Cython/perf sidebar (§6) covers the question "what other
improvements does having Cython at install time unlock?" — the main
answer is bm25s, which has its own pending spec discussed separately.

**Decomposition recommendation:** ship A first (biggest gap, no
external dependency); B second (small, mostly config); C third (small,
ergonomic). Each gets its own implementation plan via writing-plans.

## 3. Sub-project A — Code-aware chunking, symbol index, notebooks

### 3.1 Goals

1. Replace splitter-only Python chunking with AST chunking that emits
   one chunk per top-level `FunctionDef` / `AsyncFunctionDef` /
   `ClassDef`. Preserve start/end lines and the docstring.
2. Add notebook (`.ipynb`) support: one chunk per code cell, with
   inner AST split when the cell contains function/class defs. Outputs
   stripped on read.
3. Add R / Rmd support via the same regex chunker ASB uses.
4. Add an optional Tree-sitter path for languages with no `ast`
   equivalent (JS, TS, Go, Rust, Java, C++, Ruby, Swift, Kotlin, C#).
   Gracefully fall back to the current splitter when the optional dep
   isn't installed.
5. Attach module-level imports to every chunk (so a query that names
   a library matches even when the import lives outside the function
   body — same idea as ASB).
6. Extract the first docstring of each function/class. Surface it in
   the chunk's `searchable_text` and in a new `ChunkMetadata.docstring`
   field. This is the "docstring boost".
7. Build a per-KB **symbol index** sidecar
   (`<kb-dir>/symbols.jsonl`) so the agentic layer can look up "where
   is function `foo` defined?" without going through dense retrieval.

### 3.2 Non-goals

- No call-graph / type-inference / cross-file symbol resolution. The
  symbol index is a flat list, not a graph. Cross-file is a follow-up.
- No semantic-aware chunk merging (e.g. merging an overloaded function
  signature with its `@overload` stub).
- No reranker change. The ASB-style LLM reranker is already covered by
  Perspicacité's existing reranker module.

### 3.3 File changes

```
src/perspicacite/pipeline/
  chunking_code.py                 # NEW — AST + Tree-sitter + R + ipynb
  chunking_dispatch.py             # MODIFY — route to new module
src/perspicacite/models/documents.py
                                    # MODIFY — add ChunkMetadata fields
src/perspicacite/pipeline/symbol_index.py
                                    # NEW — write/read symbols.jsonl sidecar
src/perspicacite/models/kb.py / config/schema.py
                                    # MODIFY — KnowledgeBaseConfig.code_chunking
tests/unit/test_chunking_code_ast.py        # NEW
tests/unit/test_chunking_code_notebook.py   # NEW
tests/unit/test_chunking_code_r.py          # NEW
tests/unit/test_symbol_index.py             # NEW
tests/integration/test_chunking_code_e2e.py # NEW
pyproject.toml                              # MODIFY — optional `code-parsing` extra
```

### 3.4 Data model

`ChunkMetadata` (in `models/documents.py`) gains:

```python
symbol_name: Optional[str] = None      # e.g. "fit_transform"
symbol_kind: Optional[str] = None      # "function" | "class" | "method" | "cell" | "module"
start_line: Optional[int] = None       # 1-indexed, inclusive
end_line: Optional[int] = None         # 1-indexed, inclusive
docstring: Optional[str] = None        # first docstring text, ≤500 chars
imports: list[str] = Field(default_factory=list)  # top-level module names
```

All are nullable — existing rows load unchanged. Chroma metadata flatteners
already handle `list[str]` via JSON encoding; the writer keeps that path.

`symbols.jsonl` (per-KB sidecar, append-only, one JSON object per line):

```json
{
  "paper_id": "github:owner/repo@SHA:path/to/file.py",
  "symbol_name": "fit_transform",
  "symbol_kind": "function",
  "file_path": "path/to/file.py",
  "start_line": 42,
  "end_line": 87,
  "signature": "def fit_transform(self, X, y=None)",
  "docstring": "Fit and transform in one pass...",
  "imports": ["numpy", "scipy", "sklearn"]
}
```

Written by `pipeline/symbol_index.py:append_symbols(kb_dir, paper_id, symbols)`
in the same transaction as chunk ingestion (i.e. after Chroma `add` succeeds).
Reader: `iter_symbols(kb_dir, *, name_glob=None)`.

### 3.5 Chunker shape

`chunking_code.py` exposes one entry point and four backends:

```python
async def chunk_code(
    text: str, paper: Paper, *, language: str, file_path: str | None, config
) -> tuple[list[DocumentChunk], list[SymbolRecord]]:
    """Dispatch to AST / Tree-sitter / R-regex / notebook / splitter.

    Returns chunks AND a parallel list of symbol records (one per chunk).
    Falls back to the current LangChain splitter on SyntaxError, empty
    output, or missing optional deps. Never raises.
    """
```

Backends:

- `_chunk_python_ast(...)` — port of ASB's `chunk_python` (see
  `~/git/AgenticScienceBuilder/src/agentic_science_builder/script_linker.py:55-128`).
  Top-level FunctionDef / AsyncFunctionDef / ClassDef; falls back to a
  single module chunk on `SyntaxError` or no defs. Attaches module
  imports. Extracts docstring via `ast.get_docstring`.
- `_chunk_notebook(...)` — port of ASB's `chunk_notebook`. JSON-parse,
  one chunk per code cell, sub-chunk each cell via `_chunk_python_ast`.
  Outputs stripped before parse (reduces ipynb size 5–10×).
- `_chunk_r_regex(...)` — port of ASB's `chunk_r`. Pattern
  `^(?P<name>[A-Za-z_.][A-Za-z0-9_.]*)\s*<-\s*function\s*\(`. Module
  fallback when no matches.
- `_chunk_treesitter(text, language)` — uses `tree_sitter_languages`
  when available. Queries the language grammar for
  `(function_declaration|function_definition|method_definition|class_declaration)`
  nodes. **Optional dep:** behind `from importlib import util` check;
  no-op fallback when not installed. Recommended language set: JS, TS,
  Go, Rust, Java, C++, Ruby, Swift, Kotlin, C#.
- `_chunk_splitter(text, language)` — the existing
  `RecursiveCharacterTextSplitter.from_language` path. Used as fallback
  by all of the above.

Searchable text format (mirrors ASB so that text and code retrieval
share a structural-marker convention):

```
[FILE] path/to/file.py
[FUNCTION] fit_transform
[IMPORTS] numpy scipy sklearn
[DOCSTRING] Fit and transform in one pass over X.
[CODE] def fit_transform(self, X, y=None):
    ...
```

### 3.6 Config

`KnowledgeBaseConfig` gets one new field:

```python
code_chunking: Literal["splitter", "ast", "auto"] = "auto"
# "auto"     — prefer AST/Tree-sitter; fall back to splitter
# "ast"      — fail (log warning + fall back) if AST/TS unavailable
# "splitter" — current behaviour, keep splitter
```

The legacy `code_language_aware: bool` field stays as-is; when
`code_chunking == "splitter"` and `code_language_aware` is True, we use
the splitter exactly as today. No breaking change.

### 3.7 Tests

- `test_chunking_code_ast.py`:
  - 1 function → 1 chunk, name + line ranges + imports + docstring
    populated.
  - 1 class with 2 methods → 1 class chunk (not 2 methods — top-level
    only, matches ASB).
  - SyntaxError → 1 module chunk.
  - Empty file → 1 module chunk (empty text).
  - Async function → `symbol_kind == "function"` (ASB's choice).
- `test_chunking_code_notebook.py`:
  - 3-cell notebook (markdown / code / markdown) → 1 chunk for the code
    cell.
  - Code cell with a function def → AST split, function chunk emitted.
  - Cell outputs are stripped from the text passed to the chunker.
  - Malformed JSON → 1 module chunk.
- `test_chunking_code_r.py`:
  - 2 R functions → 2 chunks; module fallback when no `<- function(`
    pattern.
- `test_symbol_index.py`:
  - `append_symbols` writes one line per symbol; `iter_symbols` reads
    them back. Glob filter works (`name_glob="fit_*"`).
- `test_chunking_code_e2e.py` (live, slow):
  - Ingest one small real GitHub repo (e.g. `tiangolo/typer` README +
    a few `.py` files); assert ≥ N AST chunks, symbol index has ≥ M
    entries, docstrings populated.

### 3.8 Performance

- AST: O(n) in file length; negligible vs embedding cost.
- Notebook: JSON-parse + AST per cell; bounded by cell count.
- Tree-sitter: incremental parsing, fast.
- Symbol index write: one `open(..., "a")` per paper; no fsync.

## 4. Sub-project B — Per-content-type embedding routing

### 4.1 Goals

- Use a code-specialised embedder for `content_type == "code"` chunks
  (Voyage code-3 by default; `codestral-embed` / others swappable via
  config). Keep the default text embedder for everything else.
- Stay backward-compatible: when no per-type config is set, behave
  identically to today.

### 4.2 Non-goals

- No global migration of stored embeddings. The KB embedding model is
  written into capsule metadata; mixing models across types means the
  per-type model is also recorded in `ChunkMetadata.embedding_model`.
- No automatic re-embedding of historical KBs. A separate `re-embed`
  command can be added later if needed.

### 4.3 Notes on Codestral

Mistral's **`codestral`** is an LLM (chat / completion), not an
embedding model — passing it to an embedder API will return an error.
What Mistral *does* offer for code embeddings is **`codestral-embed`**
(distinct model id). The matrix:

| Embedder | Where | Free tier | Notes |
|---|---|---|---|
| `voyage-code-3` | Voyage API via litellm | No (paid) | Strongest code-retrieval scores on CodeSearchNet at time of design. Recommended default. |
| `codestral-embed` | Mistral API via litellm | No (paid) | Comparable on Python, weaker on Go/Rust per public benchmarks. |
| `text-embedding-3-small` | OpenAI via litellm | No (paid) | What we currently use for text. Adequate fallback for code. |
| `all-MiniLM-L6-v2` | sentence-transformers (local) | Yes | Final-fallback. Bad on code but free. |

We will **not** ship a specific paid provider as the default. The
config is free-form; the example yaml sets `voyage-code-3` for the
`code` slot and leaves `text` on whatever the user already configured.

### 4.4 File changes

```
src/perspicacite/llm/embeddings.py    # MODIFY — add TypedEmbeddingProvider
src/perspicacite/config/schema.py     # MODIFY — LLMConfig.embedding_models_per_type
src/perspicacite/rag/dynamic_kb.py    # MODIFY — wire TypedEmbeddingProvider when configured
config.claude_code.example.yml        # MODIFY — show the per-type block (commented)
tests/unit/test_embedding_typed_router.py  # NEW
```

### 4.5 Data model

`LLMConfig`:

```python
embedding_models_per_type: dict[str, str] = Field(default_factory=dict)
# e.g. {"code": "voyage-code-3", "text": "text-embedding-3-small"}
# Missing keys fall through to the default embedder.
```

`TypedEmbeddingProvider` wraps two or more inner providers:

```python
class TypedEmbeddingProvider:
    def __init__(self, *, default, by_content_type: dict[str, AsyncEmbeddingProvider]):
        self._default = default
        self._by_type = by_content_type

    @property
    def model_name(self) -> str:
        # "voyage-code-3+text-embedding-3-small" — used for KB metadata only.
        ...

    @property
    def dimension(self) -> int | None:
        # None when providers disagree on dim — caller must split per-type.
        ...

    async def embed(self, texts: list[str], *, content_types: list[str] | None = None):
        # Partitions texts by content_type; runs each partition through its
        # provider concurrently; stitches results back in original order.
```

### 4.6 Embedding-model metadata in chunks

`ChunkMetadata.embedding_model: Optional[str] = None` records the
*actual* model used for that chunk. This lets `vec_search` route a
query through the correct embedder when KBs mix per-type models.

### 4.7 Routing in the retrieval path

At query time, `KBSearch` already loads the KB's embedder. With per-type
routing, the search layer:

1. Embeds the query with each unique model present in the KB's chunks.
2. Runs Chroma `query` once per model on the chunks tagged with that
   model.
3. Merges results via reciprocal-rank fusion (same RRF the rest of the
   stack uses).

For KBs where all chunks share one model, behaviour is identical to
today (single embed, single Chroma query).

### 4.8 Tests

- `test_embedding_typed_router.py`:
  - Routes `["def f(): pass", "the cat sat"]` with content types
    `["code", "text"]` to two different stub providers; result order
    preserved.
  - Missing key (e.g. only `"code"` configured, text comes in) falls
    through to default.
  - Single-type case (`content_types is None`) goes straight to default
    and matches today's behaviour byte-for-byte.

## 5. Sub-project C — Multimodal retrieval modes (force / display)

### 5.1 Goals

1. Add a config knob for multimodal mode: `off` / `auto` / `force`
   (three values). Current behaviour is `auto`. Display is a separate
   orthogonal flag (§5.4), not a mode.
2. In `force` mode, pull top-N figures by relevance from the retrieved
   papers' figure indices even when no chunk explicitly carries
   `figure_refs`. Send them with the LLM call when the model supports
   vision.
3. Add a **display** channel: copy the rendered figures into the
   session artefact dir and surface a structured `figures` field on
   the response, so the CLI / MCP / web UI can show them inline.
4. Expose figures via an MCP resource so external clients can fetch
   the rendered image bytes by id.

### 5.2 Non-goals

- No new web UI work. The CLI prints a path; the MCP exposes a URI;
  whether the user opens the image is their call.
- No image-search-by-image. The "relevance" score for `force` mode
  is computed from figure caption embeddings vs the query embedding,
  same way text chunks are scored.
- No image OCR / table-extraction changes (already handled by
  `pipeline/parsers/figures.py`).

### 5.3 File changes

```
src/perspicacite/rag/multimodal.py            # MODIFY — force + display
src/perspicacite/rag/figure_retrieval.py      # NEW — figure-level retrieval
src/perspicacite/config/schema.py             # MODIFY — multimodal.mode, .display
src/perspicacite/cli/main.py                  # MODIFY — --figures flag
src/perspicacite/mcp/resources.py             # MODIFY — perspicacite://session/{id}/figures/{fid}
src/perspicacite/models/responses.py          # MODIFY — Response.figures
tests/unit/test_multimodal_modes.py           # NEW
tests/integration/test_multimodal_force_e2e.py  # NEW (live, opt-in)
```

### 5.4 Mode semantics

```python
class MultimodalMode(str, Enum):
    OFF = "off"        # never attach figures
    AUTO = "auto"      # current — attach when chunk.figure_refs is non-empty
    FORCE = "force"    # also pull top-N figures by caption relevance
```

Plus an orthogonal flag `multimodal.display: bool` (default False):

- `display=True` writes each attached figure to
  `<session-dir>/figures/<paper_id>__<fid>.png` and adds a
  `FigureRef(id=fid, paper_id, label, caption, local_path, page,
  source_url)` entry to `Response.figures`.
- The CLI prints paths after the answer. The MCP exposes them via
  `perspicacite://session/{sid}/figures/{fid}`.
- A new CLI flag `--figures [N]` toggles `mode=force` with cap N
  (default 4) and `display=True`. With no flag, current `auto`
  behaviour is unchanged.

### 5.5 `force` retrieval

`rag/figure_retrieval.py:retrieve_figures(query, papers, *, top_k, embedder)`:

1. For each paper in the retrieved set, load `figures/index.json` from
   the paper's capsule.
2. Build a `caption` corpus: `f"[Figure {label}] {caption}"`.
3. Embed query + corpus with the *text* embedder (captions are
   natural-language).
4. Rank by cosine similarity; return top_k `FigureContext` records.
5. Apply the existing `build_multimodal_messages` cap policy
   (non-supplementary first, lower numbers first).

This sits *alongside* the existing chunk-driven attach path: in
`force` mode, the LLM call gets `figures = auto_figures ∪ force_figures`,
deduplicated by `fid`, capped at `multimodal.max_images`.

### 5.6 MCP resource

```python
@mcp.resource("perspicacite://session/{session_id}/figures/{figure_id}")
def get_figure(session_id: str, figure_id: str) -> Resource:
    """Return rendered figure bytes for a session figure."""
```

Reads `<sessions_root>/<session_id>/figures/<figure_id>.png` and
returns `Resource(mimeType="image/png", blob=base64)`. Surfaces 404
when missing.

### 5.7 Tests

- `test_multimodal_modes.py`:
  - `MultimodalMode.OFF` strips images from the user message even when
    `figure_refs` exists.
  - `MultimodalMode.AUTO` matches today's behaviour exactly (regression
    guard).
  - `MultimodalMode.FORCE` adds figures when no chunk has refs, deduped
    against auto-figures.
  - `display=True` writes files to a fake session dir and populates
    `Response.figures`.
- `test_multimodal_force_e2e.py` (live, slow):
  - Real one-paper KB with figures; query that doesn't surface
    `figure_refs` in chunks; force mode pulls the right figure.

## 6. Cython / perf sidebar (cross-cut, not its own sub-project)

The user asked: "If we have Cython installed, what else can we
improve?" The main win is **bm25s** (Cython-accelerated BM25 with
persistent index files). That was offered as a separate spec in the
previous conversation and is NOT in scope for this design. Other
candidates:

| Lib | Wins | Worth it now? |
|---|---|---|
| `bm25s` | persistent BM25 index; 5–10× faster index build; lower memory | **Yes**, separate spec |
| `hnswlib` | tighter ANN control vs Chroma | No — Chroma is a stable boundary |
| `faiss-cpu` | GPU-ready ANN | No — Chroma is enough |
| `tree-sitter` | Cython-accelerated AST parsing for non-Python langs | **Yes**, part of Sub-project A as optional dep |
| `tiktoken` | already pulled in | already in use |
| `numpy/scipy` | already Cython-backed | already in use |

We will add `tree-sitter` + `tree-sitter-languages` as an **optional
extra** (`pip install perspicacite[code-parsing]`) so the dep doesn't
slow down minimal installs.

## 7. Backward compatibility

- All new `ChunkMetadata` fields are nullable / default-empty. Existing
  Chroma rows load with these fields as None.
- `KnowledgeBaseConfig.code_chunking` defaults to `"auto"`, which
  prefers AST/Tree-sitter when present and falls back to the current
  splitter — so existing KBs continue to ingest cleanly.
- `LLMConfig.embedding_models_per_type` defaults to `{}`, which routes
  every content type through the existing single embedder.
- `multimodal.mode` defaults to `"auto"`, the current behaviour. The
  CLI flag `--figures` is opt-in.

## 8. Open questions deliberately closed

- **Codestral vs Voyage as default code embedder:** neither is shipped
  as a default. Example yaml uses `voyage-code-3` because it is
  better-benchmarked on multi-language code today; users can swap.
- **Symbol index storage:** JSONL sidecar, not SQLite. Append-only,
  one line per symbol, glob-on-read. SQLite is overkill at expected
  sizes (≤100k symbols per KB).
- **Cross-file symbol resolution:** out of scope. Symbol index is flat.
- **Web-UI image display:** out of scope. We expose paths + MCP
  resources; rendering is downstream.

## 9. Success criteria

- **Sub-project A:** ingesting a small real GitHub repo (e.g.
  `tiangolo/typer`) produces AST chunks with populated `symbol_name`,
  `docstring`, `imports`, and a `symbols.jsonl` sidecar. Existing text
  KBs ingest unchanged (regression test).
- **Sub-project B:** a KB configured with `embedding_models_per_type =
  {"code": "voyage-code-3"}` writes code chunks under that model while
  text chunks stay on the default. Query path stitches results from
  both correctly.
- **Sub-project C:** `--figures 4` on a paper KB returns an answer
  with `Response.figures` populated and image files written to the
  session dir. The MCP resource returns the rendered bytes.

## 10. Decomposition for writing-plans

The implementation plan will be one document with three clearly
separated task groups, so writing-plans can scope the first task group
(A) without depending on the others. Suggested order:

1. **A** — Code-aware chunking + symbol index + notebooks (~3 days,
   biggest user-visible gap)
2. **B** — Per-type embedding routing (~1 day, mostly config; landing
   B right after A means new code-KB ingestions pick up the
   code-specialised embedder from day one instead of needing a
   re-embed later)
3. **C** — Multimodal modes + display + MCP resource (~1.5 days,
   ergonomically valuable, low-risk, independent of A and B)
