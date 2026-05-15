"""Zotero ingest worker — dedups by DOI, attaches notes, handles missing PDFs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.integrations.zotero_ingest import (
    ZoteroKBPlanEntry,
    build_kbs_from_zotero,
)


class _Reg:
    def __init__(self):
        self.events = []
        self.finished = None
        self.failed = None

    async def publish(self, jid, ev):
        self.events.append(ev)

    async def finish(self, jid, res):
        self.finished = res

    async def fail(self, jid, err):
        self.failed = err


class _Client:
    async def list_items_in_collection(self, key, *, include_subcollections=True):
        return [{"key": "I1", "data": {"DOI": "10.1/x", "title": "T1"}}]

    async def list_top_level_items_without_collection(self):
        return []

    async def get_item_attachments(self, key):
        return []

    async def get_item_notes(self, key):
        return ["note text"]

    async def download_attachment_bytes(self, key):
        return None


@pytest.mark.asyncio
async def test_worker_dedups_by_doi_and_attaches_notes(monkeypatch):
    seen: dict = {}

    class _DKB:
        def __init__(self, **kw):
            pass

        async def add_papers(self, papers, include_full_text=True):
            seen["papers"] = papers
            return len(papers)

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.DynamicKnowledgeBase",
        _DKB,
    )

    async def _retrieve(doi, **_):
        return SimpleNamespace(success=True, full_text="full body", abstract=None, metadata={})

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.retrieve_paper_content",
        _retrieve,
    )

    fake_state = SimpleNamespace(
        config=SimpleNamespace(
            pdf_download=None,
            capsule=SimpleNamespace(auto_build_on_ingest=False),
        ),
        pdf_parser=SimpleNamespace(parse=AsyncMock()),
        vector_store=SimpleNamespace(paper_exists=AsyncMock(return_value=False)),
        embedding_provider=None,
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="perspicacite_TestKB",
                paper_count=0,
                chunk_count=0,
            )),
            create_kb_metadata=AsyncMock(),
            save_kb_metadata=AsyncMock(),
        ),
    )
    plan = [ZoteroKBPlanEntry(
        kb_name="TestKB",
        source_collection_key="C1",
        source_collection_name="Coll1",
        item_count=1, with_doi_count=1, with_pdf_count=0, with_notes_count=1,
    )]
    reg = _Reg()
    await build_kbs_from_zotero(
        _Client(), plan=plan, app_state=fake_state, registry=reg, job_id="J1",
    )
    assert reg.finished is not None, "expected registry.finish to be called"
    assert "papers" in seen, "expected papers to reach DKB.add_papers"
    paper = seen["papers"][0]
    assert "note text" in (paper.full_text or "")


@pytest.mark.asyncio
async def test_worker_skips_existing_doi(monkeypatch):
    class _DKB:
        def __init__(self, **kw):
            pass

        async def add_papers(self, papers, include_full_text=True):
            return len(papers)

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.DynamicKnowledgeBase", _DKB,
    )

    async def _retrieve(doi, **_):
        return SimpleNamespace(success=True, full_text="x", abstract=None, metadata={})

    monkeypatch.setattr(
        "perspicacite.integrations.zotero_ingest.retrieve_paper_content", _retrieve,
    )

    fake_state = SimpleNamespace(
        config=SimpleNamespace(
            pdf_download=None,
            capsule=SimpleNamespace(auto_build_on_ingest=False),
        ),
        pdf_parser=SimpleNamespace(parse=AsyncMock()),
        vector_store=SimpleNamespace(paper_exists=AsyncMock(return_value=True)),  # all exist
        embedding_provider=None,
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                collection_name="perspicacite_TestKB", paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
    )
    plan = [ZoteroKBPlanEntry(
        kb_name="TestKB",
        source_collection_key="C1",
        source_collection_name="Coll1",
        item_count=1, with_doi_count=1, with_pdf_count=0, with_notes_count=0,
    )]
    reg = _Reg()
    await build_kbs_from_zotero(
        _Client(), plan=plan, app_state=fake_state, registry=reg, job_id="J2",
    )
    skipped_events = [e for e in reg.events if e.get("status") == "skipped"]
    assert skipped_events, "expected a skipped progress event for existing DOI"
