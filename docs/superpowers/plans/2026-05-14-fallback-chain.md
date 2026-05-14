# Per-provider fallback chain — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** `providers_per_stage` accepts a list of providers; client
tries each on failure.

**Spec:** `docs/superpowers/specs/2026-05-14-fallback-chain-design.md`

---

## Task 1: Widen the schema

**Files:**
- Modify: `src/perspicacite/config/schema.py` (the `providers_per_stage` field)
- Test: `tests/unit/test_fallback_chain_schema.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fallback_chain_schema.py
"""Verify providers_per_stage accepts both str and list[str] (Wave 3.2)."""
from perspicacite.config.schema import LLMConfig


def test_single_string_per_stage():
    cfg = LLMConfig(providers_per_stage={"routing": "anthropic"})
    assert cfg.providers_per_stage["routing"] == "anthropic"


def test_list_per_stage():
    cfg = LLMConfig(providers_per_stage={
        "synthesis_heavy": ["anthropic", "claude_cli", "deepseek"],
    })
    assert cfg.providers_per_stage["synthesis_heavy"] == [
        "anthropic", "claude_cli", "deepseek"
    ]


def test_mixed_per_stage():
    cfg = LLMConfig(providers_per_stage={
        "routing": "anthropic",
        "synthesis_heavy": ["anthropic", "claude_cli"],
    })
    assert isinstance(cfg.providers_per_stage["routing"], str)
    assert isinstance(cfg.providers_per_stage["synthesis_heavy"], list)
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_fallback_chain_schema.py -v
```

The list-form test will fail with a pydantic validation error
(field type is `dict[str, str]`).

- [ ] **Step 3: Widen the field type**

In `src/perspicacite/config/schema.py`, find the `providers_per_stage`
field on `LLMConfig` (around line 328). Replace its type annotation:

```python
    providers_per_stage: dict[str, str | list[str]] = Field(
        default_factory=dict,
        description=(
            "Per-stage provider override. Value may be a single "
            "provider string (today's behaviour) or a list of "
            "providers — the client tries each in order on failure "
            "(see fallback-chain spec Wave 3.2). "
            "Same keys as `models`. Falls back to `default_provider`."
        ),
    )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_fallback_chain_schema.py -v
pytest tests/integration/test_config_audit.py -v   # no regression
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_fallback_chain_schema.py
git commit -m "feat(config): providers_per_stage accepts list for fallback (Wave 3.2)"
```

---

## Task 2: resolve_stage_chain helper

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Test: `tests/unit/test_fallback_chain_resolution.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fallback_chain_resolution.py
"""resolve_stage_chain returns [(provider, model)] in fallback order (Wave 3.2)."""
from perspicacite.config.schema import LLMConfig
from perspicacite.llm.client import resolve_stage_chain


def _wrap(llm_cfg: LLMConfig):
    """Helper: resolve_stage_chain expects the outer config object."""
    class _C:
        pass
    c = _C()
    c.llm = llm_cfg
    return c


def test_single_string_returns_one_entry():
    cfg = _wrap(LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        providers_per_stage={"routing": "anthropic"},
        models={"routing": "claude-haiku-4-5"},
    ))
    chain = resolve_stage_chain(cfg, "routing")
    assert chain == [("anthropic", "claude-haiku-4-5")]


def test_list_returns_multi_element_chain():
    cfg = _wrap(LLMConfig(
        default_provider="anthropic",
        default_model="claude-sonnet-4-5",
        providers_per_stage={
            "synthesis_heavy": ["anthropic", "claude_cli", "deepseek"],
        },
        models={"synthesis_heavy": "claude-sonnet-4-5"},
    ))
    chain = resolve_stage_chain(cfg, "synthesis_heavy")
    assert chain == [
        ("anthropic", "claude-sonnet-4-5"),
        ("claude_cli", "claude-sonnet-4-5"),
        ("deepseek", "claude-sonnet-4-5"),
    ]


def test_missing_stage_falls_back_to_defaults():
    cfg = _wrap(LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
    ))
    chain = resolve_stage_chain(cfg, "unknown-stage")
    assert chain == [("anthropic", "claude-haiku-4-5")]


def test_chain_uses_default_model_when_stage_model_missing():
    """providers_per_stage list set, but no entry in models[stage] →
    each chain entry uses default_model."""
    cfg = _wrap(LLMConfig(
        default_provider="anthropic",
        default_model="claude-sonnet-4-5",
        providers_per_stage={"synthesis_heavy": ["anthropic", "claude_cli"]},
    ))
    chain = resolve_stage_chain(cfg, "synthesis_heavy")
    assert chain == [
        ("anthropic", "claude-sonnet-4-5"),
        ("claude_cli", "claude-sonnet-4-5"),
    ]


def test_none_config_returns_safe_default():
    chain = resolve_stage_chain(None, "anything")
    assert chain == [("anthropic", "claude-haiku-4-5")]
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_fallback_chain_resolution.py -v
```

- [ ] **Step 3: Implement `resolve_stage_chain`**

In `src/perspicacite/llm/client.py`, just below the existing
`resolve_stage_model` function (around line 51), add:

```python
def resolve_stage_chain(
    config: Any,
    stage: str,
) -> list[tuple[str, str]]:
    """Return the ordered ``[(provider, model), ...]`` fallback chain
    for a stage.

    ``providers_per_stage[stage]`` may be a single provider string
    (single-element chain) or a list of providers (multi-element).
    Missing stages produce a one-element chain from
    ``(default_provider, default_model)``.

    The same model is used for every chain entry (per-provider model
    overrides are a documented Wave 3.2 followup). Agent-CLI providers
    apply their own model_aliases internally, so a list like
    ``["anthropic", "claude_cli"]`` with model ``"claude-sonnet-4-5"``
    works out of the box.

    See ``docs/superpowers/specs/2026-05-14-fallback-chain-design.md``.
    """
    if config is None:
        return [("anthropic", "claude-haiku-4-5")]
    llm_cfg = getattr(config, "llm", None)
    if llm_cfg is None:
        return [("anthropic", "claude-haiku-4-5")]
    default_provider = llm_cfg.default_provider or "anthropic"
    default_model = llm_cfg.default_model or "claude-sonnet-4-5"
    models = getattr(llm_cfg, "models", {}) or {}
    providers = getattr(llm_cfg, "providers_per_stage", {}) or {}

    model = models.get(stage, default_model)
    pinned = providers.get(stage, default_provider)
    if isinstance(pinned, str):
        return [(pinned, model)]
    # list[str]
    return [(p, model) for p in pinned]
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_fallback_chain_resolution.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/client.py tests/unit/test_fallback_chain_resolution.py
git commit -m "feat(llm): resolve_stage_chain — string-or-list provider resolution (Wave 3.2)"
```

---

## Task 3: complete_with_chain method

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Test: `tests/unit/test_fallback_chain_dispatch.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_fallback_chain_dispatch.py
"""Verify complete_with_chain advances on failure (Wave 3.2)."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.budget import (
    BudgetExceededError, BudgetTracker, set_budget_tracker,
)
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.errors import RateLimitError


def _client(tmp_path: Path) -> AsyncLLMClient:
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    return AsyncLLMClient(cfg)


@pytest.mark.asyncio
async def test_chain_returns_first_success(tmp_path):
    client = _client(tmp_path)
    fake = AsyncMock(return_value="success!")
    with patch.object(client, "complete", new=fake):
        out = await client.complete_with_chain(
            messages=[{"role": "user", "content": "hi"}],
            chain=[("anthropic", "claude-haiku-4-5"),
                   ("claude_cli", "sonnet")],
        )
    assert out == "success!"
    # Only the first provider was called.
    assert fake.call_count == 1


@pytest.mark.asyncio
async def test_chain_falls_through_on_rate_limit(tmp_path):
    client = _client(tmp_path)

    calls = {"n": 0}

    async def fake_complete(messages, model=None, provider=None, **kw):
        calls["n"] += 1
        if provider == "anthropic":
            raise RateLimitError("anthropic limited", provider="anthropic")
        return f"from-{provider}"

    with patch.object(client, "complete", new=fake_complete):
        out = await client.complete_with_chain(
            messages=[{"role": "user", "content": "x"}],
            chain=[("anthropic", "claude-haiku-4-5"),
                   ("claude_cli", "sonnet")],
        )
    assert out == "from-claude_cli"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_chain_falls_through_on_generic_exception(tmp_path):
    client = _client(tmp_path)

    async def fake_complete(messages, model=None, provider=None, **kw):
        if provider == "anthropic":
            raise RuntimeError("transient network error")
        return "ok"

    with patch.object(client, "complete", new=fake_complete):
        out = await client.complete_with_chain(
            messages=[{"role": "user", "content": "x"}],
            chain=[("anthropic", "claude-haiku-4-5"),
                   ("deepseek", "deepseek-chat")],
        )
    assert out == "ok"


@pytest.mark.asyncio
async def test_chain_raises_last_exception_when_all_fail(tmp_path):
    client = _client(tmp_path)

    async def fake_complete(messages, model=None, provider=None, **kw):
        raise RateLimitError(f"{provider} limited", provider=provider)

    with patch.object(client, "complete", new=fake_complete):
        with pytest.raises(RateLimitError) as exc:
            await client.complete_with_chain(
                messages=[{"role": "user", "content": "x"}],
                chain=[("anthropic", "claude-haiku-4-5"),
                       ("claude_cli", "sonnet")],
            )
    # The last exception raised should be from the final provider.
    assert exc.value.provider == "claude_cli"


@pytest.mark.asyncio
async def test_chain_short_circuits_on_budget_exceeded(tmp_path):
    client = _client(tmp_path)

    async def fake_complete(messages, model=None, provider=None, **kw):
        raise BudgetExceededError("over budget")

    with patch.object(client, "complete", new=fake_complete):
        with pytest.raises(BudgetExceededError):
            await client.complete_with_chain(
                messages=[{"role": "user", "content": "x"}],
                chain=[("anthropic", "claude-haiku-4-5"),
                       ("claude_cli", "sonnet")],
            )


@pytest.mark.asyncio
async def test_empty_chain_raises_value_error(tmp_path):
    client = _client(tmp_path)
    with pytest.raises(ValueError):
        await client.complete_with_chain(
            messages=[{"role": "user", "content": "x"}],
            chain=[],
        )
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_fallback_chain_dispatch.py -v
```

Expected: `AttributeError: 'AsyncLLMClient' object has no attribute 'complete_with_chain'`.

- [ ] **Step 3: Implement `complete_with_chain`**

In `src/perspicacite/llm/client.py`, add a new method on
`AsyncLLMClient` (after `complete_with_fallback` at the bottom of the
class):

```python
    async def complete_with_chain(
        self,
        messages: list[dict[str, Any]],
        chain: list[tuple[str, str]],
        **kwargs: Any,
    ) -> str:
        """Try each ``(provider, model)`` in order. Returns the first
        success. On :class:`RateLimitError` or other ``Exception`` (but
        not :class:`BudgetExceededError`), logs and tries the next.
        Raises the last exception when all fail.

        See docs/superpowers/specs/2026-05-14-fallback-chain-design.md.
        """
        if not chain:
            raise ValueError("complete_with_chain requires a non-empty chain")

        from perspicacite.llm.budget import BudgetExceededError

        last_exc: Exception | None = None
        for i, (provider, model) in enumerate(chain):
            try:
                return await self.complete(
                    messages=messages,
                    model=model,
                    provider=provider,
                    **kwargs,
                )
            except BudgetExceededError:
                # Switching providers won't help a budget breach.
                raise
            except Exception as e:
                last_exc = e
                logger.warning(
                    "llm_chain_step_failed",
                    attempt=i + 1,
                    chain_length=len(chain),
                    provider=provider,
                    model=model,
                    error=str(e),
                    error_type=type(e).__name__,
                )
        # All steps failed.
        assert last_exc is not None  # chain non-empty → at least one attempt
        raise last_exc
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_fallback_chain_dispatch.py -v
```

Expected: 6 PASSED.

Also re-run the broader suite to make sure nothing regressed:

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/client.py tests/unit/test_fallback_chain_dispatch.py
git commit -m "feat(llm): complete_with_chain — multi-provider fallback dispatch (Wave 3.2)"
```

---

## Task 4: Docs

**Files:**
- Create: `docs/fallback-chain-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
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
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/fallback-chain-*.md` to `.gitignore` after
`!docs/rate-limit-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/fallback-chain-2026-05-14.md .gitignore
git commit -m "docs(fallback-chain): operator guide (Wave 3.2)"
```

---

## Done

After Task 4:

- `providers_per_stage` accepts `str | list[str]`.
- `resolve_stage_chain(config, stage) → list[(provider, model)]`.
- `AsyncLLMClient.complete_with_chain(messages, chain)` dispatches
  with rate-limit-aware fallback.
- 14 new tests, all passing.
- Operator doc landed.
- Orchestrator migration is a follow-up.
