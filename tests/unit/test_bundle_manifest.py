"""Tests for BundleManifest parser and link extractor."""
from __future__ import annotations

from perspicacite.pipeline.github.bundle import (
    DEFAULT_INCLUDE_GLOBS,
    BundleManifest,
    extract_links_from_text,
)


def test_minimal_valid_yaml(tmp_path):
    p = tmp_path / "bundle.yml"
    p.write_text("name: scrna-qc\n")
    m = BundleManifest.parse(p)
    assert m.name == "scrna-qc"
    assert m.papers == []
    assert m.content.include == DEFAULT_INCLUDE_GLOBS


def test_unknown_keys_ignored(tmp_path):
    p = tmp_path / "bundle.yml"
    p.write_text("name: x\nfuture_field: foo\n")
    m = BundleManifest.parse(p)
    assert m.name == "x"


def test_falls_back_to_readme_when_yaml_missing(tmp_path):
    (tmp_path / "README.md").write_text("# My skill\n\nIntro.")
    m = BundleManifest.from_directory(tmp_path)
    assert m.name == tmp_path.name
    assert m.readme_only is True
    assert "My skill" in (m.readme_text or "")


def test_link_extraction_from_papers_section(tmp_path):
    p = tmp_path / "bundle.yml"
    p.write_text(
        "name: x\n"
        "papers:\n"
        "  - doi: 10.1234/foo\n"
        "  - arxiv: '2204.12345'\n"
        "  - pmc: 'PMC9123456'\n"
    )
    m = BundleManifest.parse(p)
    dois = m.collect_paper_refs()
    assert ("doi", "10.1234/foo") in dois
    assert ("arxiv", "2204.12345") in dois
    assert ("pmc", "PMC9123456") in dois


def test_extract_doi_from_text():
    bag = extract_links_from_text("See 10.1038/nature12345 for details.")
    assert "10.1038/nature12345" in bag.dois


def test_extract_arxiv_from_text():
    bag = extract_links_from_text("Also arXiv: 2204.12345v2 is relevant.")
    assert "2204.12345v2" in bag.arxiv_ids


def test_extract_pmc_from_text():
    bag = extract_links_from_text("Available at PMC9123456.")
    assert "PMC9123456" in bag.pmc_ids


def test_empty_text_returns_empty_bag():
    bag = extract_links_from_text("")
    assert bag.dois == [] and bag.arxiv_ids == [] and bag.pmc_ids == []


def test_from_directory_no_readme_no_yaml(tmp_path):
    m = BundleManifest.from_directory(tmp_path)
    assert m.name == tmp_path.name
    assert m.readme_only is True
    assert m.readme_text is None
