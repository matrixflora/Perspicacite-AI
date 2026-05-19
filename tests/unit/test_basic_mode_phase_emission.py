"""Verify the new phase_progress / tokens / cost_estimate emit helpers.

We don't try to drive a full BasicRAGMode.execute_stream() here — that
would require massive fixture machinery (vector store, embedding
provider, scope resolver, web fallback aggregator). Instead we:

1. Smoke-test the source file to defend against typos in the phase
   string literals the AB-T1 progress adapter expects.
2. Exercise the helpers in :mod:`perspicacite.rag.telemetry` directly
   to confirm event shape.
"""

from __future__ import annotations

import pathlib

import pytest

from perspicacite.rag.telemetry import (
    ListTelemetrySink,
    emit_cost,
    emit_phase,
    emit_tokens,
)


def test_emit_phase_appends_to_sink() -> None:
    sink = ListTelemetrySink()
    emit_phase(sink, phase="retrieve", state="running")
    emit_phase(sink, phase="retrieve", state="done")
    emit_phase(sink, phase="synthesize", state="running")

    kinds = [ev.get("kind") for ev in sink.events]
    phases = [(ev.get("phase"), ev.get("state")) for ev in sink.events]
    assert kinds == ["phase_progress"] * 3
    assert phases == [
        ("retrieve", "running"),
        ("retrieve", "done"),
        ("synthesize", "running"),
    ]


def test_emit_phase_with_none_sink_is_noop() -> None:
    # Must not raise — modes pass getattr(request, "telemetry_sink", None)
    # which is None whenever the legacy code-path is not wired up.
    emit_phase(None, phase="retrieve", state="running")


def test_emit_tokens_event_shape() -> None:
    sink = ListTelemetrySink()
    emit_tokens(sink, input_tokens=12, output_tokens=7, model="m", provider="p")
    ev = sink.events[0]
    assert ev["kind"] == "tokens"
    assert ev["in"] == 12
    assert ev["out"] == 7
    assert ev["model"] == "m"


def test_emit_cost_event_shape() -> None:
    sink = ListTelemetrySink()
    emit_cost(sink, usd=0.0123, model="m")
    ev = sink.events[0]
    assert ev["kind"] == "cost_estimate"
    assert ev["usd"] == pytest.approx(0.0123)
    assert ev["model"] == "m"


# ---- typo-defense smoke test on basic.py source -----------------------

_BASIC_SRC = pathlib.Path(__file__).resolve().parents[2] / (
    "src/perspicacite/rag/modes/basic.py"
)


def test_basic_mode_emits_retrieve_and_synthesize_phase_literals() -> None:
    """Defend against typos in phase strings the MCP adapter consumes."""
    text = _BASIC_SRC.read_text()
    assert 'phase="retrieve"' in text
    assert 'phase="synthesize"' in text
    # And the import line we depend on.
    assert "from perspicacite.rag.telemetry import emit_phase" in text
