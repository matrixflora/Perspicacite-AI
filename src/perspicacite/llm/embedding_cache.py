"""On-disk cache for embedding vectors.

See docs/superpowers/specs/2026-05-14-embedding-cache-design.md.
The cache is per-text (not per-batch), so overlapping batches share
entries. Vectors are stored as float32 BLOBs.
"""

from __future__ import annotations

import hashlib


def build_embedding_cache_key(*, model: str, text: str) -> str:
    """Compute the SHA256 cache key for an (model, text) pair.

    The null-byte separator prevents ambiguity at the model/text
    boundary (no ``"foobar" + ""`` vs ``"foo" + "bar"`` collisions).
    Empty inputs raise ``ValueError`` — the wrapper handles those
    upstream with the zero-vector contract.
    """
    if not model:
        raise ValueError("model must be non-empty")
    if not text:
        raise ValueError("text must be non-empty")
    payload = model.encode("utf-8") + b"\x00" + text.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
