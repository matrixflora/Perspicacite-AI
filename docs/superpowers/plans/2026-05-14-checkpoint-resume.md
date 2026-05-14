# Checkpoint/resume — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Crash-resilient ingests. Re-running picks up where it
failed.

**Spec:** `docs/superpowers/specs/2026-05-14-checkpoint-resume-design.md`

---

## Task 1: CheckpointStore module + atomic save

**Files:**
- Create: `src/perspicacite/pipeline/checkpoint.py`
- Test: `tests/unit/test_checkpoint_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_checkpoint_store.py
"""Tests for CheckpointStore + atomic save (Wave 3.3)."""
import json
from pathlib import Path

import pytest

from perspicacite.pipeline.checkpoint import CheckpointStore


def _store(tmp_path: Path) -> CheckpointStore:
    return CheckpointStore(
        path=tmp_path / "ck.json",
        kb_name="kb1",
        operation="ingest_dois",
    )


def test_load_returns_none_when_file_missing(tmp_path):
    s = _store(tmp_path)
    assert s.load() is None


def test_save_then_load_roundtrip(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a", "b", "c"])
    state.record("a", "added")
    s.save(state)

    s2 = _store(tmp_path)
    loaded = s2.load()
    assert loaded is not None
    assert loaded.processed == {"a": "added"}
    assert loaded.planned_ids == ["a", "b", "c"]


def test_record_adds_to_processed(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a", "b"])
    state.record("a", "added")
    state.record("b", "failed", reason="timeout")
    assert state.processed["a"] == "added"
    assert "failed" in state.processed["b"]
    assert "timeout" in state.processed["b"]


def test_remaining_ids_excludes_processed(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a", "b", "c", "d"])
    state.record("a", "added")
    state.record("c", "failed", reason="x")
    assert list(state.remaining_ids()) == ["b", "d"]


def test_retry_failed_re_includes_failed_ids(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a", "b", "c"])
    state.record("a", "added")
    state.record("b", "failed", reason="x")
    assert list(state.remaining_ids(retry_failed=True)) == ["b", "c"]


def test_is_complete(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a", "b"])
    state.record("a", "added")
    assert state.is_complete() is False
    state.record("b", "added")
    assert state.is_complete() is True


def test_atomic_save_no_tmp_left_behind(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a"])
    s.save(state)
    # tmp suffix file should not exist after save.
    assert not (tmp_path / "ck.json.tmp").exists()
    assert (tmp_path / "ck.json").exists()


def test_delete_removes_file(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a"])
    s.save(state)
    assert (tmp_path / "ck.json").exists()
    s.delete()
    assert not (tmp_path / "ck.json").exists()
    # Delete on absent file is a no-op.
    s.delete()


def test_record_failed_reason_truncated_to_200_chars(tmp_path):
    s = _store(tmp_path)
    state = s.load_or_create(planned_ids=["a"])
    long_reason = "x" * 500
    state.record("a", "failed", reason=long_reason)
    assert len(state.processed["a"]) <= 220   # "failed: " + 200 chars + slack
    assert "failed:" in state.processed["a"]
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_checkpoint_store.py -v
```

- [ ] **Step 3: Implement**

Create `src/perspicacite/pipeline/checkpoint.py`:

```python
"""Crash-resilient checkpoint store for multi-item ingests (Wave 3.3).

A single JSON file per (KB, operation) tracks which planned IDs have
been processed and how. Saved atomically (tmp + rename) so a SIGKILL
mid-write never leaves a half-written file.

See docs/superpowers/specs/2026-05-14-checkpoint-resume-design.md.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


_REASON_MAX = 200


@dataclass
class CheckpointState:
    kb_name: str
    operation: str
    planned_ids: list[str]
    processed: dict[str, str] = field(default_factory=dict)
    started_at: int = field(default_factory=lambda: int(time.time()))
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def record(self, item_id: str, outcome: str, reason: str | None = None) -> None:
        if outcome == "failed":
            r = (reason or "").strip()[:_REASON_MAX]
            self.processed[item_id] = f"failed: {r}" if r else "failed"
        else:
            self.processed[item_id] = outcome
        self.updated_at = int(time.time())

    def remaining_ids(self, *, retry_failed: bool = False) -> Iterator[str]:
        for pid in self.planned_ids:
            outcome = self.processed.get(pid)
            if outcome is None:
                yield pid
            elif retry_failed and outcome.startswith("failed"):
                yield pid

    def is_complete(self) -> bool:
        return all(pid in self.processed for pid in self.planned_ids)

    def to_dict(self) -> dict:
        return {
            "kb_name": self.kb_name,
            "operation": self.operation,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "total_planned": len(self.planned_ids),
            "planned_ids": list(self.planned_ids),
            "processed": dict(self.processed),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CheckpointState":
        return cls(
            kb_name=d.get("kb_name", ""),
            operation=d.get("operation", ""),
            planned_ids=list(d.get("planned_ids", [])),
            processed=dict(d.get("processed", {})),
            started_at=int(d.get("started_at", time.time())),
            updated_at=int(d.get("updated_at", time.time())),
        )


class CheckpointStore:
    """File-backed accessor for a CheckpointState."""

    def __init__(self, *, path: Path | str, kb_name: str, operation: str):
        self.path = Path(path)
        self.kb_name = kb_name
        self.operation = operation
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> CheckpointState | None:
        if not self.path.exists():
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Corrupt / unreadable file → treat as missing. Caller can
            # decide whether to overwrite with load_or_create.
            return None
        return CheckpointState.from_dict(data)

    def load_or_create(self, planned_ids: list[str]) -> CheckpointState:
        existing = self.load()
        if existing is not None:
            return existing
        return CheckpointState(
            kb_name=self.kb_name,
            operation=self.operation,
            planned_ids=list(planned_ids),
        )

    def save(self, state: CheckpointState) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def delete(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_checkpoint_store.py -v
```

Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/checkpoint.py tests/unit/test_checkpoint_store.py
git commit -m "feat(checkpoint): file-backed CheckpointStore with atomic save (Wave 3.3)"
```

---

## Task 2: Wire into ingest_dois_into_kb

**Files:**
- Modify: `src/perspicacite/pipeline/search_to_kb.py` (the `ingest_dois_into_kb` function)
- Modify: `src/perspicacite/config/schema.py` (add `KnowledgeBaseConfig.checkpoint_dir`)
- Test: `tests/unit/test_ingest_dois_resume.py` (new)

- [ ] **Step 1: Add the config field**

In `src/perspicacite/config/schema.py`, in `KnowledgeBaseConfig`,
after the embedding-cache fields (Wave 2.2), add:

```python
    checkpoint_dir: Path = Field(
        default=Path("data/checkpoints"),
        description=(
            "Directory for ingest checkpoint files (Wave 3.3). "
            "Each multi-paper ingest writes <kb>__<op>.json here "
            "and removes it on clean completion."
        ),
    )
```

- [ ] **Step 2: Write the failing integration test**

```python
# tests/unit/test_ingest_dois_resume.py
"""Verify ingest_dois_into_kb wires CheckpointStore + resumes on re-run (Wave 3.3)."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.checkpoint import CheckpointStore
from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb


def _app_state(tmp_path: Path) -> SimpleNamespace:
    """Minimal app_state stand-in. Avoids pulling the full DI graph."""
    config = SimpleNamespace(
        pdf_download=None,
        kb=SimpleNamespace(checkpoint_dir=tmp_path / "ck"),
    )
    session_store = MagicMock()
    session_store.get_kb_metadata = AsyncMock(return_value=SimpleNamespace(
        paper_count=0, chunk_count=0,
    ))
    session_store.save_kb_metadata = AsyncMock()
    vector_store = MagicMock()
    vector_store.paper_exists = AsyncMock(return_value=False)
    return SimpleNamespace(
        config=config,
        session_store=session_store,
        vector_store=vector_store,
        embedding_provider=MagicMock(),
        pdf_parser=MagicMock(),
    )


@pytest.mark.asyncio
async def test_resume_skips_already_processed(tmp_path):
    """If a checkpoint already shows 2 of 3 DOIs added, the next call
    only processes the remaining 1."""
    # Seed a checkpoint with 2 of 3 already added.
    ck_dir = tmp_path / "ck"
    ck_dir.mkdir()
    store = CheckpointStore(
        path=ck_dir / "kb1__ingest_dois.json",
        kb_name="kb1",
        operation="ingest_dois",
    )
    state = store.load_or_create(planned_ids=["10.1/a", "10.2/b", "10.3/c"])
    state.record("10.1/a", "added")
    state.record("10.2/b", "added")
    store.save(state)

    # Mock everything that goes over the wire.
    fetched_dois: list[str] = []

    async def fake_retrieve(doi, **kw):
        fetched_dois.append(doi)
        return SimpleNamespace(
            success=True, full_text="x", abstract=None, metadata={},
        )

    state = _app_state(tmp_path)
    with patch(
        "perspicacite.pipeline.search_to_kb.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.search_to_kb.build_authenticated_client",
    ) as mock_client_ctx, patch(
        "perspicacite.pipeline.search_to_kb.DynamicKnowledgeBase",
    ) as mock_dkb:
        # async context manager that yields a mocked http client.
        mock_client_ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_client_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_dkb.return_value.add_papers = AsyncMock(return_value=1)

        result = await ingest_dois_into_kb(
            state, "kb1",
            ["10.1/a", "10.2/b", "10.3/c"],
        )

    # Only "10.3/c" should have been fetched — the other two were
    # already in the checkpoint.
    assert fetched_dois == ["10.3/c"]
    # The checkpoint should have been deleted on clean completion.
    assert not (ck_dir / "kb1__ingest_dois.json").exists()
```

- [ ] **Step 3: Run, watch fail**

```bash
pytest tests/unit/test_ingest_dois_resume.py -v
```

Expected: failure because the function doesn't check the checkpoint
yet.

- [ ] **Step 4: Wire the checkpoint into `ingest_dois_into_kb`**

In `src/perspicacite/pipeline/search_to_kb.py`, modify
`ingest_dois_into_kb` to accept new kwargs and wire the checkpoint:

```python
async def ingest_dois_into_kb(
    app_state: Any,
    kb_name: str,
    dois: list[str],
    *,
    resume: bool = True,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Add each DOI's full-text paper to ``kb_name``.

    ... (existing docstring)

    Wave 3.3: this function is crash-resilient via
    :class:`CheckpointStore`. On re-run with the same ``kb_name`` and
    DOIs, already-processed entries are skipped. Pass ``resume=False``
    to ignore the checkpoint and start fresh; pass ``retry_failed=True``
    to retry entries that previously failed.
    """
    from perspicacite.pipeline.checkpoint import CheckpointStore
    # ... existing imports ...

    # ---- checkpoint setup (Wave 3.3) -----------------------------------
    ck_dir = Path(getattr(app_state.config.kb, "checkpoint_dir",
                          "data/checkpoints"))
    ckpt = CheckpointStore(
        path=ck_dir / f"{kb_name}__ingest_dois.json",
        kb_name=kb_name,
        operation="ingest_dois",
    )
    if not resume:
        ckpt.delete()
    state = ckpt.load_or_create(planned_ids=list(dois))
    dois_to_process = list(state.remaining_ids(retry_failed=retry_failed))

    # ... existing kb_meta + pdf_kwargs setup ...

    # Replace `for raw_doi in dois:` with:
    async with build_authenticated_client(cookies_path=cookies_path) as client:
        for raw_doi in dois_to_process:
            doi = (raw_doi or "").strip().replace("https://doi.org/", "")
            if not doi:
                continue
            if await app_state.vector_store.paper_exists(collection_name, doi):
                skipped.append({"doi": doi})
                state.record(doi, "skipped")
                ckpt.save(state)
                continue
            dl["attempted"] += 1
            try:
                result = await retrieve_paper_content(...)
            except Exception as e:
                failed.append({"doi": doi, "reason": str(e)})
                dl["failed"] += 1
                state.record(doi, "failed", reason=str(e))
                ckpt.save(state)
                continue
            if not result or not result.success:
                failed.append({"doi": doi, "reason": "no content"})
                dl["failed"] += 1
                state.record(doi, "failed", reason="no content")
                ckpt.save(state)
                continue
            # ... existing paper construction ...
            papers_to_add.append(paper)
            state.record(doi, "added")
            ckpt.save(state)

    # ... existing add_papers + metadata update ...

    # Clean up checkpoint on clean completion.
    if state.is_complete():
        ckpt.delete()

    return { ... }
```

You'll need to also add `from pathlib import Path` to the imports if
not already present, and read the existing function carefully to put
the new lines in the right places. The plan above sketches the
shape — implement following the existing patterns in the file.

- [ ] **Step 5: Run, watch pass**

```bash
pytest tests/unit/test_ingest_dois_resume.py -v
pytest tests/unit/test_checkpoint_store.py -v   # no regression
```

Also run the broader suite to catch regressions:

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/pipeline/search_to_kb.py \
        src/perspicacite/config/schema.py \
        tests/unit/test_ingest_dois_resume.py
git commit -m "feat(ingest): checkpoint+resume for ingest_dois_into_kb (Wave 3.3)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/checkpoint-resume-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
# Checkpoint & resume — operator guide (2026-05-14)

Wave 3.3 of the framework-hardening roadmap. Crash-resilient
multi-paper ingests.

## What it does

`ingest_dois_into_kb` writes a JSON file per (KB, operation) to
`data/checkpoints/<kb>__<op>.json`. After each DOI is processed
(added / skipped / failed), the file is atomically updated. If the
process crashes — or you Ctrl-C — the next run with the same DOIs
picks up where the previous left off.

## Behaviour

```bash
# First run, network glitch at DOI 47 of 100:
perspicacite ingest-dois mykb dois.txt
# → 46 added, then RuntimeError. Checkpoint shows 46 done.

# Second run — same command:
perspicacite ingest-dois mykb dois.txt
# → 54 added (DOIs 47-100). Checkpoint deleted on clean completion.
```

## Knobs

| Kwarg | Default | Effect |
|---|---|---|
| `resume` | `True` | Honour existing checkpoint. Pass `False` to start fresh. |
| `retry_failed` | `False` | Re-attempt DOIs that previously failed. |

```python
await ingest_dois_into_kb(
    app_state, "mykb", dois,
    resume=False,            # force restart
)
await ingest_dois_into_kb(
    app_state, "mykb", dois,
    retry_failed=True,       # retry the 3 PDFs that timed out
)
```

## File format

```json
{
  "kb_name": "mykb",
  "operation": "ingest_dois",
  "started_at": 1731575689,
  "updated_at": 1731575900,
  "total_planned": 100,
  "planned_ids": ["10.1/a", "10.2/b", ...],
  "processed": {
    "10.1/a": "added",
    "10.2/b": "skipped",
    "10.3/c": "failed: timeout reading PDF"
  }
}
```

Atomically written via tmp-file + `os.replace`. SIGKILL mid-write
leaves the file in its previous valid state — never half-written.

## Manual cleanup

```bash
# Inspect:
ls data/checkpoints/
cat data/checkpoints/mykb__ingest_dois.json | jq .processed

# Wipe checkpoint for a KB:
rm data/checkpoints/mykb__ingest_dois.json
```

## Config

```yaml
kb:
  checkpoint_dir: data/checkpoints     # default
```

## Scope today

Wired into:

- `ingest_dois_into_kb` ✅

Followups (separate sub-projects):

- `search_filter_and_ingest`
- `snowball.expand_kb_via_citations`
- `bibtex_kb.build_kb_from_bibtex`
- `external/fetch_orchestrator.run`

Each is a small mechanical change once the pattern is proven.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/checkpoint.py` | `CheckpointStore`, `CheckpointState`, atomic save |
| `src/perspicacite/pipeline/search_to_kb.py` | wiring in `ingest_dois_into_kb` |
| `src/perspicacite/config/schema.py` | `kb.checkpoint_dir` field |
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/checkpoint-resume-*.md` to `.gitignore` after
`!docs/fallback-chain-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/checkpoint-resume-2026-05-14.md .gitignore
git commit -m "docs(checkpoint): operator guide (Wave 3.3)"
```

---

## Done

After Task 3:

- New `CheckpointStore` module (~120 LoC) with atomic save.
- `ingest_dois_into_kb` is crash-resilient.
- 10 new tests (9 unit + 1 integration), all passing.
- Operator doc landed.
- Other ingest entry points are documented followups.
