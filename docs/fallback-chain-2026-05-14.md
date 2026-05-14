# Per-provider fallback chain — operator guide (2026-05-14)

Wave 3.2 of the framework-hardening roadmap. Pin a list of providers
per stage; the client tries each on failure.

## Config

```yaml
llm:
  providers_per_stage:
    routing: anthropic                      # single — backwards compat
    synthesis_heavy:
      - anthropic                           # primary
      - claude_cli                          # subscription fallback
      - deepseek                            # cheap reliable fallback
```

The same model from `models[stage]` (or `default_model`) is used
across the chain. Agent-CLI providers' `model_aliases` translate
names internally — so `claude-sonnet-4-5` on `anthropic` and
`claude_cli` "just works" if claude_cli's preset includes the alias
`claude-sonnet: sonnet`.

## API

```python
from perspicacite.llm.client import resolve_stage_chain

chain = resolve_stage_chain(config, "synthesis_heavy")
# → [("anthropic", "claude-sonnet-4-5"),
#    ("claude_cli", "claude-sonnet-4-5"),
#    ("deepseek", "claude-sonnet-4-5")]

text = await client.complete_with_chain(messages=msgs, chain=chain)
```

## What triggers fallback

- `RateLimitError` (Wave 3.1) — primary use case.
- Any other `Exception` after `complete()`'s inner tenacity retries
  exhaust.

## What does NOT trigger fallback

- `BudgetExceededError` (Wave 2.4) — re-raised immediately. Switching
  providers doesn't help a budget breach.

## All-attempts-fail behaviour

The **last** exception is raised. Earlier failures are logged
(`llm_chain_step_failed`) but not aggregated. Keep the surface
simple: if you want to know what happened on attempt 1, read the
log.

## Caveats

- Caching (Wave 2.1) is keyed on the resolved (provider, model)
  pair. A cache miss on attempt 1 means attempt 2 also checks
  *its own* cache key — different provider, different key. This is
  intentional: switching providers is the whole point.
- Budget tracking (Wave 2.4) records each actual call, not the
  intended primary. A run that always falls through to `deepseek`
  costs deepseek prices, not anthropic prices.
- Orchestrator call sites that use `complete()` directly don't
  benefit from the chain — they need to migrate to
  `complete_with_chain` + `resolve_stage_chain`. Migration is a
  documented followup.

## Followups

- Migrate orchestrator call sites (kb_router, screen_papers,
  rephrase_query, synthesis) to `complete_with_chain`.
- Per-provider model overrides (dict form in `providers_per_stage`).
- Sticky failed-provider cache: skip providers known to be
  rate-limited until their `retry_after_seconds` elapses.
- Per-stage circuit breaker.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/config/schema.py` | Widen `providers_per_stage` type |
| `src/perspicacite/llm/client.py` | `resolve_stage_chain` + `complete_with_chain` |
