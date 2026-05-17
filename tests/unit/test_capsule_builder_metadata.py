"""capsule_builder writes metadata.json with the v0.1 schema."""

from __future__ import annotations

import json

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.capsule_builder import capsule_dir_for, write_metadata


def test_capsule_dir_for_local(tmp_path):
    paper = Paper(id="local:abc123", title="t", source=PaperSource.LOCAL)
    out = capsule_dir_for(paper, root=tmp_path)
    assert out == tmp_path / "local_abc123"


def test_capsule_dir_for_doi(tmp_path):
    paper = Paper(id="doi:10.1234/abc", title="t", source=PaperSource.USER_UPLOAD)
    out = capsule_dir_for(paper, root=tmp_path)
    # ":" replaced with "_", "/" replaced with "__"
    assert out == tmp_path / "doi_10.1234__abc"


def test_write_metadata_schema(tmp_path):
    paper = Paper(
        id="doi:10.1234/abc",
        title="A Paper",
        source=PaperSource.USER_UPLOAD,
        year=2025,
        doi="10.1234/abc",
    )
    cap_dir = tmp_path / "cap"
    cap_dir.mkdir()
    write_metadata(cap_dir, paper=paper, producer_version="0.0.0-test")
    payload = json.loads((cap_dir / "metadata.json").read_text())
    assert payload["capsule_version"] == "0.1"
    assert payload["producer"] == "perspicacite"
    assert payload["paper_id"] == "doi:10.1234/abc"
    assert payload["title"] == "A Paper"
    assert payload["year"] == 2025
    assert payload["doi"] == "10.1234/abc"
    assert payload["task_id"] is None
    assert "built_at" in payload
