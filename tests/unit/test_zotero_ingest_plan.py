"""plan_kbs_from_zotero counts items, with-doi, with-pdf, with-notes correctly."""

from __future__ import annotations

import pytest

from perspicacite.integrations.zotero_ingest import plan_kbs_from_zotero


class _FakeClient:
    def __init__(self):
        self.collections_top = [{"key": "TOP1", "data": {"name": "Top1"}}]

    async def list_top_level_collections(self):
        return self.collections_top

    async def list_items_in_collection(self, key, *, include_subcollections=True):
        return [
            {"key": "I1", "data": {"DOI": "10.1/x", "title": "A"}},
            {"key": "I2", "data": {"title": "B"}},  # no DOI
        ]

    async def list_top_level_items_without_collection(self):
        return [{"key": "U1", "data": {"DOI": "10.2/u"}}]

    async def get_item_attachments(self, item_key):
        if item_key == "I1":
            return [
                {
                    "key": "A1",
                    "data": {
                        "linkMode": "imported_file",
                        "contentType": "application/pdf",
                    },
                }
            ]
        return []

    async def get_item_notes(self, item_key):
        if item_key == "I1":
            return ["a note"]
        return []


@pytest.mark.asyncio
async def test_plan_includes_top_level_and_unfiled():
    c = _FakeClient()
    plan = await plan_kbs_from_zotero(c, include_unfiled=True)
    by_name = {(p.source_collection_name or "Unfiled"): p for p in plan}
    assert set(by_name.keys()) == {"Top1", "Unfiled"}
    top1 = by_name["Top1"]
    assert top1.item_count == 2
    assert top1.with_doi_count == 1
    assert top1.with_pdf_count == 1
    assert top1.with_notes_count == 1
    unf = by_name["Unfiled"]
    assert unf.item_count == 1
    assert unf.with_doi_count == 1


@pytest.mark.asyncio
async def test_plan_filters_to_requested_keys():
    c = _FakeClient()
    c.collections_top = [
        {"key": "TOP1", "data": {"name": "Top1"}},
        {"key": "TOP2", "data": {"name": "Top2"}},
    ]
    plan = await plan_kbs_from_zotero(
        c, top_level_collection_keys=["TOP1"], include_unfiled=False,
    )
    names = {p.source_collection_name for p in plan}
    assert names == {"Top1"}


@pytest.mark.asyncio
async def test_plan_omits_empty_unfiled():
    c = _FakeClient()

    async def _no_unfiled():
        return []

    c.list_top_level_items_without_collection = _no_unfiled
    plan = await plan_kbs_from_zotero(c, include_unfiled=True)
    assert all(p.source_collection_key is not None for p in plan)
