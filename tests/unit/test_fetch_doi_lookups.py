import json
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.pipeline.external import fetch_doi


@pytest.mark.asyncio
async def test_fetch_crossref_writes_artifact(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    payload = {"message": {"DOI": "10.1/x", "title": ["A paper"]}}
    with patch.object(fetch_doi, "http_get_json",
                      new=AsyncMock(return_value=payload)) as m:
        r = await fetch_doi.fetch_crossref(
            "10.1/x", capsule_dir=cap, cache_dir=tmp_path / "cache",
        )
    assert r == payload
    out = cap / "external" / "crossref" / "10.1_x.json"
    assert out.exists()
    assert json.loads(out.read_text())["message"]["DOI"] == "10.1/x"
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_crossref_returns_none_on_miss(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    with patch.object(fetch_doi, "http_get_json",
                      new=AsyncMock(return_value=None)):
        r = await fetch_doi.fetch_crossref(
            "10.1/missing", capsule_dir=cap, cache_dir=tmp_path / "cache",
        )
    assert r is None
    assert not (cap / "external" / "crossref" / "10.1_missing.json").exists()


@pytest.mark.asyncio
async def test_fetch_unpaywall_writes_artifact(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    payload = {"doi": "10.1/x", "is_oa": True, "best_oa_location": {"url": "https://oa.example/x.pdf"}}
    with patch.object(fetch_doi, "http_get_json",
                      new=AsyncMock(return_value=payload)):
        r = await fetch_doi.fetch_unpaywall(
            "10.1/x", capsule_dir=cap, cache_dir=tmp_path / "cache",
            email="me@example.com",
        )
    assert r["is_oa"] is True
    assert (cap / "external" / "unpaywall" / "10.1_x.json").exists()


@pytest.mark.asyncio
async def test_fetch_pubmed_writes_abstract_and_xml(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    xml = """<PubmedArticleSet>
      <PubmedArticle><Article><Abstract>
        <AbstractText Label="BACKGROUND">first part</AbstractText>
        <AbstractText Label="METHODS">second part</AbstractText>
      </Abstract></Article></PubmedArticle>
    </PubmedArticleSet>"""
    with patch.object(fetch_doi, "http_get_text",
                      new=AsyncMock(return_value=xml)):
        r = await fetch_doi.fetch_pubmed(
            "12345", capsule_dir=cap, cache_dir=tmp_path / "cache",
        )
    assert r is not None
    assert "BACKGROUND: first part" in r["abstract"]
    assert "METHODS: second part" in r["abstract"]
    assert (cap / "external" / "pubmed" / "12345.xml").exists()
    assert (cap / "external" / "pubmed" / "12345.json").exists()


@pytest.mark.asyncio
async def test_fetch_pubmed_handles_unparseable_xml(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    with patch.object(fetch_doi, "http_get_text",
                      new=AsyncMock(return_value="<not really xml")):
        r = await fetch_doi.fetch_pubmed(
            "999", capsule_dir=cap, cache_dir=tmp_path / "cache",
        )
    # XML parse failure → abstract None, but record still written
    assert r is not None
    assert r["abstract"] is None


@pytest.mark.asyncio
async def test_fetch_pmcid_for_doi(tmp_path):
    payload = {"records": [{"doi": "10.1/x", "pmcid": "PMC1234567"}]}
    with patch.object(fetch_doi, "http_get_json",
                      new=AsyncMock(return_value=payload)):
        r = await fetch_doi.fetch_pmcid_for_doi(
            "10.1/x", cache_dir=tmp_path / "cache",
        )
    assert r == "PMC1234567"


@pytest.mark.asyncio
async def test_fetch_pmcid_for_doi_no_record(tmp_path):
    with patch.object(fetch_doi, "http_get_json",
                      new=AsyncMock(return_value={"records": []})):
        r = await fetch_doi.fetch_pmcid_for_doi(
            "10.1/missing", cache_dir=tmp_path / "cache",
        )
    assert r is None
