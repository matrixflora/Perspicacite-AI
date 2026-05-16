"""Tests for the URL extractors (Priority 3)."""

import httpx
import pytest

from perspicacite.pipeline.download.url_extractors import (
    classify_url,
    extract_generic_html,
    extract_github,
    extract_openreview,
    extract_preprints_org,
    extract_url,
)


def test_classify_url():
    assert classify_url("https://github.com/langchain-ai/langgraph") == "github"
    assert classify_url("https://github.com/huggingface/smolagents.git") == "github"
    assert classify_url("https://openreview.net/forum?id=abc123") == "openreview"
    assert classify_url("https://www.preprints.org/manuscript/202311.0001/v1") == "preprints_org"
    assert classify_url("https://anthropic.com/research/agent-skills") == "generic"
    assert classify_url("") == "generic"


@pytest.mark.asyncio
async def test_extract_github_returns_repo_metadata(respx_mock):
    respx_mock.get("https://api.github.com/repos/langchain-ai/langgraph").mock(
        return_value=httpx.Response(200, json={
            "description": "Build resilient language agents as graphs.",
            "language": "Python",
            "default_branch": "main",
            "created_at": "2024-01-15T00:00:00Z",
            "topics": ["agents", "llm"],
        })
    )
    respx_mock.get("https://api.github.com/repos/langchain-ai/langgraph/readme").mock(
        return_value=httpx.Response(200, text="# LangGraph\n\nMain README content here.")
    )
    respx_mock.get("https://api.github.com/repos/langchain-ai/langgraph/contributors").mock(
        return_value=httpx.Response(200, json=[{"login": "alice"}, {"login": "bob"}])
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_github("https://github.com/langchain-ai/langgraph",
                                       http_client=http)
    assert paper["item_type"] == "computerProgram"
    assert paper["title"] == "langchain-ai/langgraph"
    assert paper["authors"] == ["alice", "bob"]
    assert "Build resilient language agents" in paper["abstract"]
    assert "LangGraph" in paper["abstract"]
    assert paper["programming_language"] == "Python"
    assert "agents" in paper["tags"]
    assert paper["repository"] == "GitHub"


@pytest.mark.asyncio
async def test_extract_openreview_returns_note_metadata(respx_mock):
    respx_mock.get("https://api.openreview.net/notes?id=evoprompt2024").mock(
        return_value=httpx.Response(200, json={
            "notes": [{
                "content": {
                    "title": "EvoPrompt: ...",
                    "authors": ["Q. Guo", "R. Wang"],
                    "abstract": "We connect LLMs with evolutionary algorithms.",
                    "pdf": "/pdf?id=evoprompt2024",
                    "venue": "ICLR 2024",
                },
            }],
        })
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_openreview(
            "https://openreview.net/forum?id=evoprompt2024", http_client=http,
        )
    assert paper["title"] == "EvoPrompt: ..."
    assert paper["authors"] == ["Q. Guo", "R. Wang"]
    assert paper["pdf_url"] == "https://openreview.net/pdf?id=evoprompt2024"
    assert paper["item_type"] == "preprint"
    assert paper["repository"] == "ICLR 2024"


@pytest.mark.asyncio
async def test_extract_openreview_v2_wrapped_values(respx_mock):
    """OpenReview API v2 wraps each field as {"value": ...}."""
    respx_mock.get("https://api.openreview.net/notes?id=v2note").mock(
        return_value=httpx.Response(200, json={
            "notes": [{
                "content": {
                    "title": {"value": "V2 wrapped"},
                    "abstract": {"value": "An abstract"},
                    "authors": {"value": ["X", "Y"]},
                    "pdf": {"value": "/pdf?id=v2note"},
                    "venue": {"value": "NeurIPS 2025"},
                },
            }],
        })
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_openreview(
            "https://openreview.net/forum?id=v2note", http_client=http,
        )
    assert paper["title"] == "V2 wrapped"
    assert paper["authors"] == ["X", "Y"]
    assert paper["abstract"] == "An abstract"


@pytest.mark.asyncio
async def test_extract_generic_html_mines_citation_tags(respx_mock):
    body = """
    <html><head>
      <title>Untitled fallback</title>
      <meta name="citation_title" content="Real Paper Title">
      <meta name="citation_author" content="Smith, John">
      <meta name="citation_author" content="Doe, Jane">
      <meta name="citation_publication_date" content="2025-03-15">
      <meta name="citation_doi" content="10.1234/abc">
      <meta name="citation_journal_title" content="Nat Mach Intell">
      <meta name="citation_pdf_url" content="https://example.org/x.pdf">
      <meta property="og:description" content="A paper about agents.">
    </head><body>...</body></html>
    """
    respx_mock.get("https://example.org/article/x").mock(
        return_value=httpx.Response(200, text=body,
                                      headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_generic_html(
            "https://example.org/article/x", http_client=http,
        )
    assert paper["title"] == "Real Paper Title"
    assert paper["authors"] == ["Smith, John", "Doe, Jane"]
    assert paper["doi"] == "10.1234/abc"
    assert paper["year"] == "2025"
    assert paper["journal"] == "Nat Mach Intell"
    assert paper["pdf_url"] == "https://example.org/x.pdf"
    assert paper["abstract"] == "A paper about agents."
    assert paper["item_type"] == "journalArticle"  # has DOI


@pytest.mark.asyncio
async def test_extract_generic_html_arxiv_id_constructs_doi(respx_mock):
    """Live-discovered (2026-05-16): arxiv.org pages emit
    ``citation_arxiv_id`` but not ``citation_doi``. The extractor must
    construct the standard arXiv DOI form ``10.48550/arXiv.<id>`` so the
    push pipeline routes the item as ``preprint`` instead of ``webpage``."""
    body = """
    <html><head>
      <title>arXiv:2510.09901</title>
      <meta name="citation_title" content="Autonomous Agents for Sci Discovery">
      <meta name="citation_author" content="Zhou, Lianhao">
      <meta name="citation_arxiv_id" content="2510.09901">
      <meta name="citation_pdf_url" content="https://arxiv.org/pdf/2510.09901">
    </head></html>
    """
    respx_mock.get("https://arxiv.org/abs/2510.09901").mock(
        return_value=httpx.Response(200, text=body,
                                      headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_generic_html(
            "https://arxiv.org/abs/2510.09901", http_client=http,
        )
    assert paper["doi"] == "10.48550/arXiv.2510.09901"
    assert paper["item_type"] == "preprint"
    assert paper["repository"] == "arXiv"
    assert paper["archive_id"] == "2510.09901"


@pytest.mark.asyncio
async def test_extract_generic_html_no_doi_falls_back_to_webpage(respx_mock):
    body = """
    <html><head>
      <title>Anthropic Research</title>
      <meta property="og:title" content="Building multi-agent research">
      <meta property="og:description" content="How we built it.">
    </head></html>
    """
    respx_mock.get("https://anthropic.com/research/multi-agent").mock(
        return_value=httpx.Response(200, text=body,
                                      headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_generic_html(
            "https://anthropic.com/research/multi-agent", http_client=http,
        )
    assert paper["title"] == "Building multi-agent research"
    assert paper["item_type"] == "webpage"
    assert paper["doi"] == ""
    assert paper["abstract"] == "How we built it."


@pytest.mark.asyncio
async def test_extract_preprints_org_marks_as_preprint(respx_mock):
    body = """
    <html><head>
      <meta name="citation_title" content="Building MCP-Native Ecosystems">
      <meta name="citation_journal_title" content="Preprints.org">
      <meta name="citation_pdf_url" content="https://preprints.org/pdf/abc">
    </head></html>
    """
    respx_mock.get("https://www.preprints.org/manuscript/202507.1951").mock(
        return_value=httpx.Response(200, text=body,
                                      headers={"content-type": "text/html"})
    )
    async with httpx.AsyncClient() as http:
        paper = await extract_preprints_org(
            "https://www.preprints.org/manuscript/202507.1951", http_client=http,
        )
    assert paper["item_type"] == "preprint"
    assert paper["repository"] == "Preprints.org"
    assert paper["ingest_format"] == "preprints_org"


@pytest.mark.asyncio
async def test_extract_url_dispatches_correctly(respx_mock):
    """extract_url should pick the right branch by URL pattern."""
    respx_mock.get("https://api.github.com/repos/foo/bar").mock(
        return_value=httpx.Response(200, json={"description": "d", "language": "Go",
                                                  "default_branch": "main",
                                                  "created_at": "2025", "topics": []})
    )
    respx_mock.get("https://api.github.com/repos/foo/bar/readme").mock(
        return_value=httpx.Response(404)
    )
    respx_mock.get("https://api.github.com/repos/foo/bar/contributors").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with httpx.AsyncClient() as http:
        p = await extract_url("https://github.com/foo/bar", http_client=http)
    assert p["item_type"] == "computerProgram"
