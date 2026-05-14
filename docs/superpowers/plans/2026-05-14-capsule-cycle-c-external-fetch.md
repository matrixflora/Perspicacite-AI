# Capsule Cycle C — External Resources V2 (fetch on demand) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make external resources mined into `resources.json` (Cycle A V1) **fetchable on demand** — GitHub READMEs/scripts/notebooks, Zenodo metadata (and small text/script files with hard caps), Crossref/Unpaywall/PubMed lookups — and route fetched text into the existing KB ingest pipeline as `is_external=True, parent_paper_id=<paper>` chunks so chat answers can cite repo files alongside paper content.

**Architecture:** Vendor ASB's `enrichment.py` helpers (httpx-adapted), wire them through a new `fetch_paper_resources(paper, kinds, ingest, ...)` entry point used by MCP, CLI, and a new web route. Fetched text-like files are written under `<capsule>/external/{kind}/...` and the same files are routed through `ingest_local_documents` (notebooks pre-processed via `_strip_notebook_outputs`). Size caps + extension allowlist gate Zenodo to prevent dataset blob downloads.

**Tech Stack:** Python 3.11+, httpx (already a dependency, replacing ASB's stdlib urllib), pydantic v2, FastAPI, Click CLI.

---

## Pre-flight notes for implementer subagents

- ASB enrichment lives at `~/git/AgenticScienceBuilder/src/agentic_science_builder/enrichment.py` (1057 lines, SHA `a10eced` at time of writing — verify before vendoring). Use **stdlib urllib → httpx** adapter for V2.
- Cycle A V1 mining already populates `resources.json` per capsule via `pipeline/capsule_builder.py` (`mine_accessions`, `extract_github_repos`, `extract_zenodo_record_ids`, `extract_doi_candidates`).
- The Cycle B `CapsuleReader.ingest_capsule` flag `is_external` and chunk metadata fields `parent_paper_id` / `is_external` were added in Cycle A's `ChunkMetadata` (`src/perspicacite/models/documents.py:34-38`).
- Test memory: full pytest suite OOMs (chromadb/torch). **Run only the new test file per task** with `.venv/bin/pytest -xvs tests/unit/<new_file>.py --noconftest -p no:cacheprovider`. Skip per-task verification when chunking/embedding imports trigger the slowdown; rely on code review + smoke imports + targeted lightweight unit tests.
- Vendored helpers MUST carry a `Synced from AgenticScienceBuilder @ <sha>; keep API in sync.` header line (Cycle A precedent).
- Commit per task. Direct to `main` via fast-forward of `claude/capsule-cycle-a` at the end.
- LLM calls: NONE. This cycle is deterministic — mining + HTTP + chunking only.

---

## File Structure

**New files:**
- `src/perspicacite/pipeline/external/cache.py` — httpx-adapted cache layer (`_cache_path`, `_cache_load`, `_cache_store`)
- `src/perspicacite/pipeline/external/http.py` — httpx wrappers (`http_get_json`, `http_get_bytes`, `http_get_text`) with timeout + retry + cache
- `src/perspicacite/pipeline/external/notebooks.py` — `strip_notebook_outputs`
- `src/perspicacite/pipeline/external/fetch_github.py` — `fetch_github_repo`, `fetch_github_docs`
- `src/perspicacite/pipeline/external/fetch_zenodo.py` — `fetch_zenodo` (metadata-only by default; opt-in small-file fetch with caps)
- `src/perspicacite/pipeline/external/fetch_doi.py` — `fetch_crossref`, `fetch_unpaywall`, `fetch_pubmed`, `fetch_pmcid_for_doi`
- `src/perspicacite/pipeline/external/fetch_orchestrator.py` — `fetch_paper_resources(paper, kinds, capsule_dir, *, app_state, registry, job_id, ingest=False)`
- Test files (one per new module above)

**Modified files:**
- `src/perspicacite/config/schema.py` — add `ExternalResourcesConfig`
- `src/perspicacite/integrations/local_docs.py` — accept `parent_paper_id` + `is_external` annotation when called from the external-ingest path
- `src/perspicacite/web/routers/kb.py` — `POST /api/kb/{name}/paper/{paper_id:path}/fetch-resources` (JobRegistry SSE)
- `src/perspicacite/mcp/server.py` — `fetch_paper_resources` MCP tool
- `src/perspicacite/cli.py` — `fetch-resources` Click subcommand
- `templates/index.html`, `static/js/kb.js`, `static/css/kb.css` — per-resource "Fetch & Ingest" button + per-paper "Fetch all" (JobRegistry stream)
- `MANUAL_QA.md` — Cycle C section
- `config.example.yml` — `external_resources` block

---

### Task 1: ExternalResourcesConfig in config schema

**Files:**
- Modify: `src/perspicacite/config/schema.py` (after `MultimodalConfig`)
- Test: `tests/unit/test_external_resources_config.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_external_resources_config.py
from perspicacite.config.schema import Config, ExternalResourcesConfig


def test_defaults():
    c = ExternalResourcesConfig()
    assert c.mine is True
    assert c.fetch_on_demand is True
    assert c.cache_ttl_days == 30
    assert c.zenodo_max_bytes_per_file == 500_000
    assert c.zenodo_max_bytes_per_record == 5_000_000
    assert ".py" in c.text_file_extensions
    assert ".R" in c.text_file_extensions
    assert ".jl" in c.text_file_extensions
    assert ".ipynb" in c.text_file_extensions


def test_config_has_external_resources():
    cfg = Config()
    assert isinstance(cfg.external_resources, ExternalResourcesConfig)
```

- [ ] **Step 2: Implement**

```python
class ExternalResourcesConfig(BaseModel):
    """V1 mining + V2 fetch-on-demand for paper-referenced external resources."""

    mine: bool = True                      # V1 — always-on (Cycle A wires this)
    fetch_on_demand: bool = True           # V2 — gated by user/MCP action
    cache_dir: Path = Path("./data/cache")
    cache_ttl_days: int = 30
    zenodo_max_bytes_per_file: int = 500_000
    zenodo_max_bytes_per_record: int = 5_000_000
    text_file_extensions: list[str] = Field(default_factory=lambda: [
        ".md", ".rst", ".txt",
        ".py", ".R", ".r", ".jl",
        ".ipynb",
        ".yml", ".yaml", ".toml", ".json", ".csv",
    ])
```

Attach to `Config`:

```python
    external_resources: ExternalResourcesConfig = Field(default_factory=ExternalResourcesConfig)
```

- [ ] **Step 3: Verify + commit**

```
git add src/perspicacite/config/schema.py tests/unit/test_external_resources_config.py
git commit -m "feat(config): add ExternalResourcesConfig (cache, Zenodo caps, ext allowlist)"
```

---

### Task 2: Cache layer (httpx-adapted)

**Files:**
- Create: `src/perspicacite/pipeline/external/cache.py`
- Test: `tests/unit/test_external_cache.py`

Mirror ASB's cache helpers but adapt to async-safe filesystem layout. Layout: `<cache_dir>/<api>/<query_hash>.json`. TTL on read.

- [ ] **Step 1: Tests** — write tests for `_cache_path`, `_cache_load` (returns None for missing + expired), `_cache_store` (atomic write).

- [ ] **Step 2: Implement** with header `"""Synced from AgenticScienceBuilder @ <sha>; httpx-adapted, keep API in sync."""`.

Public surface:

```python
def cache_path(cache_dir: Path, api: str, query: str) -> Path: ...
def cache_load(path: Path, *, ttl_seconds: int) -> dict | None: ...
def cache_store(path: Path, payload: dict) -> None: ...
```

- [ ] **Step 3: Commit** — `feat(external): cache layer (httpx-adapted from ASB)`

---

### Task 3: HTTP helpers (httpx wrappers)

**Files:**
- Create: `src/perspicacite/pipeline/external/http.py`
- Test: `tests/unit/test_external_http.py`

Async helpers built on `httpx.AsyncClient`. Each accepts `cache_dir, api_name, query, ttl_seconds` and routes through the cache layer.

Public surface:

```python
async def http_get_json(url, *, cache_dir, api, query, ttl_seconds=30*86400,
                        headers=None, timeout=30.0, max_retries=3) -> dict | list | None: ...
async def http_get_bytes(url, *, cache_dir, api, query, ttl_seconds=30*86400,
                         headers=None, timeout=60.0, max_retries=3,
                         max_bytes=None) -> bytes | None: ...
async def http_get_text(url, *, cache_dir, api, query, ttl_seconds=30*86400,
                        headers=None, timeout=30.0, max_retries=3,
                        max_bytes=None) -> str | None: ...
```

- Cache hit → return cached.
- Cache miss → fetch with `httpx.AsyncClient`, exponential-backoff retries, write cache, return.
- `max_bytes` cap → return `None` if response exceeds (no partial cache write).
- Network errors → log warning + return `None` (don't raise; callers tolerate misses).

Tests use `respx` (already a dep) to mock httpx responses.

- [ ] **Step 1-3: TDD + commit** — `feat(external): async httpx wrappers with cache + retries`

---

### Task 4: Notebook output stripper

**Files:**
- Create: `src/perspicacite/pipeline/external/notebooks.py`
- Test: `tests/unit/test_strip_notebook_outputs.py`

Mirror ASB's `_strip_notebook_outputs(raw: str) -> str`. Strip `outputs`, `execution_count`, and any embedded image data from each code cell. Return JSON string (re-serialized) or markdown-flattened code-fence form — match ASB exactly.

- [ ] **Step 1-3: TDD + commit** — `feat(external): strip_notebook_outputs vendored from ASB`

---

### Task 5: fetch_github_docs

**Files:**
- Create: `src/perspicacite/pipeline/external/fetch_github.py`
- Test: `tests/unit/test_fetch_github.py`

Public surface:

```python
async def fetch_github_repo(owner: str, repo: str, *,
    cache_dir: Path, ttl_seconds: int, timeout: float = 30.0,
) -> dict | None: ...

async def fetch_github_docs(owner: str, repo: str, *,
    capsule_dir: Path, cache_dir: Path,
    text_file_extensions: list[str], max_bytes_per_file: int = 1_000_000,
    extra_docs: bool = True,
    ttl_seconds: int = 30 * 86400,
) -> dict | None:
    """Fetch README + docs + scripts + notebooks + tree into
    capsule_dir/external/github/<owner>__<repo>/.

    Returns a dict summary {"files_fetched": int, "bytes_fetched": int,
    "tree": [...]} and writes `.extra_fetched` sentinel to prevent
    duplicate calls."""
```

Mirror ASB. Wire `text_file_extensions` to the config's allowlist (`.R`/`.r`/`.jl` widened in Perspicacité). Strip notebook outputs via `notebooks.strip_notebook_outputs`.

- [ ] **Step 1-3: TDD with respx + commit** — `feat(external): fetch_github_docs (README, docs, scripts, notebooks, tree)`

---

### Task 6: fetch_zenodo

**Files:**
- Create: `src/perspicacite/pipeline/external/fetch_zenodo.py`
- Test: `tests/unit/test_fetch_zenodo.py`

Public surface:

```python
async def fetch_zenodo(record_id: str, *,
    capsule_dir: Path, cache_dir: Path,
    text_file_extensions: list[str],
    max_bytes_per_file: int = 500_000,
    max_bytes_per_record: int = 5_000_000,
    metadata_only: bool = True,
    ttl_seconds: int = 30 * 86400,
) -> dict | None: ...
```

- `metadata_only=True` (default): fetch only `https://zenodo.org/api/records/<id>` JSON; write to `capsule/external/zenodo/<id>.json`.
- `metadata_only=False`: also fetch small text/code files from `record.files` if their extension is in `text_file_extensions` and they're under `max_bytes_per_file`, and the cumulative bytes stay under `max_bytes_per_record`. Skip archives (`.zip`, `.tar.gz`, etc.).
- Never extract archives.

- [ ] **Step 1-3: TDD with respx + commit** — `feat(external): fetch_zenodo (metadata + optional small files with caps)`

---

### Task 7: fetch_crossref + fetch_unpaywall

**Files:**
- Create: `src/perspicacite/pipeline/external/fetch_doi.py` (start)
- Test: `tests/unit/test_fetch_doi_lookups.py`

```python
async def fetch_crossref(doi: str, *, cache_dir: Path, ttl_seconds: int = 30*86400) -> dict | None: ...
async def fetch_unpaywall(doi: str, *, email: str | None, cache_dir: Path, ttl_seconds: int = 30*86400) -> dict | None: ...
```

Write to `capsule/external/crossref/<doi_slug>.json` and `capsule/external/unpaywall/<doi_slug>.json` respectively. `doi_slug = doi.replace("/", "_")`.

- [ ] **Step 1-3: TDD + commit** — `feat(external): fetch_crossref + fetch_unpaywall`

---

### Task 8: fetch_pubmed + fetch_pmcid_for_doi

**Files:**
- Modify: `src/perspicacite/pipeline/external/fetch_doi.py` (append)
- Test: `tests/unit/test_fetch_pubmed.py`

```python
async def fetch_pubmed(pmid: str, *, cache_dir: Path, ttl_seconds: int = 30*86400) -> dict | None: ...
async def fetch_pmcid_for_doi(doi: str, *, cache_dir: Path, ttl_seconds: int = 30*86400) -> str | None: ...
```

Reuse the XML-abstract parser from ASB (`_parse_pubmed_abstract`). Write to `capsule/external/pubmed/<pmid>.json` containing `{abstract, ...}`.

- [ ] **Step 1-3: TDD + commit** — `feat(external): fetch_pubmed + fetch_pmcid_for_doi`

---

### Task 9: Fetch orchestrator

**Files:**
- Create: `src/perspicacite/pipeline/external/fetch_orchestrator.py`
- Test: `tests/unit/test_fetch_orchestrator.py`

```python
async def fetch_paper_resources(
    *,
    paper: Paper,
    capsule_dir: Path,
    kinds: list[str] | None,            # e.g., ["github", "zenodo"]; None = all in resources.json
    app_state,
    registry,
    job_id: str,
    ingest: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Resolve resources.json → dispatch fetch_* helpers → optionally ingest.

    Returns {"github": N, "zenodo": M, "doi": K, "ingested_chunks": C}.
    Emits per-resource progress events via registry.publish.
    """
```

Reads `<capsule>/resources.json`. For each resource matching `kinds`, dispatches the right `fetch_*` helper with the config-driven caps + allowlist. After fetch completes, if `ingest=True`, builds a list of fetched text-like file paths and routes them through `ingest_local_documents` (Task 10) tagging chunks with `parent_paper_id=paper.id, is_external=True`.

- [ ] **Step 1-3: TDD + commit** — `feat(external): fetch_paper_resources orchestrator`

---

### Task 10: local_docs external annotation

**Files:**
- Modify: `src/perspicacite/integrations/local_docs.py`
- Test: `tests/unit/test_local_docs_external_annotation.py`

Add `external_metadata: dict[str, str] | None = None` parameter to `ingest_local_documents`. When set, every chunk written gains:
- `metadata.parent_paper_id = external_metadata["parent_paper_id"]`
- `metadata.is_external = True`
- `metadata.resource_refs = [external_metadata.get("resource_id"), ...]` (if provided)

Notebooks (`.ipynb`) are pre-processed via `strip_notebook_outputs` before chunking.

- [ ] **Step 1-3: TDD + commit** — `feat(local_docs): external_metadata annotation for fetched-resource ingest`

---

### Task 11: MCP tool — fetch_paper_resources

**Files:**
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_mcp_fetch_paper_resources.py`

```python
@mcp.tool
async def fetch_paper_resources(
    kb_name: str, paper_id: str,
    kinds: list[str] | None = None,
    ingest: bool = True,
    force: bool = False,
) -> dict[str, Any]: ...
```

Resolves the paper via `vector_store.list_paper_metadata` + `resolve_paper_from_metadata`, locates the capsule dir, delegates to `fetch_paper_resources` orchestrator. Add to `_TOOL_NAMES`.

- [ ] **Step 1-3: TDD + commit** — `feat(mcp): fetch_paper_resources tool`

---

### Task 12: CLI — perspicacite fetch-resources

**Files:**
- Modify: `src/perspicacite/cli.py`
- Test: `tests/unit/test_cli_fetch_resources.py`

```
perspicacite fetch-resources --kb <name> --paper <doi-or-id> [--ingest] [--include github,zenodo,doi] [--force]
```

Calls the same orchestrator. Prints per-resource progress lines + final summary.

- [ ] **Step 1-3: TDD + commit** — `feat(cli): fetch-resources subcommand`

---

### Task 13: Web route — POST /api/kb/{name}/paper/{paper_id:path}/fetch-resources

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_kb_router_fetch_resources.py`

JobRegistry SSE pattern (same as Cycle A `build-capsules`). Body: `{"kinds": ["github", "zenodo"], "ingest": true, "force": false}`. Returns `{"job_id": "..."}`. The job streams per-resource events.

- [ ] **Step 1-3: TDD + commit** — `feat(web/kb): fetch-resources endpoint (JobRegistry SSE)`

---

### Task 14: UI — Fetch & Ingest buttons

**Files:**
- Modify: `templates/index.html`, `static/js/kb.js`, `static/css/kb.css`
- Test: `tests/unit/test_kb_ui_fetch_resources_button.py` (HTML/JS surface only)

Per-paper button "Fetch external resources" inside the paper detail panel. On click → POST to the new endpoint → render the SSE stream events into a fetch-progress pane.

- [ ] **Step 1-3: commit** — `feat(web/ui): fetch external resources button + SSE progress`

---

### Task 15: docs/superpowers/external_resources.md

**Files:**
- Create: `docs/superpowers/external_resources.md`

Document: V1 mining (Cycle A) vs V2 fetch (Cycle C); supported `kinds`; Zenodo size caps; extension allowlist; on-disk layout under `<capsule>/external/`; cache layout under `<config.external_resources.cache_dir>/<api>/`; how fetched-text chunks are tagged (`is_external=True, parent_paper_id, resource_refs`).

- [ ] Commit — `docs: Cycle C external-resources V2 fetch reference`

---

### Task 16: MANUAL_QA additions

**Files:**
- Modify: `MANUAL_QA.md`

Append "Capsule Cycle C — external resources V2" section: ingest a paper with mined GitHub repo → trigger fetch → confirm `external/github/<owner>__<repo>/README.md` exists, notebooks have stripped outputs, chunks appear in KB with `is_external=True`, RAG answer cites repo-file content.

- [ ] Commit — `docs(qa): MANUAL_QA — Cycle C external-resources checklist`

---

### Task 17: config.example.yml block

**Files:**
- Modify: `config.example.yml`

Append `external_resources:` block reflecting `ExternalResourcesConfig` defaults.

- [ ] Commit — `docs(config): add external_resources block to config.example.yml`

---

### Task 18: Final integration walk-through (manual)

- [ ] Ingest a paper with a GitHub URL + a Zenodo record in its text. Confirm `resources.json` is populated.
- [ ] CLI `perspicacite fetch-resources --kb <name> --paper <id> --ingest --include github,zenodo`. Confirm files appear under `<capsule>/external/...`, notebook outputs stripped, Zenodo data files NOT downloaded (only metadata + small text files within caps).
- [ ] Query the KB on a topic only mentioned in the repo's README. Confirm the answer cites the repo with `is_external=True, parent_paper_id=<paper>` provenance.
- [ ] Switch to a paper without resources → fetch returns `{}` cleanly.
- [ ] UI fetch button works; SSE progress renders.

If anything fails, fix and re-verify. No commit unless fixes were needed.

---

### Task 19: Final code review + merge

- [ ] Final code-review pass over all Cycle C commits.
- [ ] Address any issues.
- [ ] `git checkout main` (in the primary worktree) + `git merge --ff-only claude/capsule-cycle-a` to land Cycle C on main.

---

## Risks & mitigations

- **httpx adaptation of stdlib urllib** — ASB uses synchronous `urllib`; we'll use `httpx.AsyncClient`. Error semantics differ (httpx raises specific exception types vs urllib's `URLError`). Mitigation: catch `httpx.HTTPError` + `OSError` + `asyncio.TimeoutError`; return `None` on miss; log warning. Don't propagate.
- **Zenodo data exfil risk** — the spec is firm: metadata only by default; opt-in small text/script files only with hard byte caps; never extract archives. Mitigation: enforce `max_bytes_per_file` and `max_bytes_per_record` at the HTTP layer (`max_bytes` arg of `http_get_bytes`) AND at the orchestrator level (running total). Extension allowlist is checked before fetch.
- **GitHub rate limits** — unauthenticated GitHub API is 60 req/hr per IP. Mitigation: cache aggressively (30-day TTL by default); accept `GITHUB_TOKEN` env via `httpx` `Authorization: Bearer ...` header when set; user can `export GITHUB_TOKEN=...` to lift the limit. Document in MANUAL_QA.
- **Pytest memory pressure** — same as Cycles A and B. Use `.venv/bin/pytest --noconftest -p no:cacheprovider` for the new test files. Skip per-task verification when chunking imports trigger the slowdown; rely on code review + smoke imports.
- **Vendor drift** — ASB's `enrichment.py` will evolve. Mitigation: vendored modules carry `Synced from AgenticScienceBuilder @ <sha>` header; future-sync follows Cycle A's precedent of re-vendoring a clean copy.
- **Idempotency** — `.extra_fetched` sentinel in GitHub repo dirs prevents duplicate calls. `force=True` bypasses. Mirrors ASB.
