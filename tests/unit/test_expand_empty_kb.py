"""Unit test: expand_kb_via_citations fails loudly on an empty KB (audit #2).

A 0-paper KB must raise (-> the MCP tool returns success:false) rather than a
silent zeroed report, so an LLM caller can distinguish "found nothing new" from
"there was nothing to expand from".
"""
from types import SimpleNamespace

import pytest

from perspicacite.pipeline.snowball import expand_kb_via_citations


def _empty_kb_app_state():
    async def _get_kb_metadata(name):
        return SimpleNamespace(collection_name="kb_collection")

    async def _list_paper_metadata(collection):
        return []  # empty KB -> no seed DOIs

    return SimpleNamespace(
        session_store=SimpleNamespace(get_kb_metadata=_get_kb_metadata),
        vector_store=SimpleNamespace(list_paper_metadata=_list_paper_metadata),
        config=SimpleNamespace(pdf_download=None),
    )


@pytest.mark.asyncio
async def test_expand_empty_kb_raises_loudly():
    with pytest.raises(ValueError, match="no papers to expand"):
        await expand_kb_via_citations(
            app_state=_empty_kb_app_state(), kb_name="empty_kb"
        )
