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
