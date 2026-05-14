# tests/unit/test_embedding_cache_key.py
"""Cache-key tests for the embedding cache (Wave 2.2)."""
import pytest

from perspicacite.llm.embedding_cache import build_embedding_cache_key


def test_key_stable():
    a = build_embedding_cache_key(model="m1", text="hello world")
    b = build_embedding_cache_key(model="m1", text="hello world")
    assert a == b
    assert len(a) == 64


def test_key_differs_on_model():
    a = build_embedding_cache_key(model="m1", text="hello")
    b = build_embedding_cache_key(model="m2", text="hello")
    assert a != b


def test_key_differs_on_text():
    a = build_embedding_cache_key(model="m1", text="hello")
    b = build_embedding_cache_key(model="m1", text="world")
    assert a != b


def test_key_disambiguates_concatenation():
    """The null-byte separator prevents collisions between
    (model='ab', text='c') and (model='a', text='bc')."""
    a = build_embedding_cache_key(model="ab", text="c")
    b = build_embedding_cache_key(model="a", text="bc")
    assert a != b


def test_key_rejects_empty_text():
    """Empty texts should never reach the cache — the wrapper handles
    them with the zero-vector contract before we get here."""
    with pytest.raises(ValueError):
        build_embedding_cache_key(model="m", text="")


def test_key_rejects_empty_model():
    with pytest.raises(ValueError):
        build_embedding_cache_key(model="", text="text")
