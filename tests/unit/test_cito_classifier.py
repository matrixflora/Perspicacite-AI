"""Unit tests for indicium_layer.cito_classifier.classify_pairs."""

import json

import pytest

from perspicacite.indicium_layer.cito_classifier import (
    CITO_CONFIDENCE_THRESHOLD,
    classify_pairs,
)


class _FakeLLM:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self._i = 0

    async def complete(self, *, messages, stage=None, **kw):
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return r


def _claim(cid):
    return {
        "_local_id": cid,
        "context": "ctx",
        "subject": "s",
        "qualifier": "inhibits",
        "relation": "affects",
        "object": "o",
    }


async def test_classify_pairs_returns_filtered_edges():
    pairs = [(_claim("a"), _claim("b")), (_claim("c"), _claim("d"))]
    llm = _FakeLLM(
        [
            json.dumps(
                [
                    {"pair_id": 0, "label": "supports", "confidence": 0.9},
                    {"pair_id": 1, "label": "none", "confidence": 0.95},
                ]
            )
        ]
    )
    edges = await classify_pairs(pairs, llm_client=llm, batch_size=10)
    assert len(edges) == 1
    assert edges[0]["label"] == "supports"
    assert edges[0]["from"]["_local_id"] == "a"
    assert edges[0]["to"]["_local_id"] == "b"
    assert edges[0]["confidence"] == pytest.approx(0.9)


async def test_classify_pairs_drops_low_confidence():
    pairs = [(_claim("a"), _claim("b"))]
    llm = _FakeLLM(
        [
            json.dumps(
                [
                    {
                        "pair_id": 0,
                        "label": "supports",
                        "confidence": CITO_CONFIDENCE_THRESHOLD - 0.05,
                    }
                ]
            )
        ]
    )
    assert await classify_pairs(pairs, llm_client=llm) == []


async def test_classify_pairs_rejects_invalid_label():
    pairs = [(_claim("a"), _claim("b"))]
    llm = _FakeLLM([json.dumps([{"pair_id": 0, "label": "garbage", "confidence": 0.99}])])
    assert await classify_pairs(pairs, llm_client=llm) == []


async def test_classify_pairs_handles_malformed_json():
    pairs = [(_claim("a"), _claim("b"))]
    llm = _FakeLLM(["not json at all"])
    assert await classify_pairs(pairs, llm_client=llm) == []


async def test_classify_pairs_batches():
    pairs = [(_claim(f"a{i}"), _claim(f"b{i}")) for i in range(25)]
    responses = [
        json.dumps([{"pair_id": k, "label": "supports", "confidence": 0.9} for k in range(10)]),
        json.dumps([{"pair_id": k, "label": "supports", "confidence": 0.9} for k in range(10)]),
        json.dumps([{"pair_id": k, "label": "supports", "confidence": 0.9} for k in range(5)]),
    ]
    llm = _FakeLLM(responses)
    edges = await classify_pairs(pairs, llm_client=llm, batch_size=10)
    assert len(edges) == 25
