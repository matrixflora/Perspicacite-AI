# Checkpoint & resume — operator guide (2026-05-14)

Wave 3.3 of the framework-hardening roadmap. Crash-resilient
multi-paper ingests.

## What it does

`ingest_dois_into_kb` writes a JSON file per (KB, operation) to
`data/checkpoints/<kb>__<op>.json`. After each DOI is processed
(added / skipped / failed), the file is atomically updated. If the
process crashes — or you Ctrl-C — the next run with the same DOIs
picks up where the previous left off.

## Behaviour

```bash
# First run, network glitch at DOI 47 of 100:
perspicacite ingest-dois mykb dois.txt
# → 46 added, then RuntimeError. Checkpoint shows 46 done.

# Second run — same command:
perspicacite ingest-dois mykb dois.txt
# → 54 added (DOIs 47-100). Checkpoint deleted on clean completion.
```

## Knobs

| Kwarg | Default | Effect |
|---|---|---|
| `resume` | `True` | Honour existing checkpoint. Pass `False` to start fresh. |
| `retry_failed` | `False` | Re-attempt DOIs that previously failed. |

```python
await ingest_dois_into_kb(
    app_state, "mykb", dois,
    resume=False,            # force restart
)
await ingest_dois_into_kb(
    app_state, "mykb", dois,
    retry_failed=True,       # retry the 3 PDFs that timed out
)
```

## File format

```json
{
  "kb_name": "mykb",
  "operation": "ingest_dois",
  "started_at": 1731575689,
  "updated_at": 1731575900,
  "total_planned": 100,
  "planned_ids": ["10.1/a", "10.2/b", ...],
  "processed": {
    "10.1/a": "added",
    "10.2/b": "skipped",
    "10.3/c": "failed: timeout reading PDF"
  }
}
```

Atomically written via tmp-file + `os.replace`. SIGKILL mid-write
leaves the file in its previous valid state — never half-written.

## Manual cleanup

```bash
# Inspect:
ls data/checkpoints/
cat data/checkpoints/mykb__ingest_dois.json | jq .processed

# Wipe checkpoint for a KB:
rm data/checkpoints/mykb__ingest_dois.json
```

## Config

```yaml
kb:
  checkpoint_dir: data/checkpoints     # default
```

## Scope today

Wired into:

- `ingest_dois_into_kb` ✅

Followups (separate sub-projects):

- `search_filter_and_ingest`
- `snowball.expand_kb_via_citations`
- `bibtex_kb.build_kb_from_bibtex`
- `external/fetch_orchestrator.run`

Each is a small mechanical change once the pattern is proven.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/checkpoint.py` | `CheckpointStore`, `CheckpointState`, atomic save |
| `src/perspicacite/pipeline/search_to_kb.py` | wiring in `ingest_dois_into_kb` |
| `src/perspicacite/config/schema.py` | `kb.checkpoint_dir` field |
