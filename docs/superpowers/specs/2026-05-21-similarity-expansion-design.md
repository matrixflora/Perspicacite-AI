# Similarity-based KB expansion — design

**Date:** 2026-05-21
**Status:** approved (design), pending implementation plan
**Origin:** inspired by ScienceGuide's `abstract_compare/screen_papers.py` (BM25 abstract-similarity screening), reimplemented natively in Perspicacité v2's stack.

## Context / problem

Perspicacité grows a KB from its citation graph via `expand_kb_via_citations` (`pipeline/snowball.py`): forward/backward snowball over OpenAlex → optional relevance screen → ingest. That screen is **query-based** — when no `query` is given it falls back to `kb_meta.description or kb_name` (snowball.py ~683), a single short string standing in for the whole KB. Validated with colleagues: snowball returns many off-topic papers, and a one-line description is too thin a target to filter them — especially for broad / multi-topic KBs.

## Goal

Let a researcher expand a KB and keep only candidates genuinely similar to **what's already in it**, by screening snowball candidates against the **set** of the KB's actual paper abstracts, with an interactive **calibrate-by-example** threshold. Maximize reuse of existing screening/retrieval infrastructure.

## Conceptual model — three orthogonal axes (not a new "method")

The existing `bm25`/`llm`/`rerank` screens are *all* similarity/relevance comparisons, so "similarity" is not a distinct method. What this feature actually adds are points on three **independent** axes that already exist implicitly:

| Axis | Options (★ = new) |
|---|---|
| **Reference** — what candidates are compared against | a query string · **★ the KB's abstract set** |
| **Scorer** — how the comparison is scored | bm25 · **★ embedding** · **★ hybrid** · rerank · llm  *(rerank/llm are query-only)* |
| **Threshold** — how the keep/drop cutoff is chosen | fixed value · **★ calibrate-by-example** (histogram + label samples) |

All scorers already emit a normalized **0–1** `ScreenResult.score`, so the threshold axis works on *any* scorer's output, and both reference modes feed the same scorers (subject to the rerank/llm "query-only" constraint).

**"Expand by similarity"** is then just a **named preset** of these axes — *reference = KB abstract set, scorer = hybrid (default), threshold = calibrate-by-example* — not a special code path. Every piece underneath is a reusable building block.

## Non-goals (v1)

- CLI and MCP adapters (the core is interface-agnostic; both are thin and come later).
- Cross-encoder rerank as a *set* scorer (it needs a single query; combinatorial over a set). `rerank`/`llm` stay query-only.
- Gap/elbow auto-threshold and percentile presets (calibrate-by-example replaces them for v1).
- A non-KB `.bib` reference set in the **web** flow (the scorers accept arbitrary reference abstracts; the web flow always uses the KB being expanded).
- Iterative/binary-search calibration (v1 is one round of samples).

## Component placement (file-by-file)

New *logic* clusters in `search/screening.py`; everything else is thin wiring on the layer it belongs to.

### `search/screening.py` — scorers (axis 2) + threshold helpers (axis 3)

- **`screen_papers_embedding(candidates, *, collection, embedding_provider, vector_store, top_k=5, threshold=0.3) -> list[ScreenResult]`** *(new)* — set-embedding scorer. For each candidate: embed `title + abstract` with `embedding_provider` (the KB's own provider/model → same vector space), call `vector_store.search(collection, query_embedding, top_k)` (existing; returns 0–1 cosine hits), score = mean of top-k hit scores. No-abstract candidate → `0.0`, `reason="no abstract"`. Dependency-injected, same style as `screen_papers_llm(... llm)`.
- **`screen_papers_hybrid(candidates, *, reference_abstracts, collection, embedding_provider, vector_store, weights=(0.5, 0.5), threshold=0.3) -> list[ScreenResult]`** *(new, default scorer)* — run set-`bm25` (existing `screen_papers` with `reference_abstracts` as the list) and `screen_papers_embedding`, blend the two already-0–1 scores per candidate: `w_bm25*bm25 + w_emb*emb`. `reason` carries both components.
- **`select_calibration_samples(results, n=4) -> list[ScreenResult]`** *(new)* — bucket scores into a histogram; return the candidates nearest the ~85/60/40/20th percentiles of the score range (return all if `len(results) <= n`; dedup).
- **`cutoff_from_labels(labeled: list[tuple[ScreenResult, bool]]) -> float`** *(new)* — cutoff that **best separates** the labels (minimizes misclassified samples; ties → higher/more-conservative cutoff). Clean monotonic labels reduce to "between the lowest 'relevant' and highest 'not'." Always returns a value.
- **`bm25` against the set:** *no new code* — `screen_papers` already accepts `reference: str | Sequence[str]` and scores max-over-list.
- `screen_papers_rerank` / `screen_papers_llm`: unchanged, retained as the query-only methods.

### Reference assembly (axis 1) — abstracts in metadata, chunk-text fallback

The KB-set reference is the KB's **per-paper abstracts**. To make these available, the abstract is **stored in chunk metadata at ingest**: `ChunkMetadata` gains an `abstract` field, populated from `paper.abstract` on the chunk-0 ("metadata") chunk; `_chunk_to_metadata`/`_metadata_to_chunk`/`list_paper_metadata` carry it through. Reference assembly (`get_kb_reference_texts(collection, cap)`) then prefers those abstracts and **falls back to capped chunk texts** (`list_chunk_texts`) when a KB has none — so **existing KBs work unchanged** (they hit the fallback), and newly-ingested papers get clean-abstract references. The embedding scorer needs no text — it queries the collection directly.

The interactive v1 flow calls the Plan-1 scorers **directly** from the orchestrator, so **`screen_candidates` and the one-shot `expand_kb_via_citations` are NOT modified** in v1 (that query/`screen_method` wiring is deferred with the CLI/MCP adapters). Their existing query-based screening is untouched.

### `pipeline/similarity_expansion.py` — two-phase orchestrator *(new file)*

The interactive web flow is stateful across two calls, a different contract from the one-shot `expand_kb_via_citations`; it reuses the same lower-level pieces rather than reimplementing them.

- **`score_expansion_candidates(*, app_state, kb_name, direction, max_per_seed, method, weights) -> ExpansionScoreReport`** (phase 1): `snowball_expand` → drop already-in-KB + `apply_filters` (existing year/citation/abstract gates) → score survivors via the chosen scorer against the KB set. Returns **all** scored candidates (no cutoff applied here — the `ScreenResult.kept` flag is ignored in phase 1; the cutoff is chosen in phase 2), plus histogram buckets of the scores and the `select_calibration_samples` picks.
- **`commit_expansion(*, app_state, kb_name, scored_candidates, cutoff) -> IngestReport`** (phase 2): apply `score >= cutoff`, then `ingest_dois_into_kb` the kept DOIs.

### `web/routers/kb.py` — two thin endpoints

- `POST /api/kb/{name}/expand-similar/score` (SSE progress) → calls `score_expansion_candidates`; returns scored candidates + histogram + samples.
- `POST /api/kb/{name}/expand-similar/commit` → body carries sample labels (→ `cutoff_from_labels`) or an explicit cutoff + the phase-1 scored candidates; calls `commit_expansion`; returns the ingest report.

### `frontend/src/app/…` — the page

Pick KB → snowball direction + per-seed cap + scorer → run (phase 1) → see the **histogram** (reusing the KB-stats histogram render) + the ~4 sample papers → mark each relevant/not → cutoff line placed (slider-adjustable) → review the ranked kept list → confirm → ingest (phase 2).

## Data flow

```
KB seeds ──snowball_expand(direction)──▶ candidates ──drop existing / apply_filters──▶ survivors
survivors ──scorer(method, reference = KB abstract set + collection)──▶ ScreenResult[] (0–1)
ScreenResult[] ──histogram + select_calibration_samples(n=4)──▶ web review
user labels ──cutoff_from_labels──▶ cutoff (slider-adjustable)
cutoff ──score>=cutoff filter──▶ kept ──ingest_dois_into_kb──▶ KB grows
```

## Reuse summary (the "no redundant code" goal)

Reused as-is: `screen_papers` (set-bm25), `ChromaVectorStore.search` (embedding cosine), `screen_papers_rerank`/`_llm`, `snowball_expand` + `apply_filters`, `ingest_dois_into_kb`, the KB-stats histogram render, the `GET /api/kb/{name}/papers` paper-derivation. **Net-new:** one embedding scorer + a hybrid blend + two threshold helpers (all in `screening.py`), a small dispatch/reference change in `screen_candidates`, one new orchestrator file, two thin endpoints, one frontend page.

## Error handling / edge cases

- Candidate with no abstract → `0.0`, `kept=False`, `reason="no abstract"` (flagged, not silently dropped).
- Empty pool after snowball/gates → clean "nothing to screen" report; no histogram step.
- Non-monotonic sample labels → best-separating cutoff + a note ("your judgments didn't line up cleanly with the scores — adjust if needed").
- Embedding/vector errors (missing collection, provider failure) → that scorer degrades to an error in the report rather than raising; user can retry with `bm25`.
- All paths return a report; no exceptions surface to the SSE stream as crashes.

## Testing

- **Unit (`tests/unit/`):** `screen_papers_embedding` + `screen_papers_hybrid` with a **stub embedding provider + stub vector store** (relevant candidates rank above off-topic; no-abstract → 0). `select_calibration_samples` (spans the distribution; tiny-pool). `cutoff_from_labels` (clean monotonic; non-monotonic best-fit; all-relevant / all-not).
- **Integration (thin):** `score_expansion_candidates` / `commit_expansion` with mocked `snowball_expand` + stubbed scorer (phase-1 payload shape; cutoff application in phase 2); the two endpoints with the orchestrator mocked.
- No new frontend test harness (none exists); the page is verified manually.

## Deferred (post-v1)

CLI + MCP adapters over the same core; cross-encoder re-rank of the embedding shortlist (embedding → top-N → cross-encoder) for extra precision; percentile presets / gap-elbow auto-threshold; iterative calibration; non-KB `.bib` reference in the web flow.
