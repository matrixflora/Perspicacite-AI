"""Regression guard for the 2026-05-15 PaperSource migration.

After the audit follow-up, no production module under ``src/`` should
construct a Paper with ``source=PaperSource.WEB_SEARCH``. The string
``PaperSource.WEB_SEARCH`` is still allowed where it is *referenced*
non-constructively — for example, in the enum definition itself,
in the docstring of normalize_paper_dict, or in legacy default-param
declarations that callers always override.

We allow the literal string ``PaperSource.WEB_SEARCH`` to appear, but
forbid the specific pattern ``source=PaperSource.WEB_SEARCH`` which is
what every Paper-construction call-site uses.
"""
from __future__ import annotations

from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "perspicacite"

# Files allowed to mention ``source=PaperSource.WEB_SEARCH`` even after
# the migration — e.g. legacy parameter defaults whose callers all
# override. Each entry is the *relative* path under src/perspicacite/.
ALLOWED_FILES: set[str] = {
    # normalize_paper_dict default param — historical fallback for
    # callers that don't specify source=; migrated callers always do.
    "models/papers.py",
}


def test_no_web_search_construction_in_src():
    """grep ``source=PaperSource.WEB_SEARCH`` across src/ must return
    only the explicitly-allow-listed files."""
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        rel = str(py.relative_to(SRC_ROOT))
        if rel in ALLOWED_FILES:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "source=PaperSource.WEB_SEARCH" in text:
            offenders.append(rel)
    assert not offenders, (
        f"PaperSource.WEB_SEARCH default has regressed in: {offenders}.\n"
        "If this is intentional, add the path to ALLOWED_FILES with a "
        "comment explaining why."
    )
