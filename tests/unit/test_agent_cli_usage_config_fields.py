"""Tests for the two usage-path fields on LLMProviderConfig (Wave 2.3)."""
from perspicacite.config.schema import LLMProviderConfig


def test_usage_paths_default_none():
    cfg = LLMProviderConfig()
    assert cfg.usage_input_tokens_path is None
    assert cfg.usage_output_tokens_path is None


def test_usage_paths_accept_strings():
    cfg = LLMProviderConfig(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    assert cfg.usage_input_tokens_path == "usage.input_tokens"
    assert cfg.usage_output_tokens_path == "usage.output_tokens"
