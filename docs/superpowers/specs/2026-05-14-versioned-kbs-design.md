# Versioned KBs (append log) — design spec

**Wave 4.3 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Track every paper-add event per KB in an append-only JSONL
log. Enables rollback (drop everything added after a timestamp /
commit point) and provenance audit (when was this paper added, by
which command).

## Why now

Today, the KB stores chunks in Chroma + metadata in
`session_store.kb_metadata`. Neither records a history. A botched
ingest contaminates the KB permanently unless the user reconstructs
it from scratch.

An append-only event log lets us answer:

- *"When was paper 10.1234/x added to KB 'astro'?"* — scan log.
- *"Undo yesterday's ingest"* — replay log, drop events newer than
  a timestamp.
- *"Show provenance of every paper in KB 'foo'"* — scan log.

## Architecture

Each KB has its own `data/kb_logs/<kb_name>.jsonl` (one JSON object
per line). Each line records one event:

```json
{
  "ts": 1731575689,
  "event": "paper_added",
  "kb_name": "astro",
  "paper_id": "10.1234/example",
  "title": "...",
  "source_doi": "10.1234/example",
  "source_command": "ingest_dois_into_kb",
  "chunks": 17,
  "operator_label": null
}
```

Event types:

| event | When recorded |
|---|---|
| `paper_added` | After `dkb.add_papers(...)` succeeds and metadata is updated. |
| `paper_skipped` | When a paper is dropped as duplicate. |
| `paper_failed` | When ingest fails for a paper (carries the reason). |
| `kb_created` | When `create_knowledge_base` first writes the KB. |
| `kb_pruned` | When a rollback / manual prune removes papers. |

The log is *append-only*. Rollback is implemented by writing a new
`kb_pruned` event with the dropped paper IDs, not by editing previous
lines.

## Components

| File | Change |
|---|---|
| `src/perspicacite/pipeline/kb_log.py` (new) | `KBLogWriter` — atomic append + read. `KBEvent` dataclass. |
| `src/perspicacite/pipeline/search_to_kb.py` | Record `paper_added` / `paper_skipped` / `paper_failed` in `ingest_dois_into_kb`. |
| `src/perspicacite/config/schema.py` | Add `kb.log_dir: Path = Path("data/kb_logs")`. |
| `tests/unit/test_kb_log.py` (new) | Append, read-back, ordering, atomic append, rollback. |
| `tests/unit/test_ingest_dois_kb_log.py` (new) | Integration: a successful ingest emits the right events. |

## API

```python
from perspicacite.pipeline.kb_log import KBLogWriter, KBEvent

writer = KBLogWriter(path=Path("data/kb_logs/astro.jsonl"))
writer.append(KBEvent(
    event="paper_added",
    kb_name="astro",
    paper_id="10.1234/x",
    title="...",
    source_command="ingest_dois_into_kb",
    chunks=12,
))

# Read back:
events = writer.read_all()
recent = writer.read_after(ts=1731_000_000)
```

## Concurrency & durability

- Each append opens the file in `"a"` mode, writes one line, fsyncs,
  and closes. POSIX append-mode writes are atomic for buffers ≤
  PIPE_BUF — our JSON lines are well under that on typical systems
  (typically 4096 bytes). Lines never interleave.
- No locking required. Concurrent ingests on different KBs hit
  different files. Concurrent ingests on the same KB serialise
  through the OS append guarantee.
- A SIGKILL mid-write may leave a partial line at EOF. The reader
  tolerates this by skipping `json.JSONDecodeError` on the last
  line only (silent recovery, logs a warning).

## Behaviour contract

- Disabled / log path missing → `KBLogWriter` creates the parent
  directory on first append. Never raises in the hot path.
- Read on a missing file → returns `[]` (empty list), no exception.
- Malformed line → logged, skipped. One bad line doesn't taint the
  rest of the log.
- The recording call is best-effort: a write failure (disk full,
  permissions) logs an error but does not propagate to the ingest
  loop. Provenance is nice-to-have, not load-bearing.

## Rollback (v1 scope)

A helper `rollback_after(ts: int) -> list[str]`:

- Reads all `paper_added` events newer than `ts`.
- Returns the list of paper IDs to drop.
- Appends a `kb_pruned` event recording what was rolled back.

The actual chunk-drop is the caller's responsibility (vector store +
KB metadata) — this v1 ships just the log + the candidate list.
Full rollback orchestration is a follow-up.

## Test plan

- `test_append_writes_one_line_per_event`
- `test_read_all_returns_events_in_order`
- `test_read_all_on_missing_file_returns_empty`
- `test_partial_line_at_eof_skipped`
- `test_concurrent_appends_dont_interleave_on_same_file`
- `test_read_after_filters_by_timestamp`
- `test_rollback_after_returns_paper_ids_and_records_event`
- `test_ingest_dois_emits_paper_added_event` (integration with
  mocked `ingest_dois_into_kb`).
- `test_ingest_dois_emits_paper_skipped_for_duplicate`
- `test_ingest_dois_emits_paper_failed_with_reason`

## Followups

- Full rollback orchestration (drop chunks + recompute KB metadata).
- `perspicacite kb log <kb_name>` CLI for human inspection.
- KB diff: "papers added between timestamps X and Y".
- Wire `add_papers_to_kb`, `add_dois_to_kb` MCP tools, `snowball`,
  `bibtex_kb`, `external/fetch_orchestrator` to the same logger.
- Cross-link with Wave 3.3 checkpoint store for resumable ingests
  that emit log events on retry.
