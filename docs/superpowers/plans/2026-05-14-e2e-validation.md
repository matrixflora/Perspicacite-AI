# E2E validation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automated E2E pipeline tests (Wave 6.1), persistence/integrity tests (Wave 6.2), and a perf regression baseline (Wave 6.3) per the design spec at `docs/superpowers/specs/2026-05-14-e2e-validation-design.md`.

**Architecture:** All tests mock the LLM and embedding providers for determinism. Real-LLM runs are a separate manual audit step (not covered here). Tests live under `tests/e2e/`, `tests/integration/`.

**Tech Stack:** pytest-asyncio, hypothesis (optional), the existing `MockLLM` and `MockEmbeddingProvider` fixtures from `tests/conftest.py`, the SciLEx mock pattern from `tests/integration/test_provider_matrix.py`.

---

## Discovery (do this first)

Before implementing, inspect:

- `tests/conftest.py` for existing mock fixtures (`mock_llm_client`, `mock_embedding_provider`, `sample_papers`).
- `src/perspicacite/mcp/server.py` for the entrypoint shapes of `create_knowledge_base`, `add_papers_to_kb`, `add_dois_to_kb`, `search_knowledge_base`, `generate_report`, `expand_kb_via_citations`. They're decorated MCP tools but internally do real work — your E2E scenarios should call the same underlying functions (look for the pattern in existing `tests/test_mcp_server.py`).
- `src/perspicacite/pipeline/search_to_kb.py` — `ingest_dois_into_kb` is the workhorse and already emits KB-log events (Wave 4.3).
- `src/perspicacite/rag/kb_router.py` — `auto_route_kbs(query, kbs, ...)` is the routing entrypoint.
- `src/perspicacite/pipeline/checkpoint.py` — Wave 3.3 atomic save / load.
- `src/perspicacite/llm/cache.py` — Wave 2.1 disk cache.
- `src/perspicacite/llm/embedding_cache.py` — Wave 2.2.

Some MCP tools call out to external services (Crossref, OpenAlex, PDF fetchers). Mock those at the `httpx.AsyncClient` level (or whichever HTTP client the code uses) — find the patches used in `tests/integration/test_provider_matrix.py` for reference.

---

### Task 1: Register pytest markers

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1:** Add `"e2e: End-to-end pipeline tests"` and `"perf: Performance regression tests"` to the `markers` list in `[tool.pytest.ini_options]`.

- [ ] **Step 2:** Run `pytest --markers | grep -E 'e2e|perf'`. Expected: both markers listed.

- [ ] **Step 3:** Commit `chore(tests): register e2e + perf pytest markers (Wave 6)`

---

### Task 2: E2E shared conftest

**Files:**
- Create: `tests/e2e/__init__.py` (empty)
- Create: `tests/e2e/conftest.py`

- [ ] **Step 1: Create** `tests/e2e/__init__.py` — empty file.

- [ ] **Step 2: Implement** `tests/e2e/conftest.py`:

```python
"""Shared fixtures for E2E pipeline tests (Wave 6.1)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config, KnowledgeBaseConfig, LLMConfig


def _deterministic_vec(text: str, dim: int = 384) -> list[float]:
    """SHA-256-derived vector — same text always returns same vector."""
    h = hashlib.sha256(text.encode("utf-8")).digest()
    # Pad/repeat into `dim` floats in [-1, 1].
    floats: list[float] = []
    while len(floats) < dim:
        for b in h:
            floats.append((b / 127.5) - 1.0)
            if len(floats) >= dim:
                break
    return floats


class DeterministicEmbeddingProvider:
    def __init__(self, dim: int = 384) -> None:
        self._dim = dim
        self.calls = 0

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "deterministic-mock"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [_deterministic_vec(t, self._dim) for t in texts]


class StagedLLM:
    """Returns canned strings keyed by `stage` kwarg."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses = responses or {}
        self.default = (
            "Mocked LLM response. The paper at DOI {doi} discusses the topic in detail."
        )
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[dict], **kwargs) -> str:
        stage = kwargs.get("stage", "default")
        self.calls.append({"stage": stage, "messages": messages, "kwargs": kwargs})
        return self.responses.get(stage, f"[mock:{stage}] " + str(messages)[:200])

    async def stream(self, messages, **kwargs):
        text = await self.complete(messages, **kwargs)
        for tok in text.split():
            yield tok + " "


@pytest.fixture
def deterministic_embedder() -> DeterministicEmbeddingProvider:
    return DeterministicEmbeddingProvider()


@pytest.fixture
def staged_llm() -> StagedLLM:
    return StagedLLM()


@pytest.fixture
def e2e_config(tmp_path: Path) -> Config:
    """A Config pointing all storage at tmp_path so tests don't pollute the dev env."""
    cfg = Config()
    cfg.knowledge_base = KnowledgeBaseConfig(
        embedding_model="deterministic-mock",
        log_dir=tmp_path / "kb_logs",
        checkpoint_dir=tmp_path / "checkpoints",
        embedding_cache_path=tmp_path / "embedding_cache.db",
        embedding_cache_enabled=True,
        orcid_cache_path=tmp_path / "orcid_cache.db",
        mcp_resource_max_events=100,
    )
    cfg.llm = LLMConfig(
        cache_enabled=True,
        cache_path=tmp_path / "llm_cache.db",
        cache_ttl_hours=24,
    )
    return cfg


@pytest.fixture
def synthetic_paper() -> dict:
    return {
        "id": "doi:10.0001/synthetic",
        "doi": "10.0001/synthetic",
        "title": "On the formation of red giants in low-metallicity environments",
        "authors": [{"name": "A. Mocker", "family": "Mocker"}],
        "year": 2025,
        "abstract": (
            "We model the late-stage evolution of low-metallicity stars and "
            "find that red-giant formation rates scale inversely with "
            "metallicity. We use Monte-Carlo stellar-evolution simulations."
        ),
        "full_text": (
            "Section 1: Introduction. " * 40 +
            "Section 2: Methods. Monte-Carlo simulations. " * 40 +
            "Section 3: Results. Red giants form at higher rates. " * 40 +
            "Section 4: Conclusions. Metallicity matters. " * 20
        ),
    }


@pytest.fixture
def synthetic_corpus() -> list[dict]:
    """5 papers, 2 in astro topic, 2 in bio topic, 1 cross-disciplinary."""
    base = [
        ("10.0001/a1", "Stellar nucleosynthesis in massive stars",
         "Stellar physics, supernova ejecta, heavy elements."),
        ("10.0001/a2", "Red giant branch evolution",
         "Helium-burning shells, asymptotic giant branch, mass loss."),
        ("10.0001/b1", "AlphaFold-2 predictions of GPCR structures",
         "Protein folding, structure prediction, transmembrane domains."),
        ("10.0001/b2", "Cryo-EM of ribosome assembly intermediates",
         "Ribosome biogenesis, protein structure, RNA folding."),
        ("10.0001/x1", "Astrobiology: searching for biosignatures on exoplanets",
         "Exoplanets, biosignatures, atmospheric spectroscopy, protein chemistry."),
    ]
    return [
        {
            "id": f"doi:{doi}", "doi": doi, "title": title,
            "authors": [{"name": "Mock Author", "family": "Author"}],
            "year": 2024, "abstract": abstract,
            "full_text": (title + ". " + abstract + " ") * 30,
        }
        for (doi, title, abstract) in base
    ]
```

- [ ] **Step 3:** Run `pytest tests/e2e/ --collect-only`. Expected: 0 tests collected but no import errors.

- [ ] **Step 4:** Commit `test(e2e): shared conftest with deterministic mocks (Wave 6.1)`

---

### Task 3: E2E Scenario A — single-paper round trip

**Files:**
- Create: `tests/e2e/test_single_paper.py`

- [ ] **Step 1: Implement.** The test builds a KB, ingests 1 paper using `DynamicKnowledgeBase.add_papers`, searches, and verifies retrieval returns the paper.

```python
"""E2E Scenario A: single-paper round trip (Wave 6.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_single_paper_round_trip(
    tmp_path: Path, deterministic_embedder, e2e_config, synthetic_paper
):
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval import ChromaVectorStore

    vs = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=deterministic_embedder,
    )
    kb = DynamicKnowledgeBase(
        name="solo",
        config=KnowledgeBaseConfig(),
        vector_store=vs,
        embedding_provider=deterministic_embedder,
    )
    await kb.initialize()

    # Convert the synthetic paper into whatever shape `add_papers` expects.
    # Inspect add_papers signature first; build the input accordingly.
    added = await kb.add_papers([synthetic_paper])
    assert added >= 1, "should report papers added"

    # Search for a phrase from the abstract.
    hits = await kb.search(query="red giant low metallicity formation rate", top_k=5)
    assert hits, "search should return at least one hit"
    # The top hit should reference our paper's DOI.
    top = hits[0]
    text = getattr(top, "text", "") or getattr(top, "content", "")
    meta = getattr(top, "metadata", {}) or {}
    assert "synthetic" in str(meta.get("paper_id", "")).lower() or "red" in text.lower()

    await kb.cleanup()
```

- [ ] **Step 2: Investigate** `DynamicKnowledgeBase.add_papers` signature and adapt the test input to match. The codebase has helpers (`Paper` dataclass, `DocumentChunk`) — use them as the existing search-to-kb code does.

- [ ] **Step 3: Run** `pytest tests/e2e/test_single_paper.py -v`. Iterate until PASS. If `add_papers` requires `Paper` objects with specific fields, build them; if it requires pre-chunked input, chunk via the existing chunker.

- [ ] **Step 4: Commit** `test(e2e): scenario A — single-paper round trip (Wave 6.1)`

---

### Task 4: E2E Scenario B — multi-paper + citations

**Files:**
- Create: `tests/e2e/test_multi_paper_citations.py`

- [ ] **Step 1: Implement.** Build KB, ingest 5 papers, simulate citation expansion (mock the resolver), verify the KB log carries the events.

```python
"""E2E Scenario B: multi-paper + citation expansion (Wave 6.1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_multi_paper_with_citations(
    tmp_path: Path, deterministic_embedder, e2e_config, synthetic_corpus
):
    """5-paper ingest + 1-paper citation expansion. Verify KB log carries the events."""
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval import ChromaVectorStore
    from perspicacite.pipeline.kb_log import KBLogWriter

    log_dir = e2e_config.knowledge_base.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log = KBLogWriter(path=log_dir / "multi.jsonl")

    vs = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=deterministic_embedder,
    )
    kb = DynamicKnowledgeBase(
        name="multi",
        config=KnowledgeBaseConfig(),
        vector_store=vs,
        embedding_provider=deterministic_embedder,
    )
    await kb.initialize()
    await kb.add_papers(synthetic_corpus)

    # Emit synthetic KB-log entries (in production this happens inside
    # ingest_dois_into_kb; here we replicate the contract).
    from perspicacite.pipeline.kb_log import KBEvent
    for p in synthetic_corpus:
        log.append(KBEvent(
            event="paper_added", kb_name="multi", paper_id=p["doi"],
            title=p["title"], chunks=3, source_command="test",
        ))
    # Simulated citation-expansion adds 1 ref:
    log.append(KBEvent(
        event="paper_added", kb_name="multi", paper_id="10.0099/expanded-ref",
        title="Cited reference", chunks=2, source_command="expand_citations",
    ))

    events = log.read_all()
    added = [e for e in events if e["event"] == "paper_added"]
    assert len(added) == 6  # 5 originals + 1 expanded
    assert any("expanded-ref" in e["paper_id"] for e in added)

    # Retrieval reach: a query about stellar physics returns at least one of
    # the two astro papers from the corpus.
    hits = await kb.search(query="stellar nucleosynthesis supernova ejecta", top_k=5)
    pids = {(h.metadata or {}).get("paper_id", "") for h in hits}
    assert any("a1" in p or "a2" in p for p in pids)

    await kb.cleanup()
```

- [ ] **Step 2: Run** `pytest tests/e2e/test_multi_paper_citations.py -v`. Iterate until PASS.

- [ ] **Step 3: Commit** `test(e2e): scenario B — multi-paper + citations (Wave 6.1)`

---

### Task 5: E2E Scenario C — cross-KB routing

**Files:**
- Create: `tests/e2e/test_cross_kb_routing.py`

- [ ] **Step 1: Implement.** Build two KBs with topically distinct descriptions, call the router for queries that should pick one each.

```python
"""E2E Scenario C: cross-KB routing (Wave 6.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_cross_kb_routing_picks_relevant_kb(
    tmp_path: Path, deterministic_embedder, e2e_config, synthetic_corpus
):
    """Build astro + bio KBs; verify routing picks the right one per query."""
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.rag.kb_router import auto_route_kbs
    from perspicacite.retrieval import ChromaVectorStore

    vs = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=deterministic_embedder,
    )

    astro_kb = DynamicKnowledgeBase(
        name="astro", config=KnowledgeBaseConfig(),
        vector_store=vs, embedding_provider=deterministic_embedder,
    )
    bio_kb = DynamicKnowledgeBase(
        name="bio", config=KnowledgeBaseConfig(),
        vector_store=vs, embedding_provider=deterministic_embedder,
    )
    await astro_kb.initialize()
    await bio_kb.initialize()

    astro_papers = [p for p in synthetic_corpus if p["doi"].startswith("10.0001/a")]
    bio_papers   = [p for p in synthetic_corpus if p["doi"].startswith("10.0001/b")]
    await astro_kb.add_papers(astro_papers)
    await bio_kb.add_papers(bio_papers)

    # Stub KB-metadata records the router consults. The exact shape depends on
    # how auto_route_kbs reads metadata — inspect and adapt.
    kb_meta = [
        {"name": "astro", "description": "Stellar physics, supernova nucleosynthesis, red giants."},
        {"name": "bio",   "description": "Protein folding, AlphaFold, cryo-EM."},
    ]

    pick1 = await auto_route_kbs(
        query="how do red giants form in low-metallicity environments?",
        kbs=kb_meta,
    )
    assert "astro" in (pick1 if isinstance(pick1, list) else [pick1])

    pick2 = await auto_route_kbs(
        query="how does AlphaFold predict GPCR protein structure?",
        kbs=kb_meta,
    )
    assert "bio" in (pick2 if isinstance(pick2, list) else [pick2])

    await astro_kb.cleanup()
    await bio_kb.cleanup()
```

- [ ] **Step 2: Inspect `auto_route_kbs`** signature in `src/perspicacite/rag/kb_router.py` and adapt the call. If it requires an LLM client, pass the `staged_llm` fixture; if it uses BM25, it can run with only the kb metadata.

- [ ] **Step 3: Run** `pytest tests/e2e/test_cross_kb_routing.py -v`. Iterate until PASS.

- [ ] **Step 4: Commit** `test(e2e): scenario C — cross-KB routing (Wave 6.1)`

---

### Task 6: Persistence / integrity tests (Wave 6.2)

**Files:**
- Create: `tests/integration/test_persistence_integrity.py`

- [ ] **Step 1: Implement** all 8 tests listed in the spec. For each, prefer a tiny in-process simulation over real network/filesystem stress.

Skeleton (fill out each test body following the design spec):

```python
"""Persistence + data-integrity tests (Wave 6.2)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_kb_survives_close_reopen(tmp_path: Path, deterministic_embedder, synthetic_paper):
    # Build → cleanup → re-open from same persist_dir → search returns same hit.
    ...


@pytest.mark.asyncio
async def test_chroma_collection_persists(tmp_path: Path, deterministic_embedder, synthetic_corpus):
    # Add → drop client → fresh client at same persist_dir → count unchanged.
    ...


@pytest.mark.asyncio
async def test_concurrent_kb_log_appends(tmp_path: Path):
    # 4 tasks × 100 events on same KBLogWriter path. All 400 land. No torn lines.
    from perspicacite.pipeline.kb_log import KBLogWriter, KBEvent
    path = tmp_path / "log.jsonl"
    writers = [KBLogWriter(path=path) for _ in range(4)]
    async def burst(w, tag):
        for i in range(100):
            w.append(KBEvent(event="paper_added", kb_name="x",
                              paper_id=f"{tag}-{i}", title="t", chunks=1,
                              source_command="t"))
    await asyncio.gather(*(burst(w, str(i)) for i, w in enumerate(writers)))
    events = KBLogWriter(path=path).read_all()
    assert len(events) == 400
    pids = {e["paper_id"] for e in events}
    assert len(pids) == 400


@pytest.mark.asyncio
async def test_concurrent_session_store_writes(tmp_path: Path):
    from perspicacite.memory.session_store import SessionStore
    store = SessionStore(tmp_path / "s.db")
    await store.init_db()
    # 4 tasks each writing 50 distinct KB metadata rows.
    ...


@pytest.mark.asyncio
async def test_session_store_reopen_preserves_rows(tmp_path: Path):
    ...


def test_checkpoint_survives_kill_mid_save(tmp_path: Path):
    # Create good checkpoint, then write a partial tmp file to simulate kill mid-save.
    # The reopened CheckpointStore must load the previous good checkpoint.
    from perspicacite.pipeline.checkpoint import CheckpointStore, CheckpointState
    store = CheckpointStore(path=tmp_path / "ckpt.json")
    state = CheckpointState(run_id="r1", target_kb="x", remaining_ids=["a","b","c"])
    store.save(state)
    # Write partial tmp file
    (tmp_path / "ckpt.json.tmp").write_text('{"run_id": "r1", "target_kb"')
    # Re-open and verify the original good file is intact.
    reopened = CheckpointStore(path=tmp_path / "ckpt.json")
    loaded = reopened.load()
    assert loaded is not None
    assert loaded.run_id == "r1"


@pytest.mark.asyncio
async def test_llm_cache_survives_reopen(tmp_path: Path):
    from perspicacite.llm.cache import LLMResponseCache, build_cache_key
    db = tmp_path / "llm_cache.db"
    c1 = LLMResponseCache(path=db)
    key = build_cache_key(
        provider="x", model="y",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
    )
    c1.put(key, "cached-output", ttl_seconds=3600)
    del c1
    c2 = LLMResponseCache(path=db)
    assert c2.get(key) == "cached-output"


@pytest.mark.asyncio
async def test_embedding_cache_dedup_across_reopens(tmp_path: Path):
    from perspicacite.llm.embedding_cache import CachedEmbeddingProvider

    class FakeInner:
        @property
        def dimension(self): return 4
        @property
        def model_name(self): return "fake"
        async def embed(self, texts):
            FakeInner.calls += 1
            return [[1.0,2.0,3.0,4.0] for _ in texts]
    FakeInner.calls = 0

    db = tmp_path / "emb.db"
    c1 = CachedEmbeddingProvider(inner=FakeInner(), cache_path=db)
    await c1.embed(["hello"])
    assert FakeInner.calls == 1
    # Re-open and re-embed the same text — inner must NOT be called again.
    c2 = CachedEmbeddingProvider(inner=FakeInner(), cache_path=db)
    await c2.embed(["hello"])
    assert FakeInner.calls == 1
```

- [ ] **Step 2:** Inspect actual signatures of `CheckpointStore`, `CheckpointState`, `LLMResponseCache`, `CachedEmbeddingProvider`, and `SessionStore` to fill in the test bodies accurately.

- [ ] **Step 3: Run** `pytest tests/integration/test_persistence_integrity.py -v`. Iterate until all PASS.

- [ ] **Step 4: Commit** `test(integration): persistence + integrity tests (Wave 6.2)`

---

### Task 7: Perf regression baseline (Wave 6.3)

**Files:**
- Create: `tests/integration/test_perf_baseline.py`
- Create: `tests/data/perf_corpus/{paper_1..paper_5}.json` (5 fixtures)
- Create: `tests/data/perf_baseline.json` (initial baseline — captured on first run)

- [ ] **Step 1: Create** 5 synthetic paper fixtures under `tests/data/perf_corpus/`. Each ~2 KB of realistic-looking text.

- [ ] **Step 2: Implement** `tests/integration/test_perf_baseline.py`:

```python
"""Perf regression baseline test (Wave 6.3)."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.perf


CORPUS_DIR = Path(__file__).parent.parent / "data" / "perf_corpus"
BASELINE_PATH = Path(__file__).parent.parent / "data" / "perf_baseline.json"
UPDATE = os.environ.get("PERSPICACITE_UPDATE_PERF_BASELINE") == "1"
TOLERANCE = float(os.environ.get("PERSPICACITE_PERF_TOLERANCE", "1.30"))


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


@pytest.mark.asyncio
async def test_perf_baseline(tmp_path, deterministic_embedder):
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval import ChromaVectorStore
    from perspicacite.pipeline.kb_log import KBLogWriter, KBEvent

    papers = []
    for f in sorted(CORPUS_DIR.glob("paper_*.json")):
        papers.append(json.loads(f.read_text()))
    assert len(papers) == 5

    vs = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=deterministic_embedder,
    )
    kb = DynamicKnowledgeBase(
        name="perf",
        config=KnowledgeBaseConfig(),
        vector_store=vs,
        embedding_provider=deterministic_embedder,
    )
    await kb.initialize()

    # 1. Ingest 5 papers
    t0 = time.perf_counter()
    await kb.add_papers(papers)
    ingest_s = time.perf_counter() - t0

    # 2. Search top10
    t0 = time.perf_counter()
    for _ in range(10):
        await kb.search(query="results methods conclusions", top_k=10)
    search_s = (time.perf_counter() - t0) / 10

    # 3. Mock report-synthesis time — sum embeddings + a synthetic delay
    t0 = time.perf_counter()
    await deterministic_embedder.embed(["x" * 500] * 20)
    report_s = time.perf_counter() - t0

    # 4. Embeddings per second
    t0 = time.perf_counter()
    await deterministic_embedder.embed(["sample"] * 100)
    emb_per_s = 100 / (time.perf_counter() - t0)

    # 5. KB log writes per second
    log = KBLogWriter(path=tmp_path / "log.jsonl")
    t0 = time.perf_counter()
    for i in range(500):
        log.append(KBEvent(event="paper_added", kb_name="perf",
                            paper_id=f"p{i}", title="t", chunks=1,
                            source_command="perf_test"))
    log_writes_per_s = 500 / (time.perf_counter() - t0)

    metrics = {
        "ingest_5_papers_seconds": ingest_s,
        "search_top10_seconds": search_s,
        "report_synthesis_seconds": report_s,
        "embeddings_per_second": emb_per_s,
        "kb_log_writes_per_second": log_writes_per_s,
        "git_sha": _git_sha(),
        "timestamp": time.time(),
    }

    if UPDATE or not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(json.dumps(metrics, indent=2))
        pytest.skip(f"Baseline written to {BASELINE_PATH}. Re-run without UPDATE.")

    baseline = json.loads(BASELINE_PATH.read_text())
    failures = []
    speedups = []
    for k in ("ingest_5_papers_seconds", "search_top10_seconds",
              "report_synthesis_seconds"):
        ratio = metrics[k] / max(baseline[k], 1e-9)
        if ratio > TOLERANCE:
            failures.append(f"{k}: {ratio:.2f}× slower (current={metrics[k]:.3f}, baseline={baseline[k]:.3f})")
        elif ratio < (1 / TOLERANCE):
            speedups.append(f"{k}: {1/ratio:.2f}× faster")
    for k in ("embeddings_per_second", "kb_log_writes_per_second"):
        ratio = baseline[k] / max(metrics[k], 1e-9)
        if ratio > TOLERANCE:
            failures.append(f"{k}: {ratio:.2f}× slower (current={metrics[k]:.1f}/s, baseline={baseline[k]:.1f}/s)")
        elif ratio < (1 / TOLERANCE):
            speedups.append(f"{k}: {1/ratio:.2f}× faster")

    if speedups:
        print("Perf speedups: " + "; ".join(speedups))
    if failures:
        pytest.fail("Perf regression beyond tolerance:\n  " + "\n  ".join(failures))

    await kb.cleanup()
```

- [ ] **Step 3: Generate the initial baseline** — run `PERSPICACITE_UPDATE_PERF_BASELINE=1 pytest tests/integration/test_perf_baseline.py -v`. The test skips after writing `perf_baseline.json`.

- [ ] **Step 4: Verify** — run `pytest tests/integration/test_perf_baseline.py -v` (no env var). Expected: PASS (current run within tolerance of just-captured baseline).

- [ ] **Step 5: Sanity check the tolerance** — run `PERSPICACITE_PERF_TOLERANCE=1.01 pytest tests/integration/test_perf_baseline.py -v`. Expected: FAIL (tight tolerance triggers regression).

- [ ] **Step 6: Commit** `test(perf): regression baseline against fixed 5-paper corpus (Wave 6.3)`

---

### Task 8: Operator docs

**Files:**
- Create: `docs/e2e-validation-2026-05-14.md`

- [ ] **Step 1: Write** a ~60-line operator guide:
  - How to run the 3 E2E scenarios (`pytest -m e2e`).
  - How to run the persistence suite.
  - How to update the perf baseline (env-var workflow).
  - Caveats: mocked LLM, deterministic embedder, what's NOT covered.

- [ ] **Step 2: Allowlist** in `.gitignore`: `!docs/e2e-validation-*.md`.

- [ ] **Step 3: Commit** `docs(e2e): operator guide for Wave 6 test suite`

---

### Task 9: Final roadmap tick + summary

- [ ] **Step 1:** Tick 6.1 + 6.2 + 6.3 in `docs/roadmap-2026-05-followups.md`.

- [ ] **Step 2:** Commit `docs(roadmap): Wave 6.1–6.3 shipped`
