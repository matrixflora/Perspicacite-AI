# Resume/checkpoint for multi-paper ingests — design spec

**Wave 3.3 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** When a multi-paper ingest crashes (rate limit, network
glitch, OOM), re-running the same command resumes from the failed
paper instead of restarting from zero.

## Scope

v1 wires the checkpoint into `ingest_dois_into_kb` only — the most
common multi-paper entry point. `search_filter_and_ingest`,
`snowball`, `bibtex_kb`, and `external/fetch_orchestrator` are
followups; they iterate over similar lists and can plug in the same
`CheckpointStore` once the pattern is proven.

## Architecture

A small file-backed `CheckpointStore` per (KB, operation):

```
data/checkpoints/<kb_name>__<operation>.json
```

Contents:

```json
{
  "kb_name": "exposomics",
  "operation": "ingest_dois",
  "started_at": 1731575689,
  "updated_at": 1731575900,
  "total_planned": 100,
  "processed": {
    "10.1234/abc": "added",
    "10.5678/def": "skipped",
    "10.9999/xyz": "failed: timeout reading PDF"
  }
}
```

The ingest loop:

1. Computes `remaining = [doi for doi in planned if doi not in processed]`.
2. For each remaining DOI, runs the existing work + calls
   `ckpt.record(doi, outcome)`.
3. `ckpt.save()` after **every** record — atomic JSON write so a
   mid-write crash never leaves a half-written file.
4. On clean completion, `ckpt.delete()` so a future run with the
   same args starts fresh.

If the user wants to ignore an existing checkpoint, pass
`resume=False` to the ingest function — explicit "start over".

## Atomic save

`save()` writes to `<path>.tmp` then `os.replace(...)` onto the final
path. POSIX `rename` is atomic for files on the same filesystem;
Windows `os.replace` is also atomic since Python 3.3. So a SIGKILL
mid-save either keeps the previous valid file or commits the new
one — never a partial.

## API

```python
from perspicacite.pipeline.checkpoint import CheckpointStore

ckpt = CheckpointStore(
    path=checkpoint_path,
    kb_name=kb_name,
    operation="ingest_dois",
)
state = ckpt.load_or_create(planned_ids=dois)
for doi in state.remaining_ids():
    try:
        await process(doi)
        state.record(doi, "added")
    except Exception as e:
        state.record(doi, "failed", reason=str(e))
    state.save()
if state.is_complete():
    ckpt.delete()
```

## Outcomes vocabulary

- `"added"` — paper successfully added to the KB.
- `"skipped"` — already in the KB (de-dup); not a failure, but
  shouldn't be retried.
- `"failed: <short reason>"` — capped at 200 chars. On resume, the
  same DOI is **not** retried unless `retry_failed=True` is passed.

## Behaviour contract

- Existing checkpoint file present + `resume=True` (default):
  remaining work runs, processed entries are skipped.
- Existing checkpoint file present + `resume=False`: file is deleted
  first, then a fresh checkpoint is created.
- No checkpoint file: behave as today, but create one and write
  through it.
- `retry_failed=True` flag (Wave 3.3 v1): when set, entries with
  `"failed: ..."` state are re-added to `remaining_ids`.

## Components

| File | Change |
|---|---|
| `src/perspicacite/pipeline/checkpoint.py` (new) | `CheckpointStore`, `CheckpointState`, atomic JSON save. |
| `src/perspicacite/pipeline/search_to_kb.py` (modify) | Wire into `ingest_dois_into_kb`. Add `resume: bool = True`, `retry_failed: bool = False` kwargs. |
| `src/perspicacite/config/schema.py` (modify) | Add `kb.checkpoint_dir: Path = Path("data/checkpoints")`. |
| `tests/unit/test_checkpoint_store.py` (new) | Load/save roundtrip, atomic save, remaining_ids, retry_failed. |
| `tests/unit/test_ingest_dois_resume.py` (new) | Mocked ingest: crash after 2 of 5; resume picks up at 3. |

## Test plan

- `test_load_returns_none_when_file_missing`
- `test_save_then_load_roundtrip`
- `test_record_adds_to_processed`
- `test_remaining_ids_excludes_processed`
- `test_atomic_save_via_replace` (verify tmp file is gone after save)
- `test_is_complete_when_all_planned_processed`
- `test_retry_failed_re_includes_failed_ids`
- `test_delete_removes_file`
- `test_ingest_dois_resume_skips_already_added` (integration with mocked KB)

## Followups

- Wire `search_filter_and_ingest`, `snowball`, `bibtex_kb` (one PR per).
- Per-stage sub-checkpoints (e.g., "downloaded but not chunked").
- TTL on stale checkpoints (delete after 7 days untouched).
- CLI tool: `perspicacite checkpoint list` / `clear` / `show`.
