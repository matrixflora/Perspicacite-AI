"""Tests for visual-extraction fields on KnowledgeBaseConfig (Wave 4.1)."""
import pytest

from perspicacite.config.schema import KnowledgeBaseConfig


def test_visual_defaults_off():
    kb = KnowledgeBaseConfig()
    assert kb.visual_extraction_enabled is False
    assert kb.visual_extraction_model == "claude-sonnet-4-5"
    assert kb.visual_extraction_provider == "anthropic"
    assert kb.visual_extraction_dpi == 150


def test_visual_can_enable():
    kb = KnowledgeBaseConfig(
        visual_extraction_enabled=True,
        visual_extraction_dpi=200,
        visual_extraction_model="gpt-4o",
        visual_extraction_provider="openai",
    )
    assert kb.visual_extraction_enabled is True
    assert kb.visual_extraction_dpi == 200
    assert kb.visual_extraction_model == "gpt-4o"
    assert kb.visual_extraction_provider == "openai"


def test_visual_dpi_bounded():
    """DPI must be at least 72 (legible) and at most 300 (image-size sanity)."""
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(visual_extraction_dpi=30)
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(visual_extraction_dpi=600)
