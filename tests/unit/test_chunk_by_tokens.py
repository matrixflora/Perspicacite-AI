"""Edge-case tests for ``pipeline.chunking._chunk_by_tokens``.

Covers a previously discovered hang (when text shorter than overlap_chars
produced an infinite loop), the followup thrash (a forward-progress fix
that emitted hundreds of near-duplicate tail chunks), and the normal
multi-chunk case as a sanity check.
"""
from __future__ import annotations

import signal
from contextlib import contextmanager

import pytest

from perspicacite.models.kb import ChunkConfig
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking import _chunk_by_tokens


def _paper() -> Paper:
    return Paper(id="p", title="t", abstract="", source=PaperSource.BIBTEX)


@contextmanager
def _timeout(seconds: int):
    """SIGALRM-based watchdog — fails the test if the body takes too long."""
    def _handler(signum, frame):
        raise TimeoutError(f"hang detected (>{seconds}s)")

    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def test_empty_text_returns_empty_list():
    assert _chunk_by_tokens("", _paper(), ChunkConfig()) == []


def test_single_char_text_emits_one_chunk():
    """Pre-existing infinite-loop regression test: short text + default
    config (overlap_chars=800 > len(text)=1) used to hang."""
    with _timeout(5):
        chunks = _chunk_by_tokens("x", _paper(), ChunkConfig())
    assert len(chunks) == 1
    assert chunks[0].text == "x"


def test_short_text_with_default_config_does_not_hang():
    """Direct regression for the original bug (text < overlap_chars)."""
    with _timeout(5):
        chunks = _chunk_by_tokens("hello world", _paper(), ChunkConfig())
    assert len(chunks) == 1


def test_text_exactly_chunk_size_emits_one_chunk():
    """4000-char text equals char_per_chunk (1000 tokens × 4). Should be
    a single chunk — not 800+ overlapping tail chunks."""
    text = "a" * 4000
    with _timeout(5):
        chunks = _chunk_by_tokens(text, _paper(), ChunkConfig())
    # Expect 1, allow ≤2 to be lenient on sentence-boundary heuristic edge.
    assert len(chunks) <= 2, f"thrash detected: {len(chunks)} chunks for 4000-char text"


def test_overlap_larger_than_chunk_does_not_thrash():
    """Pathological config (overlap > chunk_size). Bounded chunk count
    is acceptable; thrash (~800 near-identical chunks) is not."""
    cfg = ChunkConfig(chunk_size=100, chunk_overlap=200)  # 400 vs 800 chars
    text = "a" * 800
    with _timeout(5):
        chunks = _chunk_by_tokens(text, _paper(), cfg)
    # With proper guard: ~3 chunks. Without: 800.
    assert len(chunks) <= 10, f"thrash detected: {len(chunks)} chunks"


def test_long_text_produces_multiple_overlapping_chunks():
    """Sanity check: a normal multi-chunk text still produces the expected
    number of overlapping chunks."""
    text = "a" * 12000  # ~3 chunks worth at default config
    chunks = _chunk_by_tokens(text, _paper(), ChunkConfig())
    assert 2 <= len(chunks) <= 6, f"expected 2-6 chunks, got {len(chunks)}"


def test_chunk_text_strictly_advances():
    """Every chunk's content should differ from the previous (the
    fix-and-break ensures no identical-tail duplication).
    Uses text with a unique marker at each chunk boundary so identical
    content cannot arise from legitimate overlap."""
    # Build text where each 4000-char window is unique:
    # first 4000 chars are "a"s, next 4000 are "b"s, next 4000 are "c"s.
    text = "a" * 4000 + "b" * 4000 + "c" * 4000
    chunks = _chunk_by_tokens(text, _paper(), ChunkConfig())
    seen = set()
    for c in chunks:
        assert c.text not in seen, "duplicate chunk text — thrash"
        seen.add(c.text)
