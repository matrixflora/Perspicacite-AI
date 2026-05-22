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
- **Author searches:** set `optimize_query=False`, or better, resolve the author
  to an ORCID/OpenAlex id and filter by it. The rewrite is tuned for topical
  recall and may drop a bare surname it does not recognise as a scientific term
  (e.g. "Libis" → dropped), turning an author search into a topic search.
- For passage search (`search_by_passage`), pass the **raw** sentence/paragraph
  text — that tool matches on the literal input, so do not rewrite it.

# 3. Database selection

`suggest_databases` runs a fast deterministic heuristic (no LLM, ~25ms) —
always worth calling when the domain is unfamiliar. Pass its `recommended`
list directly as `databases=[...]` to `search_literature` or `generate_report`.
Known databases: `semantic_scholar`, `openalex`, `pubmed`, `europepmc`,
`arxiv`, `pubchem`, `crossref`, `inspire`.

# 4. Tool-choice decision table

| You want | Call |
|----------|------|
| A synthesized, cited answer | `generate_report` |
| A raw list of candidate papers | `search_literature` |
| Passages similar to a sentence/paragraph you hold | `search_by_passage` |
| Keyword passages from a KB | `get_relevant_passages(adaptive=True)` |
| Structured numeric parameters from passages | `extract_parameters_from_passages` |
| Documented failure modes / limitations from passages | `extract_failure_modes_from_passages` |
| Ambiguous or multi-step research | `generate_report(mode="agentic")` |
| Which of my KBs is most relevant to a query | `route_kbs` |
| Grow a KB by following citation edges | `expand_kb_via_citations` |

Use `adaptive=True` on `get_relevant_passages` for terse or jargon-heavy
queries: the server runs the optimizer once and retries if the first pass
returns nothing. The response `attempts` array and `refined_query` field show
whether the retry fired.

**Extraction tools (`extract_*_from_passages`) — critical:**  
Pass the `passages` list returned by `get_relevant_passages` **as-is** — do
not serialise it to a string, truncate it, or restructure it. The tool
validates the list structure on arrival; a stringified or restructured payload
causes an immediate error (~100ms). The correct call pattern is:

```
result = get_relevant_passages(query=..., kb_name=..., k=8)
extract_failure_modes_from_passages(
    passages=result["passages"],   # exact list, not str(result["passages"])
    context="<domain description>"
)
```

# 5. KB management pitfalls

**Paywalled or inaccessible DOIs silently produce empty KBs.**  
`add_dois_to_kb` gracefully skips papers it cannot retrieve (no OA PDF, no
PMC XML, no arXiv). The response shows `added` / `skipped` / `errored`
counts. Always check that `added >= 1` before calling `search_by_passage`,
`get_relevant_passages`, or `expand_kb_via_citations` on that KB — operating
on an empty KB returns zero results without an error, which can look like a
retrieval failure when it is really an ingestion failure.

**Abstract-only papers produce one chunk, not paragraph-level chunks.**  
When full text cannot be fetched, Perspicacité indexes the paper as a single
metadata chunk: `Title / Authors / Year / DOI / Abstract`. This chunk IS
searchable and will appear in `search_by_passage` results — but a paper with
full text produces many more chunks, so full-text papers dominate ranked
results. Expect lower recall from abstract-only papers on specific passage
queries.

**Multi-KB retrieval uses `kb_names` (list), not `kb_name` (string).**  
`search_by_passage`, `get_relevant_passages`, and `generate_report` accept
either `kb_name="single_kb"` or `kb_names=["kb_a", "kb_b"]` — never both.
When passing `kb_names`, all KBs must share the same embedding model; the
server checks compatibility and errors if they differ. Each result hit carries
`kb_name` identifying its origin KB.

**KB routing before blind multi-KB search.**  
When you hold several KBs and are unsure which is most relevant, call
`route_kbs(query=..., kb_names=[...])` first. It returns a ranked list so
you can target `search_by_passage` at the winner rather than fan-out across
all KBs.

# 6. Mode and screening (`generate_report`)

- `basic` — quick single-pass retrieval + synthesis, no rerank.
- `advanced` — screening + rerank with query expansion (default).
- `profound` — deep multi-cycle research with planning + reflection.
- `contradiction` — surfaces agreement / disagreement / open questions.
- `agentic` — multi-step, intent-driven orchestration with tool use.
- `literature_survey` — broad survey with theme clustering + recommendations.

When precision matters, set `screen_method` (`bm25` | `rerank` | `llm`) and a
`screen_threshold` in [0, 1]. Use `max_papers_to_download` to cap full-text
fetches, `recency_weight` (>0) to bias toward recent work, and `kb_names` to
fan across several knowledge bases (they must share an embedding model).

# 7. Reading results

- Every tool returns a JSON string with a `{success: true/false, ...}`
  envelope. Check `success` before using the payload; on failure read `error`.
- Some responses append `META: {...}` telemetry tails and embed fields such as
  `usage` (tokens/cost), `attempts` (passage-retrieval tries), and
  `query_rephrasings` / `refined_query`. Use these to see whether a retry fired
  or the query was rewritten.
- `add_dois_to_kb` response carries `added`, `skipped`, and `errored` counts.
  Treat `added == 0` as a signal to investigate access before proceeding.

# 8. Live reference

Treat this file as a snapshot. For the authoritative current capabilities,
decision rules, full tool index, and knob defaults, call `get_usage_guide` —
prefer it over trusting this snapshot when planning a multi-step task.
