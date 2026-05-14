"""Synced from AgenticScienceBuilder @ a10eced — extended with TTL on read.

On-disk cache for external API responses. Layout:
``<cache_dir>/<api>__<sha256-of-query[:32]>.json`` containing a payload wrapper
``{"_cached_at": <epoch>, "data": <value>}`` so a TTL can be enforced on load.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def cache_path(cache_dir: Path, api: str, query: str) -> Path:
    """Return the on-disk cache path for ``(api, query)``."""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:32]
    return Path(cache_dir) / f"{api}__{digest}.json"


def cache_load(path: Path, *, ttl_seconds: int) -> Any | None:
    """Return cached payload, or ``None`` if missing/expired/corrupt.

    Treats files with no ``_cached_at`` wrapper as legacy-format and accepts
    them (no TTL); files older than ``ttl_seconds`` are purged.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        p.unlink(missing_ok=True)
        return None
    if isinstance(raw, dict) and "_cached_at" in raw and "data" in raw:
        age = time.time() - float(raw["_cached_at"])
        if age > ttl_seconds:
            p.unlink(missing_ok=True)
            return None
        return raw["data"]
    # Legacy / unwrapped payload — accept as-is.
    return raw


def cache_store(path: Path, payload: Any) -> None:
    """Atomically write ``payload`` to ``path`` with current epoch timestamp."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    wrapped = {"_cached_at": time.time(), "data": payload}
    tmp.write_text(json.dumps(wrapped, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
