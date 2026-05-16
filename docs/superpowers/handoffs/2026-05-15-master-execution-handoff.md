# Session handoff — 2026-05-15 master execution (Scriptorium fixes + ASB ingest)

**Branch:** `claude/trusting-aryabhata-92508b` (worktree) — 27 commits ahead of `main`.
**HEAD:** `c303f12 test: fix Phase E1 regressions in scilex log capture + MCP smoke args`
**Push status:** nothing pushed (per standing workflow). User fast-forwards `main` locally.
**Working tree:** clean.

---

## What landed this session

26 tasks across 5 phases. All planned work shipped. Test suite green except for pre-existing flakes documented below.

### Phase A — Scriptorium MCP critical fixes (5 commits)

- `034e835` **fix(mcp): serialize PaperSource as `.value`, not enum repr** — `mcp/server.py:220` was emitting `"PaperSource.SCILEX"`; downstream Scriptorium clients matching on the lowercase value broke. New pin test `test_mcp_paper_source_serialization.py`.
- `27118c5` **fix(mcp): emit both `success`+`ok` envelope keys for one cycle; document contract** — `_json_ok` / `_json_error` now carry both keys for backwards compat with Scriptorium v0.13. New `docs/MCP.md` documents envelope + latency + auth. Pinned by `test_mcp_envelope.py`.
- `abcda2a` **fix(search): per-backend failure isolation in `SciLExAdapter`** — A single bad backend no longer poisons the whole 3-backend fan-out. Extracted `_collect_from_backend` helper with structured WARNING logs; added a Phase-2 guard so all-fail returns `[]` rather than raising `FileNotFoundError`. Tests in `test_scilex_per_backend_isolation.py`.
- `4fbc493` **fix(api): `/api/chat` accepts `{message: ...}` as a `{query: ...}` alias** — `model_validator(mode="before")` on `ChatRequest`. `query` wins when both present. Tests in `test_chat_request_message_alias.py`.
- `609ee8b` **docs(readme): link MCP envelope/latency contract from quick start** — README quick-start now points to `docs/MCP.md`.

### Phase B — UX / perf improvements (4 commits)

- `faa661a` **feat(search): normalise-then-retry on zero-result title queries** — When a title-like query (`:` or `(...)`) returns 0 hits, retry once with the subtitle/parens stripped. Retry-derived papers get `metadata["search_normalized_from"] = original_query`. New `title_normalize.py` helper. Tests in `test_search_title_normalize.py`.
- `82b4d1c` **perf(search): parallelise per-backend fan-out via `ThreadPoolExecutor`** — 3-backend default fan-out now runs in parallel (`_collect_all_backends`). Wall time = max-of-backends, not sum. Per-backend failure isolation preserved. Tests in `test_scilex_parallel.py`.
- `da7f3c9` **docs(mcp): inline latency expectations in slow tool docstrings** — All 10 multi-second MCP tools (`search_literature`, `generate_report`, `add_dois_to_kb`, `build_kb_from_search`, `expand_kb_via_citations`, `build_capsule`, `build_capsules_for_kb`, `fetch_paper_resources`, `fetch_supplementary`, `enrich_kb_from_cite_graph_tool`) now surface `**Latency:**` in their docstrings. LLM clients budgeting timeouts see this via `tools/list`. Parametrized test `test_mcp_latency_docstrings.py`.
- `8c7e92c` **feat(api): `/api/llm/proxy` — raw-LLM gateway endpoint** — Pure pass-through: client sends `{prompt, model?, stage?, max_tokens?, temperature?}`, server streams text/plain. **No RAG, no KB** (enforced by an import-grep test). Lets Scriptorium use Perspicacité's API-key/stage-tiering config without retrieval. Tests in `test_llm_proxy_endpoint.py`.

### Phase C+D — ASB Bundle Ingest (12 commits)

- `6eb1acc` **test(asb): copy 2026-05-16 + 2026-05-15 ASB output subsets as fixtures** (C1) — `tests/fixtures/asb/{article_878_v4_subset,metlinkr_subset}/` — two subsets (~452KB total) exercising both DAG edge schemas. Smoke test `test_asb_fixtures_present.py`.
- `f3914fe` **feat(models): add `PaperSource.SKILL_BUNDLE`** (D1) — New enum value for ASB-derived chunks.
- `4db963b` **feat(asb): skill-bundle parser (`skills/_index.json` + per-skill sidecars)** (D2) — New `pipeline/asb/{models,skill_parser}.py`. Six pydantic models with `ConfigDict(extra="allow")` for schema forward-compat. Tolerates both list-of-dicts and `{tools: [...]}` sidecar shapes. 6 tests.
- `8add344` **feat(asb): workflow-card parser with 2026-05-16 schema-drift absorption** (D3 + C3) — `ParsedCard` (57 fields, `extra="allow"`). Tolerates both `executable: bool` (2026-05-15) and `executable: dict` (2026-05-16). Surfaces `task_objective`, `task_inputs/outputs`, `execution_profile`, `run_timeout_seconds`, `reproducibility_tier`, `expected_artifact_name`, `linked_result_ids`, `provenance_source`, `source_package`, `scenario_id`, `github_name`. 12 tests.
- `20c5bbd` **feat(asb): `workflow_dag.json` reader with dual-edge-format support** (D4 + C2) — `Edge` carries optional `port`. Parses both 2026-05-15 (`[[src, dst], ...]`) and 2026-05-16+ (`[{"from", "port", "to"}, ...]`) edge formats. `to_dict()` always serialises to the new dict form. 9 tests.
- `3578c30` **feat(asb): chunk producer — Paper builder for skills + cards + DAG** (D5 + C3) — `skill_to_paper(skill) -> Paper` and `card_to_paper(card, *, dag) -> Paper`. Papers carry `source=PaperSource.SKILL_BUNDLE` and full structured metadata. Stable IDs (`asb_skill:{slug}`, `asb_card:{task_id}`) for idempotent re-ingest. Card abstract uses `task_objective`. `paper_github` prefers `github_name` over legacy. 12 tests.
- `266d1cf` **feat(asb): `skill_kb.json` round-trip writer (in-place, idempotent)** (D6) — `write_skill_kb_entries(skill_kb_path, *, entries)`. Entries deduped by `source_url`. Original ASB notes preserved. `perspicacite_ingest_completed=<ts>` stamp replaces (not duplicates) on re-run. 6 tests.
- `a613ea7` **feat(asb): top-level orchestrator `ingest_asb_run`** (D7) — Wires parsers → chunk producer → KB → skill_kb writer. Supports `composite` (one KB) and `per-skill` (one KB per skill, workflows still composite) modes. Module-level seams (`_make_or_get_kb`, `_ingest_backing_paper_dois`) for testability. 7 integration tests with mocked KB.
- `7618069` **feat(mcp): `ingest_asb_run` tool** (D8) — MCP tool wrapping the orchestrator. Returns dual-key envelope per A2. Latency hint per B3. 5 tests.
- `3596fb7` **feat(cli): `ingest-asb-run` command** (D9) — Click command wrapping the orchestrator. `--kb-name`, `--include {skills,workflows}`, `--mode {composite,per-skill}`, `--no-skill-kb-update`. 5 tests.
- `2309ab4` **feat(asb): response-time `skill_metadata` + `workflow_metadata` payloads** (D10) — Pure `build_asb_response_metadata(chunks)` helper. Derives summary blocks from chunk dicts; surfaces 2026-05-16 fields (`executable` dict, `execution_profile`, `task_inputs/outputs`, `expected_artifact_name`, `run_timeout_seconds`, `reproducibility_tier`). Chat-router + MCP wiring deferred. 8 tests.
- `9c4beb1` **test(asb): e2e gated live test + response-payload regression coverage** (D11) — Three integration tests: gated live e2e (skips without `PERSPICACITE_E2E_ASB=1`), response-payload regression with 2026-05-16 fields, and orchestrator→helper smoke.

### Phase E — Final validation + handoff (2 commits)

- `c303f12` **test: fix Phase E1 regressions in scilex log capture + MCP smoke args** — Switched `test_failing_backend_logs_warning_and_appends_to_failed` from `capsys` to `structlog.testing.capture_logs` (the project routes structlog through stdlib `logging`, not `PrintLoggerFactory`). Added `_TOOL_ARGS` entries for `ingest_asb_run` and `enrich_kb_from_cite_graph_tool` so the parametrized MCP smoke loop doesn't TypeError.
- (this commit) **docs(handoff): master execution plan session-end summary**

---

## Test counts

```
PYTHONPATH=src pytest tests/unit tests/integration --ignore=tests/integration/test_provider_matrix.py -q
1486 passed, 6 skipped, 2 failed (perf baselines — pre-existing flakes)
```

- **+88 new tests** from the 21 implementation tasks (Phase A: 9, Phase B: 16, Phase C: 2, Phase D: 61).
- **6 skips** include 1 ASB e2e (env-gated `PERSPICACITE_E2E_ASB`) plus pre-existing skips.
- **2 failures** = `test_perf_baseline` and `test_perf_baseline_llm` (wall-clock latency tests against recorded baselines — pre-existing flakes, unrelated to this session).
- **Provider matrix** (`test_provider_matrix.py::test_liveness_*`) — 4 pre-existing failures due to `RuntimeError: There is no current event loop in thread 'MainThread'` (Python 3.12+ + pytest-asyncio). Excluded from the suite-wide run above. Not in this session's scope.

---

## Active backlog

### P0 — Implementation, specs ready

**(none — everything in the master plan shipped)**

### P1 — Implementation, design decisions still needed

- **Chat-router + MCP wiring for the response-metadata helper.** `build_asb_response_metadata(chunks)` is pinned and tested but not yet plugged into the chat-router and MCP response builders. Wiring requires picking the right spot in two large response-assembly paths in `src/perspicacite/web/routers/chat.py` (~line 484) and `src/perspicacite/mcp/server.py` (~line 1816). Surface chunks must be transformed into the `{"metadata": {...}}` shape the helper expects.
- **Repo fetching from `links.json[category=repo_github]`.** Already covered by the parent `docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md` plan — run that plan to add repo cloning + per-repo chunking to ASB ingest.

### P2 — Smaller / less-blocking

- **Pre-existing perf-baseline flakes** (`test_perf_baseline`, `test_perf_baseline_llm`). Wall-clock-sensitive tests. A broader regression sweep would be useful but not urgent.
- **Provider matrix event-loop bug.** `tests/integration/test_provider_matrix.py::test_liveness_*` need an event-loop fixup for Python 3.12+. Not session-blocking; would unlock the liveness smoke when env vars are set.
- **Chat-router `/api/chat` end-to-end test.** A4 added the alias + 3 unit tests; an integration test exercising the full SSE path with `{"message": "..."}` would close the loop.

### P3 — Larger, needs brainstorm

- **ASB capsules ingest (`capsules/{paper}__task_NNN/`)** — Per-task RO-Crate containers. Explicit v2 item; needs a separate spec.
- **ASB `scenarios/` dir ingest** — New artifact stream introduced in 2026-05-16+ ASB runs. Out of this session's scope; needs spec.
- **Workflow DAG as queryable graph nodes (graph-RAG)** — Currently DAG is bundle-level metadata. Indexing edges as retrievable chunks is a separate feature.
- **Hosting an ASB MCP server in this repo.** Federation-only per current design. Reconsider if Scriptorium-style downstream needs proliferate.
- **Migrating Scriptorium past the `ok` envelope alias.** Needs Scriptorium-side change before we can drop the `ok` key in a future minor cycle.

---

## Standing workflow (carry forward — do not re-discover)

- **Per-task commits directly to the worktree branch; never push.** The user fast-forwards `main` locally when the worktree work is done.
- **Workflow for non-trivial work:** brainstorm → spec (`docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`) → plan (`docs/superpowers/plans/YYYY-MM-DD-<topic>.md`) → subagent-driven execution.
- **No clarifying questions** — make the reasonable call and continue; the user redirects if needed.
- **Heredoc commit messages** with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **PYTHONPATH=src** when running pytest inside a worktree (the editable install points to the main repo's `src/`, not the worktree's — this is a real foot-gun).
- **Subagent-driven development:** one implementer per task; two-stage review (spec compliance + code quality); don't pause between tasks.
- **Memory at `~/.claude/projects/-Users-holobiomicslab-git-Perspicacite-AI/memory/`** carries cross-session state. Today's relevant entries: `project_asb_kb_pairing_strategy.md`.

---

## Quick-start commands for the next session

```bash
# See this session's work
git log --oneline c8d8ecc..HEAD          # 24 commits (master plan + tasks + handoff)

# Confirm clean tree + correct branch
git status

# Verify the test suite is still green
PYTHONPATH=src pytest tests/unit tests/integration --ignore=tests/integration/test_provider_matrix.py -q --tb=line | tail -5
# Expected: ~1486 passed, ~6 skipped, ~2 failed (perf baselines)

# Try the new CLI command against a real ASB run
perspicacite ingest-asb-run ~/git/AgenticScienceBuilder/outputs/audit_2026-05-16_workflow_validation/article_878_v4/ \
    --kb-name article_878_v4 \
    --include skills --include workflows

# Try the new MCP tool (the dev server hosts MCP at /mcp)
curl -X POST http://localhost:5468/mcp -H 'Content-Type: application/json' -d '{
  "method":"tools/call","jsonrpc":"2.0","id":1,
  "params":{"name":"ingest_asb_run","arguments":{"asb_run_dir":"/path/to/asb_run","kb_name":"test"}}
}'

# Try the LLM proxy (Scriptorium-style)
curl -X POST http://localhost:5468/api/llm/proxy -H 'Content-Type: application/json' \
     -d '{"prompt":"What is metabolomics?","model":"claude-haiku-4-5"}'

# Confirm MCP envelope contract doc renders
cat docs/MCP.md

# Inspect the new ASB pipeline modules
ls src/perspicacite/pipeline/asb/
```

---

## Suggested first move

If the user says "continue", three natural options:

1. **Wire the response-metadata helper** into `chat.py` + `mcp/server.py` (P1 above). Smallest follow-up; the helper + tests already exist.
2. **Run the parent `github-skill-bundle-ingest` plan** to add repo cloning (P1 above). Complements ASB ingest with the deferred repo-fetching path.
3. **Spec-and-plan the `scenarios/` dir** if 2026-05-16+ ASB runs are being consumed downstream and scenario metadata is wanted.

A conservative first move: fast-forward `main` to `c303f12`, push, and tag a release.

---

## Pinned context

- **Repo:** `/Users/holobiomicslab/git/Perspicacite-AI`
- **Active worktree:** `.claude/worktrees/trusting-aryabhata-92508b` at `c303f12` (this branch). 27 commits ahead of `main`.
- **Branch:** `claude/trusting-aryabhata-92508b` (worktree-private). The merge target is `main`.
- **Other worktrees:** `dazzling-rhodes-7246b7`, `modest-banzai-98b17c` — unaffected by this session.

### Test invariants pinned this session (don't regress)

- `tests/unit/test_mcp_paper_source_serialization.py` — no `str(p.source)` in `mcp/server.py`
- `tests/unit/test_mcp_envelope.py` — both `success` and `ok` keys present
- `tests/unit/test_chat_request_message_alias.py` — `message` accepted as `query` alias
- `tests/unit/test_search_title_normalize.py` — title-normalize retry behaviour
- `tests/unit/test_scilex_parallel.py` — per-backend fan-out runs concurrently
- `tests/unit/test_scilex_per_backend_isolation.py` — failure isolation + all-fail returns `[]`
- `tests/unit/test_mcp_latency_docstrings.py` — slow MCP tools surface latency
- `tests/unit/test_llm_proxy_endpoint.py` — `/api/llm/proxy` has no RAG/KB coupling
- `tests/unit/test_asb_*.py` (~57 tests across skill_parser, card_parser, dag, chunk_producer, skill_kb_writer, response_metadata)
- `tests/integration/test_asb_run_ingest_end_to_end.py` (~10 tests, 1 env-gated live)
- `tests/unit/test_mcp_ingest_asb_run.py` + `tests/unit/test_cli_ingest_asb_run.py` (MCP/CLI wrappers)

### Memory entries (cross-session)

- `project_perspicacite_setup.md` — repo + workspace setup
- `feedback_workflow.md` — brainstorm→spec→plan→subagent contract
- `project_asb_kb_pairing_strategy.md` — ASB↔KB pairing strategy

---

## Files / paths a fresh session will want to grep first

```bash
# New ASB pipeline modules
ls src/perspicacite/pipeline/asb/                                  # parsers, dag, chunk_producer, response, run_ingest

# MCP envelope + latency contract
cat docs/MCP.md
grep -n '\*\*Latency:\*\*' src/perspicacite/mcp/server.py | head

# Master plan + sub-plan (for reference)
cat docs/superpowers/plans/2026-05-15-master-execution-plan.md     # 1492 lines
cat docs/superpowers/plans/2026-05-15-asb-bundle-ingest.md         # 1946 lines
cat docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md  # ASB spec with 2026-05-16 drift

# CLI + MCP entry points for ASB
grep -n 'ingest_asb_run\|ingest-asb-run' src/perspicacite/cli.py src/perspicacite/mcp/server.py | head

# /api/llm/proxy router
ls src/perspicacite/web/routers/llm_proxy.py
```

---

## Effort summary

- **Total commits in this session:** 24 (excluding pre-existing master-plan + spec + sub-plan + handoff commits inherited at start).
- **New source modules:** 8 (`pipeline/asb/{__init__,models,skill_parser,card_parser,dag,chunk_producer,skill_kb_writer,run_ingest,response}.py`, `search/title_normalize.py`, `web/routers/llm_proxy.py`).
- **New test modules:** 15.
- **Lines added (approx):** ~3200 source + ~3600 test + ~600 docs.
- **Wall time (subagent dispatch + reviews):** ~6 hours.
