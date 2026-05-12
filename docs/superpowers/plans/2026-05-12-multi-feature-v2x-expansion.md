# Perspicacité v2.x Multi-Feature Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an additive, six-phase batch of roadmap features — content-pipeline coverage (bioRxiv, Crossref), KB-building tools (`screen_papers`, PubMed adapter, batch DOI ingestion), RAG depth (contradiction mode, recency-weighted retrieval), multi-KB query, and web UI/observability (KB stats, paper detail, conversation search/export) — without any risky architectural refactor.

**Architecture:** Each feature is a new module / new endpoint / new MCP tool / new RAG mode / new UI panel that slots into existing extension points. New content sources append to `retrieve_paper_content()`'s priority chain; new RAG mode extends `BaseRAGMode` and registers in `RAGEngine._modes`; new request options are optional fields on `RAGRequest` that are no-ops when unset; new MCP tools follow the `_json_ok`/`_json_error`/`_require_state` pattern; new endpoints follow the `app_state` router pattern.

**Tech Stack:** Python 3.12, FastAPI, fastmcp, ChromaDB, aiosqlite, LiteLLM, httpx, Biopython (Entrez), rank-bm25, pytest + respx (HTTP mocking), Click (CLI), structlog, mypy, ruff. Package manager: `uv`.

**Authoritative spec:** `docs/superpowers/specs/2026-05-12-multi-feature-v2x-expansion-design.md` — read it before starting; it has the rationale, out-of-scope list, and risks. This plan is the executable breakdown.

---

## Global Rules (apply to every task)

- **Read the relevant `docs/rules/*.md`** before touching a subsystem: `rag_development.md` (RAG modes), `content_pipeline.md` (download pipeline), `api_web.md` (routers/SSE/static), `testing.md` (markers/fixtures).
- **Logging:** in `src/perspicacite/`, use `from perspicacite.logging import get_logger`; `logger.info("event_name", key=value)` — never f-strings in log calls. (Routers under `web/` currently use stdlib `logging` — match the file you're editing.)
- **LLM calls:** `await llm.complete(messages=[...])` via `AsyncLLMClient`.
- **Streaming:** RAG modes yield `StreamEvent`; errors are `StreamEvent(event="error", data=...)`, never raised out of `execute_stream`.
- **Tests:** new `tests/unit/` tests must not touch the network. Any external HTTP/Entrez call is mocked (use `respx` for httpx, monkeypatch for `Bio.Entrez`). Live tests stay in top-level `tests/test_*.py` behind the `live` marker.
- **Per-task done bar:** `uv run pytest tests/unit/ -m "not live" -q` stays green with no new failures (baseline = 470 passing as of 2026-05-12). Run `uv run ruff format <files-you-touched>`. The repo has a large pre-existing lint/type backlog (`ruff check src/ tests/` ≈ 1769 errors, `mypy src/` ≈ 310 errors) — do **not** attempt to fix that backlog; just ensure your new/modified code introduces no obvious new ruff errors and no new mypy errors *in the files you touched* (`uv run ruff check <your-files>`, `uv run mypy <your-files>`). Commit after each task with a conventional-commit message.
- **Per-phase done bar:** append an entry to `AGENT_LOG.md`; move completed `ROADMAP.md` items to the "Completed (archive)" section with a `(2026-05)` tag; if on a branch, open a PR for the phase.
- **Additive-only constraint:** if a task seems to require a refactor that changes the behavior of existing code paths (beyond the explicitly-allowed §5.3 ingest-helper extraction), STOP, leave a note in `AGENT_LOG.md`, and move to the next phase.
- **`respx` dependency:** added in Phase 0 Task 0.5. If a task before that needs HTTP mocking, use `monkeypatch` on `httpx.AsyncClient` instead.

---

# PHASE 0 — Foundations & Hygiene

## Task 0.1: Restore `config.example.yml`, remove stray `config (1).yml`

**Files:**
- Create: `config.example.yml` (repo root)
- Delete: `config (1).yml` (repo root)
- Reference (do not edit): `config.yml`, `.gitignore` (lines ~96-104 already ignore `config.yml`)

- [ ] **Step 1: Build `config.example.yml` from `config.yml` with secrets scrubbed**

Read `config.yml`. Copy it to `config.example.yml`. Replace any non-empty API keys / tokens / emails with placeholders, e.g.:
- `unpaywall_email: "you@example.com"`
- `wiley_tdm_token: ""`, `aaas_api_key: ""`, `rsc_api_key: ""`, `springer_api_key: ""`, `elsevier_api_key: ""`
- LLM `api_key:` fields → `""` (keys come from `.env`)
Keep all structure, comments, and default values. Add a top comment: `# Copy to config.yml and fill in. config.yml is git-ignored.`

- [ ] **Step 2: Delete the stray file**

```bash
rm "config (1).yml"
```

- [ ] **Step 3: Verify config still loads**

```bash
uv run python -c "from perspicacite.config.loader import load_config; load_config('config.example.yml'); print('ok')"
```
Expected: `ok` (if `load_config` requires a real key, that's fine — just confirm no schema/parse error; if it errors only on missing keys, that's acceptable for an example file).

- [ ] **Step 4: Commit**

```bash
git add config.example.yml ".gitignore" && git rm --cached "config (1).yml" 2>/dev/null; git add -A
git commit -m "chore: restore config.example.yml, drop stray 'config (1).yml'"
```
(If `config (1).yml` was never tracked, `git rm --cached` is a no-op — fine.)

---

## Task 0.2: Configurable reranker model

**Files:**
- Modify: `src/perspicacite/config/schema.py`
- Modify: `src/perspicacite/retrieval/reranker.py:24-36` (`CrossEncoderReranker.__init__`)
- Modify: wherever `CrossEncoderReranker(...)` is instantiated (grep: `rg "CrossEncoderReranker\(" src/`)
- Test: `tests/unit/test_config.py`, `tests/unit/test_reranker.py` (new)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_reranker.py`:
```python
from perspicacite.retrieval.reranker import CrossEncoderReranker


def test_reranker_uses_explicit_model_name():
    r = CrossEncoderReranker(model_name="my-org/custom-reranker")
    assert r.model_name == "my-org/custom-reranker"


def test_reranker_default_model_name():
    r = CrossEncoderReranker()
    assert r.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
```
In `tests/unit/test_config.py` add:
```python
def test_config_reranker_model_default(tmp_path):
    from perspicacite.config.loader import load_config
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("llm:\n  default_provider: deepseek\n")
    cfg = load_config(str(cfg_path))
    assert cfg.rag_modes.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"
```
(Adjust the minimal YAML to whatever `load_config` accepts; the point is the new field has a default.)

- [ ] **Step 2: Run, expect fail**

`uv run pytest tests/unit/test_reranker.py tests/unit/test_config.py::test_config_reranker_model_default -v`
Expected: FAIL (`reranker_model` attribute missing) — `test_reranker_*` should already pass.

- [ ] **Step 3: Add the config field**

In `src/perspicacite/config/schema.py`, on `RAGModesConfig` add:
```python
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="HuggingFace cross-encoder model used for reranking",
    )
```

- [ ] **Step 4: Thread it into the reranker instantiation**

Find where `CrossEncoderReranker(...)` is created (likely in a mode handler or `web/state.py` / `retrieval/__init__.py`). Pass `model_name=config.rag_modes.reranker_model`. If no config is in scope at that call site, pass it from the nearest place that has `config`. Do NOT change `CrossEncoderReranker.__init__`'s signature — it already accepts `model_name`.

- [ ] **Step 5: Run tests, expect pass**

`uv run pytest tests/unit/test_reranker.py tests/unit/test_config.py -v` → PASS
`uv run ruff check src/ tests/ && uv run mypy src/` → clean

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(config): make reranker model configurable via rag_modes.reranker_model"
```

---

## Task 0.3: Configurable agentic map-reduce cap

**Files:**
- Modify: `src/perspicacite/config/schema.py` (`RAGModeSettings` or the `agentic` block — pick `RAGModeSettings` so all modes can have it; default 8)
- Modify: `src/perspicacite/rag/agentic/orchestrator.py:26` (the `MAP_REDUCE_MAX_PAPERS = 8` constant) and its use at line ~1747
- Test: `tests/unit/test_config.py`, plus a small orchestrator unit test

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_config.py`:
```python
def test_config_map_reduce_max_papers_default(tmp_path):
    from perspicacite.config.loader import load_config
    cfg_path = tmp_path / "c.yml"
    cfg_path.write_text("llm:\n  default_provider: deepseek\n")
    cfg = load_config(str(cfg_path))
    assert cfg.rag_modes.agentic.map_reduce_max_papers == 8
```
In `tests/unit/test_agentic_phase1.py` (or a new `tests/unit/test_orchestrator_config.py`):
```python
def test_orchestrator_reads_map_reduce_cap_from_config():
    from perspicacite.config.schema import Config
    from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator  # adjust import
    cfg = Config()
    cfg.rag_modes.agentic.map_reduce_max_papers = 3
    # Construct the orchestrator the same way RAGEngine/AgenticRAGMode does; assert the
    # effective cap == 3. If the orchestrator computes the cap lazily, call the helper
    # that returns it. Keep the test light — no LLM, no network.
```
(If wiring the orchestrator standalone is heavy, instead assert via `AgenticRAGMode(cfg)` that the cap value it will use equals `cfg.rag_modes.agentic.map_reduce_max_papers`.)

- [ ] **Step 2: Run, expect fail**

`uv run pytest tests/unit/test_config.py::test_config_map_reduce_max_papers_default -v` → FAIL

- [ ] **Step 3: Add config field + use it**

`schema.py`, on `RAGModeSettings`:
```python
    map_reduce_max_papers: int = Field(default=8, ge=1, le=64)
```
`orchestrator.py`: keep `MAP_REDUCE_MAX_PAPERS = 8` as the fallback constant, but where the orchestrator slices `indexed = indexed[:MAP_REDUCE_MAX_PAPERS]`, change to use a cap resolved from config at construction time, e.g. in `__init__`: `self._map_reduce_cap = getattr(config.rag_modes.agentic, "map_reduce_max_papers", MAP_REDUCE_MAX_PAPERS)` and use `self._map_reduce_cap`. If the orchestrator doesn't currently hold `config`, pass it through from `AgenticRAGMode`.

- [ ] **Step 4: Run tests, expect pass**

`uv run pytest tests/unit/ -k "config or orchestrator or agentic" -v` → PASS; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(config): make agentic map_reduce_max_papers configurable (default 8)"
```

---

## Task 0.4: Hybrid BM25/vector weights as `RAGRequest` options

**Files:**
- Modify: `src/perspicacite/models/rag.py` (`RAGRequest`)
- Modify: `src/perspicacite/rag/engine.py` (pass weights into handlers — they already receive `request`)
- Modify: the mode handlers that build a hybrid retriever (grep `rg "vector_weight|bm25_weight|combine_scores|HybridRetriever|use_hybrid" src/perspicacite/rag/`) — when `request.bm25_weight`/`request.vector_weight` are set, use them instead of the config/LLM-derived defaults
- Test: `tests/unit/test_models.py`, `tests/unit/test_hybrid_module.py`

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_models.py`:
```python
def test_ragrequest_weight_fields_default_none():
    from perspicacite.models.rag import RAGRequest
    r = RAGRequest(query="x")
    assert r.bm25_weight is None and r.vector_weight is None
    r2 = RAGRequest(query="x", bm25_weight=0.7, vector_weight=0.3)
    assert r2.bm25_weight == 0.7 and r2.vector_weight == 0.3
```
In `tests/unit/test_hybrid_module.py` add a test that, given explicit weights, `combine_scores` (already exists) is invoked with them — or, if testing at the mode level is too heavy, just assert the resolution helper: write a tiny pure function `resolve_hybrid_weights(request, config) -> tuple[float, float]` in `src/perspicacite/retrieval/hybrid.py` and test it:
```python
def test_resolve_hybrid_weights_prefers_request():
    from perspicacite.retrieval.hybrid import resolve_hybrid_weights
    from perspicacite.models.rag import RAGRequest
    v, b = resolve_hybrid_weights(RAGRequest(query="x", vector_weight=0.8, bm25_weight=0.2), default=(0.5, 0.5))
    assert (v, b) == (0.8, 0.2)
    v, b = resolve_hybrid_weights(RAGRequest(query="x"), default=(0.5, 0.5))
    assert (v, b) == (0.5, 0.5)
```

- [ ] **Step 2: Run, expect fail**

`uv run pytest tests/unit/test_models.py -k weight tests/unit/test_hybrid_module.py -k resolve -v` → FAIL

- [ ] **Step 3: Implement**

`models/rag.py` — add to `RAGRequest`:
```python
    bm25_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    vector_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
```
`retrieval/hybrid.py` — add:
```python
def resolve_hybrid_weights(request, default: tuple[float, float] = (0.5, 0.5)) -> tuple[float, float]:
    """Return (vector_weight, bm25_weight): request overrides win, else `default`.

    `default` may come from config or determine_weights_with_llm(). If only one of
    request.vector_weight / request.bm25_weight is set, the other is its complement.
    """
    rv = getattr(request, "vector_weight", None)
    rb = getattr(request, "bm25_weight", None)
    if rv is None and rb is None:
        return default
    if rv is None:
        rv = max(0.0, 1.0 - (rb or 0.0))
    if rb is None:
        rb = max(0.0, 1.0 - (rv or 0.0))
    total = rv + rb
    if total <= 0:
        return default
    return rv / total, rb / total
```
In each mode handler that does hybrid retrieval, where it currently picks weights (config default or `determine_weights_with_llm`), wrap with `resolve_hybrid_weights(request, default=(those_weights))`. Keep `determine_weights_with_llm` as the source of `default` when LLM-weighting is on.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(rag): allow per-request BM25/vector hybrid weights"
```

---

## Task 0.5: Add `respx` dev dependency; verify `bibtexparser`

**Files:** `pyproject.toml`, `uv.lock`

- [ ] **Step 1:** Confirm `bibtexparser` is a runtime dep (`rg bibtexparser pyproject.toml`). If missing, add it under `[project] dependencies`. Add `respx` under the dev extra (the `[dependency-groups] dev` or `[project.optional-dependencies] dev` block — match what's there).
- [ ] **Step 2:** `uv sync --dev && uv lock`
- [ ] **Step 3:** `uv run python -c "import respx, bibtexparser; print('ok')"` → `ok`
- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock && git commit -m "chore: add respx (dev) for HTTP-mocked tests; ensure bibtexparser dep"
```

---

## Task 0.6: Phase 0 wrap-up

- [ ] Append to `AGENT_LOG.md` a dated entry: "Phase 0 — config knobs (reranker_model, agentic.map_reduce_max_papers, RAGRequest hybrid weights), repo hygiene (config.example.yml), respx dep."
- [ ] In `ROADMAP.md`, tick "Reranker model configurability", "Increase MAP_REDUCE_MAX_PAPERS cap" (note: now *configurable*, not blindly raised), "LLM-tunable BM25/vector weights exposed in UI" → mark backend done, UI in Phase 5.
- [ ] In `config.example.yml`, document the new `rag_modes.reranker_model` and `rag_modes.agentic.map_reduce_max_papers` keys with comments.
- [ ] Commit: `git add -A && git commit -m "docs: log Phase 0, update roadmap and config.example.yml"`

---

# PHASE 1 — Content-Pipeline Coverage

> Read `docs/rules/content_pipeline.md` first. New sources **append** into `retrieve_paper_content()` in `src/perspicacite/pipeline/download/unified.py`; do not restructure the existing branches. Look at `src/perspicacite/pipeline/download/pmc.py` and `discovery.py` as the shape templates. `PaperContent` and `PaperDiscovery` are in `src/perspicacite/pipeline/download/base.py`.

## Task 1.1: bioRxiv/medRxiv DOI detection + metadata fetch

**Files:**
- Create: `src/perspicacite/pipeline/download/biorxiv.py`
- Test: `tests/unit/test_biorxiv.py` (new)

- [ ] **Step 1: Write failing tests**

`tests/unit/test_biorxiv.py`:
```python
import httpx
import pytest

from perspicacite.pipeline.download.biorxiv import is_biorxiv_doi, get_content_from_biorxiv


def test_is_biorxiv_doi():
    assert is_biorxiv_doi("10.1101/2021.01.01.425001")
    assert is_biorxiv_doi("https://doi.org/10.1101/2021.01.01.425001")
    assert not is_biorxiv_doi("10.1038/s41467-022-33890-w")
    assert not is_biorxiv_doi("")


@pytest.mark.asyncio
async def test_get_content_from_biorxiv_abstract_only(respx_mock):
    doi = "10.1101/2021.01.01.425001"
    respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
        return_value=httpx.Response(200, json={
            "messages": [{"status": "ok"}],
            "collection": [{
                "doi": doi, "title": "A Preprint", "authors": "Doe, J.; Roe, R.",
                "date": "2021-01-01", "abstract": "We show stuff.", "server": "biorxiv",
                "category": "neuroscience", "jatsxml": "",
            }],
        })
    )
    async with httpx.AsyncClient() as client:
        result = await get_content_from_biorxiv(doi, http_client=client)
    assert result is not None
    assert result.success is True
    assert result.content_type == "abstract"
    assert result.content_source in ("biorxiv", "medrxiv")
    assert result.abstract == "We show stuff."
    assert result.metadata["title"] == "A Preprint"


@pytest.mark.asyncio
async def test_get_content_from_biorxiv_not_found(respx_mock):
    respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
        return_value=httpx.Response(200, json={"messages": [{"status": "no posts found"}], "collection": []})
    )
    async with httpx.AsyncClient() as client:
        result = await get_content_from_biorxiv("10.1101/x", http_client=client)
    assert result is None
```
(`respx_mock` fixture comes from the `respx` pytest plugin; ensure `pytest-asyncio` is configured — it already is, given existing async tests.)

- [ ] **Step 2: Run, expect fail** — `uv run pytest tests/unit/test_biorxiv.py -v` → import error.

- [ ] **Step 3: Implement `biorxiv.py`**

```python
"""bioRxiv / medRxiv structured-retrieval source.

API: https://api.biorxiv.org/details/{server}/{doi}  (server = biorxiv | medrxiv)
JATS full text (when present): the `jatsxml` URL in the details response.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from perspicacite.logging import get_logger
from .base import PaperContent

logger = get_logger("perspicacite.pipeline.download.biorxiv")

_BIORXIV_DOI_RE = re.compile(r"10\.1101/", re.IGNORECASE)


def is_biorxiv_doi(doi: str | None) -> bool:
    if not doi:
        return False
    return bool(_BIORXIV_DOI_RE.search(doi))


def _normalize_doi(doi: str) -> str:
    return doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")


async def _fetch_details(doi: str, http_client: httpx.AsyncClient, server: str) -> dict[str, Any] | None:
    url = f"https://api.biorxiv.org/details/{server}/{doi}"
    try:
        resp = await http_client.get(url, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("biorxiv_details_failed", doi=doi, server=server, error=str(e))
        return None
    coll = data.get("collection") or []
    if not coll:
        return None
    # Pick the most recent version (last entry).
    return coll[-1]


async def get_content_from_biorxiv(
    doi: str,
    http_client: httpx.AsyncClient,
    **_: Any,
) -> PaperContent | None:
    """Return a PaperContent for a 10.1101/* DOI, or None if not on bioRxiv/medRxiv."""
    if not is_biorxiv_doi(doi):
        return None
    norm = _normalize_doi(doi)
    record: dict[str, Any] | None = None
    for server in ("biorxiv", "medrxiv"):
        record = await _fetch_details(norm, http_client, server)
        if record:
            break
    if not record:
        return None

    server = (record.get("server") or "biorxiv").lower()
    content_source = "medrxiv" if "med" in server else "biorxiv"
    authors_raw = record.get("authors") or ""
    authors = [a.strip() for a in re.split(r";|\band\b", authors_raw) if a.strip()]
    year: int | None = None
    date = record.get("date") or ""
    m = re.match(r"(\d{4})", date)
    if m:
        year = int(m.group(1))
    metadata: dict[str, Any] = {
        "doi": norm,
        "title": record.get("title"),
        "authors": authors,
        "year": year,
        "journal": content_source,
        "category": record.get("category"),
        "is_oa": True,
        "work_type": "preprint",
    }

    # Try JATS full text.
    jats_url = record.get("jatsxml") or ""
    if jats_url:
        try:
            from .pmc import parse_jats_xml  # reuse the JATS parser if exposed
        except Exception:  # noqa: BLE001
            parse_jats_xml = None  # type: ignore[assignment]
        if parse_jats_xml is not None:
            try:
                xml_resp = await http_client.get(jats_url, timeout=60.0)
                xml_resp.raise_for_status()
                parsed = parse_jats_xml(xml_resp.text)  # expected: object/dict with full_text, sections, references
                full_text = getattr(parsed, "full_text", None) or (parsed.get("full_text") if isinstance(parsed, dict) else None)
                if full_text:
                    sections = getattr(parsed, "sections", None) or (parsed.get("sections") if isinstance(parsed, dict) else None)
                    references = getattr(parsed, "references", None) or (parsed.get("references") if isinstance(parsed, dict) else None)
                    logger.info("biorxiv_structured", doi=norm, source=content_source, chars=len(full_text))
                    return PaperContent(
                        success=True, doi=norm, content_type="structured",
                        content_source=content_source, full_text=full_text,
                        sections=sections, references=references,
                        abstract=record.get("abstract"), metadata=metadata,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("biorxiv_jats_failed", doi=norm, error=str(e))

    abstract = record.get("abstract")
    if abstract:
        logger.info("biorxiv_abstract", doi=norm, source=content_source)
        return PaperContent(
            success=True, doi=norm, content_type="abstract",
            content_source=content_source, abstract=abstract, metadata=metadata,
        )
    return None
```
> NOTE: `pmc.py` may not export a reusable `parse_jats_xml`. Before implementing, `rg "def .*jats|JATS|parse" src/perspicacite/pipeline/download/pmc.py src/perspicacite/pipeline/parsers/`. If there's a usable function, import it (adjust the name). If not, **skip the JATS branch entirely** for this task — return `abstract`-type only — and note it; the abstract path is the must-have.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(pipeline): add bioRxiv/medRxiv content source"
```

---

## Task 1.2: Wire bioRxiv into the unified pipeline

**Files:**
- Modify: `src/perspicacite/pipeline/download/unified.py`
- Test: `tests/unit/test_download.py` (or `tests/test_download_real.py` is live — keep this mocked in `tests/unit/`)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_download.py` (create if it focuses on unit-level; there's already `tests/unit/test_download.py`):
```python
@pytest.mark.asyncio
async def test_retrieve_paper_content_uses_biorxiv(respx_mock, monkeypatch):
    from perspicacite.pipeline.download import retrieve_paper_content
    doi = "10.1101/2021.01.01.425001"
    # Make discovery cheap/empty so the pipeline reaches the biorxiv branch.
    respx_mock.get(url__regex=r"https://api\.biorxiv\.org/details/.*").mock(
        return_value=httpx.Response(200, json={"messages":[{"status":"ok"}], "collection":[{
            "doi": doi, "title": "BR Preprint", "authors": "X", "date": "2021-01-01",
            "abstract": "abstract text", "server": "biorxiv", "jatsxml": "",
        }]})
    )
    # Stub discovery to avoid OpenAlex/Unpaywall network (route those hosts to 404 or monkeypatch discover_paper_sources).
    monkeypatch.setattr(
        "perspicacite.pipeline.download.unified.discover_paper_sources",
        lambda *a, **k: _make_empty_discovery(doi),  # define a tiny helper returning PaperDiscovery with no OA urls
    )
    async with httpx.AsyncClient() as client:
        result = await retrieve_paper_content(doi, http_client=client)
    assert result.success and result.content_source in ("biorxiv", "medrxiv")
    assert result.abstract == "abstract text"
```
> Look at `PaperDiscovery` in `base.py` to construct `_make_empty_discovery`. If `discover_paper_sources` is async, `monkeypatch` it to an async stub.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Wire it in**

In `unified.py`, import `from .biorxiv import is_biorxiv_doi, get_content_from_biorxiv`. In the STRUCTURED stage of `retrieve_paper_content()` — after the PMC and arXiv attempts, before publisher-PDF — add:
```python
        # bioRxiv / medRxiv preprints
        if is_biorxiv_doi(doi) or (disc is not None and getattr(disc, "work_type", "") == "preprint" and is_biorxiv_doi(doi)):
            br = await get_content_from_biorxiv(doi, http_client=http_client)
            if br is not None and br.success and br.content_type in ("structured", "abstract"):
                # Only return now if it's structured; if abstract-only, keep it as a candidate
                # and let the existing abstract-fallback stage prefer the richest source.
                if br.content_type == "structured":
                    return br
                biorxiv_abstract_candidate = br  # remember for the ABSTRACT stage
```
Then in the ABSTRACT stage, if `biorxiv_abstract_candidate` exists and the discovery abstract is empty/shorter, return `biorxiv_abstract_candidate`. Keep it minimal — initialize `biorxiv_abstract_candidate = None` near the top of the function.

- [ ] **Step 4: Run tests, expect pass**; run the full `tests/unit/` suite to confirm no regression; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(pipeline): try bioRxiv/medRxiv in unified retrieval chain"
```

---

## Task 1.3: Crossref metadata enrichment module

**Files:**
- Create: `src/perspicacite/pipeline/download/crossref.py`
- Test: `tests/unit/test_crossref.py` (new)

- [ ] **Step 1: Write failing tests**

```python
import httpx
import pytest
from perspicacite.pipeline.download.crossref import enrich_from_crossref


@pytest.mark.asyncio
async def test_enrich_fills_missing_only(respx_mock):
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {
            "title": ["Crossref Title"], "published": {"date-parts": [[2020, 5]]},
            "author": [{"given": "Jane", "family": "Doe"}], "container-title": ["J. Test"],
            "abstract": "<jats:p>An abstract.</jats:p>", "reference": [{"DOI": "10.1/ref"}],
        }})
    )
    base = {"title": "Existing Title", "authors": [], "year": None, "journal": None, "abstract": None}
    async with httpx.AsyncClient() as client:
        patch = await enrich_from_crossref("10.1/x", http_client=client, base_metadata=base, mailto="me@example.com")
    assert patch.get("title") is None or "title" not in patch  # title was already present → not patched
    assert patch["year"] == 2020
    assert patch["journal"] == "J. Test"
    assert patch["abstract"] == "An abstract."  # JATS tags stripped
    assert patch["authors"]  # filled because base had empty list


@pytest.mark.asyncio
async def test_enrich_network_error_returns_empty(respx_mock):
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as client:
        patch = await enrich_from_crossref("10.1/x", http_client=client, base_metadata={"title": None}, mailto=None)
    assert patch == {}
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
"""Crossref metadata enrichment — fills gaps left by OpenAlex/Unpaywall."""
from __future__ import annotations

import re
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.download.crossref")

_JATS_TAG_RE = re.compile(r"<[^>]+>")


def _strip_jats(text: str | None) -> str | None:
    if not text:
        return None
    return _JATS_TAG_RE.sub("", text).strip() or None


def _norm_doi(doi: str) -> str:
    return doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")


async def enrich_from_crossref(
    doi: str,
    http_client: httpx.AsyncClient,
    base_metadata: dict[str, Any],
    mailto: str | None = None,
) -> dict[str, Any]:
    """Return a patch dict containing ONLY fields missing/empty in `base_metadata`.

    Never overwrites a value that base_metadata already has. Failures → {}.
    """
    url = f"https://api.crossref.org/works/{_norm_doi(doi)}"
    headers = {"User-Agent": f"perspicacite/2 (mailto:{mailto})"} if mailto else {}
    try:
        resp = await http_client.get(url, headers=headers, timeout=20.0)
        resp.raise_for_status()
        msg = (resp.json() or {}).get("message") or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("crossref_enrich_failed", doi=doi, error=str(e))
        return {}

    patch: dict[str, Any] = {}

    def _missing(key: str) -> bool:
        v = base_metadata.get(key)
        return v is None or v == "" or v == []

    if _missing("title"):
        titles = msg.get("title") or []
        if titles:
            patch["title"] = titles[0]
    if _missing("journal"):
        ct = msg.get("container-title") or []
        if ct:
            patch["journal"] = ct[0]
    if _missing("year"):
        dp = (msg.get("published") or msg.get("issued") or {}).get("date-parts") or [[]]
        if dp and dp[0]:
            patch["year"] = dp[0][0]
    if _missing("authors"):
        authors = []
        for a in msg.get("author") or []:
            name = " ".join(x for x in (a.get("given"), a.get("family")) if x).strip()
            if name:
                authors.append(name)
        if authors:
            patch["authors"] = authors
    if _missing("abstract"):
        abs_ = _strip_jats(msg.get("abstract"))
        if abs_:
            patch["abstract"] = abs_
    if _missing("references") and msg.get("reference"):
        refs = []
        for r in msg["reference"]:
            refs.append({"doi": r.get("DOI"), "title": r.get("article-title") or r.get("unstructured"), "year": r.get("year")})
        if refs:
            patch["references"] = refs
    license_url = None
    for lic in msg.get("license") or []:
        if lic.get("URL"):
            license_url = lic["URL"]
            break
    if license_url and _missing("license"):
        patch["license"] = license_url

    if patch:
        logger.info("crossref_enriched", doi=doi, fields=sorted(patch.keys()))
    return patch
```

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(pipeline): add Crossref metadata enrichment helper"
```

---

## Task 1.4: Wire Crossref into the discovery stage

**Files:**
- Modify: `src/perspicacite/pipeline/download/unified.py` (DISCOVERY stage)
- Test: `tests/unit/test_download.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_discovery_enriched_by_crossref(respx_mock, monkeypatch):
    from perspicacite.pipeline.download import retrieve_paper_content
    doi = "10.1234/sparse"
    # discovery returns a PaperDiscovery missing year + journal:
    monkeypatch.setattr("perspicacite.pipeline.download.unified.discover_paper_sources",
                        _stub_sparse_discovery)  # returns PaperDiscovery(title="T", year=None, authors=["A"], ...)
    respx_mock.get(url__regex=r"https://api\.crossref\.org/works/.*").mock(
        return_value=httpx.Response(200, json={"message": {"published": {"date-parts": [[2019]]}, "container-title": ["J"]}})
    )
    # everything else 404 so it falls through to abstract/none; we only assert metadata:
    async with httpx.AsyncClient() as client:
        result = await retrieve_paper_content(doi, http_client=client)
    assert result.metadata.get("year") == 2019
    assert result.metadata.get("journal") == "J"
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Wire it in**

In `unified.py`, after `disc = await discover_paper_sources(...)` and after `_metadata_from_discovery(...)` builds the base `metadata` dict, add:
```python
        # Crossref gap-fill (cheap; never overwrites existing values).
        if any(metadata.get(k) in (None, "", []) for k in ("title", "authors", "year", "journal", "abstract")):
            from .crossref import enrich_from_crossref
            mailto = kwargs.get("unpaywall_email")  # reuse the polite-pool email if configured
            patch = await enrich_from_crossref(doi, http_client=http_client, base_metadata=metadata, mailto=mailto)
            for k, v in patch.items():
                if metadata.get(k) in (None, "", []):
                    metadata[k] = v
            # if discovery had no abstract but Crossref did, use it for the abstract-fallback stage
            if not getattr(disc, "abstract", None) and patch.get("abstract"):
                # store on a local var the ABSTRACT stage already checks, or set disc.abstract if mutable
                ...
```
Make sure `metadata` is the same dict object that is attached to every `PaperContent` returned later (it is, via `_metadata_from_discovery`). Keep the abstract handling minimal — if `disc.abstract` is mutable, set it; else add a `crossref_abstract` local that the ABSTRACT stage falls back to.

- [ ] **Step 4: Run tests + full unit suite, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(pipeline): enrich discovery metadata via Crossref when gaps remain"
```

---

## Task 1.5: Phase 1 wrap-up

- [ ] `AGENT_LOG.md`: dated entry — "Phase 1 — bioRxiv/medRxiv content source + Crossref metadata enrichment in the discovery stage."
- [ ] `ROADMAP.md`: tick "bioRxiv/medRxiv — structured preprint retrieval" and "Crossref metadata enrichment"; move to Completed archive with `(2026-05)`.
- [ ] `CLAUDE.md`: in the Content Retrieval Pipeline section, add bioRxiv/medRxiv to the structured-full-text bullet and Crossref to the discovery bullet.
- [ ] `docs/rules/content_pipeline.md`: add a short note on the two new modules.
- [ ] Commit: `git add -A && git commit -m "docs: log Phase 1, update roadmap/CLAUDE/content rules"`

---

# PHASE 2 — KB-Building Power Tools

> Reimplement the `old_tools/library_expansion_with_abstract/` scripts on the app's stack — do not copy v1 code. Keep `old_tools/` in place (a later human-confirmed cleanup removes it).

## Task 2.1: `screening` module — relevance scoring

**Files:**
- Create: `src/perspicacite/search/screening.py`
- Test: `tests/unit/test_screening.py` (new)

- [ ] **Step 1: Write failing tests**

```python
import pytest
from perspicacite.search.screening import screen_papers, ScreenResult


def test_bm25_screening_bands():
    candidates = [
        {"title": "Deep learning for protein folding", "abstract": "neural networks predict protein structure from sequence"},
        {"title": "A history of Renaissance painting", "abstract": "oil on canvas in 15th century Florence"},
    ]
    refs = ["protein structure prediction with deep neural networks"]
    results = screen_papers(candidates, reference=refs, method="bm25", threshold=0.2)
    assert all(isinstance(r, ScreenResult) for r in results)
    by_title = {r.item.get("title"): r for r in results}
    assert by_title["Deep learning for protein folding"].score > by_title["A history of Renaissance painting"].score
    assert by_title["Deep learning for protein folding"].kept is True


@pytest.mark.asyncio
async def test_llm_screening(monkeypatch):
    class FakeLLM:
        async def complete(self, messages, **kw):
            # return JSON array of {index, score, reason}
            return '[{"index":0,"score":0.9,"reason":"on topic"},{"index":1,"score":0.1,"reason":"unrelated"}]'
    candidates = [{"title": "A", "abstract": "x"}, {"title": "B", "abstract": "y"}]
    results = await screen_papers(  # async when method="llm"
        candidates, reference="topic query", method="llm", threshold=0.5, llm=FakeLLM()
    ) if False else None
    # NOTE: see Step 3 — pick ONE API shape (sync for bm25, async for llm via a separate
    # `screen_papers_llm` coroutine) and write the test to match. Keep BM25 sync.
```
> Decide the API in Step 3 and finalize this test accordingly. Recommendation: `screen_papers(...)` is **sync** and only does BM25; `screen_papers_llm(...)` is an **async** coroutine taking an `llm`. This keeps types clean (no sometimes-coroutine).

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
"""Relevance screening of candidate papers against a reference query/set."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

from rank_bm25 import BM25Okapi

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.search.screening")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the","a","an","and","or","of","to","in","for","on","with","is","are","be","as","by","that","this","we","our",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


def _doc_text(item: dict[str, Any]) -> str:
    return f"{item.get('title') or ''} {item.get('abstract') or ''}".strip()


@dataclass
class ScreenResult:
    item: dict[str, Any]
    score: float
    kept: bool
    reason: str = ""


def screen_papers(
    candidates: Sequence[dict[str, Any]],
    reference: str | Sequence[str],
    method: str = "bm25",
    threshold: float = 0.3,
) -> list[ScreenResult]:
    """BM25 relevance screening. `reference` is a query string or a list of reference abstracts.

    Score = max BM25 similarity of the candidate doc to any reference doc, normalized to [0, ~1+].
    """
    if method != "bm25":
        raise ValueError("screen_papers only supports method='bm25'; use screen_papers_llm for LLM scoring")
    refs = [reference] if isinstance(reference, str) else list(reference)
    ref_tokens = [_tokenize(r) for r in refs if r and r.strip()]
    if not ref_tokens:
        return [ScreenResult(item=c, score=0.0, kept=False) for c in candidates]
    cand_docs = [_tokenize(_doc_text(c)) for c in candidates]
    # Build BM25 over candidates; query with each reference; take per-candidate max.
    bm25 = BM25Okapi([d if d else ["__empty__"] for d in cand_docs])
    per_cand_max = [0.0] * len(candidates)
    for rt in ref_tokens:
        if not rt:
            continue
        scores = bm25.get_scores(rt)
        for i, s in enumerate(scores):
            per_cand_max[i] = max(per_cand_max[i], float(s))
    # Normalize by the max observed so thresholds are comparable across runs.
    top = max(per_cand_max) or 1.0
    results = []
    for c, raw in zip(candidates, per_cand_max):
        norm = raw / top
        results.append(ScreenResult(item=c, score=round(norm, 4), kept=norm >= threshold))
    results.sort(key=lambda r: r.score, reverse=True)
    logger.info("screen_papers_bm25", n=len(candidates), kept=sum(r.kept for r in results), threshold=threshold)
    return results


async def screen_papers_llm(
    candidates: Sequence[dict[str, Any]],
    query: str,
    llm: Any,
    threshold: float = 0.5,
    batch_size: int = 20,
) -> list[ScreenResult]:
    """LLM relevance scoring (0..1 + one-line reason) of candidates against `query`."""
    out: list[ScreenResult] = []
    for start in range(0, len(candidates), batch_size):
        batch = list(candidates[start:start + batch_size])
        listing = "\n".join(
            f"{i}. {c.get('title') or '(no title)'} — {(c.get('abstract') or '')[:400]}"
            for i, c in enumerate(batch)
        )
        messages = [
            {"role": "system", "content": "You rate how relevant each paper is to a research query. "
             "Respond ONLY with a JSON array of objects {\"index\": int, \"score\": float in [0,1], \"reason\": short string}."},
            {"role": "user", "content": f"Query: {query}\n\nPapers:\n{listing}"},
        ]
        raw = await llm.complete(messages=messages)
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        try:
            m = re.search(r"\[.*\]", text, re.S)
            parsed = json.loads(m.group(0) if m else text)
        except Exception:  # noqa: BLE001
            logger.warning("screen_papers_llm_parse_failed", batch_start=start)
            parsed = []
        scored: dict[int, dict[str, Any]] = {int(o["index"]): o for o in parsed if "index" in o}
        for i, c in enumerate(batch):
            o = scored.get(i, {})
            score = float(o.get("score", 0.0) or 0.0)
            out.append(ScreenResult(item=c, score=round(score, 4), kept=score >= threshold, reason=str(o.get("reason", ""))))
    out.sort(key=lambda r: r.score, reverse=True)
    logger.info("screen_papers_llm", n=len(candidates), kept=sum(r.kept for r in out))
    return out
```

- [ ] **Step 4: Finalize the test** to match (BM25 sync test + an async `screen_papers_llm` test with `FakeLLM`). Run, expect pass; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(search): add paper relevance screening (BM25 + LLM)"
```

---

## Task 2.2: `screen_papers` MCP tool

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (add tool; update `get_info()` + module docstring)
- Test: `tests/test_mcp_server.py` (extend)

- [ ] **Step 1: Write the failing test**

In `tests/test_mcp_server.py` (this file is unit-ish — it imports the module; keep network mocked). Add:
```python
@pytest.mark.asyncio
async def test_screen_papers_tool_uninitialized():
    from perspicacite.mcp import server as mcp_server
    mcp_server.mcp_state.initialized = False
    out = await mcp_server.screen_papers(candidates=["10.1/a"], query="x")
    import json; data = json.loads(out)
    assert data["success"] is False


@pytest.mark.asyncio
async def test_screen_papers_tool_bm25(monkeypatch):
    from perspicacite.mcp import server as mcp_server
    mcp_server.mcp_state.initialized = True
    # avoid network: stub the abstract-fetch the tool uses
    monkeypatch.setattr(mcp_server, "_fetch_abstracts_for_screening",
                        lambda items, **kw: [{"title": t, "abstract": "neural net protein"} for t in items], raising=False)
    out = await mcp_server.screen_papers(candidates=["protein A", "painting B"], query="protein neural networks", method="bm25", threshold=0.0)
    import json; data = json.loads(out)
    assert data["success"] is True and len(data["screened"]) == 2
```
> Adjust to the actual helper name you create. If you don't need a helper (titles passed directly are usable as docs), drop the monkeypatch and pass titles whose text differs enough to rank.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement the tool**

In `mcp/server.py`, after the existing tools:
```python
@mcp.tool
async def screen_papers(
    candidates: list[str],
    query: str,
    method: str = "bm25",
    threshold: float = 0.3,
    max_results: int = 50,
) -> str:
    """
    Score candidate papers (DOIs or titles) by relevance to a query.

    Args:
        candidates: List of DOIs or titles to screen.
        query: The research query / topic to screen against.
        method: "bm25" (text similarity, fast, no LLM) or "llm" (LLM-rated 0-1 with reasons).
        threshold: Keep papers scoring >= this (0..1).
        max_results: Cap on returned items.

    Returns:
        JSON: {"screened": [{"doi"|"title", "score", "kept", "reason"}, ...]}
    """
    state = _require_state()
    if isinstance(state, str):
        return state
    try:
        from perspicacite.search.screening import screen_papers as _bm25, screen_papers_llm as _llm
        # Build candidate dicts; fetch abstracts when the candidate looks like a DOI.
        import httpx
        from perspicacite.pipeline.download import retrieve_paper_content
        items: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for c in candidates:
                if c.strip().lower().startswith("10.") or "doi.org/" in c:
                    try:
                        r = await retrieve_paper_content(c, http_client=client, pdf_parser=state.pdf_parser)
                        md = r.metadata or {}
                        items.append({"doi": c, "title": md.get("title") or c, "abstract": r.abstract or md.get("abstract") or ""})
                    except Exception:  # noqa: BLE001
                        items.append({"doi": c, "title": c, "abstract": ""})
                else:
                    items.append({"title": c, "abstract": ""})
        if method == "llm":
            results = await _llm(items, query=query, llm=state.llm_client, threshold=threshold)
        else:
            results = _bm25(items, reference=query, method="bm25", threshold=threshold)
        screened = []
        for r in results[:max_results]:
            entry = {"score": r.score, "kept": r.kept, "reason": r.reason}
            if r.item.get("doi"):
                entry["doi"] = r.item["doi"]
            entry["title"] = r.item.get("title")
            screened.append(entry)
        logger.info("mcp_screen_papers", n=len(candidates), method=method)
        return _json_ok({"query": query, "method": method, "screened": screened})
    except Exception as e:  # noqa: BLE001
        logger.error("mcp_screen_papers_error", error=str(e))
        return _json_error(f"Screening failed: {e}")
```
Add `"screen_papers"` to the tool list in `get_info()` and the module docstring's "Tools exposed" list. (Count bump to 10 happens in Task 2.6 along with `add_dois_to_kb` — or do it now and again then; just be consistent.)

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(mcp): add screen_papers tool"
```

---

## Task 2.3: `screen-papers` CLI subcommand

**Files:**
- Modify: `src/perspicacite/cli.py`
- Test: `tests/unit/test_cli_screen.py` (new) — invoke via `click.testing.CliRunner` with tiny `.bib` fixtures and mocked network

- [ ] **Step 1: Write the failing test**

```python
from click.testing import CliRunner
from perspicacite.cli import cli


def test_screen_papers_cli_help():
    res = CliRunner().invoke(cli, ["screen-papers", "--help"])
    assert res.exit_code == 0 and "screen" in res.output.lower()


def test_screen_papers_cli_bm25(tmp_path, monkeypatch):
    refs = tmp_path / "refs.bib"; refs.write_text('@article{r1, title={protein neural networks}, abstract={deep learning protein}}\n')
    cand = tmp_path / "cand.bib"; cand.write_text(
        '@article{c1, title={Deep nets for proteins}, abstract={neural protein folding}}\n'
        '@article{c2, title={Renaissance art}, abstract={oil canvas Florence}}\n'
    )
    out = tmp_path / "out.bib"
    res = CliRunner().invoke(cli, ["screen-papers", "--input", str(refs), "--candidates", str(cand), "--output", str(out), "--method", "bm25", "--threshold", "0.0"])
    assert res.exit_code == 0
    assert out.exists() and "c1" in out.read_text()
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

In `cli.py`, add a command (mirror existing `@cli.command()` style; reuse `from perspicacite.pipeline.bibtex_kb import entries_to_papers` and `bibtexparser` for I/O):
```python
@cli.command(name="screen-papers")
@click.option("--input", "input_bib", required=True, type=click.Path(exists=True), help="Reference .bib (defines the topic)")
@click.option("--candidates", "cand_bib", required=True, type=click.Path(exists=True), help="Candidate .bib to screen")
@click.option("--output", "output_bib", required=True, type=click.Path(), help="Output .bib of kept papers")
@click.option("--method", type=click.Choice(["bm25", "llm"]), default="bm25")
@click.option("--threshold", type=float, default=0.3)
@click.option("--csv", "csv_path", type=click.Path(), default=None, help="Optional CSV report")
@click.pass_context
def screen_papers_cmd(ctx, input_bib, cand_bib, output_bib, method, threshold, csv_path):
    """Screen candidate papers for relevance to a reference set's topic."""
    import bibtexparser, csv as _csv
    from perspicacite.search.screening import screen_papers
    ref_entries = bibtexparser.loads(open(input_bib).read()).entries
    cand_db = bibtexparser.loads(open(cand_bib).read())
    cand_entries = cand_db.entries
    refs = [f"{e.get('title','')} {e.get('abstract','')}" for e in ref_entries]
    cands = [{"title": e.get("title", ""), "abstract": e.get("abstract", ""), "_entry": e} for e in cand_entries]
    if method == "llm":
        raise click.ClickException("LLM screening from the CLI is not wired yet; use --method bm25")  # or: load config + run asyncio
    results = screen_papers(cands, reference=refs, method="bm25", threshold=threshold)
    kept_entries = [r.item["_entry"] for r in results if r.kept]
    out_db = bibtexparser.bibdatabase.BibDatabase(); out_db.entries = kept_entries
    open(output_bib, "w").write(bibtexparser.dumps(out_db))
    if csv_path:
        with open(csv_path, "w", newline="") as fh:
            w = _csv.writer(fh); w.writerow(["title", "score", "kept"])
            for r in results:
                w.writerow([r.item.get("title"), r.score, r.kept])
    click.echo(f"Kept {len(kept_entries)}/{len(cand_entries)} → {output_bib}")
```
(If wiring LLM mode is cheap given the existing `_run_query`-style config loading in `cli.py`, do it; otherwise the `ClickException` guard is acceptable for v1.)

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cli): add screen-papers subcommand"
```

---

## Task 2.4: PubMed search adapter (`pubmed_explorer` port)

**Files:**
- Create: `src/perspicacite/search/pubmed.py`
- Modify: `src/perspicacite/config/schema.py` (add `databases.pubmed_email` or a `pubmed_email` field — check `DatabaseConfig`/`SciLexConfig`; pick the simplest place; default `""`)
- Test: `tests/unit/test_pubmed.py` (new) — monkeypatch `Bio.Entrez`

- [ ] **Step 1: Write failing tests**

```python
import pytest
from perspicacite.search.pubmed import PubMedSearchAdapter, PubMedConfigError


@pytest.mark.asyncio
async def test_pubmed_requires_email():
    with pytest.raises(PubMedConfigError):
        PubMedSearchAdapter(email="")


@pytest.mark.asyncio
async def test_pubmed_search_parses(monkeypatch):
    import io
    class FakeEntrez:
        email = None
        api_key = None
        @staticmethod
        def esearch(**kw):
            return io.StringIO("")  # read() handled by FakeRead below
        @staticmethod
        def read(handle):
            return {"IdList": ["111", "222"]}
        @staticmethod
        def efetch(**kw):
            return io.StringIO("<xml/>")
    # Provide a parsed-record structure for efetch via a second monkeypatch on the parser the adapter uses.
    monkeypatch.setattr("perspicacite.search.pubmed.Entrez", FakeEntrez, raising=False)
    monkeypatch.setattr("perspicacite.search.pubmed._parse_efetch",
        lambda handle: [
            {"pmid": "111", "title": "Paper One", "year": 2020, "doi": "10.1/one", "abstract": "a", "journal": "J", "authors": ["Doe J"]},
            {"pmid": "222", "title": "Paper Two", "year": 2021, "doi": None, "abstract": "b", "journal": "K", "authors": []},
        ], raising=False)
    adapter = PubMedSearchAdapter(email="me@example.org")
    papers = await adapter.search("crispr", max_results=5)
    assert len(papers) == 2
    assert papers[0].title == "Paper One" and papers[0].doi == "10.1/one"
    assert papers[0].metadata.get("pmid") == "111"
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
"""PubMed deep-search adapter using Biopython Entrez (port of v1 pubmed_explorer)."""
from __future__ import annotations

import asyncio
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.pubmed")

try:
    from Bio import Entrez  # type: ignore
except Exception:  # noqa: BLE001
    Entrez = None  # type: ignore


class PubMedConfigError(RuntimeError):
    pass


_OBVIOUS_PLACEHOLDERS = {"", "user@example.com", "you@example.com", "your.email@domain.com", "email@example.com"}


def _parse_efetch(handle: Any) -> list[dict[str, Any]]:
    """Parse an Entrez efetch (PubMed XML) handle into plain dicts. Defensive."""
    records = Entrez.read(handle)  # type: ignore[union-attr]
    out: list[dict[str, Any]] = []
    for art in records.get("PubmedArticle", []):
        cit = art.get("MedlineCitation", {})
        article = cit.get("Article", {})
        pmid = str(cit.get("PMID", "")) or None
        title = str(article.get("ArticleTitle", "")) or None
        abstract_parts = article.get("Abstract", {}).get("AbstractText", []) or []
        abstract = " ".join(str(p) for p in abstract_parts) or None
        journal = str(article.get("Journal", {}).get("Title", "")) or None
        year = None
        try:
            year = int(article.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {}).get("Year"))
        except Exception:  # noqa: BLE001
            pass
        doi = None
        for aid in art.get("PubmedData", {}).get("ArticleIdList", []):
            if getattr(aid, "attributes", {}).get("IdType") == "doi":
                doi = str(aid)
        authors = []
        for a in article.get("AuthorList", []) or []:
            name = " ".join(x for x in (a.get("ForeName"), a.get("LastName")) if x)
            if name:
                authors.append(name)
        out.append({"pmid": pmid, "title": title, "year": year, "doi": doi,
                    "abstract": abstract, "journal": journal, "authors": authors})
    return out


class PubMedSearchAdapter:
    def __init__(self, email: str, api_key: str | None = None, rate_limit_per_sec: float | None = None):
        if Entrez is None:
            raise PubMedConfigError("Biopython is not installed")
        if not email or email.strip().lower() in _OBVIOUS_PLACEHOLDERS:
            raise PubMedConfigError(
                "PubMed search requires a real NCBI email. Set config.databases.pubmed_email "
                "(or pdf_download.unpaywall_email) to your address."
            )
        self.email = email
        self.api_key = api_key or None
        Entrez.email = email  # type: ignore[union-attr]
        if self.api_key:
            Entrez.api_key = self.api_key  # type: ignore[union-attr]
        self._min_interval = 1.0 / (rate_limit_per_sec or (10.0 if self.api_key else 3.0))

    async def search(self, query: str, max_results: int = 20,
                     year_min: int | None = None, year_max: int | None = None, **_: Any) -> list[Paper]:
        term = query
        if year_min or year_max:
            term += f' AND ({year_min or 1800}:{year_max or 2100}[dp])'
        def _run() -> list[dict[str, Any]]:
            h = Entrez.esearch(db="pubmed", term=term, retmax=max_results)  # type: ignore[union-attr]
            ids = Entrez.read(h).get("IdList", [])  # type: ignore[union-attr]
            if not ids:
                return []
            fh = Entrez.efetch(db="pubmed", id=",".join(ids), rettype="xml", retmode="xml")  # type: ignore[union-attr]
            return _parse_efetch(fh)
        raw = await asyncio.to_thread(_run)
        papers: list[Paper] = []
        for r in raw:
            papers.append(Paper(
                id=r.get("doi") or f"pmid:{r.get('pmid')}",
                title=r.get("title") or "",
                authors=[Author(name=a) for a in r.get("authors", [])],
                year=r.get("year"),
                doi=r.get("doi"),
                abstract=r.get("abstract"),
                journal=r.get("journal"),
                source=PaperSource.WEB_SEARCH,
                metadata={"pmid": r.get("pmid")},
            ))
        logger.info("pubmed_search", query=query, results=len(papers))
        return papers
```
Add config field: in `schema.py`, on `DatabaseConfig` (or wherever PubMed config naturally lives) add `pubmed_email: str = ""`. If there's no obvious place, add it on `SciLexConfig`. Keep it optional with `""` default.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(search): add PubMed Entrez search adapter (port of pubmed_explorer)"
```

---

## Task 2.5: `pubmed-search` CLI subcommand + optional `search_literature` backend hook

**Files:**
- Modify: `src/perspicacite/cli.py`
- Modify (only if cheap & non-breaking): `src/perspicacite/search/scilex_adapter.py` or `src/perspicacite/mcp/server.py` `search_literature` to allow `databases=["pubmed_deep"]` routing to `PubMedSearchAdapter`
- Test: `tests/unit/test_cli_pubmed.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from click.testing import CliRunner
from perspicacite.cli import cli


def test_pubmed_search_cli_help():
    res = CliRunner().invoke(cli, ["pubmed-search", "--help"])
    assert res.exit_code == 0


def test_pubmed_search_cli_runs(tmp_path, monkeypatch):
    import perspicacite.search.pubmed as pm
    class FakeAdapter:
        def __init__(self, *a, **k): pass
        async def search(self, *a, **k):
            from perspicacite.models.papers import Paper, PaperSource
            return [Paper(id="10.1/x", title="T", authors=[], year=2020, doi="10.1/x", abstract="a", source=PaperSource.WEB_SEARCH, metadata={"pmid": "1"})]
    monkeypatch.setattr(pm, "PubMedSearchAdapter", FakeAdapter)
    out = tmp_path / "o.bib"
    res = CliRunner().invoke(cli, ["pubmed-search", "crispr", "--max", "1", "--output", str(out), "--email", "me@example.org"])
    assert res.exit_code == 0 and out.exists()
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
@cli.command(name="pubmed-search")
@click.argument("query")
@click.option("--max", "max_results", type=int, default=20)
@click.option("--year-min", type=int, default=None)
@click.option("--year-max", type=int, default=None)
@click.option("--email", default=None, help="NCBI email (else taken from config)")
@click.option("--output", "output_bib", type=click.Path(), default=None, help="Write results to .bib")
@click.pass_context
def pubmed_search_cmd(ctx, query, max_results, year_min, year_max, email, output_bib):
    """Deep PubMed search via NCBI Entrez."""
    import asyncio
    from perspicacite.search.pubmed import PubMedSearchAdapter
    cfg = ctx.obj.get("config") if isinstance(ctx.obj, dict) else None  # match how other commands read config
    eff_email = email or (getattr(getattr(cfg, "databases", None), "pubmed_email", "") if cfg else "") or \
                (getattr(getattr(cfg, "pdf_download", None), "unpaywall_email", "") if cfg else "")
    adapter = PubMedSearchAdapter(email=eff_email or "")
    papers = asyncio.run(adapter.search(query, max_results=max_results, year_min=year_min, year_max=year_max))
    click.echo(f"Found {len(papers)} papers")
    for p in papers[:10]:
        click.echo(f"  - {p.year or '????'}  {p.title[:90]}")
    if output_bib:
        import bibtexparser
        db = bibtexparser.bibdatabase.BibDatabase()
        db.entries = [{
            "ENTRYTYPE": "article", "ID": (p.doi or f"pmid{p.metadata.get('pmid')}").replace("/", "_"),
            "title": p.title, "year": str(p.year or ""), "doi": p.doi or "",
            "journal": p.journal or "", "abstract": p.abstract or "",
            "author": " and ".join(a.name for a in p.authors),
        } for p in papers]
        open(output_bib, "w").write(bibtexparser.dumps(db))
        click.echo(f"Wrote {output_bib}")
```
> Check how existing commands access the loaded config (`ctx.obj`?). Mirror that exactly.

Optional backend hook (skip if it requires touching SciLEx internals in a risky way): in `mcp/server.py` `search_literature`, if `databases == ["pubmed_deep"]`, route to `PubMedSearchAdapter(email=state.config.databases.pubmed_email)` and convert to the same dict shape. Keep the existing behavior for all other `databases` values untouched.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(cli): add pubmed-search subcommand"
```

---

## Task 2.6: Batch DOI ingestion endpoint + `add_dois_to_kb` MCP tool

**Files:**
- Create (small helper) OR inline-duplicate: `src/perspicacite/pipeline/kb_ingest.py` — a coroutine `async def ingest_papers_into_kb(kb, papers, *, app_state-like deps) -> dict` that does download → enrich → dedup → `DynamicKnowledgeBase.add_papers` → metadata update, returning the stats dict. **Only extract this if `add_papers_to_kb` and `add_bibtex_to_kb` can be refactored to call it with identical behavior** (write tests pinning their current output first). If risky, skip the helper and duplicate the loop in the new endpoint.
- Modify: `src/perspicacite/web/routers/kb.py` (new `POST /api/kb/{name}/dois`)
- Modify: `src/perspicacite/mcp/server.py` (new `add_dois_to_kb` tool; bump tool count → 10 here)
- Test: `tests/unit/test_kb_dois_endpoint.py` (new), `tests/test_mcp_server.py` (extend)

- [ ] **Step 1: Write failing tests**

`tests/unit/test_kb_dois_endpoint.py`:
```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Build a TestClient over the FastAPI app with app_state subsystems mocked.
    # Look at tests/unit/test_web_app_routes.py for the existing pattern and reuse it.
    ...

def test_dois_endpoint_kb_not_found(client, monkeypatch):
    # session_store.get_kb_metadata -> None
    r = client.post("/api/kb/nope/dois", json={"dois": ["10.1/a"]})
    assert "not found" in str(r.json()).lower()

def test_dois_endpoint_oversize(client, monkeypatch):
    r = client.post("/api/kb/default/dois", json={"dois": ["10.1/x"] * 1000})
    assert r.status_code == 400

def test_dois_endpoint_happy(client, monkeypatch):
    # mock retrieve_paper_content -> PaperContent(success=True, full_text="...", metadata={"title":"T"})
    # mock DynamicKnowledgeBase.add_papers -> 3
    # mock vector_store.paper_exists -> False
    r = client.post("/api/kb/default/dois", json={"dois": ["10.1/a", "10.1/b"]})
    body = r.json()
    assert body["added_papers"] == 2 and body["added_chunks"] == 6  # 3 per paper
```
`tests/test_mcp_server.py`:
```python
@pytest.mark.asyncio
async def test_add_dois_to_kb_uninitialized():
    from perspicacite.mcp import server as s
    s.mcp_state.initialized = False
    import json; assert json.loads(await s.add_dois_to_kb("k", ["10.1/a"]))["success"] is False
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement the endpoint**

In `kb.py`, add a request model and route (mirror `add_bibtex_to_kb`):
```python
class KBAddDOIsRequest(BaseModel):
    dois: List[str] = Field(..., min_length=1, max_length=200)


@router.post("/api/kb/{name}/dois")
async def add_dois_to_kb(name: str, request: KBAddDOIsRequest):
    """Bulk-add papers to a KB from a list of DOIs (synchronous)."""
    if not app_state.session_store:
        return {"error": "System not initialized"}
    if len(request.dois) > 200:
        raise HTTPException(status_code=400, detail="At most 200 DOIs per request")
    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}

    from perspicacite.models.papers import Paper, Author, PaperSource
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
    from perspicacite.pipeline.download import retrieve_paper_content

    pdf_kw = _get_pdf_fallback_kwargs(app_state.config.pdf_download if app_state.config else None)
    papers_to_add: list[Paper] = []
    skipped: list[dict] = []
    failed: list[dict] = []
    dl = {"attempted": 0, "success": 0, "failed": 0}

    for raw_doi in request.dois:
        doi = raw_doi.strip().replace("https://doi.org/", "")
        if not doi:
            continue
        if await app_state.vector_store.paper_exists(kb.collection_name, doi):
            skipped.append({"doi": doi})
            continue
        dl["attempted"] += 1
        try:
            result = await retrieve_paper_content(doi, pdf_parser=app_state.pdf_parser, **pdf_kw)
        except Exception as e:  # noqa: BLE001
            failed.append({"doi": doi, "reason": str(e)})
            dl["failed"] += 1
            continue
        if not result or not result.success:
            failed.append({"doi": doi, "reason": "no content"})
            dl["failed"] += 1
            continue
        md = result.metadata or {}
        paper = Paper(
            id=doi, title=md.get("title") or f"Reference {doi}",
            authors=[Author(name=a) for a in (md.get("authors") or [])],
            year=md.get("year"), doi=doi, abstract=result.abstract or md.get("abstract"),
            journal=md.get("journal"), source=PaperSource.WEB_SEARCH,
        )
        if result.full_text:
            paper.full_text = result.full_text
            dl["success"] += 1
        else:
            dl["failed"] += 1
        papers_to_add.append(paper)

    if not papers_to_add:
        return {"added_papers": 0, "added_chunks": 0, "skipped_duplicates": len(skipped),
                "failed": failed, "pdf_download": dl, "kb": name}

    dkb = DynamicKnowledgeBase(vector_store=app_state.vector_store, embedding_service=app_state.embedding_provider)
    dkb.collection_name = kb.collection_name
    dkb._initialized = True
    added = await dkb.add_papers(papers_to_add, include_full_text=True)
    kb.paper_count += len(papers_to_add)
    kb.chunk_count += added
    await app_state.session_store.save_kb_metadata(kb)
    logger.info(f"Added {len(papers_to_add)} papers from DOI list to KB '{name}' ({added} chunks)")
    return {"added_papers": len(papers_to_add), "added_chunks": added,
            "skipped_duplicates": len(skipped), "failed": failed, "pdf_download": dl, "kb": name}
```

- [ ] **Step 4: Implement the MCP tool**

In `mcp/server.py`:
```python
@mcp.tool
async def add_dois_to_kb(kb_name: str, dois: list[str]) -> str:
    """
    Add papers to a knowledge base from a list of DOIs (downloads + indexes each).

    Args:
        kb_name: Target KB.
        dois: List of DOIs (max 200).

    Returns:
        JSON: {"added_papers", "added_chunks", "skipped_duplicates", "failed": [...], "pdf_download": {...}}
    """
    state = _require_state()
    if isinstance(state, str):
        return state
    if len(dois) > 200:
        return _json_error("At most 200 DOIs per request")
    try:
        from perspicacite.models.kb import chroma_collection_name_for_kb
        # Reuse the same per-DOI loop as the web endpoint (factor into pipeline/kb_ingest.py if extracted in Step 3,
        # else duplicate). Build PaperContent via retrieve_paper_content with state.config.pdf_download kwargs,
        # dedup via state.vector_store.paper_exists, add via DynamicKnowledgeBase, update KB metadata via session_store.
        ...
        return _json_ok({...})
    except Exception as e:  # noqa: BLE001
        logger.error("mcp_add_dois_error", kb_name=kb_name, error=str(e))
        return _json_error(f"Failed to add DOIs: {e}")
```
Then update tool count to **10** everywhere: `get_info()` list, module docstring, `CLAUDE.md` ("8 tools" → "10 tools"; add the two new ones to the MCP section), `docs/perspicacite_skills.md` (add entries for `screen_papers` and `add_dois_to_kb` in the existing format), `README.md`.

- [ ] **Step 5: Run all tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(api,mcp): batch DOI ingestion endpoint + add_dois_to_kb tool"
```

---

## Task 2.7: Phase 2 wrap-up

- [ ] `AGENT_LOG.md`: dated entry — "Phase 2 — ported old_tools: screening module + screen_papers MCP/CLI, PubMed Entrez adapter + pubmed-search CLI, batch DOI ingestion endpoint + add_dois_to_kb tool. MCP tool count 8→10."
- [ ] `ROADMAP.md`: tick "Port old_tools/library_expansion_with_abstract/" (note: `build_libraries_from_dois`'s citation/reference-expansion mode NOT ported — only flat DOI-list ingestion; leave that as a remaining item), "screen_papers MCP tool", "Batch DOI ingestion endpoint". Move done items to Completed archive.
- [ ] Commit: `git add -A && git commit -m "docs: log Phase 2, update roadmap"`

---

# PHASE 3 — RAG Depth

> Read `docs/rules/rag_development.md`. The new mode extends `BaseRAGMode` (`src/perspicacite/rag/modes/base.py`) — implement `execute()` and `execute_stream()`; errors are `StreamEvent(event="error", ...)`. Use `src/perspicacite/rag/modes/advanced.py` as the closest template (hybrid retrieval + rerank, no planning).

## Task 3.1: `RAGMode.CONTRADICTION` enum value + engine registration scaffolding

**Files:**
- Modify: `src/perspicacite/models/rag.py` (add enum member + docstring line)
- Modify: `src/perspicacite/config/schema.py` (`RAGModesConfig` — add a `contradiction` settings block, like `advanced`)
- Modify: `src/perspicacite/rag/engine.py` (register in `_modes` — will fail until Task 3.2 creates the class; do the import+registration in 3.2 instead and keep 3.1 to just the enum+config)
- Test: `tests/unit/test_models.py`, `tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
def test_contradiction_mode_enum():
    from perspicacite.models.rag import RAGMode
    assert RAGMode.CONTRADICTION.value == "contradiction"


def test_config_has_contradiction_settings():
    from perspicacite.config.schema import Config
    assert Config().rag_modes.contradiction is not None
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

`models/rag.py`: add `CONTRADICTION = "contradiction"` to `RAGMode` and a docstring line.
`schema.py`: on `RAGModesConfig` add:
```python
    contradiction: RAGModeSettings = Field(default_factory=lambda: RAGModeSettings(
        max_iterations=1, tools=["kb_search"], rerank=True, query_expansion=True,
        enable_planning=False, enable_reflection=False, use_hybrid=True,
    ))
```

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(rag): add CONTRADICTION mode enum + config block"
```

---

## Task 3.2: Contradiction-detection mode handler

**Files:**
- Create: `src/perspicacite/rag/modes/contradiction.py`
- Modify: `src/perspicacite/rag/modes/__init__.py` (export `ContradictionRAGMode`)
- Modify: `src/perspicacite/rag/engine.py` (register `RAGMode.CONTRADICTION: ContradictionRAGMode(config)`)
- Modify: `src/perspicacite/rag/prompts.py` (add the clustering + synthesis prompts)
- Test: `tests/unit/test_contradiction_mode.py` (new)

- [ ] **Step 1: Write failing tests**

```python
import pytest


@pytest.mark.asyncio
async def test_contradiction_mode_three_buckets(monkeypatch):
    from perspicacite.config.schema import Config
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode
    from perspicacite.models.rag import RAGRequest

    mode = ContradictionRAGMode(Config())

    # Fake retrieval: return >=3 chunks across 3 papers.
    class FakeChunk:
        def __init__(self, pid, text): self.metadata = {"paper_id": pid, "title": f"P{pid}", "doi": f"10.1/{pid}", "year": 2020}; self.text = text; self.score = 0.9
    fake_chunks = [FakeChunk("a", "X increases Y"), FakeChunk("b", "X has no effect on Y"), FakeChunk("c", "X may increase Y under condition Z")]

    class FakeLLM:
        async def complete(self, messages, **kw):
            # First call: clustering JSON; later calls: synthesis text. Inspect messages to branch, or just return JSON
            return '{"consensus": ["P? "], "disagreement": [{"claim": "X increases Y", "papers": ["10.1/a"]}, {"claim": "no effect", "papers": ["10.1/b"]}], "open": ["condition Z dependence"]}'

    # Patch the mode's retrieval to return fake_chunks (look at how advanced.py retrieves and patch that seam).
    monkeypatch.setattr(mode, "_retrieve", lambda *a, **k: fake_chunks, raising=False)

    events = []
    async for ev in mode.execute_stream(request=RAGRequest(query="Does X affect Y?", kb_name="k"),
                                         llm=FakeLLM(), vector_store=object(), embedding_provider=object(), tools=object()):
        events.append(ev)
    kinds = [e.event for e in events]
    assert "content" in kinds
    assert any(e.event == "error" for e in events) is False
    text = "".join(__import__("json").loads(e.data).get("delta", "") for e in events if e.event == "content")
    assert "consensus" in text.lower() or "agreement" in text.lower()
    assert "disagree" in text.lower()


@pytest.mark.asyncio
async def test_contradiction_mode_few_papers_degrades(monkeypatch):
    from perspicacite.config.schema import Config
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode
    from perspicacite.models.rag import RAGRequest
    mode = ContradictionRAGMode(Config())
    monkeypatch.setattr(mode, "_retrieve", lambda *a, **k: [], raising=False)  # 0 chunks
    class FakeLLM:
        async def complete(self, messages, **kw): return "fallback answer"
    events = [e async for e in mode.execute_stream(request=RAGRequest(query="q", kb_name="k"),
                                                   llm=FakeLLM(), vector_store=object(), embedding_provider=object(), tools=object())]
    assert any(e.event == "content" for e in events)
    assert all(e.event != "error" for e in events)  # must not error on empty
```
> Adjust `_retrieve` / seam names to whatever you implement. The key invariants the tests pin: (1) three-bucket structured output when ≥3 papers; (2) graceful degrade, no `error` event, when too few papers; (3) `source` events emitted for cited papers (add an assertion once you settle the event shape — mirror `advanced.py`).

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement `contradiction.py`**

Skeleton (fill in using `advanced.py` for the retrieval+rerank machinery and the `BaseRAGMode` contract):
```python
"""Contradiction-detection RAG mode: surface agreement / disagreement / open questions across papers."""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator

from perspicacite.logging import get_logger
from perspicacite.models.rag import RAGMode, RAGRequest, RAGResponse, SourceReference, StreamEvent
from perspicacite.rag.modes.base import BaseRAGMode

logger = get_logger("perspicacite.rag.modes.contradiction")

MIN_PAPERS_FOR_ANALYSIS = 3


class ContradictionRAGMode(BaseRAGMode):
    def __init__(self, config: Any):
        super().__init__(config)
        self.settings = config.rag_modes.contradiction

    async def execute(self, request, llm, vector_store, embedding_provider, tools) -> RAGResponse:
        # Collect the streamed result into a RAGResponse (mirror how other modes' execute() reuse execute_stream(),
        # or how advanced.py implements execute()).
        ...

    async def execute_stream(self, request, llm, vector_store, embedding_provider, tools) -> AsyncGenerator[StreamEvent, None]:
        try:
            chunks = await self._retrieve(request, vector_store, embedding_provider, llm)  # hybrid + rerank like advanced
            # group by paper
            by_paper: dict[str, list[Any]] = {}
            for c in chunks:
                pid = (getattr(c, "metadata", {}) or {}).get("paper_id") or "?"
                by_paper.setdefault(pid, []).append(c)
            n_papers = len([p for p in by_paper if p != "?"])

            if n_papers < MIN_PAPERS_FOR_ANALYSIS:
                # graceful degrade: do a plain advanced-style answer with a note
                yield StreamEvent(event="content", data=json.dumps({"delta": (
                    f"Note: contradiction analysis needs at least {MIN_PAPERS_FOR_ANALYSIS} papers; "
                    f"found {n_papers}. Answering normally instead.\n\n")}))
                async for ev in self._fallback_answer(request, chunks, llm):
                    yield ev
                return

            # map: per-paper claim summary (cap with config.rag_modes.contradiction.map_reduce_max_papers)
            cap = getattr(self.settings, "map_reduce_max_papers", 8)
            paper_summaries = await self._summarize_claims(by_paper, llm, cap)

            # cluster: LLM groups claims into consensus / disagreement / open
            clusters = await self._cluster_claims(request.query, paper_summaries, llm)

            # synthesize structured answer, streaming
            async for ev in self._synthesize(request.query, clusters, paper_summaries, llm):
                yield ev

            # emit source events for cited papers
            for pid, chunk_list in by_paper.items():
                if pid == "?":
                    continue
                md = (getattr(chunk_list[0], "metadata", {}) or {})
                yield StreamEvent(event="source", data=json.dumps({
                    "title": md.get("title"), "doi": md.get("doi"), "year": md.get("year"),
                    "relevance_score": getattr(chunk_list[0], "score", 0.0),
                }))
        except Exception as e:  # noqa: BLE001
            logger.error("contradiction_mode_error", error=str(e))
            yield StreamEvent(event="error", data=json.dumps({"message": str(e)}))

    # --- helpers: _retrieve, _summarize_claims, _cluster_claims, _synthesize, _fallback_answer ---
    # Implement _retrieve by copying the hybrid+rerank retrieval block from advanced.py (DRY: if advanced.py
    # exposes a reusable retrieval method, call it; otherwise replicate). _summarize_claims, _cluster_claims,
    # _synthesize use `await llm.complete(messages=[...])` with prompts added to rag/prompts.py.
```
Add to `rag/prompts.py`:
- `CONTRADICTION_CLAIM_SUMMARY_PROMPT` — "Summarize this paper's central claims relevant to: {query}. 2-4 bullet points."
- `CONTRADICTION_CLUSTER_PROMPT` — "Given these per-paper claim summaries about '{query}', output JSON {{'consensus': [...], 'disagreement': [{{'claim':..., 'papers':[doi,...]}}], 'open': [...]}}."
- `CONTRADICTION_SYNTHESIS_PROMPT` — "Write a structured brief with sections 'Points of consensus', 'Points of disagreement', 'Open / under-determined', citing papers by [Author, Year]. Clusters: {clusters}."

Register in `engine.py`: `from perspicacite.rag.modes import ContradictionRAGMode` and add `RAGMode.CONTRADICTION: ContradictionRAGMode(config)` to `self._modes`. Export from `rag/modes/__init__.py`.

- [ ] **Step 4: Run tests, expect pass**; run full `tests/unit/` to catch import regressions; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(rag): add contradiction-detection RAG mode"
```

---

## Task 3.3: Expose `contradiction` via MCP `generate_report` + chat router

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (`generate_report` `mode_map` → add `"contradiction": RAGMode.CONTRADICTION`; update the docstring's `mode:` description)
- Modify: `src/perspicacite/web/routers/chat.py` if it validates mode against a fixed set (add `contradiction`)
- Test: `tests/test_mcp_server.py`, `tests/unit/test_chat_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_generate_report_accepts_contradiction_mode(monkeypatch):
    from perspicacite.mcp import server as s
    # mock state + RAGEngine.query_stream to yield one content event; assert mode "contradiction" is routed
    ...
```
And in `test_chat_endpoint.py`, a request with `mode="contradiction"` is accepted (mock the engine).

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement** — add the mapping; ensure no `KeyError`/422 for `"contradiction"`.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat(api,mcp): expose contradiction mode in generate_report and chat"
```

---

## Task 3.4: Recency-weighted retrieval option

**Files:**
- Modify: `src/perspicacite/models/rag.py` (`RAGRequest`: `recency_weight: Optional[float] = None`, `recency_half_life_years: Optional[float] = None`)
- Create: `src/perspicacite/retrieval/recency.py` — pure function `apply_recency_weighting(chunks, recency_weight, half_life_years, current_year) -> list` that rescales each chunk's `.score`
- Modify: the mode handlers (or the shared retrieval seam) to call it after rerank/WRRF when `request.recency_weight`
- Modify: `src/perspicacite/mcp/server.py` (`generate_report` gains `recency_weight: float = 0.0`)
- Test: `tests/unit/test_recency.py` (new), `tests/unit/test_models.py`

- [ ] **Step 1: Write failing tests**

```python
def test_recency_request_fields():
    from perspicacite.models.rag import RAGRequest
    assert RAGRequest(query="x").recency_weight is None
    assert RAGRequest(query="x", recency_weight=0.5).recency_weight == 0.5


def test_apply_recency_weighting_reorders():
    from perspicacite.retrieval.recency import apply_recency_weighting
    class C:
        def __init__(self, year, score): self.metadata = {"year": year}; self.score = score
    chunks = [C(2010, 0.80), C(2024, 0.78)]
    out = apply_recency_weighting(list(chunks), recency_weight=0.6, half_life_years=5.0, current_year=2026)
    # 2024 paper should now outrank the 2010 paper
    assert out[0].metadata["year"] == 2024


def test_apply_recency_weighting_noop_when_zero():
    from perspicacite.retrieval.recency import apply_recency_weighting
    class C:
        def __init__(self, year, score): self.metadata = {"year": year}; self.score = score
    chunks = [C(2010, 0.80), C(2024, 0.10)]
    before = [(c.metadata["year"], c.score) for c in chunks]
    out = apply_recency_weighting(chunks, recency_weight=0.0, half_life_years=5.0, current_year=2026)
    assert [(c.metadata["year"], c.score) for c in out] == before
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement `recency.py`**

```python
"""Recency re-weighting of retrieved chunks (post-scoring re-rank)."""
from __future__ import annotations

import math
from typing import Any, Sequence

DEFAULT_HALF_LIFE_YEARS = 8.0


def _year_of(chunk: Any) -> int | None:
    md = getattr(chunk, "metadata", {}) or {}
    y = md.get("year")
    try:
        return int(y) if y else None
    except (TypeError, ValueError):
        return None


def apply_recency_weighting(
    chunks: Sequence[Any],
    recency_weight: float | None,
    half_life_years: float | None = None,
    current_year: int | None = None,
) -> list[Any]:
    """Blend each chunk's score with an exponential-decay recency factor, then re-sort desc.

    new_score = (1 - w) * old_score + w * (old_score * recency_factor)
              = old_score * (1 - w + w * recency_factor)
    recency_factor = 0.5 ** (age_years / half_life); papers with no year get factor 1.0 (neutral).
    w<=0 or None → no-op (returns the input order/scores unchanged).
    """
    chunks = list(chunks)
    if not recency_weight or recency_weight <= 0:
        return chunks
    w = min(1.0, float(recency_weight))
    hl = float(half_life_years or DEFAULT_HALF_LIFE_YEARS)
    import datetime as _dt
    cy = int(current_year or _dt.date.today().year)
    for c in chunks:
        y = _year_of(c)
        factor = 1.0 if y is None else 0.5 ** (max(0, cy - y) / hl)
        old = float(getattr(c, "score", 0.0) or 0.0)
        new = old * (1.0 - w + w * factor)
        try:
            c.score = new
        except Exception:  # noqa: BLE001
            pass
    chunks.sort(key=lambda c: float(getattr(c, "score", 0.0) or 0.0), reverse=True)
    return chunks
```

- [ ] **Step 4: Wire into modes** — after the final ranking in `basic`, `advanced`, `profound`, `agentic`, `contradiction` retrieval seams, add:
```python
if getattr(request, "recency_weight", None):
    from perspicacite.retrieval.recency import apply_recency_weighting
    chunks = apply_recency_weighting(chunks, request.recency_weight, getattr(request, "recency_half_life_years", None))
```
Add `recency_weight: float = 0.0` to MCP `generate_report` and pass it onto the `RAGRequest`.

- [ ] **Step 5: Run tests + full unit suite, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat(rag): add recency-weighted retrieval option"
```

---

## Task 3.5: Phase 3 wrap-up

- [ ] `AGENT_LOG.md`: dated entry — "Phase 3 — contradiction-detection RAG mode (RAGMode.CONTRADICTION) + recency-weighted retrieval option on RAGRequest."
- [ ] `ROADMAP.md`: tick "Contradiction detection mode", "Time-aware retrieval". Move to Completed archive.
- [ ] `CLAUDE.md`: add `contradiction` row to the RAG-modes table; mention `recency_weight` as a request option.
- [ ] `docs/rules/rag_development.md`: note the new mode and the recency option.
- [ ] Commit: `git add -A && git commit -m "docs: log Phase 3, update roadmap/CLAUDE/rag rules"`

---

# PHASE 4 — Multi-KB Query

> Additive at the engine surface. Single-KB path must be byte-for-byte unchanged when `kb_names` is unset or length 1.

## Task 4.1: `kb_names` on `RAGRequest` + `kb_name` on `SourceReference`

**Files:**
- Modify: `src/perspicacite/models/rag.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write failing test**

```python
def test_ragrequest_kb_names_and_source_kb():
    from perspicacite.models.rag import RAGRequest, SourceReference
    r = RAGRequest(query="x")
    assert r.kb_names is None
    r2 = RAGRequest(query="x", kb_names=["a", "b"])
    assert r2.kb_names == ["a", "b"]
    s = SourceReference(title="T", kb_name="a")
    assert s.kb_name == "a"
    assert SourceReference(title="T").kb_name is None
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement** — add `kb_names: Optional[List[str]] = None` to `RAGRequest`; add `kb_name: Optional[str] = None` to `SourceReference`.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(rag): add kb_names to RAGRequest and kb_name to SourceReference"`

---

## Task 4.2: Multi-KB retrieval helper

**Files:**
- Create: `src/perspicacite/retrieval/multi_kb.py` — `async def multi_kb_search(vector_store, embedding_provider, kb_metas, query, top_k) -> list[chunk]` that searches each collection, tags chunks with `metadata["kb_name"]`, merges by score, dedups by `metadata["paper_id"]` (keep highest score), returns top_k. Also `check_embedding_compat(kb_metas) -> str | None` returning a mismatch message or None.
- Test: `tests/unit/test_multi_kb.py` (new)

- [ ] **Step 1: Write failing tests**

```python
import pytest


def test_check_embedding_compat():
    from perspicacite.retrieval.multi_kb import check_embedding_compat
    class KB:  # minimal stand-in
        def __init__(self, name, model): self.name = name; self.embedding_model = model; self.collection_name = name
    assert check_embedding_compat([KB("a", "m1"), KB("b", "m1")]) is None
    msg = check_embedding_compat([KB("a", "m1"), KB("b", "m2")])
    assert msg and "m1" in msg and "m2" in msg


@pytest.mark.asyncio
async def test_multi_kb_search_merges_and_dedups(monkeypatch):
    from perspicacite.retrieval.multi_kb import multi_kb_search
    class Chunk:
        def __init__(self, pid, score, kb=None): self.metadata = {"paper_id": pid}; self.score = score; self.text = pid
    class FakeStore:
        async def search_collection(self, collection_name, query_embedding, top_k):  # adjust to real API
            if collection_name == "a": return [Chunk("p1", 0.9), Chunk("p2", 0.5)]
            return [Chunk("p1", 0.7), Chunk("p3", 0.6)]
    class FakeEmb:
        dimension = 3
        async def embed(self, text): return [0.0, 0.0, 0.0]
    class KB:
        def __init__(self, n): self.name = n; self.collection_name = n; self.embedding_model = "m"
    out = await multi_kb_search(FakeStore(), FakeEmb(), [KB("a"), KB("b")], "q", top_k=10)
    pids = [c.metadata["paper_id"] for c in out]
    assert pids[0] == "p1" and pids.count("p1") == 1  # dedup, highest score kept (0.9)
    assert set(pids) == {"p1", "p2", "p3"}
    assert all("kb_name" in c.metadata for c in out)
```
> Inspect `ChromaVectorStore` for the actual per-collection search method name/signature and adapt `FakeStore` + the helper accordingly.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement `multi_kb.py`** following the test contract. Use the existing `ChromaVectorStore` search primitive for a single collection in a loop; embed the query once.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(retrieval): add multi-KB search + embedding-compat check"`

---

## Task 4.3: Wire multi-KB into `RAGEngine` / modes

**Files:**
- Modify: `src/perspicacite/rag/engine.py` — before dispatching to a handler, if `request.kb_names` has length > 1: run `check_embedding_compat`; on mismatch yield/return an error (`StreamEvent(event="error", ...)` for `query_stream`; raise/return an error response for `query`). Otherwise, attach the merged chunk set so the handler uses it instead of single-KB retrieval.
- The cleanest seam: a `MultiKBRetriever` object that satisfies whatever retrieval protocol the modes expect, passed in place of `vector_store`. If modes call `vector_store.search(...)` directly, make `MultiKBRetriever.search(...)` fan out. Prefer this over editing every mode.
- Modify: handlers only if they can't transparently use the wrapper.
- Test: `tests/unit/test_engine_multi_kb.py` (new)

- [ ] **Step 1: Write failing tests**

```python
import pytest


@pytest.mark.asyncio
async def test_engine_single_kb_unchanged(monkeypatch):
    # With kb_names=None, engine.query_stream behaves exactly as before (one KB).
    ...

@pytest.mark.asyncio
async def test_engine_multi_kb_mismatch_yields_error(monkeypatch):
    from perspicacite.rag.engine import RAGEngine
    # session metadata lookups return KBs with different embedding_model -> error event
    ...

@pytest.mark.asyncio
async def test_engine_multi_kb_merges(monkeypatch):
    # two compatible KBs -> handler sees merged, kb_name-tagged chunks; source events carry kb_name
    ...
```
> These need the engine's KB-metadata lookup seam. `RAGEngine` currently takes `kb_name` via `request`; multi-KB needs metadata for each name. If `RAGEngine` doesn't have a session store, pass `kb_metas` in via the request resolution layer (the router/MCP builds them) — adjust the design to whatever's least invasive and document it in the task as you discover it.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement.** Keep single-KB path untouched. Add the `MultiKBRetriever` wrapper. Ensure `source` events get `kb_name` from `chunk.metadata["kb_name"]`.

- [ ] **Step 4: Run tests + full unit suite, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(rag): multi-KB query fan-out + merge in RAGEngine"`

---

## Task 4.4: Expose multi-KB via API + MCP

**Files:**
- Modify: `src/perspicacite/web/routers/chat.py` (accept `kb_names`), `src/perspicacite/mcp/server.py` (`generate_report` + `search_knowledge_base` gain optional `kb_names: list[str] | None = None`; multi-KB `search_knowledge_base` returns chunks tagged with `kb_name`)
- Test: `tests/unit/test_chat_endpoint.py`, `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests** — chat request with `kb_names=["a","b"]` is accepted and routed; `generate_report(kb_names=[...])` returns sources with `kb_name`; `search_knowledge_base(kb_names=[...])` returns multi-KB chunks. Mock the engine/vector store.
- [ ] **Step 2: Run, expect fail.**
- [ ] **Step 3: Implement** — thread `kb_names` through; build `kb_metas` from `session_store`/`mcp_state.session_store`; on mismatch return a clear error JSON / 400.
- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(api,mcp): accept kb_names for multi-KB queries"`

---

## Task 4.5: Phase 4 wrap-up

- [ ] `AGENT_LOG.md`: dated entry — "Phase 4 — multi-KB query: RAGRequest.kb_names, MultiKBRetriever fan-out/merge/dedup, kb_name source attribution; embedding-model-mismatch guard."
- [ ] `ROADMAP.md`: tick "Multi-KB query". Move to Completed archive.
- [ ] `CLAUDE.md`: note multi-KB support in the Retrieval / RAG sections.
- [ ] Commit: `git add -A && git commit -m "docs: log Phase 4, update roadmap/CLAUDE"`

---

# PHASE 5 — Web UI & Observability

> Read `docs/rules/api_web.md`. Static assets: `static/css/` (6 files), `static/js/` (8 files), SPA shell `templates/index.html`. The agent cannot visually QA — write router + JS-logic tests where feasible and create `MANUAL_QA.md` at repo root with a click-through checklist. After editing static assets, the human must hard-refresh (Cmd+Shift+R); note that in `MANUAL_QA.md`.

## Task 5.1: `GET /api/kb/{name}/stats` endpoint

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_kb_stats_endpoint.py` (new)

- [ ] **Step 1: Write failing tests**

```python
import pytest
from fastapi.testclient import TestClient


def test_kb_stats_not_found(client, monkeypatch):  # reuse the TestClient fixture pattern from test_web_app_routes.py
    r = client.get("/api/kb/nope/stats")
    assert "not found" in str(r.json()).lower()


def test_kb_stats_aggregates(client, monkeypatch):
    # mock session_store.get_kb_metadata -> KB(collection_name="c", embedding_model="m", paper_count=2)
    # mock vector_store collection .get(include=["metadatas"]) -> metadatas with years/sources/content_type
    r = client.get("/api/kb/default/stats")
    body = r.json()
    assert body["paper_count"] >= 1
    assert "by_year" in body and isinstance(body["by_year"], dict)
    assert "by_source" in body and "by_content_type" in body
    assert body["embedding_model"] == "m"
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
@router.get("/api/kb/{name}/stats")
async def get_kb_stats(name: str):
    """Aggregate statistics for a KB (computed from ChromaDB metadata + SQLite KB record)."""
    if not app_state.session_store:
        return {"error": "System not initialized"}
    kb = await app_state.session_store.get_kb_metadata(name)
    if not kb:
        return {"error": f"Knowledge base '{name}' not found"}
    coll = app_state.vector_store.client.get_collection(name=kb.collection_name)
    total_chunks = coll.count()
    # Scan metadata (capped) to aggregate per-paper.
    SCAN_CAP = 20000
    got = coll.get(limit=min(total_chunks, SCAN_CAP), include=["metadatas"])
    metas = got.get("metadatas") or []
    by_year: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_content_type: dict[str, int] = {}
    by_journal: dict[str, int] = {}
    seen_papers: set[str] = set()
    for m in metas:
        pid = m.get("paper_id")
        if not pid or pid in seen_papers:
            continue
        seen_papers.add(pid)
        y = str(m.get("year") or "unknown")
        by_year[y] = by_year.get(y, 0) + 1
        src = str(m.get("source") or "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        ct = str(m.get("content_type") or "unknown")
        by_content_type[ct] = by_content_type.get(ct, 0) + 1
        j = (m.get("journal") or "").strip()
        if j:
            by_journal[j] = by_journal.get(j, 0) + 1
    top_journals = sorted(by_journal.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "name": kb.name,
        "paper_count": len(seen_papers) or kb.paper_count,
        "chunk_count": total_chunks,
        "by_year": dict(sorted(by_year.items())),
        "by_source": by_source,
        "by_content_type": by_content_type,
        "top_journals": [{"journal": j, "count": c} for j, c in top_journals],
        "embedding_model": kb.embedding_model,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "scanned_chunks": len(metas),
        "scan_capped": total_chunks > SCAN_CAP,
    }
```
> If chunks don't currently store `content_type`/`journal` in metadata, the histograms will just be `{"unknown": n}` — that's acceptable; Task 5.2 backfills `content_type` into chunk metadata going forward, but don't block on a migration.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(api): add GET /api/kb/{name}/stats"`

---

## Task 5.2: KB stats UI panel

**Files:**
- Create: `static/js/kb_stats.js`
- Create: `static/css/kb_stats.css` (or append to `kb.css`)
- Modify: `templates/index.html` (add a "Stats" tab/button in the KB view; `<link>` the CSS; `<script>` the JS; ensure `main.js` calls the init)
- Modify: `static/js/main.js` (wire init/event)
- Test: `tests/unit/test_static_assets.py` (assert new files exist and are referenced)

- [ ] **Step 1: Write the failing test** — extend `test_static_assets.py`:
```python
def test_kb_stats_assets_present():
    from pathlib import Path
    assert Path("static/js/kb_stats.js").exists()
    html = Path("templates/index.html").read_text()
    assert "kb_stats.js" in html
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

`kb_stats.js`: a function `loadKbStats(kbName)` that fetches `/api/kb/${kbName}/stats`, renders into a container: a simple inline-SVG bar chart for `by_year` (no chart library), and small tables for `by_source`, `by_content_type`, `top_journals`. Follow the module style of the other `static/js/*.js` files (look at `kb.js`). Add a "Stats" tab button to the KB detail view in `index.html`; on click → `loadKbStats(currentKb)`. Add a `<link rel="stylesheet" href="/static/css/kb_stats.css">` (and the file) plus `<script src="/static/js/kb_stats.js"></script>` near the other script tags. Hook the tab in `main.js` if that's where tab wiring lives.

- [ ] **Step 4: Run tests, expect pass.** Manually: not possible (no browser) — add a line to `MANUAL_QA.md` (created in Task 5.7).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(web): KB statistics panel"`

---

## Task 5.3: `GET /api/paper` detail endpoint

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py` (or a small new `src/perspicacite/web/routers/papers.py` registered in `web/app.py` — match existing router-registration style)
- Test: `tests/unit/test_paper_endpoint.py` (new)

- [ ] **Step 1: Write failing tests**

```python
def test_paper_endpoint_missing_doi(client):
    r = client.get("/api/paper")
    assert r.status_code in (400, 422)


def test_paper_endpoint_cache_hit(client, monkeypatch, tmp_path):
    # monkeypatch the per-DOI cache reader (perspicacite.pipeline.download.unified._load_cached_references or
    # whatever caches PaperContent) to return a cached record; assert no live fetch happens.
    ...


def test_paper_endpoint_live_fetch(client, monkeypatch):
    # monkeypatch retrieve_paper_content -> PaperContent(success=True, content_type="abstract",
    #   content_source="openalex", abstract="A", metadata={"title":"T","authors":["X"],"year":2020})
    r = client.get("/api/paper", params={"doi": "10.1/x"})
    body = r.json()
    assert body["doi"] == "10.1/x" and body["content_type"] == "abstract"
    assert body["title"] == "T" and body["abstract"] == "A"
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
@router.get("/api/paper")
async def get_paper_detail(doi: str):
    """Discovery metadata + abstract + which content type is available, for a DOI. Uses the per-DOI
    JSON cache in ./data/papers/ when present (cheap path) before doing a live fetch."""
    if not doi or not doi.strip():
        raise HTTPException(status_code=400, detail="doi query param required")
    doi = doi.strip().replace("https://doi.org/", "")
    # 1) cheap path: cached reference json (if the project caches PaperContent per DOI)
    cached = _read_cached_paper(doi)  # implement: read ./data/papers/<safe(doi)>.json if it exists
    if cached:
        return cached
    # 2) live fetch via the unified pipeline (short timeout)
    from perspicacite.pipeline.download import retrieve_paper_content
    pdf_kw = _get_pdf_fallback_kwargs(app_state.config.pdf_download if app_state.config else None)
    try:
        result = await retrieve_paper_content(doi, pdf_parser=app_state.pdf_parser, **pdf_kw)
    except Exception as e:  # noqa: BLE001
        return {"doi": doi, "error": str(e), "content_type": "none"}
    md = result.metadata or {}
    return {
        "doi": doi,
        "title": md.get("title"),
        "authors": md.get("authors") or [],
        "year": md.get("year"),
        "journal": md.get("journal"),
        "abstract": result.abstract or md.get("abstract"),
        "content_type": result.content_type,
        "content_source": result.content_source,
        "has_full_text": bool(result.full_text),
        "references_count": len(result.references or []) if result.references else 0,
    }
```
Implement `_read_cached_paper(doi)` reading `./data/papers/` (look at `unified.py` for the existing cache filename scheme; reuse it). If there's no per-DOI PaperContent cache (only a references cache), it's fine to skip the cheap path and always do the live fetch — just keep the timeout short.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(api): add GET /api/paper detail endpoint"`

---

## Task 5.4: Paper detail panel + pipeline-step badges in chat results

**Files:**
- Create: `static/js/paper_detail.js`
- Modify: `static/js/chat.js` (render a small badge on each paper card from `content_type`/`content_source` in the `source` event payload; click → open detail panel via `paper_detail.js`)
- Modify: `static/css/chat.css` (badge styles, panel styles)
- Modify: `templates/index.html` (script/link tags; a `<div id="paper-detail-panel">` modal/sidebar container)
- Modify (if needed): the `source` stream event payload to include `content_type`/`content_source` — check `advanced.py`/`base.py`/`engine.py` where `source` events are built; if those fields aren't present, add them where the chunk metadata has them
- Test: `tests/unit/test_static_assets.py`; if any Python changes to the `source` payload, a unit test asserting the new keys appear

- [ ] **Step 1: Write the failing test**
```python
def test_paper_detail_assets_present():
    from pathlib import Path
    assert Path("static/js/paper_detail.js").exists()
    html = Path("templates/index.html").read_text()
    assert "paper_detail.js" in html and "paper-detail-panel" in html
```
Plus, if you touch the `source` payload: a unit test that a streamed `source` event JSON includes `content_type` (mock the retrieval so chunks carry it).

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement** — `paper_detail.js`: `openPaperDetail(doi)` fetches `/api/paper?doi=...` and fills `#paper-detail-panel` (title, authors, year, journal, abstract, content-type badge, references count). In `chat.js`, when rendering a source/paper card, add `<span class="pipeline-badge pipeline-${ct}">${label}</span>` where `ct ∈ {structured, full_text, abstract, none}` and clicking the card calls `openPaperDetail(doi)`. CSS for the badges (color-coded) and the panel. Add tags to `index.html`.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean. Add `MANUAL_QA.md` lines.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(web): paper detail panel + pipeline-step badges"`

---

## Task 5.5: Conversation full-text search

**Files:**
- Modify: `src/perspicacite/memory/session_store.py` (in `init_db()`: idempotently create an FTS5 virtual table over conversation messages + triggers to keep it in sync; backfill once if empty. Detect FTS5 availability; if absent, skip the vtable and rely on a `LIKE` query path)
- Modify: `src/perspicacite/web/routers/conversations.py` (new `GET /api/conversations/search?q=...`)
- Test: `tests/unit/test_conversation_search.py` (new) — uses a temp SQLite DB

- [ ] **Step 1: Write failing tests**

```python
import pytest


@pytest.mark.asyncio
async def test_conversation_search_fts(tmp_path):
    from perspicacite.memory.session_store import SessionStore
    store = SessionStore(tmp_path / "t.db")
    await store.init_db()
    # create a conversation with messages mentioning "photosynthesis" — use the store's existing API
    conv_id = await store.create_conversation(...)  # adjust to real signature
    await store.add_message(conv_id, role="user", content="Tell me about photosynthesis in algae")  # adjust
    results = await store.search_conversations("photosynthesis")
    assert any(r["id"] == conv_id for r in results)
    assert "snippet" in results[0]
    assert await store.search_conversations("nonexistentword") == []
```
> Adapt to `SessionStore`'s real method names for creating conversations/messages. If those don't exist as discrete methods, search at the SQL level inside `search_conversations`.

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

In `session_store.py` `init_db()`, after the existing table creation:
```python
        # Full-text search over conversation messages (FTS5 if available).
        try:
            await db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, conversation_id UNINDEXED)")
            self._fts_available = True
        except Exception:  # noqa: BLE001
            self._fts_available = False
        if getattr(self, "_fts_available", False):
            # one-time backfill if empty
            cur = await db.execute("SELECT count(*) FROM messages_fts")
            (n,) = await cur.fetchone()
            if n == 0:
                await db.execute("INSERT INTO messages_fts(content, conversation_id) SELECT content, conversation_id FROM messages")  # adjust table/col names
            await db.commit()
```
Add a method:
```python
    async def search_conversations(self, query: str, limit: int = 20) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = []
            if getattr(self, "_fts_available", False):
                cur = await db.execute(
                    "SELECT conversation_id, snippet(messages_fts, 0, '[', ']', '…', 10) AS snippet "
                    "FROM messages_fts WHERE messages_fts MATCH ? LIMIT ?", (query, limit))
                rows = await cur.fetchall()
            else:
                like = f"%{query}%"
                cur = await db.execute(
                    "SELECT conversation_id, substr(content, 1, 200) AS snippet FROM messages WHERE content LIKE ? LIMIT ?", (like, limit))
                rows = await cur.fetchall()
            # dedup by conversation_id, join conversation titles
            seen, out = set(), []
            for r in rows:
                cid = r["conversation_id"]
                if cid in seen:
                    continue
                seen.add(cid)
                meta = await self.get_conversation(cid)  # adjust to real getter; may return title/created_at
                out.append({"id": cid, "title": getattr(meta, "title", None) if meta else None, "snippet": r["snippet"]})
            return out
```
> Adjust every table/column name to match the real schema in `session_store.py` (it may store messages as JSON on the conversation row rather than a `messages` table — if so, do a JSON-aware `LIKE` scan and skip FTS, or extract message text into the FTS table at write time). Read the file carefully before implementing.

Router:
```python
@router.get("/api/conversations/search")
async def search_conversations(q: str):
    if not app_state.session_store:
        return {"results": []}
    if not q or not q.strip():
        return {"results": []}
    return {"results": await app_state.session_store.search_conversations(q.strip())}
```

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(api): conversation full-text search (FTS5 + LIKE fallback)"`

---

## Task 5.6: Conversation Markdown export

**Files:**
- Modify: `src/perspicacite/web/routers/conversations.py` (new `GET /api/conversations/{id}/export?format=markdown`)
- Test: `tests/unit/test_conversation_export.py` (new)

- [ ] **Step 1: Write failing test**

```python
def test_conversation_export_markdown(client, monkeypatch):
    # mock session_store.get_conversation(id) to return a conversation with 2 turns + sources
    r = client.get("/api/conversations/abc/export", params={"format": "markdown"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    body = r.text
    assert "# " in body  # has a title heading
    assert "## " in body  # has turn headings
    assert "References" in body or "Sources" in body
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement**

```python
from fastapi.responses import PlainTextResponse

@router.get("/api/conversations/{conversation_id}/export")
async def export_conversation(conversation_id: str, format: str = "markdown"):
    if not app_state.session_store:
        raise HTTPException(status_code=503, detail="System not initialized")
    conv = await app_state.session_store.get_conversation(conversation_id)  # adjust to real getter
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if format != "markdown":
        raise HTTPException(status_code=400, detail="Only format=markdown is supported")
    lines: list[str] = []
    title = getattr(conv, "title", None) or f"Conversation {conversation_id}"
    lines.append(f"# {title}\n")
    created = getattr(conv, "created_at", None)
    if created:
        lines.append(f"_Exported from Perspicacité — created {created}_\n")
    all_sources: list[dict] = []
    for turn in _iter_turns(conv):  # implement: yield {question, answer, sources:[...]} per turn from the stored shape
        lines.append(f"## {turn['question']}\n")
        lines.append(turn["answer"].rstrip() + "\n")
        if turn.get("sources"):
            lines.append("**Sources:** " + ", ".join(
                f"[{s.get('title') or s.get('doi') or '?'}]" + (f"(https://doi.org/{s['doi']})" if s.get('doi') else "")
                for s in turn["sources"]) + "\n")
            all_sources.extend(turn["sources"])
    if all_sources:
        lines.append("\n## References\n")
        seen = set()
        for s in all_sources:
            key = s.get("doi") or s.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            doi = s.get("doi")
            lines.append(f"- {s.get('title') or doi}" + (f" — https://doi.org/{doi}" if doi else ""))
    md = "\n".join(lines) + "\n"
    return PlainTextResponse(md, media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="conversation-{conversation_id}.md"'})
```
> Implement `_iter_turns` against the real stored conversation shape (read `session_store.py` / `models/messages.py`). If messages are a flat list of role/content, pair user→assistant turns; sources may be attached to assistant messages or stored separately — handle whatever's there.

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(api): conversation Markdown export"`

---

## Task 5.7: Conversation search/export UI + advanced-options UI + mode picker + MANUAL_QA

**Files:**
- Create: `static/js/conversation_search.js` (or extend `conversations.js`)
- Modify: `static/js/conversations.js` (search box wiring, "Export ⤓" button per conversation → `window.location = /api/conversations/${id}/export?format=markdown`)
- Modify: `static/js/mode.js` (add `contradiction` to the mode picker options)
- Modify: `static/js/chat.js` (an "Advanced options" disclosure: BM25/vector weight sliders → send `bm25_weight`/`vector_weight`; recency slider → `recency_weight`; KB multi-select → `kb_names`; all optional, defaults preserve behavior)
- Modify: `templates/index.html` (markup for the search box, advanced-options block, script/link tags)
- Modify: `static/css/*` as needed
- Create: `MANUAL_QA.md` (repo root) — checklist
- Modify: `tests/unit/test_static_assets.py`, `tests/unit/test_web_app_routes.py` (assert new assets referenced; new endpoints reachable)

- [ ] **Step 1: Write failing tests**
```python
def test_phase5_assets_and_routes(client):
    from pathlib import Path
    html = Path("templates/index.html").read_text()
    assert "conversation_search.js" in html
    assert "contradiction" in Path("static/js/mode.js").read_text()
    # routes exist:
    assert client.get("/api/conversations/search", params={"q": "x"}).status_code in (200, 503)
```

- [ ] **Step 2: Run, expect fail.**

- [ ] **Step 3: Implement** the UI pieces (follow the existing `static/js/*.js` module conventions; no new JS deps). Add the chat-request payload fields when the advanced options are non-default. Create `MANUAL_QA.md`:
```markdown
# Manual QA Checklist — v2.x Multi-Feature Expansion

Run `uv run perspicacite -c config.yml serve`, open the UI, hard-refresh (Cmd+Shift+R).

## Phase 5 UI
- [ ] KB view → "Stats" tab shows a by-year bar chart + source/content-type/journal tables for a non-empty KB.
- [ ] Chat results: each paper card shows a pipeline badge (structured / full text / abstract / none). Clicking a card opens the paper detail panel with metadata + abstract.
- [ ] Conversations sidebar: typing in the search box returns matching conversations with snippets. "Export ⤓" downloads a readable `.md`.
- [ ] Mode picker includes "contradiction"; running it on a KB with ≥3 relevant papers yields a consensus/disagreement/open structure. On a tiny KB it degrades to a normal answer without error.
- [ ] Advanced options: moving BM25/vector sliders, the recency slider, and selecting multiple KBs changes results sensibly; with everything at default, behavior matches before.

## Backend smoke (optional)
- [ ] `uv run perspicacite -c config.yml screen-papers --input a.bib --candidates b.bib --output out.bib --threshold 0.0`
- [ ] `uv run perspicacite -c config.yml pubmed-search "crispr" --max 3 --email you@example.org`
- [ ] `POST /api/kb/<name>/dois` with `{"dois": ["10.1101/..."]}` adds the preprint (bioRxiv path).
```

- [ ] **Step 4: Run tests, expect pass**; `ruff`/`mypy` clean.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(web): conversation search/export UI, advanced query options, contradiction in mode picker; add MANUAL_QA.md"`

---

## Task 5.8: Phase 5 wrap-up

- [ ] `AGENT_LOG.md`: dated entry — "Phase 5 — KB stats endpoint+panel, /api/paper detail endpoint + detail panel + pipeline badges, conversation FTS search + Markdown export, advanced query-options UI, contradiction in mode picker, MANUAL_QA.md."
- [ ] `ROADMAP.md`: tick "Paper detail panel", "KB statistics dashboard", "Conversation search", "Retry / fallback logging — surface which pipeline step succeeded per paper", "No conversation export" (→ markdown export shipped; Obsidian vault still deferred), "LLM-tunable BM25/vector weights exposed in UI". Move to Completed archive.
- [ ] `CLAUDE.md`: add the new endpoints under the Web App section; note the new static files; bump the static-file counts.
- [ ] `README.md`: mention the new MCP tools (10 total), new CLI subcommands, new RAG mode.
- [ ] Commit: `git add -A && git commit -m "docs: log Phase 5, update roadmap/CLAUDE/README"`

---

# Final wrap-up (after all phases, or after the last completed phase)

- [ ] Run the full gate one more time on the final state:
  ```bash
  uv run pytest tests/unit/ -m "not live" -q
  uv run ruff check src/ tests/
  uv run ruff format --check src/ tests/
  uv run mypy src/
  ```
  All must be green.
- [ ] Confirm `AGENT_LOG.md`, `ROADMAP.md`, `CLAUDE.md`, `docs/perspicacite_skills.md`, `README.md`, `config.example.yml`, `MANUAL_QA.md` all reflect the final state.
- [ ] If working on a branch, ensure each phase's PR is open (or one combined PR if the chosen workflow batched them).
- [ ] In `AGENT_LOG.md`, write a short "Stopped at Phase N because …" note if not all six phases were completed.

---

## Self-Review Notes (for the plan author — already applied)

- **Spec coverage:** every numbered section of the spec maps to a task — §3 → Phase 0 (Tasks 0.1–0.6); §4 → Phase 1 (1.1–1.5); §5 → Phase 2 (2.1–2.7); §6 → Phase 3 (3.1–3.5); §7 → Phase 4 (4.1–4.5); §8 → Phase 5 (5.1–5.8); §9 cross-cutting rules → "Global Rules" + per-phase wrap-up tasks; §10 risks → reflected in the "additive-only / STOP" rule and graceful-degrade tasks; §11 done-criteria → "Final wrap-up".
- **Known soft spots flagged in-task (the implementing agent must resolve against real code):** the JATS parser reuse in 1.1; the config-access pattern in `cli.py` for 2.5; the `ChromaVectorStore` per-collection search API in 4.2/4.3; the `RAGEngine` KB-metadata seam in 4.3; the `SessionStore` schema (messages table vs JSON blob) in 5.5/5.6; whether chunk metadata carries `content_type`/`journal` in 5.1/5.2/5.4. Each task says "read the file and adapt" rather than guessing a signature that may not exist.
- **Types:** `ScreenResult` (2.1) is used consistently in 2.2/2.3; `PaperContent`/`PaperDiscovery` shapes referenced from `base.py`; `resolve_hybrid_weights`/`apply_recency_weighting`/`multi_kb_search`/`check_embedding_compat` signatures match between their definition task and their wiring task.
