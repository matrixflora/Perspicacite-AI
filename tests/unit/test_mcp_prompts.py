"""Tests for MCP canned-workflow prompts (Wave 5.2)."""
from __future__ import annotations

from perspicacite.mcp.prompts import (
    compare_papers,
    ingest_dois,
    literature_review,
    screen_topic,
    summarize_kb,
)


def _content(msgs):
    """Return all message contents concatenated."""
    out = []
    for m in msgs:
        c = m["content"] if isinstance(m, dict) else m.content
        out.append(c if isinstance(c, str) else c.text)
    return "\n".join(out)


def test_literature_review_prompt_interpolates_args():
    msgs = literature_review(topic="exoplanet biosignatures", kb_name="astro", max_papers=20)
    body = _content(msgs)
    assert "exoplanet biosignatures" in body
    assert "astro" in body
    assert "20" in body


def test_compare_papers_prompt_includes_both_ids():
    msgs = compare_papers(paper_a="10.1/x", paper_b="10.2/y")
    body = _content(msgs)
    assert "10.1/x" in body and "10.2/y" in body


def test_summarize_kb_prompt_requires_kb_name():
    msgs = summarize_kb(kb_name="astro")
    body = _content(msgs)
    assert "astro" in body
    assert "summary" in body.lower() or "summarize" in body.lower()


def test_ingest_dois_prompt_renders_doi_list():
    msgs = ingest_dois(kb_name="astro", dois=["10.1/a", "10.2/b"])
    body = _content(msgs)
    assert "10.1/a" in body and "10.2/b" in body and "astro" in body


def test_screen_topic_prompt_threshold_appears_in_body():
    msgs = screen_topic(topic="black holes", kb_name="astro", threshold=0.75)
    body = _content(msgs)
    assert "0.75" in body
    assert "black holes" in body
