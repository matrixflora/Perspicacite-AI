"""Audit #3: extract_*_from_passages return an actionable error when `passages`
is a JSON-encoded string instead of a list of passage dicts."""
from perspicacite.mcp.server import _passages_type_message


def test_string_passages_rejected_with_actionable_message():
    msg = _passages_type_message('[{"text": "x", "source_doi": "10.1/x"}]')
    assert msg is not None
    assert "list of passage dicts" in msg
    assert "not a string" in msg
    assert "get_relevant_passages" in msg


def test_list_passages_accepted():
    assert _passages_type_message([{"text": "x"}]) is None
    assert _passages_type_message([]) is None
