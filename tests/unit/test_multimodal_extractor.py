# tests/unit/test_multimodal_extractor.py
"""Tests for MultimodalPDFExtractor (Wave 4.1)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.parsers.multimodal import (
    MultimodalPDFExtractor,
    VisualExtract,
)


def _well_formed_response(page: int = 1) -> str:
    return json.dumps({
        "visuals": [
            {
                "kind": "figure",
                "page": page,
                "label": "Figure 3",
                "caption": "Comparison of methods A and B.",
                "content": "Bar chart showing method A outperforms B by 15%.",
            },
            {
                "kind": "table",
                "page": page,
                "label": "Table 1",
                "caption": "Summary statistics.",
                "content": "| Method | Acc |\n|---|---|\n| A | 0.91 |\n| B | 0.78 |",
            },
        ]
    })


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=_well_formed_response())
    return llm


def test_visual_extract_dataclass_shape():
    v = VisualExtract(
        kind="figure", page=2, label="Figure 1",
        caption="A figure.", content="Bars.",
    )
    assert v.kind == "figure"
    assert v.page == 2


def test_parses_well_formed_response(mock_llm):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="claude-sonnet-4-5", provider="anthropic",
    )
    visuals = extractor._parse_response(_well_formed_response())
    assert len(visuals) == 2
    assert visuals[0].kind == "figure"
    assert visuals[0].label == "Figure 3"
    assert visuals[1].kind == "table"
    assert "Acc" in visuals[1].content


def test_returns_empty_on_malformed_json(mock_llm):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    assert extractor._parse_response("not-json {{{") == []


def test_returns_empty_on_missing_visuals_key(mock_llm):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    assert extractor._parse_response('{"results": []}') == []


def test_filters_invalid_kind(mock_llm):
    """Unknown 'kind' values are dropped, not raised."""
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    bad = json.dumps({
        "visuals": [
            {"kind": "diagram", "page": 1, "label": "X",
             "caption": "y", "content": "z"},
            {"kind": "figure", "page": 1, "label": "Figure 1",
             "caption": "ok", "content": "ok"},
        ]
    })
    out = extractor._parse_response(bad)
    assert len(out) == 1
    assert out[0].kind == "figure"


def test_to_chunks_builds_correct_metadata(mock_llm):
    from perspicacite.models.documents import DocumentChunk
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    visuals = [
        VisualExtract(kind="figure", page=2, label="Figure 3",
                      caption="cap", content="desc"),
        VisualExtract(kind="table", page=3, label="Table 1",
                      caption="cap2", content="| x |"),
    ]
    chunks = extractor.to_chunks(visuals, paper_id="paper-1", chunk_index_offset=10)
    assert len(chunks) == 2
    assert all(isinstance(c, DocumentChunk) for c in chunks)
    assert chunks[0].metadata.paper_id == "paper-1"
    assert chunks[0].metadata.chunk_index == 10
    assert chunks[0].metadata.page_number == 2
    assert chunks[0].metadata.content_type == "figure"
    assert chunks[0].metadata.section == "Figure 3"
    assert "Figure 3" in chunks[0].text
    assert "cap" in chunks[0].text
    assert "desc" in chunks[0].text
    assert chunks[1].metadata.chunk_index == 11
    assert chunks[1].metadata.content_type == "table"


@pytest.mark.asyncio
async def test_render_failure_for_one_page_doesnt_kill_run(mock_llm, tmp_path):
    """If PyMuPDF chokes on page 2 of 3, we still extract pages 1 and 3."""
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )

    call_count = {"n": 0}

    def fake_render(page_num: int) -> bytes:
        call_count["n"] += 1
        if page_num == 2:
            raise RuntimeError("page 2 corrupt")
        return b"fake-png-bytes"

    with patch.object(extractor, "_render_png", side_effect=fake_render), \
         patch.object(extractor, "_page_count", return_value=3):
        result = await extractor.extract_visuals(
            pdf_path=tmp_path / "fake.pdf",
            paper_id="p1",
        )
    # 2 pages succeeded, each yielding 2 visuals from the mocked LLM.
    assert len(result) == 4


@pytest.mark.asyncio
async def test_image_content_block_anthropic_shape(mock_llm, tmp_path):
    """The messages we send must be a list with an image-type block."""
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="claude-sonnet-4-5", provider="anthropic",
    )
    with patch.object(extractor, "_render_png", return_value=b"png-bytes"), \
         patch.object(extractor, "_page_count", return_value=1):
        await extractor.extract_visuals(
            pdf_path=tmp_path / "fake.pdf",
            paper_id="p1",
        )
    args, kwargs = mock_llm.complete.call_args
    messages = kwargs.get("messages") or args[0]
    # Expect a user message with a list-content holding an image block.
    user_msg = next(m for m in messages if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    types_present = [b.get("type") for b in user_msg["content"]]
    assert "image" in types_present
    assert "text" in types_present


@pytest.mark.asyncio
async def test_extract_visuals_respects_page_range(mock_llm, tmp_path):
    extractor = MultimodalPDFExtractor(
        llm_client=mock_llm, model="m", provider="p",
    )
    with patch.object(extractor, "_render_png", return_value=b"png"), \
         patch.object(extractor, "_page_count", return_value=10):
        await extractor.extract_visuals(
            pdf_path=tmp_path / "fake.pdf",
            paper_id="p1",
            page_range=(3, 5),  # pages 3, 4, 5 (1-indexed inclusive)
        )
    # 3 LLM calls — one per page in the range.
    assert mock_llm.complete.call_count == 3
