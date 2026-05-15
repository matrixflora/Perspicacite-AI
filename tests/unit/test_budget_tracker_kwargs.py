from __future__ import annotations

import pytest

from perspicacite.llm.budget import BudgetExceededError, BudgetTracker


def test_max_cost_usd_alias_for_max_usd():
    t = BudgetTracker(max_cost_usd=1.0)
    assert t.max_usd == 1.0


def test_explicit_max_usd_still_works():
    t = BudgetTracker(max_usd=2.5)
    assert t.max_usd == 2.5
    assert t.max_cost_usd is None or t.max_cost_usd == 2.5  # either is fine


def test_max_usd_wins_when_both_set_to_different_values():
    """If a caller sets both, the canonical (max_usd) value wins."""
    t = BudgetTracker(max_usd=3.0, max_cost_usd=1.0)
    assert t.max_usd == 3.0


def test_max_tokens_combined_cap_enforced():
    """max_tokens caps tokens_in + tokens_out combined."""
    t = BudgetTracker(max_tokens=100, action="abort")
    t.record(provider="claude_cli", model="*", input_tokens=40, output_tokens=40)
    # 80 < 100 → ok
    with pytest.raises(BudgetExceededError):
        t.record(provider="claude_cli", model="*", input_tokens=30, output_tokens=0)
    # 110 > 100 → breach


def test_max_tokens_warn_mode_does_not_raise():
    t = BudgetTracker(max_tokens=10, action="warn")
    # Should not raise even when breached
    t.record(provider="claude_cli", model="*", input_tokens=20, output_tokens=0)
    assert any("max_tokens" in b or "total_tokens" in b for b in t.breaches)


def test_natural_audit_harness_call_works():
    """The exact call from the audit harness — must not raise."""
    t = BudgetTracker(max_tokens=1000, max_cost_usd=1.0)
    assert t.max_usd == 1.0
    # Recording within cap is fine
    t.record(provider="claude_cli", model="*", input_tokens=10, output_tokens=10)
    assert t.tokens_in + t.tokens_out == 20
