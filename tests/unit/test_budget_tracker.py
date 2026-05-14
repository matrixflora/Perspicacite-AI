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


def test_check_raises_when_state_already_over_budget():
    """check() must detect an existing breach even if the breaching
    record() happened under warn mode. Useful when an orchestrator
    flips action='warn'→'abort' midway through a run."""
    t = BudgetTracker(max_input_tokens=10, action="warn")
    t.record(provider="anthropic", model="claude-haiku-4-5",
             input_tokens=20, output_tokens=0)  # over, but warn — doesn't raise
    t.action = "abort"
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
