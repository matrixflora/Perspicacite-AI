# tests/unit/test_kb_config_code_chunking.py
import pytest
from pydantic import ValidationError

from perspicacite.config.schema import KnowledgeBaseConfig


def test_default_is_auto():
    cfg = KnowledgeBaseConfig()
    assert cfg.code_chunking == "auto"


def test_explicit_values_accepted():
    for v in ("auto", "ast", "splitter"):
        cfg = KnowledgeBaseConfig(code_chunking=v)
        assert cfg.code_chunking == v


def test_invalid_value_rejected():
    with pytest.raises(ValidationError):
        KnowledgeBaseConfig(code_chunking="treesitter")  # not in literal


def test_legacy_code_language_aware_still_present():
    cfg = KnowledgeBaseConfig()
    assert cfg.code_language_aware is True  # back-compat
