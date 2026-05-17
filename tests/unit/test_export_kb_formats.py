"""Verify export_kb writes the right files for each format (Wave 4.5)."""
import json
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
