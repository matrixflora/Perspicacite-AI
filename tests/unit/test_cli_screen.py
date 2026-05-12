from click.testing import CliRunner

from perspicacite.cli import cli


def test_screen_papers_cli_help():
    res = CliRunner().invoke(cli, ["screen-papers", "--help"])
    assert res.exit_code == 0
    assert "screen" in res.output.lower()


def test_screen_papers_cli_bm25(tmp_path):
    refs = tmp_path / "refs.bib"
    refs.write_text(
        "@article{r1, title={protein neural networks},"
        " abstract={deep learning protein structure prediction}}\n"
    )
    cand = tmp_path / "cand.bib"
    cand.write_text(
        "@article{c1, title={Deep nets for proteins}, abstract={neural protein folding}}\n"
        "@article{c2, title={Renaissance art history}, abstract={oil canvas Florence}}\n"
    )
    out = tmp_path / "out.bib"
    res = CliRunner().invoke(
        cli,
        [
            "screen-papers",
            "--input",
            str(refs),
            "--candidates",
            str(cand),
            "--output",
            str(out),
            "--method",
            "bm25",
            "--threshold",
            "0.0",
        ],
    )
    assert res.exit_code == 0, res.output
    assert out.exists()
    body = out.read_text()
    assert (
        "c1" in body
    )  # the relevant candidate is kept (threshold 0.0 keeps both, but c1 must be present)


def test_screen_papers_cli_csv(tmp_path):
    refs = tmp_path / "refs.bib"
    refs.write_text("@article{r1, title={alpha beta}, abstract={alpha beta gamma}}\n")
    cand = tmp_path / "cand.bib"
    cand.write_text("@article{c1, title={alpha beta}, abstract={alpha beta}}\n")
    out = tmp_path / "out.bib"
    csvp = tmp_path / "report.csv"
    res = CliRunner().invoke(
        cli,
        [
            "screen-papers",
            "--input",
            str(refs),
            "--candidates",
            str(cand),
            "--output",
            str(out),
            "--threshold",
            "0.0",
            "--csv",
            str(csvp),
        ],
    )
    assert res.exit_code == 0, res.output
    assert csvp.exists() and "title" in csvp.read_text()


def test_screen_papers_cli_llm_not_wired(tmp_path):
    refs = tmp_path / "r.bib"
    refs.write_text("@article{r1, title={x}, abstract={x}}\n")
    cand = tmp_path / "c.bib"
    cand.write_text("@article{c1, title={y}, abstract={y}}\n")
    out = tmp_path / "o.bib"
    res = CliRunner().invoke(
        cli,
        [
            "screen-papers",
            "--input",
            str(refs),
            "--candidates",
            str(cand),
            "--output",
            str(out),
            "--method",
            "llm",
        ],
    )
    # LLM screening from the CLI is not wired in v1 -> non-zero exit with a clear message
    assert res.exit_code != 0
    assert "bm25" in res.output.lower() or "llm" in res.output.lower()
