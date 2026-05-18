# tests/unit/test_config_query_optimization.py
from perspicacite.config.schema import Config, SearchConfig


def test_search_config_has_query_optimization_defaults():
    cfg = SearchConfig()
    qo = cfg.query_optimization
    assert qo.enabled is True
    assert qo.timeout_s == 5.0
    assert qo.max_context_chars == 300
    assert qo.grounding_enabled is True
    assert qo.grounding_timeout_s == 4.0
    assert qo.grounding_max_prior_chars == 200
    assert qo.grounding_max_query_chars == 200


def test_search_config_query_optimization_overrides():
    cfg = SearchConfig(
        query_optimization={
            "enabled": False,
            "timeout_s": 2.0,
            "max_context_chars": 150,
            "grounding_enabled": False,
            "grounding_timeout_s": 1.5,
            "grounding_max_prior_chars": 100,
            "grounding_max_query_chars": 100,
        }
    )
    qo = cfg.query_optimization
    assert qo.enabled is False
    assert qo.timeout_s == 2.0
    assert qo.max_context_chars == 150
    assert qo.grounding_enabled is False


def test_full_config_includes_search_query_optimization():
    cfg = Config()
    assert cfg.search.query_optimization.enabled is True
