"""Tests for search_skill_kb MCP tool."""
import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_search_skill_kb_no_edam_passes_all():
    """Without EDAM filters, results should equal search_knowledge_base results."""
    from perspicacite.mcp.server import search_skill_kb

    fake_chunks = [
        {"paper_id": "skill-a", "chunk_text": "Feature detection", "relevance_score": 0.9, "metadata": {}},
        {"paper_id": "skill-b", "chunk_text": "Annotation methods", "relevance_score": 0.7, "metadata": {}},
    ]

    with patch(
        "perspicacite.mcp.server.search_knowledge_base",
        new_callable=AsyncMock,
    ) as mock_skb:
        mock_skb.return_value = json.dumps({
            "success": True,
            "query": "peak detection",
            "results": fake_chunks,
        })
        result_str = await search_skill_kb(
            query="peak detection",
            kb_name="test-skills-kb",
        )

    data = json.loads(result_str)
    assert data["success"] is True
    assert len(data["results"]) == 2


@pytest.mark.asyncio
async def test_search_skill_kb_edam_topic_filters():
    """With edam_topics, only matching chunks should be returned."""
    from perspicacite.mcp.server import search_skill_kb

    target_topic = "http://edamontology.org/topic_3172"
    fake_chunks = [
        {
            "paper_id": "skill-a",
            "chunk_text": "Feature detection in metabolomics",
            "relevance_score": 0.9,
            "metadata": {"edam_topics": [target_topic]},
        },
        {
            "paper_id": "skill-b",
            "chunk_text": "Sequence alignment",
            "relevance_score": 0.8,
            "metadata": {"edam_topics": ["http://edamontology.org/topic_0080"]},
        },
    ]

    with patch(
        "perspicacite.mcp.server.search_knowledge_base",
        new_callable=AsyncMock,
    ) as mock_skb:
        mock_skb.return_value = json.dumps({
            "success": True,
            "query": "peak detection",
            "results": fake_chunks,
        })
        result_str = await search_skill_kb(
            query="peak detection",
            kb_name="test-skills-kb",
            edam_topics=[target_topic],
        )

    data = json.loads(result_str)
    assert data["success"] is True
    ids = [r["paper_id"] for r in data["results"]]
    assert "skill-a" in ids
    assert "skill-b" not in ids
    assert data.get("edam_filter_applied") is True


@pytest.mark.asyncio
async def test_search_skill_kb_returns_skill_iris():
    """Response should include skill_iris derived from skill_iri metadata fields."""
    from perspicacite.mcp.server import search_skill_kb

    fake_chunks = [
        {
            "paper_id": "skill-a",
            "chunk_text": "Feature detection",
            "relevance_score": 0.9,
            "metadata": {
                "skill_iri": "https://w3id.org/holobiomicslab/asb-skill/feature-detection-lcms",
                "edam_topics": [],
            },
        },
    ]

    with patch(
        "perspicacite.mcp.server.search_knowledge_base",
        new_callable=AsyncMock,
    ) as mock_skb:
        mock_skb.return_value = json.dumps({
            "success": True,
            "query": "peak detection",
            "results": fake_chunks,
        })
        result_str = await search_skill_kb(
            query="peak detection",
            kb_name="test-skills-kb",
        )

    data = json.loads(result_str)
    assert data["success"] is True
    assert "skill_iris" in data
    assert "https://w3id.org/holobiomicslab/asb-skill/feature-detection-lcms" in data["skill_iris"]


@pytest.mark.asyncio
async def test_search_skill_kb_propagates_search_failure():
    """If search_knowledge_base returns failure, surface it."""
    from perspicacite.mcp.server import search_skill_kb

    with patch(
        "perspicacite.mcp.server.search_knowledge_base",
        new_callable=AsyncMock,
    ) as mock_skb:
        mock_skb.return_value = json.dumps({
            "success": False,
            "error": "KB not found",
        })
        result_str = await search_skill_kb(
            query="peak detection",
            kb_name="nonexistent-kb",
        )

    data = json.loads(result_str)
    assert data["success"] is False
