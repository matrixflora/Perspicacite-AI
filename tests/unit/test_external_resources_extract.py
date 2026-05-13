"""ASB-aligned resource-URL extraction (DOI, GitHub, Zenodo) — vendored regexes."""

from __future__ import annotations

import pytest


def test_extract_doi_candidates():
    from perspicacite.pipeline.external.resources import extract_doi_candidates
    txt = "See https://doi.org/10.1234/abcdef and 10.5555/zenodo.987654 in the SI."
    out = extract_doi_candidates(txt)
    assert "10.1234/abcdef" in out
    assert "10.5555/zenodo.987654" in out


def test_extract_github_repos():
    from perspicacite.pipeline.external.resources import extract_github_repos
    txt = (
        "Code is at https://github.com/HolobiomicsLab/AgenticScienceBuilder "
        "and github.com/foo/bar."
    )
    out = extract_github_repos(txt)
    assert "HolobiomicsLab/AgenticScienceBuilder" in out
    assert "foo/bar" in out


def test_extract_zenodo_record_ids():
    from perspicacite.pipeline.external.resources import extract_zenodo_record_ids
    txt = "Data: https://zenodo.org/record/9876543 ; also 10.5281/zenodo.1234567"
    out = extract_zenodo_record_ids(txt)
    assert "9876543" in out
    assert "1234567" in out


def test_no_match():
    from perspicacite.pipeline.external.resources import (
        extract_doi_candidates, extract_github_repos, extract_zenodo_record_ids,
    )
    assert extract_doi_candidates("nothing here") == []
    assert extract_github_repos("nothing here") == []
    assert extract_zenodo_record_ids("nothing here") == []
