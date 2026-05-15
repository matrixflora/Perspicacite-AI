"""F8 (audit 2026-05-15): FallbackEmbeddingProvider must track which
inner provider actually produced the vectors of the last call, so KB
metadata writers can record the real model rather than the ambiguous
"primary|fallback" tag.
"""
from __future__ import annotations

import pytest

from perspicacite.llm.embeddings import FallbackEmbeddingProvider


class _Stub:
    def __init__(self, name: str, dim: int = 4, *, fail: bool = False) -> None:
        self._name = name
        self._dim = dim
        self._fail = fail
        self.calls = 0

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self._fail:
            raise RuntimeError(f"{self._name}: forced failure")
        return [[1.0, 2.0, 3.0, 4.0] for _ in texts]


@pytest.mark.asyncio
async def test_last_used_model_records_primary_on_success():
    primary = _Stub("primary-model")
    fallback = _Stub("fallback-model")
    fb = FallbackEmbeddingProvider(primary=primary, fallback=fallback)

    await fb.embed(["hello"])
    assert fb.last_used_model == "primary-model"
    assert fb.fallback_triggered_count == 0


@pytest.mark.asyncio
async def test_last_used_model_records_fallback_when_primary_fails():
    primary = _Stub("primary-model", fail=True)
    fallback = _Stub("fallback-model")
    fb = FallbackEmbeddingProvider(primary=primary, fallback=fallback)

    await fb.embed(["hello"])
    assert fb.last_used_model == "fallback-model"
    assert fb.fallback_triggered_count == 1


@pytest.mark.asyncio
async def test_legacy_model_name_unchanged():
    """The historical "primary|fallback" tag must still be exposed so
    existing KB rows (stored under that string) still load."""
    primary = _Stub("a")
    fallback = _Stub("b")
    fb = FallbackEmbeddingProvider(primary=primary, fallback=fallback)
    assert fb.model_name == "a|b"


@pytest.mark.asyncio
async def test_fallback_count_increments_across_calls():
    primary = _Stub("primary", fail=True)
    fallback = _Stub("fallback")
    fb = FallbackEmbeddingProvider(primary=primary, fallback=fallback)

    await fb.embed(["x"])
    await fb.embed(["y"])
    await fb.embed(["z"])
    assert fb.fallback_triggered_count == 3
