# E2E validation suite — operator guide (Wave 6)

The Wave 6 test suite ships three layers of automated checks:

1. **E2E scenarios** (`tests/e2e/`, marker `e2e`) — three canonical
   pipelines through the framework.
2. **Persistence / integrity** (`tests/integration/test_persistence_integrity.py`,
   marker `integration`) — KB survives close/reopen, concurrent writes
   land, caches persist, checkpoints survive simulated kills.
3. **Performance regression baseline**
   (`tests/integration/test_perf_baseline.py`, marker `perf`) —
   regression check against a stored baseline at
   `tests/data/perf_baseline.json`.

All tests use deterministic mocks (no network, no API keys).
Full suite: ~3 seconds on a typical dev laptop.

## Running

```bash
# E2E scenarios
pytest tests/e2e/ -v

# Persistence
pytest tests/integration/test_persistence_integrity.py -v

# Perf check vs stored baseline
pytest tests/integration/test_perf_baseline.py -m perf -v

# Re-capture the perf baseline (e.g. after a perf-related refactor)
PERSPICACITE_UPDATE_PERF_BASELINE=1 pytest tests/integration/test_perf_baseline.py -m perf -v
git add tests/data/perf_baseline.json && git commit -m "perf: refresh baseline"

# All Wave 6 tests at once
pytest tests/e2e/ tests/integration/test_persistence_integrity.py \
       tests/integration/test_perf_baseline.py -v
```

## The three E2E scenarios

| File | What it covers |
|---|---|
| `test_single_paper.py` | KB build → ingest 1 Paper → search → retrieve by paper_id. Single round-trip through chunking + embedding + Chroma. |
| `test_multi_paper_citations.py` | 5-paper ingest + simulated citation expansion. Verifies the KB log (Wave 4.3) carries the right events including the expanded reference. |
| `test_cross_kb_routing.py` | Two topically-distinct KBs (astro / bio). `auto_route_kbs` picks the correct one for queries on each side. |

## The 8 persistence checks

- `test_kb_survives_close_reopen`
- `test_chroma_collection_persists`
- `test_concurrent_kb_log_appends` — 4 tasks × 100 events on the same file → all 400 land, no torn lines.
- `test_concurrent_session_store_writes` — concurrent SQLite KB-metadata inserts hold under WAL.
- `test_session_store_reopen_preserves_rows`
- `test_checkpoint_survives_kill_mid_save` — Wave 3.3 atomic-save invariant.
- `test_llm_cache_survives_reopen` — Wave 2.1 disk cache.
- `test_embedding_cache_dedup_across_reopens` — Wave 2.2.

## Perf baseline knobs

| Env var | Default | Effect |
|---|---|---|
| `PERSPICACITE_UPDATE_PERF_BASELINE` | unset | Set to `1` to regenerate `tests/data/perf_baseline.json` (test then skips). |
| `PERSPICACITE_PERF_TOLERANCE` | `1.30` | Ratio cur/baseline above which the test fails. |

Metrics:

```json
{
  "ingest_5_papers_seconds":   "<float, lower-is-better>",
  "search_top10_seconds_avg":  "<float, lower-is-better>",
  "report_synthesis_seconds":  "<float, lower-is-better>",
  "embeddings_per_second":     "<float, higher-is-better>",
  "kb_log_writes_per_second":  "<float, higher-is-better>",
  "git_sha":  "<str>",
  "timestamp": "<unix float>"
}
```

Sub-10ms timings are below the noise floor and skip the regression
check (still recorded for trend analysis).

## What is NOT covered here

- **Real-LLM E2E** — the user runs the full pipeline against API LLMs
  on two example queries, and again with Claude Code, comparing
  outputs. This audit lives outside CI.
- **Output quality** — BLEU / ROUGE / expert judgement against gold
  corpora is a separate effort.
- **Multi-process OS-level concurrency** — only async-task concurrency
  within a single process is tested.

## Updating the baseline

When you intentionally improve performance (e.g. swap to a faster
embedder, batch a hot path), regenerate the baseline and commit the
new `perf_baseline.json` along with the change. The commit message
should explain why the baseline moved.

When the baseline gets stale on a new machine (e.g. CI runner is
slower than dev box), prefer raising `PERSPICACITE_PERF_TOLERANCE`
for that environment over regenerating the baseline.
