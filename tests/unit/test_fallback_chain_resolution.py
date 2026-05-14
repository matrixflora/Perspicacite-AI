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
