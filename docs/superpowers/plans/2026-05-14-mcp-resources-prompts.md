# MCP resources + prompts — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship KB-browsing MCP resources (5.1) and canned MCP prompts (5.2) per the design spec at `docs/superpowers/specs/2026-05-14-mcp-resources-prompts-design.md`.

**Architecture:** Two new modules — `mcp/resources.py` and `mcp/prompts.py` — registered against the same `FastMCP("perspicacite")` instance in `server.py`. Resource readers reuse `mcp_state`; prompts are pure string builders.

**Tech Stack:** FastMCP 3.2, structlog, existing `MCPState`, Wave 4.3 `kb_log`.

---

### Task 1: Config knob for resource event bound

**Files:**
- Modify: `src/perspicacite/config/schema.py` (add `mcp_resource_max_events`)
- Test: `tests/unit/test_config_schema.py` (add assertion)

- [ ] **Step 1: Read** `src/perspicacite/config/schema.py` and find `KnowledgeBaseConfig`.

- [ ] **Step 2: Add field**

```python
# Inside KnowledgeBaseConfig, alongside other kb_log_* / orcid_* fields.
mcp_resource_max_events: int = Field(
    default=1000,
    description="Max KB-log events returned by the perspicacite://kb/{name}/log resource.",
)
```

- [ ] **Step 3: Add test**

```python
def test_mcp_resource_max_events_defaults_to_1000():
    cfg = KnowledgeBaseConfig()
    assert cfg.mcp_resource_max_events == 1000
```

- [ ] **Step 4: Run** `pytest tests/unit/test_config_schema.py -v -k mcp_resource_max_events`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(config): kb.mcp_resource_max_events knob (Wave 5.1)`

---

### Task 2: MCP resource readers (5.1)

**Files:**
- Create: `src/perspicacite/mcp/resources.py`
- Modify: `src/perspicacite/mcp/server.py` (register resources)
- Test: `tests/unit/test_mcp_resources.py`

- [ ] **Step 1: Write failing tests first** (TDD)

`tests/unit/test_mcp_resources.py`:

```python
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
        {"name": "bio",   "paper_count": 3, "chunk_count": 22},
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
```

- [ ] **Step 2: Run** `pytest tests/unit/test_mcp_resources.py -v`. Expected: all FAIL (`module not found`).

- [ ] **Step 3: Implement** `src/perspicacite/mcp/resources.py`:

```python
"""MCP resource readers for KB browsing (Wave 5.1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.mcp.resources")


def _get_state() -> Any:
    """Resolve the singleton MCP state (indirected for tests)."""
    from perspicacite.mcp.server import mcp_state
    if not getattr(mcp_state, "initialized", False):
        return None
    return mcp_state


def _err(code: str, **extra: Any) -> str:
    return json.dumps({"error": code, **extra})


async def kbs_resource() -> str:
    """Resource: list of all KBs."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kbs = await state.session_store.list_kbs()
        out = []
        for kb in kbs:
            out.append({
                "uri": f"perspicacite://kb/{kb.name}",
                "name": kb.name,
                "description": getattr(kb, "description", None),
                "paper_count": getattr(kb, "paper_count", 0),
                "chunk_count": getattr(kb, "chunk_count", 0),
                "created_at": str(getattr(kb, "created_at", "")) or None,
            })
        return json.dumps({"knowledge_bases": out})
    except Exception as e:
        logger.error("mcp_resource_kbs_error", error=str(e))
        return _err("kbs_resource_failed", message=str(e))


async def kb_resource(name: str) -> str:
    """Resource: a single KB's metadata."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if kb is None:
            return _err("kb_not_found", kb_name=name)
        return json.dumps({
            "name": kb.name,
            "description": getattr(kb, "description", None),
            "paper_count": getattr(kb, "paper_count", 0),
            "chunk_count": getattr(kb, "chunk_count", 0),
            "embedding_model": getattr(kb, "embedding_model", None),
            "collection_name": getattr(kb, "collection_name", None),
            "created_at": str(getattr(kb, "created_at", "")) or None,
            "updated_at": str(getattr(kb, "updated_at", "")) or None,
            "papers_uri": f"perspicacite://kb/{name}/papers",
            "log_uri": f"perspicacite://kb/{name}/log",
        })
    except Exception as e:
        logger.error("mcp_resource_kb_error", error=str(e), kb_name=name)
        return _err("kb_resource_failed", message=str(e))


def _log_path(state: Any, name: str) -> Path:
    log_dir = Path(getattr(state.config.knowledge_base, "log_dir", "data/kb_logs"))
    return log_dir / f"{name}.jsonl"


def _read_log_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # Tolerate partial last line (Wave 4.3 contract).
            if i == len(lines) - 1:
                logger.warning("mcp_resource_log_partial_line_skipped", path=str(path))
                continue
            logger.warning("mcp_resource_log_bad_line", path=str(path), line_no=i)
    return events


async def kb_papers_resource(name: str) -> str:
    """Resource: papers in a KB. Prefers kb_log, falls back to Chroma."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if kb is None:
            return _err("kb_not_found", kb_name=name)
        events = _read_log_lines(_log_path(state, name))
        added = [e for e in events if e.get("event") == "paper_added"]
        if added:
            papers = [
                {
                    "paper_id": e.get("paper_id"),
                    "title": e.get("title"),
                    "chunks": e.get("chunks", 0),
                }
                for e in added
            ]
            return json.dumps({"kb_name": name, "papers": papers})
        # Fallback: ask vector store.
        if state.vector_store is not None and hasattr(
            state.vector_store, "list_paper_ids_in_collection"
        ):
            rows = await state.vector_store.list_paper_ids_in_collection(
                getattr(kb, "collection_name", f"kb_{name}")
            )
            papers = [{"paper_id": pid, "title": title, "chunks": n} for (pid, title, n) in rows]
            return json.dumps({"kb_name": name, "papers": papers})
        return json.dumps({"kb_name": name, "papers": []})
    except Exception as e:
        logger.error("mcp_resource_kb_papers_error", error=str(e), kb_name=name)
        return _err("kb_papers_resource_failed", message=str(e))


async def kb_log_resource(name: str) -> str:
    """Resource: the most-recent N KB-log events."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if kb is None:
            return _err("kb_not_found", kb_name=name)
        events = _read_log_lines(_log_path(state, name))
        cap = int(getattr(state.config.knowledge_base, "mcp_resource_max_events", 1000))
        if len(events) > cap:
            events = events[-cap:]
        return json.dumps({"kb_name": name, "events": events})
    except Exception as e:
        logger.error("mcp_resource_kb_log_error", error=str(e), kb_name=name)
        return _err("kb_log_resource_failed", message=str(e))
```

- [ ] **Step 4: Register in `server.py`** — add near the existing `@mcp.resource("perspicacite://info")` block:

```python
from perspicacite.mcp import resources as _resources  # noqa: E402

@mcp.resource("perspicacite://kbs")
async def _kbs_resource() -> str:
    return await _resources.kbs_resource()

@mcp.resource("perspicacite://kb/{name}")
async def _kb_resource(name: str) -> str:
    return await _resources.kb_resource(name)

@mcp.resource("perspicacite://kb/{name}/papers")
async def _kb_papers_resource(name: str) -> str:
    return await _resources.kb_papers_resource(name)

@mcp.resource("perspicacite://kb/{name}/log")
async def _kb_log_resource(name: str) -> str:
    return await _resources.kb_log_resource(name)
```

- [ ] **Step 5: Run** `pytest tests/unit/test_mcp_resources.py -v`. Expected: all PASS. If `list_paper_ids_in_collection` doesn't exist on Chroma, that test will error — either add a no-op fallback method in `ChromaVectorStore` or adjust the test to monkeypatch instead. Prefer the no-op fallback (returns `[]`) so future Chroma versions can backfill.

- [ ] **Step 6: Commit** `feat(mcp): KBs as resources (perspicacite://kb/{name}/...) (Wave 5.1)`

---

### Task 3: ChromaVectorStore fallback for paper-listing

**Files:**
- Modify: `src/perspicacite/retrieval/chroma_store.py` (or wherever `ChromaVectorStore` lives)
- Test: covered by Task 2

- [ ] **Step 1: Find** the `ChromaVectorStore` class:

```bash
grep -nE "class ChromaVectorStore" src/perspicacite/retrieval/*.py
```

- [ ] **Step 2: Add helper** that returns `[(paper_id, title, chunk_count), ...]`:

```python
async def list_paper_ids_in_collection(self, collection_name: str) -> list[tuple[str, str, int]]:
    """Return distinct (paper_id, title, chunk_count) for the collection.

    Returns [] if the collection doesn't exist or the metadata doesn't
    carry paper_id (older KBs).
    """
    try:
        coll = self._client.get_collection(collection_name)
    except Exception:
        return []
    # Chroma .get() with no ids returns all docs. metadatas is a list of dicts.
    data = coll.get(include=["metadatas"])
    counts: dict[str, dict] = {}
    for meta in data.get("metadatas") or []:
        pid = meta.get("paper_id") if meta else None
        if not pid:
            continue
        entry = counts.setdefault(pid, {"title": meta.get("title", ""), "n": 0})
        entry["n"] += 1
    return [(pid, info["title"], info["n"]) for pid, info in counts.items()]
```

- [ ] **Step 3: Run** the relevant test from Task 2: `pytest tests/unit/test_mcp_resources.py::test_kb_papers_resource_falls_back_to_chroma_when_log_empty -v`. Expected: PASS.

- [ ] **Step 4: Commit** `feat(retrieval): list_paper_ids_in_collection helper on ChromaVectorStore`

---

### Task 4: MCP prompts (5.2)

**Files:**
- Create: `src/perspicacite/mcp/prompts.py`
- Modify: `src/perspicacite/mcp/server.py` (register prompts)
- Test: `tests/unit/test_mcp_prompts.py`

- [ ] **Step 1: Write failing tests first**

`tests/unit/test_mcp_prompts.py`:

```python
"""Tests for MCP canned-workflow prompts (Wave 5.2)."""
from __future__ import annotations

import pytest

from perspicacite.mcp.prompts import (
    compare_papers,
    ingest_dois,
    literature_review,
    screen_topic,
    summarize_kb,
)


def _content(msgs):
    """Return all message contents concatenated."""
    out = []
    for m in msgs:
        c = m["content"] if isinstance(m, dict) else m.content
        out.append(c if isinstance(c, str) else c.text)
    return "\n".join(out)


def test_literature_review_prompt_interpolates_args():
    msgs = literature_review(topic="exoplanet biosignatures", kb_name="astro", max_papers=20)
    body = _content(msgs)
    assert "exoplanet biosignatures" in body
    assert "astro" in body
    assert "20" in body


def test_compare_papers_prompt_includes_both_ids():
    msgs = compare_papers(paper_a="10.1/x", paper_b="10.2/y")
    body = _content(msgs)
    assert "10.1/x" in body and "10.2/y" in body


def test_summarize_kb_prompt_requires_kb_name():
    msgs = summarize_kb(kb_name="astro")
    body = _content(msgs)
    assert "astro" in body
    assert "summary" in body.lower() or "summarize" in body.lower()


def test_ingest_dois_prompt_renders_doi_list():
    msgs = ingest_dois(kb_name="astro", dois=["10.1/a", "10.2/b"])
    body = _content(msgs)
    assert "10.1/a" in body and "10.2/b" in body and "astro" in body


def test_screen_topic_prompt_threshold_appears_in_body():
    msgs = screen_topic(topic="black holes", kb_name="astro", threshold=0.75)
    body = _content(msgs)
    assert "0.75" in body
    assert "black holes" in body
```

- [ ] **Step 2: Run** `pytest tests/unit/test_mcp_prompts.py -v`. Expected: all FAIL (module not found).

- [ ] **Step 3: Implement** `src/perspicacite/mcp/prompts.py`:

```python
"""Canned MCP prompts (Wave 5.2)."""
from __future__ import annotations

from typing import Any


def _msg(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def literature_review(
    topic: str,
    kb_name: str | None = None,
    max_papers: int = 30,
) -> list[dict[str, Any]]:
    """Run a literature review on a topic.

    If `kb_name` is given, search that KB. Otherwise call
    `search_literature` across configured databases.
    """
    if kb_name:
        retrieval = (
            f"Use `search_knowledge_base` against the `{kb_name}` KB to find papers "
            f"about: {topic}. Limit to {max_papers} top hits."
        )
    else:
        retrieval = (
            f"Use `search_literature` to find up to {max_papers} papers on: {topic}. "
            "Pull from at least Crossref + OpenAlex."
        )
    return [
        _msg(
            f"I'd like a literature review on: **{topic}**.\n\n"
            f"{retrieval}\n\n"
            "Then call `generate_report` with `synthesis_style=\"literature_review\"`. "
            "Cover scope, methods, key findings, gaps, and recommended next reads. "
            "Cite every claim with the paper's DOI."
        )
    ]


def compare_papers(
    paper_a: str,
    paper_b: str,
    kb_name: str | None = None,
) -> list[dict[str, Any]]:
    """Side-by-side comparison of two papers."""
    extra = f" Use KB `{kb_name}` as context if helpful." if kb_name else ""
    return [
        _msg(
            f"Compare two papers side-by-side:\n"
            f"- A: `{paper_a}`\n"
            f"- B: `{paper_b}`\n\n"
            f"Fetch each via `get_paper_content`, then produce a table with rows for:\n"
            f"  research question, methods, dataset, key findings, limitations, "
            f"reproducibility.\n"
            f"Close with a 2-paragraph synthesis of where the papers agree, "
            f"where they diverge, and which holds up better.{extra}"
        )
    ]


def summarize_kb(kb_name: str, max_papers: int = 50) -> list[dict[str, Any]]:
    """Five-paragraph summary of a KB."""
    return [
        _msg(
            f"Summarize the knowledge base `{kb_name}` (up to {max_papers} papers). "
            "First call `search_knowledge_base` with a broad query to pull a "
            "representative sample, then produce a 5-paragraph summary covering:\n"
            "  1. Scope and time range of papers in the KB.\n"
            "  2. Top 3 thematic clusters (with paper counts each).\n"
            "  3. Methodological trends.\n"
            "  4. Open questions / visible gaps.\n"
            "  5. Three recommended next reads with DOIs."
        )
    ]


def ingest_dois(kb_name: str, dois: list[str]) -> list[dict[str, Any]]:
    """Ingest a list of DOIs into a KB."""
    doi_lines = "\n".join(f"  - {d}" for d in dois)
    return [
        _msg(
            f"Add these DOIs to KB `{kb_name}`:\n{doi_lines}\n\n"
            f"Call `add_dois_to_kb` with kb_name=`{kb_name}` and the DOI list. "
            "Then list per-DOI status: added / skipped (duplicate) / failed (with reason)."
        )
    ]


def screen_topic(
    topic: str,
    kb_name: str,
    threshold: float = 0.6,
) -> list[dict[str, Any]]:
    """Screen a KB for papers relevant to a topic above a confidence threshold."""
    return [
        _msg(
            f"Screen KB `{kb_name}` for relevance to: **{topic}**.\n"
            f"Call `screen_papers` with topic=`{topic}`, kb_name=`{kb_name}`, "
            f"threshold={threshold}.\n"
            "Report the matching papers ranked by score, with a one-line rationale each."
        )
    ]
```

- [ ] **Step 4: Register in `server.py`** — add alongside the resource block:

```python
from perspicacite.mcp import prompts as _prompts  # noqa: E402

@mcp.prompt()
def literature_review(topic: str, kb_name: str | None = None, max_papers: int = 30):
    """Run a literature review on a topic, optionally against a KB."""
    return _prompts.literature_review(topic, kb_name, max_papers)

@mcp.prompt()
def compare_papers(paper_a: str, paper_b: str, kb_name: str | None = None):
    """Compare two papers side-by-side."""
    return _prompts.compare_papers(paper_a, paper_b, kb_name)

@mcp.prompt()
def summarize_kb(kb_name: str, max_papers: int = 50):
    """Summarize an entire knowledge base in five paragraphs."""
    return _prompts.summarize_kb(kb_name, max_papers)

@mcp.prompt()
def ingest_dois(kb_name: str, dois: list[str]):
    """Ingest a list of DOIs into a KB."""
    return _prompts.ingest_dois(kb_name, dois)

@mcp.prompt()
def screen_topic(topic: str, kb_name: str, threshold: float = 0.6):
    """Screen a KB for papers relevant to a topic."""
    return _prompts.screen_topic(topic, kb_name, threshold)
```

- [ ] **Step 5: Run** `pytest tests/unit/test_mcp_prompts.py -v`. Expected: all PASS.

- [ ] **Step 6: Commit** `feat(mcp): canned prompts — lit-review, compare, summarize, ingest, screen (Wave 5.2)`

---

### Task 5: Operator docs

**Files:**
- Create: `docs/mcp-resources-prompts-2026-05-14.md`

- [ ] **Step 1: Write** a ~80-line operator guide:
  - Section "Resources" — list the 4 new URIs + payload shapes (copy from spec).
  - Section "Prompts" — list the 5 prompts + arg signatures.
  - Section "Trying them in Claude Desktop" — how to add the server, where prompts appear in the "/" menu.
  - Section "Config" — `kb.mcp_resource_max_events`.

- [ ] **Step 2: Update `.gitignore` allowlist** if needed (`!docs/mcp-resources-prompts-*.md`).

- [ ] **Step 3: Commit** `docs(mcp): resources + prompts operator guide (Wave 5)`

---

### Task 6: Final summary commit + roadmap update

- [ ] **Step 1: Tick Wave 5.1 + 5.2** in `docs/roadmap-2026-05-followups.md` (add ✅ markers per the existing pattern).

- [ ] **Step 2: Commit** `docs(roadmap): Wave 5.1 + 5.2 shipped`
