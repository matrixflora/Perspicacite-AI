from __future__ import annotations

import pytest

from perspicacite.pipeline.library_doi import (
    LibraryPaper,
    resolve_library_paper,
)


@pytest.mark.asyncio
async def test_config_map_takes_precedence():
    paper = await resolve_library_paper(
        "openff-evaluator",
        bundle=None,
        github_repo=None,
        config_map={"openff-evaluator": "10.1021/acs.jctc.8b00640"},
        readme_text=None,
    )
    assert paper is not None
    assert paper.source == "config"
    assert paper.confidence == 1.0
    assert paper.doi == "10.1021/acs.jctc.8b00640"


@pytest.mark.asyncio
async def test_bundle_field_used_when_no_config_match():
    bundle = {"tools": [
        {"name": "openff-evaluator", "paper_doi": "10.0/bundle"},
        {"name": "other-tool"},
    ]}
    paper = await resolve_library_paper(
        "openff-evaluator",
        bundle=bundle, github_repo=None, config_map=None, readme_text=None,
    )
    assert paper is not None
    assert paper.source == "bundle"
    assert paper.doi == "10.0/bundle"


@pytest.mark.asyncio
async def test_readme_scrape_finds_please_cite():
    readme = (
        "# my-lib\n\nA cool library.\n\n"
        "If you use my-lib in your research, please cite "
        "DOI 10.1234/abcd1234 for the original paper.\n"
    )
    paper = await resolve_library_paper(
        "my-lib",
        bundle=None, github_repo=None, config_map=None, readme_text=readme,
    )
    assert paper is not None
    assert paper.source == "readme"
    assert paper.doi == "10.1234/abcd1234"
    assert 0.4 <= paper.confidence <= 0.9


@pytest.mark.asyncio
async def test_returns_none_when_nothing_resolvable():
    paper = await resolve_library_paper(
        "unknown-lib",
        bundle=None, github_repo=None, config_map={}, readme_text=None,
    )
    assert paper is None


@pytest.mark.asyncio
async def test_citation_cff_doi_field_recognised():
    cff_like = "cff-version: 1.2.0\ndoi: 10.0/cff\n"
    paper = await resolve_library_paper(
        "lib",
        bundle=None, github_repo=None, config_map=None, readme_text=cff_like,
    )
    assert paper is not None
    assert paper.doi == "10.0/cff"
