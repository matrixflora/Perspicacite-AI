import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.pipeline.external import fetch_github


@pytest.mark.asyncio
async def test_fetch_github_repo_caches(tmp_path):
    payload = {"full_name": "a/b", "default_branch": "main"}
    with patch.object(fetch_github, "http_get_json",
                      new=AsyncMock(return_value=payload)) as m:
        r = await fetch_github.fetch_github_repo("a/b", cache_dir=tmp_path)
        assert r == payload
        m.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_github_docs_readme_only_when_no_extra(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    with patch.object(fetch_github, "http_get_text",
                      new=AsyncMock(side_effect=lambda *a, **kw: "# README\nbody")):
        r = await fetch_github.fetch_github_docs(
            "owner", "repo", capsule_dir=cap, cache_dir=tmp_path / "cache",
            extra_docs=False,
        )
    readme = cap / "external" / "github" / "owner__repo" / "README.md"
    assert readme.exists()
    assert readme.read_text() == "# README\nbody"
    assert r["files_fetched"] == 1
    assert "README.md" in r["paths"]


@pytest.mark.asyncio
async def test_fetch_github_docs_extra_walks_tree(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    tree = {
        "tree": [
            {"path": "README.md", "type": "blob", "sha": "x", "size": 100},
            {"path": "docs/guide.md", "type": "blob", "sha": "y", "size": 1000},
            {"path": "requirements.txt", "type": "blob", "sha": "z", "size": 50},
            {"path": "notebooks/demo.ipynb", "type": "blob", "sha": "n", "size": 5000},
            {"path": "scripts/run.py", "type": "blob", "sha": "s", "size": 200},
            {"path": "data/big.csv", "type": "blob", "sha": "d", "size": 999999},
        ]
    }
    notebook_raw = json.dumps({"cells": [
        {"cell_type": "code", "source": ["print('hi')"], "execution_count": 1,
         "outputs": [{"output_type": "stream", "text": "hi"}]}
    ]})

    async def fake_text(url, **kw):
        if "/readme" in url:
            return "# README"
        if "/git/trees/" in url:
            return json.dumps(tree)
        if "docs/guide.md" in url:
            return "# Guide content"
        if "requirements.txt" in url:
            return "pytest\nhttpx\n"
        if "demo.ipynb" in url:
            return notebook_raw
        if "scripts/run.py" in url:
            return "print('run')\n"
        return None

    with patch.object(fetch_github, "http_get_text",
                      new=AsyncMock(side_effect=fake_text)):
        r = await fetch_github.fetch_github_docs(
            "owner", "repo", capsule_dir=cap, cache_dir=tmp_path / "cache",
            extra_docs=True,
            text_file_extensions=[".py", ".R"],
        )

    base = cap / "external" / "github" / "owner__repo"
    assert (base / "README.md").exists()
    assert (base / "tree.json").exists()
    assert (base / "docs" / "guide.md").exists()
    assert (base / "env" / "requirements.txt").exists()
    assert (base / "notebooks" / "demo.ipynb").exists()
    assert (base / "scripts" / "run.py").exists()
    assert (base / "data_manifest.json").exists()
    assert (base / ".extra_fetched").exists()

    # Notebook outputs were stripped.
    nb_loaded = json.loads((base / "notebooks" / "demo.ipynb").read_text())
    assert nb_loaded["cells"][0]["outputs"] == []
    assert nb_loaded["cells"][0]["execution_count"] is None

    # Data dir was NOT downloaded — only listed in manifest.
    manifest = json.loads((base / "data_manifest.json").read_text())
    assert any(d["path"] == "data/big.csv" for d in manifest)

    assert r["files_fetched"] >= 4


@pytest.mark.asyncio
async def test_sentinel_skips_second_run(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    base = cap / "external" / "github" / "owner__repo"
    base.mkdir(parents=True)
    (base / "README.md").write_text("cached")
    (base / ".extra_fetched").touch()

    with patch.object(fetch_github, "http_get_text",
                      new=AsyncMock(side_effect=AssertionError("should not call"))):
        r = await fetch_github.fetch_github_docs(
            "owner", "repo", capsule_dir=cap, cache_dir=tmp_path / "cache",
            extra_docs=True,
        )
    assert r["files_fetched"] == 0
