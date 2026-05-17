"""Unit tests for openrouter_fallback helper — all HTTP mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _resolve_api_key ──────────────────────────────────────────────────────────

def test_resolve_api_key_uses_config_value():
    from perspicacite.search.openrouter_fallback import _resolve_api_key
    assert _resolve_api_key("sk-config") == "sk-config"


def test_resolve_api_key_falls_back_to_env(monkeypatch):
    from perspicacite.search.openrouter_fallback import _resolve_api_key
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    assert _resolve_api_key("") == "sk-env"


def test_resolve_api_key_returns_empty_when_neither_set(monkeypatch):
    from perspicacite.search.openrouter_fallback import _resolve_api_key
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert _resolve_api_key("") == ""


# ── _build_payload ─────────────────────────────────────────────────────────────

def test_build_payload_structure():
    from perspicacite.search.openrouter_fallback import _build_payload
    payload = _build_payload("CRISPR", "deepseek/deepseek-chat", 5, ["arxiv.org"])
    assert payload["model"] == "deepseek/deepseek-chat"
    assert payload["tool_choice"] == "required"
    assert payload["tools"][0]["type"] == "openrouter:web_search"
    assert payload["tools"][0]["parameters"]["engine"] == "exa"
    assert payload["tools"][0]["parameters"]["max_results"] == 5
    assert "arxiv.org" in payload["tools"][0]["parameters"]["allowed_domains"]
    assert "CRISPR" in payload["messages"][0]["content"]


def test_build_payload_clamps_max_results_to_25():
    from perspicacite.search.openrouter_fallback import _build_payload
    payload = _build_payload("test", "model", 99, [])
    assert payload["tools"][0]["parameters"]["max_results"] == 25


# ── _parse_response ────────────────────────────────────────────────────────────

def test_parse_response_extracts_json_array():
    from perspicacite.search.openrouter_fallback import _parse_response
    content = 'Here are papers: [{"title": "Test", "year": 2021}] done.'
    result = _parse_response(content)
    assert result == [{"title": "Test", "year": 2021}]


def test_parse_response_returns_empty_on_no_array():
    from perspicacite.search.openrouter_fallback import _parse_response
    assert _parse_response("No JSON here.") == []


def test_parse_response_handles_multiline_array():
    from perspicacite.search.openrouter_fallback import _parse_response
    content = '[\n  {"title": "A"},\n  {"title": "B"}\n]'
    result = _parse_response(content)
    assert len(result) == 2


# ── _build_paper ───────────────────────────────────────────────────────────────

def test_build_paper_full_entry():
    from perspicacite.search.openrouter_fallback import _build_paper
    from perspicacite.models.papers import PaperSource

    entry = {
        "title": "AlphaFold",
        "authors": ["Jumper J", "Evans R"],
        "year": 2021,
        "doi": "10.1038/s41586-021-03819-2",
        "abstract": "Protein structure prediction...",
        "url": "https://nature.com/articles/s41586-021-03819-2",
    }
    paper = _build_paper(entry)
    assert paper is not None
    assert paper.title == "AlphaFold"
    assert paper.doi == "10.1038/s41586-021-03819-2"
    assert paper.id == "10.1038/s41586-021-03819-2"
    assert paper.year == 2021
    assert paper.source == PaperSource.OPENROUTER_WEB
    assert len(paper.authors) == 2
    assert paper.abstract == "Protein structure prediction..."


def test_build_paper_uses_url_hash_when_no_doi():
    from perspicacite.search.openrouter_fallback import _build_paper
    entry = {"title": "No DOI Paper", "url": "https://arxiv.org/abs/1234.5678"}
    paper = _build_paper(entry)
    assert paper is not None
    assert paper.doi is None
    assert paper.id.startswith("openrouter:")


def test_build_paper_source_is_openrouter_web():
    from perspicacite.search.openrouter_fallback import _build_paper
    from perspicacite.models.papers import PaperSource
    paper = _build_paper({"title": "Test", "url": "https://arxiv.org/abs/1"})
    assert paper.source == PaperSource.OPENROUTER_WEB


def test_build_paper_handles_null_year():
    from perspicacite.search.openrouter_fallback import _build_paper
    paper = _build_paper({"title": "Test", "year": None, "url": "https://arxiv.org/1"})
    assert paper.year is None


# ── openrouter_academic_search (integration, mocked HTTP) ────────────────────

def _make_mock_client(response_content: str, raise_on_post: Exception | None = None):
    """Build a mock httpx.AsyncClient."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=json.loads(response_content))

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    if raise_on_post:
        mock_client.post = AsyncMock(side_effect=raise_on_post)
    else:
        mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


_VALID_OR_RESPONSE = json.dumps({
    "choices": [{
        "message": {
            "content": json.dumps([
                {
                    "title": "Attention Is All You Need",
                    "authors": ["Vaswani A", "Shazeer N"],
                    "year": 2017,
                    "doi": "10.5555/3295222.3295349",
                    "abstract": "The dominant sequence transduction models...",
                    "url": "https://arxiv.org/abs/1706.03762",
                }
            ])
        }
    }]
})


@pytest.mark.asyncio
async def test_search_returns_papers_on_valid_response():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    mock_client = _make_mock_client(_VALID_OR_RESPONSE)
    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search(
            "attention transformer",
            api_key="sk-test",
            max_results=5,
        )

    assert len(papers) == 1
    assert papers[0].title == "Attention Is All You Need"
    assert papers[0].doi == "10.5555/3295222.3295349"
    assert papers[0].year == 2017


@pytest.mark.asyncio
async def test_search_returns_empty_on_http_error():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    mock_client = _make_mock_client("{}", raise_on_post=Exception("connection refused"))
    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search("test", api_key="sk-test")

    assert papers == []


@pytest.mark.asyncio
async def test_search_returns_empty_when_no_api_key(monkeypatch):
    from perspicacite.search.openrouter_fallback import openrouter_academic_search
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    papers = await openrouter_academic_search("test", api_key="")
    assert papers == []


@pytest.mark.asyncio
async def test_search_returns_empty_on_malformed_json():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    bad_response = json.dumps({"choices": [{"message": {"content": "No JSON here."}}]})
    mock_client = _make_mock_client(bad_response)

    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search("test", api_key="sk-test")

    assert papers == []


@pytest.mark.asyncio
async def test_search_uses_env_var_key(monkeypatch):
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-from-env")
    mock_client = _make_mock_client(_VALID_OR_RESPONSE)

    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        papers = await openrouter_academic_search("test", api_key="")

    assert len(papers) == 1
    call_kwargs = mock_client.post.call_args[1]
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-from-env"


@pytest.mark.asyncio
async def test_search_passes_allowed_domains_to_payload():
    from perspicacite.search.openrouter_fallback import openrouter_academic_search

    mock_client = _make_mock_client(_VALID_OR_RESPONSE)
    with patch("perspicacite.search.openrouter_fallback.httpx.AsyncClient",
               return_value=mock_client):
        await openrouter_academic_search(
            "test",
            api_key="sk-test",
            allowed_domains=["arxiv.org", "biorxiv.org"],
        )

    sent_payload = mock_client.post.call_args[1]["json"]
    domains = sent_payload["tools"][0]["parameters"]["allowed_domains"]
    assert domains == ["arxiv.org", "biorxiv.org"]
