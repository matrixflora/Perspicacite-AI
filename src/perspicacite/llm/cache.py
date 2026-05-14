"""On-disk SQLite cache for AsyncLLMClient.complete() responses.

See docs/superpowers/specs/2026-05-14-llm-disk-cache-design.md for
the design rationale. This module is intentionally narrow: pure key
building + a thin sqlite3 wrapper. AsyncLLMClient composes it in
client.py.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Kwargs that don't affect the model's output and therefore must not
# participate in the cache key. Adding to this list is backwards
# compatible (only widens hits). Removing requires bumping a key
# version, which would invalidate the cache.
_VOLATILE_KWARGS: frozenset[str] = frozenset({
    "stage",      # provenance label only
    "cache",      # the cache-bypass flag itself
    "timeout",    # affects whether a call succeeds, not what it returns
})


def _canonical(obj: Any) -> Any:
    """Coerce ``obj`` into a form whose JSON serialisation is stable
    across Python dict-ordering. Recursive."""
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    return obj


def build_cache_key(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    extra_kwargs: dict[str, Any],
) -> str:
    """Compute the SHA256 cache key for an LLM call.

    The key is stable across:
    - Python dict insertion order (we canonicalise before serialising).
    - Volatile-kwarg values (``stage``, ``cache``, ``timeout``).

    The key changes whenever any field that meaningfully affects the
    provider's response changes (provider, model, messages, temperature,
    max_tokens, response_format, tools, ...).
    """
    filtered = {k: v for k, v in extra_kwargs.items() if k not in _VOLATILE_KWARGS}
    payload = {
        "provider": provider,
        "model": model,
        "messages": _canonical(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra": _canonical(filtered),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
