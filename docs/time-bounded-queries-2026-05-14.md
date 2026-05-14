# Time-bounded queries — operator guide (2026-05-14)

Wave 4.2 of the framework-hardening roadmap. Restrict KB search to a
publication-year window.

## Usage

```python
# MCP tool call
search_knowledge_base(
    query="DESI redshift surveys",
    kb_name="astro",
    year_min=2020,        # inclusive lower bound
    year_max=2024,        # inclusive upper bound
)
```

Either bound is optional. Omit one for an open window in that
direction.

## How it works

The two parameters become a `SearchFilters(year_min=..., year_max=...)`
object. The existing `_filters_to_where` translator converts that
to a Chroma where-clause:

```
{"$and": [
  {"year": {"$gte": 2020}},
  {"year": {"$lte": 2024}}
]}
```

Chunks without a `year` field are excluded once either bound is
set — if you asked for a year window, undated material is silently
dropped.

## Limitations (today)

- **Multi-KB mode** (`kb_names=[...]` with len > 1) does not yet
  pass filters through `MultiKBRetriever`. Year params are accepted
  but ignored, with a warning log. Wave 4.2 followup.
- **`generate_report`** doesn't surface year filters yet. Followup.
- **Granularity**: chunk metadata stores `year: int` only — no month
  or day. Filtering on full publication dates requires a metadata
  schema bump, deferred to a future wave.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/models/search.py` | `SearchFilters` (existed) |
| `src/perspicacite/retrieval/chroma_store.py` | `_filters_to_where` (existed) |
| `src/perspicacite/rag/dynamic_kb.py` | now accepts `filters` |
| `src/perspicacite/mcp/server.py` | `year_min` / `year_max` MCP params |

## Followups

- `MultiKBRetriever.search` plumbing.
- `generate_report` filter passthrough.
- Month-level granularity once metadata grows a `published_at` field.
