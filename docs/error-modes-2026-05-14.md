# Error modes — operator catalogue (2026-05-14)

Wave 3.4 of the framework-hardening roadmap. Pinned contract for
every common failure mode.

## Exception hierarchy

```
RuntimeError
└── LLMError                     # base
    ├── RateLimitError           # provider rate / quota limit
    ├── AuthError                # 401 / missing key / expired session
    └── TimeoutError             # subprocess or API timeout

RuntimeError                     # not LLMError — for things like
                                 # missing binary, config errors

BudgetExceededError              # Wave 2.4 — separate type, doesn't
                                 # advance fallback chains
```

All `LLMError` subclasses carry `.provider` so the orchestrator /
fallback chain can react. Messages follow the format:

```
"<provider>: <kind>. <suggested action>"
```

## Failure-mode catalogue

| Trigger | Exception | Message hint |
|---|---|---|
| `ANTHROPIC_API_KEY` missing | `AuthError(provider="anthropic")` | "auth failed. Wait for ... or set the key" |
| `OPENAI_API_KEY` missing | `AuthError(provider="openai")` | Same |
| Anthropic 401 | `AuthError(provider="anthropic")` | Same |
| Codex `Please run 'codex login'` | `AuthError(provider="agent_cli")` | "auth failed. Wait for reset or fall back" |
| Anthropic 429 / rate-limit text | `RateLimitError(provider="anthropic")` | "rate limit. Route to DeepSeek / OpenAI / Gemini" |
| Claude Code "Rate limit reached. Try again in 1h 23m." | `RateLimitError(provider="claude_cli", retry_after_seconds=4980)` | "rate limit. Switch to direct API or wait" |
| `claude` binary missing | `RuntimeError` | "claude_cli: CLI not found at 'claude'. Install it..." |
| Codex binary missing | `RuntimeError` | "agent_cli: CLI not found at 'codex'. Install it..." |
| Ollama not running | `httpx.ConnectError` (unwrapped — message is already clear) | — |
| Budget breach | `BudgetExceededError` | "input_tokens=X > cap=Y" |

## What still leaks (and why)

- **Ollama connection refused** — `httpx.ConnectError` from LiteLLM
  bubbles up. Message already says "Connection refused at
  localhost:11434" which is the actionable detail. Wrapping would
  add no value.
- **Wrong model name** — LiteLLM raises a clean 404 with the model
  name. Already actionable.
- **PDF parse failure during ingest** — separate subsystem; Wave 3.3
  checkpoints catch this so the user sees `state.processed[doi] =
  "failed: <reason>"`.

## Test pins

`tests/unit/test_error_path_audit.py` runs each scenario with
mocked dependencies. CI will fail if any of these regress.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/llm/errors.py` | `LLMError`, `RateLimitError`, `AuthError`, pattern detectors |
| `src/perspicacite/llm/client.py` | `_maybe_wrap_error` — LiteLLM exception wrapping |
| `src/perspicacite/llm/agent_cli.py` | stderr scanning for rate-limit + auth patterns |

## Followups

- Standardise `provider` on `BudgetExceededError` (currently
  optional).
- Wrap LiteLLM `APIConnectionError` / `APITimeoutError` as our
  `TimeoutError`.
- Single helper for the `"<provider>: <kind>. <action>"` message
  format so all sites stay consistent.
