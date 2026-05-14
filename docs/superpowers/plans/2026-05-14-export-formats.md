# Export formats — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Add CSL JSON + RIS exporters alongside the existing BibTeX
output.

**Spec:** `docs/superpowers/specs/2026-05-14-export-formats-design.md`

---

## Task 1: Per-format renderers

**Files:**
- Modify: `src/perspicacite/pipeline/export_kb.py`
- Test: `tests/unit/test_export_formats.py` (new)

- [ ] **Step 1: Write the failing tests for the renderers**

```python
# tests/unit/test_export_formats.py
"""Tests for CSL JSON + RIS exporters (Wave 4.5)."""
import json

import pytest

from perspicacite.pipeline.export_kb import (
    render_csl_json_entry,
    render_ris_entry,
)


_PAPER = {
    "title": "Cool Paper About Quasars",
    "authors": ["Smith, J.", "Doe, A."],
    "year": 2024,
    "journal": "ApJ",
    "doi": "10.1234/cool",
    "abstract": "We show that quasars are interesting.",
}


def test_csl_json_basic_fields():
    item = render_csl_json_entry(_PAPER)
    assert item["type"] == "article-journal"
    assert item["title"] == "Cool Paper About Quasars"
    assert item["container-title"] == "ApJ"
    assert item["issued"] == {"date-parts": [[2024]]}
    assert item["DOI"] == "10.1234/cool"
    assert item["URL"] == "https://doi.org/10.1234/cool"
    assert item["abstract"] == "We show that quasars are interesting."
    # ID must be a non-empty string
    assert isinstance(item["id"], str) and item["id"]


def test_csl_json_multi_author_split():
    item = render_csl_json_entry(_PAPER)
    assert item["author"] == [
        {"family": "Smith", "given": "J."},
        {"family": "Doe", "given": "A."},
    ]


def test_csl_json_handles_string_authors():
    """`authors` may come in as a comma-and-and-separated string."""
    paper = {**_PAPER, "authors": "Smith, J. and Doe, A. and Roe, P."}
    item = render_csl_json_entry(paper)
    assert len(item["author"]) == 3
    assert item["author"][2] == {"family": "Roe", "given": "P."}


def test_csl_json_omits_missing_fields():
    paper = {"title": "Only Title"}
    item = render_csl_json_entry(paper)
    assert item["title"] == "Only Title"
    assert "DOI" not in item
    assert "URL" not in item
    assert "abstract" not in item


def test_csl_json_no_author_field():
    paper = {"title": "Anonymous"}
    item = render_csl_json_entry(paper)
    assert "author" not in item


def test_ris_basic_fields():
    out = render_ris_entry(_PAPER)
    lines = out.splitlines()
    assert lines[0] == "TY  - JOUR"
    assert "T1  - Cool Paper About Quasars" in lines
    assert "PY  - 2024" in lines
    assert "JO  - ApJ" in lines
    assert "DO  - 10.1234/cool" in lines
    assert "UR  - https://doi.org/10.1234/cool" in lines
    assert lines[-1] == "ER  - "


def test_ris_multi_author_repeats_AU():
    out = render_ris_entry(_PAPER)
    au_lines = [l for l in out.splitlines() if l.startswith("AU  - ")]
    assert au_lines == ["AU  - Smith, J.", "AU  - Doe, A."]


def test_ris_handles_string_authors():
    paper = {**_PAPER, "authors": "Smith, J. and Doe, A."}
    out = render_ris_entry(paper)
    au_lines = [l for l in out.splitlines() if l.startswith("AU  - ")]
    assert au_lines == ["AU  - Smith, J.", "AU  - Doe, A."]


def test_ris_escapes_newlines_in_fields():
    """Newlines inside a field would corrupt the line-oriented format —
    replace them with spaces."""
    paper = {
        **_PAPER,
        "title": "Line one\nLine two",
        "abstract": "Para one.\n\nPara two.",
    }
    out = render_ris_entry(paper)
    title_lines = [l for l in out.splitlines() if l.startswith("T1  - ")]
    assert len(title_lines) == 1
    assert "\n" not in title_lines[0]


def test_ris_omits_missing_fields():
    paper = {"title": "Only Title"}
    out = render_ris_entry(paper)
    assert "DO  - " not in out
    assert "AB  - " not in out
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_export_formats.py -v
```

- [ ] **Step 3: Implement the renderers**

In `src/perspicacite/pipeline/export_kb.py`, after
`render_bibtex_entry`, add:

```python
def _parse_authors(authors: Any) -> list[dict[str, str]]:
    """Normalise a paper's `authors` field to a list of CSL author dicts.

    Accepts:
    - ``list[str]`` of ``"Family, Given"`` entries.
    - ``str`` joined by ``" and "`` (BibTeX style).
    - ``list[dict]`` with ``family`` / ``given`` already present
      (pass-through after light validation).
    """
    if not authors:
        return []
    if isinstance(authors, str):
        names = [a.strip() for a in authors.split(" and ") if a.strip()]
    elif isinstance(authors, list):
        names = []
        for a in authors:
            if isinstance(a, str):
                names.append(a.strip())
            elif isinstance(a, dict) and ("family" in a or "given" in a):
                # Already CSL-shaped — keep as-is.
                names.append(a)  # type: ignore[arg-type]
    else:
        return []
    out: list[dict[str, str]] = []
    for n in names:
        if isinstance(n, dict):
            out.append(n)  # already shaped
            continue
        if "," in n:
            family, _, given = n.partition(",")
            out.append({"family": family.strip(), "given": given.strip()})
        else:
            # Single name — treat as family-only.
            out.append({"family": n, "given": ""})
    return out


def render_csl_json_entry(paper: dict[str, Any]) -> dict[str, Any]:
    """Render one paper as a CSL JSON item.

    Schema: https://github.com/citation-style-language/schema
    """
    item: dict[str, Any] = {
        "id": _bibtex_citation_key(paper),
        "type": "article-journal",
    }
    if paper.get("title"):
        item["title"] = str(paper["title"])
    authors = _parse_authors(paper.get("authors"))
    if authors:
        item["author"] = authors
    if paper.get("year"):
        try:
            year = int(paper["year"])
        except (TypeError, ValueError):
            year = None
        if year is not None:
            item["issued"] = {"date-parts": [[year]]}
    if paper.get("journal"):
        item["container-title"] = str(paper["journal"])
    if paper.get("doi"):
        item["DOI"] = str(paper["doi"])
        item["URL"] = f"https://doi.org/{paper['doi']}"
    if paper.get("abstract"):
        item["abstract"] = str(paper["abstract"])
    return item


def _ris_clean(value: str) -> str:
    """Collapse newlines so the line-oriented RIS format stays valid."""
    return " ".join(value.split())


def render_ris_entry(paper: dict[str, Any]) -> str:
    """Render one paper as a RIS record.

    Type is always ``JOUR`` (journal article) for now — extending to
    book / chapter / other types would mean carrying a structured
    `type` field in the paper metadata.
    """
    lines: list[str] = ["TY  - JOUR"]
    key = _bibtex_citation_key(paper)
    lines.append(f"ID  - {key}")
    if paper.get("title"):
        lines.append(f"T1  - {_ris_clean(str(paper['title']))}")
    for a in _parse_authors(paper.get("authors")):
        family = a.get("family", "")
        given = a.get("given", "")
        name = f"{family}, {given}".strip(", ").strip()
        if name:
            lines.append(f"AU  - {_ris_clean(name)}")
    if paper.get("year"):
        try:
            year = int(paper["year"])
            lines.append(f"PY  - {year}")
        except (TypeError, ValueError):
            pass
    if paper.get("journal"):
        lines.append(f"JO  - {_ris_clean(str(paper['journal']))}")
    if paper.get("doi"):
        lines.append(f"DO  - {paper['doi']}")
        lines.append(f"UR  - https://doi.org/{paper['doi']}")
    if paper.get("abstract"):
        lines.append(f"AB  - {_ris_clean(str(paper['abstract']))}")
    lines.append("ER  - ")
    return "\n".join(lines)
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_export_formats.py -v
```

Expected: 10 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/export_kb.py \
        tests/unit/test_export_formats.py
git commit -m "feat(export): CSL JSON + RIS renderers (Wave 4.5)"
```

---

## Task 2: export_kb supports multiple formats

**Files:**
- Modify: `src/perspicacite/pipeline/export_kb.py` (the `export_kb` function + `ExportReport`)
- Test: `tests/unit/test_export_kb_formats.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_export_kb_formats.py
"""Verify export_kb writes the right files for each format (Wave 4.5)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.pipeline.export_kb import export_kb


def _app_state():
    state = SimpleNamespace()
    state.session_store = MagicMock()
    state.session_store.get_kb_metadata = AsyncMock(return_value=SimpleNamespace(
        collection_name="coll", paper_count=2, embedding_model="x",
    ))
    state.vector_store = MagicMock()
    state.vector_store.list_paper_metadata = AsyncMock(return_value=[
        {
            "title": "Paper A", "authors": ["Smith, J."], "year": 2024,
            "journal": "ApJ", "doi": "10.1/a",
            "abstract": "alpha",
        },
        {
            "title": "Paper B", "authors": ["Doe, A.", "Roe, P."], "year": 2023,
            "journal": "Nature", "doi": "10.2/b",
            "abstract": "beta",
        },
    ])
    state.config = SimpleNamespace(
        pdf_download=None,
        capsule=SimpleNamespace(root="/tmp"),
    )
    return state


@pytest.mark.asyncio
async def test_default_formats_unchanged(tmp_path):
    """No formats arg → just .bib, same as before."""
    state = _app_state()
    await export_kb(
        app_state=state, kb_name="kb1", out_dir=tmp_path,
        with_pdfs=False, with_supplementary=False,
    )
    assert (tmp_path / "kb1.bib").exists()
    assert not (tmp_path / "kb1.csl.json").exists()
    assert not (tmp_path / "kb1.ris").exists()


@pytest.mark.asyncio
async def test_all_three_formats(tmp_path):
    state = _app_state()
    report = await export_kb(
        app_state=state, kb_name="kb1", out_dir=tmp_path,
        with_pdfs=False, with_supplementary=False,
        formats=["bibtex", "csl_json", "ris"],
    )
    assert (tmp_path / "kb1.bib").exists()
    assert (tmp_path / "kb1.csl.json").exists()
    assert (tmp_path / "kb1.ris").exists()

    # CSL JSON must be valid JSON array.
    csl = json.loads((tmp_path / "kb1.csl.json").read_text())
    assert isinstance(csl, list)
    assert len(csl) == 2
    assert csl[0]["title"] == "Paper A"

    # RIS must contain both records.
    ris = (tmp_path / "kb1.ris").read_text()
    assert ris.count("TY  - JOUR") == 2
    assert ris.count("ER  - ") == 2

    # Report counts.
    assert report.bibtex_entries == 2
    assert report.csl_json_entries == 2
    assert report.ris_entries == 2


@pytest.mark.asyncio
async def test_unknown_format_raises(tmp_path):
    state = _app_state()
    with pytest.raises(ValueError, match="unknown format"):
        await export_kb(
            app_state=state, kb_name="kb1", out_dir=tmp_path,
            with_pdfs=False, with_supplementary=False,
            formats=["bibtex", "lattice"],
        )


@pytest.mark.asyncio
async def test_empty_formats_raises(tmp_path):
    state = _app_state()
    with pytest.raises(ValueError):
        await export_kb(
            app_state=state, kb_name="kb1", out_dir=tmp_path,
            formats=[],
        )


@pytest.mark.asyncio
async def test_csl_only_no_bib_written(tmp_path):
    state = _app_state()
    await export_kb(
        app_state=state, kb_name="kb1", out_dir=tmp_path,
        with_pdfs=False, with_supplementary=False,
        formats=["csl_json"],
    )
    assert (tmp_path / "kb1.csl.json").exists()
    assert not (tmp_path / "kb1.bib").exists()
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Wire formats into export_kb**

**3a.** Add fields to `ExportReport`. Find its dataclass definition
(around line 42) and add:

```python
    csl_json_entries: int = 0
    ris_entries: int = 0
    csl_json_path: str | None = None
    ris_path: str | None = None
```

**3b.** Modify `export_kb` signature:

```python
async def export_kb(
    *,
    app_state: Any,
    kb_name: str,
    out_dir: str | Path,
    with_pdfs: bool = True,
    with_supplementary: bool = False,
    overwrite: bool = False,
    formats: list[str] | None = None,
) -> ExportReport:
```

**3c.** Near the top of `export_kb`, validate formats:

```python
    _VALID = {"bibtex", "csl_json", "ris"}
    formats = formats or ["bibtex"]
    if not formats:
        raise ValueError("formats must be a non-empty list")
    for f in formats:
        if f not in _VALID:
            raise ValueError(f"unknown format: {f!r}; expected one of {sorted(_VALID)}")
```

**3d.** Where the existing code writes `bib_path`, gate it on
`"bibtex" in formats`. After the existing BibTeX write, add CSL JSON
and RIS writes:

```python
    if "csl_json" in formats:
        csl_path = out / f"{kb_name}.csl.json"
        if csl_path.exists() and not overwrite:
            raise FileExistsError(f"{csl_path} exists. Pass overwrite=True.")
        csl_items = [render_csl_json_entry(p) for p in papers_meta]
        csl_path.write_text(json.dumps(csl_items, indent=2))
        report.csl_json_entries = len(csl_items)
        report.csl_json_path = str(csl_path)

    if "ris" in formats:
        ris_path = out / f"{kb_name}.ris"
        if ris_path.exists() and not overwrite:
            raise FileExistsError(f"{ris_path} exists. Pass overwrite=True.")
        ris_records = [render_ris_entry(p) for p in papers_meta]
        ris_path.write_text("\n\n".join(ris_records) + "\n")
        report.ris_entries = len(ris_records)
        report.ris_path = str(ris_path)
```

Also: the BibTeX block's `bib_path.exists()` check + `bib_entries`
accumulation should also be gated on `"bibtex" in formats`. The
existing path is BibTeX-only — wrap the `bib_path = out / f"{kb_name}.bib"`
line and the writing block in `if "bibtex" in formats:`.

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_export_kb_formats.py -v
pytest tests/unit/test_export_formats.py -v   # no regression
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/export_kb.py \
        tests/unit/test_export_kb_formats.py
git commit -m "feat(export): export_kb writes selectable formats (Wave 4.5)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/export-formats-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
# Export formats — operator guide (2026-05-14)

Wave 4.5 of the framework-hardening roadmap. Export a KB's metadata
as BibTeX (existing), CSL JSON, or RIS for reference managers.

## Quick usage

```python
from perspicacite.pipeline.export_kb import export_kb

# Today (unchanged): BibTeX only.
await export_kb(
    app_state=state, kb_name="exposomics", out_dir="/tmp/export",
)

# All three formats:
await export_kb(
    app_state=state, kb_name="exposomics", out_dir="/tmp/export",
    formats=["bibtex", "csl_json", "ris"],
)
```

Output files:

| Format | Filename |
|---|---|
| `bibtex` | `<kb>.bib` |
| `csl_json` | `<kb>.csl.json` |
| `ris` | `<kb>.ris` |

## Format choices

- **BibTeX** (default): LaTeX, Zotero, JabRef. Native PDF attachment
  via `file = {…}`. Most universal.
- **CSL JSON**: Pandoc citeproc, Zotero (via better-bibtex export),
  any modern tooling that speaks CSL. Lossless author / date
  structure.
- **RIS**: EndNote, Mendeley, Papers, ProQuest, Web of Science.
  Older but widely supported.

## Behaviour contract

- `formats=None` (default) → BibTeX only, today's exact behaviour.
- `formats=[]` → `ValueError`. Empty list is operator error.
- `formats=["unknown"]` → `ValueError("unknown format: 'unknown'; ...")`.
- Each format's file overwrites only when `overwrite=True`.
- Citation key / `id` field is consistent across all three formats —
  derived from the BibTeX key (`<first-author><year><first-word>`).

## Limitations

- All records are typed as `article-journal` / `JOUR`. Books and
  chapters are not differentiated. Adding type discrimination
  requires a structured `type` field in the paper metadata, a
  separate followup.
- Authors are normalised to `family` / `given` pairs. Unicode/Asian
  name formats with no comma may end up with `family` only.
- RIS line wrapping is not enforced; newlines inside fields are
  collapsed to spaces so the file stays valid.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/export_kb.py` | `render_bibtex_entry`, `render_csl_json_entry`, `render_ris_entry`, `export_kb` |
| `tests/unit/test_export_formats.py` | Per-format renderer tests |
| `tests/unit/test_export_kb_formats.py` | `export_kb` format-selection tests |

## Followups

- Hayagriva YAML (Typst's bibliography format).
- EndNote XML.
- Book / chapter / inproceedings types.
- Round-trip test: export → re-ingest verifies no metadata loss.
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/export-formats-*.md` to `.gitignore` after
`!docs/time-bounded-queries-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/export-formats-2026-05-14.md .gitignore
git commit -m "docs(export): operator guide for BibTeX/CSL/RIS (Wave 4.5)"
```

---

## Done

After Task 3:

- Two new renderers (CSL JSON + RIS).
- `export_kb` accepts a `formats` list.
- 15 new tests across renderers and `export_kb`.
- Default behaviour unchanged.
- Operator doc landed.
