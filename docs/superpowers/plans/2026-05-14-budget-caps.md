# Budget caps — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Hard token / dollar budget per process; cleanly aborts when
crossed.

**Architecture:** `BudgetTracker` in a `ContextVar`, consulted by
`AsyncLLMClient.complete()` around the dispatch path. Static pricing
table with user overrides.

**Spec:** `docs/superpowers/specs/2026-05-14-budget-caps-design.md`

---

## Task 1: BudgetTracker module + pricing

**Files:**
- Create: `src/perspicacite/llm/budget.py`
- Test: `tests/unit/test_budget_tracker.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_budget_tracker.py
"""Tests for BudgetTracker, pricing lookup, and contextvar accessors (Wave 2.4)."""
import pytest

from perspicacite.llm.budget import (
    BudgetExceededError,
    BudgetTracker,
    get_budget_tracker,
    lookup_pricing,
    set_budget_tracker,
)


def test_empty_tracker_zero_totals():
    t = BudgetTracker()
    s = t.summary()
    assert s["tokens_in"] == 0
    assert s["tokens_out"] == 0
    assert s["usd"] == 0.0
    assert s["has_unknown_costs"] is False
    assert s["breaches"] == []


def test_record_accumulates():
    t = BudgetTracker()
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=1000, output_tokens=500)
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=2000, output_tokens=300)
    s = t.summary()
    assert s["tokens_in"] == 3000
    assert s["tokens_out"] == 800
    # haiku: $0.80 in / M, $4.00 out / M
    expected = (3000 / 1e6) * 0.80 + (800 / 1e6) * 4.00
    assert s["usd"] == pytest.approx(expected, rel=1e-6)


def test_token_cap_raises_in_abort_mode():
    t = BudgetTracker(max_input_tokens=1500, action="abort")
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=1000, output_tokens=0)
    with pytest.raises(BudgetExceededError) as exc:
        t.record(provider="anthropic", model="claude-haiku-4-5",
                 input_tokens=1000, output_tokens=0)
    assert "input_tokens" in str(exc.value)


def test_token_cap_warns_in_warn_mode(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    t = BudgetTracker(max_output_tokens=100, action="warn")
    # The breach should log but not raise.
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=0, output_tokens=500)
    # Subsequent calls still proceed.
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=0, output_tokens=10)
    s = t.summary()
    assert s["tokens_out"] == 510


def test_check_raises_before_call():
    t = BudgetTracker(max_input_tokens=10, action="abort")
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=20, output_tokens=0)  # already over
    # check() called before next call should raise without needing record.
    # Actually record() raises first — verify check() also catches it.
    with pytest.raises(BudgetExceededError):
        t.check()


def test_usd_cap_raises():
    t = BudgetTracker(max_usd=0.01, action="abort")
    # haiku output is $4 / M tokens → 3000 out = $0.012 = breach.
    with pytest.raises(BudgetExceededError) as exc:
        t.record(provider="anthropic", model="claude-haiku-4-5",
                 input_tokens=0, output_tokens=3000)
    assert "usd" in str(exc.value)


def test_unknown_pair_flagged():
    t = BudgetTracker()
    t.record(provider="weird-provider", model="weird-model",
             input_tokens=100, output_tokens=50)
    s = t.summary()
    assert s["has_unknown_costs"] is True
    assert s["tokens_in"] == 100
    # No dollar contribution from unknown pair.
    assert s["usd"] == 0.0


def test_wildcard_provider_matches():
    """claude_cli / agent_cli / ollama price at $0 for any model."""
    t = BudgetTracker()
    t.record(provider="claude_cli", model="sonnet",
             input_tokens=10000, output_tokens=5000)
    s = t.summary()
    assert s["usd"] == 0.0
    assert s["has_unknown_costs"] is False
    assert s["tokens_in"] == 10000


def test_override_pricing_wins():
    t = BudgetTracker(
        pricing_overrides={"anthropic": {"claude-haiku-4-5": (10.0, 20.0)}},
    )
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=1_000_000, output_tokens=0)
    s = t.summary()
    assert s["usd"] == pytest.approx(10.0, rel=1e-6)


def test_lookup_pricing_returns_none_for_unknown():
    assert lookup_pricing("weird", "weird") == (None, None)


def test_lookup_pricing_returns_floats_for_known():
    in_p, out_p = lookup_pricing("anthropic", "claude-haiku-4-5")
    assert in_p == 0.80
    assert out_p == 4.00


def test_contextvar_set_and_get():
    """Round-trip a tracker through the contextvar."""
    assert get_budget_tracker() is None
    t = BudgetTracker()
    token = set_budget_tracker(t)
    try:
        assert get_budget_tracker() is t
    finally:
        # Reset for test isolation.
        import perspicacite.llm.budget as _b
        _b._tracker.reset(token)
    assert get_budget_tracker() is None
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_budget_tracker.py -v
```

- [ ] **Step 3: Implement `budget.py`**

Create `src/perspicacite/llm/budget.py`:

```python
"""Per-process LLM budget tracking with optional caps.

See docs/superpowers/specs/2026-05-14-budget-caps-design.md.

The tracker lives in a ``ContextVar`` so concurrent MCP requests on
the same server process get independent budgets. Call sites consult
``get_budget_tracker()`` and skip the check entirely when it returns
``None`` — preserving today's behaviour for users who don't enable
budgets.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Literal

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.llm.budget")


class BudgetExceededError(RuntimeError):
    """Raised when a budget cap would be (or has been) breached."""


# (provider, model) -> ($/M input, $/M output). The model "*" matches
# any model under that provider, with lower priority than an exact
# match. Subscription / local providers price at zero.
PRICING_TABLE: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-haiku-4-5"):  (0.80, 4.00),
    ("anthropic", "claude-sonnet-4-5"): (3.00, 15.00),
    ("anthropic", "claude-opus-4"):     (15.00, 75.00),
    ("openai", "gpt-4o-mini"):           (0.15, 0.60),
    ("openai", "gpt-4o"):                (2.50, 10.00),
    ("openai", "gpt-5"):                 (2.50, 10.00),  # placeholder
    ("openai", "gpt-5.5"):               (2.50, 10.00),  # placeholder
    ("deepseek", "deepseek-chat"):       (0.27, 1.10),
    ("gemini", "gemini-1.5-flash"):      (0.075, 0.30),
    ("gemini", "gemini-1.5-pro"):        (1.25, 5.00),
    ("claude_cli", "*"): (0.0, 0.0),
    ("agent_cli",  "*"): (0.0, 0.0),
    ("ollama",     "*"): (0.0, 0.0),
}


def lookup_pricing(
    provider: str,
    model: str,
    overrides: dict[str, dict[str, tuple[float, float]]] | None = None,
) -> tuple[float | None, float | None]:
    """Return ``($/M input, $/M output)`` or ``(None, None)`` if unknown.

    Lookup order:
    1. ``overrides[provider][model]`` (exact)
    2. ``PRICING_TABLE[(provider, model)]``
    3. ``PRICING_TABLE[(provider, "*")]``
    4. ``(None, None)``
    """
    if overrides:
        prov_over = overrides.get(provider)
        if prov_over and model in prov_over:
            return prov_over[model]
    if (provider, model) in PRICING_TABLE:
        return PRICING_TABLE[(provider, model)]
    if (provider, "*") in PRICING_TABLE:
        return PRICING_TABLE[(provider, "*")]
    return (None, None)


@dataclass
class BudgetTracker:
    """Accumulates token / dollar spend across all LLM calls in a run.

    All caps default to ``None`` (no limit). Pass any combination of
    ``max_input_tokens``, ``max_output_tokens``, ``max_usd``.

    ``action="abort"`` raises :class:`BudgetExceededError` immediately
    on breach (default). ``action="warn"`` logs but allows the call.
    """

    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_usd: float | None = None
    action: Literal["abort", "warn"] = "abort"
    pricing_overrides: dict[str, dict[str, tuple[float, float]]] = field(
        default_factory=dict,
    )

    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    has_unknown_costs: bool = False
    _warned_breaches: set[str] = field(default_factory=set)
    breaches: list[str] = field(default_factory=list)

    # ---- core API ------------------------------------------------------

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.tokens_in += int(input_tokens or 0)
        self.tokens_out += int(output_tokens or 0)

        in_price, out_price = lookup_pricing(provider, model, self.pricing_overrides)
        if in_price is None or out_price is None:
            self.has_unknown_costs = True
        else:
            self.usd += (input_tokens / 1e6) * in_price
            self.usd += (output_tokens / 1e6) * out_price

        self._enforce()

    def check(self) -> None:
        """Raise if any cap is already breached. Idempotent."""
        self._enforce(checking=True)

    def summary(self) -> dict:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usd": round(self.usd, 6),
            "has_unknown_costs": self.has_unknown_costs,
            "breaches": list(self.breaches),
        }

    # ---- internals -----------------------------------------------------

    def _enforce(self, *, checking: bool = False) -> None:
        breaches: list[tuple[str, str]] = []
        if self.max_input_tokens is not None and self.tokens_in > self.max_input_tokens:
            breaches.append(("input_tokens",
                f"input_tokens={self.tokens_in} > cap={self.max_input_tokens}"))
        if self.max_output_tokens is not None and self.tokens_out > self.max_output_tokens:
            breaches.append(("output_tokens",
                f"output_tokens={self.tokens_out} > cap={self.max_output_tokens}"))
        if self.max_usd is not None and self.usd > self.max_usd:
            note = ""
            if self.has_unknown_costs:
                note = " (note: some calls had unknown pricing — usd is a lower bound)"
            breaches.append(("usd",
                f"usd=${self.usd:.4f} > cap=${self.max_usd:.2f}{note}"))

        if not breaches:
            return

        for kind, msg in breaches:
            if msg not in self.breaches:
                self.breaches.append(msg)
            if self.action == "warn":
                if kind not in self._warned_breaches:
                    logger.warning("budget_breach_warn", kind=kind, detail=msg)
                    self._warned_breaches.add(kind)
            else:
                logger.error("budget_breach_abort", kind=kind, detail=msg)

        if self.action == "abort":
            raise BudgetExceededError("; ".join(m for _, m in breaches))
        # warn mode: fall through, allow the caller to proceed.


# ---- contextvar accessors -------------------------------------------------

_tracker: contextvars.ContextVar[BudgetTracker | None] = contextvars.ContextVar(
    "perspicacite_budget_tracker", default=None,
)


def get_budget_tracker() -> BudgetTracker | None:
    return _tracker.get()


def set_budget_tracker(t: BudgetTracker | None) -> contextvars.Token:
    return _tracker.set(t)
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_budget_tracker.py -v
```

Expected: 12 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/budget.py tests/unit/test_budget_tracker.py
git commit -m "feat(budget): BudgetTracker + pricing table + contextvar accessors (Wave 2.4)"
```

---

## Task 2: Config — BudgetConfig + pricing_overrides

**Files:**
- Modify: `src/perspicacite/config/schema.py`
- Test: `tests/unit/test_budget_config.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_budget_config.py
"""Tests for the BudgetConfig nested model on LLMConfig (Wave 2.4)."""
from perspicacite.config.schema import LLMConfig


def test_budget_defaults_off():
    cfg = LLMConfig()
    assert cfg.budget.enabled is False
    assert cfg.budget.max_input_tokens is None
    assert cfg.budget.max_output_tokens is None
    assert cfg.budget.max_usd is None
    assert cfg.budget.action == "abort"


def test_budget_can_enable_with_caps():
    cfg = LLMConfig(budget={
        "enabled": True,
        "max_input_tokens": 1_000_000,
        "max_usd": 5.0,
        "action": "warn",
    })
    assert cfg.budget.enabled is True
    assert cfg.budget.max_input_tokens == 1_000_000
    assert cfg.budget.max_usd == 5.0
    assert cfg.budget.action == "warn"


def test_pricing_overrides_default_empty():
    cfg = LLMConfig()
    assert cfg.pricing_overrides == {}


def test_pricing_overrides_round_trip():
    cfg = LLMConfig(pricing_overrides={
        "anthropic": {"claude-haiku-4-5": [0.5, 2.0]},
    })
    # Should be coerced to tuples or remain as lists; either way the
    # values are accessible.
    haiku = cfg.pricing_overrides["anthropic"]["claude-haiku-4-5"]
    assert haiku[0] == 0.5
    assert haiku[1] == 2.0
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Add BudgetConfig + fields**

In `src/perspicacite/config/schema.py`, add a new model just before
`LLMConfig`:

```python
class BudgetConfig(BaseModel):
    """Per-process LLM spend caps (Wave 2.4).

    Default off — set ``enabled: true`` to activate. When enabled,
    any breach raises ``BudgetExceededError`` (under ``action='abort'``)
    or logs a warning (under ``action='warn'``).
    """

    enabled: bool = Field(default=False, description="Master on/off switch.")
    max_input_tokens: int | None = Field(
        default=None, description="Total input tokens across all calls. None = no cap.",
    )
    max_output_tokens: int | None = Field(
        default=None, description="Total output tokens. None = no cap.",
    )
    max_usd: float | None = Field(
        default=None, description="Estimated dollar spend. None = no cap.",
    )
    action: Literal["abort", "warn"] = Field(
        default="abort",
        description="'abort' raises BudgetExceededError; 'warn' logs and continues.",
    )
```

Then add two new fields on `LLMConfig` near the existing
`use_mcp_sampling`:

```python
    budget: BudgetConfig = Field(
        default_factory=BudgetConfig,
        description="Per-process token / dollar caps. See BudgetConfig.",
    )
    pricing_overrides: dict[str, dict[str, list[float] | tuple[float, float]]] = Field(
        default_factory=dict,
        description=(
            "Optional per-(provider, model) pricing overrides in $/M tokens "
            "as [input, output]. Falls through to the default PRICING_TABLE "
            "in perspicacite.llm.budget."
        ),
    )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_budget_config.py -v
pytest tests/integration/test_config_audit.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_budget_config.py
git commit -m "feat(config): BudgetConfig + pricing_overrides on LLMConfig (Wave 2.4)"
```

---

## Task 3: Wire tracker into AsyncLLMClient

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Test: `tests/unit/test_budget_client_integration.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_budget_client_integration.py
"""End-to-end: BudgetTracker accumulates from real complete() calls (Wave 2.4)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.budget import (
    BudgetExceededError, BudgetTracker, set_budget_tracker,
)
from perspicacite.llm.client import AsyncLLMClient


def _mk_config(tmp_path: Path) -> LLMConfig:
    return LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,  # avoid cache interaction
        cache_path=tmp_path / "no.db",
    )


def _resp(text: str, in_t: int, out_t: int):
    msg = MagicMock(); msg.content = text
    choice = MagicMock(); choice.message = msg
    r = MagicMock(); r.choices = [choice]
    r.get = MagicMock(side_effect=lambda k, d=None: {
        "usage": {"prompt_tokens": in_t, "completion_tokens": out_t}
    }.get(k, d))
    return r


@pytest.mark.asyncio
async def test_tracker_accumulates_from_complete_calls(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    tracker = BudgetTracker()
    token = set_budget_tracker(tracker)
    try:
        fake = AsyncMock(return_value=_resp("hi", 100, 50))
        with patch.object(client, "_get_litellm") as mock_get:
            litellm = MagicMock(); litellm.acompletion = fake
            mock_get.return_value = litellm
            await client.complete(messages=[{"role": "user", "content": "hi"}])
        s = tracker.summary()
        assert s["tokens_in"] == 100
        assert s["tokens_out"] == 50
        assert s["usd"] > 0  # haiku is priced
    finally:
        import perspicacite.llm.budget as _b
        _b._tracker.reset(token)


@pytest.mark.asyncio
async def test_tracker_breach_raises_mid_pipeline(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    tracker = BudgetTracker(max_input_tokens=150, action="abort")
    token = set_budget_tracker(tracker)
    try:
        fake = AsyncMock(return_value=_resp("ok", 100, 10))
        with patch.object(client, "_get_litellm") as mock_get:
            litellm = MagicMock(); litellm.acompletion = fake
            mock_get.return_value = litellm
            # First call: 100 in, under cap.
            await client.complete(messages=[{"role": "user", "content": "a"}])
            # Second call would push to 200 in, over cap.
            with pytest.raises(BudgetExceededError):
                await client.complete(messages=[{"role": "user", "content": "b"}])
    finally:
        import perspicacite.llm.budget as _b
        _b._tracker.reset(token)


@pytest.mark.asyncio
async def test_no_tracker_no_change(tmp_path):
    """Without a tracker installed, behaviour matches today."""
    client = AsyncLLMClient(_mk_config(tmp_path))
    fake = AsyncMock(return_value=_resp("ok", 1_000_000, 1_000_000))
    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock(); litellm.acompletion = fake
        mock_get.return_value = litellm
        # Should NOT raise even though token counts are huge.
        result = await client.complete(messages=[{"role": "user", "content": "a"}])
        assert result == "ok"
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Hook the tracker into `complete()`**

In `src/perspicacite/llm/client.py`, in the `complete` method,
just after the `cache_bypass = ...` block and **before** the
agent_cli / MCP-sampling / LiteLLM dispatch (after the cache lookup
returned None), add:

```python
        # ---- budget (Wave 2.4) ---------------------------------------
        from perspicacite.llm.budget import get_budget_tracker
        tracker = get_budget_tracker()
        if tracker is not None:
            tracker.check()
```

Then after **each** successful call's `add_llm_call(...)` block in
the same function (Minimax branch, standard branch, agent-CLI branch),
add a tracker `.record(...)` call. For the agent-CLI branch the
counts may be zero (Wave 2.3 covers Claude Code; Codex still 0/0)
but recording with 0/0 is harmless. Example for the standard branch:

```python
            if tracker is not None:
                tracker.record(
                    provider=provider, model=model,
                    input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    output_tokens=int(usage.get("completion_tokens", 0) or 0),
                )
```

For the agent-CLI branch, the `cli.complete(...)` call returns just
text; we don't have usage here. Skip the record for now (the agent
CLI itself records into the provenance collector via its own code path;
budget integration is a follow-up since we'd need to expose usage
from `AgentCLIClient.complete`). **Or**, refactor `AgentCLIClient` to
return `(text, in_tokens, out_tokens)` — out of scope for this task.
Add a TODO comment.

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_budget_client_integration.py -v
```

Also re-run the broader suite to make sure no regression:

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
git add src/perspicacite/llm/client.py tests/unit/test_budget_client_integration.py
git commit -m "feat(budget): tracker check+record around AsyncLLMClient dispatch (Wave 2.4)"
```

---

## Task 4: Operator doc

**Files:**
- Create: `docs/budget-caps-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
# Budget caps — status & operator guide (2026-05-14)

Wave 2.4 of the framework-hardening roadmap. Per-process token /
dollar spend caps with clean abort.

## Quick start

```yaml
llm:
  budget:
    enabled: true
    max_input_tokens:  500_000
    max_output_tokens: 250_000
    max_usd: 5.00
    action: "abort"        # or "warn"
```

When any cap is breached, the next `complete()` call raises
`BudgetExceededError` (under abort) or logs once and continues
(under warn). The orchestrator's existing error handlers surface a
descriptive message.

## What counts

- Every successful `AsyncLLMClient.complete()` call records its
  reported `prompt_tokens` / `completion_tokens` against the tracker.
- Cache hits (Wave 2.1) do NOT count — the provider was not actually
  called. The tracker reflects provider spend, not "tokens
  consumed".
- Agent-CLI calls (Claude Code / Codex / OpenClaw / Hermes) currently
  bypass the tracker — `AgentCLIClient` doesn't yet surface usage
  back through the wrapper. Wave 2.3 lit up usage parsing into
  provenance; followup work will plumb it into the budget tracker.

## Pricing

Built-in pricing table (in `src/perspicacite/llm/budget.py`):

| Provider | Model | $/M in | $/M out |
|---|---|---|---|
| anthropic | claude-haiku-4-5 | 0.80 | 4.00 |
| anthropic | claude-sonnet-4-5 | 3.00 | 15.00 |
| anthropic | claude-opus-4 | 15.00 | 75.00 |
| openai | gpt-4o-mini | 0.15 | 0.60 |
| openai | gpt-4o | 2.50 | 10.00 |
| deepseek | deepseek-chat | 0.27 | 1.10 |
| gemini | gemini-1.5-flash | 0.075 | 0.30 |
| gemini | gemini-1.5-pro | 1.25 | 5.00 |
| claude_cli / agent_cli / ollama | * | 0.00 | 0.00 |

Override per pair:

```yaml
llm:
  pricing_overrides:
    anthropic:
      claude-haiku-4-5: [0.80, 4.00]   # [in, out] in $/M tokens
```

Unknown `(provider, model)` pairs count tokens but contribute $0 to
the cost total. The tracker sets `has_unknown_costs=true` and
includes a note in the breach message. **Always set
`max_input_tokens` alongside `max_usd` when running on a custom
model — dollars-only caps don't catch unknown-cost paths.**

## API (for orchestrator)

```python
from perspicacite.llm.budget import (
    BudgetTracker, BudgetExceededError, set_budget_tracker,
)

tracker = BudgetTracker(max_usd=5.0, action="abort")
token = set_budget_tracker(tracker)
try:
    result = await rag.run(...)
except BudgetExceededError as e:
    logger.error("budget_breach: %s", e)
    return partial_result_with_warning(...)
finally:
    summary = tracker.summary()
    import perspicacite.llm.budget as _b
    _b._tracker.reset(token)
```

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/llm/budget.py` | `BudgetTracker`, `PRICING_TABLE`, contextvar |
| `src/perspicacite/llm/client.py` | `tracker.check()` + `tracker.record()` wiring |
| `src/perspicacite/config/schema.py` | `BudgetConfig` model + `pricing_overrides` |

## Followups

- Plumb agent-CLI usage into the budget tracker (depends on Wave 2.3
  expansion — Codex event-stream parsing).
- Per-stage caps (`max_usd_per_stage.synthesis_heavy: 0.5`).
- Cost breakdown in the MCP response payload.
- Auto-downshift when within 10% of `max_usd`.
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/budget-caps-*.md` to `.gitignore` after the existing
`!docs/embedding-cache-*.md` line.

- [ ] **Step 3: Commit**

```bash
git add docs/budget-caps-2026-05-14.md .gitignore
git commit -m "docs(budget): operator guide (Wave 2.4)"
```

---

## Done

After Task 4:

- New module `src/perspicacite/llm/budget.py` (~180 LoC).
- New `BudgetConfig` + `pricing_overrides` on `LLMConfig`.
- `AsyncLLMClient.complete()` consults the tracker around dispatch.
- 16 new tests, all passing.
- Operator doc landed.
- Agent-CLI integration is a documented follow-up (depends on
  finishing Wave 2.3 plumbing for Codex).
