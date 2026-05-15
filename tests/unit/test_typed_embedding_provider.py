from __future__ import annotations

import pytest

from perspicacite.llm.embeddings import TypedEmbeddingProvider


class _StubProvider:
    def __init__(self, name: str, dim: int = 4):
        self._name = name
        self._dim = dim
        self.calls: list[list[str]] = []

    @property
    def model_name(self) -> str:
        return self._name

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        # Vectors are stamped with the provider name's first char for visibility.
        marker = float(ord(self._name[0]) if self._name else 0)
        return [[marker] * self._dim for _ in texts]


@pytest.mark.asyncio
async def test_routes_by_content_type_preserving_order():
    code = _StubProvider("codestral-embed")
    text = _StubProvider("text-embedding-3-small")
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})

    texts = ["def f(): pass", "the cat sat", "another snippet"]
    types = ["code", "text", "code"]
    vecs = await tp.embed(texts, content_types=types)

    # Output order matches input order.
    assert len(vecs) == 3
    # 'c' for codestral, 't' for text.
    assert vecs[0][0] == float(ord("c"))
    assert vecs[1][0] == float(ord("t"))
    assert vecs[2][0] == float(ord("c"))

    # Each provider got exactly its share, contiguous.
    assert code.calls == [["def f(): pass", "another snippet"]]
    assert text.calls == [["the cat sat"]]


@pytest.mark.asyncio
async def test_missing_type_falls_through_to_default():
    text = _StubProvider("text-embedding-3-small")
    tp = TypedEmbeddingProvider(default=text, by_content_type={})

    vecs = await tp.embed(["hi", "yo"], content_types=["markdown", "pdf"])
    assert len(vecs) == 2
    assert text.calls == [["hi", "yo"]]


@pytest.mark.asyncio
async def test_none_content_types_uses_default_only():
    text = _StubProvider("text-embedding-3-small")
    code = _StubProvider("codestral-embed")
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})

    vecs = await tp.embed(["a", "b"])  # no content_types kwarg
    assert len(vecs) == 2
    assert text.calls == [["a", "b"]]
    assert code.calls == []


def test_model_name_composes_inner_names():
    text = _StubProvider("text-embedding-3-small")
    code = _StubProvider("codestral-embed")
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})
    # Stable composition; default first, then sorted-key inner providers.
    assert tp.model_name == "text-embedding-3-small+code:codestral-embed"


def test_dimension_matches_default_when_homogeneous():
    text = _StubProvider("text-embedding-3-small", dim=8)
    code = _StubProvider("codestral-embed", dim=8)
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})
    assert tp.dimension == 8


def test_dimension_none_when_inner_dims_disagree():
    text = _StubProvider("text-embedding-3-small", dim=8)
    code = _StubProvider("codestral-embed", dim=4)
    tp = TypedEmbeddingProvider(default=text, by_content_type={"code": code})
    assert tp.dimension is None
