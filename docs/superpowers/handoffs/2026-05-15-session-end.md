# Session handoff — 2026-05-15 (end of day)

**Branch:** `main` and worktree `claude/trusting-aryabhata-92508b` are at the same commit.
**HEAD:** `5f350a6 docs(readme): trim + link out to new docs tree`
**Push status:** nothing pushed (per standing workflow).
**Working tree:** clean.

---

## What landed this session

29 commits on `main` between `4567a61` (session start) and `5f350a6` (session end).

### Code work — done end-to-end

**P2: PaperSource WEB_SEARCH adapter migration** (9 commits, `4f35b2a..5af3840`)

Replaced every remaining `PaperSource.WEB_SEARCH` default in `src/` with the domain-correct enum value. Added one new enum value (`SEMANTIC_SCHOLAR`); migrated `semantic_scholar.py`, `rag/chunking.py`, `mcp/server.py` (2 sites), `web/routers/kb.py` (3 sites), `pipeline/search_to_kb.py`, `rag/agentic/orchestrator.py` (2 sites). Pin tests in `tests/unit/test_paper_source_adapter_migration.py` and file-wide invariant in `test_paper_source_no_websearch_defaults.py`. Live `mistral/codestral-embed` smoke test confirmed end-to-end (1536-dim, `MISTRAL_API_KEY` gated).

**P2: 9 pre-existing unit-test failures** (5 commits, `eeb764b..c845b2b`)

All test-mock drift, no production bugs. Fixed: `test_arxiv_id_fallback` (stale assertion), `test_local_docs_capsule_reader_route` (missing `external_metadata` kwarg in mock signature), `test_mcp_multi_kb_passthrough` (MagicMock + `filters` kwarg), `test_provenance_engine_wiring` (MagicMock vs pydantic), `test_zotero_ingest_worker` (`capsule.auto_build_on_ingest` attr missing on mock).

**P3: SS fallback cite-graph for arXiv** (6 commits including spec/plan, `ce19bfb..9fffa4c`)

OpenAlex underreports arXiv preprint forward citations (RAG paper: 18 hits vs ~7000 in SS). Added `fetch_ss_references` / `fetch_ss_citations` in `src/perspicacite/search/semantic_scholar.py`; added `ExpansionHit.provenance` field + `_seed_needs_ss_fallback` / `_ss_id_for_seed` / `_merge_ss_into_hits` in `src/perspicacite/pipeline/snowball.py`; new `include_semantic_scholar: bool = True` kwarg on `snowball_expand` (opt-out for tests/batches). Live smoke test confirms the audit finding: RAG paper goes from 18 OA hits → 43 combined (SS adds 25, **2.4× lift**).

### Documentation + repo prep — done end-to-end

**`.github/` scaffolding** (commit `013486b`) — `CODEOWNERS`, `SECURITY.md`, `PULL_REQUEST_TEMPLATE.md`, `ISSUE_TEMPLATE/{bug_report,feature_request,config}`.

**`docs/VISION.md`** (commit `0a3b9b2`) — 4-page framework vision + core capabilities doc covering problem, design philosophy, architecture (ingestion → storage → retrieval → reasoning → surface layers), capability catalog, anti-features, roadmap pointers.

**GitHub-rendered `docs/` tree** (commit `12e9fa9`) — 23 new docs files (~4,100 lines):
- `docs/index.md`, `docs/getting-started.md`
- `docs/concepts/{knowledge-bases,rag-modes,capsules,provenance,citation-graph}.md`
- `docs/guides/{ingest-bibtex,search-to-kb,expand-via-citations,zotero-integration,obsidian-export,institutional-pdf-access}.md`
- `docs/reference/{cli,rest-api,mcp-tools,config,paper-source-enum}.md`
- `docs/development/{contributing,architecture,testing,superpowers-workflow}.md`

All CLI / REST / MCP surfaces verified against source before being documented (19 CLI subcommands, 36 REST routes, 23 MCP tools — counts match the actual code).

**README refresh** (commit `5f350a6`) — 936 → 253 lines (−73%); deep-content sections replaced with one-line summaries + links to the new `docs/` tree. The agent also corrected the default-port reference: code says `port: int = Field(default=5468, ...)`, not `:8000` as the old README suggested.

### Design decisions — written down, not yet implemented

**ASB ↔ Perspicacité bundle ingest** (commit `f348692`, spec `docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md`)

Addendum to the generic skill-bundle ingest spec, specifying the actual ASB run-output schema (per-skill directories with `tools.json` / `environments.json` / `parameters.json` / `papers.json` / `links.json` / `skill_kb.json`) and the **`skill_kb.json` round-trip contract**: ASB pre-flights what repos need fetching with `entries: []` and a `notes` summary; Perspicacité fetches, embeds via the existing `TypedEmbeddingProvider` (code → `mistral/codestral-embed`, text → default), and writes `entries[]` back. Per-chunk metadata schema captures tool/env/parameter requirements so the auto-KB-routing payload surfaces them. New `PaperSource.SKILL_BUNDLE` enum value reserved (not yet added).

Real ASB output reference / test-fixture source: `~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/`.

---

## Active backlog

### P0 — Implementation work, specs ready

**(none currently — every spec with a plan has either shipped or is awaiting a paired implementation plan)**

### P1 — Implementation work, design decisions still needed

- **GitHub-skill-bundle + ASB-bundle ingest implementation.** Two specs accepted:
  - `docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md` (generic ingest scaffolding)
  - `docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md` (the ASB-specific entry point — addendum)
  - Existing plan `docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md` predates the ASB addendum and only covers the generic path. Before executing, write a complement plan (or extend the existing one) for the ASB entry point: parser, `skill_kb.json` round-trip, per-chunk metadata, MCP tool `ingest_asb_skills`.

### P2 — Smaller / less-blocking

- **Pre-existing failures hygiene.** Some tests (`test_arxiv_id_fallback`, `test_local_docs_capsule_reader_route`, etc.) were updated this session to match production. A broader sweep to confirm no other mock signatures have drifted would be valuable but is not urgent.
- **Audit results housekeeping.** `tests/audit/results/*` (32 files) are tracked; check whether older runs (pre-2026-05-15) are still useful or should be moved to a `tests/audit/results/archive/` subdir to keep the directory legible.
- **`docs/development/superpowers-workflow.md`** is 83 lines (~350 words). Reads fine as-is but the doc agent flagged it as below the 200-word floor it was given. No action required.

### P3 — Larger, needs brainstorm

- **Per-paper figure-aware multimodal retrieval.** The figure-extraction capsule already produces `figures/index.json` + PNGs. A retrieval surface that lets a query like "show me the figure where X" return the figure metadata + image is a meaningful next feature. The capsule infrastructure exists; the retrieval glue does not.

---

## Standing workflow (carry forward — do not re-discover)

- **Per-task commits directly to `main`; never push.** The user merges via local fast-forward when work is done in a worktree.
- **Workflow for non-trivial work:** brainstorm → spec (`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`) → plan (`docs/superpowers/plans/YYYY-MM-DD-<topic>.md`) → subagent-driven execution. Save plans / specs before touching code.
- **No clarifying questions** — make the reasonable call and continue; the user redirects if needed.
- **Heredoc commit messages** with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **PYTHONPATH=src** when running pytest inside a worktree (the editable install points to the main repo's `src/`, not the worktree's — this is a real foot-gun that ate a chunk of debugging time earlier this session).
- **Memory at `~/.claude/projects/-Users-holobiomicslab-git-Perspicacite-AI/memory/`** carries cross-session state. Today's additions: `project_asb_kb_pairing_strategy.md`. Update or add entries when learning something durable.

---

## Quick-start commands for the next session

```bash
# See this session's work
git log --oneline 4567a61..main

# Confirm clean tree + correct branch
git status

# Read the two pending design docs (the most likely next action)
cat docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md
cat docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md
cat docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md

# Verify the test suite is still green
PYTHONPATH=src pytest tests/unit -q --tb=line | tail -5
# Expected: 1316+ passed, 1 skipped, 0 failed

# Sample real ASB output (for fixture / understanding)
ls ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/cross-identifier-reconciliation/
cat ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/cross-identifier-reconciliation/skill_kb.json
```

---

## Suggested first move

If the user says "continue", the natural next step is **drafting the implementation plan for the ASB-aware skill-bundle ingest path** — the design is settled, the real ASB output is on disk as a fixture source, and the existing plan needs the ASB-specific extensions wired in.

Process: read both specs, read the existing skill-bundle plan, draft a `docs/superpowers/plans/2026-05-15-asb-bundle-ingest.md` (or extend the existing plan), then execute via subagent-driven development per the standing workflow.

A more conservative first move: read the new `docs/` tree, the VISION doc, and the ASB spec to absorb the framing, then ask the user which of the P1/P2/P3 items they want to pick up.

---

## Pinned context

- **Repo:** `/Users/holobiomicslab/git/Perspicacite-AI`
- **Active worktree:** `.claude/worktrees/trusting-aryabhata-92508b` (synced with main at `5f350a6`). Safe to delete with `git worktree remove` when convenient — work is fully merged.
- **Other worktrees:** `dazzling-rhodes-7246b7` (claude/capsule-cycle-a), `modest-banzai-98b17c` (separate concern). Not affected by this session.
- **Test invariants pinned this session (don't regress):**
  - `tests/unit/test_paper_source_no_websearch_defaults.py` — no `source=PaperSource.WEB_SEARCH` in `src/`
  - `tests/unit/test_paper_source_adapter_migration.py` — 10 pin tests, one per migrated call-site
  - `tests/integration/test_codestral_embed_live.py` — Mistral codestral live smoke (`MISTRAL_API_KEY` gated)
  - `tests/integration/test_snowball_ss_fallback_live.py` — SS-fallback cite-graph live smoke (`SEMANTIC_SCHOLAR_API_KEY` gated)
- **Memory entries:** `project_perspicacite_setup.md`, `feedback_workflow.md`, `project_asb_kb_pairing_strategy.md`

---

## Files / paths a fresh session will want to grep first

```bash
grep -rn 'PaperSource\.' src/ | grep -v WEB_SEARCH | head -20      # current enum landscape
grep -n 'def \|class ' src/perspicacite/pipeline/snowball.py       # snowball public surface
grep -rln 'fetch_ss_references\|fetch_ss_citations' src/           # SS fallback wiring
ls docs/superpowers/{plans,specs}/                                 # active design work
ls .github/                                                        # new templates
```
