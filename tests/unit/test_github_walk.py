"""Tests for walk_filtered."""
from pathlib import Path

from perspicacite.pipeline.github.walk import walk_filtered


def test_walk_returns_only_matching_files(tmp_path):
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / "b.txt").write_text("hello")
    results = walk_filtered(tmp_path, include=["**/*.py"], exclude=[])
    assert Path("a.py") in results
    assert Path("b.txt") not in results


def test_walk_exclude_takes_precedence(tmp_path):
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"FAKE")
    results = walk_filtered(tmp_path, include=["**/*.py", "**/*.pyc"], exclude=["__pycache__/**"])
    paths = [str(r) for r in results]
    assert not any("__pycache__" in p for p in paths)


def test_walk_empty_directory_returns_empty(tmp_path):
    assert walk_filtered(tmp_path, include=["**/*.py"], exclude=[]) == []
