"""Unit tests for GET /api/paper endpoint.

Tests the get_paper_detail handler directly by monkeypatching app_state
and retrieve_paper_content.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers: fake dependencies
# ---------------------------------------------------------------------------


def _make_fake_app_state():
    """Build a SimpleNamespace that mimics the relevant app_state surface."""
    config = MagicMock()
    config.pdf_download = None

    return SimpleNamespace(
        config=config,
        pdf_parser=MagicMock(),
    )


def _make_paper_content(
    success=True,
    doi="10.1/x",
    content_type="abstract",
    content_source="openalex",
    abstract="An abstract.",
    full_text=None,
    references=None,
    metadata=None,
):
    """Build a fake PaperContent dataclass-like object."""
    from perspicacite.pipeline.download.base import PaperContent

    return PaperContent(
        success=success,
        doi=doi,
        content_type=content_type,
        content_source=content_source,
        abstract=abstract,
        full_text=full_text,
        references=references,
        metadata=metadata or {"title": "T", "authors": ["X"], "year": 2020, "journal": "J"},
    )


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_paper_detail_empty_doi_raises_400():
    """Empty doi query param raises HTTPException(400)."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state()

    with patch.object(kb_router, "app_state", fake_state):
        with pytest.raises(HTTPException) as exc_info:
            await kb_router.get_paper_detail(doi="")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_paper_detail_whitespace_doi_raises_400():
    """Whitespace-only doi raises HTTPException(400)."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state()

    with patch.object(kb_router, "app_state", fake_state):
        with pytest.raises(HTTPException) as exc_info:
            await kb_router.get_paper_detail(doi="   ")

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Tests: live fetch path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_paper_detail_happy_path(monkeypatch):
    """Happy path returns correct metadata from retrieve_paper_content result."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state()
    fake_content = _make_paper_content(
        success=True,
        doi="10.1/x",
        content_type="abstract",
        content_source="openalex",
        abstract="An abstract.",
        full_text=None,
        references=None,
        metadata={"title": "T", "authors": ["X"], "year": 2020, "journal": "J"},
    )

    async def fake_retrieve(doi, **kw):
        return fake_content

    with patch.object(kb_router, "app_state", fake_state):
        with patch("perspicacite.pipeline.download.retrieve_paper_content", fake_retrieve):
            result = await kb_router.get_paper_detail(doi="10.1/x")

    assert result["doi"] == "10.1/x", result
    assert result["content_type"] == "abstract", result
    assert result["title"] == "T", result
    assert result["abstract"] == "An abstract.", result
    assert result["has_full_text"] is False, result
    assert result["authors"] == ["X"], result
    assert result["year"] == 2020, result
    assert result["journal"] == "J", result


@pytest.mark.asyncio
async def test_get_paper_detail_has_full_text(monkeypatch):
    """has_full_text is True when full_text is present."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state()
    fake_content = _make_paper_content(
        success=True,
        doi="10.1/y",
        content_type="full_text",
        content_source="pmc",
        abstract="Abstract.",
        full_text="Full paper text here.",
        references=[{"title": "Ref1"}],
        metadata={"title": "Y", "authors": ["A", "B"], "year": 2021, "journal": "J2"},
    )

    async def fake_retrieve(doi, **kw):
        return fake_content

    with patch.object(kb_router, "app_state", fake_state):
        with patch("perspicacite.pipeline.download.retrieve_paper_content", fake_retrieve):
            result = await kb_router.get_paper_detail(doi="10.1/y")

    assert result["has_full_text"] is True, result
    assert result["references_count"] == 1, result


@pytest.mark.asyncio
async def test_get_paper_detail_doi_prefix_stripped(monkeypatch):
    """https://doi.org/ prefix is stripped from the doi parameter."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state()
    captured = {}

    fake_content = _make_paper_content(doi="10.1/x")

    async def fake_retrieve(doi, **kw):
        captured["doi"] = doi
        return fake_content

    with patch.object(kb_router, "app_state", fake_state):
        with patch("perspicacite.pipeline.download.retrieve_paper_content", fake_retrieve):
            result = await kb_router.get_paper_detail(doi="https://doi.org/10.1/x")

    assert captured["doi"] == "10.1/x", captured
    assert result["doi"] == "10.1/x", result


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_paper_detail_exception_returns_error_dict(monkeypatch):
    """If retrieve_paper_content raises, returns error dict without raising."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state()

    async def fake_retrieve(doi, **kw):
        raise RuntimeError("boom")

    with patch.object(kb_router, "app_state", fake_state):
        with patch("perspicacite.pipeline.download.retrieve_paper_content", fake_retrieve):
            result = await kb_router.get_paper_detail(doi="10.1/x")

    assert result["doi"] == "10.1/x", result
    assert result["error"] == "boom", result
    assert result["content_type"] == "none", result


@pytest.mark.asyncio
async def test_paper_detail_does_not_pass_pdf_parser(monkeypatch):
    """get_paper_detail must pass pdf_parser=None (fast metadata lookup, no PDF fetch)."""
    from perspicacite.pipeline.download.base import PaperContent
    from perspicacite.web.routers import kb as kb_router

    captured: dict = {}

    async def _fake(doi, **kw):
        captured.update(kw)
        return PaperContent(
            success=True,
            doi=doi,
            content_type="abstract",
            content_source="openalex",
            abstract="a",
            metadata={"title": "T"},
        )

    fake_state = _make_fake_app_state()

    with (
        patch.object(kb_router, "app_state", fake_state),
        patch("perspicacite.pipeline.download.retrieve_paper_content", _fake),
    ):
        await kb_router.get_paper_detail(doi="10.1/x")

    assert captured.get("pdf_parser") is None, (
        f"get_paper_detail must pass pdf_parser=None, got {captured.get('pdf_parser')!r}"
    )
