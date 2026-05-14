# Rate-limit detection & clean error surface — design spec

**Wave 3.1 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Detect rate-limit errors from every LLM path (direct API,
Claude Code CLI, Codex CLI, generic agent_cli) and surface them as a
single `RateLimitError` with a descriptive, actionable message
instead of cryptic stderr text or generic LiteLLM exceptions.

## Why

Today, a rate-limit hit during a multi-paper ingest produces:

- LiteLLM path: `Exception("RateLimitError: ...")` from inside
  tenacity's retry loop, eventually surfacing the last exception
  buried under retry wrapper context.
- Agent-CLI path: `RuntimeError("claude_cli: CLI exited 1: Rate
  limit reached. Try again in 47m 32s.")` — usable but inconsistent.

After this wave: every rate-limit signal produces a
`RateLimitError` with structured fields (provider, retry-after
hint, suggested action) the orchestrator can format nicely or route
to the fallback chain (Wave 3.2 will pick this up).

## Architecture

A new exception hierarchy in `perspicacite.llm.errors`:

```python
class LLMError(RuntimeError): ...                # base
class RateLimitError(LLMError):
    provider: str
    retry_after_seconds: int | None  # parsed when possible
    suggested_action: str             # human-readable hint
class TimeoutError(LLMError): ...                # CLI / API timeout
class AuthError(LLMError): ...                   # 401 / missing creds
```

Detection happens in two places:

1. **Agent-CLI subprocess errors** (`agent_cli.py`): when
   `proc.returncode != 0`, run stderr+stdout through a small list of
   regex patterns. On match → `RateLimitError`. Otherwise fall back
   to the existing generic `RuntimeError`.

2. **LiteLLM exceptions** (`client.py`): wrap the `await
   litellm.acompletion(...)` call in a `try/except` that catches
   `litellm.exceptions.RateLimitError` (and any subclass) and
   re-raises as our `RateLimitError` with provider/model context.

The existing tenacity retry chain still works — it catches `Exception`
broadly. The new exception types are still `Exception` subclasses, so
backoff still happens. The improvement is the *final* exception the
caller sees.

## Pattern catalogue

Stored in `errors.py` as a list of `(regex, retry_seconds_extractor)`
tuples. Detection runs each pattern; first match wins.

| Source | Pattern (case-insensitive) | Retry parser |
|---|---|---|
| Claude Code stderr | `rate limit reached.*try again in (\d+)h? ?(\d+)?m` | hours+minutes → seconds |
| Claude Code stderr | `usage limit exceeded.*resets at` | None |
| Codex stderr | `429` or `rate.?limit` | None |
| Codex stderr | `quota exceeded` | None |
| Generic | `too many requests` | None |
| Generic | `\b429\b` | None |

The list lives in `errors.py` as a module-level constant so adding a
new pattern is a one-line change.

## Suggested-action messages

Hard-coded mapping from provider to advice:

| Provider | Suggested action |
|---|---|
| anthropic | "Wait for the quota reset, or set `providers_per_stage` to route this stage through DeepSeek / OpenAI / Gemini." |
| openai | Same, suggest Anthropic / DeepSeek / Gemini. |
| claude_cli | "Your Claude Pro/Max session is rate-limited. Switch to the direct API path (`providers_per_stage` to `anthropic`) or wait for reset." |
| agent_cli (codex) | "Codex subscription is rate-limited. Wait for reset or fall back to another agent_cli / direct API." |
| ollama | "Local Ollama returned a transient error — check the server logs (`localhost:11434`)." |
| default | "Wait for quota reset or configure a fallback provider." |

When Wave 3.2 (fallback chain) lands, the orchestrator can use the
`provider` field to skip the failing provider on retry.

## Components

| File | Change |
|---|---|
| `src/perspicacite/llm/errors.py` (new) | `LLMError`, `RateLimitError`, `TimeoutError`, `AuthError`, pattern list, `detect_rate_limit(text)`. |
| `src/perspicacite/llm/agent_cli.py` | On non-zero exit, run stderr+stdout through `detect_rate_limit`; raise `RateLimitError` on match. |
| `src/perspicacite/llm/client.py` | Catch `litellm.exceptions.RateLimitError` (and subclasses) and re-raise as our `RateLimitError`. |
| `tests/unit/test_rate_limit_detection.py` (new) | Pattern matches, retry-seconds parsing, suggested-action lookup. |
| `tests/unit/test_agent_cli_rate_limit.py` (new) | Mocked subprocess returning rate-limit stderr → `RateLimitError` propagation. |

## Behaviour contract

- A LiteLLM rate-limit exception → `RateLimitError(provider=..., retry_after_seconds=None)`.
- An agent-CLI process exiting non-zero with rate-limit text in
  stderr → `RateLimitError(provider=..., retry_after_seconds=N or None)`.
- An agent-CLI process exiting non-zero **without** rate-limit text →
  unchanged `RuntimeError` (no behavioural change for non-rate-limit
  errors).
- Tenacity retry still applies; after exhaustion the exception that
  bubbles up is the new structured one.

## Test plan

- `test_detect_rate_limit_claude_code_with_retry_minutes`
- `test_detect_rate_limit_claude_code_without_minutes`
- `test_detect_rate_limit_codex_429`
- `test_detect_rate_limit_generic_too_many_requests`
- `test_detect_rate_limit_non_matching_returns_none`
- `test_suggested_action_per_provider`
- `test_agent_cli_raises_rate_limit_error_on_match`
- `test_agent_cli_raises_runtime_error_on_non_rate_limit_failure`
- `test_litellm_rate_limit_wrapped_as_perspicacite_error`

## Followups

- Wave 3.2 (fallback chain) reads the structured exception to choose
  the next provider.
- HTTP `Retry-After` header parsing on direct-API rate limits (LiteLLM
  may or may not surface it — depends on provider).
- Adaptive backoff: cache the rate-limit reset time per provider so
  subsequent attempts skip the failing provider until the deadline.
