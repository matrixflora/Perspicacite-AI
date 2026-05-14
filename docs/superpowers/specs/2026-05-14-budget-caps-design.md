# Budget caps — design spec

**Wave 2.4 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Hard ceiling on LLM token / dollar spend per process so a
runaway ingest can't burn through someone's API budget. Default off;
opt-in. Aborts cleanly with a descriptive error instead of N
consecutive subprocess / API failures.

## Architecture

A `BudgetTracker` accumulates per-call usage. It lives in a
`ContextVar` so concurrent runs in the same process (e.g., a MCP
server handling two requests) get independent budgets. The
`AsyncLLMClient.complete()` method consults the tracker before each
call (cheap fast check) and updates it after success.

```
complete()
  ├── tracker = get_budget_tracker()
  ├── tracker.check()                # raises BudgetExceededError if over
  ├── ... do the call ...
  └── tracker.record(provider, model, in, out)
```

When the tracker is absent (no `set_budget_tracker(...)` in the
current context), the check / record are no-ops — no behaviour change
for callers who don't enable budgets.

## Cost estimation

A small static pricing table maps `(provider, model)` → `($ per M
input, $ per M output)`. User-supplied overrides take precedence.

```python
PRICING_TABLE: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-haiku-4-5"):  (0.80, 4.00),
    ("anthropic", "claude-sonnet-4-5"): (3.00, 15.00),
    ("anthropic", "claude-opus-4"):     (15.00, 75.00),
    ("openai", "gpt-4o-mini"):           (0.15, 0.60),
    ("openai", "gpt-4o"):                (2.50, 10.00),
    ("deepseek", "deepseek-chat"):       (0.27, 1.10),
    ("gemini", "gemini-1.5-flash"):      (0.075, 0.30),
    ("gemini", "gemini-1.5-pro"):        (1.25, 5.00),
    # Agent-CLI providers cost $0 from the user's perspective — they
    # pay via subscription, not per-call. Recorded but priced at zero.
    ("claude_cli", "*"): (0.0, 0.0),
    ("agent_cli",  "*"): (0.0, 0.0),
    ("ollama",     "*"): (0.0, 0.0),
}
```

The wildcard `"*"` matches any model under that provider. Lookups
fall through: exact pair → wildcard-model under that provider →
`(None, None)`. Unknown pairs cost `None` (counted as "unknown" — see
below).

## Behaviour on unknown pricing

When neither exact nor wildcard match resolves, the call is recorded
but contributes `0.0` to the dollar total **and** sets the tracker's
`has_unknown_costs` flag. Cap-checks against `max_usd` log a warning
that some calls have unknown cost when the flag is set, so the user
isn't lulled into a false sense of security.

Token caps (`max_input_tokens`, `max_output_tokens`) work
independently of pricing — they always apply even when costs are
unknown.

## Cached calls

Cache hits (Wave 2.1) don't reach `complete()`'s tracker-record path
because they return before the dispatch branches. **Intentional**:
cached calls cost $0 from the provider's perspective. Token totals
displayed in reports should be "tokens we actually paid for" — cache
hits skipping is correct.

If a future report wants "tokens consumed including cached", the
provenance collector still has every record; sum from there.

## Config

```yaml
llm:
  budget:
    enabled: false              # default off — opt-in safety
    max_input_tokens: null      # int or null (no cap)
    max_output_tokens: null
    max_usd: null               # float or null
    action: "abort"             # "abort" raises, "warn" logs
  pricing_overrides:            # optional per-pair overrides
    anthropic:
      claude-haiku-4-5: [0.80, 4.00]
```

## API

```python
from perspicacite.llm.budget import (
    BudgetTracker, BudgetExceededError, set_budget_tracker, get_budget_tracker,
)

tracker = BudgetTracker(
    max_input_tokens=1_000_000,
    max_output_tokens=500_000,
    max_usd=5.00,
    action="abort",
)
token = set_budget_tracker(tracker)
try:
    await rag.run(...)
finally:
    tracker.summary()  # dict of totals
```

The MCP / CLI entry-point installs a tracker when `llm.budget.enabled`
is true, then surfaces the summary in the final response.

## Components

| File | Purpose |
|---|---|
| `src/perspicacite/llm/budget.py` (new) | `BudgetTracker`, `BudgetExceededError`, contextvar accessors, default `PRICING_TABLE`. |
| `src/perspicacite/llm/client.py` (modify) | `tracker.check()` + `tracker.record()` around the dispatch path in `complete()`. |
| `src/perspicacite/config/schema.py` (modify) | New `BudgetConfig` model + `pricing_overrides` dict on `LLMConfig`. |
| `tests/unit/test_budget_tracker.py` (new) | Pricing lookup, accumulation, threshold breach, wildcard match, unknown-cost flag, action=warn vs abort. |
| `tests/unit/test_budget_client_integration.py` (new) | E2E with mocked LiteLLM: tracker accumulates from real complete() calls, BudgetExceededError raises mid-pipeline. |
| `docs/budget-caps-2026-05-14.md` (new) | Operator guide. |

## Behaviour contract

- `tracker.check()` raises `BudgetExceededError` **before** the next
  call if any cap is already breached.
- `tracker.record(...)` increments totals and re-runs check; under
  `action="abort"` an over-budget recording raises immediately so the
  caller's `except BudgetExceededError` cleanup runs once and the
  next call doesn't start.
- Under `action="warn"`, breach logs once per breach type
  (in-tokens / out-tokens / usd) and lets the call proceed.

## Test plan

- **Unit (`test_budget_tracker.py`):**
  - Empty tracker has zero totals.
  - `record(prov, model, in, out)` accumulates.
  - Token cap raises when breached (abort mode).
  - Token cap logs when breached (warn mode).
  - Wildcard pricing matches `("claude_cli", "anything")`.
  - Override pricing wins over default.
  - Unknown pair sets `has_unknown_costs`.
  - `summary()` shape: keys for `tokens_in`, `tokens_out`, `usd`,
    `breaches`, `has_unknown_costs`.

- **Integration (`test_budget_client_integration.py`):**
  - Tracker installed → first complete() succeeds, totals increment.
  - Second complete() that crosses the cap raises BudgetExceededError.
  - No tracker installed → behaviour unchanged (no-op).
  - Cached call (Wave 2.1 hit) doesn't increment.

## Rollout

- **Default off.** Users who don't set `enabled: true` see no change.
- The pricing table is meant to be reasonable but not authoritative —
  the doc tells users to override per-pair when they care about
  accuracy.
- Cap exceedance raises a descriptive error; the orchestrator's
  existing `BudgetExceededError` handler logs a summary line then
  returns the partial result.

## Followups

- Per-stage caps (`budget.max_usd_per_stage.synthesis_heavy: 0.50`)
  for users who want fine-grained control.
- Cost-per-call breakdown in the MCP response payload.
- Auto-downshift: when within 10% of `max_usd`, route remaining calls
  through the cheapest configured stage instead of aborting.
