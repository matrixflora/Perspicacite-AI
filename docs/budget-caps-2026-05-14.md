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
