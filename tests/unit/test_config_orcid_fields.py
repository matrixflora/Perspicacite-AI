"""Tests for ORCID resolver config fields (Wave 4.4)."""
from pathlib import Path

import pytest

from perspicacite.config.schema import KnowledgeBaseConfig


def test_orcid_defaults():
    kb = KnowledgeBaseConfig()
    assert kb.orcid_cache_path == Path("data/orcid_cache.db")
    assert kb.orcid_cache_ttl_days == 30
    assert kb.orcid_confidence_threshold == 0.20


def test_orcid_overrides():
    kb = KnowledgeBaseConfig(
        orcid_cache_path="custom/orcid.db",
        orcid_cache_ttl_days=7,
        orcid_confidence_threshold=0.5,
    )
    assert kb.orcid_cache_path == Path("custom/orcid.db")
    assert kb.orcid_cache_ttl_days == 7
    assert kb.orcid_confidence_threshold == 0.5


def test_orcid_threshold_bounded():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(orcid_confidence_threshold=-0.1)
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(orcid_confidence_threshold=1.5)
