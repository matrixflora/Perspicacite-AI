"""Tests for ``perspicacite.pipeline.download.youtube``.

Covers URL detection across surface forms, transcript markdown
rendering, LLM-correction graceful fallback (bad output never blocks
ingest), and the empty-snippets error path.
"""
from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.download.youtube import (
    _coalesce_snippets,
    _format_timestamp,
    _llm_correct_transcript,
    extract_video_id,
    fetch_youtube_transcript,
    is_youtube_url,
)


# ---------------------------------------------------------------------------
# URL detection across all surface forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=mhngGqJv7qw", "mhngGqJv7qw"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/AbCdEfGhIjK", "AbCdEfGhIjK"),
    ("https://www.youtube.com/embed/AbCdEfGhIjK", "AbCdEfGhIjK"),
    ("https://m.youtube.com/watch?v=AbCdEfGhIjK", "AbCdEfGhIjK"),
    ("youtube.com/watch?v=AbCdEfGhIjK", "AbCdEfGhIjK"),
])
def test_extract_video_id_accepts_known_surface_forms(url, expected):
    assert extract_video_id(url) == expected
    assert is_youtube_url(url) is True


@pytest.mark.parametrize("url", [
    "https://example.com/watch?v=xyz",
    "https://vimeo.com/12345",
    "https://github.com/owner/repo",
    "",
    "not a url",
])
def test_extract_video_id_rejects_non_youtube(url):
    assert extract_video_id(url) is None
    assert is_youtube_url(url) is False


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_format_timestamp_short_under_one_hour():
    assert _format_timestamp(0) == "00:00"
    assert _format_timestamp(63.5) == "01:03"


def test_format_timestamp_long_with_hours():
    assert _format_timestamp(3725) == "1:02:05"


def test_coalesce_groups_within_window():
    """Snippets within the 30s window join; the next block opens at
    a snippet whose start > previous-block-start + 30s."""
    class Snip:
        def __init__(self, start, text):
            self.start = start
            self.text = text
    snippets = [
        Snip(0.0, "first segment"),
        Snip(5.0, "second segment"),
        Snip(28.0, "still within window"),
        Snip(35.0, "new block starts here"),
        Snip(50.0, "still in second block"),
    ]
    out = _coalesce_snippets(snippets, merge_window_seconds=30.0)
    assert len(out) == 2
    assert out[0][0] == 0.0
    assert "first segment" in out[0][1]
    assert "still within window" in out[0][1]
    assert out[1][0] == 35.0
    assert "still in second block" in out[1][1]


def test_coalesce_handles_empty_input():
    assert _coalesce_snippets([]) == []


# ---------------------------------------------------------------------------
# LLM correction — graceful fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_correction_succeeds_when_line_count_matches():
    """Happy path: LLM returns the same number of timestamped lines.
    Original text is replaced with corrected text."""
    class FakeLLM:
        async def complete(self, messages, **kw):
            # Return exactly 2 lines with the original timestamps
            return (
                "[00:00] First corrected segment.\n"
                "[00:30] Second corrected segment."
            )

    blocks = [(0.0, "first uncorrected"), (30.0, "second uncorrected")]
    out = await _llm_correct_transcript(
        title="Test Video", author_name="Channel",
        blocks=blocks, llm_client=FakeLLM(),
    )
    assert len(out) == 2
    assert out[0][1] == "First corrected segment."
    assert out[1][1] == "Second corrected segment."


@pytest.mark.asyncio
async def test_llm_correction_falls_through_on_line_count_mismatch():
    """If the LLM hallucinates extra lines or drops some, we must NOT
    silently corrupt the transcript — keep originals for that chunk."""
    class FakeLLM:
        async def complete(self, messages, **kw):
            return "[00:00] Only one line returned for two input blocks"

    blocks = [(0.0, "first original"), (30.0, "second original")]
    out = await _llm_correct_transcript(
        title="T", author_name="C",
        blocks=blocks, llm_client=FakeLLM(),
    )
    assert out == blocks  # unchanged


@pytest.mark.asyncio
async def test_llm_correction_falls_through_on_llm_exception():
    """LLM crash should not abort transcript ingest — keep originals."""
    class CrashyLLM:
        async def complete(self, messages, **kw):
            raise RuntimeError("rate-limited")

    blocks = [(0.0, "keep this"), (30.0, "and this")]
    out = await _llm_correct_transcript(
        title="T", author_name="C",
        blocks=blocks, llm_client=CrashyLLM(),
    )
    assert out == blocks


# ---------------------------------------------------------------------------
# fetch_youtube_transcript: end-to-end (mocked network + transcript api)
# ---------------------------------------------------------------------------


def _install_fake_youtube_api(monkeypatch, snippets):
    """Helper: monkeypatch ``youtube_transcript_api`` with a deterministic
    fake that returns the given snippets."""
    class FakeFetched:
        pass
    fetched = FakeFetched()
    fetched.snippets = snippets

    class FakeApi:
        def fetch(self, vid):
            return fetched

    import sys
    import types
    fake_mod = types.ModuleType("youtube_transcript_api")
    fake_mod.YouTubeTranscriptApi = FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_mod)


class _FakeSnip:
    def __init__(self, text, start):
        self.text = text
        self.start = start


@pytest.mark.asyncio
async def test_fetch_youtube_transcript_renders_markdown(respx_mock, monkeypatch):
    """Full path: oEmbed metadata + mocked transcript API → markdown."""
    respx_mock.get(url__regex=r"https://www\.youtube\.com/oembed.*").mock(
        return_value=httpx.Response(200, json={
            "title": "My Cool Talk",
            "author_name": "Cool Channel",
        })
    )
    _install_fake_youtube_api(monkeypatch, [
        _FakeSnip("Hello and welcome", 0.0),
        _FakeSnip("to the talk", 5.0),
        _FakeSnip("In the next part", 35.0),
    ])

    async with httpx.AsyncClient() as http:
        md, title = await fetch_youtube_transcript(
            "https://youtu.be/AbCdEfGhIjK",
            http_client=http,
            llm_client=None,  # skip correction for deterministic test
        )
    assert title == "My Cool Talk"
    assert "# My Cool Talk" in md
    assert "**Channel:** Cool Channel" in md
    assert "[00:00]" in md
    assert "Hello and welcome to the talk" in md
    assert "[00:35]" in md
    assert "In the next part" in md


@pytest.mark.asyncio
async def test_uncorrected_transcript_carries_warning_header(
    respx_mock, monkeypatch,
):
    """Default (``correct_with_llm=False``) must prepend the
    ``⚠️ Auto-generated`` warning + one-sentence context, so chunks
    flowing into the KB inherit the "may be garbled" flag."""
    respx_mock.get(url__regex=r"https://www\.youtube\.com/oembed.*").mock(
        return_value=httpx.Response(200, json={
            "title": "MS/MS Library Cleanup",
            "author_name": "Dorrestein Lab",
        })
    )
    _install_fake_youtube_api(monkeypatch, [
        _FakeSnip("structure mast example", 0.0),
    ])
    async with httpx.AsyncClient() as http:
        md, _t = await fetch_youtube_transcript(
            "https://youtu.be/AbCdEfGhIjK",
            http_client=http,
            llm_client=None,
            correct_with_llm=False,
        )
    assert "Auto-generated YouTube transcript" in md
    assert "MS/MS Library Cleanup" in md
    assert "Dorrestein Lab" in md
    # Auto-captions get the alarming emoji prefix; corrected captions
    # do not.
    assert "⚠️" in md or "Auto-generated" in md


@pytest.mark.asyncio
async def test_corrected_transcript_uses_softer_note(
    respx_mock, monkeypatch,
):
    """When ``correct_with_llm=True`` AND the LLM actually changed
    something, the header switches to the milder
    ``LLM-corrected auto-captions`` form."""
    respx_mock.get(url__regex=r"https://www\.youtube\.com/oembed.*").mock(
        return_value=httpx.Response(200, json={
            "title": "Talk",
            "author_name": "Channel",
        })
    )
    _install_fake_youtube_api(monkeypatch, [
        _FakeSnip("strukshur mast", 0.0),
    ])

    class FakeLLM:
        async def complete(self, messages, **kw):
            return "[00:00] StructureMASST"

    async with httpx.AsyncClient() as http:
        md, _t = await fetch_youtube_transcript(
            "https://youtu.be/AbCdEfGhIjK",
            http_client=http,
            llm_client=FakeLLM(),
            correct_with_llm=True,
        )
    assert "LLM-corrected auto-captions" in md
    assert "StructureMASST" in md
    assert "⚠️" not in md  # softer note has no warning emoji


@pytest.mark.asyncio
async def test_correct_with_llm_false_does_not_call_llm(
    respx_mock, monkeypatch,
):
    """Cost guard: with ``correct_with_llm=False`` (default), the LLM
    client's complete() must not be called even when one is provided."""
    respx_mock.get(url__regex=r"https://www\.youtube\.com/oembed.*").mock(
        return_value=httpx.Response(200, json={"title": "T", "author_name": "C"})
    )
    _install_fake_youtube_api(monkeypatch, [_FakeSnip("hi", 0.0)])

    call_count = {"n": 0}
    class CountingLLM:
        async def complete(self, messages, **kw):
            call_count["n"] += 1
            return "[00:00] hi"

    async with httpx.AsyncClient() as http:
        await fetch_youtube_transcript(
            "https://youtu.be/AbCdEfGhIjK",
            http_client=http,
            llm_client=CountingLLM(),
            correct_with_llm=False,
        )
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_fetch_youtube_raises_on_non_youtube_url():
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="not a YouTube"):
            await fetch_youtube_transcript(
                "https://example.com/video",
                http_client=http,
            )


@pytest.mark.asyncio
async def test_fetch_youtube_raises_on_transcript_unavailable(
    respx_mock, monkeypatch,
):
    """Videos with disabled captions / region-blocks / live-streams
    surface a clear ValueError so callers can fall back."""
    respx_mock.get(url__regex=r"https://www\.youtube\.com/oembed.*").mock(
        return_value=httpx.Response(200, json={"title": "X", "author_name": "Y"})
    )

    class FakeApi:
        def fetch(self, vid):
            raise RuntimeError("Subtitles are disabled for this video")

    import sys
    import types
    fake_mod = types.ModuleType("youtube_transcript_api")
    fake_mod.YouTubeTranscriptApi = FakeApi
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_mod)

    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="transcript unavailable"):
            await fetch_youtube_transcript(
                "https://youtu.be/AbCdEfGhIjK",
                http_client=http,
            )
