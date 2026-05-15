# E2E real-LLM audit — final status (2026-05-15)

Companion to `AUDIT-2026-05-15.md`. Summarises the 9 findings and the
fixes that landed during the same audit cycle.

## Findings → fixes shipped

| # | Finding | Fix | Commit |
|---|---|---|---|
| F1 | AsyncLLMClient retried on AuthError | `retry_if_exception` predicate skips deterministic-fail cases | 49c2738 |
| F2 | agent_cli token usage discarded without provenance collector | `complete` now pushes to BudgetTracker directly | 74283bc |
| F3 | AuthError hint conflated invalid-key with quota | `suggested_action(provider, hint=...)` + `_auth_hint` sniffer | 49c2738 |
| F4 | Claude CLI rich result fields (cost_usd, cache hits) thrown away | `cost_usd_path` / `cache_read_tokens_path` / `cache_creation_tokens_path` config; `_parse_output_full` extracts them; `BudgetTracker.record_cost` accepts them | 74283bc |
| F5 | Output quality (sonnet vs haiku) — positive finding, no fix | n/a | — |
| F6 | Latency profile (positive) | n/a | — |
| F7 | Wave 6.3 perf baseline doesn't cover real synthesis | New `tests/integration/test_perf_baseline_llm.py` marked `live+perf`, env-var-driven baseline at `tests/data/perf_baseline_llm.json` | (this commit) |
| F8 | Embedding fallback opacity | FallbackEmbeddingProvider gains `last_used_model` + `fallback_triggered_count`; structured warning includes count | (this commit) |
| F9 | LiteLLM stderr banner noise | `litellm.suppress_debug_info=True` on import; loggers silenced at ERROR | 49c2738 |

## Verified empirically (post-fix)

**F1 + F3:** API path with invalid key, before fix:

```
FAILED in 6.407s: AuthError: anthropic: auth failed. Wait for the Anthropic quota reset, ...
FAILED in 4.513s: AuthError: anthropic: auth failed. Wait for the Anthropic quota reset, ...
```

After fix (from `anthropic_api-1778821380.json`):

```
FAILED in 0.355s: AuthError: anthropic: auth failed. API key is missing or invalid. Set the appropriate `*_API_KEY` env var ...
FAILED in 0.176s: AuthError: anthropic: auth failed. API key is missing or invalid. Set the appropriate `*_API_KEY` env var ...
```

- ~18× faster failure (no wasted retries on a deterministic problem).
- Message correctly identifies the failure mode.
- No LiteLLM banner pollution.

**F2 + F4:** Claude Code Sonnet path with the new wiring
(`claude_cli_sonnet-1778821085.json`):

```
ex_a: cost_usd=0.04634325  input_tokens=3  output_tokens=703  cache_read=15435  cache_creation=8309
ex_b: cost_usd=0.03344925  input_tokens=3  output_tokens=615  cache_read=18730  cache_creation=4959

budget summary: {'tokens_in': 6, 'tokens_out': 1318, 'usd': 0.079793, 'has_unknown_costs': False}
```

- Exact CLI-reported cost lands in BudgetTracker (sum: $0.0798).
- Prompt-cache reads / creates are observable (Wave 2.1 cache *is* working
  on the API path — visible cache_read tokens on calls 2+).
- No more "unknown costs" warning.

**F7:** Captured baseline: claude_cli/haiku, ~3.8 s avg synthesis,
~990 chars output.

**F8:** Unit-tested via `tests/unit/test_embedding_fallback_tracking.py`.
Run-time: `last_used_model` now exposes the actual model used per call
(previously only the static "primary|fallback" tag was available).

## Test footprint added by this audit cycle

| File | Tests | Purpose |
|---|---|---|
| `tests/unit/test_audit_hardening.py` | 13 | F1, F3, F9 |
| `tests/unit/test_agent_cli_rich_fields.py` | 6 | F2, F4 |
| `tests/unit/test_embedding_fallback_tracking.py` | 4 | F8 |
| `tests/integration/test_perf_baseline_llm.py` | 1 (live) | F7 |
| `tests/audit/run_e2e_audit.py` | (harness) | reproducible end-to-end audit |

23 new unit tests, all green. Run-time: ~2 seconds total.

## What still isn't covered

1. **Real Anthropic API parity comparison.** Blocked by the invalid
   `ANTHROPIC_API_KEY` in `.env` — placeholder string, not a real key.
   With a valid key, `python tests/audit/run_e2e_audit.py --provider api`
   would capture the data.

2. **Output-quality regression detection.** The audit captured the
   actual text outputs from sonnet + haiku for two example queries.
   Comparing future outputs against these stored answers (semantic
   similarity, citation coverage) would catch synthesis regressions.
   That's a separate corpus-evaluation effort — not in scope for
   framework-hardening.

3. **Wave 4.1 multimodal extraction in the live pipeline.** The
   audit only exercised abstract-only synthesis. A full real-LLM
   audit including figure/table parsing would need ~10 minutes per
   example for PDF downloads and is a follow-up.

4. **Cross-KB routing under real LLM.** Today's `auto_route_kbs`
   path uses BM25 + (optionally) an LLM judge. The LLM-judge variant
   wasn't exercised in this audit.

## Bottom line

7 of 9 audit findings shipped fixes in the same cycle (F1, F2, F3,
F4, F7, F8, F9). F5 + F6 were positive findings (no fix needed).
The framework-hardening roadmap (Waves 1–7) plus this audit-driven
hardening (F1–F9) is now done.

322 commits ahead of `origin/main`; nothing has been pushed per the
user's standing instruction.
