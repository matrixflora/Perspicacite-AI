# Rate-limit handling — status & operator guide (2026-05-14)

Wave 3.1 of the framework-hardening roadmap. Detect rate-limit
signals from every LLM path and surface a structured `RateLimitError`
with provider, retry hint, and a suggested next step.

## What it does

Any rate-limit response — whether from a direct API call, the
Anthropic LiteLLM path, or a subprocess agent CLI — now raises:

```python
RateLimitError(provider="anthropic",
               retry_after_seconds=2840,
               args=("anthropic: rate limit. Wait for the Anthropic ..."))
```

The previous behaviour (cryptic `Exception` / `RuntimeError`) was a
debugging hazard. The structured type is also the foundation for
Wave 3.2 (fallback chain), which will use the `provider` field to
skip the failing provider on retry.

## Detection patterns

`src/perspicacite/llm/errors.py` ships a small regex list:

- `rate limit reached. try again in Xh Ym` (Claude Code format —
  parses retry-after seconds)
- `usage limit exceeded`
- HTTP 429 / "too many requests"
- generic "rate limit"
- "quota exceeded" / "quota exhausted"

The list is permissive on purpose: false positives are cheap (tenacity
retries either way), false negatives buried the failure under cryptic
output.

## Where detection runs

| Path | Detection point |
|---|---|
| `AsyncLLMClient.complete()` standard branch | LiteLLM exception class-name match + message regex |
| Minimax branch | Same |
| `AgentCLIClient` (Claude Code, Codex, OpenClaw, Hermes) | stderr+stdout regex scan on non-zero exit |
| MCP sampling | Falls through to the inner branch — no extra wrapping needed today |

## Operator advice

Per provider, `suggested_action(provider)` returns a human-readable
hint:

| Provider | Hint |
|---|---|
| anthropic | Route to DeepSeek / OpenAI / Gemini via `providers_per_stage` |
| openai | Route to Anthropic / DeepSeek / Gemini |
| claude_cli | Switch to direct API or wait for subscription reset |
| agent_cli | Wait for reset or fall back |
| ollama | Check `localhost:11434` server logs |
| default | Wait or configure fallback |

## Caveats

- LiteLLM may not surface upstream `Retry-After` headers as
  structured fields — only the message text is regex-scanned.
- Agent-CLI detection only fires on `returncode != 0`. A CLI that
  exits 0 with a rate-limit warning in stdout is not caught. None of
  our verified presets behave this way, but worth knowing.
- Wave 3.2 (fallback chain) reads the `provider` field to skip; it's
  not implemented yet, so today's tenacity retry simply retries the
  same provider.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/llm/errors.py` | exception hierarchy + `detect_rate_limit` + `suggested_action` |
| `src/perspicacite/llm/agent_cli.py` | subprocess stderr scanning |
| `src/perspicacite/llm/client.py` | LiteLLM exception wrapping |

## Followups

- Wave 3.2: per-provider fallback chain (this wave is the prerequisite).
- HTTP `Retry-After` header extraction from LiteLLM responses.
- Adaptive backoff: cache the reset time per provider so subsequent
  attempts skip until the deadline.
