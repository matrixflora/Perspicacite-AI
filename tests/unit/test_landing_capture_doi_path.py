"""Cookie-authenticated landing-capture fallback in the DOI pipeline.

The URL/GUI ingest path (ingest_url) already fetches publisher landing pages
through the cookie-authenticated client via capture_landing_html. The DOI path
(retrieve_paper_content, used by add_dois_to_kb) historically stopped at PDF
sources, so a paywalled-Nature DOI failed where pasting its URL succeeded.

This wires the same landing capture in as an opt-in last-resort fallback,
enabled only when cookies are configured (so default behaviour and the
abstract-only path are unchanged).
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.pipeline.download import unified
from perspicacite.pipeline.download.base import PaperDiscovery
from perspicacite.pipeline.download.html_capture import HtmlCapture


def _disc() -> PaperDiscovery:
    # No oa_url, no full text anywhere → pipeline reaches the fallback tail.
    return PaperDiscovery(
        doi="10.1038/paywalled",
        title="A Paywalled Nature Paper",
        abstract="A short abstract from discovery.",
        is_oa=False,
    )


@pytest.mark.asyncio
async def test_landing_capture_used_when_enabled_and_pdf_missed(tmp_path):
    cap = HtmlCapture(
        path=Path(tmp_path) / "x.html",
        tier="full_text",
        char_count=12000,
        extracted_title="A Paywalled Nature Paper",
        extracted_text="Full body text recovered from the publisher page. " * 50,
    )
    with (
        patch(
            "perspicacite.pipeline.download.unified.discover_paper_sources",
            new_callable=AsyncMock,
            return_value=_disc(),
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_content_from_europepmc",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "perspicacite.pipeline.download.unified._try_pdf_sources",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "perspicacite.pipeline.download.unified.capture_landing_html",
            new_callable=AsyncMock,
            return_value=cap,
        ) as mock_cap,
        patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path),
    ):
        result = await unified.retrieve_paper_content(
            "10.1038/paywalled",
            http_client=AsyncMock(),
            pdf_parser=object(),
            enable_landing_capture=True,
        )

    mock_cap.assert_awaited_once()
    assert result.success is True
    assert result.content_source == "landing_html"
    assert result.content_type == "full_text"
    assert result.full_text and len(result.full_text) > 200


@pytest.mark.asyncio
async def test_landing_capture_not_used_when_disabled(tmp_path):
    """Default (flag off): the fallback must not run, and the pipeline
    degrades to abstract-only as before."""
    with (
        patch(
            "perspicacite.pipeline.download.unified.discover_paper_sources",
            new_callable=AsyncMock,
            return_value=_disc(),
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_content_from_europepmc",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "perspicacite.pipeline.download.unified._try_pdf_sources",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "perspicacite.pipeline.download.unified.capture_landing_html",
            new_callable=AsyncMock,
        ) as mock_cap,
        patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path),
    ):
        result = await unified.retrieve_paper_content(
            "10.1038/paywalled",
            http_client=AsyncMock(),
            pdf_parser=object(),
        )

    mock_cap.assert_not_awaited()
    assert result.content_type == "abstract"


@pytest.mark.asyncio
async def test_landing_capture_thin_result_falls_through_to_abstract(tmp_path):
    """A stub/abstract-tier capture is no better than the abstract path, so it
    must not be accepted as full_text."""
    thin = HtmlCapture(
        path=Path(tmp_path) / "x.html",
        tier="bibliographic_stub",
        char_count=80,
        extracted_text="A Paywalled Nature Paper A short abstract.",
    )
    with (
        patch(
            "perspicacite.pipeline.download.unified.discover_paper_sources",
            new_callable=AsyncMock,
            return_value=_disc(),
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_fulltext_from_pmc",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
        patch(
            "perspicacite.pipeline.download.unified.get_content_from_europepmc",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "perspicacite.pipeline.download.unified._try_pdf_sources",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "perspicacite.pipeline.download.unified.capture_landing_html",
            new_callable=AsyncMock,
            return_value=thin,
        ),
        patch("perspicacite.pipeline.download.discovery._CACHE_DIR", tmp_path),
    ):
        result = await unified.retrieve_paper_content(
            "10.1038/paywalled",
            http_client=AsyncMock(),
            pdf_parser=object(),
            enable_landing_capture=True,
        )

    assert result.content_type == "abstract"
