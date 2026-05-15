import pytest
from pydantic import ValidationError
from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig


def test_cite_graph_config_defaults():
    cfg = CiteGraphConfig()
    assert cfg.min_year_offset == 7
    assert cfg.min_citations == 1
    assert cfg.max_papers == 50
    assert cfg.include_scripts is False
    assert cfg.venue_denylist == []


def test_cite_graph_weight_defaults_sum_to_one():
    cfg = CiteGraphConfig()
    s = cfg.w_citations + cfg.w_recency + cfg.w_oa + cfg.w_match
    assert abs(s - 1.0) < 1e-6


def test_kb_config_library_paper_map_default_empty():
    kb = KnowledgeBaseConfig()
    assert kb.library_paper_map == {}


def test_kb_config_cite_graph_default_factory():
    kb = KnowledgeBaseConfig()
    assert isinstance(kb.cite_graph, CiteGraphConfig)


def test_invalid_weight_rejected():
    with pytest.raises(ValidationError):
        CiteGraphConfig(w_citations=-0.1)
    with pytest.raises(ValidationError):
        CiteGraphConfig(w_citations=1.5)
