# E2E validation — design spec

**Wave 6 of `docs/roadmap-2026-05-followups.md` (6.1 + 6.2 + 6.3).**

**Goal:** Lock in framework correctness with three layers of automated
tests: end-to-end pipeline scenarios (6.1), persistence / data
integrity (6.2), and a performance-regression baseline (6.3).

All tests in this wave **mock the LLM and embedding providers** so
they're deterministic and fast (target: full suite < 30s). The
*real* full-pipeline run against API LLMs is a separate manual
audit step (post-Wave-7), not part of CI.

## 6.1 — Canonical E2E scenarios

Three scripted scenarios under `tests/e2e/`:

### Scenario A — Single-paper round trip

`tests/e2e/test_single_paper.py`

```
create KB → add 1 paper (mocked fetcher + LLM) → search KB →
generate_report → assert: report references the paper's DOI
```

Coverage: KB creation, chunking, embedding, retrieval ranking,
report synthesis, citation generation.

### Scenario B — Multi-paper + citations

`tests/e2e/test_multi_paper_citations.py`

```
create KB → ingest 5 DOIs (mocked) → expand_kb_via_citations
(mocked references for paper #1) → search KB →
generate_report → assert: report cites at least 2 of the 5 +
   exactly 1 expanded reference
```

Coverage: bulk DOI ingest, citation expansion, the Wave 4.3 KB log
emits the right events, the Wave 3.3 checkpoint store records progress.

### Scenario C — Cross-KB routing

`tests/e2e/test_cross_kb_routing.py`

```
create KB "astro" (1 paper on stellar physics) + KB "bio" (1 paper
on protein folding) → route_kbs(query="how do red giants form?")
→ assert: astro picked, bio not
→ route_kbs(query="alphafold predicts protein structure?")
→ assert: bio picked, astro not
```

Coverage: KB selection, the description-based routing heuristic, the
`MultiKBRetriever` glue.

## 6.2 — Persistence / data integrity

`tests/integration/test_persistence_integrity.py`

| Test | What it verifies |
|---|---|
| `test_kb_survives_close_reopen` | Build KB → close DKB → re-open from disk → all chunks queryable, metadata intact. |
| `test_chroma_collection_persists` | Chroma's WAL flushes — kill the process (simulated via fresh client), reopen, count unchanged. |
| `test_concurrent_kb_log_appends` | 4 tasks each appending 100 events on the same KB log. All 400 land, no interleaving, no torn lines (Wave 4.3 invariant). |
| `test_concurrent_session_store_writes` | 4 tasks each writing 50 KB metadata rows on different KBs. SQLite WAL holds; no `database is locked` exceptions. |
| `test_session_store_reopen_preserves_rows` | Insert → close → reopen → all rows still there. |
| `test_checkpoint_survives_kill_mid_save` | Simulate kill mid-write (truncated tmp file) → reopen → previous good checkpoint loads (Wave 3.3 atomic-save invariant). |
| `test_llm_cache_survives_reopen` | Wave 2.1 disk cache: write → close → reopen → entries still retrievable. |
| `test_embedding_cache_dedup_across_reopens` | Wave 2.2: same text re-embedded after reopen returns the cached vector, not a fresh embedding call. |

## 6.3 — Performance regression baseline

`tests/integration/test_perf_baseline.py`

Records baseline timings against a fixed 5-paper synthetic corpus
(bundled under `tests/data/perf_corpus/`). Each run captures:

```json
{
  "ingest_5_papers_seconds": ...,
  "search_top10_seconds": ...,
  "report_synthesis_seconds": ...,
  "embeddings_per_second": ...,
  "kb_log_writes_per_second": ...,
  "git_sha": "...",
  "timestamp": "..."
}
```

**Behaviour:**

- The test reads `tests/data/perf_baseline.json` (the saved baseline).
- Runs the same pipeline (mocked LLM + mocked embedder, deterministic).
- Computes the ratio of each metric vs baseline.
- **Fails** if any metric is > 30% slower (`current / baseline > 1.30`).
- **Warns** (prints, doesn't fail) on > 30% faster — surprises both ways.
- Run with `--update-baseline` flag (via `PERSPICACITE_UPDATE_PERF_BASELINE=1`
  env var) regenerates `perf_baseline.json` instead of comparing.

Marked `@pytest.mark.perf` so CI can run it selectively.

## Components

| File | Change |
|---|---|
| `tests/e2e/__init__.py` (new) | empty marker. |
| `tests/e2e/conftest.py` (new) | Shared fixtures: `mock_paper_fetcher`, `mock_llm_for_e2e`, `tmp_kb_root`, `e2e_app`. |
| `tests/e2e/test_single_paper.py` (new) | Scenario A. |
| `tests/e2e/test_multi_paper_citations.py` (new) | Scenario B. |
| `tests/e2e/test_cross_kb_routing.py` (new) | Scenario C. |
| `tests/integration/test_persistence_integrity.py` (new) | 8 persistence tests. |
| `tests/integration/test_perf_baseline.py` (new) | Perf regression check. |
| `tests/data/perf_corpus/*.json` (new) | 5 synthetic papers as JSON fixtures. |
| `tests/data/perf_baseline.json` (new) | Initial captured baseline (populated by running the test once with `PERSPICACITE_UPDATE_PERF_BASELINE=1`). |
| `pyproject.toml` | Register the `perf` and `e2e` markers. |

## Behaviour contract

- **No network calls.** All LLM / embedding / Chroma / SQLite interactions either use mocks or in-memory / tmp_path fixtures.
- **Determinism.** Mock embedding provider returns SHA-256-based deterministic vectors (same input → same output). Mock LLM returns canned strings keyed by stage name.
- **Speed.** Full e2e + persistence suite must complete in < 30s on a typical dev box.
- **Tolerance.** Perf baseline ratio threshold defaults to 1.30; configurable via `PERSPICACITE_PERF_TOLERANCE` env var.
- **Skips.** If `chromadb` / `pymupdf` is not installed, persistence tests that need them `pytest.skip()` rather than fail.

## Test plan (meta — the tests themselves are the deliverable)

Coverage of the test suite itself:

- All 3 e2e scenarios pass on a clean checkout.
- All 8 persistence tests pass.
- The perf baseline test passes with the initial baseline committed.
- Running with `PERSPICACITE_PERF_TOLERANCE=1.01` makes the perf
  test fail (sanity check that the threshold works).

## Out of scope (followups)

- **Real-LLM E2E.** Separate manual audit pass after Wave 7 — the user
  will run the full pipeline against API LLMs on 2 example queries and
  again with Claude Code, comparing outputs.
- **Output-quality evaluation.** Per the roadmap, BLEU/Rouge/expert-
  judgement is a separate effort with gold-standard corpora.
- **Multi-process concurrency.** Tests cover async concurrency within
  a single process. True OS-level concurrent ingests (e.g. two
  `perspicacite ingest` shell commands at once) is a fuzz-test for later.
- **GPU embedding paths.** Tests use the CPU-only mock provider.
