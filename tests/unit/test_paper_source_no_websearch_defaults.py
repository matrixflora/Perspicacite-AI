"""Regression guard for the 2026-05-15 PaperSource migration.

After the audit follow-up, no production module under ``src/`` should
construct a Paper with the keyword-argument pattern
``source=PaperSource.WEB_SEARCH``. The string ``PaperSource.WEB_SEARCH``
is still allowed elsewhere — for example, in the enum definition
itself, or as a typed-parameter default of the form
``source: PaperSource = PaperSource.WEB_SEARCH`` (which is textually
distinct from the kwarg form and naturally outside this guard's reach).

We forbid the specific pattern ``source=PaperSource.WEB_SEARCH``, which
is what every Paper-construction call-site uses.
"""
from __future__ import annotations

from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "perspicacite"


def test_no_web_search_construction_in_src():
    """grep ``source=PaperSource.WEB_SEARCH`` across src/ must return
    zero matches."""
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "source=PaperSource.WEB_SEARCH" in text:
            offenders.append(str(py.relative_to(SRC_ROOT)))
    assert not offenders, (
        f"PaperSource.WEB_SEARCH default has regressed in: {offenders}.\n"
        "Every production Paper-construction site must carry a "
        "domain-correct PaperSource value (see "
        "test_paper_source_adapter_migration.py for the per-adapter "
        "pinning convention)."
    )
