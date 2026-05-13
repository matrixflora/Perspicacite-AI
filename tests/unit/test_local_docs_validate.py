"""validate_local_path rejects unsafe paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.integrations.local_docs import (
    LocalDocsDisabledError,
    LocalDocsValidationError,
    validate_local_path,
)


def test_rejects_relative_path(tmp_path):
    with pytest.raises(LocalDocsValidationError):
        validate_local_path("relative/path.md", allowed_roots=[tmp_path])


def test_rejects_dotdot(tmp_path):
    with pytest.raises(LocalDocsValidationError):
        validate_local_path(str(tmp_path / ".." / "x"), allowed_roots=[tmp_path])


def test_rejects_outside_allowed_roots(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "f.md").write_text("x")
    with pytest.raises(LocalDocsValidationError):
        validate_local_path(str(other / "f.md"), allowed_roots=[tmp_path / "inside"])


def test_raises_disabled_when_roots_empty(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("x")
    with pytest.raises(LocalDocsDisabledError):
        validate_local_path(str(p), allowed_roots=[])


def test_accepts_valid_path_under_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    f = root / "doc.md"
    f.write_text("hi")
    out = validate_local_path(str(f), allowed_roots=[root])
    assert out == f.resolve()
