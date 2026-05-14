# Rate-limit detection — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Detect rate-limit signals from every LLM path and raise
`RateLimitError` with structured context instead of cryptic messages.

**Spec:** `docs/superpowers/specs/2026-05-14-rate-limit-detection-design.md`

---

## Task 1: errors.py module — exception hierarchy + detection

**Files:**
- Create: `src/perspicacite/llm/errors.py`
- Test: `tests/unit/test_rate_limit_detection.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_rate_limit_detection.py
"""Pattern detection + structured-exception tests for Wave 3.1."""
import pytest

from perspicacite.llm.errors import (
    LLMError,
    RateLimitError,
    detect_rate_limit,
    suggested_action,
)


def test_claude_code_rate_limit_with_minutes():
    text = "Rate limit reached. Try again in 1h 23m."
    hit = detect_rate_limit(text)
    assert hit is not None
    assert hit.retry_after_seconds == 1 * 3600 + 23 * 60


def test_claude_code_rate_limit_minutes_only():
    text = "Rate limit reached. Try again in 47m."
    hit = detect_rate_limit(text)
    assert hit is not None
    assert hit.retry_after_seconds == 47 * 60


def test_claude_code_usage_limit_no_minutes():
    text = "Usage limit exceeded. Resets at 5pm."
    hit = detect_rate_limit(text)
    assert hit is not None
    assert hit.retry_after_seconds is None


def test_codex_429():
    text = "Error: HTTP 429 Too Many Requests"
    assert detect_rate_limit(text) is not None


def test_generic_too_many_requests():
    text = "API responded: Too Many Requests"
    assert detect_rate_limit(text) is not None


def test_non_matching_returns_none():
    assert detect_rate_limit("Some unrelated error") is None
    assert detect_rate_limit("") is None


def test_suggested_action_anthropic_mentions_fallback():
    msg = suggested_action("anthropic")
    assert "fallback" in msg.lower() or "providers_per_stage" in msg or "fallback" in msg.lower() \
        or "route" in msg.lower()


def test_suggested_action_claude_cli_mentions_direct_api():
    msg = suggested_action("claude_cli")
    assert "anthropic" in msg.lower() or "direct" in msg.lower()


def test_suggested_action_default_for_unknown_provider():
    msg = suggested_action("totally-made-up-provider")
    assert isinstance(msg, str)
    assert len(msg) > 0


def test_rate_limit_error_is_llm_error():
    err = RateLimitError("test", provider="anthropic")
    assert isinstance(err, LLMError)
    assert err.provider == "anthropic"
    assert err.retry_after_seconds is None
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_rate_limit_detection.py -v
```

- [ ] **Step 3: Implement `errors.py`**

Create `src/perspicacite/llm/errors.py`:

```python
"""LLM error hierarchy + rate-limit detection helpers.

See docs/superpowers/specs/2026-05-14-rate-limit-detection-design.md.

The patterns below are intentionally permissive — false positives
(treating a non-rate-limit error as a rate limit) are mild because
the tenacity retry chain will retry either way. The exception type
just changes the message the orchestrator surfaces and which
provider Wave 3.2's fallback chain will skip next.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class LLMError(RuntimeError):
    """Base class for Perspicacité LLM errors."""


@dataclass
class _RateLimitHit:
    retry_after_seconds: int | None


class RateLimitError(LLMError):
    """Provider declined the call due to rate / quota limits."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        retry_after_seconds: int | None = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds


class TimeoutError(LLMError):
    """The LLM call timed out (subprocess or API)."""


class AuthError(LLMError):
    """Provider auth failed (401, missing creds, etc.)."""


# (compiled pattern, retry_seconds_extractor). Extractors return None
# when no usable retry hint is available. First match wins.
_RATE_LIMIT_PATTERNS: list[tuple[re.Pattern[str], "callable"]] = [
    # Claude Code: "Rate limit reached. Try again in 1h 23m."
    (
        re.compile(r"rate\s*limit\s*reached.*?try\s*again\s*in\s*"
                   r"(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?",
                   re.IGNORECASE | re.DOTALL),
        lambda m: (
            (int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60)
            or None
        ),
    ),
    # Claude Code: "Usage limit exceeded"
    (
        re.compile(r"usage\s*limit\s*exceeded", re.IGNORECASE),
        lambda m: None,
    ),
    # HTTP 429 from anywhere
    (
        re.compile(r"\b429\b|too\s*many\s*requests", re.IGNORECASE),
        lambda m: None,
    ),
    # Generic "rate limit"
    (
        re.compile(r"\brate.?limit", re.IGNORECASE),
        lambda m: None,
    ),
    # Codex / OpenAI: "quota exceeded"
    (
        re.compile(r"quota\s*(exceeded|exhausted)", re.IGNORECASE),
        lambda m: None,
    ),
]


def detect_rate_limit(text: str) -> _RateLimitHit | None:
    """Return a hit (with optional retry hint) if ``text`` matches any
    known rate-limit pattern. ``None`` otherwise."""
    if not text:
        return None
    for pattern, extractor in _RATE_LIMIT_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                seconds = extractor(m)
            except Exception:
                seconds = None
            return _RateLimitHit(retry_after_seconds=seconds)
    return None


_SUGGESTED_ACTIONS: dict[str, str] = {
    "anthropic": (
        "Wait for the Anthropic quota reset, or route this stage through "
        "a fallback provider via `llm.providers_per_stage` (DeepSeek / "
        "OpenAI / Gemini)."
    ),
    "openai": (
        "Wait for the OpenAI quota reset, or route through Anthropic / "
        "DeepSeek / Gemini via `llm.providers_per_stage`."
    ),
    "deepseek": (
        "DeepSeek rate-limited. Wait for reset or fall back to another "
        "provider via `llm.providers_per_stage`."
    ),
    "gemini": (
        "Gemini rate-limited. Wait for reset or fall back via "
        "`llm.providers_per_stage`."
    ),
    "claude_cli": (
        "Your Claude Pro/Max subscription is rate-limited. Switch to the "
        "direct Anthropic API (`providers_per_stage` → `anthropic`) or "
        "wait for the quota reset."
    ),
    "agent_cli": (
        "The agent CLI's subscription is rate-limited. Wait for reset "
        "or fall back to another agent_cli / direct API."
    ),
    "ollama": (
        "Local Ollama returned a transient error. Check the server logs "
        "(`localhost:11434`)."
    ),
}


def suggested_action(provider: str) -> str:
    """Return a human-readable next-step message for a rate-limited call."""
    return _SUGGESTED_ACTIONS.get(
        provider,
        "Wait for the quota reset or configure a fallback provider via "
        "`llm.providers_per_stage`.",
    )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_rate_limit_detection.py -v
```

Expected: 10 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/errors.py tests/unit/test_rate_limit_detection.py
git commit -m "feat(llm-errors): exception hierarchy + rate-limit pattern detection (Wave 3.1)"
```

---

## Task 2: Agent-CLI integration

**Files:**
- Modify: `src/perspicacite/llm/agent_cli.py`
- Test: `tests/unit/test_agent_cli_rate_limit.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_agent_cli_rate_limit.py
"""Verify AgentCLIClient surfaces RateLimitError on rate-limit stderr (Wave 3.1)."""
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.llm.agent_cli import AgentCLIClient
from perspicacite.llm.errors import RateLimitError


def _make_proc_mock(returncode: int, stdout: bytes, stderr: bytes):
    """Return a coroutine that mimics asyncio.create_subprocess_exec
    enough for AgentCLIClient.complete."""

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
async def test_rate_limit_stderr_raises_rate_limit_error():
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="claude_cli",
        output_format="text",
    )

    factory = _make_proc_mock(
        returncode=1,
        stdout=b"",
        stderr=b"Rate limit reached. Try again in 47m.",
    )

    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RateLimitError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])

    assert exc.value.provider == "claude_cli"
    assert exc.value.retry_after_seconds == 47 * 60


@pytest.mark.asyncio
async def test_non_rate_limit_failure_raises_runtime_error():
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="claude_cli",
        output_format="text",
    )

    factory = _make_proc_mock(
        returncode=1,
        stdout=b"",
        stderr=b"Some other unrelated error",
    )

    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RuntimeError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])

    # Should NOT be a RateLimitError.
    assert not isinstance(exc.value, RateLimitError)
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Wire detection into agent_cli**

In `src/perspicacite/llm/agent_cli.py`, find the block that handles
non-zero return code (around line 295-307). Replace:

```python
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            if out_path:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
            raise RuntimeError(
                f"{self.provider_label}: CLI exited {proc.returncode}: {err}"
            )
```

with:

```python
        if proc.returncode != 0:
            err_full = (stderr or b"").decode("utf-8", errors="replace")
            out_str = (stdout or b"").decode("utf-8", errors="replace")
            err = err_full[:500]
            if out_path:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
            # Detect rate-limit signals — raise structured error so the
            # orchestrator / Wave 3.2 fallback chain can react.
            from perspicacite.llm.errors import (
                RateLimitError, detect_rate_limit, suggested_action,
            )
            hit = detect_rate_limit(err_full) or detect_rate_limit(out_str)
            if hit is not None:
                raise RateLimitError(
                    f"{self.provider_label}: rate limit. "
                    f"{suggested_action(self.provider_label)}",
                    provider=self.provider_label,
                    retry_after_seconds=hit.retry_after_seconds,
                )
            raise RuntimeError(
                f"{self.provider_label}: CLI exited {proc.returncode}: {err}"
            )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_agent_cli_rate_limit.py -v
pytest tests/unit/test_agent_cli_usage_parsing.py -v  # no regression
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/agent_cli.py tests/unit/test_agent_cli_rate_limit.py
git commit -m "feat(agent-cli): raise RateLimitError on rate-limit stderr (Wave 3.1)"
```

---

## Task 3: LiteLLM integration

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Test: `tests/unit/test_litellm_rate_limit_wrap.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_litellm_rate_limit_wrap.py
"""Verify LiteLLM rate-limit exceptions are re-raised as our type (Wave 3.1)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.errors import RateLimitError


@pytest.mark.asyncio
async def test_litellm_rate_limit_exception_wrapped(tmp_path):
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    client = AsyncLLMClient(cfg)

    # Build a fake litellm.exceptions.RateLimitError-like exception.
    class FakeRateLimit(Exception):
        pass
    FakeRateLimit.__name__ = "RateLimitError"
    FakeRateLimit.__module__ = "litellm.exceptions"

    async def boom(*args, **kwargs):
        raise FakeRateLimit("rate limit reached")

    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock()
        litellm.acompletion = boom
        mock_get.return_value = litellm
        with pytest.raises(RateLimitError) as exc:
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                cache=False,
            )
    assert exc.value.provider == "anthropic"
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Wrap the LiteLLM call**

In `src/perspicacite/llm/client.py`, the existing tenacity retry
wraps `complete()`. We need to catch rate-limit exceptions inside
the standard branch's `try/except` (around the `response = await
litellm.acompletion(**completion_kwargs)` call, ~line 415) and
re-raise as our `RateLimitError`.

A clean way: add a small helper at the top of the file:

```python
def _maybe_rate_limit(exc: Exception, provider: str) -> Exception:
    """If ``exc`` is a LiteLLM rate-limit exception (or its message
    matches our rate-limit patterns), return a fresh
    :class:`RateLimitError`. Otherwise return ``exc`` unchanged."""
    # Class-name match: covers litellm.exceptions.RateLimitError and
    # subclasses without importing litellm here (it's a lazy import).
    cls_name = type(exc).__name__
    msg = str(exc)
    if cls_name == "RateLimitError" or cls_name.endswith(".RateLimitError"):
        from perspicacite.llm.errors import RateLimitError, suggested_action
        return RateLimitError(
            f"{provider}: rate limit. {suggested_action(provider)}",
            provider=provider,
        )
    from perspicacite.llm.errors import detect_rate_limit, RateLimitError, suggested_action
    hit = detect_rate_limit(msg)
    if hit is not None:
        return RateLimitError(
            f"{provider}: rate limit. {suggested_action(provider)}",
            provider=provider,
            retry_after_seconds=hit.retry_after_seconds,
        )
    return exc
```

Then in the standard `try/except` block around the litellm call,
change:

```python
        except Exception as e:
            logger.error(...)
            raise
```

to:

```python
        except Exception as e:
            logger.error(...)
            raise _maybe_rate_limit(e, provider) from e
```

Do the same wrap inside the Minimax branch's try block (same
function). Two call sites total in `complete()`.

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_litellm_rate_limit_wrap.py -v
```

Also re-run cache integration tests so we didn't break anything:

```bash
pytest tests/unit/test_llm_client_cache_integration.py -v
pytest tests/unit/test_budget_client_integration.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/client.py tests/unit/test_litellm_rate_limit_wrap.py
git commit -m "feat(llm-client): wrap LiteLLM rate-limit exceptions as RateLimitError (Wave 3.1)"
```

---

## Task 4: Operator doc

**Files:**
- Create: `docs/rate-limit-handling-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
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
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/rate-limit-*.md` to `.gitignore` after `!docs/budget-caps-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/rate-limit-handling-2026-05-14.md .gitignore
git commit -m "docs(rate-limit): operator guide (Wave 3.1)"
```

---

## Done

After Task 4:

- New `errors.py` module (~120 LoC).
- `AgentCLIClient` raises `RateLimitError` on rate-limit stderr.
- `AsyncLLMClient.complete()` wraps LiteLLM rate-limit exceptions.
- 12 new tests, all passing.
- Operator doc landed.
- Foundation for Wave 3.2 fallback chain.
