# Perspicacité MCP Usage Guidance & Skill — Design

> **Status:** Spec. Working document — DO NOT commit to git (per standing convention).
> **Date:** 2026-05-20
> **Author:** autonomous sprint (continuation of the Perspicacité MCP parity work)

## Motivation

Perspicacité's web front-end embodies a *decision procedure* for using the
research engine well — when to rephrase a query, when to translate, which
databases to search, which mode and screening to apply, which tool to reach for,
and how to sequence multi-step work. An LLM calling Perspicacité over MCP gets the
shared core's knobs (query optimization, screening, recency, multi-KB) but has no
artifact that teaches the *judgment* the front-end encodes. The result: MCP callers
under-use the tools or use them naively.

This sprint closes that gap with a layered design: server-owned, always-on guidance
(enriched tool descriptions + a live usage-guide tool); two server helpers that fill
decisions the calling LLM can't make alone (database suggestion, agentic
orchestration); and a generic skill plus thin consumer playbacks that teach the
procedure — with client-side behaviors (translation, tool/mode choice) handled by
the calling LLM rather than new server code.

## Goals / Non-goals

**Goals**
- Make the MCP surface self-describing enough that a competent LLM uses it well.
- Add `suggest_databases` and expose the agentic orchestrator via MCP.
- Provide a live `get_usage_guide` source of truth that cannot drift from the tools.
- Ship a generic `perspicacite-mcp` skill + ASB and Scriptorium playbacks.

**Non-goals**
- No server-side translation module (the calling LLM is multilingual; the skill
  instructs it to translate non-English queries before searching).
- No single "smart_research" meta-tool (rejected: hides decisions, duplicates the
  in-loop LLM, less flexible).
- No automatic database routing inside `search_literature` (kept as an explicit
  `databases` arg; `suggest_databases` advises, the caller decides).

## Architecture

Three phases, built in order (the skill documents what the server phase adds):

```
Phase 1 — Server enablement (Perspicacite-AI)
  1a. Enrich tool docstrings (the descriptions the LLM sees over MCP)
  1b. suggest_databases tool   (query -> recommended DB list + reasoning)
  1c. generate_report mode="agentic" (+ literature_survey if not wired)
  1d. get_usage_guide info tool (live capability/decision/tool-index payload)
Phase 2 — Generic skill: perspicacite-mcp (decision procedure + client behaviors)
Phase 3 — Consumer playbacks: ASB (KB/skill-pack building), Scriptorium (drafting search)
```

### Phase 1 — Server enablement

**1a. Docstring enrichment.** The thin docstrings (`extract_parameters_from_passages`,
`extract_failure_modes_from_passages`, `search_by_passage`, `get_relevant_passages`)
get a consistent structure: one-line purpose, "when to use this vs. the nearest
alternative", key knobs with defaults, and the response shape. The already-rich
tools (`search_literature`, `generate_report`) get a short "defaults / when to use"
header if missing. These strings are the MCP tool descriptions, so this is always-on
guidance with zero runtime cost. No behavior change.

**1b. `suggest_databases`.** New `@mcp.tool()` (registered in `_TOOL_NAMES`).
Signature: `suggest_databases(query: str, hints: list[str] | None = None)`.
Returns the standard `_json_ok` envelope:
`{success: true, recommended: [...], reasoning: str, all_known: [...]}`.
Implementation is a **deterministic** keyword/topic rule map over the canonical
`KNOWN_DATABASES` set (e.g. biomedical/clinical→pubmed, europepmc; physics/CS/math→
arxiv; chemistry→pubchem; high-energy physics→inspire; general/cross-domain→
semantic_scholar, openalex, crossref). Unknown/ambiguous topic → a broad safe
default (semantic_scholar, openalex, crossref). `all_known` echoes `KNOWN_DATABASES`
so the caller can override. Never raises for normal input → always `success: true`.
Deterministic-first so it is unit-testable without an LLM.

**1c. `generate_report` agentic mode — VERIFY + DOCUMENT (no new wiring expected).**
Exploration confirmed `generate_report` already accepts `mode="agentic"` and
`mode="literature_survey"` (server.py:1408–1416 maps them to `RAGMode.AGENTIC` /
`RAGMode.LITERATURE_SURVEY`, dispatched through `RAGEngine`; `rag/modes/agentic.py`
delegates to `AgenticOrchestrator`). So the orchestrator is already reachable over
MCP. Scope here is therefore: (1) an end-to-end test proving `mode="agentic"` routes
to the orchestrator and returns the standard envelope; (2) documenting these modes in
the enriched `generate_report` docstring and the usage guide. Only if the test
surfaces a real break do we touch wiring. The previously-considered dedicated
`research_agentic` tool is dropped — redundant given the existing mode.

**1d. `get_usage_guide`.** New `@mcp.tool()` (registered in `_TOOL_NAMES`) returning a
structured, version-matched payload via `_json_ok`:
`{success: true, capabilities: [...], decision_rules: [...], tools: [{name, purpose,
when_to_use, key_knobs}], knob_defaults: {...}}`. Implemented as a **tool** rather
than only a resource because MCP clients invoke tools far more reliably than they
read resources; the existing `perspicacite://info` resource (server.py:4752, returns
`_TOOL_NAMES`) stays as-is for resource-aware clients. The guide's `tools` content is
sourced from a single module-level data structure so it cannot silently lag the
docstrings. A **drift test** asserts every entry in `_TOOL_NAMES` is represented in
the guide's `tools` list (fails the suite if a tool is added without updating the
guide).

### Phase 2 — Generic skill `perspicacite-mcp`

A Claude Code skill at `Perspicacite-AI/.claude/skills/perspicacite-mcp/SKILL.md`
(introducing the `.claude/skills/` dir, following Scriptorium's proven
`SKILL.md`-in-a-directory format with `name` + `description` frontmatter). A one-line
pointer is added to `Perspicacite-AI/CLAUDE.md`. The skill teaches the decision
procedure. Sections:
1. **Translate** — non-English query → translate to English before searching; keep
   the original for display/citation.
2. **Query shaping** — pass user text largely verbatim; set the server
   `optimize_query` knob (default on for literature search) rather than
   pre-rephrasing; use raw text for passage search.
3. **Database selection** — call `suggest_databases`, then set `databases=`.
4. **Tool choice** — decision table: synthesized answer→`generate_report`; raw paper
   list→`search_literature`; sentence/paragraph similarity→`search_by_passage`; KB
   keyword passages→`get_relevant_passages(adaptive=True)`; structured facts→
   `extract_*`; ambiguous/multi-step→`generate_report(mode="agentic")`.
5. **Mode & screening** — basic/advanced/profound/contradiction/agentic; set
   `screen_method`/`screen_threshold` when precision matters.
6. **Reading results** — the `{success, ...}` envelope, `META:` tails, `usage`/
   `attempts`/`query_rephrasings` fields.
7. **Live reference** — call `get_usage_guide` for the authoritative tool index and
   current defaults rather than trusting this file's snapshot.

### Phase 3 — Consumer playbacks (thin, reference the generic skill)

- **ASB playbook** at `AgenticScienceBuilder/.claude/commands/perspicacite-kb.md`
  (ASB uses `.claude/commands/*.md` with a `description` frontmatter; no skills dir).
  Building skill-pack knowledge bases via MCP: `build_kb_from_search` →
  `extract_parameters_from_passages` / `extract_failure_modes_from_passages`, with
  provenance (`perspicacite_mcp`). Points to the generic skill for the
  search/selection procedure.
- **Scriptorium playbook** at
  `Scriptorium/.claude/skills/perspicacite-search/SKILL.md` (Scriptorium uses
  `.claude/skills/{name}/SKILL.md` with `name` + `description`). Literature search
  while drafting: `search_by_passage` for citation suggestions, alongside the
  existing `/find-related` command. Points to the generic skill.

## Data flow (caller's path)

user query → [skill] detect language → translate if needed → `suggest_databases` →
choose tool+mode (skill decision table) → call tool with knobs (`optimize_query`,
`databases`, `screen_*`) → read `{success,...}` envelope + `META`/`usage` →
escalate to `mode="agentic"` if multi-step. `get_usage_guide` consulted whenever the
caller needs the authoritative current capabilities.

## Error handling

- All tools keep the `{success: false, error: ...}` envelope; the skill teaches:
  read error → broaden/retry → fall back to a simpler tool.
- `suggest_databases` never hard-fails on normal input (returns a broad default).
- `get_usage_guide` is static data → no failure path.
- Agentic mode surfaces orchestrator failures through the existing generate_report
  error envelope.

## Testing

- **suggest_databases:** rule mapping per domain (biomed→pubmed, CS→arxiv, chem→
  pubchem, general→broad), unknown→broad default, envelope shape, `all_known`
  equals `KNOWN_DATABASES`.
- **generate_report agentic mode:** routing reaches the orchestrator entry point
  (mocked) and returns a success envelope; existing modes unaffected.
- **get_usage_guide:** returns the expected top-level keys; **drift test** — every
  `_TOOL_NAMES` entry appears in `tools`.
- **Docstrings:** light check that the four enriched tools have non-trivial
  descriptions (length / required substrings).
- **Skills/playbacks:** markdown — validate frontmatter only (name + description
  present); no runtime tests.
- All existing suites stay green (Perspicacite-AI pytest, ASB unittest).

## Risks

- Agentic-mode wiring may be more involved than a mode switch (orchestrator may
  stream differently than RAGEngine). Mitigation: confirm entry-point signature in
  the plan; fall back to a dedicated tool if needed.
- Skill-file convention differs per repo. Mitigation: plan confirms exact
  directory/frontmatter for each of the three repos before authoring.
- `get_usage_guide` drift. Mitigation: the drift test makes omissions fail loudly.
