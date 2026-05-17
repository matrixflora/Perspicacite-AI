from __future__ import annotations

import pytest

from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig
from perspicacite.pipeline import cite_graph as cg


@pytest.mark.asyncio
async def test_include_scripts_attaches_scripts_to_hits(monkeypatch):
    fake_hit_oa_work = {
        "id": "https://openalex.org/W10",
        "doi": "https://doi.org/10.1/test",
        "title": "Cited paper",
        "publication_year": 2024,
        "cited_by_count": 50,
        "abstract_inverted_index": {"alphafold": [0]},
        "open_access": {"is_oa": True},
        "best_oa_location": {"source": {"display_name": "Nature"}},
        "external_ids": [],
    }

    async def fake_resolve_and_fetch(*, tool, doi, openalex_id, headers, client, max_results):
        return ([fake_hit_oa_work], "AlphaFold seed")

    def fake_repo_lookup(client, oa_work, *, headers):
        return "deepmind/alphafold"

    async def fake_fetch_repo(full_name, *, cache_dir, ttl_seconds, token=None):
        return {"scripts": [
            {"path": "fold.py", "text": "def f():\n    return 1\n"},
            {"path": "io.py",   "text": "def g():\n    return 2\n"},
        ]}

    monkeypatch.setattr(cg, "_resolve_and_fetch", fake_resolve_and_fetch)
    monkeypatch.setattr(cg, "_github_repo_for_work", fake_repo_lookup, raising=False)
    monkeypatch.setattr(cg, "fetch_github_repo", fake_fetch_repo, raising=False)

    kb = KnowledgeBaseConfig(
        library_paper_map={"alphafold": "10.0/seed"},
        cite_graph=CiteGraphConfig(min_citations=0, min_year_offset=10, include_scripts=True),
    )
    hits = await cg.enrich_kb_from_cite_graph(
        tool="alphafold",
        kb_config=kb,
        existing_dois=set(),
        dry_run=False,
        now_year=2025,
    )
    assert len(hits) == 1
    hit = hits[0]
    assert hasattr(hit, "scripts")
    assert isinstance(hit.scripts, list)
    assert len(hit.scripts) >= 1
    assert hit.scripts[0]["path"].endswith(".py")


@pytest.mark.asyncio
async def test_include_scripts_off_by_default(monkeypatch):
    fake_hit_oa_work = {
        "id": "https://openalex.org/W10",
        "doi": "https://doi.org/10.1/test",
        "title": "Cited paper",
        "publication_year": 2024,
        "cited_by_count": 50,
        "abstract_inverted_index": {"alphafold": [0]},
        "open_access": {"is_oa": True},
    }

    async def fake_resolve_and_fetch(*, tool, doi, openalex_id, headers, client, max_results):
        return ([fake_hit_oa_work], "AlphaFold seed")

    monkeypatch.setattr(cg, "_resolve_and_fetch", fake_resolve_and_fetch)

    kb = KnowledgeBaseConfig(
        library_paper_map={"alphafold": "10.0/seed"},
        cite_graph=CiteGraphConfig(min_citations=0, min_year_offset=10, include_scripts=False),
    )
    hits = await cg.enrich_kb_from_cite_graph(
        tool="alphafold",
        kb_config=kb,
        existing_dois=set(),
        dry_run=False,
        now_year=2025,
    )
    assert len(hits) == 1
    assert hits[0].scripts == []
