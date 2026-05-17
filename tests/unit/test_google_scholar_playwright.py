"""Unit tests for the Google Scholar Playwright provider.

All tests mock _render_and_extract_cards so no browser is needed.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from perspicacite.search.google_scholar_playwright import (
    GoogleScholarPlaywrightProvider,
    _build_scholar_url,
    _extract_doi_from_url,
    _parse_meta_line,
)


# ── Pure helper tests (no mock needed) ───────────────────────────────────────

def test_build_scholar_url_with_year_range():
    url = _build_scholar_url("alphafold protein", year_min=2020, year_max=2023)
    assert "as_ylo=2020" in url
    assert "as_yhi=2023" in url
    assert "scholar.google.com" in url


def test_build_scholar_url_without_years():
    url = _build_scholar_url("microbiome diversity")
    assert "as_ylo" not in url
    assert "as_yhi" not in url
    assert "scholar.google.com" in url


def test_build_scholar_url_pagination():
    url = _build_scholar_url("test", start=10)
    assert "start=10" in url


def test_parse_meta_line_full():
    authors, venue, year = _parse_meta_line(
        "J Jumper, R Evans, A Senior - Nature, 2021 - nature.com"
    )
    assert year == 2021
    assert "Jumper" in authors
    assert venue  # non-empty


def test_parse_meta_line_year_only():
    _, _, year = _parse_meta_line("Some Author - Some Journal - 2019")
    assert year == 2019


def test_parse_meta_line_no_year():
    _, _, year = _parse_meta_line("Some Author - Some Journal")
    assert year is None


def test_extract_doi_from_doi_url():
    doi = _extract_doi_from_url("https://doi.org/10.1038/s41587-020-00744-z")
    assert doi == "10.1038/s41587-020-00744-z"


def test_extract_doi_from_doi_url_http():
    doi = _extract_doi_from_url("http://dx.doi.org/10.1016/j.cell.2021.01.001")
    assert doi == "10.1016/j.cell.2021.01.001"


def test_extract_doi_from_non_doi_url_returns_none():
    assert _extract_doi_from_url("https://arxiv.org/abs/2204.12345") is None
    assert _extract_doi_from_url("https://www.nature.com/articles/s41587") is None
    assert _extract_doi_from_url("") is None


# ── Provider behaviour tests (mock _render_and_extract_cards) ────────────────

_FAKE_CARDS = [
    {
        "title": "Deep Learning for Protein Structure",
        "url": "https://doi.org/10.1038/s41587-020-00744-z",
        "meta": "J Jumper, R Evans - Nature, 2021 - nature.com",
        "snippet": "We present AlphaFold2...",
    },
    {
        "title": "Attention Is All You Need",
        "url": "https://arxiv.org/abs/1706.03762",
        "meta": "A Vaswani, N Shazeer - NeurIPS, 2017 - papers.nips.cc",
        "snippet": "The dominant sequence model...",
    },
]


@pytest.mark.asyncio
async def test_provider_converts_cards_to_papers():
    async def fake_render(url, *, delay, headless, user_agent):
        return list(_FAKE_CARDS)

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        papers = await provider.search("protein structure prediction", max_results=10)

    assert len(papers) == 2
    p = papers[0]
    assert p.title == "Deep Learning for Protein Structure"
    assert p.doi == "10.1038/s41587-020-00744-z"
    assert p.year == 2021
    assert p.source.value == "google_scholar"


@pytest.mark.asyncio
async def test_provider_respects_max_results():
    async def fake_render(url, *, delay, headless, user_agent):
        return list(_FAKE_CARDS)

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        papers = await provider.search("test", max_results=1)

    assert len(papers) <= 1


@pytest.mark.asyncio
async def test_provider_returns_empty_on_render_error():
    async def fake_render(url, *, delay, headless, user_agent):
        raise RuntimeError("browser crash")

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        papers = await provider.search("test", max_results=5)

    assert papers == []


@pytest.mark.asyncio
async def test_provider_passes_year_filters_to_url():
    captured_urls: list[str] = []

    async def fake_render(url, *, delay, headless, user_agent):
        captured_urls.append(url)
        return []

    with patch(
        "perspicacite.search.google_scholar_playwright._render_and_extract_cards",
        new=fake_render,
    ):
        provider = GoogleScholarPlaywrightProvider(delay_seconds=0.0)
        await provider.search("CRISPR", max_results=5, year_min=2020, year_max=2023)

    assert captured_urls
    assert "as_ylo=2020" in captured_urls[0]
    assert "as_yhi=2023" in captured_urls[0]
