"""Unit tests for indicium_layer.manifest."""

import json

import pytest

from perspicacite.indicium_layer.manifest import (
    Manifest,
    manifest_path,
    read_manifest,
    write_manifest,
)


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "perspicacite.indicium_layer.manifest._DATA_DIR",
        tmp_path / "claim_graphs",
    )
    return tmp_path / "claim_graphs"


def test_read_manifest_missing_returns_default(tmp_data_dir):
    m = read_manifest("nope")
    assert m.kb_name == "nope"
    assert m.paper_hashes == {}
    assert m.last_build_iso is None


def test_write_then_read_roundtrip(tmp_data_dir):
    m = Manifest(
        kb_name="kbA",
        paper_hashes={"10.1/a": "abcd1234abcd1234"},
        indicium_schema_version="1.2.3",
        builder_version="2",
        last_build_iso="2026-05-23T10:00:00Z",
    )
    write_manifest(m)

    assert manifest_path("kbA") == tmp_data_dir / "kbA" / "manifest.json"
    raw = json.loads((tmp_data_dir / "kbA" / "manifest.json").read_text())
    assert raw["paper_hashes"] == {"10.1/a": "abcd1234abcd1234"}

    m2 = read_manifest("kbA")
    assert m2 == m


def test_write_creates_directories(tmp_data_dir):
    write_manifest(Manifest(kb_name="deep/kb"))
    assert (tmp_data_dir / "deep" / "kb" / "manifest.json").exists()
