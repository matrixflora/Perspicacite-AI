from click.testing import CliRunner

from perspicacite.cli import cli


def test_pubmed_search_cli_help():
    res = CliRunner().invoke(cli, ["pubmed-search", "--help"])
    assert res.exit_code == 0


def test_pubmed_search_cli_runs(tmp_path, monkeypatch):
    import perspicacite.search.pubmed as pm
    from perspicacite.models.papers import Author, Paper, PaperSource

    class FakeAdapter:
        def __init__(self, *a, **k):
            pass

        async def search(self, query, max_results=20, year_min=None, year_max=None, **kw):
            return [
                Paper(
                    id="10.1/x",
                    title="The Title",
                    authors=[Author(name="Doe J")],
                    year=2020,
                    doi="10.1/x",
                    abstract="abstract text",
                    journal="J. Test",
                    source=PaperSource.WEB_SEARCH,
                    metadata={"pmid": "1"},
                )
            ]

    monkeypatch.setattr(pm, "PubMedSearchAdapter", FakeAdapter)

    out = tmp_path / "out.bib"
    res = CliRunner().invoke(
        cli,
        [
            "pubmed-search",
            "crispr gene editing",
            "--max",
            "1",
            "--output",
            str(out),
            "--email",
            "me@example.org",
        ],
    )
    assert res.exit_code == 0, res.output
    assert out.exists()
    body = out.read_text()
    assert "The Title" in body and "10.1/x" in body


def test_pubmed_search_cli_no_output(tmp_path, monkeypatch):
    import perspicacite.search.pubmed as pm
    from perspicacite.models.papers import Paper, PaperSource

    class FakeAdapter:
        def __init__(self, *a, **k):
            pass

        async def search(self, *a, **k):
            return [
                Paper(
                    id="pmid:9",
                    title="T2",
                    authors=[],
                    year=2019,
                    doi=None,
                    abstract="x",
                    journal="K",
                    source=PaperSource.WEB_SEARCH,
                    metadata={"pmid": "9"},
                )
            ]

    monkeypatch.setattr(pm, "PubMedSearchAdapter", FakeAdapter)
    res = CliRunner().invoke(cli, ["pubmed-search", "q", "--email", "me@example.org"])
    assert res.exit_code == 0, res.output
    assert "T2" in res.output  # prints a brief listing
