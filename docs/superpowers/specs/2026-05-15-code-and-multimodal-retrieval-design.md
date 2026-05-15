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
  (Mistral `codestral-embed` as default; `voyage-code-3` / others
  swappable via config). Keep the default text embedder for everything
  else.
- Stay backward-compatible: when no per-type config is set, behave
  identically to today.

### 4.2 Non-goals

- No global migration of stored embeddings. The KB embedding model is
  written into capsule metadata; mixing models across types means the
  per-type model is also recorded in `ChunkMetadata.embedding_model`.
- No automatic re-embedding of historical KBs. A separate `re-embed`
  command can be added later if needed.

### 4.3 Default code embedder: Mistral `codestral-embed`

We will use **Mistral `codestral-embed`**
(<https://mistral.ai/news/codestral-embed>) as the recommended /
example-yaml default for the `code` content type. It is purpose-built
for code retrieval (announced May 2025, beats `voyage-code-3` and
OpenAI `text-embedding-3-large` on the CodeSearchNet, SWE-Bench-Lite
and CommitPack reference benchmarks Mistral published). Available via
litellm under model id `mistral/codestral-embed`.

Important distinction (kept from earlier discussion): Mistral
**`codestral`** is an LLM, not an embedder — passing it to an embedder
API errors. The embedder is **`codestral-embed`** (distinct model id).

The full matrix for reference:

| Embedder | Where | Free tier | Notes |
|---|---|---|---|
| `codestral-embed` | Mistral API via litellm | No (paid) | **Recommended default for code.** Strongest published code-retrieval scores at time of design. |
| `voyage-code-3` | Voyage API via litellm | No (paid) | Strong alternative; configurable. |
| `text-embedding-3-small` | OpenAI via litellm | No (paid) | What we currently use for text. Adequate fallback for code. |
| `all-MiniLM-L6-v2` | sentence-transformers (local) | Yes | Final-fallback. Bad on code but free. |

The config is free-form — the user can swap any of the above. The
example yaml ships `codestral-embed` in the `code` slot. Pricing /
dimensionality at time of design: `codestral-embed` is 1536-dim with
optional Matryoshka truncation to 256 / 512 / 1024 supported by the
provider; we will pass the full 1536 vector and let users configure
truncation later if needed (out of scope for v1).

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
# e.g. {"code": "mistral/codestral-embed", "text": "text-embedding-3-small"}
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
        # "codestral-embed+text-embedding-3-small" — used for KB metadata only.
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
    `["code", "text"]` to two different stub providers (stubs mimicking
    `codestral-embed` and `text-embedding-3-small`); result order
    preserved.
  - Missing key (e.g. only `"code"` configured, text comes in) falls
    through to default.
  - Single-type case (`content_types is None`) goes straight to default
    and matches today's behaviour byte-for-byte.

## 5. Sub-project C — Multimodal modes + figure & code display in the GUI

### 5.1 Goals

1. Add a config knob for multimodal mode: `off` / `auto` / `force`
   (three values). Current behaviour is `auto`. Display is a separate
   orthogonal flag (§5.4), not a mode.
2. In `force` mode, pull top-N figures by relevance from the retrieved
   papers' figure indices even when no chunk explicitly carries
   `figure_refs`. Send them with the LLM call when the model supports
   vision.
3. Add a **display channel** with two attachment kinds: **figures**
   (rendered images) and **code excerpts** (the AST/notebook chunks
   from sub-project A that were used as citations). Both are surfaced
   on the response so the CLI / MCP / web UI can render them inline
   alongside the answer text.
4. In the web UI, code excerpts render as a syntax-highlighted code
   box with a header showing `file_path · symbol_name · lines L<s>-L<e>`
   and a "View on GitHub" link out to the original URI. Figures render
   as captioned thumbnails with a "View source" link to the paper.
5. Expose figures *and* code excerpts via MCP resources so external
   clients can fetch them by id.

### 5.2 Non-goals

- No image-search-by-image. The "relevance" score for `force` mode
  is computed from figure caption embeddings vs the query embedding,
  same way text chunks are scored.
- No image OCR / table-extraction changes (already handled by
  `pipeline/parsers/figures.py`).
- No new web-UI page; the changes are in-place additions to the
  existing `templates/index.html` answer panel.
- No in-browser code editing or "open in IDE" actions. The link-out
  is a normal `<a target="_blank">` to GitHub / GitLab / equivalent.

### 5.3 File changes

```
src/perspicacite/rag/multimodal.py            # MODIFY — force + display
src/perspicacite/rag/figure_retrieval.py      # NEW — figure-level retrieval
src/perspicacite/rag/code_excerpts.py         # NEW — collect code-chunk excerpts
                                              #   from cited chunks; build
                                              #   CodeExcerpt records (with
                                              #   source URI + line range)
src/perspicacite/config/schema.py             # MODIFY — multimodal.mode, .display,
                                              #   multimodal.show_code
src/perspicacite/cli/main.py                  # MODIFY — --figures, --code flags
src/perspicacite/mcp/resources.py             # MODIFY — perspicacite://session/{id}/figures/{fid}
                                              #   and  perspicacite://session/{id}/code/{cid}
src/perspicacite/models/rag.py                # MODIFY — RAGResponse.figures,
                                              #   RAGResponse.code_excerpts
templates/index.html                          # MODIFY — render figures + code-box
static/css/main.css                           # MODIFY — .code-excerpt, .figure-card
static/js/main.js                             # MODIFY — render hooks + Prism init
tests/unit/test_multimodal_modes.py           # NEW
tests/unit/test_code_excerpts.py              # NEW
tests/integration/test_multimodal_force_e2e.py  # NEW (live, opt-in)
tests/web/test_index_renders_attachments.py   # NEW (renders index.html with
                                              #   stub response, asserts code-box
                                              #   + figure markup present)
```

### 5.4 Mode semantics

```python
class MultimodalMode(str, Enum):
    OFF = "off"        # never attach figures
    AUTO = "auto"      # current — attach when chunk.figure_refs is non-empty
    FORCE = "force"    # also pull top-N figures by caption relevance
```

Plus two orthogonal flags (both default False):

- `multimodal.display: bool` — turn on the figure-display channel.
- `multimodal.show_code: bool` — turn on the code-excerpt display
  channel.

**Figure display (`display=True`):**

- Writes each attached figure to
  `<session-dir>/figures/<paper_id>__<fid>.png` and adds a
  `FigureRef(id=fid, paper_id, label, caption, local_path, page,
  source_url)` entry to `RAGResponse.figures`.
- The CLI prints paths after the answer. The MCP exposes them via
  `perspicacite://session/{sid}/figures/{fid}`. The web UI renders
  them as captioned thumbnails.

**Code-excerpt display (`show_code=True`):**

- After retrieval, the system walks the cited chunks and keeps the
  ones with `content_type == "code"`. For each, it emits a
  `CodeExcerpt(id, paper_id, file_path, symbol_name, symbol_kind,
  language, start_line, end_line, text, source_url)` record on
  `RAGResponse.code_excerpts`.
- `source_url` is built from `paper_id`. For GitHub-sourced KBs
  ingested via the GitHub-KB / skill-bundle path, `paper_id` already
  embeds `owner/repo@SHA:path`; the URL is
  `https://github.com/<owner>/<repo>/blob/<sha>/<path>#L<start>-L<end>`.
  For Zotero PDFs, `source_url` falls back to the paper's DOI / Zotero
  link.
- The web UI renders each excerpt as a syntax-highlighted code box
  (Prism.js, already used elsewhere; loaded from CDN with SRI; falls
  back to plain `<pre>` if blocked). Header: `<file_path> · <symbol>
  · L<s>-L<e>`. Footer: "View on GitHub →" anchor.
- The MCP exposes raw excerpt text via
  `perspicacite://session/{sid}/code/{cid}` returning `text/plain`.
- The CLI prints a compact one-liner per excerpt by default
  (`<file>:<line> <symbol> → <url>`) and the full text only when
  `--code-full` is passed.

**CLI flags:**

- `--figures [N]` — sets `multimodal.mode=force`, `display=True`,
  `max_images=N` (default 4).
- `--code` — sets `multimodal.show_code=True`. Implied on by default
  in the web UI; opt-in in CLI to avoid wall-of-code output.
- `--code-full` — in CLI, print the full excerpt text rather than the
  one-liner.

With no flags, behaviour is unchanged from today.

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

### 5.6 MCP resources

Two new readers under `src/perspicacite/mcp/resources.py`:

```python
@mcp.resource("perspicacite://session/{session_id}/figures/{figure_id}")
def get_figure(session_id: str, figure_id: str) -> Resource:
    """Return rendered figure bytes for a session figure (image/png)."""

@mcp.resource("perspicacite://session/{session_id}/code/{excerpt_id}")
def get_code_excerpt(session_id: str, excerpt_id: str) -> Resource:
    """Return the raw code-excerpt text for a session code excerpt
    (text/plain). Body is the excerpt text; metadata header carries
    file_path, symbol, lines, source_url."""
```

`get_figure` reads `<sessions_root>/<session_id>/figures/<figure_id>.png`
and returns `Resource(mimeType="image/png", blob=base64)`. `get_code_excerpt`
reads `<sessions_root>/<session_id>/code/<excerpt_id>.json` (a small
JSON containing text + metadata) and returns the unwrapped text plus
metadata in the resource description. Both surface 404 when missing.

### 5.7 Tests

- `test_multimodal_modes.py`:
  - `MultimodalMode.OFF` strips images from the user message even when
    `figure_refs` exists.
  - `MultimodalMode.AUTO` matches today's behaviour exactly (regression
    guard).
  - `MultimodalMode.FORCE` adds figures when no chunk has refs, deduped
    against auto-figures.
  - `display=True` writes files to a fake session dir and populates
    `RAGResponse.figures`.
- `test_code_excerpts.py`:
  - Given a retrieval result with three chunks (one Python AST chunk,
    one notebook cell, one text chunk), `collect_code_excerpts(...)`
    returns exactly two `CodeExcerpt` records (skips text), each with
    correct `source_url` (GitHub blob URL with `#L<s>-L<e>` for the
    Python chunk; falls back to paper DOI for the notebook).
  - Excerpts are deduplicated by `(paper_id, file_path, start, end)`.
  - `show_code=False` (default) skips the excerpt collection entirely
    — `RAGResponse.code_excerpts` is empty.
- `test_index_renders_attachments.py` (web):
  - Renders `templates/index.html` with a stub `RAGResponse`
    containing one figure and one code excerpt. Asserts:
    - `<figure class="figure-card">` is present with `<img>` and
      caption text.
    - `<div class="code-excerpt">` is present with the file-path
      header, the line-range, a `<pre><code class="language-python">`
      block, and a "View on GitHub" `<a target="_blank">` whose href
      matches the expected GitHub blob URL.
- `test_multimodal_force_e2e.py` (live, slow):
  - Real one-paper KB with figures; query that doesn't surface
    `figure_refs` in chunks; force mode pulls the right figure.
  - GitHub-KB ingest + RAG query; assert `code_excerpts` contains the
    expected function with the right source URL.

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
  CLI flags `--figures`, `--code`, `--code-full` are opt-in.
- `multimodal.show_code` defaults to `False` in CLI / API and to `True`
  in the web UI's default config preset (web users expect code to be
  visible). `RAGResponse.code_excerpts` defaults to an empty list; old
  consumers that ignore the field continue to work.

## 8. Open questions deliberately closed

- **Default code embedder:** Mistral `codestral-embed` (model id
  `mistral/codestral-embed` in litellm). Selected over `voyage-code-3`
  per the public benchmarks Mistral published at launch and the user's
  explicit preference. Users can swap to `voyage-code-3` or any other
  model via `embedding_models_per_type`. Requires a Mistral API key
  (`MISTRAL_API_KEY` env var); when missing, falls through to the
  default embedder with a structured warning.
- **Live verification of `codestral-embed` deferred.** The user does
  not have a `MISTRAL_API_KEY` at design time. Sub-project B ships
  with mocked `codestral-embed` responses in its unit tests (asserting
  the routing path picks the right inner provider and stitches results
  correctly); a live integration test guarded by the env var will be
  added once the key is available, modelled on
  `tests/integration/test_perf_baseline_llm.py`. The fallback path
  (missing key → default embedder + structured warning) is covered by
  unit tests so the codepath is safe to merge before live verification.
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
  {"code": "mistral/codestral-embed"}` writes code chunks under that
  model while text chunks stay on the default. Query path stitches
  results from both correctly via per-type embed + RRF fusion.
- **Sub-project C:**
  - `--figures 4` on a paper KB returns an answer with
    `RAGResponse.figures` populated, image files written to the
    session dir, and the figure MCP resource returns the rendered
    bytes.
  - `--code` on a GitHub-ingested KB returns an answer with
    `RAGResponse.code_excerpts` populated; each excerpt has a working
    GitHub blob URL with line range. Rendering `templates/index.html`
    against that response shows the answer text, captioned figure
    thumbnails (if any), and one syntax-highlighted code box per
    excerpt with a "View on GitHub" link.

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
