# Test-suite baseline — 2026-05-14

Baseline snapshot taken as Wave 1.1 of the framework-hardening
roadmap (`docs/roadmap-2026-05-followups.md`). This document records
the state of the existing 141-file test suite as-is — **no test
code or source code was modified**, no tests were skipped via
decorator. The numbers below tell future contributors which tests
are trustworthy and which need triage.

## Headline numbers

| Metric | Value |
|---|---|
| Total collected (after skip list) | **966 tests** |
| Unit tests run (`tests/unit/`) | **881** (after skipping 1 RAM-killer) |
| Unit tests passed | **869** |
| Unit tests failed | **12** |
| Wall time (unit) | 94 s (signal-method timeout=15 s) |
| Ruff findings | **1846 errors** (1279 auto-fixable) |

## Skipped outright — DO NOT RUN as part of the default suite

These were excluded by path because they instantiate heavy ML models
or hit live external APIs. Running any of them without explicit
intent will spike RAM (multi-GB) or burn rate-limit budget. They
are NOT failures — they're tests with side effects the audit chose
not to incur.

| File | Why skipped |
|---|---|
| `tests/unit/test_embeddings.py` | Instantiates `SentenceTransformerEmbeddingProvider(model="all-MiniLM-L6-v2")` 6+ times. Each `__init__` loads PyTorch + the model weights into RAM (2–4 GB resident per fixture). Primary RAM-blowup vector. |
| `tests/test_mcp_live.py` | Opens `chromadb.PersistentClient` (loads default embedding fn → sentence-transformers) AND makes live MCP calls. |
| `tests/test_anthropic_api.py` | Live calls to the Anthropic API (`ANTHROPIC_API_KEY` required). |
| `tests/test_publisher_api_live.py` | Live calls to publisher APIs. |
| `tests/test_download_real.py` | Real PDF downloads. |
| `tests/test_download_with_bibtex.py` | Real PDF downloads. |
| `tests/test_mcp_server.py` | Full MCP server boot — heavier than the smoke test we want for Wave 1.3. |

These are not bad tests — they're integration / live tests that
belong in a separate suite gated by env-var or marker. **Wave 6.1
(E2E pipeline tests)** will formalise that separation.

Two additional `tests/unit/` files were skipped because they make
real HTTP calls without mocking despite being labelled "unit":

| File | Real HTTP target |
|---|---|
| `tests/unit/test_capsule_builder_orchestrator.py` | europepmc + doi.org + acs.org |
| `tests/unit/test_fetch_doi_lookups.py` | DOI resolver |

These should either be moved out of `tests/unit/` or refactored to
mock the network layer.

## Failures — categorised by root cause

All 12 failures fall into three buckets. None are RAM-related; all
are test-staleness or unmocked-dependency issues.

### A. Production hang in chunking — `chunking.py:74` (7 tests)

The `_chunk_by_tokens` function in
`src/perspicacite/pipeline/chunking.py` hangs for >60 s even on a
trivial test input. This is **a real production bug**, not test
flakiness — verified by running one test in isolation with a 60 s
timeout. Worth a dedicated debugging pass (likely Wave 3 reliability).

| Test | Note |
|---|---|
| `test_capsule_reader_ingest.py::test_ingest_chunks_blocks_jsonl` | Timeout in chunking.py:77 |
| `test_capsule_reader_ingest.py::test_ingest_propagates_resource_ids` | Timeout in chunking.py:77 |
| `test_capsule_reader_ingest.py::test_ingest_finalize_false_skips_finish` | Timeout in chunking.py:77 |
| `test_chunking_dispatch.py::test_chunk_falls_back_when_flag_disabled` | Timeout in chunking.py:74 — hangs >60 s standalone |
| `test_local_docs_external_annotation.py::test_ipynb_outputs_stripped_before_chunking` | Timeout in chunking.py:74 |
| `test_capsule_reader_ingest.py` × 1 more | Same path |
| `test_local_docs_capsule_reader_route` (1 of the 2 below — partial) | Indirect via ingest |

### B. Mock signature drift (3 tests)

Production code added a parameter that test mocks don't accept.

| Test | Drift |
|---|---|
| `test_local_docs_capsule_reader_route.py::test_non_capsule_paths_route_to_files` | `fake_ingest_files()` got unexpected kwarg `external_metadata` |
| `test_local_docs_capsule_reader_route.py::test_mixed_inputs_route_to_both` | Same drift |
| `test_provenance_engine_wiring.py::test_mcp_generate_report_wires_provenance_and_message_id` | `query_stream` was never invoked — mock-wiring drift |

### C. Pydantic / fixture mismatches (2 tests)

| Test | Issue |
|---|---|
| `test_mcp_multi_kb_passthrough.py::test_generate_report_passes_kb_names_to_rag_request` | `RAGRequest(provider=MagicMock)` — Pydantic now requires `str`. Mocks need to return strings, not bare MagicMocks. |
| `test_mcp_multi_kb_passthrough.py::test_generate_report_single_kb_names_collapses_to_kb_name` | Same issue |

### D. Stale fixture attribute (2 tests)

| Test | Issue |
|---|---|
| `test_zotero_ingest_worker.py::test_worker_dedups_by_doi_and_attaches_notes` | `'types.SimpleNamespace' object has no attribute 'capsule'` — production added `.capsule` attr the fixture omits |
| `test_zotero_ingest_worker.py::test_worker_skips_existing_doi` | Same issue |

## Healthy test foundation — 869 passing tests

The 869 passing unit tests are the trustworthy core. Notable
well-covered modules:

- `capsule_builder_blocks`, `capsule_builder_figures`,
  `capsule_builder_metadata`, `capsule_builder_resources`,
  `capsule_paper_lookup` (extraction + capsule pipeline, mocked
  cleanly)
- `agentic_phase1` through `agentic_phase4` + `agentic_concurrency`
  + `agentic_chat_provenance` + `agentic_orchestrator_provenance`
  (agentic mode coverage)
- `multi_kb` family (8 files — routing, fanout, advanced /
  profound / literature-survey variants)
- `provenance_collector`, `provenance_store`, `provenance_endpoints`,
  `provenance_engine_wiring` (the provenance subsystem — well-mocked)
- `biorxiv`, `crossref`, `europepmc`, `pubmed`, `semantic_scholar`,
  `zotero_client_read`, `external_http` (API clients — use `respx`
  for HTTP mocking)
- `bibtex_kb`, `obsidian_export`, `rocrate_export`,
  `conversation_export` (data export paths)
- `llm_client`, `llm_client_provenance`, `providers` (LLM routing —
  mocked, includes the new `agent_cli` path)
- `chroma_store`, `tokens`, `hybrid_module`, `recency`, `reranker`,
  `screening`, `section_splitter`, `chunking_dispatch` (retrieval +
  chunking infra — mostly passing apart from the chunking hang)

## Ruff state — 1846 findings

By rule (top 10):

| Count | Rule | Description |
|---|---|---|
| 579 | W293 | blank-line-with-whitespace (auto-fixable) |
| 298 | E501 | line-too-long |
| 250 | UP006 | non-pep585-annotation (`List[X]` → `list[X]`) |
| 171 | UP045 | non-pep604-annotation-optional (`Optional[X]` → `X \| None`) |
| 122 | I001 | unsorted-imports (auto-fixable) |
| 118 | F401 | unused-import (auto-fixable with `--unsafe-fixes`) |
| 41 | UP035 | deprecated-import |
| 25 | W291 | trailing-whitespace (auto-fixable) |
| 22 | UP015 | redundant-open-modes (auto-fixable) |
| 14 | F541 | f-string-missing-placeholders |

1279 of 1846 (69 %) are auto-fixable with `ruff check --fix`.
A one-shot lint pass would clean most of these, but doing it now
would explode the diff for an audit commit; tracked as a future
chore.

## How to reproduce

```bash
source .venv/bin/activate

# Install dev deps (one-time)
pip install pytest pytest-asyncio pytest-cov pytest-timeout ruff

# Run unit tests with the skip list
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line

# Ruff
ruff check src/ tests/ --statistics
```

**Important:** always use `--timeout-method=signal` (not the default
`thread`). The thread method doesn't reliably interrupt async tests,
which causes the suite to wedge on the `chunking.py:74` hang.

## Implications for the roadmap

- **Wave 1.5 (CI)**: must use `signal` timeout, must apply the skip
  list above. The 12 failures should fail CI until they're fixed,
  to avoid hiding regressions under "they were already broken."
- **Wave 3 (reliability)** should adopt the chunking hang as a
  first-class debugging target — it's a real bug, not a test issue.
- **Wave 3.4 (error-path audit)** should mock the network layer
  cleanly so unit tests can never accidentally hit real URLs.
- **Wave 6.1 (E2E)** should formalise the `live` / `real` /
  `download` markers as a separate gated suite, not files in `tests/`.
