"""LocalDocsConfig + KnowledgeBaseConfig new flags."""

from __future__ import annotations

from pathlib import Path

from perspicacite.config.schema import Config, KnowledgeBaseConfig, LocalDocsConfig


def test_local_docs_config_default_empty():
    c = LocalDocsConfig()
    assert c.allowed_roots == []


def test_local_docs_config_with_roots(tmp_path):
    # use tmp_path so the validator's resolve() doesn't choke
    a = tmp_path / "docs"
    b = tmp_path / "data"
    a.mkdir()
    b.mkdir()
    c = LocalDocsConfig(allowed_roots=[str(a), str(b)])
    assert len(c.allowed_roots) == 2
    for p in c.allowed_roots:
        assert isinstance(p, Path)
        assert p.is_absolute()


def test_kb_config_has_smart_chunk_flags():
    kb = KnowledgeBaseConfig()
    assert kb.markdown_heading_aware is True
    assert kb.code_language_aware is True


def test_main_config_has_local_docs():
    c = Config()
    assert isinstance(c.local_docs, LocalDocsConfig)
    assert c.local_docs.allowed_roots == []


def test_kb_config_flags_can_be_overridden():
    kb = KnowledgeBaseConfig(markdown_heading_aware=False, code_language_aware=False)
    assert kb.markdown_heading_aware is False
    assert kb.code_language_aware is False
