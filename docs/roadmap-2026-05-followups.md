# Roadmap — framework hardening follow-ups (May 2026)

Master decomposition of the brainstorm conducted after the
agent-CLI rollout (commits `eb0f6c9` through `2441414`). Each wave is
a coherent group of related sub-projects; each sub-project gets its
own brainstorm → spec → plan → subagent execution cycle. Commit
per-task, directly to main, per the repo convention.

**Status as of 2026-05-14:**

- Working tree clean on `main` (233+ commits ahead of origin/main).
- LLM routing has five paths shipped (direct API + caching, Ollama,
  `agent_cli` / `claude_cli`, MCP sampling, per-stage tiering).
- Caveats doc shipped (`docs/agent-cli-caveats.md`).
- Live verified: Claude Code 5–14 s/call, Codex 6–16 s/call.
- Tests exist (141 Python files under `tests/`) but pytest is not
  installed in the active venv — coverage and current health unknown.

## Wave ordering rationale

1. **Wave 1 (Foundation)** unblocks everything else. Before changing
   anything substantive, we need a working test runner + a matrix
   audit to catch regressions.
2. **Wave 2 (Cost/efficiency)** pays back immediately on every dev
   iteration and every user run, especially for the slow agent-CLI
   paths shipped this week.
3. **Wave 3 (Reliability)** turns flaky multi-paper ingests into
   resumable ones — single biggest user-visible quality jump.
4. **Wave 4 (Scientific features)** broadens the framework's
   capability surface. Figure/table parsing is the headline.
5. **Wave 5 (MCP polish)** improves UX inside Claude Desktop /
   Cursor / community clients without changing core.
6. **Wave 6 (E2E validation)** locks in correctness once the
   framework is more capable.
7. **Wave 7 (Docs)** lands after the surface stabilises.

Within a wave, items are roughly ordered by dependency / leverage.

---

## Wave 1 — Foundation

| # | Item | Why first |
|---|------|-----------|
| 1.1 | **pytest install + dev-deps audit** | Without this every other wave flies blind. |
| 1.2 | **Provider × stage matrix audit** (as a pytest test) | One script confirms every routing path × stage actually delivers a completion. Catches 80% of regressions. |
| 1.3 | **MCP tool inventory smoke** (test) | Spawns the server, invokes every tool with minimal valid args, asserts no exceptions. |
| 1.4 | **Config loading audit** (test) | All 6 example YAMLs parse into `LLMConfig`. Stage resolution falls through correctly. |
| 1.5 | **GitHub Actions CI** | ruff + pytest on push. Light, just to surface regressions. |

## Wave 2 — Cost / efficiency

| # | Item | Notes |
|---|------|-------|
| 2.1 | **Disk-cached LLM responses** | Keyed on `(provider, model, system, body, temperature)`. 24-hour TTL default. Huge dev-iteration win for slow agent_cli. |
| 2.2 | **Embedding cache** | Hash chunk text → reuse embedding. Critical when re-building / extending KBs. |
| 2.3 | **Token-usage parsing for `agent_cli`** | Claude Code JSON has `usage.input_tokens` / `usage.output_tokens`. Codex `--json` event stream has per-turn counts. Honest cost accounting. |
| 2.4 | **Budget caps** | `llm.budget_usd_per_run: 10.0` → abort cleanly when crossed. |

## Wave 3 — Reliability

| # | Item | Notes |
|---|------|-------|
| 3.1 | **Rate-limit detection + clean error surface** | Detect Claude Code / Codex / Anthropic rate-limit error patterns. Back off, then surface a clear message instead of N consecutive subprocess failures. |
| 3.2 | **Per-provider fallback chain** | `providers_per_stage.synthesis_heavy: ["anthropic", "claude_cli", "ollama"]` — try in order on failure. |
| 3.3 | **Resume / checkpoint** for multi-paper ingests | `.perspicacite_checkpoint.json` per ingest. Re-run from where it failed. |
| 3.4 | **Error-path audit** (test suite) | Missing API key, wrong model, Ollama down, `claude` binary missing — each yields a helpful message, not a stack trace. |

## Wave 4 — Scientific features

| # | Item | Notes |
|---|------|-------|
| 4.1 | **PDF figure / table parsing** | Use the `agentic_science_builder` approach (multimodal LLM on rendered pages → extract figure captions, table-as-markdown, formula transcription). Attach as chunks with `kind=figure` / `kind=table` metadata so retrieval can boost or filter by modality. |
| 4.2 | **Time-bounded queries** | `papers_published_after / before` filters in search + KB query. Critical for literature reviews. |
| 4.3 | **Versioned KBs** (append log) | Track when each paper was added, by which command. Enables rollback and provenance audits. |
| 4.4 | **Author / ORCID disambiguation** | "Smith J." → canonical author ID via ORCID API / OpenAlex. |
| 4.5 | **Export formats** | BibTeX / CSL JSON / RIS for KB contents. |

## Wave 5 — MCP polish

| # | Item | Notes |
|---|------|-------|
| 5.1 ✅ | **KBs as MCP resources** | Shipped 2026-05-14 — `perspicacite://kbs`, `perspicacite://kb/{name}[/papers,/log]`. See `docs/mcp-resources-prompts-2026-05-14.md`. |
| 5.2 ✅ | **MCP `prompts`** (canned workflows) | Shipped 2026-05-14 — `literature_review`, `compare_papers`, `summarize_kb`, `ingest_dois`, `screen_topic`. |
| 5.3 | **MCP sampling retest** | When [anthropics/claude-code#1785](https://github.com/anthropics/claude-code/issues/1785) lands, the adapter is already in place — just verify and flip `use_mcp_sampling: true` in the relevant presets. |

## Wave 6 — E2E validation

| # | Item | Notes |
|---|------|-------|
| 6.1 ✅ | **E2E pipeline integration tests** | Shipped 2026-05-15 — `tests/e2e/test_{single_paper,multi_paper_citations,cross_kb_routing}.py`. |
| 6.2 ✅ | **Persistence / data integrity tests** | Shipped 2026-05-15 — `tests/integration/test_persistence_integrity.py` (8 tests, concurrent log appends, checkpoint atomic save, llm/embedding cache survival). |
| 6.3 ✅ | **Performance regression baseline** | Shipped 2026-05-15 — `tests/integration/test_perf_baseline.py` against fixed 5-paper corpus. 1.30× tolerance, env-var-driven baseline updates. |

## Wave 7 — Docs

| # | Item | Notes |
|---|------|-------|
| 7.1 | **Recipe book** | Task-oriented "how to do X" pages. Currently README is reference-style. |
| 7.2 | **Architecture diagram** | One-pager dataflow: query → router → KB → retrieval → synthesis. |

---

## Execution rules

- **One sub-project at a time.** Brainstorm → spec → plan → execute
  → commit. Then next sub-project.
- **Spec lives at** `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
- **Plan lives at** `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`
- **Subagent dispatch** uses `superpowers:subagent-driven-development`.
- **Commit cadence**: per-task within a plan, plus one final
  "summary" commit per sub-project linking to the spec.
- **Reorder freely**: if a wave-2 item becomes blocking for a
  wave-4 item, hoist it. The waves are a sequencing guide, not a
  contract.

## Out of scope for this roadmap

- Output-quality evaluation (separate effort — needs gold-standard
  corpora, BLEU/Rouge/expert judgement protocols).
- UI / web frontend (currently CLI + MCP; no plans to add a web UI).
- Multi-user / multi-tenant features (single-user research tool).
- Replacing the storage layer (SQLite / Chroma is fine for current scale).
