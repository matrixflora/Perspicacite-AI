import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.external import fetch_orchestrator as fo


def _capsule(tmp_path: Path, resources: list[dict]) -> Path:
    cap = tmp_path / "cap"
    cap.mkdir()
    (cap / "resources.json").write_text(json.dumps(resources))
    return cap


def _app_state(tmp_path: Path, fetch_on_demand: bool = True):
    s = MagicMock()
    s.config.external_resources.fetch_on_demand = fetch_on_demand
    s.config.external_resources.cache_dir = tmp_path / "cache"
    s.config.external_resources.cache_ttl_days = 30
    s.config.external_resources.text_file_extensions = [".md", ".py"]
    s.config.external_resources.zenodo_max_bytes_per_file = 500_000
    s.config.external_resources.zenodo_max_bytes_per_record = 5_000_000
    return s


def _registry():
    r = MagicMock()
    r.publish = AsyncMock()
    r.finish = AsyncMock()
    r.fail = AsyncMock()
    return r


def _paper() -> Paper:
    return Paper(id="doi:10.1/x", title="P", source=PaperSource.LOCAL)


@pytest.mark.asyncio
async def test_disabled_short_circuits(tmp_path):
    cap = _capsule(tmp_path, [])
    app_state = _app_state(tmp_path, fetch_on_demand=False)
    reg = _registry()
    r = await fo.fetch_paper_resources(
        paper=_paper(), capsule_dir=cap, kinds=None,
        app_state=app_state, registry=reg, job_id="j",
    )
    assert r == {"disabled": True}
    reg.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_resources_json_fails(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    app_state = _app_state(tmp_path)
    reg = _registry()
    r = await fo.fetch_paper_resources(
        paper=_paper(), capsule_dir=cap, kinds=None,
        app_state=app_state, registry=reg, job_id="j",
    )
    assert r == {}
    reg.fail.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatches_github_resource(tmp_path):
    cap = _capsule(tmp_path, [
        {"kind": "github", "identifier": "owner/repo",
         "resource_id": "github:owner/repo", "url": "..."},
    ])
    app_state = _app_state(tmp_path)
    reg = _registry()
    with patch.object(fo, "fetch_github_docs",
                      new=AsyncMock(return_value={"files_fetched": 3, "bytes_fetched": 500, "paths": []})) as m:
        r = await fo.fetch_paper_resources(
            paper=_paper(), capsule_dir=cap, kinds=None,
            app_state=app_state, registry=reg, job_id="j",
        )
    m.assert_awaited_once()
    assert r["github"] == 1
    assert r["files_fetched"] == 3


@pytest.mark.asyncio
async def test_kinds_filter_skips_unselected(tmp_path):
    cap = _capsule(tmp_path, [
        {"kind": "github", "identifier": "a/b",
         "resource_id": "github:a/b", "url": "..."},
        {"kind": "zenodo", "identifier": "999",
         "resource_id": "zenodo:999", "url": "..."},
    ])
    app_state = _app_state(tmp_path)
    reg = _registry()
    with patch.object(fo, "fetch_github_docs",
                      new=AsyncMock(return_value={"files_fetched": 0, "bytes_fetched": 0, "paths": []})), \
         patch.object(fo, "fetch_zenodo",
                      new=AsyncMock(side_effect=AssertionError("zenodo should not be called"))):
        r = await fo.fetch_paper_resources(
            paper=_paper(), capsule_dir=cap, kinds=["github"],
            app_state=app_state, registry=reg, job_id="j",
        )
    assert r["github"] == 1
    assert r["zenodo"] == 0


@pytest.mark.asyncio
async def test_doi_calls_both_crossref_and_unpaywall(tmp_path):
    cap = _capsule(tmp_path, [
        {"kind": "doi", "identifier": "10.1/x",
         "resource_id": "doi:10.1/x", "url": "..."},
    ])
    app_state = _app_state(tmp_path)
    reg = _registry()
    with patch.object(fo, "fetch_crossref",
                      new=AsyncMock(return_value={"ok": 1})) as crossref, \
         patch.object(fo, "fetch_unpaywall",
                      new=AsyncMock(return_value={"is_oa": True})) as unpaywall:
        r = await fo.fetch_paper_resources(
            paper=_paper(), capsule_dir=cap, kinds=None,
            app_state=app_state, registry=reg, job_id="j",
        )
    assert r["doi"] == 1
    crossref.assert_awaited_once()
    unpaywall.assert_awaited_once()


@pytest.mark.asyncio
async def test_resource_failure_emits_progress_and_continues(tmp_path):
    cap = _capsule(tmp_path, [
        {"kind": "github", "identifier": "a/b",
         "resource_id": "github:a/b", "url": "..."},
        {"kind": "doi", "identifier": "10.1/y",
         "resource_id": "doi:10.1/y", "url": "..."},
    ])
    app_state = _app_state(tmp_path)
    reg = _registry()
    with patch.object(fo, "fetch_github_docs",
                      new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(fo, "fetch_crossref",
                      new=AsyncMock(return_value={"ok": 1})), \
         patch.object(fo, "fetch_unpaywall",
                      new=AsyncMock(return_value={"is_oa": False})):
        r = await fo.fetch_paper_resources(
            paper=_paper(), capsule_dir=cap, kinds=None,
            app_state=app_state, registry=reg, job_id="j",
        )
    # GitHub failed, DOI succeeded
    assert r["github"] == 0
    assert r["doi"] == 1
    # publish was called with at least one "failed" event
    calls = [c.args[1] for c in reg.publish.await_args_list]
    assert any(c.get("status") == "failed" for c in calls)
    assert any(c.get("status") == "fetched" for c in calls)
