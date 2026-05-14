# Error-path audit — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Pin the contract: every common failure mode yields a
structured exception with a clear message. Wrap two residual leaks
(missing API key → `AuthError`, agent-CLI auth-expired → `AuthError`).

**Spec:** `docs/superpowers/specs/2026-05-14-error-path-audit-design.md`

---

## Task 1: Auth-pattern detection in errors.py

**Files:**
- Modify: `src/perspicacite/llm/errors.py`
- Test: `tests/unit/test_auth_error_detection.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_auth_error_detection.py
"""detect_auth_error patterns + AuthError shape (Wave 3.4)."""
from perspicacite.llm.errors import (
    AuthError, LLMError, detect_auth_error, suggested_action,
)


def test_detects_authentication_failed():
    assert detect_auth_error("AuthenticationError: invalid API key")


def test_detects_api_key_missing():
    assert detect_auth_error("OPENAI_API_KEY environment variable not set")


def test_detects_401():
    assert detect_auth_error("HTTP 401 Unauthorized")


def test_detects_codex_login_prompt():
    assert detect_auth_error("Please run 'codex login' to authenticate")


def test_non_matching_returns_false():
    assert not detect_auth_error("Some other error")
    assert not detect_auth_error("")


def test_auth_error_provider_field():
    err = AuthError("anthropic: API key missing", provider="anthropic")
    assert isinstance(err, LLMError)
    assert err.provider == "anthropic"
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_auth_error_detection.py -v
```

Expected: `AttributeError: module 'perspicacite.llm.errors' has no attribute 'detect_auth_error'` plus `AuthError(...)` not accepting `provider=`.

- [ ] **Step 3: Implement**

In `src/perspicacite/llm/errors.py`:

**3a.** Replace the existing `AuthError` definition with one that
takes a `provider` field, matching `RateLimitError`'s shape:

```python
class AuthError(LLMError):
    """Provider auth failed (401, missing creds, expired session)."""

    def __init__(self, message: str, *, provider: str = "unknown"):
        super().__init__(message)
        self.provider = provider
```

**3b.** Add the auth-pattern list and detector below the existing
rate-limit code:

```python
_AUTH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bauthentication\s*error\b", re.IGNORECASE),
    re.compile(r"\b(api[_\s]?key)\b.*\b(missing|not\s*set|required|invalid)\b",
               re.IGNORECASE),
    re.compile(r"\b(missing|not\s*set)\b.*\b(api[_\s]?key)\b", re.IGNORECASE),
    re.compile(r"environment\s*variable.*\bnot\s*set\b", re.IGNORECASE),
    re.compile(r"please\s+run\s+['\"]?\w+\s+login", re.IGNORECASE),
    re.compile(r"\b401\b|\bunauthorized\b", re.IGNORECASE),
]


def detect_auth_error(text: str) -> bool:
    """Return True when ``text`` looks like an auth failure."""
    if not text:
        return False
    return any(p.search(text) for p in _AUTH_PATTERNS)
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_auth_error_detection.py -v
pytest tests/unit/test_rate_limit_detection.py -v   # no regression
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/errors.py tests/unit/test_auth_error_detection.py
git commit -m "feat(llm-errors): AuthError with provider field + detect_auth_error patterns (Wave 3.4)"
```

---

## Task 2: Wrap auth errors in client + agent_cli

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Modify: `src/perspicacite/llm/agent_cli.py`
- Test: `tests/unit/test_error_path_audit.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_error_path_audit.py
"""End-to-end error-path audit (Wave 3.4)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.agent_cli import AgentCLIClient
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.errors import AuthError, LLMError, RateLimitError


def _make_proc_mock(returncode: int, stderr: bytes, stdout: bytes = b""):
    class _FakeProc:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self, _stdin=None):
            return stdout, stderr

        async def wait(self):
            return self.returncode

        def kill(self):
            pass

    async def factory(*args, **kwargs):
        return _FakeProc()

    return factory


@pytest.mark.asyncio
async def test_codex_auth_expired_raises_auth_error():
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="agent_cli",
        output_format="text",
    )
    factory = _make_proc_mock(
        returncode=1,
        stderr=b"Please run 'codex login' to authenticate first.",
    )
    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(AuthError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])
    assert exc.value.provider == "agent_cli"


@pytest.mark.asyncio
async def test_claude_binary_missing_raises_friendly_runtime_error():
    cli = AgentCLIClient(
        executable="/nonexistent-binary-perspicacite-test",
        provider_label="claude_cli",
        output_format="text",
    )
    async def factory(*args, **kwargs):
        raise FileNotFoundError("No such file")
    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RuntimeError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])
    msg = str(exc.value)
    assert "claude_cli" in msg
    assert "nonexistent" in msg or "Install" in msg or "executable" in msg


@pytest.mark.asyncio
async def test_litellm_auth_error_wrapped(tmp_path):
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    client = AsyncLLMClient(cfg)

    class FakeAuth(Exception):
        pass
    FakeAuth.__name__ = "AuthenticationError"

    async def boom(*args, **kwargs):
        raise FakeAuth("AuthenticationError: invalid x-api-key")

    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock()
        litellm.acompletion = boom
        mock_get.return_value = litellm
        with pytest.raises(AuthError) as exc:
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                cache=False,
            )
    assert exc.value.provider == "anthropic"


@pytest.mark.asyncio
async def test_missing_api_key_message_raises_auth_error(tmp_path):
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    client = AsyncLLMClient(cfg)

    async def boom(*args, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock()
        litellm.acompletion = boom
        mock_get.return_value = litellm
        with pytest.raises(AuthError):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                cache=False,
            )


@pytest.mark.asyncio
async def test_rate_limit_wins_when_both_patterns_match():
    """Rate-limit detection runs before auth detection, so a message
    containing both signals stays a RateLimitError."""
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="claude_cli",
        output_format="text",
    )
    factory = _make_proc_mock(
        returncode=1,
        stderr=b"Rate limit reached. Try again in 5m. (HTTP 401)",
    )
    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RateLimitError):
            await cli.complete([{"role": "user", "content": "hi"}])
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_error_path_audit.py -v
```

- [ ] **Step 3: Extend the client wrapper**

In `src/perspicacite/llm/client.py`, find the existing
`_maybe_rate_limit` helper. Rename to `_maybe_wrap_error` and extend
to handle auth:

```python
def _maybe_wrap_error(exc: Exception, provider: str) -> Exception:
    """If ``exc`` is a known LLM-error pattern (rate limit, auth),
    return a fresh structured exception. Otherwise return ``exc``
    unchanged."""
    cls_name = type(exc).__name__
    msg = str(exc)

    # Rate-limit takes priority over auth — see test_rate_limit_wins_when_both_patterns_match.
    from perspicacite.llm.errors import (
        AuthError, RateLimitError, detect_auth_error, detect_rate_limit,
        suggested_action,
    )
    if cls_name == "RateLimitError" or cls_name.endswith(".RateLimitError"):
        return RateLimitError(
            f"{provider}: rate limit. {suggested_action(provider)}",
            provider=provider,
        )
    hit = detect_rate_limit(msg)
    if hit is not None:
        return RateLimitError(
            f"{provider}: rate limit. {suggested_action(provider)}",
            provider=provider,
            retry_after_seconds=hit.retry_after_seconds,
        )
    if cls_name in ("AuthenticationError", "PermissionDeniedError") or detect_auth_error(msg):
        return AuthError(
            f"{provider}: auth failed. {suggested_action(provider)}",
            provider=provider,
        )
    return exc
```

Update the two `raise _maybe_rate_limit(e, provider) from e` call
sites to `raise _maybe_wrap_error(e, provider) from e`.

- [ ] **Step 4: Extend agent_cli detection**

In `src/perspicacite/llm/agent_cli.py`, find the non-zero-exit branch
where rate-limit detection was added (Wave 3.1). Just after the
rate-limit `if hit is not None: raise RateLimitError(...)` block, add
an auth check:

```python
            from perspicacite.llm.errors import (
                AuthError, detect_auth_error,
            )
            if detect_auth_error(err_full) or detect_auth_error(out_str):
                raise AuthError(
                    f"{self.provider_label}: auth failed. "
                    f"{suggested_action(self.provider_label)}",
                    provider=self.provider_label,
                )
```

- [ ] **Step 5: Run, watch pass**

```bash
pytest tests/unit/test_error_path_audit.py -v
pytest tests/unit/test_agent_cli_rate_limit.py -v       # no regression
pytest tests/unit/test_litellm_rate_limit_wrap.py -v    # no regression
```

Then broader suite:

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/llm/client.py \
        src/perspicacite/llm/agent_cli.py \
        tests/unit/test_error_path_audit.py
git commit -m "feat(llm): AuthError wrapping in client + agent_cli (Wave 3.4)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/error-modes-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
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
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/error-modes-*.md` to `.gitignore` after
`!docs/checkpoint-resume-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/error-modes-2026-05-14.md .gitignore
git commit -m "docs(error-modes): operator catalogue (Wave 3.4)"
```

---

## Done

After Task 3:

- `AuthError` carries `provider` field, matching `RateLimitError`.
- `_maybe_wrap_error` catches auth patterns in LiteLLM exceptions.
- `AgentCLIClient` raises `AuthError` on auth-expired stderr.
- 11 new tests pinning the failure-mode contract.
- Operator catalogue doc landed.
