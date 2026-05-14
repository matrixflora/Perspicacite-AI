# Per-provider fallback chain — design spec

**Wave 3.2 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Let users pin a *list* of providers per stage. On
`RateLimitError` (or generic call failure), the client transparently
tries the next provider in the chain before giving up.

## Use case

```yaml
llm:
  providers_per_stage:
    synthesis_heavy:                       # most expensive stage
      - anthropic                          # primary: direct API + caching
      - claude_cli                         # fallback 1: subscription
      - deepseek                           # fallback 2: cheap & reliable
    routing: anthropic                     # single string — backwards compat
```

When `anthropic` rate-limits during a long ingest, the call retries
on `claude_cli` automatically; if that's also limited, then
`deepseek`. The user sees their final answer instead of a stack
trace.

## Backwards compatibility

`providers_per_stage` today accepts `dict[str, str]`. We widen the
type to `dict[str, str | list[str]]`. A bare string is treated as a
one-element chain. Every existing config continues to parse and
behave identically.

## Architecture

A new helper in `llm/client.py`:

```python
def resolve_stage_chain(config, stage: str) -> list[tuple[str, str]]:
    """Return [(provider, model), ...] in fallback order."""
```

- Reads `providers_per_stage[stage]` — accepts `str` or `list[str]`.
- Falls back to `[(default_provider, default_model)]` when the stage
  isn't pinned.
- The same `models[stage]` (or `default_model`) is used for every
  provider in the chain. Per-provider model alias translation happens
  inside `AgentCLIClient._resolve_model` for the agent-CLI paths;
  direct-API providers accept the configured model name as-is.

A new method on `AsyncLLMClient`:

```python
async def complete_with_chain(
    self,
    messages: list[dict],
    chain: list[tuple[str, str]],
    **kwargs,
) -> str:
    """Try each (provider, model) in order. On RateLimitError or any
    Exception, log + try the next. Raise the last exception if all
    fail. Returns the first successful response.

    The disk cache (Wave 2.1) and budget tracker (Wave 2.4) operate
    transparently across attempts: a hit on attempt 1's key short-
    circuits all attempts; budget records accumulate per actual call.
    """
```

The existing `complete_with_fallback(primary, fallback)` keeps
working — call sites that already use it don't change.

## What counts as "try next"

The chain advances on:

1. `RateLimitError` (Wave 3.1).
2. Any subclass of `LLMError`.
3. Generic `Exception` after tenacity exhausts retries — the inner
   retry chain still runs at each step, so a single transient
   network error gets retried 3× on attempt 1 before falling through
   to attempt 2.

The chain does **not** advance on:
- `BudgetExceededError` — re-raised immediately; switching providers
  doesn't help a budget breach.
- `KeyboardInterrupt` / `asyncio.CancelledError` — re-raise.

## Components

| File | Change |
|---|---|
| `src/perspicacite/config/schema.py` | Widen `providers_per_stage` type annotation. |
| `src/perspicacite/llm/client.py` | Add `resolve_stage_chain` + `AsyncLLMClient.complete_with_chain`. |
| `tests/unit/test_fallback_chain_resolution.py` (new) | Chain resolution: string vs list vs missing. |
| `tests/unit/test_fallback_chain_dispatch.py` (new) | First success returned; rate-limit on primary falls through; all-fail raises last; budget-exceeded propagates. |

## Behaviour contract

- Chain length 1 → identical to `complete()`.
- Chain length N, attempts 1..K-1 fail with `RateLimitError`,
  attempt K succeeds → returns K's response.
- All attempts fail → raises the **last** exception (most recent
  attempt). Earlier exceptions are logged but not aggregated into a
  custom exception — keep the surface simple.
- `BudgetExceededError` on any attempt → raise immediately,
  short-circuit the rest of the chain.

## Test plan

- `test_resolve_chain_single_string_returns_one_element`
- `test_resolve_chain_list_of_strings`
- `test_resolve_chain_missing_stage_falls_back_to_default`
- `test_resolve_chain_uses_stage_model_when_pinned`
- `test_chain_returns_first_success`
- `test_chain_falls_through_on_rate_limit`
- `test_chain_falls_through_on_generic_exception`
- `test_chain_raises_last_exception_when_all_fail`
- `test_chain_short_circuits_on_budget_exceeded`

## Followups

- Per-provider model overrides via dict form
  (`[{provider: ollama, model: llama3.1:70b}, ...]`).
- Sticky failed-provider cache: skip providers known to be
  rate-limited for the duration of their `retry_after_seconds`.
- Per-stage circuit breaker: if synthesis_heavy hits the fallback
  twice in a row, demote the primary for the remainder of the run.
