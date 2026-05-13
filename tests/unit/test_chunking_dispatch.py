"""chunking_dispatch.infer_content_type and chunk_document."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.config.schema import KnowledgeBaseConfig
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import chunk_document, infer_content_type


def test_infer_content_type_pdf():
    ct, lang = infer_content_type(Path("/a/b/file.pdf"))
    assert ct == "pdf" and lang is None


def test_infer_content_type_markdown():
    ct, lang = infer_content_type(Path("/a/b/file.md"))
    assert ct == "markdown" and lang is None


def test_infer_content_type_mdx():
    ct, lang = infer_content_type(Path("/a/b/file.mdx"))
    assert ct == "markdown" and lang is None


def test_infer_content_type_code_python():
    ct, lang = infer_content_type(Path("/a/b/foo.py"))
    assert ct == "code" and lang == "python"


def test_infer_content_type_code_typescript():
    ct, lang = infer_content_type(Path("/a/b/foo.ts"))
    assert ct == "code" and lang == "typescript"


def test_infer_content_type_code_go():
    ct, lang = infer_content_type(Path("/a/b/main.go"))
    assert ct == "code" and lang == "go"


def test_infer_content_type_fallback_text():
    ct, lang = infer_content_type(Path("/a/b/notes.unknown"))
    assert ct == "text" and lang is None


def _paper():
    return Paper(id="local:p1", title="t", source=PaperSource.LOCAL)


@pytest.mark.asyncio
async def test_chunk_markdown_keeps_heading_path():
    cfg = KnowledgeBaseConfig()
    text = "# Top\n\nIntro.\n\n## Sub\n\nDetail.\n\n### Sub2\n\nMore."
    chunks = await chunk_document(
        text, _paper(), content_type="markdown", language=None, config=cfg,
    )
    assert chunks
    heading_paths = {tuple(c.metadata.heading_path or []) for c in chunks}
    assert ("Top",) in heading_paths
    assert ("Top", "Sub") in heading_paths
    for c in chunks:
        assert c.metadata.content_type == "markdown"


@pytest.mark.asyncio
async def test_chunk_markdown_atomic_code_fence():
    cfg = KnowledgeBaseConfig()
    text = (
        "# Top\n\nstart\n\n```python\n"
        "def foo():\n    return 1\n"
        "```\n\nafter."
    )
    chunks = await chunk_document(
        text, _paper(), content_type="markdown", language=None, config=cfg,
    )
    fence_text = "\n".join(c.text for c in chunks if "```" in c.text)
    assert "def foo" in fence_text, "code fence body must stay together with its opening fence"


@pytest.mark.asyncio
async def test_chunk_code_python_tagged_with_language():
    cfg = KnowledgeBaseConfig(chunk_size=200, chunk_overlap=20)
    code = "\n\n".join([f"def func_{i}():\n    return {i}" for i in range(20)])
    chunks = await chunk_document(
        code, _paper(), content_type="code", language="python", config=cfg,
    )
    assert chunks
    for c in chunks:
        assert c.metadata.content_type == "code"
        assert c.metadata.language == "python"


@pytest.mark.asyncio
async def test_chunk_falls_back_when_flag_disabled():
    """markdown_heading_aware=False -> use text fallback (no heading_path)."""
    cfg = KnowledgeBaseConfig(markdown_heading_aware=False)
    text = "# Top\n\nIntro.\n\n## Sub\n\nDetail."
    chunks = await chunk_document(
        text, _paper(), content_type="markdown", language=None, config=cfg,
    )
    # Fallback chunker doesn't set heading_path, so it should remain None
    assert all(c.metadata.heading_path is None for c in chunks)
