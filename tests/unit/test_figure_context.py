"""ASB-aligned figure context fusion — vendored."""

from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _FakePdfFig:
    page: int = 3
    index: int = 2
    caption: str = "Figure 1. Overview."
    figure_number: str = "1"
    subcomponent_label: str | None = None
    panel_files: list = None
    def __post_init__(self):
        if self.panel_files is None:
            self.panel_files = []


def test_build_figure_context_pdf_only():
    from perspicacite.pipeline.parsers.figure_context import build_figure_context
    out = build_figure_context(pdf_figures=[_FakePdfFig()], jats_figures=())
    assert len(out) == 1
    assert out[0].figure_id == "pdf_p3_i2"
    assert out[0].label == "Figure 1"
    assert out[0].source == "pdf"


def test_supports_vision_allowlist():
    from perspicacite.pipeline.parsers.figure_context import supports_vision
    assert supports_vision("anthropic/claude-opus-4-7") is True
    assert supports_vision("openai/gpt-4o-2024-08-06") is True
    assert supports_vision("mistral/mistral-large") is False
    assert supports_vision("") is False


def test_format_figures_block_empty():
    from perspicacite.pipeline.parsers.figure_context import format_figures_block
    assert format_figures_block([]) == ""


def test_load_image_b64_missing(tmp_path):
    from perspicacite.pipeline.parsers.figure_context import load_image_b64
    assert load_image_b64(tmp_path / "nope.png") is None


def test_load_image_b64_roundtrip(tmp_path):
    import base64
    from perspicacite.pipeline.parsers.figure_context import load_image_b64
    p = tmp_path / "x.png"
    payload = b"\x89PNG\r\n\x1a\nhello"
    p.write_bytes(payload)
    got = load_image_b64(p)
    assert got == base64.b64encode(payload).decode("ascii")
