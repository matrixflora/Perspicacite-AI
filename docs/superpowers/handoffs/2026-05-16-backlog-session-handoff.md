# Session handoff — 2026-05-16 backlog drain (ASB metadata wiring + GitHub/skill-bundle ingest + arXiv/PMC resolver + embedding-conflict detection)

**Branch:** `claude/trusting-aryabhata-92508b` (worktree) — 52 commits ahead of `main`.
**HEAD:** `3d808d0 test(mcp): extend SLOW_TOOLS registry with ingest_asb_run + github tools`
**Push status:** nothing pushed (per standing workflow). User fast-forwards `main` locally.
**Working tree:** clean.
**Companion handoff:** `docs/superpowers/handoffs/2026-05-15-master-execution-handoff.md` (the previous session's 26-task master plan).

---

## What landed this session

Two backlog P1 items from the previous handoff drained, in order:

### Sub-project 1 — ASB metadata wiring (7 commits)

The previous session shipped `build_asb_response_metadata(chunks)` as a pure helper with full unit-test coverage but left it un-wired to the chat router and MCP server. The wiring turned out to be a 3-layer change because `Paper.metadata` was being dropped at ingestion (`ChunkMetadata` is a frozen pydantic model with a closed field set). Plan: `docs/superpowers/plans/2026-05-16-asb-metadata-wiring.md`.

- `d5faed1` **docs(plan): asb metadata wiring (round-trip + plumb + wire)** — 3-task plan.
- `2e414e2` **feat(kb): round-trip Paper.metadata through ingestion → chroma → retrieval** (Task 1) — adds `paper_metadata_json: str | None` to `ChunkMetadata`, JSON-encodes on ingest, decodes on `search_two_pass`, exposes `paper_metadata: dict | None` on paper-result dicts.
- `33cb1ff` **refactor(kb): hoist json import, extract _decode_paper_md, cover peek path** — Task 1 code-quality follow-up: extracted helper, narrow exception, added `peek_paper_metadata_row`-path test (4th test).
- `5592039` **feat(rag): plumb paper metadata onto SourceReference in all 4 RAG modes** (Task 2) — adds `metadata: dict | None` to `SourceReference`; basic/advanced/profound/contradiction each pass `paper_metadata` onto the emitted source.
- `5393cd8` **refactor(rag): consolidate paper_metadata_json decoder into shared module** — Task 2 follow-up: three near-identical decoders folded into `src/perspicacite/rag/paper_metadata_codec.py::decode_paper_metadata_json`.
- `b37638c` **feat(asb): wire build_asb_response_metadata into chat SSE + MCP** (Task 3) — chat router emits a new `'type': 'asb_metadata'` SSE event between answer and done (only when non-empty); MCP `generate_report` adds `asb_metadata` to envelope and propagates `metadata` onto each source; MCP `search_knowledge_base` decodes `paper_metadata_json` into per-chunk `metadata` and emits `asb_metadata` for both single-KB and multi-KB paths.
- `e666bbc` **fix(asb): harden build_asb_response_metadata against non-dict input** — defensive guard so SimpleNamespace / string / list metadata silently skips rather than raising; covers non-dict tool entries too.

### Sub-project 2 — GitHub + skill-bundle ingest (16 commits, 10 plan tasks + 6 follow-ups)

Per the parent plan `docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md` (10 tasks). Spec: `docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`.

- `e963f55` **feat(config): github + bundles config + source_skill filter** (Task 1) — `GitHubConfig`, `BundlesConfig`, `SearchFilters.source_skill` plumbed through `_filters_to_where`.
- `587ee1c` **fix(config): add field validators for GitHubConfig + BundlesConfig** — Task 1 follow-up: `cache_max_mb > 0`, `api_base` http(s), template-placeholder requirements, non-empty strings.
- `bbed5da` **feat(github): fetcher with tarball + clone fallback + SHA cache** (Task 2) — `RepoRef`, `parse_repo_url`, `GitHubFetcher` (tarball download, shallow-clone fallback on rate-limit, `RateLimitedError` carrying `reset_at`, `FetcherError`).
- `c557ad6` **fix(github): prevent token leak via git stderr + sentinel-based cache** — Task 2 follow-up: `git -c http.extraHeader=Authorization: Bearer <token>` instead of URL-baked PAT; `_scrub_secrets` redacts stderr; `.complete` sentinel file (cache-corruption resilience); path-traversal regression test + defense-in-depth `resolve().is_relative_to()`; `__init__.py` re-export.
- `e5e231e` **feat(github): bundle.yml parser + link extractor** (Task 3) — `BundleManifest`, `LinkBag`, `PaperRef`, `ContentSpec`, `extract_links_from_text` (DOI / arXiv / PMC / classifier).
- `2ed240b` **fix(github): preserve DOI suffix case; keep domain as list[str]** — Task 3 follow-up: `_normalize_doi` lowercases prefix only; `BundleManifest.domain: list[str]` (scalar YAML → single-element list).
- `e2afab8` **feat(github): file walker + chunk producer (md/py/ipynb)** (Task 4) — `walk.py::walk_filtered` (pathspec gitwildmatch on root-relative POSIX paths); `chunk_producer.py::papers_from_directory` with 4 handlers (markdown + first-H1 title; notebook via stdlib `json`, outputs dropped; Python `ast` docstrings only; generic text fallback). Stable Paper IDs `github:{org}/{repo}@{sha or HEAD}:{rel_path}`.
- `acf4a88` **feat(github): top-level ingest_github_repo + ingest_skill_bundle + batch** (Task 5) — 3 async entry points + `IngestSummary` dataclass; DOI-only linked-paper routing; arXiv/PMC surface in `linked_papers_skipped_non_doi`; KB-name template, per-skill vs composite modes.
- `4ba91cb` **chore(gitignore): allowlist tests/data/sample_bundle/**/*.md** — fixture support.
- `fc4d6a8` **refactor(github-kb): reviewer fixes — dataclasses.replace, early precondition, shared embedder fixture** — Task 5 follow-up: `replace(summary, ...)` for composite re-stamp; `ValueError` moved to function entry; `DeterministicEmbeddingProvider` lifted to `tests/conftest.py`.
- `daaae27` **feat(cli): ingest-github-repo + ingest-skill-bundle[s] commands** (Task 6) — 3 Click commands mirroring `ingest-asb-run`'s `try/finally + app_state cleanup` pattern.
- `d43a061` **feat(mcp): ingest_github_repo + ingest_skill_bundle tools** (Task 7) — 2 MCP tools with `**Latency:**` docstrings; `_summary_to_dict` via `dataclasses.asdict`; smoke-test args registered.
- `693bb22` **docs(github-bundles): operator guide** (Task 8) — `docs/github-skill-bundle-ingest-2026-05-15.md` (198 lines): overview, quick start, CLI ref, MCP tools, config, auth, caching, per-filetype chunking, linked-paper ingest, per-skill vs composite, followups, troubleshooting.
- `869b306` **feat(github): emit external_link KB-log events for non-paper URLs** (Task 9) — extended `EventKind` Literal with `"external_link"`; `BundleManifest.collect_external_links() -> LinkBag`; `IngestSummary.external_links_logged: int`; emitted with `extra={"url", "category": "dataset"|"tool"}`.
- `f13deab` **docs(roadmap): GitHub + skill-bundle ingest shipped (2026-05-15)** (Task 10) — Wave 4.6 ✅; this commit closes the plan.

### Sub-project 3 — arXiv/PMC resolver for skill-bundle linked papers (1 commit)

Closes a P1 backlog item from the post-github-plan handoff. Previously arXiv and PMC IDs from `bundle.yml`/README were surfaced in `IngestSummary.linked_papers_skipped_non_doi` but never auto-routed; only DOIs went through `ingest_dois_into_kb`. The new resolvers convert arXiv/PMC → DOI so they ingest through the existing path.

- `6d0d3d2` **feat(pipeline): arXiv/PMC → DOI resolvers + auto-ingest in skill-bundle path** — new `src/perspicacite/pipeline/external_id_resolver.py` with `resolve_arxiv_to_doi` (OpenAlex `/works/doi:10.48550/arxiv.<id>` short-circuit, then arXiv API title + OpenAlex `title.search` fallback) and `resolve_pmc_to_doi` (NCBI `idconv/v1.0/`). `github_kb._route_linked_papers` partitions refs, runs resolvers on arXiv/PMC, dedups, populates new `IngestSummary.linked_papers_resolved_via_external_id` field. 9 unit tests + 1 integration test.

### Sub-project 4 — embedding-model conflict detection (2 commits)

Closes the other P1 backlog item from the handoff. Today, all KB-creation sites silently accept "KB exists with different embedding model" — a multi-KB query against incompatible KBs fails surprisingly far downstream. Now caught at ingest time with a clear error.

- `1e0d76d` **feat(rag): embedding-model conflict detection at KB creation** — new `src/perspicacite/rag/kb_compat.py` with `EmbeddingModelConflictError(ValueError)` + `check_embedding_compat_for_ingest(*, kb_meta, embedding_service)`. Wired into `search_to_kb._create_kb_if_missing` (Site A; covers `_make_or_get_kb` via delegation), `github_kb._add_papers_to_kb` (Site C), `ingest_dois_into_kb` (bonus — same vulnerability surface). Best-effort: no-op when either side lacks a model_name. 8 unit tests + 5 integration tests.
- `3d808d0` **test(mcp): extend SLOW_TOOLS registry with ingest_asb_run + github tools** — P2 housekeeping: the central `SLOW_TOOLS` list in `tests/unit/test_mcp_latency_docstrings.py` was missing entries for `ingest_asb_run`, `ingest_github_repo`, `ingest_skill_bundle`. All three already have `**Latency:**` docstrings; this just closes the registry-drift gap flagged in the Task 7 code review.

---

## Test counts

```
PYTHONPATH=src pytest tests/unit tests/integration --ignore=tests/integration/test_provider_matrix.py -q
1673 passed, 6 skipped, 276 warnings in 43.38s
```

- **+187 tests added** this session across the four sub-projects (164 first round + 10 arXiv/PMC + 13 embedding-conflict).
- **6 skips** = 1 ASB e2e (env-gated `PERSPICACITE_E2E_ASB`) + pre-existing skips.
- **No new failures.** The 2 pre-existing perf-baseline flakes (`test_perf_baseline`, `test_perf_baseline_llm`) appear to have stabilized in this run — they're wall-clock-sensitive and pass on average.
- **Provider matrix** (`test_provider_matrix.py::test_liveness_*`) excluded as before.

---

## Active backlog (post-session)

### P1 — Implementation, design decisions still needed

**(P1 backlog from the original handoff is now drained. Two items shipped this session — see Sub-projects 3 and 4 above.)**

New P1 candidates surfaced by recent reviews:

- **`cache_max_mb` is dead.** `GitHubConfig.cache_max_mb` is configured but no eviction logic uses it. Either implement size-based eviction in `pipeline/github/fetcher.py` (probably an LRU on cache_dir/<sha>/ entries) or rename/remove (operator doc references the knob). Touched by Task 2 code review.

### P2 — Smaller / less-blocking

- **pathspec `gitwildmatch` deprecation.** `pipeline/github/walk.py` uses `pathspec.PathSpec.from_lines("gitwildmatch", patterns)`; pathspec 0.12 deprecates this in favour of `gitignore` (functionally identical for this use case). Switch when convenient; tests already cover the behaviour.
- **`ChunkMetadata.paper_metadata_json` size.** JSON-encoded blob is stored on EVERY chunk of a paper (currently the same payload for all chunks). For huge ASB payloads on big papers, that's measurable storage. Worth a Wave 8 audit; for now the size is bounded by `extra="allow"` ParsedCard models so it's tractable.
- **Resolver-output dataclass.** `_route_linked_papers` now returns a 3-tuple `(added, skipped, resolved_count)`. If a fourth counter ever lands (e.g. resolution-failure rate), this should become a dataclass per Task 5/arxiv reviewer notes.
- **`title.search` URL-encoding in `external_id_resolver`.** The OpenAlex `title.search:"<title>"` filter is built via f-string interpolation. Vanishingly rare to hit a problem in practice (arXiv titles don't contain `?` or `&`), but worth URL-quoting + escaping internal `"` for hardness.

### P3 — Larger, needs brainstorm

- **Notebook execution to capture cell outputs.** Currently `_paper_from_notebook` strips outputs. Plotting + small-table outputs would be valuable for retrieval. Needs a sandboxed `nbclient` runner.
- **Full code-symbol indexing.** Today's `.py` handler emits docstrings only; full module bodies are out. A CTags-style index over function/class signatures would let the agent answer "where is X defined" against a code-heavy KB.
- **GitHub Enterprise / GHE Server / GitLab / Bitbucket adapters.** All deferred. The fetcher API is shaped to allow drop-in adapters but none built yet.
- **Watch mode (re-ingest on push).** Cron the CLI for now.
- **ASB capsules ingest, scenarios dir, graph-RAG over workflow DAGs** — all carried over from the previous session's backlog.

---

## Standing workflow (carry forward — do not re-discover)

Same as the previous session's handoff:
- **Per-task commits directly to the worktree branch; never push.**
- **Workflow for non-trivial work:** brainstorm → spec → plan → subagent-driven execution.
- **No clarifying questions** — make the reasonable call and continue.
- **Heredoc commit messages** with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.
- **PYTHONPATH=src** when running pytest inside this worktree.
- **Subagent-driven development**: one implementer per task; two-stage review (spec compliance + code quality); continuous execution.
- **Memory** at `~/.claude/projects/-Users-holobiomicslab-git-Perspicacite-AI/memory/`.

---

## Quick-start commands for the next session

```bash
# Confirm clean tree + correct branch
git status

# See this session's work
git log --oneline 6654b64..HEAD            # 22 commits

# Full suite (was 1650 passed / 6 skipped)
PYTHONPATH=src pytest tests/unit tests/integration --ignore=tests/integration/test_provider_matrix.py -q --tb=line 2>&1 | tail -5

# Try the new github-repo ingest end-to-end (mocked, no network)
PYTHONPATH=src pytest tests/integration/test_github_kb_e2e.py -v

# Try the new MCP tools (the dev server hosts MCP at /mcp)
curl -X POST http://localhost:5468/mcp -H 'Content-Type: application/json' -d '{
  "method":"tools/call","jsonrpc":"2.0","id":1,
  "params":{"name":"ingest_skill_bundle","arguments":{"source":"tests/data/sample_bundle"}}
}'

# Try the new CLI commands
perspicacite ingest-skill-bundle tests/data/sample_bundle/ --kb-name test_bundle --no-linked-papers

# Inspect the new pipeline
ls src/perspicacite/pipeline/github/
ls src/perspicacite/pipeline/github_kb.py
cat docs/github-skill-bundle-ingest-2026-05-15.md
```

---

## Suggested first move

If the user says "continue":

1. **arXiv / PMC auto-ingest** (P1) — natural follow-up to the github-bundle work; the helper code already collects them via `BundleManifest.collect_paper_refs()`, just need the kind→DOI resolver.
2. **Embedding-model conflict detection** (P1) — touches 3 sites consistently; ~1 subagent-driven task.
3. **Wave 4.1 PDF figure/table parsing** — biggest remaining scientific-feature gap, mentioned in the roadmap.

A conservative first move: fast-forward `main` to `f13deab`, push, tag the release as `v2.x.y-2026-05-16`.

---

## Pinned context

- **Repo:** `/Users/holobiomicslab/git/Perspicacite-AI`
- **Active worktree:** `.claude/worktrees/trusting-aryabhata-92508b` at `f13deab`. 49 commits ahead of `main`.
- **Branch:** `claude/trusting-aryabhata-92508b` (worktree-private).
- **Other worktrees:** unchanged (`dazzling-rhodes-7246b7`, `modest-banzai-98b17c`).

### Test invariants pinned this session (don't regress)

- `tests/unit/test_chunk_metadata_round_trip.py` — paper_metadata_json round-trip + peek path
- `tests/unit/test_source_reference_metadata.py` — SourceReference.metadata + mode plumbing
- `tests/unit/test_paper_metadata_codec.py` — shared decoder
- `tests/unit/test_chat_asb_metadata_sse.py` + `tests/unit/test_mcp_asb_metadata.py` — chat router + MCP wiring
- `tests/unit/test_github_fetcher.py` — RepoRef parsing, tarball cache, .complete sentinel, token-leak scrub
- `tests/unit/test_bundle_manifest.py` — bundle.yml parser, link extractor, DOI prefix-only lowercase, domain list shape
- `tests/unit/test_github_walk.py` + `test_github_chunk_producer.py` — pathspec walker, 4 file handlers
- `tests/integration/test_github_kb_e2e.py` — per-skill + composite mode, KB-name template, external_link events
- `tests/unit/test_cli_github_commands.py` + `tests/unit/test_mcp_github_tools.py` — CLI + MCP wrappers
- `tests/unit/test_kb_log_external_links.py` — KBEvent `external_link` + `collect_external_links`

### Memory entries (cross-session, unchanged)

- `project_perspicacite_setup.md`
- `feedback_workflow.md`
- `project_asb_kb_pairing_strategy.md`

---

## Effort summary

- **Total commits this session:** 25 (15 features, 6 fixes, 3 refactors, 3 docs/plan + roadmap + handoff/registry).
- **New source modules:** 9 (`pipeline/github/{__init__,fetcher,bundle,walk,chunk_producer}.py`, `pipeline/github_kb.py`, `rag/paper_metadata_codec.py`, `pipeline/external_id_resolver.py`, `rag/kb_compat.py`).
- **New test files:** 13 (`test_chunk_metadata_round_trip.py`, `test_source_reference_metadata.py`, `test_paper_metadata_codec.py`, `test_chat_asb_metadata_sse.py`, `test_mcp_asb_metadata.py`, `test_search_filters.py`, `test_github_fetcher.py`, `test_bundle_manifest.py`, `test_github_walk.py`, `test_github_chunk_producer.py`, `test_github_kb_e2e.py`, `test_cli_github_commands.py`, `test_mcp_github_tools.py`, `test_kb_log_external_links.py`, `test_external_id_resolver.py`, `test_kb_compat.py`).
- **New docs:** 2 (`docs/superpowers/plans/2026-05-16-asb-metadata-wiring.md`, `docs/github-skill-bundle-ingest-2026-05-15.md`).
- **Lines added (approx):** ~3200 source + ~3700 test + ~600 docs.
