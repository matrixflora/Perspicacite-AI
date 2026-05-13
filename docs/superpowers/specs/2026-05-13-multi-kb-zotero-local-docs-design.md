# Multi-KB Expansion, Zotero-as-KB-Source, Local Documents, and Smart Chunking — Design

**Date:** 2026-05-13
**Status:** Approved — ready for plan.
**Authors:** Louis-Félix Nothias + Claude (brainstormed in-session).
**Predecessors:**
- `docs/superpowers/specs/2026-05-12-multi-feature-v2x-expansion-design.md` (cycle 1)
- `docs/superpowers/specs/2026-05-13-provenance-and-infra-expansion-design.md` (cycle 2)

This is **cycle 3** of feature work on Perspicacité. The two prior cycles added provenance, jobs, RAG mode polish, and infra completeness; this cycle expands **how knowledge bases are built and queried**.

Constraints carried over from prior cycles (still in force):

- **Additive-only.** No breaking schema/API/CLI changes. Existing single-KB callers keep working unchanged.
- **Test gates.** `uv run pytest tests/unit/ -m "not live"` stays green. No new ruff/mypy errors on touched lines. We do **not** fix the pre-existing backlog.
- **Commit cadence.** One conventional commit per task, committed directly to `main` (this is the user's documented preference).
- **Config.** Every new key documented in `config.example.yml`. New optional config blocks must default to safe/no-op.
- **UI verification.** New UI features get file-presence + light DOM-shape tests; full click-through goes in `MANUAL_QA.md`.

---

## 1. Goal & Scope

Three additive feature areas, plus one quality fix, bundled as one cycle because they touch closely-related surfaces (KB ingestion, KB retrieval, MCP, web UI).

### 1.1 Multi-KB query expansion (P1)

**What:** Honor `RAGRequest.kb_names: list[str]` in the four RAG modes that currently ignore it: `advanced`, `profound`, `literature_survey`, `agentic`. Today only `basic` and `contradiction` honor it; [docs/rules/rag_development.md](../rules/rag_development.md) and `CLAUDE.md` both flag this as a known gap.

**Why:** Users who organize literature into multiple topical KBs already expect "search all my KBs" to work in any mode. Currently the chat UI silently falls back to the first KB when more than one is selected for the four advanced modes.

**Out of scope:** Cross-KB query expansion, re-embedding KBs into a unified embedding space, federated remote KBs.

### 1.2 Zotero-as-source-of-KB (P2)

**What:** A "Build KBs from Zotero" flow that fetches a configured Zotero library and ingests it as one KB per **top-level collection**. Subcollections roll up into their parent. Items not in any collection go into a default "Library — Unfiled" KB. Each item contributes: DOI metadata, attached PDFs (when downloadable), and notes (HTML stripped → plain text). The flow is plan-then-execute: users pick which top-level collections to ingest, are shown the resulting KB names and item counts, then click Execute. Execution is async via the existing JobRegistry with SSE progress.

**Why:** Zotero is the de facto reference manager for researchers in this audience. Today, users export a `.bib` file and lose attached PDFs and notes in the process. This makes the round trip native and one-click.

**Out of scope:** Two-way sync (Zotero → KB only, never KB → Zotero). Pushing newly-ingested KB items back to Zotero (the existing `ZoteroClient.create_item` push path remains a separate feature). Group library write paths.

### 1.3 Local documents → KB, with content-type-aware chunking (P3)

**What:** Three ways to ingest local files into any KB at any time:

1. **Web upload** — multipart drag-and-drop in the KB detail panel (per-KB).
2. **CLI** — `uv run perspicacite ingest-local --kb <name> --path <p> [--recursive]`.
3. **MCP** — server-side path entry (the tool reads files on the server filesystem), behind an explicit allow-list.

Accepted file types: PDF, plain text, markdown (`.md`/`.mdx`), source code (`.py`, `.js`, `.ts`, `.tsx`, `.go`, `.rs`, `.java`, `.cpp`/`.cc`/`.h`/`.hpp`, `.rb`, `.swift`, `.kt`, `.cs`).

Chunking dispatches by content type:

- **PDF** → existing `pdf_parser` + paper-style chunking (today's behavior).
- **Markdown** → heading-aware splitter (preserves section context), code fences kept atomic.
- **Source code** → language-aware splitter via `langchain-text-splitters`' `RecursiveCharacterTextSplitter.from_language(...)`.
- **Plain text** → fall back to existing token-based chunker.

**Security (local-docs server-side path entry + MCP only):**

- Reject any path that isn't absolute.
- Reject any path containing `..`.
- Resolve the path and require it to be under one of `local_docs.allowed_roots`.
- If `local_docs.allowed_roots` is unset/empty, the **server-side path endpoint returns 503** and the MCP tool refuses every call (default-deny).
- The web multipart upload path is unaffected by `allowed_roots` (server receives bytes, never reads from the user's filesystem).

**Why:** Researchers regularly want to ask questions about reports, internal docs, code repositories, lab notebooks. Today there is no path for any of this — `add_dois_to_kb` only takes DOIs and BibTeX only carries metadata.

**Out of scope:** GitHub-repo-as-source connector (URL → clone → ingest). Watch-folder / continuous sync. Office-format conversion (`.docx`, `.xlsx`, `.pptx`) — punt to a follow-up. OCR of scanned PDFs.

### 1.4 Smart-chunking dispatch as a reusable layer (also P3)

Local-doc ingest forces us to commit to a content-type-aware chunker. We extract it as `pipeline/chunking_dispatch.py` so it can also be reused by future GitHub/docs connectors. **Paper-style chunking (existing `AdvancedChunker`) is preserved and continues to handle PDFs.** The dispatch only adds new branches for markdown and code; it does not replace the existing chunker.

---

## 2. Multi-KB query (P1) — design

### 2.1 Where the gap is today

`BaseRAGMode._build_kb_retriever()` (in [src/perspicacite/rag/modes/base.py](../../src/perspicacite/rag/modes/base.py)) already returns a `MultiKBRetriever` when `request.kb_names` has more than one entry. `basic.py` and `contradiction.py` route through this helper. The other four modes don't:

- **advanced.py** — its `_wrrf_retrieval` calls `vector_store.search(collection=...)` and `vector_store.get_chunks_by_paper_ids(collection, ...)` directly against a single collection name from `chroma_collection_name_for_kb(request.kb_name)`.
- **profound.py** — its `_execute_step` and two-pass retrieval do the same: hardcoded single-collection `vector_store.search` / `get_chunks_by_paper_ids` calls.
- **literature_survey.py** — does not retrieve from any KB at all; it uses SciLEx external search. Multi-KB is an API-contract / propagation issue (downstream tools the survey may emit, plus consistent `RAGResponse.sources[].kb_name` tagging).
- **agentic/orchestrator.py** — the `KB_SEARCH` step builds a single-KB `DynamicKnowledgeBase` from `self.kb_name`.

### 2.2 The fan-out helpers

Add two functions to `src/perspicacite/retrieval/multi_kb.py` that take a list of collection names and replicate the single-collection helpers `advanced`/`profound`/`agentic` actually use:

```python
async def query_chunks_across_collections(
    vector_store,
    embedding_service,
    *,
    collection_names: list[str],
    query: str,
    top_k: int,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    """Run vector_store.search across all collections in parallel,
    merge by paper_id (keep best score), tag each dict with kb_name,
    sort, return top_k."""

async def get_chunks_by_paper_ids_across(
    vector_store,
    *,
    collection_names: list[str],
    paper_ids: list[str],
) -> list[DocumentChunk]:
    """Fan get_chunks_by_paper_ids across collections in parallel,
    return concatenated list. Caller dedups if needed."""
```

Both are pure helpers — they do not require touching the `MultiKBRetriever` class, which keeps its existing `search` / `search_two_pass` shape that `basic`/`contradiction` already use.

### 2.3 Per-mode wiring

**advanced.py:** Build a `collection_names: list[str]` at the top of `_wrrf_retrieval` (derived from `request.kb_names` if present and >1, else a one-element list). Replace single-collection `vector_store.search` calls with `query_chunks_across_collections`. Replace the `get_chunks_by_paper_ids(collection, ids)` two-pass enrichment with `get_chunks_by_paper_ids_across(collection_names=..., ...)`. Tag each merged result and each emitted `SourceReference` with the originating `kb_name` (already a field on `SourceReference`).

**profound.py:** Same treatment for `_execute_step`, `_two_pass_retrieval`, and `_enrich_with_full_text`. Two-pass paper-level expansion across KBs: for each candidate paper, the helper finds chunks in whichever collection knows that `paper_id` — since `MultiKBRetriever` tags hits with `kb_name`, we pass the resolved (paper_id → kb_name) map down into the enrichment call so we don't fan out across all collections unnecessarily.

**literature_survey.py:** No retrieval against KB — the survey hits SciLEx. The change here is API-contract propagation only:
- Accept `request.kb_names` without erroring.
- Pass it through to any downstream tool calls (e.g., when the survey decides to **store** discovered papers in a KB, it stores in `kb_names[0]` if set, else `kb_name`).
- Source references attached to recommendations carry `kb_name=None` (or the storage KB) — no behavior change for users who only set `kb_name`.

**agentic/orchestrator.py:** When `KB_SEARCH` runs and `self.kb_metas` (a new field, populated from `request.kb_names`) has >1 entry, use a `MultiKBRetriever` instead of the single `DynamicKnowledgeBase`. Tools the orchestrator emits to (the existing `tools/kb_search`, etc.) honor `kb_names` already if they were updated for `basic`/`contradiction`; we re-verify and extend if not.

### 2.4 Embedding-compat check

Already present (`check_embedding_compat`) in `retrieval/multi_kb.py`. We extend the four newly-wired modes to call it once at the start of `execute_stream` and yield a `StreamEvent(event="error", ...)` (matching the existing pattern in `basic`/`contradiction`) when KBs disagree on embedding model.

### 2.5 API surface

No new endpoints. The existing `/api/chat` SSE route, the existing `generate_report` MCP tool, and the existing `search_knowledge_base` MCP tool already accept `kb_names: list[str] | None`. We re-verify they pass it through to every newly-wired mode.

### 2.6 Tests

Unit tests (mock vector store):

- `tests/unit/test_multi_kb_advanced.py` — `_wrrf_retrieval` fans out across two collections, merges, returns kb-tagged sources.
- `tests/unit/test_multi_kb_profound.py` — two-pass enrichment honors the (paper_id → kb_name) map.
- `tests/unit/test_multi_kb_agentic.py` — `KB_SEARCH` step uses `MultiKBRetriever` when `kb_metas` has >1 entry.
- `tests/unit/test_multi_kb_literature_survey.py` — survey mode accepts `kb_names`, emits sources with `kb_name=None` or `kb_names[0]`.
- `tests/unit/test_multi_kb_compat_check.py` — modes yield a stream error when embedding models differ.

---

## 3. Zotero-as-source-of-KB (P2) — design

### 3.1 Zotero Web API v3 — primer

- Auth: `Zotero-API-Key` header + `Zotero-API-Version: 3`.
- Library root: `/users/{id}` or `/groups/{id}`.
- Collections: `GET /{lib}/collections` (paginated, `limit=100`).
- Top-level: `GET /{lib}/collections/top`.
- Items in a collection: `GET /{lib}/collections/{collKey}/items`.
- Items not in any collection: `GET /{lib}/items/top?itemType=-attachment%20%7C%7C%20note&collection=`. We use the `top` endpoint and filter `collections == []` client-side as a more reliable cross-server pattern.
- Item children (attachments, notes): `GET /{lib}/items/{itemKey}/children`.
- Attachment bytes: `GET /{lib}/items/{itemKey}/file` (returns raw bytes for `itemType=attachment` items with `linkMode in {imported_file, imported_url}`).

### 3.2 ZoteroClient extensions

Extend `src/perspicacite/integrations/zotero.py` (currently only has `create_item`):

```python
async def list_collections(self) -> list[dict]:
    """All collections, paginated. Returns raw API objects."""

async def list_top_level_collections(self) -> list[dict]:
    """Collections with no parent, paginated."""

async def list_items_in_collection(self, coll_key: str, *, include_subcollections: bool = True) -> list[dict]:
    """All items in a collection (or rolled up across descendants).
    Excludes attachments and notes — those come via get_item_attachments / get_item_notes per parent."""

async def list_top_level_items_without_collection(self) -> list[dict]:
    """Top-level library items not in any collection."""

async def get_item_attachments(self, item_key: str) -> list[dict]:
    """Children where itemType == 'attachment'."""

async def download_attachment_bytes(self, attachment_key: str) -> bytes | None:
    """Returns the file bytes for an attachment (linkMode in imported_file/imported_url),
    None for linked-file or web-link attachments we can't fetch."""

async def get_item_notes(self, item_key: str) -> list[str]:
    """Children where itemType == 'note'; returns plain text (HTML stripped)."""
```

Plus a private `_html_to_text(html: str) -> str` helper (uses `html.parser` from stdlib; no new dep).

The existing `create_item` method and its dedup-by-DOI behavior stay untouched.

### 3.3 Ingest pipeline — `integrations/zotero_ingest.py` (new)

```python
async def plan_kbs_from_zotero(
    client: ZoteroClient,
    *,
    top_level_collection_keys: list[str] | None = None,  # None = all top-level + unfiled
    include_unfiled: bool = True,
) -> list[ZoteroKBPlanEntry]:
    """Return preview of what would be ingested:
    [ZoteroKBPlanEntry(kb_name, source_collection_key | None, item_count, with_pdf_count, with_doi_count, with_notes_count)]."""

async def build_kbs_from_zotero(
    client: ZoteroClient,
    *,
    plan: list[ZoteroKBPlanEntry],
    app_state,
    registry,        # JobRegistry
    job_id: str,
) -> dict:
    """Execute the plan. For each ZoteroKBPlanEntry:
       1. create the KB if it doesn't exist (idempotent — same name = reuse)
       2. for each Zotero item:
           a. resolve DOI; if found, run the unified content pipeline (already used
              by /api/kb/{name}/dois)
           b. if no usable full text + an attached PDF exists, download attachment
              bytes, parse via app_state.pdf_parser
           c. attach any Zotero notes as additional plain-text 'note' sections
              merged into paper.full_text under a 'Notes' heading
           d. dedup by DOI inside the target KB (paper_exists check)
       3. emit per-item registry.publish progress
       4. on completion update KB metadata counts."""
```

`ZoteroKBPlanEntry` is a small Pydantic model:

```python
class ZoteroKBPlanEntry(BaseModel):
    kb_name: str                      # the target KB name (user-editable in UI)
    source_collection_key: str | None # None = unfiled
    source_collection_name: str | None
    item_count: int
    with_doi_count: int
    with_pdf_count: int
    with_notes_count: int
```

**KB naming convention:** `<library_name>/<collection_name>` (slugified to valid KB name), or `<library_name>/Unfiled` for the unfiled bucket. The web UI lets the user edit the names before clicking Execute.

**Concurrency:** Sequential per-item to keep things simple and to stay polite to Zotero's API. Each top-level collection is processed in order. The plan itself uses concurrent reads (`asyncio.gather`) when listing items across many collections.

### 3.4 Endpoints

Add to a new file `src/perspicacite/web/routers/zotero_ingest.py` (we keep this separate from the existing `zotero.py` router that handles **push**):

- `GET  /api/zotero/plan` — returns `{plan: [ZoteroKBPlanEntry], library_name: str}`. Reads from `app_state.config.zotero`. Returns 503 if Zotero is not configured.
- `POST /api/zotero/build-kbs/async` — body: `{plan: [ZoteroKBPlanEntry]}`. Creates a job in the registry, starts the worker, returns `{job_id, sse_url}`. Progress events stream from the existing `/api/jobs/{id}/events` SSE endpoint.

Both endpoints honor existing auth (Bearer token). The router is registered in `web/app.py` next to the existing routers.

### 3.5 MCP tool

Add one new MCP tool to `src/perspicacite/mcp/server.py` (tool count goes 11 → 12):

```python
@mcp.tool()
async def build_kbs_from_zotero(
    top_level_collection_keys: list[str] | None = None,
    include_unfiled: bool = True,
    plan_only: bool = False,
) -> dict:
    """Build one KB per Zotero top-level collection.
    plan_only=True returns the plan without executing.
    plan_only=False (default) executes the full plan and returns final job summary.
    Requires zotero.enabled = true and credentials in config.yml."""
```

`get_info()` reflects the new count.

### 3.6 UI

Add to `templates/index.html` + `static/js/kb.js`:

- A **"Build KBs from Zotero"** button in the KBs panel header (visible only when `/api/zotero/plan` returns 200).
- Clicking opens a modal:
  - Calls `/api/zotero/plan`, renders a table: `[ ] checkbox | KB name (editable) | source collection | items | with DOI | with PDF | with notes`.
  - "Execute" button posts the (filtered + renamed) plan to `/api/zotero/build-kbs/async`.
  - Switches to a progress view that subscribes to the SSE job and shows a per-item progress bar.
  - On completion, refreshes the KB list.

CSS additions in an existing `static/css/*.css` file (no new files unless one fits the existing naming pattern).

### 3.7 Tests

- `tests/unit/test_zotero_client_read.py` — list/get methods, mocked httpx responses including pagination and 404.
- `tests/unit/test_zotero_ingest_plan.py` — plan builder rolls up subcollections, counts attachments and notes correctly.
- `tests/unit/test_zotero_ingest_worker.py` — worker dedups by DOI, attaches notes, skips when attachment download fails.
- `tests/unit/test_zotero_html_to_text.py` — HTML-to-text helper handles bold, lists, code blocks, multiple paragraphs.
- `tests/unit/test_zotero_ingest_router.py` — endpoints return 503 when zotero not configured; plan and async build endpoints work end-to-end with mocked client + mocked JobRegistry.

---

## 4. Local documents → KB (P3) — design

### 4.1 Web upload (multipart)

Add to `src/perspicacite/web/routers/kb.py`:

```python
@router.post("/api/kb/{name}/local-files")
async def add_local_files(
    name: str,
    files: list[UploadFile] = File(...),
    background_tasks: BackgroundTasks = ...,
) -> JSONResponse:
    """Accept multipart files, write them to a tmpdir, dispatch async ingest worker,
    return {job_id, sse_url}."""
```

**Worker:** `_local_files_ingest_worker(name, file_paths, job_id, registry)` in `integrations/local_docs.py` (new module). The worker:

1. Loads the KB metadata.
2. For each file, calls `infer_content_type(path)` (helper, see §5.1).
3. Routes through:
   - PDF → existing `pdf_parser` → `Paper(..., source=PaperSource.LOCAL, full_text=parsed.text)`, paper-style chunking via the existing `DynamicKnowledgeBase.add_papers` path.
   - Markdown / code / text → bypass `add_papers` (which assumes papers); instead build `DocumentChunk`s directly using `chunking_dispatch.chunk_document()` and call a new helper `dkb.add_local_document_chunks(file_path, chunks, content_type, language)` that handles paper_id assignment (`local:<sha1>` of the path), metadata fill (no DOI, no year), and embedding in one batch.
4. Publishes per-file progress events.
5. Updates KB chunk counts on completion.

**Uploaded file size limit:** Reuse FastAPI's default; we don't enforce additional limits here (deferred to a follow-up).

### 4.2 Server-side path entry

Add to `src/perspicacite/web/routers/kb.py`:

```python
@router.post("/api/kb/{name}/local-paths")
async def add_local_paths(
    name: str,
    request: AddLocalPathsRequest,  # {paths: list[str], recursive: bool = True}
) -> JSONResponse:
    """Accept absolute server-side paths, dispatch ingest worker, return {job_id, sse_url}.
    Returns 503 if local_docs.allowed_roots is unset."""
```

**Path validation** (in `integrations/local_docs.py`):

```python
def validate_local_path(raw_path: str, *, allowed_roots: list[Path]) -> Path:
    """Raise ValueError unless raw_path is absolute, has no '..' component,
    resolves under one of allowed_roots, and exists.
    Returns the resolved Path."""
```

- If `allowed_roots` is empty/unset, the function raises with a 503-flavored marker; the endpoint maps that to HTTP 503.
- The MCP tool calls the same validator. If `allowed_roots` is empty, the tool refuses every call with an explanatory error.
- Recursive directory expansion is done in the worker after validation, but every expanded file is re-validated to stay under `allowed_roots` (covers symlink escapes).

### 4.3 CLI subcommand

Add to `src/perspicacite/cli.py`:

```bash
uv run perspicacite ingest-local --kb <name> --path <p> [--path <p2> ...] [--recursive]
```

This calls the same worker directly (no server needed). Path validation is skipped (CLI = local trust boundary, like the existing `screen-papers` and `pubmed-search` subcommands).

### 4.4 MCP tool

Add to `src/perspicacite/mcp/server.py` (count 12 → 13):

```python
@mcp.tool()
async def ingest_local_documents(
    kb_name: str,
    paths: list[str],
    recursive: bool = True,
) -> dict:
    """Ingest local files or directories into a KB. Files must live under
    one of the server's local_docs.allowed_roots. Returns job summary when done."""
```

Tool refuses when `allowed_roots` is unset/empty.

### 4.5 UI

In the KB detail panel (`static/js/kb.js`, `templates/index.html`):

- A drag-and-drop zone labeled "Drop files here or click to choose".
- Posts to `/api/kb/{name}/local-files` with multipart.
- Subscribes to the returned SSE job and shows per-file progress.

### 4.6 Tests

- `tests/unit/test_local_docs_validate.py` — `validate_local_path` rejects relative paths, `..`, paths outside allowed roots; accepts valid paths under a root.
- `tests/unit/test_local_docs_worker_pdf.py` — worker routes PDFs through `pdf_parser`.
- `tests/unit/test_local_docs_worker_markdown.py` — worker routes markdown through markdown chunker, produces `DocumentChunk`s with `heading_path` metadata.
- `tests/unit/test_local_docs_worker_code.py` — worker routes `.py` through `chunk_document(language="python")`, produces chunks tagged `language="python"`.
- `tests/unit/test_local_docs_router.py` — `/api/kb/{name}/local-files` accepts multipart; `/api/kb/{name}/local-paths` returns 503 with no allowed_roots, 200 with a valid root.
- `tests/unit/test_local_docs_mcp_tool.py` — MCP `ingest_local_documents` refuses without `allowed_roots`, works with one.
- `tests/unit/test_local_docs_cli.py` — CLI subcommand calls worker correctly.

---

## 5. Content-type-aware chunking (P3) — design

### 5.1 New module: `pipeline/chunking_dispatch.py`

```python
def infer_content_type(path: Path) -> tuple[str, str | None]:
    """Return (content_type, language_or_None).
    content_type ∈ {"pdf", "markdown", "code", "text"}.
    For 'code', language is one of: python, javascript, typescript, go, rust, java,
    cpp, ruby, swift, kotlin, csharp. (Extends as needed.)"""

async def chunk_document(
    text: str,
    paper: Paper,
    *,
    content_type: str,
    language: str | None,
    config: KnowledgeBaseConfig,
) -> list[DocumentChunk]:
    """Dispatch:
        - markdown  -> _chunk_markdown(text, paper, config)
        - code      -> _chunk_code(text, paper, config, language=language)
        - text/pdf  -> existing chunk_text(text, paper, config) (token splitter)
    """
```

The two new helpers:

```python
def _chunk_markdown(text: str, paper: Paper, config) -> list[DocumentChunk]:
    """Split on '^#{1,6} ' headings (regex), build a heading-path stack,
    emit chunks each tagged with heading_path (e.g., ['Setup', 'Install']).
    Code fences (```) kept atomic — never split across chunks.
    Final chunk size still bounded by config.chunk_size with config.chunk_overlap."""

def _chunk_code(text: str, paper: Paper, config, *, language: str) -> list[DocumentChunk]:
    """Use langchain_text_splitters.RecursiveCharacterTextSplitter.from_language(
        Language[language.upper()],
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    ).
    Tag each chunk with language=<language>."""
```

`langchain-text-splitters` (pure-Python, no model weights, MIT, transitively-light) goes into `pyproject.toml`. **Not** `langchain` itself.

### 5.2 New `DocumentChunk` fields

Extend `src/perspicacite/models/documents.py::ChunkMetadata` with optional fields (all default `None`):

```python
content_type: Optional[str] = None       # "pdf" | "markdown" | "code" | "text"
language: Optional[str] = None           # python | js | ... | None
heading_path: Optional[list[str]] = None # markdown heading stack
source_file_path: Optional[str] = None   # for local docs only; absolute path
```

Pydantic with `model_config = {"frozen": True}` doesn't preclude adding optional fields with defaults — existing callers still work.

### 5.3 New `PaperSource.LOCAL`

Extend `src/perspicacite/models/papers.py::PaperSource`:

```python
LOCAL = "local"
```

Used by local-doc ingest. ChromaDB metadata writes serialize this enum as `"local"`.

### 5.4 Config

Extend `KnowledgeBaseConfig` (Pydantic model in `config/schema.py`):

```python
markdown_heading_aware: bool = True
code_language_aware: bool = True
```

Default `True`. Set both `False` to fully fall back to the existing token chunker.

Add a new optional `LocalDocsConfig`:

```python
class LocalDocsConfig(BaseModel):
    allowed_roots: list[Path] = Field(default_factory=list)
    # Note: empty list = local-docs path endpoint and MCP tool refuse all calls.
```

And wire it on `Config`:

```python
local_docs: LocalDocsConfig = Field(default_factory=LocalDocsConfig)
```

Documented in `config.example.yml`.

### 5.5 Where dispatch is used

- Local-doc ingest worker — exclusively.
- Existing paper-ingest paths (BibTeX, DOIs, etc.) **continue to use `AdvancedChunker` / `chunk_text`**. No behavior change there.
- The dispatch helper is therefore additive only; failures fall back to the existing token chunker.

### 5.6 Tests

- `tests/unit/test_chunking_dispatch_infer.py` — `infer_content_type` for every extension we claim to support.
- `tests/unit/test_chunking_markdown.py` — heading stack is correctly assembled; code fences stay atomic; `heading_path` is on every chunk.
- `tests/unit/test_chunking_code_python.py` — language-aware splitter is invoked; chunks tagged `language="python"`; `content_type="code"`.
- `tests/unit/test_chunking_code_ts.py` — same shape, `language="typescript"`.
- `tests/unit/test_chunking_text_fallback.py` — `.txt` routes through the existing chunker.

---

## 6. Cross-cutting: provenance, sources, manual QA

### 6.1 Provenance

The provenance subsystem (cycle 2) records LLM calls and tool inputs into the JSONL sidecar via the `ProvenanceCollector` contextvar. We make the multi-KB and local-doc paths visible to it:

- `MultiKBRetriever.search` / `query_chunks_across_collections` / `get_chunks_by_paper_ids_across` emit a `tool_call` provenance record per fan-out call.
- Local-doc ingest worker emits a `tool_call` with `tool="ingest_local_documents"`, `inputs={kb, paths}`, `outputs={added, skipped}`.
- Zotero ingest worker emits a `tool_call` with `tool="build_kbs_from_zotero"`, `inputs={collections}`, `outputs={kb_summaries}`.

No new provenance schema fields needed.

### 6.2 SourceReference

`SourceReference` already has `kb_name`. We additionally surface `content_type` and `language` in the chat-source-card metadata (for UI display only; not in the schema, just passed via the existing `metadata` dict on the model where present).

### 6.3 MANUAL_QA.md updates

New sections appended at the bottom (preserving existing content):

- **Multi-KB chat across all six modes** — for each mode, select two KBs, run a representative query, verify sources tag both KBs.
- **Zotero plan and progress** — happy path through the modal, including renaming a target KB and excluding a collection.
- **Local-doc drag-and-drop** — drag a PDF, a markdown file, a Python file; verify per-file progress and that chunks land in the KB.
- **Language tags in provenance** — open a conversation that retrieved a code chunk; verify the provenance sidecar JSONL row carries `language` and `content_type`.

---

## 7. Implementation order and phasing

We'll execute in three phases, all merged to `main` per-task.

**Phase 1 — Multi-KB across the four modes** (~7 tasks, ~1500 LoC):
1. Add `query_chunks_across_collections` + `get_chunks_by_paper_ids_across` helpers + tests.
2. Wire `advanced.py` to fan out + compat-check + kb_name tagging + tests.
3. Wire `profound.py` (incl. two-pass enrichment with paper_id → kb_name map) + tests.
4. Wire `literature_survey.py` (API-contract propagation only) + tests.
5. Wire `agentic/orchestrator.py` KB_SEARCH step + tests.
6. Re-verify `generate_report` / `search_knowledge_base` MCP tools pass `kb_names` through unchanged + tests.
7. Manual-QA section appended.

**Phase 2 — Zotero-as-source-of-KB** (~9 tasks, ~1500 LoC):
1. Extend `ZoteroClient` with read methods + `_html_to_text` + tests.
2. New `integrations/zotero_ingest.py` plan builder + tests.
3. New `integrations/zotero_ingest.py` worker + tests.
4. New router `web/routers/zotero_ingest.py` `/plan` + tests.
5. New router `/build-kbs/async` + tests.
6. New MCP tool `build_kbs_from_zotero` (12 tools) + tests.
7. UI: modal + button + SSE wiring + DOM-shape test.
8. `MANUAL_QA.md` section.
9. `config.example.yml` docs.

**Phase 3 — Local docs + chunking dispatch** (~9 tasks, ~1300 LoC):
1. Add `PaperSource.LOCAL` + extend `ChunkMetadata` fields + tests.
2. Add `LocalDocsConfig` + extend `KnowledgeBaseConfig` flags + tests.
3. Add `langchain-text-splitters` dependency.
4. New `pipeline/chunking_dispatch.py` (infer + markdown + code) + tests.
5. New `integrations/local_docs.py` worker (path validate + dispatch + write chunks) + tests.
6. New routers `/api/kb/{name}/local-files` and `/api/kb/{name}/local-paths` + tests.
7. New MCP tool `ingest_local_documents` (13 tools) + tests.
8. New CLI subcommand `ingest-local` + tests.
9. UI: drag-and-drop + SSE wiring + DOM-shape test. `MANUAL_QA.md` section. `config.example.yml` docs.

**Final wrap (1 task):** final code review of the full diff vs. spec; fix or document any deviations.

Total: **~26 tasks**, **~4,300 LoC**.

---

## 8. Definition of done

- Every section in this spec maps to one or more checked tasks in the plan.
- `uv run pytest tests/unit/ -m "not live"` green from the first task to the last.
- Every new public function has at least one unit test.
- `config.example.yml` and `MANUAL_QA.md` updated within the same phase as the feature lands.
- `MEMORY.md` index updated only if the cycle uncovers durable workflow / preference signals (none anticipated).
- No new ruff or mypy errors on touched lines (existing backlog explicitly out of scope per user constraint).
- Tool count in `get_info()` reflects 13 tools after Phase 3.

---

## 9. Open questions

None — all design questions were resolved during brainstorming (Sections 1–6 of the brainstorm transcript), including:

- Per-collection vs single-KB import → per-top-level-collection (rolls up subcollections).
- Attachments + notes inclusion → both included; HTML-stripped to plain text.
- Local-docs trust → absolute paths only, `..` rejected, MCP gated by `allowed_roots`, web upload unaffected.
- Markdown/code chunking → `langchain-text-splitters` (pure Python, no model weights).
- Multi-KB across `literature_survey` → API-contract propagation only (no real retrieval surface there).
