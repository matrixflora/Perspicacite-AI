import pytest

from perspicacite.search.pubmed import PubMedConfigError, PubMedSearchAdapter


def test_pubmed_requires_real_email():
    with pytest.raises(PubMedConfigError):
        PubMedSearchAdapter(email="")
    with pytest.raises(PubMedConfigError):
        PubMedSearchAdapter(email="you@example.com")  # obvious placeholder


@pytest.mark.asyncio
async def test_pubmed_search_parses(monkeypatch):
    import perspicacite.search.pubmed as pm

    class FakeEntrez:
        email = None
        api_key = None

        @staticmethod
        def esearch(**kw):
            return object()  # handle, content irrelevant since read() is faked

        @staticmethod
        def read(handle):
            return {"IdList": ["111", "222"]}

        @staticmethod
        def efetch(**kw):
            return object()

    monkeypatch.setattr(pm, "Entrez", FakeEntrez, raising=False)
    monkeypatch.setattr(
        pm,
        "_parse_efetch",
        lambda handle: [
            {
                "pmid": "111",
                "title": "Paper One",
                "year": 2020,
                "doi": "10.1/one",
                "abstract": "a",
                "journal": "J",
                "authors": ["Doe J"],
            },
            {
                "pmid": "222",
                "title": "Paper Two",
                "year": 2021,
                "doi": None,
                "abstract": "b",
                "journal": "K",
                "authors": [],
            },
        ],
        raising=False,
    )

    adapter = PubMedSearchAdapter(email="researcher@university.edu")
    papers = await adapter.search("crispr", max_results=5)
    assert len(papers) == 2
    assert papers[0].title == "Paper One"
    assert papers[0].doi == "10.1/one"
    assert papers[0].metadata.get("pmid") == "111"
    assert papers[0].year == 2020
    # paper with no DOI still gets an id
    assert papers[1].id and papers[1].doi is None


@pytest.mark.asyncio
async def test_pubmed_search_no_results(monkeypatch):
    import perspicacite.search.pubmed as pm

    class FakeEntrez:
        email = None
        api_key = None

        @staticmethod
        def esearch(**kw):
            return object()

        @staticmethod
        def read(handle):
            return {"IdList": []}

        @staticmethod
        def efetch(**kw):
            return object()

    monkeypatch.setattr(pm, "Entrez", FakeEntrez, raising=False)
    adapter = PubMedSearchAdapter(email="researcher@university.edu")
    papers = await adapter.search("nothingmatchesthis", max_results=5)
    assert papers == []
