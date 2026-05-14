# Time-bounded queries — design spec

**Wave 4.2 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Let users restrict KB search to a publication-year window
(`year_min` / `year_max`). Today the building blocks exist
(`SearchFilters.year_min/max` and `_filters_to_where` in `chroma_store`)
but the MCP `search_knowledge_base` tool doesn't expose them.

## What's already in place

- `models/search.py::SearchFilters` has `year_min: int | None`,
  `year_max: int | None`.
- `retrieval/chroma_store.py::_filters_to_where` translates these to
  Chroma `$gte` / `$lte` where-clauses.
- `mcp/server.py::search_literature` (external metadata search) already
  surfaces `year_min` / `year_max`.

## What's missing

- `DynamicKnowledgeBase.search` doesn't accept filters.
- `search_knowledge_base` MCP tool doesn't expose year params.

## Scope of v1

In scope:
- Plumb `filters` through `DynamicKnowledgeBase.search`.
- Add `year_min` / `year_max` to `search_knowledge_base`.

Out of scope (followups):
- Wiring into `generate_report` (deeper RAG-engine plumbing — needs
  its own audit pass).
- Month/day granularity (`papers_published_after: "2024-06"`).
  Today's chunk metadata only stores year.
- Aliases like `published_after` / `published_before` — match existing
  vocabulary (`year_min` / `year_max`) for consistency.

## Architecture

```
search_knowledge_base(year_min=..., year_max=...)
  └── SearchFilters(year_min, year_max)
        └── DynamicKnowledgeBase.search(query, filters=...)
              └── vector_store.search(query_embedding, filters=...)
                    └── _filters_to_where(filters)        # already exists
                          → {"$and": [{"year": {"$gte": ...}}, ...]}
```

## Components

| File | Change |
|---|---|
| `src/perspicacite/rag/dynamic_kb.py` | `DynamicKnowledgeBase.search(filters=None)` — pass to `vector_store.search`. |
| `src/perspicacite/mcp/server.py` | Add `year_min`, `year_max` to `search_knowledge_base`; build `SearchFilters` and pass through. |
| `tests/unit/test_dynamic_kb_filters.py` (new) | Filters reach `vector_store.search`. |
| `tests/unit/test_mcp_search_kb_year_filters.py` (new) | MCP tool surfaces year params and builds the filter correctly. |

## Behaviour contract

- No year params provided → behaviour unchanged (no filter built).
- Only `year_min` provided → `SearchFilters(year_min=X, year_max=None)`.
- Only `year_max` provided → `SearchFilters(year_min=None, year_max=Y)`.
- Both provided with `year_min > year_max` → caller's responsibility;
  Chroma will simply return zero results. Don't raise.
- Year is stored in chunk metadata as an int (already the contract).
  Chunks without a year (`year: None`) are excluded by the filter
  when either bound is set — this is the Chroma behaviour and matches
  the principle "if you asked for a year window, you don't want
  undated material."

## Test plan

- `test_dynamic_kb_search_passes_filters_to_store`
- `test_dynamic_kb_search_without_filters_unchanged`
- `test_mcp_search_kb_builds_filters_from_year_params`
- `test_mcp_search_kb_no_year_params_no_filters`
- `test_mcp_search_kb_only_year_min`
- `test_mcp_search_kb_only_year_max`

## Followups

- Plumb through multi-KB retrieval (`MultiKBRetriever.search`).
- Plumb through `generate_report` so synthesis only summarises papers
  in the time window.
- Month/day granularity once chunk metadata grows a full `published_at`
  field.
