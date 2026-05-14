# Versioned KBs (append log) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Per-KB append-only JSONL log of every paper-add /
skip / fail event. Enables provenance audit and (eventually) rollback.

**Spec:** `docs/superpowers/specs/2026-05-14-versioned-kbs-design.md`

---

## Task 1: KBLogWriter module

**Files:**
- Create: `src/perspicacite/pipeline/kb_log.py`
- Test: `tests/unit/test_kb_log.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_kb_log.py
"""Tests for KBLogWriter + KBEvent (Wave 4.3)."""
import json
import threading
from pathlib import Path

import pytest

from perspicacite.pipeline.kb_log import KBEvent, KBLogWriter


def _writer(tmp_path: Path) -> KBLogWriter:
    return KBLogWriter(path=tmp_path / "kb.jsonl")


def _event(kind: str = "paper_added", paper_id: str = "10.1/a", **kw) -> KBEvent:
    return KBEvent(
        event=kind, kb_name="kb1", paper_id=paper_id, **kw,
    )


def test_append_writes_one_line_per_event(tmp_path):
    w = _writer(tmp_path)
    w.append(_event("paper_added"))
    w.append(_event("paper_skipped", paper_id="10.2/b"))
    content = (tmp_path / "kb.jsonl").read_text()
    lines = content.strip().split("\n")
    assert len(lines) == 2
    # Each line must be valid JSON.
    json.loads(lines[0])
    json.loads(lines[1])


def test_read_all_returns_events_in_order(tmp_path):
    w = _writer(tmp_path)
    w.append(_event(paper_id="a"))
    w.append(_event(paper_id="b"))
    w.append(_event(paper_id="c"))
    events = w.read_all()
    assert [e.paper_id for e in events] == ["a", "b", "c"]


def test_read_all_on_missing_file_returns_empty(tmp_path):
    w = _writer(tmp_path)
    assert w.read_all() == []


def test_partial_line_at_eof_silently_skipped(tmp_path):
    """A SIGKILL mid-write may leave half a line — reader must
    tolerate it on the LAST line only."""
    p = tmp_path / "kb.jsonl"
    p.write_text(
        json.dumps({"ts": 1, "event": "paper_added", "kb_name": "kb1",
                    "paper_id": "10.1/a"}) + "\n"
        + '{"partial":'  # broken trailing fragment
    )
    w = KBLogWriter(path=p)
    events = w.read_all()
    assert len(events) == 1
    assert events[0].paper_id == "10.1/a"


def test_malformed_middle_line_logged_and_skipped(tmp_path):
    p = tmp_path / "kb.jsonl"
    p.write_text(
        json.dumps({"ts": 1, "event": "paper_added", "kb_name": "kb1",
                    "paper_id": "10.1/a"}) + "\n"
        + "not-json-junk\n"
        + json.dumps({"ts": 2, "event": "paper_added", "kb_name": "kb1",
                      "paper_id": "10.2/b"}) + "\n"
    )
    w = KBLogWriter(path=p)
    events = w.read_all()
    assert len(events) == 2
    assert events[0].paper_id == "10.1/a"
    assert events[1].paper_id == "10.2/b"


def test_read_after_filters_by_ts(tmp_path):
    w = _writer(tmp_path)
    w.append(_event(paper_id="a", ts=100))
    w.append(_event(paper_id="b", ts=200))
    w.append(_event(paper_id="c", ts=300))
    recent = w.read_after(ts=150)
    assert [e.paper_id for e in recent] == ["b", "c"]


def test_concurrent_appends_dont_interleave(tmp_path):
    """20 threads × 50 appends each = 1000 events; each line must
    still be valid JSON after the smoke."""
    w = _writer(tmp_path)

    def hammer():
        for i in range(50):
            w.append(_event(paper_id=f"p-{threading.get_ident()}-{i}"))

    threads = [threading.Thread(target=hammer) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "kb.jsonl").read_text().strip().split("\n")
    assert len(lines) == 20 * 50
    for ln in lines:
        json.loads(ln)  # raises if any line is corrupt


def test_rollback_after_returns_paper_ids(tmp_path):
    w = _writer(tmp_path)
    w.append(_event(paper_id="a", ts=100))
    w.append(_event(paper_id="b", ts=200))
    w.append(_event(paper_id="c", ts=300))
    w.append(_event("paper_skipped", paper_id="d", ts=250))

    rolled = w.rollback_after(ts=150)
    # Only paper_added events count for rollback, not skipped.
    assert set(rolled) == {"b", "c"}

    # A kb_pruned event should have been recorded after the rollback.
    events = w.read_all()
    pruned = [e for e in events if e.event == "kb_pruned"]
    assert len(pruned) == 1


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    """A disk-full / permission error must NOT propagate — provenance
    is best-effort. Caller's ingest loop keeps going."""
    w = _writer(tmp_path)

    def boom(*a, **kw):
        raise PermissionError("read-only fs")

    monkeypatch.setattr("builtins.open", boom)
    # Should not raise.
    w.append(_event())
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_kb_log.py -v
```

- [ ] **Step 3: Implement**

Create `src/perspicacite/pipeline/kb_log.py`:

```python
"""Per-KB append-only JSONL event log (Wave 4.3).

One file per KB at ``data/kb_logs/<kb_name>.jsonl``. Each line is one
:class:`KBEvent` rendered as JSON. Append is atomic for sub-PIPE_BUF
lines via POSIX append-mode write — concurrent writers don't
interleave.

See docs/superpowers/specs/2026-05-14-versioned-kbs-design.md.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.kb_log")


EventKind = Literal[
    "kb_created", "paper_added", "paper_skipped",
    "paper_failed", "kb_pruned",
]


@dataclass
class KBEvent:
    """One event in a KB's history."""

    event: EventKind
    kb_name: str
    paper_id: str = ""
    title: str | None = None
    chunks: int = 0
    source_command: str | None = None
    reason: str | None = None
    ts: int = field(default_factory=lambda: int(time.time()))
    operator_label: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class KBLogWriter:
    """Append-only JSONL log for one KB."""

    def __init__(self, *, path: Path | str):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Don't crash the caller — provenance is best-effort.
            logger.warning(
                "kb_log_mkdir_failed", path=str(self.path), error=str(exc),
            )

    # ---- write side --------------------------------------------------

    def append(self, event: KBEvent) -> None:
        """Atomically append one event line. Never raises — write
        failures log + drop."""
        try:
            line = json.dumps(asdict(event), ensure_ascii=False, sort_keys=True)
            # ``a`` mode is atomic for sub-PIPE_BUF (~4 KB) writes on POSIX.
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass  # fsync is best-effort
        except Exception as exc:  # noqa: BLE001 — best-effort logging
            logger.warning(
                "kb_log_append_failed",
                path=str(self.path),
                event=event.event,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ---- read side ---------------------------------------------------

    def read_all(self) -> list[KBEvent]:
        """Return all events in append order. Missing file → []."""
        if not self.path.exists():
            return []
        events: list[KBEvent] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError as exc:
            logger.warning("kb_log_read_failed", path=str(self.path), error=str(exc))
            return []

        for i, raw in enumerate(lines):
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Only the LAST line can be a SIGKILL-induced partial;
                # log middle-line corruption but keep reading.
                if i == len(lines) - 1:
                    logger.debug("kb_log_partial_last_line_skipped",
                                 path=str(self.path))
                else:
                    logger.warning("kb_log_malformed_line",
                                   path=str(self.path), line_no=i + 1)
                continue
            try:
                events.append(KBEvent(**data))
            except TypeError:
                # Schema drift — log and skip.
                logger.warning("kb_log_schema_drift",
                               path=str(self.path), line_no=i + 1)
        return events

    def read_after(self, *, ts: int) -> list[KBEvent]:
        """Events with ``ts > ts`` (strict)."""
        return [e for e in self.read_all() if e.ts > ts]

    # ---- rollback ----------------------------------------------------

    def rollback_after(self, *, ts: int) -> list[str]:
        """Return the paper IDs added after ``ts`` and record a
        ``kb_pruned`` event."""
        all_events = self.read_all()
        paper_ids = [
            e.paper_id for e in all_events
            if e.event == "paper_added" and e.ts > ts and e.paper_id
        ]
        kb_name = ""
        for e in all_events:
            if e.kb_name:
                kb_name = e.kb_name
                break
        self.append(KBEvent(
            event="kb_pruned",
            kb_name=kb_name,
            extra={
                "rolled_back_paper_ids": paper_ids,
                "ts_cutoff": ts,
            },
        ))
        return paper_ids
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_kb_log.py -v
```

Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/kb_log.py tests/unit/test_kb_log.py
git commit -m "feat(kb-log): append-only JSONL event log per KB (Wave 4.3)"
```

---

## Task 2: Config + wiring into ingest_dois_into_kb

**Files:**
- Modify: `src/perspicacite/config/schema.py` (add `kb.log_dir`)
- Modify: `src/perspicacite/pipeline/search_to_kb.py` (emit events)
- Test: `tests/unit/test_ingest_dois_kb_log.py` (new)

- [ ] **Step 1: Add the config field**

In `src/perspicacite/config/schema.py`, in `KnowledgeBaseConfig` after
the `checkpoint_dir` field (Wave 3.3), add:

```python
    log_dir: Path = Field(
        default=Path("data/kb_logs"),
        description=(
            "Directory for per-KB append-only event logs (Wave 4.3). "
            "Each KB writes <kb_name>.jsonl with paper_added / "
            "paper_skipped / paper_failed events for audit + rollback."
        ),
    )
```

- [ ] **Step 2: Write the failing integration test**

```python
# tests/unit/test_ingest_dois_kb_log.py
"""Verify ingest_dois_into_kb emits KBLog events (Wave 4.3)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb


def _app_state(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            pdf_download=None,
            kb=SimpleNamespace(
                checkpoint_dir=tmp_path / "ckpt",
                log_dir=tmp_path / "logs",
            ),
        ),
        session_store=MagicMock(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
        vector_store=MagicMock(paper_exists=AsyncMock(return_value=False)),
        embedding_provider=MagicMock(),
        pdf_parser=MagicMock(),
    )


@pytest.mark.asyncio
async def test_paper_added_event_recorded_on_success(tmp_path):
    state = _app_state(tmp_path)

    async def fake_retrieve(doi, **kw):
        return SimpleNamespace(
            success=True, full_text="x", abstract=None, metadata={"title": "T"},
        )

    with patch(
        "perspicacite.pipeline.download.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx, patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
    ) as mock_dkb:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_dkb.return_value.add_papers = AsyncMock(return_value=5)

        await ingest_dois_into_kb(state, "kb1", ["10.1/a"])

    log_path = tmp_path / "logs" / "kb1.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().split("\n")
    events = [json.loads(l) for l in lines]
    kinds = [e["event"] for e in events]
    assert "paper_added" in kinds
    added = next(e for e in events if e["event"] == "paper_added")
    assert added["paper_id"] == "10.1/a"
    assert added["source_command"] == "ingest_dois_into_kb"


@pytest.mark.asyncio
async def test_paper_skipped_event_for_duplicate(tmp_path):
    state = _app_state(tmp_path)
    # Pretend the paper already exists.
    state.vector_store.paper_exists = AsyncMock(return_value=True)

    with patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await ingest_dois_into_kb(state, "kb1", ["10.1/dup"])

    log_path = tmp_path / "logs" / "kb1.jsonl"
    events = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
    assert any(e["event"] == "paper_skipped" and e["paper_id"] == "10.1/dup" for e in events)


@pytest.mark.asyncio
async def test_paper_failed_event_with_reason(tmp_path):
    state = _app_state(tmp_path)

    async def fake_retrieve(doi, **kw):
        raise RuntimeError("network down")

    with patch(
        "perspicacite.pipeline.download.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await ingest_dois_into_kb(state, "kb1", ["10.1/x"])

    log_path = tmp_path / "logs" / "kb1.jsonl"
    events = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
    failed = [e for e in events if e["event"] == "paper_failed"]
    assert len(failed) == 1
    assert failed[0]["paper_id"] == "10.1/x"
    assert "network down" in (failed[0].get("reason") or "")
```

- [ ] **Step 3: Run, watch fail**

- [ ] **Step 4: Wire the logger into ingest_dois_into_kb**

In `src/perspicacite/pipeline/search_to_kb.py`, find
`ingest_dois_into_kb`. Just below the existing checkpoint setup
(Wave 3.3), add the KB log setup:

```python
    from perspicacite.pipeline.kb_log import KBEvent, KBLogWriter
    log_dir = _Path(getattr(app_state.config.kb, "log_dir", "data/kb_logs"))
    kb_log = KBLogWriter(path=log_dir / f"{kb_name}.jsonl")
```

(Re-use the existing `_Path` import added in Wave 3.3.)

Inside the per-DOI loop, augment the existing skip / fail / success
branches with `kb_log.append(...)` calls:

- After `skipped.append(...)` (duplicate paper):

  ```python
  kb_log.append(KBEvent(
      event="paper_skipped", kb_name=kb_name, paper_id=doi,
      source_command="ingest_dois_into_kb",
  ))
  ```

- After `failed.append(...)` in the exception handler:

  ```python
  kb_log.append(KBEvent(
      event="paper_failed", kb_name=kb_name, paper_id=doi,
      reason=str(e)[:500], source_command="ingest_dois_into_kb",
  ))
  ```

- After `failed.append(...)` in the no-content branch:

  ```python
  kb_log.append(KBEvent(
      event="paper_failed", kb_name=kb_name, paper_id=doi,
      reason="no content", source_command="ingest_dois_into_kb",
  ))
  ```

- After `papers_to_add.append(paper)` in the success branch:

  ```python
  kb_log.append(KBEvent(
      event="paper_added", kb_name=kb_name, paper_id=doi,
      title=md.get("title"),
      source_command="ingest_dois_into_kb",
  ))
  ```

  (The `chunks` field stays at 0 here because chunking happens after
  the loop. A `chunks` follow-up update is a deliberate followup —
  v1 records that the paper was added, not how many chunks.)

- [ ] **Step 5: Run, watch pass**

```bash
pytest tests/unit/test_ingest_dois_kb_log.py -v
pytest tests/unit/test_kb_log.py -v             # no regression
pytest tests/unit/test_ingest_dois_resume.py -v # checkpoint still works
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/config/schema.py \
        src/perspicacite/pipeline/search_to_kb.py \
        tests/unit/test_ingest_dois_kb_log.py
git commit -m "feat(ingest): emit KB log events from ingest_dois_into_kb (Wave 4.3)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/versioned-kbs-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
# Versioned KBs (append log) — operator guide (2026-05-14)

Wave 4.3 of the framework-hardening roadmap. Append-only JSONL event
log per knowledge base.

## What it gives you

A line-per-event audit trail at `data/kb_logs/<kb>.jsonl`:

```json
{"event":"paper_added","kb_name":"astro","paper_id":"10.1234/x","title":"...","ts":1731575689,"source_command":"ingest_dois_into_kb"}
{"event":"paper_skipped","kb_name":"astro","paper_id":"10.1234/y","ts":1731575700,"source_command":"ingest_dois_into_kb"}
{"event":"paper_failed","kb_name":"astro","paper_id":"10.1234/z","reason":"network down","ts":1731575710,"source_command":"ingest_dois_into_kb"}
```

## Event types

| event | When |
|---|---|
| `kb_created` | First write of a KB (followup — not emitted today). |
| `paper_added` | Paper successfully prepared for insertion. |
| `paper_skipped` | Duplicate de-dup'd. |
| `paper_failed` | Ingest failed for this paper. `reason` carries the error message. |
| `kb_pruned` | Rollback recorded — `extra.rolled_back_paper_ids` lists what's gone. |

## Inspecting

```bash
# Human read:
cat data/kb_logs/astro.jsonl | jq -c '{event, paper_id, ts}'

# When was paper X added?
grep '"paper_id":"10.1234/x"' data/kb_logs/astro.jsonl | jq .

# All failures:
jq -c 'select(.event=="paper_failed")' data/kb_logs/astro.jsonl
```

## Programmatic API

```python
from perspicacite.pipeline.kb_log import KBLogWriter
from pathlib import Path

w = KBLogWriter(path=Path("data/kb_logs/astro.jsonl"))
recent = w.read_after(ts=1731_000_000)   # events after Nov 2024
ids_to_drop = w.rollback_after(ts=1731_500_000)
# ids_to_drop is the list of paper_ids to remove from the KB.
# A `kb_pruned` event is appended automatically.
```

## What it's NOT (v1)

- **Not a full rollback orchestrator.** `rollback_after` returns the
  candidate paper IDs and records the event; actually dropping chunks
  from Chroma + updating KB metadata is the caller's job. A higher-
  level `rollback(kb, ts)` helper is a followup.
- **Not a transaction log.** Events are recorded best-effort —
  write failures are logged but never propagate (we don't want
  provenance to break ingest).
- **Not synchronous across processes.** Concurrent appends to the
  same file are safe (POSIX atomic ≤ 4 KB), but readers may see a
  partial last line during a kill-9. Readers tolerate that.

## Coverage today

Only `ingest_dois_into_kb` emits events. Other ingest paths are
documented followups:

- `add_papers_to_kb`, `add_dois_to_kb` (MCP tools, share code with
  `ingest_dois_into_kb` partially)
- `snowball.expand_kb_via_citations`
- `bibtex_kb.build_kb_from_bibtex`
- `external/fetch_orchestrator.run`

Each is a small mechanical change once we audit the entry points.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/kb_log.py` | `KBLogWriter`, `KBEvent`, append + read + rollback helper |
| `src/perspicacite/pipeline/search_to_kb.py` | Emit events from `ingest_dois_into_kb` |
| `src/perspicacite/config/schema.py` | `kb.log_dir` field |
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/versioned-kbs-*.md` to `.gitignore` after
`!docs/export-formats-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/versioned-kbs-2026-05-14.md .gitignore
git commit -m "docs(versioned-kbs): operator guide (Wave 4.3)"
```

---

## Done

After Task 3:

- `KBLogWriter` with atomic append, robust read, rollback helper.
- `ingest_dois_into_kb` emits `paper_added` / `paper_skipped` /
  `paper_failed`.
- New `kb.log_dir` config field.
- 12 new tests passing.
- Operator doc landed.
- Other ingest entry points are documented followups.
