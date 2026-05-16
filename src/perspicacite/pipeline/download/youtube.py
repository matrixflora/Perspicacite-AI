"""YouTube transcript ingest as a Markdown document.

Public videos only. No auth, no API key. Pulls captions via
``youtube-transcript-api`` (manual when present, else auto-generated)
and fetches metadata via YouTube's public oEmbed endpoint.

The returned Markdown carries the video title as the H1, channel +
duration + URL as a short header block, and the transcript body with
``[mm:ss]`` timestamp prefixes per ~30-second segment. Format is
chosen to flow into the existing heading-aware Markdown chunker
without special handling — search-by-quote retrieves the timestamped
line, telling the reader *where in the video* the answer lives.

When a cheap LLM client is provided, transcripts are post-processed:
auto-generated captions for technical talks routinely garble domain
jargon ("MS/MS" → "miss-miss", "desferrioxamine" → "death-furry-oxide
mean"). The LLM gets the video title as context and is asked to fix
obvious mis-transcriptions while preserving timestamps and meaning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger(__name__)


_YOUTUBE_URL_RE = re.compile(
    r"""(?:https?://)?
        (?:www\.|m\.)?
        (?:
            youtube\.com/(?:watch\?v=|shorts/|embed/|v/)
          | youtu\.be/
        )
        ([A-Za-z0-9_-]{11})
        (?:[?&#].*)?$""",
    re.VERBOSE,
)


def is_youtube_url(url: str) -> bool:
    """True if ``url`` is a public YouTube video URL."""
    return bool(_YOUTUBE_URL_RE.match((url or "").strip()))


def extract_video_id(url: str) -> str | None:
    """Pull the 11-character YouTube video id from any of its surface forms.

    Handles ``youtube.com/watch?v=XXXX``, ``youtu.be/XXXX``,
    ``youtube.com/shorts/XXXX``, ``youtube.com/embed/XXXX``, with or
    without scheme, www, or query params.
    """
    m = _YOUTUBE_URL_RE.match((url or "").strip())
    return m.group(1) if m else None


@dataclass
class YouTubeMetadata:
    title: str
    author_name: str
    url: str
    duration_seconds: int | None = None


async def _fetch_oembed_metadata(
    video_id: str, *, http_client: httpx.AsyncClient,
) -> YouTubeMetadata:
    """Cheap metadata fetch via YouTube's public oEmbed endpoint.

    Returns ``YouTubeMetadata``; on failure raises ``httpx.HTTPError``.
    The duration is not in oEmbed — falls back to None. Callers can
    enrich from the transcript's last-segment timestamp.
    """
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    r = await http_client.get(
        "https://www.youtube.com/oembed",
        params={"url": canonical, "format": "json"},
        timeout=15.0,
    )
    r.raise_for_status()
    data = r.json() or {}
    return YouTubeMetadata(
        title=data.get("title") or canonical,
        author_name=data.get("author_name") or "",
        url=canonical,
    )


def _format_timestamp(seconds: float) -> str:
    """``93.7`` -> ``"01:33"``, ``3725`` -> ``"1:02:05"``."""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _coalesce_snippets(
    snippets: list[Any], merge_window_seconds: float = 30.0,
) -> list[tuple[float, str]]:
    """Group consecutive transcript snippets into ~30s chunks so the
    Markdown output isn't a wall of 3-second fragments.

    Returns ``[(start_seconds, joined_text), ...]``. Each tuple becomes
    one ``[mm:ss] ...`` line in the rendered Markdown.
    """
    if not snippets:
        return []
    out: list[tuple[float, list[str]]] = []
    bucket_start: float | None = None
    bucket: list[str] = []
    for sn in snippets:
        start = float(getattr(sn, "start", 0.0) or 0.0)
        text = (getattr(sn, "text", "") or "").strip()
        if not text:
            continue
        if bucket_start is None:
            bucket_start = start
            bucket = [text]
            continue
        if start - bucket_start <= merge_window_seconds:
            bucket.append(text)
        else:
            out.append((bucket_start, list(bucket)))
            bucket_start = start
            bucket = [text]
    if bucket_start is not None and bucket:
        out.append((bucket_start, bucket))
    # Tidy: collapse whitespace, join with single spaces
    return [
        (t, re.sub(r"\s+", " ", " ".join(txts)).strip())
        for t, txts in out
    ]


async def _llm_correct_transcript(
    *,
    title: str,
    author_name: str,
    blocks: list[tuple[float, str]],
    llm_client: Any,
    chunk_chars: int = 3000,
) -> list[tuple[float, str]]:
    """Send the transcript through a cheap LLM with the video title +
    channel as context, asking only for mis-transcription fixes.

    The model is instructed to preserve timestamps verbatim — we send
    each block with its ``[mm:ss]`` marker baked in, parse the
    response, and pair the corrected lines back to their original
    timestamps. On any failure (timeout, parse, bad output length),
    returns the original blocks unchanged with a log line so ingest
    never blocks on the LLM step.
    """
    if not blocks:
        return blocks

    system = (
        "You correct auto-generated YouTube captions for a technical "
        "research video. ONLY fix obvious mis-transcriptions (proper "
        "nouns, domain jargon, repeated stutters). Preserve meaning, "
        "preserve every ``[mm:ss]`` timestamp marker verbatim, preserve "
        "line order. Do not summarize. Do not add headings. Output the "
        "same number of lines you received, each starting with its "
        "timestamp marker."
    )
    context = (
        f"Video title: {title}\n"
        f"Channel: {author_name}\n\n"
        "Apply the title and channel as context for correcting jargon."
    )

    corrected: list[tuple[float, str]] = []
    cursor = 0
    while cursor < len(blocks):
        # Pack blocks until we hit chunk_chars
        sub: list[tuple[float, str]] = []
        char_budget = 0
        while cursor < len(blocks) and char_budget < chunk_chars:
            sub.append(blocks[cursor])
            char_budget += len(blocks[cursor][1]) + 12
            cursor += 1
        body = "\n".join(
            f"[{_format_timestamp(t)}] {txt}" for t, txt in sub
        )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"{context}\n\nTranscript chunk:\n\n{body}",
            },
        ]
        try:
            raw = await llm_client.complete(
                messages=messages, stage="screening",
            )
            text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        except Exception as exc:
            logger.info(
                "youtube_transcript_llm_failed",
                error=str(exc)[:200],
                chunk_blocks=len(sub),
            )
            corrected.extend(sub)
            continue

        # Parse: extract ``[mm:ss] text`` lines, pair to the original
        # timestamps in order. If line count diverges (model added or
        # dropped lines), bail out for this chunk and keep originals.
        parsed: list[str] = []
        for line in (text or "").splitlines():
            m = re.match(r"^\s*\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s+(.*)$", line)
            if m:
                cleaned = m.group(1).strip()
                if cleaned:
                    parsed.append(cleaned)
        if len(parsed) != len(sub):
            logger.info(
                "youtube_transcript_llm_line_count_mismatch",
                expected=len(sub), got=len(parsed),
            )
            corrected.extend(sub)
            continue
        for (t, _orig), new_text in zip(sub, parsed, strict=True):
            corrected.append((t, new_text))
    return corrected


async def fetch_youtube_transcript(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    llm_client: Any | None = None,
    correct_with_llm: bool = False,
) -> tuple[str, str]:
    """Fetch a public YouTube video's transcript as Markdown.

    Returns ``(markdown, title)``. Raises ``ValueError`` for non-YouTube
    URLs or videos with no captions; raises ``httpx.HTTPError`` for
    network errors on the metadata fetch.

    LLM correction is **opt-in**. A 1-hour talk yields ~50K chars of
    auto-captions, which is ~$0.10-0.50 to LLM-clean depending on the
    model — multiplied across a batch this matters. Default behavior
    is: ship the raw auto-captions with a prominent warning header
    that says "this may contain mis-transcriptions" plus one sentence
    of video context (title + channel), so downstream chunks carry
    that flag with them and the LLM consuming the KB knows to treat
    them probabilistically.

    Pass ``correct_with_llm=True`` to enable the correction pass.
    Requires a valid ``llm_client``. On any correction failure (rate
    limit, parse error, auth) the raw transcript flows through with
    the warning header.
    """
    vid = extract_video_id(url)
    if not vid:
        raise ValueError(f"not a YouTube URL: {url!r}")

    try:
        from youtube_transcript_api import (  # type: ignore[import-not-found]
            YouTubeTranscriptApi,
        )
    except ImportError as exc:
        raise ImportError(
            "youtube-transcript-api not installed. "
            "Install with: uv pip install -e \".[youtube-ingest]\""
        ) from exc

    client = http_client or httpx.AsyncClient(
        timeout=30.0, follow_redirects=True,
    )
    should_close = http_client is None
    try:
        metadata = await _fetch_oembed_metadata(vid, http_client=client)

        # youtube-transcript-api is synchronous + thread-blocking;
        # offload to a thread so we don't stall the event loop.
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            fetched = await loop.run_in_executor(
                None,
                lambda: YouTubeTranscriptApi().fetch(vid),
            )
        except Exception as exc:
            raise ValueError(
                f"YouTube transcript unavailable for {vid}: {exc}"
            ) from exc
        snippets = list(getattr(fetched, "snippets", []) or list(fetched))
        if not snippets:
            raise ValueError(f"YouTube video {vid} has no transcript snippets")

        blocks = _coalesce_snippets(snippets)
        last_t = blocks[-1][0] if blocks else 0.0
        metadata.duration_seconds = int(last_t) + 30  # estimate

        corrected = False
        if correct_with_llm and llm_client is not None:
            new_blocks = await _llm_correct_transcript(
                title=metadata.title,
                author_name=metadata.author_name,
                blocks=blocks,
                llm_client=llm_client,
            )
            # _llm_correct_transcript returns originals on failure, so
            # we can't tell "succeeded" by identity — but we can tell
            # "actually changed" by comparing block text. Treat any
            # difference as evidence the correction pass ran.
            if any(
                a[1] != b[1]
                for a, b in zip(blocks, new_blocks, strict=True)
            ):
                corrected = True
            blocks = new_blocks

        # Warning header. When the transcript is uncorrected (which is
        # the default), downstream chunks need to carry the "may be
        # garbled" signal — title + channel give the LLM consuming the
        # KB enough context to interpret jargon probabilistically.
        if corrected:
            caption_note = (
                "> **Note:** LLM-corrected auto-captions for context: "
                f"\"{metadata.title}\""
                + (f" — {metadata.author_name}." if metadata.author_name else ".")
                + " Minor mis-transcriptions may remain."
            )
        else:
            caption_note = (
                "> **⚠️ Auto-generated YouTube transcript** — may contain "
                "mis-transcribed terms (proper nouns, jargon, technical "
                f"shorthand). Video context: \"{metadata.title}\""
                + (f" — {metadata.author_name}." if metadata.author_name else ".")
                + " Treat probabilistically when reasoning over chunks."
            )

        header = [
            f"# {metadata.title}",
            "",
            caption_note,
            "",
            f"**Channel:** {metadata.author_name}" if metadata.author_name else "",
            f"**Duration:** {_format_timestamp(metadata.duration_seconds or 0)}",
            f"**URL:** {metadata.url}",
            "",
            "## Transcript",
            "",
        ]
        body = "\n".join(
            f"[{_format_timestamp(t)}] {txt}" for t, txt in blocks
        )
        md = "\n".join(line for line in header if line is not None) + body + "\n"
        logger.info(
            "youtube_transcript_fetched",
            video_id=vid,
            title=metadata.title[:80],
            snippets=len(snippets),
            coalesced_blocks=len(blocks),
            llm_correction_requested=correct_with_llm,
            llm_correction_applied=corrected,
        )
        return md, metadata.title
    finally:
        if should_close:
            await client.aclose()
