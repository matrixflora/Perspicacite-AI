"""capsule_builder.write_blocks emits one row per paragraph with section tags."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from perspicacite.pipeline.capsule_builder import write_blocks


def test_emits_blocks_with_sections(tmp_path):
    cap = tmp_path / "cap"
    text = (
        "## Abstract\nWe present X.\n\n"
        "## Methods\nWe did Y.\n\nAnother methods paragraph.\n\n"
        "## Results\nWe found Z.\n"
    )
    rows = write_blocks(cap, text=text)
    p = cap / "text" / "blocks.jsonl"
    assert p.exists()
    parsed = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    assert rows == len(parsed)
    sections = {r["section"] for r in parsed}
    assert {"abstract", "methods", "results"} <= sections
    contents = [r["content"] for r in parsed]
    assert any("We did Y." in c for c in contents)
    # block ids are unique
    assert len({r["block_id"] for r in parsed}) == len(parsed)


def test_fallback_full_text(tmp_path):
    cap = tmp_path / "cap"
    rows = write_blocks(cap, text="just prose without headings at all.")
    p = cap / "text" / "blocks.jsonl"
    parsed = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    assert rows >= 1
    assert all(r["section"] == "full_text" for r in parsed)


def test_empty_text(tmp_path):
    cap = tmp_path / "cap"
    rows = write_blocks(cap, text="")
    assert rows == 0
    p = cap / "text" / "blocks.jsonl"
    assert p.exists()
    assert p.read_text() == ""
