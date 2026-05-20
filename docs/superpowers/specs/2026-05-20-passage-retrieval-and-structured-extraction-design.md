# Passage retrieval & structured extraction — design

**Status:** Draft — pending user review
**Date:** 2026-05-20
**Author:** Louis-Félix Nothias (via Claude)
**Scope:** Themes C + D from the 2026-05-20 frontend-vs-MCP brainstorm.
**Affected repos:** `Perspicacite-AI` (server, primary), `AgenticScienceBuilder` (consumer), `Scriptorium` (consumer).

## Motivation

The new Perspicacité front-end (poc/v2b series + `perspicacite_design_1.jsx`) surfaces methodological knobs and stream signals that MCP consumers do not get. Two downstream consumers — ASB's `enrich-skills-mcp` flow and Scriptorium's manuscript drafting — are working around capability gaps:

- **Scriptorium** uses ~3 of 27 MCP tools and has **no passage-level (sentence/paragraph) retrieval**. Writers have to stop and type keyword queries via `/cite` instead of asking "find papers in my KB similar to this paragraph".
- **ASB** regex-mines parameters and failure modes out of passage text in `skill_pack_v3.py` and silently produces empty enrichments when `get_relevant_passages` returns zero results (no adaptive query refinement).

This spec proposes server-side capabilities that close both gaps in a shared, coherent way.

Themes A (stream parity) and B (knob parity) from the brainstorm are explicitly out of scope here — separate sprints.

## Goals

1. **Passage-level retrieval as a first-class MCP tool** that takes arbitrary text and returns ranked KB chunks.
2. **Server-side structured extraction** of parameters and failure modes from passages, with license-tier-aware quote handling.
3. **Adaptive query refinement** for `get_relevant_passages` so callers stop silently producing empty results.
4. **A Scriptorium slash command** (`/find-related`) that uses passage retrieval to surface paper suggestions for a sentence/paragraph the writer is composing.
5. **ASB enrichment** that calls the new extraction tools and drops its regex pipeline.

## Non-goals

- Streaming progress parity (Theme A).
- Adding screen-method / screen-threshold / max-papers-to-download knobs to existing tools (Theme B).
- Implementing a real `search_related_papers` (ASB's no-op stub stays — separate follow-up).
- A post-paragraph auto-suggest hook in Scriptorium's `/draft-section` (validate the slash-command UX first).
- Multi-paragraph context window for extraction.

## Architecture

### Component map

```
Perspicacite-AI (server)
├── src/perspicacite/mcp/tools.py
│   ├── search_by_passage              [NEW]
│   ├── extract_parameters_from_passages    [NEW]
│   ├── extract_failure_modes_from_passages [NEW]
│   └── get_relevant_passages          [ENHANCED — adaptive kwarg]
├── src/perspicacite/pipeline/
│   ├── passage_search.py              [NEW] — embedding-based KB query
│   ├── extraction.py                  [NEW] — LLM JSON-schema extraction
│   ├── query_optimizer.py             [REUSED] — adaptive retry
│   └── license_tier.py                [REUSED or extracted] — quote handling
└── tests/
    ├── test_passage_search.py
    ├── test_extraction.py
    └── test_adaptive_relevant_passages.py

AgenticScienceBuilder (consumer)
├── src/agentic_science_builder/perspicacite_client.py
│   ├── search_by_passage()                  [NEW]
│   ├── extract_parameters()                 [NEW]
│   ├── extract_failure_modes()              [NEW]
│   └── get_relevant_passages(adaptive=True) [UPDATED]
└── src/agentic_science_builder/skill_pack_v3.py
    ├── enrich_parameters_from_passages()    [REWRITTEN — drop regex]
    └── enrich_failure_modes_from_passages() [REWRITTEN — drop regex]

Scriptorium (consumer)
├── scriptorium/literature/passage_search.py   [NEW] — thin wrapper
└── .claude/commands/find-related.md           [NEW] — slash command
```

### Data flow — Scriptorium `/find-related`

```
Writer composing → /find-related "<paragraph>"
  → search_by_passage(text=paragraph, kb_names=[project_kb], k=5)
  → top-5 PassageMatch
  → render ranked list (score, title, year, venue, DOI)
  → writer runs /cite <n>
  → existing flow: KGmemory triple + refs/references.bib update
```

### Data flow — ASB enrichment

```
skill.md → first 3 tools as query
  → get_relevant_passages(query, k=10, adaptive=True)
  → {passages, attempts, refined_query?}
  → extract_parameters_from_passages(passages, context=skill_name, parameter_families=[...])
  → ParameterSpec[] → parameters.json
  (parallel)
  → extract_failure_modes_from_passages(passages, context=skill_name)
  → FailureMode[] → failure_modes.jsonl
```

## Tool contracts

### `search_by_passage` (NEW)

Embed arbitrary text and return top-k KB chunks.

**Input**
| Field | Type | Notes |
|---|---|---|
| `text` | `str` | 1–4000 chars; longer is `400 invalid_input`. Caller chunks long inputs. |
| `kb_names` | `list[str] \| None` | None = search all KBs the server has access to. |
| `k` | `int` | Default 5, max 50. |
| `min_score` | `float \| None` | Drop matches below this similarity. |

**Output**
```json
[
  {
    "chunk_id": "kb_foo:doi:10.x/y:chunk:42",
    "chunk_text": "…",
    "score": 0.83,
    "source": {
      "doi": "10.1234/abc",
      "title": "...",
      "authors": ["..."],
      "year": 2024,
      "bibkey": "smith2024foo",
      "source_url": "https://...",
      "license_id": "CC-BY"
    },
    "kb_name": "my_project_kb"
  }
]
```

**Errors**
- `404 kb_not_found` if any `kb_names` entry does not exist
- `400 invalid_input` if text empty or > 4000 chars
- Empty result list is **not** an error.

### `extract_parameters_from_passages` (NEW)

Server-side LLM extraction of numeric parameters from a passage list.

**Input**
| Field | Type | Notes |
|---|---|---|
| `passages` | `list[Passage]` | Existing schema. |
| `context` | `str \| None` | Skill name / domain hint (improves recall). |
| `parameter_families` | `list[str] \| None` | E.g., `["threshold","concentration","pH","temperature"]`. None → server defaults. |

**Output**
```json
[
  {
    "name": "temperature",
    "type": "numeric",
    "typical": "37",
    "units": "°C",
    "min": "25",
    "max": "42",
    "source_doi": "10.1234/abc",
    "source_quote": "...",
    "confidence": 0.9
  }
]
```

**Behavior**
- Server batches passages (max 8 per LLM call).
- Uses the existing JSON-salvage utility on invalid output; on second failure returns `[]` plus a warning in response metadata.
- License tier of the source passage drives `source_quote` handling:
  - Tier A → verbatim
  - Tier B short → verbatim, long → paraphrased
  - Tier C → paraphrased or omitted (fail-safe)
- Deduplicates by `(name, units)`.

### `extract_failure_modes_from_passages` (NEW)

Symmetric to parameters extraction.

**Output**
```json
[
  {
    "symptom": "...",
    "root_cause": "...",
    "mitigation": "...",
    "source_doi": "10.1234/abc",
    "source_quote": "...",
    "confidence": 0.8
  }
]
```

Dedup by lowercased `symptom`.

### `get_relevant_passages` — `adaptive` kwarg (ENHANCEMENT)

Existing tool gains an optional `adaptive: bool = False`.

**`adaptive=False`** (default): response shape unchanged. Back-compat preserved.

**`adaptive=True`**:
1. Run the requested query.
2. If 0 results, invoke `query_optimizer.rephrase(query, context)` once and retry.
3. Return:
```json
{
  "passages": [...],
  "attempts": [
    {"query": "<original>", "hit_count": 0},
    {"query": "<refined>", "hit_count": 3}
  ],
  "refined_query": "<refined>"
}
```

Callers see at most one retry. No infinite loops, no cascading rewrites.

## Consumer changes

### Scriptorium

**`.claude/commands/find-related.md`** — new slash command.

Resolution order for the text input:
1. Explicit argv text
2. Active selection in the editor
3. The paragraph at cursor position

Calls `perspicacite:search_by_passage(text, kb_names=[<project_kb from kb_manifest.json>], k=5)`.
Renders top-5 with score, title, year, venue, DOI.
Writer runs existing `/cite <n>` to commit a citation.

**`scriptorium/literature/passage_search.py`** — thin Python wrapper around the MCP call used by the slash command and potentially by future audit / drafting hooks.

The post-paragraph auto-suggest hook in `.claude/skills/scriptorium-p5-draft/` is **deferred** to a follow-up after we see how `/find-related` performs in real use.

### AgenticScienceBuilder

**`perspicacite_client.py`** — add three methods that mirror the new MCP tools (`search_by_passage`, `extract_parameters`, `extract_failure_modes`), and update `get_relevant_passages` to accept `adaptive=True` and parse the extended response shape.

**`skill_pack_v3.py`**:
- `enrich_parameters_from_passages()` — drop the `_VALUE_TOKEN_RE` + `_PARAM_NAME_KEYWORDS` regex pipeline; call `client.extract_parameters(passages, context=skill_name, parameter_families=...)`.
- `enrich_failure_modes_from_passages()` — drop `_FAILURE_TRIGGER_RE`; call `client.extract_failure_modes(passages, context=skill_name)`.
- Pass `adaptive=True` to `get_relevant_passages` calls. Log `attempts` to `evolution.jsonl` so we have visibility into how often refinement fires.
- `_LicenseSafeClientShim` stays in place for legacy paths (Zotero, related-papers); the new extraction tools handle license tiering server-side.

## Error handling

- **`search_by_passage`**: input validation up front; empty results are fine; KB-not-found is `404`.
- **Extraction tools**: LLM failure after 1 JSON-salvage retry → return `[]` + warning in response metadata; never crash.
- **`get_relevant_passages` adaptive**: if both attempts return 0, return empty `passages` + both `attempts`; callers can log and move on.
- Across all new tools: cancellation honored via existing cancellation registry; long requests respect existing budget overrides.

## Testing

**Server (Perspicacite-AI)**
- `test_passage_search.py`:
  - Seeded fixture KB, fixed embedding model.
  - Cover: empty text → 400; > 4000 chars → 400; unknown kb_name → 404; min_score filter; k cap; multi-KB scope.
- `test_extraction.py`:
  - Mock LLM with deterministic JSON responses.
  - Cover: license tier A/B/C handling of `source_quote`; dedup; invalid JSON → salvage → empty + warning; empty passages → empty.
- `test_adaptive_relevant_passages.py`:
  - First-pass empty → optimizer triggered → second pass returns N → response includes `attempts` and `refined_query`.
  - First-pass non-empty → optimizer NOT triggered.
  - `adaptive=False` → legacy shape preserved.

**Audit (research-tools-audit)**
- Extend scenario 04 (`chained_search_screen_ingest`) to cover the extraction tools end-to-end on a small KB fixture.
- Assert that ASB outputs (`parameters.json`, `failure_modes.jsonl`) carry `provenance="perspicacite_mcp"` and **no** `regex_mined: true` markers (drop those markers in code).

**ASB**
- Unit tests covering the new client methods (mock the MCP transport).
- Smoke test of `enrich_parameters_from_passages` against a tiny seeded skill.

**Scriptorium**
- Smoke test that `/find-related` returns ranked results against the test fixture KB.

## Migration & back-compat

- `get_relevant_passages` keeps its current default behavior; only opt-in callers get the new response shape.
- ASB's regex extraction code is removed in the same PR as the new MCP calls — there is no period during which both run.
- The legacy ASB `_LicenseSafeClientShim` is preserved for non-extraction paths; no removals there.

## Open questions for review

1. **Quote-paraphrase model.** Server-side reflavoring for Tier C passages currently defaults to Haiku 4.5 (ASB's pick). Keep that, or take a server config value? **Proposed:** server config with Haiku 4.5 default; ASB-side override removed for the extraction path.
2. **`search_by_passage` license enforcement.** Today the tool returns `chunk_text` raw with a `license_id` field and lets the caller decide. Should the server proactively redact / paraphrase Tier C chunks before return? **Proposed:** no — `search_by_passage` returns raw text + license tag; callers (Scriptorium) decide. Extraction tools are different because they explicitly emit `source_quote` as part of structured output.
3. **Embedding model parity.** The new tool must use the same embedding model the KB was built with — verify the server stores model name per KB; if not, this becomes a small prerequisite refactor.
