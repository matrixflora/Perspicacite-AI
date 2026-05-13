# Provenance & Reproducibility + Mode/Infra Completeness — Design Spec

**Date:** 2026-05-13
**Status:** Approved (brainstorming complete; ready for implementation planning)
**Author:** brainstormed with Claude Code

---

## Goal

Add a provenance & reproducibility layer to Perspicacité — traceable answers, an LLM-call audit trail, and an RO-Crate-flavored "research answer bundle" export, inspired by AgenticScienceBuilder's `llm_calls.jsonl` audit log, RO-Crate capsules, and provenance-first design — and close the documented RAG-mode and infrastructure gaps. Delivered as **one phased, additive bundle** executed task-by-task by subagents, committed per task directly to `main` (same shape as the 2026-05-12 multi-feature v2.x run).

---

## Scope

### In scope — five phases

**P1 — Provenance core.**
A `ProvenanceCollector` threaded through each RAG request via a `contextvar`, so the shared `AsyncLLMClient` can append to it without signature changes. Per answer it records: RAG mode + request params (`kb_names`, `recency_weight`, `recency_half_life_years`, hybrid weights, top-k), retrieved papers/chunks (score, pipeline-step / content-type, KB, rank, stage label), and a free-form ordered `mode_trace` (planning steps, intent classification, iterations, replans). Stored in a new SQLite `provenance` table keyed by message id. Heavy LLM payloads are deferred to the JSONL sidecar introduced in P2.

**P2 — LLM-call audit + RO-Crate export.**
Every `AsyncLLMClient.complete()` / `.stream()` call, when a collector is active, appends a record to `data/provenance/<conversation_id>.jsonl` (the ASB `llm_calls.jsonl` analogue): stage label, provider/model, **full rendered prompt messages**, **full response text**, prompt/completion token counts, latency, timestamp. The SQLite `provenance` row keeps a lightweight `llm_calls_index` (stage, model, token counts, latency, ts, byte offset into the JSONL) — no prompt/response text in the DB. New export `GET /api/conversations/{conv_id}/export?format=ro-crate` → a zip containing `ro-crate-metadata.json` (RO-Crate 1.1-flavored JSON-LD, **not** SHACL-validated), `conversation.md` (reuses the existing markdown renderer), a `provenance/` directory (`answer-<n>.json` per answer + a copy of the llm-calls JSONL), and `sources.json` (flat manifest of all cited papers).

**P3 — RAG mode wiring.**
Wire `recency_weight` / `recency_half_life_years` and `kb_names` (multi-KB query) into `advanced`, `profound`, `agentic`, and `literature_survey` so all six RAG modes honor them consistently — using the existing `apply_recency_weighting`, a new paper-dict variant `apply_recency_weighting_to_papers`, `resolve_hybrid_weights`, `BaseRAGMode._build_kb_retriever`, and `check_embedding_compat`. These modes also start pushing `add_retrieval(...)` / `add_trace(...)` provenance events.

**P4 — Async ingestion + SSE progress.**
A small job registry: a SQLite `jobs` table (`id`, `kind`, `status`, `total`, `done_count`, `result`, `error`, timestamps; created idempotently in `init_db()`) plus an in-memory `dict[job_id, asyncio.Queue]` for live events. Async variants `POST /api/kb/{name}/bibtex/async` and `POST /api/kb/{name}/dois/async` return a `job_id` immediately and run the existing ingestion logic in an `asyncio` task, publishing per-paper progress. `GET /api/jobs/{id}` returns the job row; `GET /api/jobs/{id}/events` is an SSE stream terminating on `done`/`error`. The existing **synchronous** `POST /api/kb/{name}/bibtex` and `POST /api/kb/{name}/dois` are unchanged (back-compat); the web UI switches to the async path with a progress bar. CLI and MCP keep the sync path.

**P5 — Coverage & integrations.**
- **Europe PMC full-text source** — `pipeline/download/europepmc.py`: `get_content_from_europepmc(doi, pmid, pmcid, http_client)` resolves an ID via the EBI search API if needed, fetches `…/europepmc/webservices/rest/{source}/{id}/fullTextXML`, parses with the existing JATS extractors from `pmc.py`, sets `content_source = "europepmc"`, returns `None` on no full text. Wired into `unified.py`'s STRUCTURED stage after PMC JATS, before arXiv HTML. No new config (public API).
- **Zotero push** — `integrations/zotero.py`: `ZoteroClient(api_key, library_id, library_type="user")` over Zotero Web API v3; `create_item(paper)` maps DOI/title/authors/year/journal/abstract to a `journalArticle` item (searches for an existing item with the same DOI first to avoid dupes); optional `add_to_collection`. New `ZoteroConfig` block (`enabled` default false, `api_key`, `library_id`, `library_type`, optional `collection_key`); documented in `config.example.yml`. New `push_to_zotero` MCP tool (DOI or list of DOIs; metadata fetched via the unified pipeline with `pdf_parser=None`) and a `POST /api/zotero/push` endpoint (503 when unconfigured) wired to a "Send to Zotero" button on the paper-detail panel + chat source cards. MCP tool count 10 → 11.
- **Obsidian vault export** — `integrations/obsidian.py` + `GET /api/kb/{name}/export?format=obsidian-vault` → a zip: `<KB>/Papers/<doi-slug>.md` (one note per paper: YAML frontmatter with doi/year/journal/authors/source/content_type/tags, then title, abstract, metadata; filename sanitized), `<KB>/Conversations/<title-slug>.md` (the existing markdown renderer's output, post-processed so cited DOIs become `[[<doi-slug>]]` wikilinks to the paper notes), `<KB>/Index.md` (KB-stats summary + links to all notes). The existing `?format=markdown` conversation export is untouched.

### Out of scope (explicitly)

- Structured paper-understanding: entity/claim extraction at ingest time, entity-faceted retrieval.
- Local-PDF-into-KB ingestion.
- Citation/reference-graph expansion ingestion and citation-network RAG.
- Multi-user / auth layer; Docker image.
- SHACL or formal RO-Crate **validation** (we emit RO-Crate-flavored JSON-LD, we don't validate it).
- Provenance recording for non-RAG MCP calls (only RAG answers get a provenance record).
- Outbound webhooks (the polling `GET /api/jobs/{id}` endpoint covers the "callback for long-running ops" need minimally).

### Constraints (carried from the 2026-05-12 run)

- **Additive-only** — no breaking API or schema changes. New SQLite tables/columns are created idempotently in `SessionStore.init_db()`. New optional params default to `None`/disabled so existing behavior is unchanged.
- **Per-task done bar** — `pytest tests/unit/ -m "not live"` stays green; no *new* ruff or mypy errors on touched lines (the repo has a large pre-existing backlog — do **not** try to clear it).
- **Commit policy** — one conventional commit per task, directly on `main`.
- **UI verification** — file-presence unit tests + a `MANUAL_QA.md` checklist (UI is not browser-verified by subagents).
- New config keys are documented in `config.example.yml`. `AGENT_LOG.md` and `ROADMAP.md` are updated locally per phase (both git-ignored, like `CLAUDE.md` and `docs/rules/*.md`).

---

## Architecture

### Provenance subsystem (`perspicacite/provenance/`)

**`collector.py` — `ProvenanceCollector`** (one per RAG request):

Fields:
- `conversation_id: str | None`, `message_id: str | None`, `rag_mode: str`, `request_params: dict`
- `retrieval_events: list[RetrievalEvent]` — each: `paper_id`/`doi`, `title`, `score: float`, `kb_name: str | None`, `content_type: str | None`, `pipeline_step: str | None`, `rank: int`, `stage_label: str` (e.g. `"basic.retrieve"`, `"advanced.wrrf_pass2"`, `"agentic.tool.search_kb"`)
- `mode_trace: list[dict]` — ordered free-form steps the mode pushes: `{"step": "plan", "detail": {...}}`, `{"step": "intent", "value": "..."}`, `{"step": "iteration", "n": 2}`, `{"step": "replan", "reason": "..."}`
- `llm_calls: list[LLMCallRecord]` — `stage_label`, `provider`, `model`, `prompt_messages` (full), `response_text` (full), `prompt_tokens`, `completion_tokens`, `latency_ms`, `ts`

Methods: `add_retrieval(...)`, `add_trace(step: str, **detail)`, `add_llm_call(...)`, `finalize() -> dict`.

**`context.py` — contextvar wiring:**
- `current_collector: ContextVar[ProvenanceCollector | None]` (default `None`)
- `get_collector() -> ProvenanceCollector | None`, `set_collector(c)`, and a `collecting(c)` context manager that sets the var and resets the token on exit.

**`store.py` — `ProvenanceStore`** (constructed in `AppState` startup, given the `data/` dir + the `aiosqlite` connection factory used by `SessionStore`):
- `save(record: dict) -> None` — writes the SQLite `provenance` row and appends each `LLMCallRecord` as a JSON line to `data/provenance/<conversation_id>.jsonl`, recording each line's byte offset into `llm_calls_index`.
- `get_for_message(message_id) -> dict | None` — the SQLite row with `llm_calls` payloads resolved from the JSONL by offset.
- `get_for_conversation(conversation_id) -> list[dict]`
- `iter_llm_calls(conversation_id) -> Iterator[dict]`

**`rocrate.py` — RO-Crate bundle builder:**
- `build_bundle(conversation, messages, provenance_records, sources, llm_calls_jsonl_bytes) -> bytes` (an in-memory zip):
  - `ro-crate-metadata.json` — JSON-LD `@graph`: a root `Dataset` (the conversation), each answer a `CreateAction` (`object` = question text, `result` = answer text, `instrument` = the model(s) used, `mentions` = cited papers), each cited paper a `ScholarlyArticle` (with `identifier` = DOI), the llm-calls JSONL referenced as a `File`.
  - `conversation.md` — output of the existing conversation markdown renderer.
  - `provenance/answer-<n>.json` — one per answer (the merged provenance record).
  - `provenance/llm-calls.jsonl` — copied from the sidecar.
  - `sources.json` — flat list `[{doi, title, year, journal, kb_name, content_type}, …]`.

### Wiring points

- **`rag/engine.py`** — `execute_stream()` (and `execute()`): build a `ProvenanceCollector`, enter `collecting(collector)`, run the mode, and after the stream completes call `app_state.provenance_store.save(collector.finalize())`. The engine receives the message id from the caller (chat router / MCP tool) so the record can be keyed; if a message id isn't available (e.g. ad-hoc MCP `generate_report` with no conversation), the record is keyed by a generated id and `conversation_id` is `None` (no JSONL sidecar in that case — index entries carry inline payloads instead).
- **`llm/client.py`** — after each `litellm.acompletion` returns, `c = get_collector(); if c is not None: c.add_llm_call(stage_label=kwargs.get("stage", "llm"), provider=provider, model=model, prompt_messages=messages, response_text=…, prompt_tokens=…, completion_tokens=…, latency_ms=…)`. The optional `stage` kwarg is swallowed by the existing `**kwargs` / `litellm.drop_params`, so callers that don't pass it are unaffected. Token counts come from `response.usage`; latency is measured around the await.
- **`memory/session_store.py`** — `init_db()` additionally creates the `provenance` and `jobs` tables (idempotent `CREATE TABLE IF NOT EXISTS`). The chat SSE final event must include the persisted assistant message's `id` (additive to the payload) so the UI can fetch its provenance.
- **`web/state.py`** — `AppState` startup constructs `ProvenanceStore` and `JobRegistry` after the session store; both are module-level on `app_state`.

### API surface (all additive)

- `GET /api/conversations/{conv_id}/messages/{message_id}/provenance` → merged provenance record (404 if none).
- `GET /api/conversations/{conv_id}/provenance` → list of all answers' provenance records.
- `GET /api/conversations/{conv_id}/export?format=ro-crate` → `application/zip`, `Content-Disposition: attachment`.
- `POST /api/kb/{name}/bibtex/async` → `{job_id}`; `POST /api/kb/{name}/dois/async` → `{job_id}`.
- `GET /api/jobs/{id}` → job row; `GET /api/jobs/{id}/events` → SSE progress stream.
- `POST /api/zotero/push` → `{created, skipped, failed}` (503 when Zotero not configured).
- `GET /api/kb/{name}/export?format=obsidian-vault` → `application/zip`.

### MCP surface

- New tool `push_to_zotero(dois: list[str] | str) -> json` — fetches metadata via the unified pipeline (`pdf_parser=None`), creates/dedups Zotero items; returns `{created, skipped, failed}`. Returns a clear error JSON when Zotero is not configured. Tool count 10 → 11; update `get_info()`'s `tools` list, the module docstring, `README.md`, and `docs/perspicacite_skills.md`.

### Config additions (`config/schema.py`, documented in `config.example.yml`)

```yaml
zotero:
  enabled: false
  api_key: ""
  library_id: ""
  library_type: "user"      # or "group"
  collection_key: ""        # optional; empty = no collection
```

(`provenance` needs no config — always on for RAG answers; the JSONL sidecar is bounded by conversation lifetime and lives under `data/provenance/`.)

### Web UI

- `static/js/provenance.js` (new) — each assistant message gets a **"Provenance"** disclosure next to the existing sources area. On expand, fetches `…/messages/{message_id}/provenance` and renders three collapsible blocks:
  1. **Request** — RAG mode, KBs queried, recency weight / half-life, hybrid weights, top-k.
  2. **Retrieval** — table of retrieved papers: title (opens the existing paper-detail slide-over), score, KB, content-type, pipeline-step badge (reuses the Phase-5 badge styles), rank.
  3. **Reasoning & LLM calls** — the `mode_trace` steps in order, then the LLM-call list (stage, model, prompt+completion tokens, latency); each call expands to show the full prompt and response (lazy-loaded).
- A **"Download RO-Crate bundle"** link in the conversation header next to the existing Markdown-export control → `?format=ro-crate`.
- `static/js/kb.js` — the "create KB from BibTeX" and "add DOIs" flows switch to the async endpoints and show a progress bar driven by the `/api/jobs/{id}/events` SSE stream (falls back to polling `GET /api/jobs/{id}` if the stream drops).
- "Send to Zotero" button on the paper-detail panel and chat source cards → `POST /api/zotero/push` (hidden / disabled-state when the server reports Zotero unconfigured).
- `static/css/chat.css` / `static/css/kb.css` — styles for the provenance disclosure, the progress bar, the Zotero button.
- `templates/index.html` — new `<script>` tag(s).
- `MANUAL_QA.md` — a section per phase: provenance panel, RO-Crate download, async-ingestion progress bar, Zotero button, Obsidian export.

### RAG mode wiring details (P3)

- **`retrieval/recency.py`** — add `apply_recency_weighting_to_papers(papers: list[dict], recency_weight, half_life_years=None, current_year=None) -> list[dict]`: same exponential-decay math as the chunk-level function but operating on per-paper score dicts (WRRF / two-pass flows work on paper dicts, not chunks). No-op when `recency_weight` is `None` or `0`.
- **`advanced`** (`rag/modes/advanced.py`) — use `_build_kb_retriever(request, vector_store, embedding_provider)` (already `MultiKBRetriever`-aware via `search` / `search_two_pass`); after `paper_results` is assembled in `_wrrf_retrieval`, call `apply_recency_weighting_to_papers(...)`. `resolve_hybrid_weights` is already wired here.
- **`profound`** (`rag/modes/profound.py`) — same two changes inside its two-pass retrieval; each research cycle uses the multi-KB retriever and recency-weighted scores.
- **`literature_survey`** (`rag/modes/literature_survey.py`) — use `_build_kb_retriever`; apply recency weighting to the broad-search results before theme clustering.
- **`agentic`** (`rag/agentic/orchestrator.py`) — add optional `recency_weight`, `recency_half_life_years`, `kb_metas` params to the orchestrator's constructor (or its `run()` entry); `rag/modes/agentic.py` passes them through from the `RAGRequest`. Internally: build a `MultiKBRetriever` when `kb_metas` has >1 entry; apply recency weighting to each tool-driven retrieval's results. Defaults `None` everywhere → behavior unchanged.
- **Embedding-compat** — the chat router and `generate_report` / `search_knowledge_base` MCP tools already run `check_embedding_compat` before dispatch, so no change is needed there.

### Async ingestion details (P4)

- **`jobs/registry.py`** — `JobRegistry`: SQLite `jobs` table + in-memory `dict[job_id, asyncio.Queue]`. `create(kind, total) -> job_id`, `publish(job_id, event: dict)`, `update(job_id, **fields)`, `finish(job_id, result: dict)`, `fail(job_id, err: str)`, `subscribe(job_id) -> AsyncIterator[dict]` (yields published events, terminates on `done`/`error`), `get(job_id) -> dict | None`.
- Async POST handlers validate input, `registry.create(...)`, kick off the existing ingestion logic via `asyncio.create_task` wrapped so it `publish`es per paper (`{paper_index, doi, status: "downloading"|"embedded"|"skipped"|"failed", paper_count, chunk_count}`) and calls `finish`/`fail` at the end; return `{job_id}` immediately.
- On server restart in-memory queues are gone; `GET /api/jobs/{id}` reads the persisted row, and the UI falls back to polling that.

### Europe PMC details (P5)

- `pipeline/download/europepmc.py` — `EUROPEPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"`. Resolve `{source, id}`: prefer an explicit PMCID (`source="PMC"`), else PMID (`source="MED"`), else query `…/search?query=DOI:{doi}&format=json&resultType=lite` and take the first hit's `source`/`id`. Fetch `…/{source}/{id}/fullTextXML`; on 200 with a `<body>`, run `_extract_text_from_xml` / `_extract_sections_from_xml` / `_extract_references_from_xml` from `pmc.py` and return `PaperContent(success=True, content_type="structured", content_source="europepmc", …)`; on 404 or empty body return `None`.
- `unified.py` STRUCTURED stage order: PMC JATS → **Europe PMC** → arXiv HTML → bioRxiv/medRxiv JATS.

---

## Data flow

1. **Chat request** → `chat` router builds a `RAGRequest`, persists the user `Message`, generates the assistant `Message` id, calls `RAGEngine.execute_stream(request, message_id=…)`.
2. **`RAGEngine`** creates a `ProvenanceCollector(conversation_id, message_id, rag_mode, request_params)`, enters `collecting(collector)`, dispatches to the mode handler.
3. **Mode handler** retrieves chunks → `collector.add_retrieval(...)` per chunk; pushes `collector.add_trace("plan"|"intent"|"iteration"|"replan", ...)`; calls `AsyncLLMClient.complete(..., stage="...")` which (collector active) appends an `LLMCallRecord`. Streams `StreamEvent`s out as today.
4. **Stream completes** → `RAGEngine` calls `provenance_store.save(collector.finalize())` → writes the `provenance` row + appends to `data/provenance/<conversation_id>.jsonl`.
5. **Chat SSE final event** carries the assistant message id → UI can later `GET …/messages/{id}/provenance` for the panel.
6. **Export** → `GET …/export?format=ro-crate` → `rocrate.build_bundle(...)` reads the conversation, all messages, all provenance records, and the JSONL sidecar → returns a zip.
7. **Async ingestion** → `POST …/bibtex/async` → `registry.create` → `asyncio.create_task(worker)` → returns `{job_id}` → UI opens `GET /api/jobs/{job_id}/events` → progress bar → terminal `done`/`error` event.

---

## Error handling

- **Provenance is best-effort.** `provenance_store.save(...)` failures are logged (`structlog`) and swallowed — they never break an answer. A `None` collector (no context) makes every `add_*` call a no-op, so direct mode use and unit tests are unaffected.
- **JSONL sidecar I/O errors** → logged; the SQLite row is still written (with empty `llm_calls_index`).
- **RO-Crate export** with no provenance records (old conversations from before this feature) → still produces a valid bundle, just with empty `provenance/` and a note in `ro-crate-metadata.json`.
- **Async ingestion worker exceptions** → `registry.fail(job_id, str(exc))`; the SSE stream emits a final `error` event; the persisted job row records the error. Partial progress (papers already embedded) is kept.
- **Europe PMC** — any HTTP error or unparseable XML → return `None`, the pipeline continues down the chain. Never raises out of `retrieve_paper_content`.
- **Zotero** — not configured → `POST /api/zotero/push` returns 503; the MCP tool returns `{"error": "zotero_not_configured"}`. API errors (bad key, rate limit) → per-DOI `failed` entries with the reason; the call as a whole still returns 200/JSON.
- **Obsidian export** — a paper note that fails to render (bad metadata) is skipped with a warning entry in `Index.md`; the zip still builds.
- **Embedding-mismatch** on multi-KB (any mode) → already surfaced as an error SSE event / error JSON by the router/MCP layer before dispatch; modes never see a mixed-model `MultiKBRetriever`.

---

## Testing

Unit + mocked-integration; nothing in `tests/unit/` touches the network.

- `test_provenance_collector.py` — accumulation; `finalize()` shape; `None`-collector no-op.
- `test_provenance_store.py` — round-trip through `tmp_path` SQLite + JSONL sidecar; offsets resolve to the right lines; `init_db()` idempotency for the `provenance` table.
- `test_llm_client_provenance.py` — `complete()` / `stream()` append an `LLMCallRecord` when a collector is set (litellm mocked), and do nothing when not; the `stage` kwarg is optional and harmless.
- `test_rocrate_export.py` — zip entry set; `ro-crate-metadata.json` parses as JSON-LD with the expected `@type`s and DOI references; empty-provenance conversation still produces a valid bundle.
- `test_provenance_endpoints.py` — `…/messages/{id}/provenance` (incl. 404), `…/conversations/{id}/provenance`, `?format=ro-crate` via `TestClient`.
- `test_advanced_recency_multikb.py`, `test_profound_recency_multikb.py`, `test_literature_survey_recency_multikb.py`, `test_agentic_recency_multikb.py` — mock retriever + LLM; assert recency applied and a `MultiKBRetriever` is built when `kb_names` has >1; assert behavior unchanged when params are `None`.
- `test_recency.py` (extend) — `apply_recency_weighting_to_papers` math + no-op cases.
- `test_jobs_registry.py` — create/update/finish/fail; `subscribe()` yields published events then terminates; `jobs` table `init_db()` idempotency.
- `test_async_ingestion_endpoints.py` — async POST returns a `job_id`; `GET /api/jobs/{id}` reflects progress; the SSE stream terminates (ingestion worker mocked).
- `test_europepmc.py` — `respx`-mocked `fullTextXML` → structured `PaperContent`; ID-resolution via the search API; 404 / empty body → `None`; `unified.py` tries Europe PMC after PMC JATS.
- `test_zotero.py` — `respx`-mocked Web API: item-creation payload mapping, dedup-by-DOI, disabled config; `push_to_zotero` MCP tool + `/api/zotero/push` 503 when unconfigured.
- `test_obsidian_export.py` — zip structure, frontmatter validity, wikilink rewriting, filename sanitization.
- `test_static_assets.py` (extend) — `provenance.js` present; new HTML elements.
- `test_web_app_routes.py` (extend) — new routes in `EXPECTED_ROUTES`; route-count floor bumped.
- `tests/test_mcp_server.py` (extend) — `push_to_zotero` in `get_info()`; tool count 11.
- `MANUAL_QA.md` — a section per phase.

---

## Phasing

Each task = one conventional commit on `main`. Phases are ordered; every phase leaves `pytest tests/unit/ -m "not live"` green.

1. **P1 — Provenance core:** `provenance/` package (collector, context, store); `provenance` SQLite table in `init_db()`; contextvar wiring in `RAGEngine` + `AsyncLLMClient`; `basic` / `contradiction` push retrieval+trace events; `…/provenance` read endpoints; `AppState` constructs `ProvenanceStore`.
2. **P2 — LLM-call audit + RO-Crate:** JSONL sidecar + offset index in `ProvenanceStore`; `rocrate.py` builder; `?format=ro-crate` export route; provenance UI (`provenance.js`, per-message disclosure, "Download RO-Crate bundle" link); chat SSE final event carries the message id.
3. **P3 — RAG mode wiring:** `apply_recency_weighting_to_papers`; recency + multi-KB into `advanced`, `profound`, `literature_survey`, `agentic` (orchestrator interface tweak); those modes push provenance events.
4. **P4 — Async ingestion + SSE:** `jobs/` package + `jobs` table; async BibTeX/DOI endpoints; `GET /api/jobs/{id}` + `…/events`; `JobRegistry` on `AppState`; UI progress bar.
5. **P5 — Coverage & integrations:** Europe PMC source (+ `unified.py` wiring); `ZoteroConfig` + `integrations/zotero.py` + `push_to_zotero` MCP tool + `/api/zotero/push` + UI button; `integrations/obsidian.py` + `GET /api/kb/{name}/export?format=obsidian-vault`; docs (`README.md`, `docs/perspicacite_skills.md`, `config.example.yml`) updated; final code-review pass + fixes.

---

## Definition of done

- All five phases implemented and committed to `main`.
- `pytest tests/unit/ -m "not live"` green; no new ruff/mypy errors on touched lines.
- `config.example.yml` documents the `zotero` block.
- `MANUAL_QA.md` has a click-through section for each phase's UI.
- `AGENT_LOG.md` and `ROADMAP.md` updated locally (git-ignored).
- A final code-review subagent dispatched over the whole branch; any real bugs it finds are fixed (extra commits on `main`).
