"""Read an ASB-shaped capsule directory into a Perspicacité KB."""
from __future__ import annotations

import json
from pathlib import Path


def is_capsule_dir(path) -> bool:
    """Return True iff ``path`` is a directory containing ``metadata.json``
    with a non-empty ``capsule_version`` field.
    """
    p = Path(path)
    if not p.is_dir():
        return False
    meta = p / "metadata.json"
    if not meta.is_file():
        return False
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("capsule_version"))
