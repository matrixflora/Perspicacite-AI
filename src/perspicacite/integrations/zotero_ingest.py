"""Zotero → KB ingest: plan-then-execute. Worker (in same module) drives the unified pipeline."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from perspicacite.logging import get_logger

if TYPE_CHECKING:
    from perspicacite.integrations.zotero import ZoteroClient

logger = get_logger("perspicacite.zotero_ingest")


class ZoteroKBPlanEntry(BaseModel):
    """One KB to be created/populated from a Zotero source."""

    kb_name: str
    source_collection_key: str | None
    source_collection_name: str | None
    item_count: int
    with_doi_count: int
    with_pdf_count: int
    with_notes_count: int


def _slugify(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    s = re.sub(r"[^A-Za-z0-9_\-]", "", s)
    return s or "kb"


async def _summarize_items(client: ZoteroClient, items: list[dict[str, Any]]) -> dict[str, int]:
    """Return {with_doi, with_pdf, with_notes} counts for a list of Zotero items."""
    with_doi = sum(1 for it in items if (it.get("data") or {}).get("DOI"))

    async def _has_pdf(it: dict[str, Any]) -> bool:
        atts = await client.get_item_attachments(it["key"])
        return any(
            (a.get("data") or {}).get("contentType") == "application/pdf"
            and (a.get("data") or {}).get("linkMode") in {"imported_file", "imported_url"}
            for a in atts
        )

    async def _has_note(it: dict[str, Any]) -> bool:
        notes = await client.get_item_notes(it["key"])
        return any(n for n in notes)

    if not items:
        return {"with_doi": 0, "with_pdf": 0, "with_notes": 0}

    pdf_flags = await asyncio.gather(*(_has_pdf(it) for it in items))
    note_flags = await asyncio.gather(*(_has_note(it) for it in items))
    return {
        "with_doi": with_doi,
        "with_pdf": sum(1 for f in pdf_flags if f),
        "with_notes": sum(1 for f in note_flags if f),
    }


async def plan_kbs_from_zotero(
    client: ZoteroClient,
    *,
    top_level_collection_keys: list[str] | None = None,
    include_unfiled: bool = True,
    library_label: str = "Library",
) -> list[ZoteroKBPlanEntry]:
    """Return preview of what would be ingested — one entry per top-level
    collection (optionally rolled up across subcollections) plus an Unfiled
    bucket. Each entry counts items, items-with-DOI, items-with-PDF,
    items-with-notes."""
    out: list[ZoteroKBPlanEntry] = []
    tops = await client.list_top_level_collections()
    if top_level_collection_keys is not None:
        keys = set(top_level_collection_keys)
        tops = [c for c in tops if c.get("key") in keys]
    for c in tops:
        name = (c.get("data") or {}).get("name") or c["key"]
        items = await client.list_items_in_collection(c["key"], include_subcollections=True)
        summary = await _summarize_items(client, items)
        out.append(
            ZoteroKBPlanEntry(
                kb_name=f"{_slugify(library_label)}_{_slugify(name)}",
                source_collection_key=c["key"],
                source_collection_name=name,
                item_count=len(items),
                with_doi_count=summary["with_doi"],
                with_pdf_count=summary["with_pdf"],
                with_notes_count=summary["with_notes"],
            )
        )
    if include_unfiled:
        items = await client.list_top_level_items_without_collection()
        if items:
            summary = await _summarize_items(client, items)
            out.append(
                ZoteroKBPlanEntry(
                    kb_name=f"{_slugify(library_label)}_Unfiled",
                    source_collection_key=None,
                    source_collection_name=None,
                    item_count=len(items),
                    with_doi_count=summary["with_doi"],
                    with_pdf_count=summary["with_pdf"],
                    with_notes_count=summary["with_notes"],
                )
            )
    return out
