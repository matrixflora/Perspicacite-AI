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
