"""Unit tests for add_dois_to_kb caller-facing warnings (audit #1).

The MCP tool must not let a 0-paper outcome read as silent success — it surfaces
a ``warnings`` list explaining why nothing was added.
"""
from perspicacite.mcp.server import _add_dois_warnings


def test_warns_when_all_dois_failed():
    w = _add_dois_warnings(added=0, failed=[{"doi": "10.1/x"}], skipped=[])
    assert len(w) == 1
    assert "No papers could be fetched" in w[0]
    assert "preprint" in w[0]  # actionable hint


def test_notes_when_all_duplicates():
    w = _add_dois_warnings(
        added=0, failed=[], skipped=[{"doi": "10.1/a"}, {"doi": "10.1/b"}]
    )
    assert len(w) == 1
    assert "already in the KB" in w[0]


def test_warns_partial_failure_on_success():
    w = _add_dois_warnings(added=3, failed=[{"doi": "10.1/x"}], skipped=[])
    assert len(w) == 1
    assert "failed to fetch" in w[0]


def test_no_warnings_on_clean_success():
    assert _add_dois_warnings(added=5, failed=[], skipped=[]) == []
