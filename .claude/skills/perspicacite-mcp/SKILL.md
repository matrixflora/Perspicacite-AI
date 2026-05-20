---
name: perspicacite-mcp
description: Use the Perspicacité literature-research MCP server intelligently — query shaping, database selection, tool/mode choice, and reading the result envelope.
---

# Goal

Drive the Perspicacité MCP server well: translate and shape queries, pick the
right database fan-out, choose the correct tool and report mode, and read the
`{success, ...}` response envelope. This file is a stable snapshot; for the
authoritative, current tool index call `get_usage_guide` (see Live reference).

# 1. Translate non-English queries

The pipeline is tuned for English. Translate any non-English user query to
English before searching, but keep the original wording for display and for the
citation/quote you show back to the user.

# 2. Query shaping

- Pass the user's text through largely **verbatim**. Do not pre-rephrase it.
- For literature search, leave `optimize_query` on (default) so the server runs
  its own one-shot LLM rewrite; set `optimize_query=False` only when you need an
  exact verbatim query. The response surfaces `fallback_reason` if the rewrite
  failed and the verbatim query was used.
- For passage search (`search_by_passage`), pass the **raw** sentence/paragraph
  text — that tool matches on the literal input, so do not rewrite it.

# 3. Database selection

When unsure which databases fit the topic, call `suggest_databases(query)`
first, then pass its recommendations as `databases=[...]` to `search_literature`
or `generate_report`. Known databases: `semantic_scholar`, `openalex`,
`pubmed`, `arxiv`.

# 4. Tool-choice decision table

| You want | Call |
|----------|------|
| A synthesized, cited answer | `generate_report` |
| A raw list of candidate papers | `search_literature` |
| Passages similar to a sentence/paragraph you hold | `search_by_passage` |
| Keyword passages from a KB | `get_relevant_passages(adaptive=True)` |
| Structured numeric parameters from passages | `extract_parameters_from_passages` |
| Documented failure modes from passages | `extract_failure_modes_from_passages` |
| Ambiguous or multi-step research | `generate_report(mode="agentic")` |

Use `adaptive=True` on `get_relevant_passages` for terse or jargon-heavy
queries: the server runs the optimizer once and retries if the first pass
returns nothing.

# 5. Mode and screening (`generate_report`)

- `basic` — quick single-pass retrieval + synthesis, no rerank.
- `advanced` — screening + rerank with query expansion (default).
- `profound` — deep multi-cycle research with planning + reflection.
- `contradiction` — surfaces agreement / disagreement / open questions.
- `agentic` — multi-step, intent-driven orchestration with tool use.
- `literature_survey` — broad survey with theme clustering + recommendations.

When precision matters, set `screen_method` (`bm25` | `rerank` | `llm`) and a
`screen_threshold` in [0, 1]. Use `max_papers_to_download` to cap full-text
fetches, `recency_weight` (>0) to bias toward recent work, and `kb_names` to fan
across several knowledge bases (they must share an embedding model).

# 6. Reading results

- Every tool returns a JSON string with a `{success: true/false, ...}`
  envelope. Check `success` before using the payload; on failure read `error`.
- Some responses append `META: {...}` telemetry tails and embed fields such as
  `usage` (tokens/cost), `attempts` (passage-retrieval tries), and
  `query_rephrasings` / `refined_query`. Use these to see whether a retry fired
  or the query was rewritten.

# 7. Live reference

Treat this file as a snapshot. For the authoritative current capabilities,
decision rules, full tool index, and knob defaults, call `get_usage_guide` —
prefer it over trusting this snapshot when planning a multi-step task.
