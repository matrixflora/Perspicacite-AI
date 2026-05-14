"""Defense-in-depth copyright filter on synthesis output.

The system prompt already nudges the LLM to paraphrase rather than quote
verbatim (see ``rag/utils.get_system_prompt`` + per-mode prompts). This
module adds a runtime check that compares the LLM's answer against the
retrieved source chunks and flags / rewrites long verbatim copies the
LLM may still emit.

Two-tier approach:

1. **Detector** — ``find_verbatim_overlaps(answer, sources, min_ngram=8)``
   returns the list of contiguous word-ngrams shared between the answer
   and any source chunk (default n=8 words — short enough to catch
   problematic copying, long enough to ignore incidental matches like
   "in this paper we demonstrate that").

2. **Filter actions** — ``CopyrightFilter`` orchestrates a configurable
   response for each detection:
   - ``"log"``: warn + return the answer unchanged (always-on default)
   - ``"quote"``: wrap each verbatim span in quotation marks + cite
     (uses local string ops, no LLM call)
   - ``"rewrite"``: ask a cheap LLM to paraphrase the flagged spans
     (one LLM call total, regardless of how many spans)
   - ``"strip"``: replace each verbatim span with ``[content paraphrased]``

The detector is intentionally simple (sliding word ngram + set
intersection) so it stays fast and predictable on long answers. The
filter does not run when sources are empty (no risk).

Performance note: O(N+M) where N = answer length and M = total source
length. For typical RAG answers (1-3k words) and 3-10 source chunks of
~4 KB each, this runs in tens of milliseconds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.rag.copyright_filter")


@dataclass
class VerbatimMatch:
    """A run of ≥ min_ngram words that appears verbatim in both texts."""

    answer_span: tuple[int, int]  # (start_char, end_char) in answer
    text: str
    source_index: int  # index into the sources list provided to the detector
    source_title: str | None
    word_count: int


# Cheap word tokenizer: lowercase, strip punctuation at ends, split on
# whitespace. We compare with this tokenization but record character
# spans in the original answer so the filter can rewrite spans cleanly.
_WORD_RE = re.compile(r"\S+")


def _normalize_word(w: str) -> str:
    """Lowercase + strip leading/trailing punctuation for matching."""
    return w.strip(" .,;:!?\"'()[]{}").lower()


def _tokenize_with_offsets(text: str) -> list[tuple[str, int, int]]:
    """Return [(normalized_word, start, end), ...] for each word."""
    out: list[tuple[str, int, int]] = []
    for m in _WORD_RE.finditer(text):
        nw = _normalize_word(m.group(0))
        if nw:
            out.append((nw, m.start(), m.end()))
    return out


def find_verbatim_overlaps(
    answer: str,
    sources: list[dict[str, Any]],
    *,
    min_ngram: int = 8,
) -> list[VerbatimMatch]:
    """Return all contiguous word runs of length ≥ ``min_ngram`` that
    appear in ``answer`` and in at least one source's text.

    ``sources`` is a list of dicts with at least ``text`` (the chunk
    body); ``title`` is used for attribution but optional. Overlapping
    matches are coalesced (the longest run wins).
    """
    if not answer or not sources or min_ngram < 1:
        return []

    # Build set of source ngrams keyed by tuple of normalized words.
    src_ngrams: dict[tuple[str, ...], int] = {}
    for idx, s in enumerate(sources):
        text = (s or {}).get("text") or (s or {}).get("chunk_text") or ""
        if not text or not isinstance(text, str):
            continue
        s_tokens = [w for (w, _, _) in _tokenize_with_offsets(text)]
        for i in range(len(s_tokens) - min_ngram + 1):
            key = tuple(s_tokens[i : i + min_ngram])
            # Keep first occurrence's source index for attribution
            src_ngrams.setdefault(key, idx)

    if not src_ngrams:
        return []

    # Slide over answer tokens, find longest match per starting position
    a_tokens = _tokenize_with_offsets(answer)
    matches: list[VerbatimMatch] = []
    i = 0
    while i <= len(a_tokens) - min_ngram:
        key = tuple(w for (w, _, _) in a_tokens[i : i + min_ngram])
        if key in src_ngrams:
            # Extend the match as far as possible (keeps growing while
            # the next word is also in a source chunk at this position).
            src_idx = src_ngrams[key]
            src_text = sources[src_idx].get("text") or sources[src_idx].get("chunk_text") or ""
            s_tokens = [w for (w, _, _) in _tokenize_with_offsets(src_text)]
            # Find the start position in source
            best_len = 0
            for s_start in range(len(s_tokens) - min_ngram + 1):
                if tuple(s_tokens[s_start : s_start + min_ngram]) != key:
                    continue
                # Try to extend
                extend = 0
                while (
                    i + min_ngram + extend < len(a_tokens)
                    and s_start + min_ngram + extend < len(s_tokens)
                    and a_tokens[i + min_ngram + extend][0]
                    == s_tokens[s_start + min_ngram + extend]
                ):
                    extend += 1
                if min_ngram + extend > best_len:
                    best_len = min_ngram + extend
            end_idx = i + best_len - 1
            start_char = a_tokens[i][1]
            end_char = a_tokens[end_idx][2]
            matches.append(
                VerbatimMatch(
                    answer_span=(start_char, end_char),
                    text=answer[start_char:end_char],
                    source_index=src_idx,
                    source_title=sources[src_idx].get("title"),
                    word_count=best_len,
                )
            )
            i = end_idx + 1
        else:
            i += 1

    return matches


def quote_and_cite(
    answer: str,
    matches: list[VerbatimMatch],
) -> str:
    """Wrap each verbatim span in “…” and append a per-source citation.

    Spans are processed back-to-front so character indices stay stable.
    """
    if not matches:
        return answer
    sorted_m = sorted(matches, key=lambda m: -m.answer_span[0])
    out = answer
    for m in sorted_m:
        s, e = m.answer_span
        body = out[s:e]
        cite = f" [{m.source_title or 'source'}]" if m.source_title else ""
        out = out[:s] + f'“{body}”{cite}' + out[e:]
    return out


def strip_spans(
    answer: str,
    matches: list[VerbatimMatch],
    *,
    replacement: str = "[content paraphrased]",
) -> str:
    """Replace each verbatim span with ``[content paraphrased]``.

    Useful when you can't run an extra LLM call but still need to
    eliminate the verbatim copy.
    """
    if not matches:
        return answer
    sorted_m = sorted(matches, key=lambda m: -m.answer_span[0])
    out = answer
    for m in sorted_m:
        s, e = m.answer_span
        out = out[:s] + replacement + out[e:]
    return out


_REWRITE_PROMPT = """You are revising a research synthesis to remove
verbatim copies from copyrighted source papers. The following sentences
from the synthesis match source-paper text word-for-word for at least
{min_words} consecutive words:

{spans}

Rewrite the synthesis so that those sentences express the same factual
content in different words. Keep all citations, structure, and other
content exactly as-is. Return only the revised synthesis text — no
preamble, no commentary.

---
Synthesis to revise:

{answer}"""


async def rewrite_verbatim_spans(
    answer: str,
    matches: list[VerbatimMatch],
    *,
    llm_client: Any,
    model: str = "claude-haiku-4-5",
    provider: str = "anthropic",
) -> str:
    """Ask a cheap LLM to paraphrase the flagged spans, leaving the rest
    of the answer intact.

    One LLM call regardless of how many spans there are.
    """
    if not matches:
        return answer
    spans_block = "\n".join(
        f'  - "{m.text}"  (source: {m.source_title or "unknown"})'
        for m in matches
    )
    min_words = min(m.word_count for m in matches)
    prompt = _REWRITE_PROMPT.format(
        min_words=min_words, spans=spans_block, answer=answer,
    )
    try:
        revised = await llm_client.complete(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            provider=provider,
            max_tokens=4000,
            temperature=0.4,
            stage="copyright.rewrite",
        )
        if revised and isinstance(revised, str) and revised.strip():
            return revised.strip()
    except Exception as exc:
        logger.info("copyright_rewrite_failed_falling_back", error=str(exc))
    # Fall back to local strip if the LLM call fails
    return strip_spans(answer, matches)


class CopyrightFilter:
    """Configurable post-filter for synthesis output.

    Construct once per RAG mode (or once at startup) and call
    ``apply(answer, sources)``. Reads its config from the
    ``CopyrightFilterConfig`` (or any duck-typed object) passed in.

    Modes:
    - ``log``  — warn only, return answer unchanged
    - ``quote``— wrap spans in quotes + citation, no LLM call
    - ``strip``— replace spans with ``[content paraphrased]``
    - ``rewrite``— LLM-rewrite (requires llm_client)
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        action: str = "log",
        min_ngram: int = 8,
        llm_client: Any = None,
        rewrite_model: str = "claude-haiku-4-5",
        rewrite_provider: str = "anthropic",
    ) -> None:
        self.enabled = enabled
        self.action = action
        self.min_ngram = max(3, int(min_ngram))
        self.llm_client = llm_client
        self.rewrite_model = rewrite_model
        self.rewrite_provider = rewrite_provider

    async def apply(
        self,
        answer: str,
        sources: list[dict[str, Any]],
    ) -> str:
        """Run the configured action and return the (possibly revised)
        answer. Always logs a structured event with the match count for
        observability — even when action="log"."""
        if not self.enabled or not answer or not sources:
            return answer
        matches = find_verbatim_overlaps(
            answer, sources, min_ngram=self.min_ngram,
        )
        if not matches:
            return answer
        logger.warning(
            "copyright_verbatim_detected",
            matches=len(matches),
            total_words=sum(m.word_count for m in matches),
            min_ngram=self.min_ngram,
            action=self.action,
        )
        if self.action == "quote":
            return quote_and_cite(answer, matches)
        if self.action == "strip":
            return strip_spans(answer, matches)
        if self.action == "rewrite" and self.llm_client is not None:
            return await rewrite_verbatim_spans(
                answer, matches,
                llm_client=self.llm_client,
                model=self.rewrite_model,
                provider=self.rewrite_provider,
            )
        # default / "log" / unknown action / rewrite without llm_client
        return answer
