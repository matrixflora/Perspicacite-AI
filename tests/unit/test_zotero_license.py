# tests/unit/test_zotero_license.py
"""Tests for LicenseClassifier."""
from __future__ import annotations
import pytest
from perspicacite.integrations.zotero_license import LicenseClassifier, LicenseInfo


def _clf() -> LicenseClassifier:
    return LicenseClassifier()


# --- classify_spdx unit tests (pure, no HTTP) ---

def test_cc0_is_permissive():
    info = _clf().classify_spdx("CC0-1.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_cc_by_4_is_permissive():
    info = _clf().classify_spdx("CC-BY-4.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_cc_by_sa_is_permissive():
    info = _clf().classify_spdx("CC-BY-SA-4.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_mit_is_permissive():
    info = _clf().classify_spdx("MIT")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_apache_is_permissive():
    info = _clf().classify_spdx("Apache-2.0")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_bsd3_is_permissive():
    info = _clf().classify_spdx("BSD-3-Clause")
    assert info.classification == "permissive"
    assert info.policy == "verbatim"

def test_cc_by_nc_is_closed():
    info = _clf().classify_spdx("CC-BY-NC-4.0")
    assert info.classification == "closed"
    assert info.policy == "reflavor"

def test_cc_by_nd_is_closed():
    info = _clf().classify_spdx("CC-BY-ND-4.0")
    assert info.classification == "closed"
    assert info.policy == "reflavor"

def test_none_spdx_is_unknown():
    info = _clf().classify_spdx(None)
    assert info.classification == "unknown"
    assert info.policy == "reflavor"

def test_unknown_spdx_is_unknown():
    info = _clf().classify_spdx("LicenseRef-Proprietary")
    assert info.classification == "unknown"
    assert info.policy == "reflavor"

def test_classify_url_cc_by():
    info = _clf().classify_url("https://creativecommons.org/licenses/by/4.0/")
    assert info.classification == "permissive"

def test_classify_url_cc_by_nc():
    info = _clf().classify_url("https://creativecommons.org/licenses/by-nc/4.0/")
    assert info.classification == "closed"

def test_classify_zotero_tag_open_access():
    item = {"data": {"tags": [{"tag": "open-access"}, {"tag": "metabolomics"}]}}
    info = _clf().classify_zotero_tags(item)
    assert info is not None
    assert info.classification == "permissive"
    assert info.source == "zotero_tag"

def test_classify_zotero_tag_closed():
    item = {"data": {"tags": [{"tag": "closed"}]}}
    info = _clf().classify_zotero_tags(item)
    assert info is not None
    assert info.classification == "closed"

def test_classify_zotero_tag_none_when_no_known_tag():
    item = {"data": {"tags": [{"tag": "metabolomics"}]}}
    info = _clf().classify_zotero_tags(item)
    assert info is None  # no license tag → can't classify

def test_heuristic_is_oa_true_returns_permissive():
    info = _clf().heuristic(is_oa=True)
    assert info.classification == "permissive"
    assert info.source == "heuristic"

def test_heuristic_is_oa_false_returns_unknown():
    info = _clf().heuristic(is_oa=False)
    assert info.classification == "unknown"

def test_cache_hit():
    clf = _clf()
    info1 = clf.classify_spdx("CC-BY-4.0")
    clf._cache["10.9999/test"] = (info1, 9999999999)  # expires far future
    info2 = clf.get_cached("10.9999/test")
    assert info2 is info1
