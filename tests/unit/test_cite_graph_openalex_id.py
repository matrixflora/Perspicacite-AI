from __future__ import annotations

import pytest

from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig
from perspicacite.pipeline import cite_graph as cg


@pytest.mark.asyncio
async def test_orchestrator_accepts_openalex_id_and_skips_resolution(monkeypatch):
    """When openalex_id is supplied, we never call resolve_library_paper or
    openalex_id_for_doi; we fetch cited-by works directly."""
    seen: dict[str, object] = {"resolved": False, "doi_lookup": False}

    async def fake_resolve(*a, **kw):
        seen["resolved"] = True
        return None

    async def fake_doi_lookup(*a, **kw):
        seen["doi_lookup"] = True
        return None

    async def fake_resolve_and_fetch(*, tool, doi, openalex_id, headers, client, max_results):
        assert openalex_id == "W3177828909"
        assert doi is None
        assert tool is None
        return ([{"id": "https://openalex.org/W10", "doi": "10.1/test"}], "AlphaFold seed title")

    monkeypatch.setattr(cg, "resolve_library_paper", fake_resolve)
    monkeypatch.setattr(cg, "openalex_id_for_doi", fake_doi_lookup)
    monkeypatch.setattr(cg, "_resolve_and_fetch", fake_resolve_and_fetch)

    kb = KnowledgeBaseConfig(
        name="test",
        cite_graph=CiteGraphConfig(min_citations=0, min_year_offset=50),
    )
    hits = await cg.enrich_kb_from_cite_graph(
        openalex_id="W3177828909",
        kb_config=kb,
        existing_dois=set(),
        dry_run=True,
        now_year=2025,
    )
    assert isinstance(hits, list)
    assert seen["resolved"] is False
    assert seen["doi_lookup"] is False
