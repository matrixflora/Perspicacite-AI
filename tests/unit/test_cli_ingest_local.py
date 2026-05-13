"""CLI: ingest-local subcommand calls worker."""

from __future__ import annotations

from types import SimpleNamespace

from click.testing import CliRunner

from perspicacite.cli import cli


def test_ingest_local_help():
    runner = CliRunner()
    r = runner.invoke(cli, ["ingest-local", "--help"])
    assert r.exit_code == 0, r.output
    assert "--kb" in r.output
    assert "--path" in r.output


def test_ingest_local_calls_worker(tmp_path, monkeypatch):
    f = tmp_path / "x.md"
    f.write_text("# t\n\nb")
    called: dict = {}

    async def _ingest(**kwargs):
        called.update(kwargs)
        return {"added_chunks": 1, "files": 1}

    async def _noop(self, *a, **kw):
        return None

    monkeypatch.setattr(
        "perspicacite.integrations.local_docs.ingest_local_documents", _ingest,
    )
    monkeypatch.setattr(
        "perspicacite.web.state.AppState.initialize", _noop, raising=False,
    )

    runner = CliRunner()
    r = runner.invoke(
        cli,
        ["ingest-local", "--kb", "mykb", "--path", str(f)],
        obj={"config": SimpleNamespace()},
    )
    assert r.exit_code == 0, r.output
    assert called.get("kb_name") == "mykb"
    assert any(str(f) in str(p) for p in called.get("paths", []))
