# Error-path audit — design spec

**Wave 3.4 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Every common failure mode (missing API key, wrong model
name, Ollama down, `claude` binary missing) yields a helpful,
structured exception with a one-line description and a suggested
next step — not a stack trace.

This wave is mostly a **test suite** that pins the contract, plus
small fixes to wrap a few residual `Exception`s that still leak
through.

## Failure-mode catalogue (target behaviour)

| Mode | Path | Today | After Wave 3.4 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` missing | direct API | LiteLLM AuthenticationError leaks | `AuthError("anthropic: API key missing — set ANTHROPIC_API_KEY")` |
| `OPENAI_API_KEY` missing | direct API | Same | `AuthError("openai: API key missing — set OPENAI_API_KEY")` |
| Wrong model name | direct API | LiteLLM 404 / 400 leaks | unchanged (low-value wrap; 404 with model name is already clear) |
| Ollama not running | LiteLLM | `httpx.ConnectError` leaks via tenacity | unchanged (clear in stack) |
| `claude` binary missing | agent_cli | `RuntimeError("claude_cli: CLI not found at 'claude'...")` (already friendly) | unchanged ✅ |
| Codex binary missing | agent_cli | Same pattern ✅ | unchanged ✅ |
| Codex auth expired (`Please run 'codex login'`) | agent_cli | `RuntimeError("agent_cli: CLI exited 1: Please run 'codex login'")` | `AuthError(provider="agent_cli", "auth expired — run `codex login`")` |
| Rate limit | any | `RateLimitError` (Wave 3.1) ✅ | unchanged ✅ |
| Budget exceeded | any | `BudgetExceededError` (Wave 2.4) ✅ | unchanged ✅ |

The "leaks" cases get wrapped. The "unchanged" cases are already
clear — the test pins them so a regression breaks CI.

## Implementation

Two small additions to `perspicacite.llm.errors`:

```python
_AUTH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bauthentication\b.*\b(error|failed|invalid)\b", re.IGNORECASE),
    re.compile(r"\b(api[_ ]?key)\b.*\b(missing|not set|required|invalid)\b", re.IGNORECASE),
    re.compile(r"please run [\"']?\w+ login", re.IGNORECASE),
    re.compile(r"\b(401|unauthorized)\b", re.IGNORECASE),
]

def detect_auth_error(text: str) -> bool: ...
```

And extend `_maybe_rate_limit` in `client.py` into a general
`_maybe_wrap_error(exc, provider) → Exception` that also produces
`AuthError` when the auth pattern matches. (We rename the helper to
match its expanded scope.)

For the agent-CLI path, similarly extend the non-zero-exit branch to
detect auth patterns in stderr and raise `AuthError`.

## Components

| File | Change |
|---|---|
| `src/perspicacite/llm/errors.py` | Add `_AUTH_PATTERNS` + `detect_auth_error()` + `AuthError.provider` field. |
| `src/perspicacite/llm/client.py` | Rename `_maybe_rate_limit` → `_maybe_wrap_error`; also check auth patterns. |
| `src/perspicacite/llm/agent_cli.py` | After rate-limit check, also run auth-pattern check; raise `AuthError`. |
| `tests/unit/test_error_path_audit.py` (new) | One test per failure mode in the catalogue. ~10 tests. |

## Test plan

Each test sets up the failure condition (env, mock, stubbed
subprocess) and asserts the exception type + message hints.

- `test_missing_anthropic_key_raises_auth_error`
- `test_missing_openai_key_raises_auth_error`
- `test_codex_auth_expired_raises_auth_error`
- `test_claude_binary_missing_raises_helpful_runtime_error`
- `test_codex_binary_missing_raises_helpful_runtime_error`
- `test_detect_auth_error_patterns`
- `test_auth_error_carries_provider`
- `test_rate_limit_still_wins_when_both_patterns_match`
  (precedence: rate-limit pattern takes priority over generic auth)

## Followups

- Standardise the `provider` field on every `LLMError` subclass.
- Centralise the message format across the providers (single helper
  formats `"<provider>: <kind>. <suggested action>"`).
- Wrap LiteLLM `APIConnectionError` / `APITimeoutError` as our
  `TimeoutError` / `LLMError` for consistency.
