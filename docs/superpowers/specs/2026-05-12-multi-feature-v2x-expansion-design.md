# Design Spec — Perspicacité v2.x Multi-Feature Expansion

**Date:** 2026-05-12
**Status:** Approved for planning
**Scope:** Six-phase, additive-only feature bundle drawn from `ROADMAP.md`, intended for autonomous subagent execution over a long unattended run.

---

## 1. Goal & Constraints

Deliver a coherent batch of roadmap features across four tracks (RAG depth, KB-building tools, web UI/observability, database coverage) as **additive, well-isolated** changes — no risky architectural refactors, no async-pipeline rewrite, no auth/Docker work.

**Hard constraints (apply to every phase):**

- **Additive only.** New modules, new endpoints, new MCP tools, new RAG mode, new UI panels, new config knobs. Do not refactor `AppState` startup, the `retrieve_paper_content()` control flow beyond appending a new source, the SSE contract, or the BibTeX import path.
- **No async/background refactor.** Batch DOI ingestion is synchronous (same blocking pattern as the existing `POST /api/kb/{name}/bibtex`).
- **Verification bar per phase (definition of done):**
  - New/changed Python has unit tests under `tests/unit/`.
  - Any code that calls an external HTTP API (PubMed/Entrez, Crossref, bioRxiv, publisher endpoints) gets an integration-style test with the HTTP layer mocked (`respx` or monkeypatched `httpx`/`Entrez`); no live network in CI.
  - `uv run pytest tests/unit/ -m "not live"` passes.
  - `uv run ruff check src/ tests/` and `uv run ruff format --check src/ tests/` clean.
  - `uv run mypy src/` clean.
  - An entry appended to `AGENT_LOG.md` describing the phase.
  - One git commit per feature (or per phase if features are tiny), conventional-commit style, on `main` or a feature branch per the agent's plan.
- **Frontend caveat.** The autonomous agent cannot visually QA the browser UI. Phase 5 ships router/JS-logic-level tests plus a `MANUAL_QA.md` checklist appended at the end; the human reviews the UI afterward.
- **Style:** follow `CLAUDE.md` conventions — `structlog` with kwargs (not f-strings) in `src/perspicacite/`; logging module pattern; `await client.complete(messages=[...])`; `StreamEvent` for all streaming; pydantic models for config and request/response.
- **Read the relevant `docs/rules/*.md` file before touching a subsystem** (`rag_development.md`, `content_pipeline.md`, `api_web.md`, `testing.md`).

**Out of scope (explicitly deferred, do not attempt):** KB-creation SSE progress streaming, async BibTeX pipeline, Zotero sync, Obsidian vault export, Europe PMC full-text, Semantic Scholar recommendations API, citation-network RAG, multi-KB embedding-model reconciliation beyond what Phase 4 needs, multi-user/auth layer, Docker image.

---

## 2. Phase Overview

| Phase | Theme | Features | Depends on |
|-------|-------|----------|-----------|
| 0 | Foundations & hygiene | Repo cleanup; config knobs (reranker model, agentic map-reduce cap, hybrid weights via request); dependency check | — |
| 1 | Content-pipeline coverage | bioRxiv/medRxiv structured retrieval; Crossref metadata enrichment | 0 |
| 2 | KB-building power tools | `screen_papers` (MCP tool + CLI); `pubmed_explorer` → PubMed search adapter + CLI; batch DOI ingestion endpoint + MCP tool | 1 |
| 3 | RAG depth | Contradiction-detection RAG mode; time-aware / recency-weighted retrieval option | 0 |
| 4 | Multi-KB query | Cross-KB fan-out + merge in the engine; UI multi-select | 3 |
| 5 | Web UI + observability | KB statistics endpoint + panel; paper detail panel + pipeline-step badges; conversation full-text search + Markdown export | 1–4 |

Phases are independent enough that a stall after any phase leaves a clean, mergeable state. Within a phase, features may also be committed independently.

---

## 3. Phase 0 — Foundations & Hygiene

### 3.1 Repo cleanup
- The working tree contains a stray `config (1).yml` and `config.yml`, and `config.example.yml` was deleted (only `config.example.yml` is referenced by `CLAUDE.md`). Restore a canonical `config.example.yml` from the current `config.yml` (scrub any secrets/keys → placeholders), delete `config (1).yml`. Confirm `config.yml` and `config (1).yml` are git-ignored (they should be); `config.example.yml` is tracked.
- Do **not** touch `.env` / `.env.example`.

### 3.2 Config knobs (new, with safe defaults — purely additive)
In `src/perspicacite/config/schema.py`:
- `RerankerConfig` (new model) or a field on the retrieval config: `reranker_model: str` (default = the model the reranker currently hard-codes). Wire it into `src/perspicacite/retrieval/reranker.py` so the model name comes from config instead of a constant. Fallback to the current default if unset.
- Add `map_reduce_max_papers: int = 8` to `RAGModeSettings` (or to the `agentic` settings block). In `src/perspicacite/rag/agentic/orchestrator.py`, replace the module-level `MAP_REDUCE_MAX_PAPERS = 8` constant with a read from config (keep the constant as the default if config is absent — no behavior change unless configured).
- Surface hybrid BM25/vector weights on `RAGRequest` (`src/perspicacite/models/rag.py`): add optional `bm25_weight: float | None`, `vector_weight: float | None`. Thread them through `RAGEngine` → mode handlers → `HybridRetriever` where the weights are already runtime-adjustable. If `None`, behavior is unchanged.

### 3.3 Dependency check
- `biopython`, `nltk`, `pandas` are already in `pyproject.toml`. Verify `bibtexparser` is present (used by the bibtex pipeline). Add `respx` (or confirm an HTTP-mock approach with monkeypatch) to dev extras for integration tests. Run `uv sync --dev` and `uv lock` if anything changes.

### 3.4 Tests
- `tests/unit/test_config.py`: new knobs parse, defaults are correct, omitting them is a no-op.
- `tests/unit/test_reranker.py` (new or extend): reranker reads model name from config.
- `tests/unit/` for orchestrator: `map_reduce_max_papers` config value is honored (mock heavy bits — just assert the cap value used).
- Hybrid weight pass-through: a unit test that a `RAGRequest` with explicit weights reaches the retriever with those weights (mock the retriever).

---

## 4. Phase 1 — Content-Pipeline Coverage

Read `docs/rules/content_pipeline.md` first. New sources are appended into `retrieve_paper_content()`'s existing priority chain; the chain's structure does not change.

### 4.1 bioRxiv / medRxiv structured retrieval
- New module `src/perspicacite/pipeline/download/biorxiv.py`:
  - `is_biorxiv_doi(doi) -> bool` (DOIs under `10.1101/...`; distinguish bioRxiv vs medRxiv via the API's `server` field, not the DOI).
  - `get_content_from_biorxiv(doi, http_client, ...) -> PaperContent | None` using the bioRxiv/medRxiv API (`https://api.biorxiv.org/details/{server}/{doi}`) for metadata + abstract, and the JATS XML / full-text endpoint when available to populate `sections` and `references` (mirror the shape `pmc.py` returns). If only metadata is available, return an `abstract`-type `PaperContent`.
- Wire into `unified.py`: in the STRUCTURED stage, after the PMC/arXiv attempts and before the publisher-PDF stage, try bioRxiv when `is_biorxiv_doi(doi)` (or when discovery flags the work as a bioRxiv/medRxiv preprint). Append only — keep existing branches intact.
- Also expose bioRxiv as a recognized source so `content_source` reports `"biorxiv"` / `"medrxiv"`.
- Optionally (small, only if cheap): allow `databases=["biorxiv"]` in `search_literature` via the bioRxiv search endpoint — but if the SciLEx adapter has no slot for it, **skip**; coverage of full-text retrieval is the priority, not a new search backend.

### 4.2 Crossref metadata enrichment
- New module `src/perspicacite/pipeline/download/crossref.py`:
  - `enrich_from_crossref(doi, http_client, base_metadata) -> dict` querying `https://api.crossref.org/works/{doi}` (with a polite `mailto` from `pdf_download.unpaywall_email` if set). Returns a metadata patch: fills missing `title`, `authors`, `year`, `journal`, `abstract` (JATS-stripped), `references` (when Crossref has them and discovery didn't), license/OA hints.
- Wire into the DISCOVERY stage of `unified.py`: after OpenAlex+Unpaywall, if key fields are still missing, merge in the Crossref patch (Crossref fills gaps only — never overwrites a value OpenAlex/Unpaywall already provided). Failures are swallowed (log + continue), exactly like the existing discovery sub-steps.
- Keep it behind nothing — it runs whenever discovery has gaps; it's cheap and rate-limit-friendly with `mailto`.

### 4.3 Tests
- `tests/unit/test_download.py` (extend) + a new `tests/test_download_biorxiv.py` (mocked): bioRxiv DOI detection; metadata-only path → `abstract` result; full-text path → `structured` result with sections/references; non-bioRxiv DOI is untouched.
- `tests/unit/test_crossref.py` (new, mocked): gap-fill merge semantics (missing field filled, present field preserved); Crossref 404 / network error → no-op; `mailto` included when email configured.
- Integration: a mocked end-to-end `retrieve_paper_content()` call for a `10.1101/...` DOI returns a bioRxiv-sourced result; a DOI with sparse OpenAlex data gets Crossref-enriched metadata.

---

## 5. Phase 2 — KB-Building Power Tools

Port the standalone `old_tools/library_expansion_with_abstract/` scripts into first-class app surface area. **Reimplement using the app's stack** (httpx/Entrez, `structlog`, pydantic, the existing `bibtex_kb` pipeline) — do not literally copy the v1 scripts. After porting, update `ROADMAP.md` to mark the "port old_tools" item done; leave `old_tools/` in place for this batch (a follow-up cleanup can remove it once the human confirms parity).

### 5.1 `screen_papers` — relevance screening
- New module `src/perspicacite/search/screening.py`: `screen_papers(candidates, reference_query_or_papers, method="bm25"|"llm", threshold) -> list[ScreenResult]` where each result carries the candidate, a score, and (for LLM) a short rationale.
  - `method="bm25"`: BM25Okapi over abstracts (reuse `perspicacite.retrieval.bm25` if it exposes a usable primitive; otherwise `rank_bm25` directly). Score = max similarity to the reference set, mirroring the v1 tool's semantics (0.2 / 0.3 / 0.5 bands).
  - `method="llm"`: batch the candidates, ask the LLM (`await llm.complete(...)`) to rate relevance 0–1 against the query with a one-line reason; JSON-structured output, robust parse.
  - Input candidates accept DOIs, titles, or `Paper`-like dicts; abstracts fetched via the existing discovery/content pipeline (or PubMed) when missing, with a small cache.
- MCP tool `screen_papers` in `src/perspicacite/mcp/server.py`: args `candidates: list[str]` (DOIs or titles), `query: str`, `method: str = "bm25"`, `threshold: float = 0.3`, `max_results: int = 50`. Returns JSON `{ "screened": [ {doi/title, score, reason, kept: bool}, ... ] }`. Follow the existing `_json_ok`/`_json_error`/`_require_state` pattern. Update the tool list in `get_info()` and in the module docstring. (Tool-count bookkeeping: 8 base → 9 with `screen_papers` → 10 with `add_dois_to_kb` in §5.3; see §9 — update every "8 tools" reference once, consistently, when Phase 2 lands.)
- CLI subcommand `perspicacite screen-papers --input refs.bib --candidates cand.bib --output screened.bib [--method bm25|llm] [--threshold 0.3] [--csv report.csv]` in `src/perspicacite/cli.py`. Reuse `entries_to_papers` for `.bib` I/O.

### 5.2 `pubmed_explorer` — PubMed deep search adapter
- New module `src/perspicacite/search/pubmed.py`: `PubMedSearchAdapter` exposing `async search(query, max_results, year_min, year_max, ...) -> list[Paper]` using Biopython `Entrez` (esearch → efetch), parsing into `Paper` models (title, authors, year, DOI, abstract, journal, PMID in `metadata`). Requires an NCBI email — read from a new config field `databases.pubmed_email` (or reuse `pdf_download.unpaywall_email` as a fallback); fail fast with a clear error if absent (match the v1 tool's behavior). Respect Entrez rate limits (3/s no key, 10/s with key).
- Integrate as a SciLEx-adapter backend: if `SciLExAdapter` already routes `databases=["pubmed"]` somewhere, make this the implementation; otherwise add `"pubmed_deep"` as a selectable backend in `search_literature` and the web search path. Keep it additive — don't break existing pubmed behavior.
- CLI subcommand `perspicacite pubmed-search "<query>" [--max 50] [--year-min] [--year-max] [--output out.bib]`.

### 5.3 Batch DOI ingestion
- New endpoint `POST /api/kb/{name}/dois` in `src/perspicacite/web/routers/kb.py`. Request body `{ "dois": ["10.x/...", ...] }`. **Synchronous**, mirrors the `POST /api/kb/{name}/bibtex` handler: for each DOI → `retrieve_paper_content()` → build `Paper` (enrich metadata from discovery) → dedup via `vector_store.paper_exists` → `DynamicKnowledgeBase.add_papers` → update KB metadata. Returns `{ added_papers, added_chunks, skipped_duplicates, failed: [{doi, reason}], pdf_download: {...}, kb }`. Cap the list size (e.g. 200) with a clear 400 if exceeded.
- Factor the per-paper download+enrich+add loop shared by `add_papers_to_kb`, `add_bibtex_to_kb`, and the new endpoint into a small helper in `src/perspicacite/pipeline/bibtex_kb.py` (or a new `kb_ingest.py`) — **only** if it can be done without changing the behavior of the two existing handlers; if risky, just duplicate the loop. (Keep the refactor minimal and well-tested; this is the one place a small internal refactor is allowed.)
- MCP tool `add_dois_to_kb(kb_name: str, dois: list[str]) -> str` in `mcp/server.py`, reusing the same ingest path; same JSON-response conventions; this is the 10th tool (8 base + `screen_papers` + `add_dois_to_kb`) — update the counts/docstrings/`get_info()` and all "8 tools" references in `CLAUDE.md`, `docs/perspicacite_skills.md`, `README.md` to **10** in this commit.
- Update `ROADMAP.md`: tick "batch DOI ingestion endpoint", "screen_papers MCP tool", and the "port old_tools" item; note `build_libraries_from_dois`'s citation/reference-expansion mode is **not** ported in this batch (only flat DOI-list ingestion) — leave that as a roadmap item.

### 5.4 Tests
- `tests/unit/test_screening.py`: BM25 banding correctness on fixed inputs; LLM method with a mocked `AsyncLLMClient` returning canned JSON; threshold filtering; missing-abstract handling (mock the fetch).
- `tests/unit/test_pubmed.py` (mocked Entrez): query → `Paper` parsing; missing-email → fail fast; rate-limit knob respected (no real sleep — assert config).
- `tests/unit/test_chat_endpoint.py` / a new `tests/unit/test_kb_dois_endpoint.py`: `POST /api/kb/{name}/dois` happy path (mock `retrieve_paper_content` + `DynamicKnowledgeBase`), dedup, oversize-list 400, KB-not-found.
- `tests/test_mcp_server.py` (extend): the two new MCP tools are registered and return well-formed JSON with state uninitialized and with a mocked state.
- CLI: a smoke test invoking the new subcommands with `--help` and with tiny fixture `.bib` files (mocked network).

---

## 6. Phase 3 — RAG Depth

Read `docs/rules/rag_development.md` first. Both features must respect the streaming contract: `execute()` and `execute_stream()` on a `BaseRAGMode`; errors are yielded as `StreamEvent(event="error", ...)`, not raised.

### 6.1 Contradiction-detection RAG mode
- Add `RAGMode.CONTRADICTION = "contradiction"` to `src/perspicacite/models/rag.py`.
- New handler `src/perspicacite/rag/modes/contradiction.py` extending `BaseRAGMode`. Behavior:
  1. Retrieve a broad-ish set of chunks for the query (hybrid retrieval, like `advanced`).
  2. Group chunks by paper; build per-paper claim summaries (map step, capped — reuse the map-reduce cap from Phase 0 config).
  3. LLM pass that clusters claims into **agreement / disagreement / nuance** groups for the question, citing papers on each side.
  4. Synthesize a structured answer: "Points of consensus", "Points of disagreement (with who claims what)", "Open / under-determined". Stream it as `content` events; emit `source` events for cited papers like other modes.
- Register in `RAGEngine._modes`. Add a `RAGModeSettings` entry for it (similar to `advanced`: hybrid on, rerank on, no planning). Add to the MCP `generate_report` `mode_map` (`"contradiction"`) and to the web mode picker (Phase 5).
- Guard: if fewer than ~3 papers are retrieved, emit a `content` note that contradiction analysis needs more sources and degrade gracefully to an `advanced`-style answer (don't error).

### 6.2 Time-aware / recency-weighted retrieval
- Add `recency_weight: float | None = None` (0–1; `None`/0 = off) and optionally `recency_half_life_years: float | None` to `RAGRequest`.
- In the retrieval scoring path (where WRRF / hybrid scores are combined — `src/perspicacite/retrieval/hybrid.py` and/or the mode handlers), when `recency_weight > 0`, multiply/blend each chunk's score by a recency factor derived from the paper's `year` (exponential decay with the half-life; papers with no year get a neutral factor). Keep it a post-scoring re-rank so it composes with rerank and WRRF without restructuring them.
- Expose as a UI toggle/slider in Phase 5 and as an optional arg on the `generate_report` MCP tool (`recency_weight: float = 0.0`).
- Make it usable from `basic`, `advanced`, `profound`, `agentic`, and the new `contradiction` mode (it's a request-level option, not a mode).

### 6.3 Tests
- `tests/unit/test_rag_modes_pytest.py` / new `tests/unit/test_contradiction_mode.py`: with a mocked vector store + mocked LLM, the mode produces the three-bucket structure; few-papers guard degrades instead of erroring; `source` events emitted.
- `tests/unit/` recency test: a fixed set of chunks with known years → assert the recency factor reorders them as expected; `recency_weight=0`/`None` is a perfect no-op (scores identical to baseline).
- Engine routing test: `RAGMode.CONTRADICTION` dispatches to the new handler.

---

## 7. Phase 4 — Multi-KB Query

Additive at the engine surface; do not change single-KB behavior when only one KB is specified.

- `RAGRequest`: allow `kb_name` to remain a single string **and** add `kb_names: list[str] | None = None`. When `kb_names` is set (length > 1), the engine fans out retrieval across each KB's collection, tags each retrieved chunk with its source KB, merges by score, dedups by `paper_id`, then proceeds with the normal mode pipeline on the merged set. When `kb_names` is unset or length 1, behavior is byte-for-byte the existing path.
- Implementation: a thin helper in `RAGEngine` (or a `MultiKBRetriever` wrapper around `ChromaVectorStore`) that the mode handlers use transparently — the modes shouldn't need per-mode changes if retrieval is abstracted behind the existing retriever protocol. Prefer wrapping over editing every mode.
- Constraint: KBs must share an embedding model to be queried together; if they don't, return a clear `StreamEvent(event="error", ...)` / 400 listing the mismatch. (No re-embedding — that's out of scope.)
- API: `chat` router accepts `kb_names`; MCP `generate_report` / `search_knowledge_base` gain an optional `kb_names: list[str]`. `search_knowledge_base` multi-KB returns chunks tagged with their KB.
- Source attribution: `source` stream events and `SourceReference` include which KB each cited paper came from (add an optional `kb_name` field to `SourceReference`).

### 7.1 Tests
- `tests/unit/`: two mocked collections → fan-out merges + dedups by paper_id; single-KB path unchanged (golden comparison); embedding-model mismatch → error event, not crash.
- `tests/unit/test_chat_endpoint.py`: `kb_names` accepted and threaded through.
- `tests/test_mcp_server.py`: `generate_report` with `kb_names` returns sources tagged by KB.

---

## 8. Phase 5 — Web UI & Observability

Read `docs/rules/api_web.md` first. Static assets live in `static/css/` (6 files) and `static/js/` (8 files); SPA shell is `templates/index.html`. Per `CLAUDE.md`, hard-refresh notes apply. The agent writes router tests + JS-logic-level tests where feasible and appends a `MANUAL_QA.md` at repo root with a click-through checklist for the human.

### 8.1 KB statistics dashboard
- New endpoint `GET /api/kb/{name}/stats` in `routers/kb.py`. Returns: total papers, total chunks, papers-by-year histogram, source breakdown (count by `PaperSource`), content-type breakdown (how many papers have full-text vs abstract-only vs none — derived from stored chunk metadata / cached `PaperContent`), top journals, embedding model, created/updated timestamps. Compute from ChromaDB collection metadata (`vector_store.get_collection_stats` + a `coll.get(include=["metadatas"])` scan, paginated/capped for large KBs) and SQLite KB metadata. No new persistence.
- UI: a "Stats" tab/panel on the KB view rendering the histogram (simple inline SVG or a tiny canvas — no new JS deps) + breakdown tables. New `static/js/kb_stats.js` (or extend `kb.js`) and a small CSS block. Loaded by `main.js`.

### 8.2 Paper detail panel + pipeline-step badges
- Endpoint `GET /api/paper?doi=...` (or `/api/paper/{doi:path}`) returning discovery metadata + abstract + which content type is available + `content_source` — backed by `retrieve_paper_content()` with a short timeout, reading the per-DOI JSON cache in `./data/papers/` when present (cheap path) before doing a live fetch.
- Chat results: each paper card gets a small badge showing the pipeline outcome (`structured` / `full text` / `abstract` / `none`) using the `content_type`/`content_source` already flowing through. This requires the `source` stream events / KB-add responses to carry `content_type` (they largely do; thread it where missing). Clicking a card opens the detail panel (modal/sidebar) that calls the new endpoint.
- New `static/js/paper_detail.js`, small CSS; wire into `chat.js` rendering and `main.js`.
- This also satisfies the roadmap's "retry / fallback logging — surface which pipeline step succeeded per paper" item.

### 8.3 Conversation full-text search + Markdown export
- Search: `GET /api/conversations/search?q=...` in `routers/conversations.py`. Implement with SQLite FTS5 over conversation messages — create an FTS virtual table + triggers in a tiny idempotent migration in `SessionStore.init_db()` (additive: `CREATE VIRTUAL TABLE IF NOT EXISTS ...`, backfill once). Returns matching conversations with snippets. If FTS5 isn't available in the bundled SQLite, fall back to a `LIKE` scan (still correct, just slower) — detect and degrade.
- Export: `GET /api/conversations/{id}/export?format=markdown` returns a `.md` rendering of the conversation (Q&A turns, with the cited sources listed per answer and a references section). Content-Disposition attachment. (This also covers the roadmap's "conversation export" limitation; the "Obsidian vault export" is the deferred superset.)
- UI: a search box in the conversations sidebar (calls the search endpoint, shows snippets) and an "Export ⤓" button on each conversation. New `static/js/conversation_search.js` or extend `conversations.js`; small CSS.

### 8.4 UI exposure of Phase 0/3/4 knobs
- Mode picker gains the new `contradiction` mode.
- An "advanced options" disclosure in the chat input area exposing: BM25/vector weight sliders (Phase 0), recency-weight slider (Phase 3), and a multi-KB multi-select (Phase 4). All optional; defaults preserve current behavior.

### 8.5 Tests
- `tests/unit/test_web_app_routes.py` / `test_static_assets.py` (extend): new endpoints return expected shapes (mock `app_state` subsystems); new static files are referenced and load.
- `tests/unit/`: `GET /api/kb/{name}/stats` aggregation correctness against a mocked collection; `GET /api/paper` cache-hit vs live-fetch paths; conversation search FTS query + LIKE fallback; Markdown export rendering for a fixture conversation.
- Append `MANUAL_QA.md` (repo root, git-tracked) with a per-feature click-through checklist for the human reviewer.

---

## 9. Cross-Cutting Notes for the Implementing Agent

- **Order matters.** Do phases 0 → 1 → 2 → 3 → 4 → 5. Within a phase the listed features can be done in any order, but finish (tests + lint + mypy + commit + `AGENT_LOG.md`) before starting the next.
- **One commit per feature**, conventional-commit prefixes (`feat:`, `feat(pipeline):`, `feat(mcp):`, `feat(web):`, `chore:`, `test:`, `docs:`). Branch strategy is the planner's call; if working on a branch, open a PR per phase at the end.
- **MCP tool-count bookkeeping:** the project advertises "8 tools" in several places (`mcp/server.py` docstring + `get_info()`, `CLAUDE.md`, `docs/perspicacite_skills.md`, `README.md`). Phase 2 adds two (`screen_papers`, `add_dois_to_kb`) → 10. Update every reference in the same commit that adds the tool. Document the new tools in `docs/perspicacite_skills.md` following the existing format.
- **Don't break the MCP↔web split:** the MCP server has its own `MCPState`; new MCP tools must initialize what they need from `mcp_state`, not import `app_state`.
- **External APIs in tests are always mocked.** Live tests stay under top-level `tests/test_*.py` guarded by the `live` marker; never add network calls to `tests/unit/`.
- **If a feature turns out to require a refactor that violates the "additive only" constraint, stop and leave a note in `AGENT_LOG.md` rather than doing the refactor.** Better to ship 5 phases cleanly than 6 with a risky rewrite.
- **`ROADMAP.md` housekeeping:** move completed items to the "Completed (archive)" section as you go, with the `(2026-05)` date tag.
- **Update `CLAUDE.md`** where architecture changes (new RAG mode in the modes table, new MCP tool count, new endpoints under the web app section, new config knobs).

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| bioRxiv/Crossref API shape drift breaks the new modules | All parsing is defensive (`.get()`, try/except → log + continue, return `None`/no-op on failure); mocked tests pin expected shapes; pipeline already tolerates a failing sub-step |
| Entrez/PubMed requires an email and rate-limits hard | Fail fast with a clear message if no email; honor rate limits from config; never hit Entrez in unit tests |
| Synchronous batch DOI ingestion blocks the event loop on large lists | Hard cap on list size; same accepted limitation as the existing BibTeX import; the async version is a deferred roadmap item |
| Multi-KB merge across mismatched embedding models produces garbage | Explicit pre-check → error with the mismatch listed; refuse rather than silently degrade |
| Agent can't visually verify the UI | Phase 5 ships router/JS-logic tests + a `MANUAL_QA.md` checklist; UI work is the last phase so a stall there doesn't block the rest |
| The §5.3 shared-ingest-helper refactor regresses existing KB-add handlers | Only do it if it can be done without behavior change; otherwise duplicate the loop; cover all three call sites with tests |
| Long autonomous run drifts from the spec | Per-phase verification gate + `AGENT_LOG.md` entries make drift visible; phases are independently mergeable |

---

## 11. Definition of Done (whole batch)

- All six phases implemented, or a clean subset of phases (each fully done) with a clear `AGENT_LOG.md` note on where it stopped and why.
- `uv run pytest tests/unit/ -m "not live"`, `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/` all green on the final state.
- `AGENT_LOG.md`, `ROADMAP.md`, `CLAUDE.md`, `docs/perspicacite_skills.md` updated to reflect the new surface area.
- `MANUAL_QA.md` present with a UI click-through checklist.
- New MCP tools documented; new config knobs documented in `config.example.yml` (commented, with defaults).
