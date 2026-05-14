# Versioned KBs (append log) — operator guide (2026-05-14)

Wave 4.3 of the framework-hardening roadmap. Append-only JSONL event
log per knowledge base.

## What it gives you

A line-per-event audit trail at `data/kb_logs/<kb>.jsonl`:

```json
{"event":"paper_added","kb_name":"astro","paper_id":"10.1234/x","title":"...","ts":1731575689,"source_command":"ingest_dois_into_kb"}
{"event":"paper_skipped","kb_name":"astro","paper_id":"10.1234/y","ts":1731575700,"source_command":"ingest_dois_into_kb"}
{"event":"paper_failed","kb_name":"astro","paper_id":"10.1234/z","reason":"network down","ts":1731575710,"source_command":"ingest_dois_into_kb"}
```

## Event types

| event | When |
|---|---|
| `kb_created` | First write of a KB (followup — not emitted today). |
| `paper_added` | Paper successfully prepared for insertion. |
| `paper_skipped` | Duplicate de-dup'd. |
| `paper_failed` | Ingest failed for this paper. `reason` carries the error message. |
| `kb_pruned` | Rollback recorded — `extra.rolled_back_paper_ids` lists what's gone. |

## Inspecting

```bash
# Human read:
cat data/kb_logs/astro.jsonl | jq -c '{event, paper_id, ts}'

# When was paper X added?
grep '"paper_id":"10.1234/x"' data/kb_logs/astro.jsonl | jq .

# All failures:
jq -c 'select(.event=="paper_failed")' data/kb_logs/astro.jsonl
```

## Programmatic API

```python
from perspicacite.pipeline.kb_log import KBLogWriter
from pathlib import Path

w = KBLogWriter(path=Path("data/kb_logs/astro.jsonl"))
recent = w.read_after(ts=1731_000_000)   # events after Nov 2024
ids_to_drop = w.rollback_after(ts=1731_500_000)
# ids_to_drop is the list of paper_ids to remove from the KB.
# A `kb_pruned` event is appended automatically.
```

## What it's NOT (v1)

- **Not a full rollback orchestrator.** `rollback_after` returns the
  candidate paper IDs and records the event; actually dropping chunks
  from Chroma + updating KB metadata is the caller's job. A higher-
  level `rollback(kb, ts)` helper is a followup.
- **Not a transaction log.** Events are recorded best-effort —
  write failures are logged but never propagate (we don't want
  provenance to break ingest).
- **Not synchronous across processes.** Concurrent appends to the
  same file are safe (POSIX atomic <= 4 KB), but readers may see a
  partial last line during a kill-9. Readers tolerate that.

## Coverage today

Only `ingest_dois_into_kb` emits events. Other ingest paths are
documented followups:

- `add_papers_to_kb`, `add_dois_to_kb` (MCP tools, share code with
  `ingest_dois_into_kb` partially)
- `snowball.expand_kb_via_citations`
- `bibtex_kb.build_kb_from_bibtex`
- `external/fetch_orchestrator.run`

Each is a small mechanical change once we audit the entry points.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/kb_log.py` | `KBLogWriter`, `KBEvent`, append + read + rollback helper |
| `src/perspicacite/pipeline/search_to_kb.py` | Emit events from `ingest_dois_into_kb` |
| `src/perspicacite/config/schema.py` | `kb.log_dir` field |
