# tests/unit/test_llm_cache_key.py
"""Tests for cache-key stability and isolation (Wave 2.1)."""
import pytest

from perspicacite.llm.cache import build_cache_key


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_key_stable_across_calls():
    """Same input → same key. Sanity check."""
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")],
        temperature=0.0, max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")],
        temperature=0.0, max_tokens=100, extra_kwargs={},
    )
    assert k1 == k2
    assert len(k1) == 64  # SHA256 hex digest


def test_key_differs_on_provider():
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="openai", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_differs_on_model():
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-sonnet-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_differs_on_messages():
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hello")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_differs_on_temperature():
    """Temperature must participate — temp=0 and temp=0.7 are
    semantically different calls."""
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.7,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_strips_volatile_kwargs():
    """`stage`, `cache`, `timeout` don't affect what the provider
    returns; they shouldn't pollute the key."""
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={"stage": "routing", "cache": True, "timeout": 30},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={"stage": "screening", "cache": False, "timeout": 60},
    )
    assert k1 == k2


def test_key_includes_non_volatile_kwargs():
    """response_format / tools / etc. DO affect the result, so they
    must end up in the key."""
    k1 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={"response_format": {"type": "json_object"}},
    )
    k2 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={},
    )
    assert k1 != k2


def test_key_stable_across_dict_ordering():
    """Python dicts preserve insertion order; the key shouldn't."""
    k1 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, max_tokens=100,
        extra_kwargs={"a": 1, "b": 2},
    )
    k2 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[{"content": "hi", "role": "user"}],
        temperature=0.0, max_tokens=100,
        extra_kwargs={"b": 2, "a": 1},
    )
    assert k1 == k2


def test_key_handles_anthropic_content_blocks():
    """Anthropic messages can have list-of-typed-blocks content.
    The serialiser must walk them without choking."""
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "be helpful"}]},
        {"role": "user", "content": [
            {"type": "text", "text": "context", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "question"},
        ]},
    ]
    k = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=messages, temperature=0.0, max_tokens=100,
        extra_kwargs={},
    )
    assert len(k) == 64
