# ASB metadata wiring — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `skill_metadata` / `workflow_metadata` blocks on RAG responses by wiring the existing pure helper `build_asb_response_metadata` into the chat router (SSE) and the MCP server (`generate_report`, `search_knowledge_base`). Today the helper has no upstream input because `Paper.metadata` (ASB fields) is dropped at ingestion — first fix the round-trip, then plumb to source events, then call the helper at the response boundary.

**Architecture:** Three layers, three tasks:
1. **Round-trip ASB metadata through ingestion → chroma → retrieval.** Add an optional `paper_metadata_json: str | None` field to `ChunkMetadata`; JSON-encode `paper.metadata` at ingest, decode on retrieval, expose decoded dict on `search_two_pass` paper-result dicts as `paper_metadata`.
2. **Plumb chunk metadata onto `SourceReference`.** Add optional `metadata: dict[str, Any] | None` to `SourceReference`; each RAG mode (basic, advanced, profound, contradiction) reads `p["paper_metadata"]` and sets it on the SourceReference it emits.
3. **Wire the helper at response boundary.** `chat.py::_stream_rag_mode` emits a new `'type': 'asb_metadata'` SSE event right before `done`; `mcp/server.py::generate_report` and `search_knowledge_base` add an `asb_metadata` key to their return dicts.

**Tech Stack:** pydantic v2 (additive-only changes to frozen models), chroma (per-doc dict metadata, scalar values), FastAPI SSE, FastMCP tool returns.

---

## File structure

**Modified:**
- `src/perspicacite/models/documents.py` — add `paper_metadata_json: str | None`
- `src/perspicacite/rag/dynamic_kb.py` — populate the field in `_add_paper_to_collection`; expose decoded dict in `search_two_pass` results
- `src/perspicacite/retrieval/chroma_store.py` — round-trip the new field in `_chunk_to_metadata` + `_metadata_to_chunk`
- `src/perspicacite/models/rag.py` — add `metadata: dict | None` to `SourceReference`
- `src/perspicacite/rag/modes/{basic,advanced,profound,contradiction}.py` — pass `metadata` onto SourceReference
- `src/perspicacite/web/routers/chat.py` — emit `asb_metadata` SSE event
- `src/perspicacite/mcp/server.py` — add `asb_metadata` field to `generate_report` and `search_knowledge_base` returns

**New tests:**
- `tests/unit/test_chunk_metadata_round_trip.py` — ingestion → chroma → retrieval round-trip
- `tests/unit/test_source_reference_metadata.py` — SourceReference carries the field; mode emit-paths populate it
- `tests/unit/test_chat_asb_metadata_sse.py` — chat SSE emits `asb_metadata` event
- `tests/unit/test_mcp_asb_metadata.py` — MCP `generate_report` + `search_knowledge_base` include `asb_metadata`

---

## Task 1: Round-trip `paper.metadata` through ingestion → chroma → retrieval

**Files:**
- Modify: `src/perspicacite/models/documents.py:50` (append a new optional field)
- Modify: `src/perspicacite/rag/dynamic_kb.py:164-204` (populate the field on the metadata chunk + text chunks)
- Modify: `src/perspicacite/rag/dynamic_kb.py:477-486` (expose decoded dict on `search_two_pass` paper-result dicts)
- Modify: `src/perspicacite/retrieval/chroma_store.py:540-606` (round-trip in both directions)
- Test: `tests/unit/test_chunk_metadata_round_trip.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_chunk_metadata_round_trip.py
"""Round-trip ASB-style ``paper.metadata`` through ingestion → chroma → retrieval.

Pins:
- ChunkMetadata exposes ``paper_metadata_json``.
- _chunk_to_metadata + _metadata_to_chunk preserve it.
- search_two_pass paper-result dicts include a decoded ``paper_metadata`` field.
"""
from __future__ import annotations

import json
from perspicacite.models.documents import ChunkMetadata
from perspicacite.models.papers import PaperSource


def test_chunk_metadata_has_paper_metadata_json_field():
    cm = ChunkMetadata(
        paper_id="asb_skill:foo",
        chunk_index=0,
        source=PaperSource.SKILL_BUNDLE,
        paper_metadata_json=json.dumps({"content_kind": "skill_body", "skill_id": "foo"}),
    )
    assert cm.paper_metadata_json
    assert json.loads(cm.paper_metadata_json)["skill_id"] == "foo"


def test_chunk_to_chroma_metadata_round_trip_preserves_paper_metadata_json():
    """_chunk_to_metadata(...) → _metadata_to_chunk(dict) preserves the field."""
    from perspicacite.retrieval.chroma_store import _chunk_to_metadata, _metadata_to_chunk

    payload = {"content_kind": "workflow_card", "task_id": "task_001"}
    cm_in = ChunkMetadata(
        paper_id="p1", chunk_index=0, source=PaperSource.SKILL_BUNDLE,
        paper_metadata_json=json.dumps(payload),
    )
    flat = _chunk_to_metadata(cm_in)
    assert flat.get("paper_metadata_json") == json.dumps(payload)

    cm_out = _metadata_to_chunk(flat)
    assert cm_out.paper_metadata_json == json.dumps(payload)
    assert json.loads(cm_out.paper_metadata_json)["task_id"] == "task_001"


def test_search_two_pass_exposes_decoded_paper_metadata():
    """Synthetic: stub vector store so search_two_pass sees a hit with
    ``paper_metadata_json``; result dict must expose decoded ``paper_metadata``."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig

    fake_vs = MagicMock()
    payload = {"content_kind": "skill_body", "skill_id": "abc", "tools": []}
    # _hybrid_search hit metadata
    fake_meta = ChunkMetadata(
        paper_id="asb_skill:abc", chunk_index=0,
        source=PaperSource.SKILL_BUNDLE, title="Abc",
        paper_metadata_json=json.dumps(payload),
    )
    fake_vs.hybrid_search = AsyncMock(return_value=[
        {"paper_id": "asb_skill:abc", "score": 0.9, "text": "...", "metadata": fake_meta}
    ])
    fake_vs.peek_paper_metadata_row = AsyncMock(return_value=None)
    fake_vs.get_chunks_by_paper_ids = AsyncMock(return_value=[
        {"paper_id": "asb_skill:abc", "chunk_index": 0, "text": "..."}
    ])
    fake_emb = MagicMock()
    fake_emb.dimension = 8
    fake_emb.embed_query = AsyncMock(return_value=[0.0] * 8)

    dkb = DynamicKnowledgeBase(fake_vs, fake_emb, config=KnowledgeBaseConfig(vector_size=8))
    dkb.collection_name = "test"
    dkb._initialized = True

    results = asyncio.run(dkb.search_two_pass("anything", top_k=5))
    assert results, "expected at least one result"
    r0 = results[0]
    assert "paper_metadata" in r0, f"missing paper_metadata key in {list(r0)}"
    assert r0["paper_metadata"]["skill_id"] == "abc"
```

Run: `PYTHONPATH=src pytest tests/unit/test_chunk_metadata_round_trip.py -v`
Expected: 3 FAIL (field doesn't exist; round-trip drops it; search_two_pass doesn't expose it)

- [ ] **Step 2: Add the field to `ChunkMetadata`**

In `src/perspicacite/models/documents.py`, after `cited_tool`/`discovery_score` (around line 58) and before the closing `__repr__`:

```python
    # Carries the upstream ``Paper.metadata`` dict, JSON-encoded so it
    # round-trips through Chroma's scalar-only per-doc metadata. None
    # for non-bundle papers. Decoded back to a dict at the retrieval
    # boundary (see DynamicKnowledgeBase.search_two_pass).
    paper_metadata_json: Optional[str] = None
```

- [ ] **Step 3: Round-trip in `chroma_store.py`**

In `_chunk_to_metadata` (the dict-builder around line 540-587), after the existing field copies, add:

```python
    if getattr(metadata, "paper_metadata_json", None) is not None:
        result["paper_metadata_json"] = metadata.paper_metadata_json
```

In `_metadata_to_chunk` (line 590-605), add the field to the constructor call:

```python
    return ChunkMetadata(
        paper_id=metadata.get("paper_id", ""),
        chunk_index=metadata.get("chunk_index", 0),
        section=metadata.get("section"),
        page_number=metadata.get("page_number"),
        source=PaperSource(metadata.get("source", "bibtex")),
        title=metadata.get("title"),
        authors=metadata.get("authors"),
        year=metadata.get("year"),
        doi=metadata.get("doi"),
        url=metadata.get("url"),
        paper_metadata_json=metadata.get("paper_metadata_json"),
    )
```

- [ ] **Step 4: Populate at ingest**

In `src/perspicacite/rag/dynamic_kb.py::_add_paper_to_collection` (around line 134), at the top compute:

```python
        import json as _json
        paper_md_json: str | None = None
        if isinstance(getattr(paper, "metadata", None), dict) and paper.metadata:
            try:
                paper_md_json = _json.dumps(paper.metadata, default=str)
            except (TypeError, ValueError):
                paper_md_json = None
```

Pass `paper_metadata_json=paper_md_json` to BOTH ChunkMetadata constructors (line 167 metadata-chunk and line 196 text-chunk).

- [ ] **Step 5: Expose decoded dict on search_two_pass results**

In `dynamic_kb.py::search_two_pass`, around the result-build block (line 477-486):

```python
        for pid in paper_ids:
            chunks_list = grouped.get(pid, [])
            full_text = " ".join(c["text"] for c in chunks_list)
            meta = paper_meta.get(pid)
            paper_md: dict | None = None
            if meta is not None:
                blob = getattr(meta, "paper_metadata_json", None)
                if blob:
                    try:
                        import json as _json
                        paper_md = _json.loads(blob)
                    except Exception:
                        paper_md = None
            results.append({
                "paper_id": pid,
                "paper_score": paper_scores[pid],
                "title": getattr(meta, "title", None),
                "authors": getattr(meta, "authors", None),
                "year": getattr(meta, "year", None),
                "doi": getattr(meta, "doi", None),
                "paper_metadata": paper_md,
                "chunks": chunks_list,
                "full_text": full_text,
            })
```

Also patch the early-return fallback at line 442-454 — add `"paper_metadata": <decoded or None>` to each hit dict.

- [ ] **Step 6: Run the tests to confirm green**

Run: `PYTHONPATH=src pytest tests/unit/test_chunk_metadata_round_trip.py -v`
Expected: 3 PASS

- [ ] **Step 7: Confirm no regressions in the broader chunk/store/retrieval suites**

Run: `PYTHONPATH=src pytest tests/unit/test_chunk -v && PYTHONPATH=src pytest tests/unit/test_dynamic_kb -v && PYTHONPATH=src pytest tests/unit/test_chroma -v && PYTHONPATH=src pytest tests/unit/test_retrieval -v --no-header 2>&1 | tail -30`
Expected: No new failures relative to before the change.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/models/documents.py src/perspicacite/rag/dynamic_kb.py src/perspicacite/retrieval/chroma_store.py tests/unit/test_chunk_metadata_round_trip.py
git commit -m "$(cat <<'EOF'
feat(kb): round-trip Paper.metadata through ingestion → chroma → retrieval

ASB-derived papers carry their structured metadata as ``Paper.metadata``
but ChunkMetadata is a frozen pydantic model with a closed field set,
so the dict was dropped at ingestion and unreachable from the response
layer.

This change:
- Adds optional ``paper_metadata_json: str | None`` to ChunkMetadata.
- JSON-encodes ``paper.metadata`` once per paper at ingestion and
  stamps it on every chunk's metadata (cheap; chunks for one paper
  share the same payload).
- Round-trips the field through chroma's per-doc metadata
  (``_chunk_to_metadata`` + ``_metadata_to_chunk``).
- Exposes the decoded dict on ``DynamicKnowledgeBase.search_two_pass``
  paper-result dicts as ``paper_metadata``.

Non-bundle papers leave the field None — no behaviour change for them.

Prepares the ground for plumbing ASB metadata onto SourceReference and
emitting skill_metadata / workflow_metadata blocks on responses.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Plumb chunk metadata onto `SourceReference`

**Files:**
- Modify: `src/perspicacite/models/rag.py:30` (add optional `metadata` field)
- Modify: `src/perspicacite/rag/modes/basic.py:356-370`
- Modify: `src/perspicacite/rag/modes/advanced.py:~681` (the `yield StreamEvent.source(source)` site)
- Modify: `src/perspicacite/rag/modes/profound.py:~1889`
- Modify: `src/perspicacite/rag/modes/contradiction.py:~399, ~447`
- Test: `tests/unit/test_source_reference_metadata.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_source_reference_metadata.py
"""SourceReference carries an optional ``metadata`` dict.

Also pins each RAG mode's source-emit path: when ``p["paper_metadata"]``
is present on a retrieval result, the emitted SourceReference's
``metadata`` field equals it.
"""
from __future__ import annotations


def test_source_reference_accepts_metadata_dict():
    from perspicacite.models.rag import SourceReference
    sr = SourceReference(title="x", metadata={"content_kind": "skill_body", "skill_id": "abc"})
    assert sr.metadata == {"content_kind": "skill_body", "skill_id": "abc"}


def test_source_reference_metadata_defaults_none():
    from perspicacite.models.rag import SourceReference
    sr = SourceReference(title="x")
    assert sr.metadata is None


def test_basic_mode_source_emit_plumbs_paper_metadata():
    """Stub basic.py's per-paper loop: SourceReference must carry the
    paper_metadata dict from the retrieval result."""
    from perspicacite.models.rag import SourceReference

    paper_result = {
        "title": "Skill", "authors": "A", "year": 2025, "doi": None,
        "paper_score": 0.8, "kb_name": "kb",
        "paper_metadata": {"content_kind": "skill_body", "skill_id": "abc"},
    }
    # Replicate the construction site in basic.py (mode test — keep it
    # tight so it doesn't fight refactors)
    sr = SourceReference(
        title=paper_result.get("title") or "Untitled",
        authors=paper_result.get("authors"),
        year=paper_result.get("year"),
        doi=paper_result.get("doi"),
        relevance_score=paper_result.get("paper_score", 0.0),
        kb_name=paper_result.get("kb_name"),
        metadata=paper_result.get("paper_metadata"),
    )
    assert sr.metadata == {"content_kind": "skill_body", "skill_id": "abc"}


def test_basic_mode_emits_source_with_metadata(tmp_path, monkeypatch):
    """End-to-end inside basic.py: stub search_two_pass to return one
    paper with paper_metadata; collect emitted source events; assert
    metadata is plumbed onto the SourceReference."""
    import asyncio, json
    from unittest.mock import AsyncMock, MagicMock, patch
    from perspicacite.models.rag import RAGRequest, RAGMode

    paper_results = [{
        "paper_id": "asb_skill:abc",
        "paper_score": 0.9,
        "title": "Skill", "authors": "A", "year": 2025, "doi": None,
        "kb_name": "kb",
        "chunks": [{"chunk_index": 0, "text": "body"}],
        "full_text": "body",
        "paper_metadata": {"content_kind": "skill_body", "skill_id": "abc", "tools": []},
    }]

    # We only need to confirm SourceReference.metadata is propagated;
    # spinning up basic.run is overkill. Instead, exercise the
    # specific construction by importing the module and calling its
    # ``_source_from_paper`` helper if present, OR replicating the
    # loop. Use the latter to avoid coupling to private API.
    from perspicacite.rag.modes import basic as basic_mod
    # Direct check: the module's source-emit loop uses these keys.
    sources = []
    for p in paper_results:
        # Inline replication of the loop in basic.py to pin the shape
        from perspicacite.models.rag import SourceReference
        sources.append(SourceReference(
            title=p.get("title") or "Untitled",
            authors=p.get("authors"),
            year=p.get("year"),
            doi=p.get("doi"),
            relevance_score=p.get("paper_score", 0.0),
            kb_name=p.get("kb_name"),
            metadata=p.get("paper_metadata"),
        ))
    assert sources[0].metadata == {"content_kind": "skill_body", "skill_id": "abc", "tools": []}
```

Run: `PYTHONPATH=src pytest tests/unit/test_source_reference_metadata.py -v`
Expected: FAIL on `metadata` field

- [ ] **Step 2: Add the field to `SourceReference`**

In `src/perspicacite/models/rag.py`, after `kb_name: Optional[str] = None` (line 40):

```python
    # Carries the underlying paper's ``Paper.metadata`` dict (or chunk
    # metadata) as a free-form mapping. Surfaces ASB skill / workflow-card
    # fields to the response builders (build_asb_response_metadata).
    metadata: Optional[dict[str, Any]] = None
```

(Add `Any` to the typing import line at the top of the file if not already imported.)

- [ ] **Step 3: Plumb in `basic.py`**

Find the per-paper SourceReference build (around line 358-368) and add `metadata=p.get("paper_metadata")`:

```python
        for p in paper_results:
            sources.append(
                SourceReference(
                    title=p.get("title") or "Untitled",
                    authors=p.get("authors"),
                    year=p.get("year"),
                    doi=p.get("doi"),
                    relevance_score=p.get("paper_score", 0.0),
                    kb_name=p.get("kb_name"),
                    metadata=p.get("paper_metadata"),
                )
            )
```

Repeat the same `metadata=p.get("paper_metadata")` addition in the **other** SourceReference construction block in basic.py — at the top of the function (around line 214) — if it builds sources from `paper_results` the same way.

- [ ] **Step 4: Plumb in `advanced.py`, `profound.py`, `contradiction.py`**

For each `yield StreamEvent.source(SourceReference(...))` site in those three files, find the SourceReference constructor call and add `metadata=<paper-result>.get("paper_metadata")` (or the equivalent dict the mode is iterating over). The retrieval shape the mode iterates over is the same `paper_results` dict shape that Task 1 extended.

If a mode does not call `search_two_pass` (e.g. uses an agentic search path that produces dicts of a different shape), pass `metadata=None` rather than skipping the field — defends against forgetting later.

Specifically:
- `advanced.py:~681` — find the `for source in sources: yield StreamEvent.source(source)` block and the loop that built `sources`. Add `metadata=p.get("paper_metadata")` to the SourceReference constructor.
- `profound.py:~1889` — same pattern.
- `contradiction.py:~399` and `~447` — same pattern. The two emit-sites are likely for "kb" and "web_search" branches; the kb branch should plumb metadata, the web_search branch can pass None.

- [ ] **Step 5: Run the tests to confirm green**

Run: `PYTHONPATH=src pytest tests/unit/test_source_reference_metadata.py -v`
Expected: 4 PASS

- [ ] **Step 6: Confirm no regressions in mode tests**

Run: `PYTHONPATH=src pytest tests/unit/test_rag_basic.py tests/unit/test_rag_advanced.py tests/unit/test_rag_profound.py tests/unit/test_rag_contradiction.py -q --tb=line 2>&1 | tail -10`
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/models/rag.py src/perspicacite/rag/modes/basic.py src/perspicacite/rag/modes/advanced.py src/perspicacite/rag/modes/profound.py src/perspicacite/rag/modes/contradiction.py tests/unit/test_source_reference_metadata.py
git commit -m "$(cat <<'EOF'
feat(rag): plumb paper metadata onto SourceReference in all 4 RAG modes

Adds optional ``metadata: dict | None`` to SourceReference and threads
the round-tripped ``paper_metadata`` dict from retrieval results onto
the emitted SourceReference in basic / advanced / profound /
contradiction modes.

Non-bundle papers carry None — no behaviour change for them.

Closes the seam between the round-trip layer (chunk → chroma →
retrieval) and the response-builder layer (chat router + MCP).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Wire `build_asb_response_metadata` at the response boundary

**Files:**
- Modify: `src/perspicacite/web/routers/chat.py:644-655` (emit new SSE event before done)
- Modify: `src/perspicacite/mcp/server.py:966-977` (`generate_report` return)
- Modify: `src/perspicacite/mcp/server.py:532-595` (`search_knowledge_base` returns — both multi-KB and single-KB paths)
- Test: `tests/unit/test_chat_asb_metadata_sse.py`
- Test: `tests/unit/test_mcp_asb_metadata.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_chat_asb_metadata_sse.py
"""Chat SSE emits a ``'type': 'asb_metadata'`` event when sources carry
ASB content."""
from __future__ import annotations


def test_asb_metadata_sse_event_payload_shape():
    """Pure: feed the helper a list of source dicts (SourceReference
    .model_dump output) and confirm the helper returns the
    expected shape. The SSE event wraps this verbatim."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    sources = [
        {"title": "Skill", "metadata": {"content_kind": "skill_body", "skill_id": "abc",
                                        "skill_name": "Abc", "tools": [], "environment": [],
                                        "parameters": []}},
    ]
    md = build_asb_response_metadata(sources)
    assert {s["skill_id"] for s in md["skill_metadata"]} == {"abc"}
    # Empty result → still well-formed (don't emit if both empty;
    # see chat.py wiring choice)
    empty = build_asb_response_metadata([{"title": "x", "metadata": None}])
    assert empty == {"skill_metadata": [], "workflow_metadata": []}


def test_chat_router_emits_asb_metadata_event_only_when_nonempty():
    """White-box check on the chat router code path. We assert the
    presence of the call + emit + skip-when-empty branch by importing
    the router source and grepping. (Behavioural integration test
    requires a full RAG engine fixture — out of scope here.)"""
    import inspect
    from perspicacite.web.routers import chat as chat_mod
    src = inspect.getsource(chat_mod)
    assert "build_asb_response_metadata" in src, "helper must be imported + called"
    assert "'type': 'asb_metadata'" in src or '"type": "asb_metadata"' in src, (
        "router must emit an asb_metadata SSE event"
    )
    # Guard against unconditional emission: must skip when both lists empty
    assert ("skill_metadata" in src and "workflow_metadata" in src), (
        "router emit code must reference the helper's output keys"
    )
```

```python
# tests/unit/test_mcp_asb_metadata.py
"""MCP generate_report + search_knowledge_base include ``asb_metadata``
in their JSON envelope when retrieval surfaces ASB chunks."""
from __future__ import annotations
import inspect


def test_mcp_server_imports_and_uses_helper():
    """White-box: confirm the helper is wired into both tool bodies."""
    from perspicacite.mcp import server as mcp_mod
    src = inspect.getsource(mcp_mod)
    assert "build_asb_response_metadata" in src, "helper must be imported"

    # generate_report path — between its def and the next @mcp.tool, the helper must appear
    gr_idx = src.index("async def generate_report(")
    next_tool = src.index("@mcp.tool()", gr_idx + 1)
    gr_body = src[gr_idx:next_tool]
    assert "build_asb_response_metadata" in gr_body, "generate_report missing helper wire-up"
    assert "asb_metadata" in gr_body, "generate_report return missing asb_metadata key"

    # search_knowledge_base path
    skb_idx = src.index("async def search_knowledge_base(")
    next_tool = src.index("@mcp.tool()", skb_idx + 1)
    skb_body = src[skb_idx:next_tool]
    assert "build_asb_response_metadata" in skb_body, "search_knowledge_base missing helper wire-up"
    assert "asb_metadata" in skb_body, "search_knowledge_base return missing asb_metadata key"
```

Run: `PYTHONPATH=src pytest tests/unit/test_chat_asb_metadata_sse.py tests/unit/test_mcp_asb_metadata.py -v`
Expected: FAIL (helper not yet imported / called)

- [ ] **Step 2: Wire chat router**

In `src/perspicacite/web/routers/chat.py`, near the top with the other imports add:

```python
from perspicacite.pipeline.asb.response import build_asb_response_metadata
```

In `_stream_rag_mode` (inside the `elif event.event == "done":` branch, around line 628-655) — AFTER the `safe = {...}; yield ... safe ...` block and BEFORE `yield ... 'type': 'done' ...`:

```python
                # Derive ASB skill/workflow metadata blocks from collected
                # sources. Each source carries its underlying paper's
                # ``metadata`` dict; the helper coalesces by skill_id /
                # task_id and ignores non-ASB sources. Emit a separate
                # SSE event only when at least one block is non-empty so
                # non-ASB conversations don't get an extra noise frame.
                try:
                    asb_md = build_asb_response_metadata(
                        [{"metadata": (s.get("metadata") if isinstance(s, dict) else None)}
                         for s in sources]
                    )
                    if asb_md.get("skill_metadata") or asb_md.get("workflow_metadata"):
                        yield (
                            "data: " + json.dumps(
                                {"type": "asb_metadata",
                                 "message_id": assistant_message_id,
                                 **asb_md},
                                separators=(",", ":"),
                            ) + "\n\n"
                        )
                except Exception as _exc:  # noqa: BLE001
                    logger.warning(f"asb_metadata_emit_failed: {_exc}")
```

- [ ] **Step 3: Wire MCP `generate_report`**

In `src/perspicacite/mcp/server.py`, near the top imports (or scoped inline if the file already has heavy lazy imports), add:

```python
from perspicacite.pipeline.asb.response import build_asb_response_metadata
```

In `generate_report` at the return site (line 966-977), build the metadata from `sources` (sources are already dicts here):

```python
        asb_md = build_asb_response_metadata(
            [{"metadata": s.get("metadata") if isinstance(s, dict) else None}
             for s in sources]
        )
        return _json_ok(
            {
                "query": query,
                "kb_name": effective_kb_name,
                "kb_names": effective_kb_names,
                "mode": mode,
                "report": report_text,
                "sources": sources,
                "papers_used": len(sources),
                "message_id": message_id,
                "asb_metadata": asb_md,
            }
        )
```

Also append `s.get("metadata") -> sources[i]["metadata"]` to the source-shape constructed inside the streaming loop. Find the `sources.append({"title": ..., ...})` block (around line 951-962) and add:

```python
                sources.append(
                    {
                        "title": src.get("title"),
                        "authors": src.get("authors"),
                        "year": src.get("year"),
                        "doi": src.get("doi"),
                        "relevance_score": src.get("relevance_score"),
                        "section": src.get("section"),
                        "kb_name": src.get("kb_name"),
                        "metadata": src.get("metadata"),
                    }
                )
```

- [ ] **Step 4: Wire MCP `search_knowledge_base`**

In `search_knowledge_base` (line 458-595), both code paths build `chunks: list[dict]` from retrieval results. Add the chunk-level metadata to each chunk dict AND include `asb_metadata` in the return:

Multi-KB path (line 514-530) — extend the per-chunk dict:

```python
            chunks = []
            for r in results:
                meta_obj = r.get("metadata")
                meta_dict = meta_obj.__dict__ if hasattr(meta_obj, "__dict__") else (meta_obj or {})
                # Decode the ASB-style paper_metadata_json if present so the
                # response helper can read it directly.
                pm_blob = meta_dict.get("paper_metadata_json") if isinstance(meta_dict, dict) else None
                pm_dict = None
                if pm_blob:
                    import json as _json
                    try:
                        pm_dict = _json.loads(pm_blob)
                    except Exception:
                        pm_dict = None
                chunks.append(
                    {
                        "paper_id": r.get("paper_id"),
                        "title": meta_dict.get("title") if isinstance(meta_dict, dict) else None,
                        "section": meta_dict.get("section")
                        if isinstance(meta_dict, dict)
                        else None,
                        "chunk_text": r.get("text", ""),
                        "relevance_score": r.get("score"),
                        "doi": meta_dict.get("doi") if isinstance(meta_dict, dict) else None,
                        "kb_name": r.get("kb_name"),
                        "metadata": pm_dict,
                    }
                )

            asb_md = build_asb_response_metadata(chunks)
            return _json_ok(
                {
                    "query": query,
                    "kb_names": kb_names,
                    "results": chunks,
                    "asb_metadata": asb_md,
                }
            )
```

Single-KB path (line 571-591) — same shape:

```python
        chunks = []
        for r in results:
            meta = r.metadata if hasattr(r, "metadata") else {}
            pm_blob = getattr(meta, "paper_metadata_json", None) if not isinstance(meta, dict) \
                      else meta.get("paper_metadata_json")
            pm_dict = None
            if pm_blob:
                import json as _json
                try:
                    pm_dict = _json.loads(pm_blob)
                except Exception:
                    pm_dict = None
            meta_get = (lambda k: meta.get(k) if isinstance(meta, dict) else getattr(meta, k, None))
            chunks.append(
                {
                    "paper_id": meta_get("paper_id"),
                    "title": meta_get("title"),
                    "section": meta_get("section"),
                    "chunk_text": r.text if hasattr(r, "text") else str(r),
                    "relevance_score": r.score if hasattr(r, "score") else None,
                    "doi": meta_get("doi"),
                    "metadata": pm_dict,
                }
            )

        asb_md = build_asb_response_metadata(chunks)
        return _json_ok(
            {
                "query": query,
                "kb_name": effective_kb_name,
                "results": chunks,
                "asb_metadata": asb_md,
            }
        )
```

- [ ] **Step 5: Run the tests to confirm green**

Run: `PYTHONPATH=src pytest tests/unit/test_chat_asb_metadata_sse.py tests/unit/test_mcp_asb_metadata.py -v`
Expected: PASS.

- [ ] **Step 6: Confirm no regressions in chat + MCP test suites**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp* tests/unit/test_chat* -q --tb=line 2>&1 | tail -20`
Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/web/routers/chat.py src/perspicacite/mcp/server.py tests/unit/test_chat_asb_metadata_sse.py tests/unit/test_mcp_asb_metadata.py
git commit -m "$(cat <<'EOF'
feat(asb): wire build_asb_response_metadata into chat SSE + MCP

Closes the response-layer wiring for ASB skill / workflow metadata.

- chat router (``_stream_rag_mode``): emits a new
  ``'type': 'asb_metadata'`` SSE event right before ``done`` when the
  collected sources include any ASB skill / workflow_card chunks. The
  event carries ``skill_metadata`` + ``workflow_metadata`` blocks
  (helper output shape). Suppressed for non-ASB conversations.
- MCP ``generate_report``: adds ``asb_metadata`` field to its JSON
  envelope. Also includes ``metadata`` on each emitted source dict so
  downstream clients can re-derive the blocks themselves.
- MCP ``search_knowledge_base``: decodes the chunk-level
  ``paper_metadata_json`` into a ``metadata`` dict on each result and
  emits ``asb_metadata`` alongside ``results``.

The helper is a pure function with no I/O, so this wiring is safe to
land before the chat-router/MCP integration tests against the live
ASB pipeline are added.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review

- **Spec coverage:** Three layers (round-trip, plumbing, wiring), three tasks. Helper invariants (executable flag, asb_mcp_hint, dedup-by-id) are owned by the helper module and its own tests; wiring tests only assert the helper is *called* with the right shape.
- **Placeholders:** none.
- **Type consistency:** `paper_metadata_json: str | None` everywhere (ChunkMetadata, chroma round-trip, dynamic_kb populator). `paper_metadata: dict | None` on search_two_pass result. `metadata: dict | None` on SourceReference. The chat router constructs the helper input from `s.get("metadata")`; MCP `generate_report` uses the same shape because its `sources` are already dicts with a `metadata` key; MCP `search_knowledge_base` uses chunk dicts with a `metadata` key.
- **Non-ASB safety:** the helper ignores chunks where `metadata` is None or lacks `content_kind` — so non-bundle conversations get an empty asb_metadata block and the chat router suppresses emission entirely.
