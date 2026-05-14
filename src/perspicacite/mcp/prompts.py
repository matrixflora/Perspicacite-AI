"""Canned MCP prompts (Wave 5.2).

Pure string-builders. Each returns a list of ``{"role": "user",
"content": "..."}`` messages that FastMCP surfaces in Claude Desktop's
"/" menu and similar clients. The actual work happens when the model
executes the tool calls embedded in the generated message.
"""
from __future__ import annotations

from typing import Any


def _msg(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def literature_review(
    topic: str,
    kb_name: str | None = None,
    max_papers: int = 30,
) -> list[dict[str, Any]]:
    """Run a literature review on a topic.

    If ``kb_name`` is given, search that KB. Otherwise call
    ``search_literature`` across configured databases.
    """
    if kb_name:
        retrieval = (
            f"Use `search_knowledge_base` against the `{kb_name}` KB to find papers "
            f"about: {topic}. Limit to {max_papers} top hits."
        )
    else:
        retrieval = (
            f"Use `search_literature` to find up to {max_papers} papers on: {topic}. "
            "Pull from at least Crossref + OpenAlex."
        )
    return [
        _msg(
            f"I'd like a literature review on: **{topic}**.\n\n"
            f"{retrieval}\n\n"
            "Then call `generate_report` with `synthesis_style=\"literature_review\"`. "
            "Cover scope, methods, key findings, gaps, and recommended next reads. "
            "Cite every claim with the paper's DOI."
        )
    ]


def compare_papers(
    paper_a: str,
    paper_b: str,
    kb_name: str | None = None,
) -> list[dict[str, Any]]:
    """Side-by-side comparison of two papers."""
    extra = f" Use KB `{kb_name}` as context if helpful." if kb_name else ""
    return [
        _msg(
            f"Compare two papers side-by-side:\n"
            f"- A: `{paper_a}`\n"
            f"- B: `{paper_b}`\n\n"
            f"Fetch each via `get_paper_content`, then produce a table with rows for:\n"
            f"  research question, methods, dataset, key findings, limitations, "
            f"reproducibility.\n"
            f"Close with a 2-paragraph synthesis of where the papers agree, "
            f"where they diverge, and which holds up better.{extra}"
        )
    ]


def summarize_kb(kb_name: str, max_papers: int = 50) -> list[dict[str, Any]]:
    """Five-paragraph summary of a KB."""
    return [
        _msg(
            f"Summarize the knowledge base `{kb_name}` (up to {max_papers} papers). "
            "First call `search_knowledge_base` with a broad query to pull a "
            "representative sample, then produce a 5-paragraph summary covering:\n"
            "  1. Scope and time range of papers in the KB.\n"
            "  2. Top 3 thematic clusters (with paper counts each).\n"
            "  3. Methodological trends.\n"
            "  4. Open questions / visible gaps.\n"
            "  5. Three recommended next reads with DOIs."
        )
    ]


def ingest_dois(kb_name: str, dois: list[str]) -> list[dict[str, Any]]:
    """Ingest a list of DOIs into a KB."""
    doi_lines = "\n".join(f"  - {d}" for d in dois)
    return [
        _msg(
            f"Add these DOIs to KB `{kb_name}`:\n{doi_lines}\n\n"
            f"Call `add_dois_to_kb` with kb_name=`{kb_name}` and the DOI list. "
            "Then list per-DOI status: added / skipped (duplicate) / failed (with reason)."
        )
    ]


def screen_topic(
    topic: str,
    kb_name: str,
    threshold: float = 0.6,
) -> list[dict[str, Any]]:
    """Screen a KB for papers relevant to a topic above a confidence threshold."""
    return [
        _msg(
            f"Screen KB `{kb_name}` for relevance to: **{topic}**.\n"
            f"Call `screen_papers` with topic=`{topic}`, kb_name=`{kb_name}`, "
            f"threshold={threshold}.\n"
            "Report the matching papers ranked by score, with a one-line rationale each."
        )
    ]
