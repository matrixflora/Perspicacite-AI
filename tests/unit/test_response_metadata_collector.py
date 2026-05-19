"""Tests for ResponseMetadataCollector."""
from __future__ import annotations

import pytest

from perspicacite.rag.telemetry import ResponseMetadataCollector


def test_collector_records_attempts():
    c = ResponseMetadataCollector()
    c.append(
        {
            "kind": "provider_progress",
            "phase": "done",
            "query": "foo",
            "by_provider": {"arxiv": 2, "pubmed": 3},
            "total": 5,
        }
    )
    extras = c.as_response_extras()
    assert extras["attempts"] == [
        {
            "query": "foo",
            "provider_counts": {"arxiv": 2, "pubmed": 3},
            "hit_count": 5,
        }
    ]


def test_collector_records_query_rephrasings():
    c = ResponseMetadataCollector()
    c.append(
        {
            "kind": "query_rephrased",
            "original": "obscure",
            "rewritten": "obscure terms expanded",
            "reason": "low_recall",
        }
    )
    extras = c.as_response_extras()
    assert extras["query_rephrasings"] == [
        {"original": "obscure", "refined": "obscure terms expanded", "reason": "low_recall"}
    ]


def test_collector_aggregates_token_usage():
    c = ResponseMetadataCollector()
    c.append({"kind": "tokens", "in": 100, "out": 50})
    c.append({"kind": "tokens", "in": 200, "out": 75})
    c.append({"kind": "cost_estimate", "usd": 0.034, "model": "deepseek/deepseek-chat"})
    extras = c.as_response_extras()
    assert extras["usage"]["tokens_in"] == 300
    assert extras["usage"]["tokens_out"] == 125
    assert extras["usage"]["model"] == "deepseek/deepseek-chat"
    assert extras["usage"]["cost_usd_estimate"] == pytest.approx(0.034, rel=1e-6)


def test_collector_omits_empty_sections():
    c = ResponseMetadataCollector()
    # No events appended
    assert c.as_response_extras() == {}


def test_collector_partial_sections():
    c = ResponseMetadataCollector()
    c.append({"kind": "tokens", "in": 10, "out": 5})
    extras = c.as_response_extras()
    assert "usage" in extras
    assert "attempts" not in extras
    assert "query_rephrasings" not in extras


def test_collector_unknown_events_ignored():
    c = ResponseMetadataCollector()
    c.append({"kind": "random_event", "data": 42})
    c.append({"not_a_dict": "also fine"})
    c.append("a string is fine too")  # should not crash
    assert c.as_response_extras() == {}


def test_collector_is_append_compatible_with_list_sink():
    """Ensure the collector matches the .append(event) sink protocol."""
    c = ResponseMetadataCollector()
    sink = c  # collector IS a sink
    sink.append({"kind": "tokens", "in": 1, "out": 1})
    assert c.as_response_extras()["usage"]["tokens_in"] == 1
