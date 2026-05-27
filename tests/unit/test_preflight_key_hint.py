"""Audit #8: the LLM-key startup preflight error tells the user to `source`
their shell profile (the key may be set there but not in the current env)."""
from types import SimpleNamespace

import pytest

import perspicacite.web.state as state


def test_preflight_missing_key_suggests_source(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PERSPICACITE_ALLOW_MISSING_LLM_KEYS", raising=False)
    cfg = SimpleNamespace(llm=SimpleNamespace(default_provider="anthropic"))
    with pytest.raises(RuntimeError, match="source"):
        state._preflight_llm_keys(cfg)
