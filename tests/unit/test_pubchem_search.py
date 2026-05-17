# tests/unit/test_pubchem_search.py
from __future__ import annotations

import pytest

from perspicacite.models.papers import Paper, PaperSource

_PUBMED_PAPER = Paper(
    id="10.1000/test",
    title="Aspirin study",
    doi="10.1000/test",
    source=PaperSource.PUBMED,
)


@pytest.mark.asyncio
async def test_name_query_resolves_via_cid(monkeypatch):
    import perspicacite.search.pubchem_search as mod

    async def mock_get_cid(input_value, input_type, client):
        return 2244

    async def mock_get_pmids(cid, client):
        return [11234567, 22345678]

    async def mock_pmids_to_papers(pmids, email, max_results):
        return [_PUBMED_PAPER]

    monkeypatch.setattr(mod, "_get_cid", mock_get_cid)
    monkeypatch.setattr(mod, "_get_pmids_for_cid", mock_get_pmids)
    monkeypatch.setattr(mod, "_pmids_to_papers", mock_pmids_to_papers)

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    papers = await provider.search("aspirin")
    assert len(papers) == 1
    assert papers[0].title == "Aspirin study"


@pytest.mark.asyncio
async def test_inchikey_detected_as_inchikey(monkeypatch):
    import perspicacite.search.pubchem_search as mod
    detected_types: list[str] = []

    async def mock_get_cid(input_value, input_type, client):
        detected_types.append(input_type)
        return 2244

    async def mock_get_pmids(cid, client):
        return []

    async def mock_pmids_to_papers(pmids, email, max_results):
        return []

    monkeypatch.setattr(mod, "_get_cid", mock_get_cid)
    monkeypatch.setattr(mod, "_get_pmids_for_cid", mock_get_pmids)
    monkeypatch.setattr(mod, "_pmids_to_papers", mock_pmids_to_papers)

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    await provider.search("UHOVQNZJYSORNB-UHFFFAOYSA-N")
    assert "inchikey" in detected_types


@pytest.mark.asyncio
async def test_no_cid_returns_empty(monkeypatch):
    import perspicacite.search.pubchem_search as mod

    async def mock_get_cid(input_value, input_type, client):
        return None

    monkeypatch.setattr(mod, "_get_cid", mock_get_cid)

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    papers = await provider.search("nonexistentcompound999xyz")
    assert papers == []


@pytest.mark.asyncio
async def test_no_pmids_returns_empty(monkeypatch):
    import perspicacite.search.pubchem_search as mod

    async def mock_get_cid(input_value, input_type, client):
        return 9999

    async def mock_get_pmids(cid, client):
        return []

    monkeypatch.setattr(mod, "_get_cid", mock_get_cid)
    monkeypatch.setattr(mod, "_get_pmids_for_cid", mock_get_pmids)

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    papers = await provider.search("some compound")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.pubchem_search import PubChemSearchProvider
    p = PubChemSearchProvider()
    assert p.name == "pubchem"
    assert "chemistry" in p.domains
    assert p.tier == "external"
    assert p.retry == 1


def test_detect_input_type_inchikey():
    from perspicacite.search.pubchem_search import _detect_input_type
    assert _detect_input_type("UHOVQNZJYSORNB-UHFFFAOYSA-N") == "inchikey"


def test_detect_input_type_smiles():
    from perspicacite.search.pubchem_search import _detect_input_type
    assert _detect_input_type("C1CCCCC1") == "smiles"
    assert _detect_input_type("CC(=O)Oc1ccccc1C(=O)O") == "smiles"


def test_detect_input_type_name():
    from perspicacite.search.pubchem_search import _detect_input_type
    assert _detect_input_type("aspirin") == "name"
    assert _detect_input_type("glucose") == "name"


@pytest.mark.asyncio
async def test_papers_retagged_as_pubchem_source(monkeypatch):
    """Papers returned by PubMedSearchAdapter must be re-tagged as PaperSource.PUBCHEM."""
    import perspicacite.search.pubchem_search as mod

    async def mock_get_cid(input_value, input_type, client):
        return 2244

    async def mock_get_pmids(cid, client):
        return [11234567]

    async def mock_pmids_to_papers(pmids, email, max_results):
        # Simulate a paper returned by PubMed with PUBMED source
        return [Paper(id="10.1000/t", title="T", source=PaperSource.PUBMED)]

    monkeypatch.setattr(mod, "_get_cid", mock_get_cid)
    monkeypatch.setattr(mod, "_get_pmids_for_cid", mock_get_pmids)
    monkeypatch.setattr(mod, "_pmids_to_papers", mock_pmids_to_papers)

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    papers = await provider.search("aspirin")
    assert len(papers) == 1
    assert papers[0].source == PaperSource.PUBCHEM
