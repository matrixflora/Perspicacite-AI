"""Tests for the shared ``paper_metadata_json`` decoder.

Pins the contract that ``DynamicKnowledgeBase`` (retrieval) and the RAG
modes (source emission) rely on — a single canonical decoder used by all
sites that consume the JSON-encoded ``paper.metadata`` round-trip.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from perspicacite.rag.paper_metadata_codec import decode_paper_metadata_json


def test_none_meta_returns_none() -> None:
    """A ``None`` argument is a safe no-op (defensive callers may pass it)."""
    assert decode_paper_metadata_json(None) is None


def test_dict_missing_field_returns_none() -> None:
    """Dict-shaped metadata without the field yields ``None``."""
    assert decode_paper_metadata_json({"title": "T"}) is None


def test_object_missing_attr_returns_none() -> None:
    """Object-shaped metadata without the attribute yields ``None``."""
    assert decode_paper_metadata_json(SimpleNamespace(title="T")) is None


def test_empty_blob_returns_none() -> None:
    """An empty-string blob (falsy) yields ``None``, not a JSON error."""
    assert decode_paper_metadata_json({"paper_metadata_json": ""}) is None
    assert (
        decode_paper_metadata_json(SimpleNamespace(paper_metadata_json="")) is None
    )


def test_valid_json_from_dict() -> None:
    """A valid JSON blob on a dict-shaped row decodes to its dict."""
    payload = {"asb": {"version": "1.0"}, "scimba": {"tool_id": "ms2deepscore"}}
    meta = {"paper_metadata_json": json.dumps(payload)}
    assert decode_paper_metadata_json(meta) == payload


def test_valid_json_from_object() -> None:
    """A valid JSON blob on an object-shaped (ChunkMetadata-like) meta decodes."""
    payload = {"scimba": {"backend": "scilex"}}
    meta = SimpleNamespace(paper_metadata_json=json.dumps(payload))
    assert decode_paper_metadata_json(meta) == payload


def test_malformed_json_returns_none() -> None:
    """A malformed blob yields ``None`` instead of raising."""
    assert decode_paper_metadata_json({"paper_metadata_json": "{not json"}) is None
    assert (
        decode_paper_metadata_json(SimpleNamespace(paper_metadata_json="]["))
        is None
    )


def test_non_string_blob_returns_none() -> None:
    """A non-string truthy blob (e.g., int) is a ``TypeError`` from ``json.loads``;
    the decoder must swallow it.
    """
    # 12345 is truthy but not str/bytes — json.loads raises TypeError.
    assert decode_paper_metadata_json({"paper_metadata_json": 12345}) is None


@pytest.mark.parametrize(
    "value",
    [
        '"a-string"',  # JSON string — valid JSON, not a dict
        "42",  # JSON number
        "null",  # JSON null
        "[1, 2, 3]",  # JSON array
    ],
)
def test_non_dict_valid_json_passes_through(value: str) -> None:
    """The decoder does not enforce dict shape — it returns whatever
    ``json.loads`` produces. Callers tolerate non-dict by ignoring it; this
    test pins the loose-typing contract so callers can rely on it.
    """
    result = decode_paper_metadata_json({"paper_metadata_json": value})
    assert result == json.loads(value)
