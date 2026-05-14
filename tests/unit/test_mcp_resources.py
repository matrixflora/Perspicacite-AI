"""Tests for MCP KB resources (Wave 5.1)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.mcp import resources as res
from perspicacite.mcp.server import MCPState


def _make_state(tmp_path: Path, kbs: list[dict]) -> MCPState:
    state = MCPState()
    state.initialized = True
    state.session_store = MagicMock()
    # list_kbs returns list of objects with .name/.description/.paper_count/.chunk_count
    fake_kbs = []
    for kb in kbs:
        fake = MagicMock()
        fake.name = kb["name"]
        fake.description = kb.get("description", "")
        fake.paper_count = kb.get("paper_count", 0)
        fake.chunk_count = kb.get("chunk_count", 0)
        fake.embedding_model = kb.get("embedding_model", "all-MiniLM-L6-v2")
        fake.collection_name = kb.get("collection_name", f"kb_{kb['name']}")
        fake_kbs.append(fake)
    state.session_store.list_kbs = AsyncMock(return_value=fake_kbs)

    async def _get(name):
        for kb in fake_kbs:
            if kb.name == name:
                return kb
        return None

    state.session_store.get_kb_metadata = AsyncMock(side_effect=_get)
    state.config = MagicMock()
    state.config.knowledge_base = MagicMock()
    state.config.knowledge_base.log_dir = tmp_path / "kb_logs"
    state.config.knowledge_base.mcp_resource_max_events = 1000
    return state


@pytest.mark.asyncio
async def test_kbs_resource_lists_all(tmp_path, monkeypatch):
    state = _make_state(tmp_path, [
        {"name": "astro", "paper_count": 5, "chunk_count": 40},
        {"name": "bio", "paper_count": 3, "chunk_count": 22},
    ])
    monkeypatch.setattr(res, "_get_state", lambda: state)
    payload = json.loads(await res.kbs_resource())
    assert {kb["name"] for kb in payload["knowledge_bases"]} == {"astro", "bio"}
    assert all(kb["uri"].startswith("perspicacite://kb/") for kb in payload["knowledge_bases"])


@pytest.mark.asyncio
async def test_kb_resource_returns_metadata_with_subresource_uris(tmp_path, monkeypatch):
    state = _make_state(tmp_path, [{"name": "astro", "paper_count": 5}])
    monkeypatch.setattr(res, "_get_state", lambda: state)
    payload = json.loads(await res.kb_resource("astro"))
    assert payload["name"] == "astro"
    assert payload["papers_uri"] == "perspicacite://kb/astro/papers"
    assert payload["log_uri"] == "perspicacite://kb/astro/log"


@pytest.mark.asyncio
async def test_kb_resource_missing_returns_error_payload(tmp_path, monkeypatch):
    state = _make_state(tmp_path, [])
    monkeypatch.setattr(res, "_get_state", lambda: state)
    payload = json.loads(await res.kb_resource("ghost"))
    assert payload["error"] == "kb_not_found"
    assert payload["kb_name"] == "ghost"


@pytest.mark.asyncio
async def test_kb_papers_resource_reads_from_log_when_available(tmp_path, monkeypatch):
    state = _make_state(tmp_path, [{"name": "astro", "paper_count": 2}])
    log_dir = tmp_path / "kb_logs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "astro.jsonl"
    log_path.write_text(
        '{"event":"paper_added","kb_name":"astro","paper_id":"10.1/a","title":"A","chunks":3,"ts":1}\n'
        '{"event":"paper_added","kb_name":"astro","paper_id":"10.1/b","title":"B","chunks":5,"ts":2}\n'
    )
    monkeypatch.setattr(res, "_get_state", lambda: state)
    payload = json.loads(await res.kb_papers_resource("astro"))
    pids = {p["paper_id"] for p in payload["papers"]}
    assert pids == {"10.1/a", "10.1/b"}


@pytest.mark.asyncio
async def test_kb_papers_resource_falls_back_to_chroma_when_log_empty(tmp_path, monkeypatch):
    state = _make_state(tmp_path, [{"name": "astro", "paper_count": 1}])
    # No log file. Mock vector_store to return distinct paper_ids.
    state.vector_store = MagicMock()
    state.vector_store.list_paper_ids_in_collection = AsyncMock(
        return_value=[("10.1/c", "Paper C", 7)]
    )
    monkeypatch.setattr(res, "_get_state", lambda: state)
    payload = json.loads(await res.kb_papers_resource("astro"))
    assert payload["papers"][0]["paper_id"] == "10.1/c"


@pytest.mark.asyncio
async def test_kb_log_resource_bounded_at_max_events(tmp_path, monkeypatch):
    state = _make_state(tmp_path, [{"name": "astro"}])
    state.config.knowledge_base.mcp_resource_max_events = 3
    log_dir = tmp_path / "kb_logs"
    log_dir.mkdir(parents=True)
    lines = "\n".join(
        f'{{"event":"paper_added","kb_name":"astro","paper_id":"10.1/{i}","title":"P{i}","chunks":1,"ts":{i}}}'
        for i in range(10)
    ) + "\n"
    (log_dir / "astro.jsonl").write_text(lines)
    monkeypatch.setattr(res, "_get_state", lambda: state)
    payload = json.loads(await res.kb_log_resource("astro"))
    assert len(payload["events"]) == 3
    # Most-recent first or chronological-last — spec says "most-recent",
    # so the last 3 entries (paper_ids 7,8,9 by ts) must be present.
    pids = {e["paper_id"] for e in payload["events"]}
    assert pids == {"10.1/7", "10.1/8", "10.1/9"}


@pytest.mark.asyncio
async def test_resource_when_state_not_initialised_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(res, "_get_state", lambda: None)
    payload = json.loads(await res.kbs_resource())
    assert payload["error"] == "mcp_state_not_initialized"
