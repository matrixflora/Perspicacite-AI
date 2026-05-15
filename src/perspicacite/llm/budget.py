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

    def record_cost(
        self,
        *,
        provider: str,
        model: str,
        cost_usd: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """F4 (audit 2026-05-15): record a call whose USD cost is already
        known to the caller (e.g. ``agent_cli`` reading ``total_cost_usd``
        from the Claude CLI's JSON output).

        Tokens are recorded for visibility but not used to estimate cost
        — ``cost_usd`` is the source of truth.
        """
        del provider, model  # parity with .record(); unused here
        self.tokens_in += int(input_tokens or 0)
        self.tokens_out += int(output_tokens or 0)
        self.usd += float(cost_usd or 0.0)
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
