# Audit Follow-ups Batch (P1 / P2 / P3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the seven remaining queue items from the 2026-05-15 audit (method-level chunking, decorator-aware kinds, arXiv-id fallback, `--openalex-id` flag, `--include-scripts` cite-graph, figure thumbnails, bm25s migration).

**Architecture:** Each task is a self-contained enhancement to an existing sub-system. We touch chunking (Sub-A), pipeline/cite-graph + DOI resolver, CLI + MCP wrappers, retrieval (BM25), and the web UI. No new sub-projects; each task lands working software on `main` per the standing per-task-commit workflow.

**Tech Stack:** Python 3.11, `ast`, `httpx`, Click, FastMCP, FastAPI/SSE, vanilla JS, Pydantic v2, `bm25s` (new dependency).

**Source of truth for what each task fixes:** `tests/audit/results/2026-05-15-audit-findings.md` (P1 #3, P1 #4, P2 #5, P2 #7, P3 #8, P3 #9, P3 #10).

---

## Task 1: Method-level sub-chunking for large classes (P1 #3)

**Why:** RAG audit found that class-level chunks for framework code (e.g. `RagModel`) routinely span 80–160 lines and sub-optimise retrieval. Method-level chunks improve embedding quality without losing the class-level symbol-index entry (we keep both passes).

**Files:**
- Modify: `src/perspicacite/models/documents.py` (add `parent_class` field to `ChunkMetadata`)
- Modify: `src/perspicacite/pipeline/chunking_code.py` (extend `_chunk_python_ast`)
- Test: `tests/unit/test_chunking_method_level.py` (new)

- [ ] **Step 1: Add `parent_class` field to ChunkMetadata**

In `src/perspicacite/models/documents.py`, locate `class ChunkMetadata` and add the new field alongside `symbol_name` / `symbol_kind`:

```python
parent_class: Optional[str] = Field(
    None,
    description="If symbol_kind is a method, the enclosing class name. None otherwise.",
)
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_chunking_method_level.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.pipeline.chunking_code import _chunk_python_ast
from perspicacite.models.documents import Paper

LARGE_CLASS = '''\
"""Module docstring."""
import os


class BigThing:
    """A class with many methods."""

    def __init__(self, x: int) -> None:
        """Init."""
        self.x = x
        # padding line 1
        # padding line 2
        # padding line 3
        self.y = x * 2

    def compute(self, factor: int) -> int:
        """Multiply x by a factor and y by 3.

        Long body to ensure class exceeds the 1500-char threshold so
        method-level sub-chunking kicks in.
        """
        a = self.x * factor
        b = self.y * 3
        c = a + b
        d = c - factor
        e = d ** 2
        return e

    def explain(self) -> str:
        """Return a description."""
        parts = [f"x={self.x}", f"y={self.y}"]
        return ", ".join(parts)
'''
# Pad to exceed 1500 chars (the size gate).
LARGE_CLASS = LARGE_CLASS + ("\n# padding comment line\n" * 40)


def _paper() -> Paper:
    return Paper(id="p1", title="t", authors=[], year=2024, source_type="github")


@pytest.mark.asyncio
async def test_large_class_emits_method_chunks_plus_class_chunk():
    chunks = await _chunk_python_ast(
        LARGE_CLASS, _paper(),
        file_path="src/bigthing.py",
        chunk_size=4000, chunk_overlap=200,
    )
    kinds = [c.metadata.symbol_kind for c in chunks]
    # One class-level chunk plus N method-level chunks.
    assert "class" in kinds
    assert kinds.count("method") >= 2
    method_chunks = [c for c in chunks if c.metadata.symbol_kind == "method"]
    for mc in method_chunks:
        assert mc.metadata.parent_class == "BigThing"
        assert mc.metadata.symbol_name in {"__init__", "compute", "explain"}


@pytest.mark.asyncio
async def test_small_class_only_emits_class_chunk():
    src = (
        "class Tiny:\n"
        '    """Short."""\n'
        "    def m(self):\n"
        "        return 1\n"
    )
    chunks = await _chunk_python_ast(
        src, _paper(),
        file_path="src/tiny.py",
        chunk_size=4000, chunk_overlap=200,
    )
    kinds = [c.metadata.symbol_kind for c in chunks]
    assert kinds == ["class"]
```

- [ ] **Step 3: Run test to verify it fails**

```
pytest tests/unit/test_chunking_method_level.py -v
```

Expected: FAIL on `test_large_class_emits_method_chunks_plus_class_chunk` — currently only one `"class"` chunk emitted, no methods.

- [ ] **Step 4: Implement method-level sub-chunking**

In `src/perspicacite/pipeline/chunking_code.py`, modify the top-level `ast.ClassDef` branch of `_chunk_python_ast`. Locate the existing block that builds the class chunk (around line 56–81 per the survey) and extend it so that when the class body source exceeds 1500 chars we *also* emit one chunk per top-level method (FunctionDef / AsyncFunctionDef inside the class body).

Add this constant at module top (near other constants):

```python
_METHOD_SUBCHUNK_THRESHOLD_CHARS = 1500
```

Replace the class-handling block so it looks like:

```python
elif isinstance(node, ast.ClassDef):
    # Always emit the class-level chunk (symbol-index browsing).
    class_text = ast.get_source_segment(text, node) or ""
    chunks.append(_make_code_chunk(
        paper=paper,
        file_path=file_path,
        text=class_text,
        symbol_name=node.name,
        symbol_kind="class",
        parent_class=None,
        start_line=node.lineno,
        end_line=node.end_lineno or node.lineno,
        docstring=ast.get_docstring(node),
        imports=imports,
        chunk_index=len(chunks),
    ))
    # If the class body is large, also emit method-level sub-chunks.
    if len(class_text) > _METHOD_SUBCHUNK_THRESHOLD_CHARS:
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_text = ast.get_source_segment(text, sub) or ""
                if not method_text.strip():
                    continue
                chunks.append(_make_code_chunk(
                    paper=paper,
                    file_path=file_path,
                    text=method_text,
                    symbol_name=sub.name,
                    symbol_kind="method",
                    parent_class=node.name,
                    start_line=sub.lineno,
                    end_line=sub.end_lineno or sub.lineno,
                    docstring=ast.get_docstring(sub),
                    imports=imports,
                    chunk_index=len(chunks),
                ))
```

If `_make_code_chunk` does not currently accept `parent_class`, add it: locate the helper in the same file and thread `parent_class: Optional[str] = None` through to the constructed `ChunkMetadata(...)` call.

- [ ] **Step 5: Run tests to verify pass**

```
pytest tests/unit/test_chunking_method_level.py -v
pytest tests/unit/test_chunking_code_ast.py -v
```

Expected: all PASS. Existing AST tests must remain green (we kept the class-level chunk).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/documents.py src/perspicacite/pipeline/chunking_code.py tests/unit/test_chunking_method_level.py
git commit -m "feat(chunking): method-level sub-chunks for large classes"
```

---

## Task 2: Decorator-aware Python AST chunking (P3 #8)

**Why:** Symbol-index browsing should distinguish `@classmethod` / `@staticmethod` / `@property` from regular methods. This is a 1-2 hour quality-of-life fix on top of Task 1.

**Files:**
- Modify: `src/perspicacite/pipeline/chunking_code.py` (decorator inspection inside class body)
- Test: `tests/unit/test_chunking_decorator_kinds.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chunking_decorator_kinds.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.pipeline.chunking_code import _chunk_python_ast
from perspicacite.models.documents import Paper


SRC = '''\
class C:
    """C class."""

    @classmethod
    def from_dict(cls, d):
        """Build from dict."""
        return cls()

    @staticmethod
    def helper(x):
        """Pure helper."""
        return x + 1

    @property
    def name(self):
        """Computed name."""
        return "c"

    def plain(self):
        """Regular method."""
        return 0
''' + ("\n# pad\n" * 80)  # ensure method-level sub-chunking triggers


def _paper() -> Paper:
    return Paper(id="p1", title="t", authors=[], year=2024, source_type="github")


@pytest.mark.asyncio
async def test_decorator_kinds_are_recorded():
    chunks = await _chunk_python_ast(
        SRC, _paper(), file_path="c.py", chunk_size=4000, chunk_overlap=200,
    )
    by_name = {c.metadata.symbol_name: c.metadata.symbol_kind for c in chunks
               if c.metadata.symbol_kind not in {"class"}}
    assert by_name == {
        "from_dict": "classmethod",
        "helper": "staticmethod",
        "name": "property",
        "plain": "method",
    }
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_chunking_decorator_kinds.py -v
```

Expected: FAIL — current kinds all read `"method"`.

- [ ] **Step 3: Implement decorator inspection**

In `src/perspicacite/pipeline/chunking_code.py`, add a small helper near the top of the file:

```python
def _method_kind_from_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return symbol_kind based on @classmethod / @staticmethod / @property decorators.

    Falls back to "method" for anything else (including custom decorators).
    """
    for dec in node.decorator_list:
        name: str | None = None
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name == "classmethod":
            return "classmethod"
        if name == "staticmethod":
            return "staticmethod"
        if name == "property":
            return "property"
    return "method"
```

Inside the method-sub-chunk loop from Task 1, replace `symbol_kind="method"` with:

```python
symbol_kind=_method_kind_from_decorators(sub),
```

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/unit/test_chunking_decorator_kinds.py -v
pytest tests/unit/test_chunking_method_level.py -v
pytest tests/unit/test_chunking_code_ast.py -v
```

Expected: all PASS. Task 1's test still passes because `"method"` is the default for un-decorated functions.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/chunking_code.py tests/unit/test_chunking_decorator_kinds.py
git commit -m "feat(chunking): decorator-aware symbol_kind for class methods"
```

---

## Task 3: arXiv-id fallback in DOI resolver and OpenAlex seed lookup (P1 #4)

**Why:** RAG paper DOI `10.48550/arXiv.2005.11401` returns 404 from OpenAlex `/works/doi:` because OpenAlex indexes it without a DOI link. Many ML papers are arXiv-only. We parse `10.48550/arXiv.YYYY.NNNNN` and retry via the OpenAlex `external_ids` search.

**Files:**
- Create: `src/perspicacite/pipeline/arxiv_ids.py` (small pure-function module)
- Modify: `src/perspicacite/pipeline/snowball.py` (fallback in `openalex_id_for_doi`)
- Modify: `src/perspicacite/pipeline/library_doi.py` (expose arXiv ids as DOIs returned to caller — best-effort)
- Test: `tests/unit/test_arxiv_id_fallback.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_arxiv_id_fallback.py`:

```python
from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.arxiv_ids import parse_arxiv_doi
from perspicacite.pipeline.snowball import openalex_id_for_doi


def test_parse_arxiv_doi_extracts_id():
    assert parse_arxiv_doi("10.48550/arXiv.2005.11401") == "2005.11401"
    assert parse_arxiv_doi("10.48550/arxiv.2305.12345v2") == "2305.12345v2"
    assert parse_arxiv_doi("10.1038/nature12373") is None
    assert parse_arxiv_doi("") is None
    assert parse_arxiv_doi(None) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_openalex_id_for_doi_arxiv_fallback(monkeypatch):
    """When /works/doi:... 404s for an arXiv DOI, retry via arxiv_id filter."""
    calls: list[str] = []

    async def fake_get(self, url, **kwargs):
        calls.append(str(url) + "?" + repr(kwargs.get("params") or {}))
        req = httpx.Request("GET", url)
        # First call: /works/doi:10.48550/arXiv.2005.11401 -> 404
        if "doi:" in str(url):
            return httpx.Response(404, json={}, request=req)
        # Second call: /works?filter=ids.arxiv:2005.11401 -> 1 hit
        params = kwargs.get("params") or {}
        assert params.get("filter") == "ids.arxiv:2005.11401"
        return httpx.Response(
            200,
            json={"results": [{"id": "https://openalex.org/W3098425262"}]},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(
            client, "10.48550/arXiv.2005.11401", headers={},
        )
    assert oa_id == "W3098425262"
    assert len(calls) == 2  # primary + fallback
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_arxiv_id_fallback.py -v
```

Expected: FAIL — `parse_arxiv_doi` does not exist, and `openalex_id_for_doi` currently returns None on the 404.

- [ ] **Step 3: Create the arxiv_ids module**

Create `src/perspicacite/pipeline/arxiv_ids.py`:

```python
"""Parsing helpers for arXiv-style DOIs.

OpenAlex frequently indexes arXiv preprints with no DOI link even though the
canonical DOI of the form ``10.48550/arXiv.YYYY.NNNNN`` exists. We parse the
arXiv id out of that DOI and use the ``ids.arxiv`` OpenAlex filter as a
fallback when ``/works/doi:`` returns 404.
"""
from __future__ import annotations

import re
from typing import Optional

# Matches: 10.48550/arXiv.2005.11401  /  10.48550/arxiv.2305.12345v2
_ARXIV_DOI_RE = re.compile(
    r"^\s*10\.48550/arxiv\.(\d{4}\.\d{4,5}(?:v\d+)?)\s*$",
    re.IGNORECASE,
)


def parse_arxiv_doi(doi: Optional[str]) -> Optional[str]:
    """Return arXiv id (e.g. ``2005.11401``) or None if not an arXiv DOI."""
    if not doi:
        return None
    m = _ARXIV_DOI_RE.match(doi)
    return m.group(1) if m else None
```

- [ ] **Step 4: Wire fallback into openalex_id_for_doi**

In `src/perspicacite/pipeline/snowball.py`, locate `openalex_id_for_doi` and modify it so that when the primary `/works/doi:<doi>` call misses, we try the arXiv fallback. Add the import at the top of the file:

```python
from perspicacite.pipeline.arxiv_ids import parse_arxiv_doi
```

Replace the body of `openalex_id_for_doi` so that the fallback runs only after a None result and only when the DOI is in arXiv form. The new body should look like:

```python
async def openalex_id_for_doi(
    client: httpx.AsyncClient, doi: str, *, headers: dict[str, str]
) -> Optional[str]:
    """Resolve a DOI to an OpenAlex Work id (e.g. ``W3098425262``).

    Tries ``/works/doi:<doi>`` first; if that 404s and the DOI is an arXiv
    DOI (``10.48550/arXiv.<id>``), retries via the ``ids.arxiv`` filter.
    Returns None if neither path resolves.
    """
    # Primary: /works/doi:<doi>
    url = f"{OPENALEX_BASE}/works/doi:{doi}"
    try:
        resp = await client.get(url, headers=headers, timeout=20.0)
    except httpx.HTTPError:
        resp = None
    if resp is not None and resp.status_code == 200:
        data = resp.json() or {}
        oa_url = data.get("id")
        if isinstance(oa_url, str) and "/W" in oa_url:
            return oa_url.rsplit("/", 1)[-1]

    # Fallback: arXiv-id filter for arXiv DOIs.
    arxiv_id = parse_arxiv_doi(doi)
    if arxiv_id is None:
        return None
    list_url = f"{OPENALEX_BASE}/works"
    try:
        resp = await client.get(
            list_url,
            params={"filter": f"ids.arxiv:{arxiv_id}", "per-page": "1"},
            headers=headers,
            timeout=20.0,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    results = (resp.json() or {}).get("results") or []
    if not results:
        return None
    oa_url = results[0].get("id")
    if not isinstance(oa_url, str) or "/W" not in oa_url:
        return None
    return oa_url.rsplit("/", 1)[-1]
```

- [ ] **Step 5: Run tests to verify pass**

```
pytest tests/unit/test_arxiv_id_fallback.py tests/unit/test_snowball_public_helpers.py -v
```

Expected: all PASS. Existing tests still mock the primary `doi:` endpoint and don't observe the fallback path.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/pipeline/arxiv_ids.py src/perspicacite/pipeline/snowball.py tests/unit/test_arxiv_id_fallback.py
git commit -m "feat(snowball): arXiv-id fallback when /works/doi: 404s"
```

---

## Task 4: `--openalex-id` flag for `enrich-cite-graph` (P2 #5)

**Why:** Users who already know the W-id of a paper should be able to bypass DOI resolution entirely — useful when the DOI is missing or arXiv-only and the user has done the lookup by hand. Plumbs through CLI → orchestrator → MCP wrapper.

**Files:**
- Modify: `src/perspicacite/pipeline/cite_graph.py` (`enrich_kb_from_cite_graph` accepts `openalex_id`)
- Modify: `src/perspicacite/cli.py` (`--openalex-id` option on `enrich-cite-graph`)
- Modify: `src/perspicacite/mcp/server.py` (`enrich_kb_from_cite_graph_tool` parameter)
- Test: `tests/unit/test_cite_graph_openalex_id.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cite_graph_openalex_id.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.pipeline import cite_graph as cg
from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig


@pytest.mark.asyncio
async def test_orchestrator_accepts_openalex_id_and_skips_resolution(monkeypatch):
    """When openalex_id is supplied, we never call resolve_library_paper or
    openalex_id_for_doi; we fetch cited-by works directly."""
    seen: dict[str, object] = {"resolved": False, "doi_lookup": False}

    async def fake_resolve(*a, **kw):
        seen["resolved"] = True
        return None

    async def fake_doi_lookup(*a, **kw):
        seen["doi_lookup"] = True
        return None

    async def fake_resolve_and_fetch(*, tool, doi, openalex_id, headers, client, max_results):
        # We expect openalex_id passed straight through.
        assert openalex_id == "W3177828909"
        assert doi is None
        assert tool is None
        return ([{"id": "https://openalex.org/W10", "doi": "10.1/test"}], "AlphaFold seed title")

    monkeypatch.setattr(cg, "resolve_library_paper", fake_resolve)
    monkeypatch.setattr(cg, "openalex_id_for_doi", fake_doi_lookup)
    monkeypatch.setattr(cg, "_resolve_and_fetch", fake_resolve_and_fetch)

    kb = KnowledgeBaseConfig(name="test", cite_graph=CiteGraphConfig(min_citations=0, min_year_offset=-50))
    hits = await cg.enrich_kb_from_cite_graph(
        openalex_id="W3177828909",
        kb_config=kb,
        existing_dois=set(),
        dry_run=True,
        now_year=2025,
    )
    assert isinstance(hits, list)
    assert seen["resolved"] is False
    assert seen["doi_lookup"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_cite_graph_openalex_id.py -v
```

Expected: FAIL — `enrich_kb_from_cite_graph` does not accept `openalex_id` keyword.

- [ ] **Step 3: Plumb `openalex_id` through the orchestrator**

In `src/perspicacite/pipeline/cite_graph.py`:

1. Update `_resolve_and_fetch` to accept `openalex_id: Optional[str]` and, when set, skip both `resolve_library_paper` and `openalex_id_for_doi` — directly call `fetch_cited_by_works` with `seed_work = {"id": f"https://openalex.org/{openalex_id}"}` and seed_title fetched from `/works/{openalex_id}`:

```python
async def _resolve_and_fetch(
    *,
    tool: Optional[str],
    doi: Optional[str],
    openalex_id: Optional[str],
    headers: dict[str, str],
    client: httpx.AsyncClient,
    max_results: int,
) -> tuple[list[dict], Optional[str]]:
    if openalex_id:
        # Skip resolution. Fetch the seed work directly for its title.
        seed_url = f"{OPENALEX_BASE}/works/{openalex_id}"
        try:
            resp = await client.get(seed_url, headers=headers, timeout=20.0)
            seed_work = resp.json() if resp.status_code == 200 else {
                "id": f"https://openalex.org/{openalex_id}"
            }
        except httpx.HTTPError:
            seed_work = {"id": f"https://openalex.org/{openalex_id}"}
        seed_title = (seed_work.get("title") or seed_work.get("display_name")) if isinstance(seed_work, dict) else None
        works = await fetch_cited_by_works(
            client, seed_work=seed_work, max_results=max_results, headers=headers,
        )
        return works, seed_title
    # ... existing tool/doi resolution path unchanged ...
```

   Preserve the existing resolution path below this branch.

2. Update `enrich_kb_from_cite_graph` signature:

```python
async def enrich_kb_from_cite_graph(
    *,
    tool: Optional[str] = None,
    doi: Optional[str] = None,
    openalex_id: Optional[str] = None,
    kb_config,
    existing_dois: set[str],
    dry_run: bool = False,
    now_year: Optional[int] = None,
) -> list[CiteHit]:
```

   and forward `openalex_id=openalex_id` into the `_resolve_and_fetch` call.

3. Update the early-validation guard: previously we required at least one of `tool` / `doi`; now require at least one of `tool` / `doi` / `openalex_id` and raise `ValueError("must supply tool, doi, or openalex_id")` otherwise.

- [ ] **Step 4: Add the CLI option**

In `src/perspicacite/cli.py`, locate `cli_enrich_cite_graph` (around line 1440). Add the new option above `--dry-run`:

```python
@click.option(
    "--openalex-id",
    default=None,
    help="Skip resolver and use this OpenAlex Work id (e.g. W3177828909).",
)
```

Add `openalex_id: str | None` to the function signature, and forward it into the orchestrator call:

```python
hits = await enrich_kb_from_cite_graph(
    tool=tool,
    doi=doi,
    openalex_id=openalex_id,
    kb_config=kb_cfg,
    existing_dois=existing,
    dry_run=dry_run,
    now_year=datetime.now().year,
)
```

- [ ] **Step 5: Add the MCP tool parameter**

In `src/perspicacite/mcp/server.py`, locate `enrich_kb_from_cite_graph_tool` and add `openalex_id: str | None = None` to its signature. Forward it into `enrich_kb_from_cite_graph(...)` the same way.

- [ ] **Step 6: Run tests to verify pass**

```
pytest tests/unit/test_cite_graph_openalex_id.py tests/unit/test_cite_graph_cli.py tests/unit/test_cite_graph_dry_run.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/pipeline/cite_graph.py src/perspicacite/cli.py src/perspicacite/mcp/server.py tests/unit/test_cite_graph_openalex_id.py
git commit -m "feat(cite-graph): --openalex-id flag bypasses DOI resolution"
```

---

## Task 5: `--include-scripts` cite-graph orchestrator (P3 #9)

**Why:** `CiteGraphConfig.include_scripts` was plumbed but explicitly deferred. When enabled, for each kept `CiteHit` that has a linkable GitHub repo, fetch up to 3 most-relevant scripts via the existing `fetch_github_repo` helper and attach them as a `scripts: list[dict]` field on the hit. v1 keeps this behind the config flag (default False) and only runs when `dry_run=False`.

**Files:**
- Modify: `src/perspicacite/pipeline/cite_graph.py` (`CiteHit` gets `scripts`; orchestrator branches on `include_scripts`)
- Test: `tests/unit/test_cite_graph_include_scripts.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cite_graph_include_scripts.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.pipeline import cite_graph as cg
from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig


@pytest.mark.asyncio
async def test_include_scripts_attaches_scripts_to_hits(monkeypatch):
    fake_hit_oa_work = {
        "id": "https://openalex.org/W10",
        "doi": "https://doi.org/10.1/test",
        "title": "Cited paper",
        "publication_year": 2024,
        "cited_by_count": 50,
        "abstract_inverted_index": {"alphafold": [0]},
        "open_access": {"is_oa": True},
        "best_oa_location": {"source": {"display_name": "Nature"}},
        # Pretend the openalex record carries a GitHub repo for the citing paper:
        "external_ids": [],
    }

    async def fake_resolve_and_fetch(*, tool, doi, openalex_id, headers, client, max_results):
        return ([fake_hit_oa_work], "AlphaFold seed")

    async def fake_repo_lookup(client, oa_work, *, headers):
        return "deepmind/alphafold"  # the github "owner/repo" extracted

    async def fake_fetch_repo(full_name, *, cache_dir, ttl_seconds, token=None):
        return {"scripts": [
            {"path": "fold.py", "text": "def f():\n    return 1\n"},
            {"path": "io.py",   "text": "def g():\n    return 2\n"},
        ]}

    monkeypatch.setattr(cg, "_resolve_and_fetch", fake_resolve_and_fetch)
    monkeypatch.setattr(cg, "_github_repo_for_work", fake_repo_lookup, raising=False)
    monkeypatch.setattr(cg, "fetch_github_repo", fake_fetch_repo, raising=False)

    kb = KnowledgeBaseConfig(
        name="t",
        cite_graph=CiteGraphConfig(min_citations=0, min_year_offset=-10, include_scripts=True),
    )
    hits = await cg.enrich_kb_from_cite_graph(
        tool="alphafold",
        kb_config=kb,
        existing_dois=set(),
        dry_run=False,
        now_year=2025,
    )
    assert len(hits) == 1
    hit = hits[0]
    assert hasattr(hit, "scripts")
    assert isinstance(hit.scripts, list)
    assert len(hit.scripts) >= 1
    assert hit.scripts[0]["path"].endswith(".py")


@pytest.mark.asyncio
async def test_include_scripts_off_by_default(monkeypatch):
    fake_hit_oa_work = {
        "id": "https://openalex.org/W10",
        "doi": "https://doi.org/10.1/test",
        "title": "Cited paper",
        "publication_year": 2024,
        "cited_by_count": 50,
        "abstract_inverted_index": {"alphafold": [0]},
        "open_access": {"is_oa": True},
    }

    async def fake_resolve_and_fetch(*, tool, doi, openalex_id, headers, client, max_results):
        return ([fake_hit_oa_work], "AlphaFold seed")

    monkeypatch.setattr(cg, "_resolve_and_fetch", fake_resolve_and_fetch)

    kb = KnowledgeBaseConfig(
        name="t",
        cite_graph=CiteGraphConfig(min_citations=0, min_year_offset=-10, include_scripts=False),
    )
    hits = await cg.enrich_kb_from_cite_graph(
        tool="alphafold",
        kb_config=kb,
        existing_dois=set(),
        dry_run=False,
        now_year=2025,
    )
    assert len(hits) == 1
    assert hits[0].scripts == []
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_cite_graph_include_scripts.py -v
```

Expected: FAIL — `CiteHit` has no `scripts` field.

- [ ] **Step 3: Add `scripts` to `CiteHit` and orchestrator branch**

In `src/perspicacite/pipeline/cite_graph.py`:

1. Add field to `CiteHit` dataclass:

```python
@dataclass(frozen=True)
class CiteHit:
    # ... existing fields ...
    scripts: list[dict] = field(default_factory=list)
```

   (Add `from dataclasses import field` if not already imported.)

2. Add a small helper near the top that extracts a `"owner/repo"` from an OpenAlex work, scanning the work's `external_ids`, `primary_location.source`, and abstract for a GitHub URL. Conservative: returns None if nothing found.

```python
import re

_GITHUB_REPO_RE = re.compile(r"github\.com/([\w.-]+/[\w.-]+)", re.IGNORECASE)


def _github_repo_for_work(_client, oa_work: dict, *, headers: dict) -> Optional[str]:
    """Best-effort extraction of ``owner/repo`` for a citing paper."""
    blobs: list[str] = []
    blobs.append(str(oa_work.get("doi") or ""))
    pl = oa_work.get("primary_location") or {}
    if isinstance(pl, dict):
        blobs.append(str(pl.get("landing_page_url") or ""))
    for url_field in ("alternate_landing_page_urls", "external_ids"):
        v = oa_work.get(url_field)
        if isinstance(v, list):
            for item in v:
                blobs.append(str(item))
    for blob in blobs:
        m = _GITHUB_REPO_RE.search(blob)
        if m:
            return m.group(1)
    return None
```

   (The test passes this function in via monkeypatch, so the actual extraction logic is not exercised by Task 5's tests — but it's required for live execution.)

3. After `apply_cite_graph_filters` produces the kept list, add a post-pass that, when `kb_config.cite_graph.include_scripts is True` and `dry_run is False`, calls `_github_repo_for_work` and `fetch_github_repo` for each hit, capping at 3 scripts per hit. Wrap in try/except so a failed lookup just skips. The pattern:

```python
if kb_config.cite_graph.include_scripts and not dry_run:
    enriched: list[CiteHit] = []
    for hit, oa_work in zip(kept_hits, kept_oa_works):
        try:
            repo = _github_repo_for_work(client, oa_work, headers=headers)
            if not repo:
                enriched.append(hit)
                continue
            repo_blob = await fetch_github_repo(
                repo, cache_dir=str(github_cache_dir), ttl_seconds=30 * 86400,
            )
            scripts = (repo_blob.get("scripts") or [])[:3]
            enriched.append(replace(hit, scripts=scripts))
        except Exception:  # noqa: BLE001
            enriched.append(hit)
    kept_hits = enriched
```

   Add `from dataclasses import replace` and `from perspicacite.pipeline.external.fetch_github import fetch_github_repo`. The exact tuple-shape `(kept_hits, kept_oa_works)` may require threading `oa_works` alongside `kept_hits`; if the current implementation already loses them, retain a parallel list at filter time.

- [ ] **Step 4: Run tests to verify pass**

```
pytest tests/unit/test_cite_graph_include_scripts.py tests/unit/test_cite_graph_dry_run.py tests/unit/test_cite_graph_scoring.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/cite_graph.py tests/unit/test_cite_graph_include_scripts.py
git commit -m "feat(cite-graph): honour include_scripts to fetch citing-paper scripts"
```

---

## Task 6: Figure thumbnail rendering in web UI (P2 #7)

**Why:** Sub-project C's `FigureRef` model already has a `thumbnail_b64` field, but `renderFigureRef()` only renders label + caption. We populate `thumbnail_b64` from the capsule's `figures/<fid>.png` when the file exists and render it as an `<img>` thumbnail.

**Files:**
- Modify: `src/perspicacite/rag/figure_refs.py` (load thumbnail bytes when present)
- Modify: `static/js/chat.js` (`renderFigureRef` shows `<img>` when `thumbnail_b64` set)
- Modify: `static/css/chat.css` (`.figure-card img` styling)
- Test: `tests/unit/test_figure_refs_thumbnail.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_figure_refs_thumbnail.py`:

```python
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from perspicacite.rag.figure_refs import collect_figure_refs
from perspicacite.models.documents import DocumentChunk, ChunkMetadata


def _png_bytes() -> bytes:
    # Minimal 1x1 PNG (valid header) — enough to base64-roundtrip.
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000a49444154789c6300010000000500010d0a2db4000000004945"
        "4e44ae426082"
    )


def _chunk_with_fig(paper_id: str, fig_id: str) -> DocumentChunk:
    md = ChunkMetadata(
        paper_id=paper_id,
        chunk_index=0,
        content_type="text",
        figure_refs=[{"id": fig_id, "label": "Fig 1", "caption": "A figure."}],
    )
    return DocumentChunk(id=f"{paper_id}_0", text="...", metadata=md)


def test_collect_figure_refs_loads_thumbnail_when_present(tmp_path: Path):
    paper_id = "p1"
    fig_id = "f1"
    capsule_root = tmp_path / "capsule"
    fig_dir = capsule_root / paper_id / "figures"
    fig_dir.mkdir(parents=True)
    (fig_dir / f"{fig_id}.png").write_bytes(_png_bytes())

    refs = collect_figure_refs(
        [_chunk_with_fig(paper_id, fig_id)], capsule_root=capsule_root,
    )
    assert len(refs) == 1
    assert refs[0].thumbnail_b64 is not None
    decoded = base64.b64decode(refs[0].thumbnail_b64)
    assert decoded == _png_bytes()


def test_collect_figure_refs_thumbnail_none_when_missing(tmp_path: Path):
    refs = collect_figure_refs(
        [_chunk_with_fig("p1", "f-missing")], capsule_root=tmp_path / "capsule",
    )
    assert len(refs) == 1
    assert refs[0].thumbnail_b64 is None
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_figure_refs_thumbnail.py -v
```

Expected: FAIL on `test_collect_figure_refs_loads_thumbnail_when_present` — current collector does not populate `thumbnail_b64`.

- [ ] **Step 3: Implement thumbnail loading**

In `src/perspicacite/rag/figure_refs.py`, locate `collect_figure_refs`. Where it constructs each `FigureRef(...)`, add a best-effort thumbnail load:

```python
import base64
from pathlib import Path

# Cap to avoid base64-encoding large figures into SSE events.
_THUMBNAIL_MAX_BYTES = 200_000


def _load_thumbnail_b64(capsule_root: Optional[Path], paper_id: str, fig_id: str) -> Optional[str]:
    if not capsule_root or not paper_id or not fig_id:
        return None
    candidate = Path(capsule_root) / paper_id / "figures" / f"{fig_id}.png"
    try:
        if not candidate.exists():
            return None
        data = candidate.read_bytes()
    except OSError:
        return None
    if len(data) > _THUMBNAIL_MAX_BYTES:
        return None
    return base64.b64encode(data).decode("ascii")
```

In the FigureRef construction, pass `thumbnail_b64=_load_thumbnail_b64(capsule_root, paper_id, fig_id)`.

- [ ] **Step 4: Render the thumbnail in JS**

In `static/js/chat.js`, locate `renderFigureRef(payload)`. Replace its body so it prepends an `<img>` when `payload.thumbnail_b64` is present:

```javascript
function renderFigureRef(payload) {
    const panel = document.getElementById('figures-panel');
    const list = document.getElementById('figures-list');
    if (!panel || !list) return;
    panel.style.display = '';
    const card = document.createElement('div');
    card.className = 'figure-card';

    if (payload.thumbnail_b64) {
        const img = document.createElement('img');
        img.src = `data:image/png;base64,${payload.thumbnail_b64}`;
        img.alt = payload.label || 'Figure thumbnail';
        img.className = 'figure-thumbnail';
        card.appendChild(img);
    }

    if (payload.label) {
        const lbl = document.createElement('div');
        lbl.className = 'figure-label';
        lbl.textContent = payload.label;
        card.appendChild(lbl);
    }
    if (payload.caption) {
        const cap = document.createElement('div');
        cap.className = 'figure-caption';
        cap.textContent = payload.caption;
        card.appendChild(cap);
    }
    list.appendChild(card);
}
```

- [ ] **Step 5: Add CSS for the thumbnail**

In `static/css/chat.css`, near the existing `.figure-card` rule, append:

```css
.figure-card .figure-thumbnail {
    max-width: 100%;
    max-height: 240px;
    display: block;
    margin-bottom: 0.5rem;
    border-radius: 4px;
    border: 1px solid var(--border-color, #ddd);
    object-fit: contain;
}
```

- [ ] **Step 6: Run tests to verify pass**

```
pytest tests/unit/test_figure_refs_thumbnail.py tests/unit/test_figure_refs.py -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/rag/figure_refs.py static/js/chat.js static/css/chat.css tests/unit/test_figure_refs_thumbnail.py
git commit -m "feat(web-ui): render figure thumbnails from capsule when present"
```

---

## Task 7: bm25s migration with persistent index (P3 #10)

**Why:** `rank-bm25` rebuilds the corpus on every `route_kbs()` call. `bm25s` is a faster pure-Python BM25 (Lucene-style) with persistent on-disk indices. This task migrates the KB router and adds a cache keyed by `(kb_set hash, corpus hash)` so we rebuild only when KB contexts change.

**Files:**
- Modify: `pyproject.toml` (replace `rank-bm25` with `bm25s`)
- Modify: `src/perspicacite/rag/kb_router.py` (use `bm25s.BM25` + cache)
- Test: `tests/unit/test_kb_router_bm25s.py` (new)

- [ ] **Step 1: Add bm25s dependency**

Edit `pyproject.toml`. In the dependencies list, replace:

```
"rank-bm25>=0.2.2",
```

with:

```
"bm25s>=0.2.0",
```

Then install:

```
uv pip install -e .
```

(or whatever the project's install command is — fall back to `pip install -e .` if no `uv` is configured).

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_kb_router_bm25s.py`:

```python
from __future__ import annotations

import pytest

from perspicacite.rag.kb_router import route_kbs, _bm25_cache_clear


def test_route_kbs_returns_relevant_kbs():
    _bm25_cache_clear()
    kb_contexts = {
        "biochem":    "alphafold protein structure prediction folding",
        "ml_general": "transformer attention language model gpt",
        "math":       "theorem proof lemma category topology",
    }
    chosen = route_kbs(
        query="how does alphafold predict protein structure",
        kb_contexts=kb_contexts,
        top_k=2,
    )
    assert "biochem" in chosen
    assert "math" not in chosen


def test_route_kbs_cache_reuses_index(monkeypatch):
    _bm25_cache_clear()
    kb_contexts = {
        "a": "alpha beta gamma",
        "b": "delta epsilon zeta",
    }
    calls = {"build": 0}

    import perspicacite.rag.kb_router as kr
    orig_build = kr._build_bm25_index

    def counting_build(corpus_tokens, *, fingerprint):
        calls["build"] += 1
        return orig_build(corpus_tokens, fingerprint=fingerprint)

    monkeypatch.setattr(kr, "_build_bm25_index", counting_build)

    route_kbs(query="alpha", kb_contexts=kb_contexts, top_k=1)
    route_kbs(query="delta", kb_contexts=kb_contexts, top_k=1)
    # Same corpus → cache hit.
    assert calls["build"] == 1

    # Different corpus → rebuild.
    route_kbs(query="alpha", kb_contexts={"x": "alpha"}, top_k=1)
    assert calls["build"] == 2
```

- [ ] **Step 3: Run test to verify it fails**

```
pytest tests/unit/test_kb_router_bm25s.py -v
```

Expected: FAIL — `_bm25_cache_clear` and `_build_bm25_index` don't exist.

- [ ] **Step 4: Migrate kb_router.py**

In `src/perspicacite/rag/kb_router.py`, replace the BM25 implementation. Read the current file first; the migration:

1. Remove `from rank_bm25 import BM25Okapi`.
2. Add at top:

```python
import bm25s
import hashlib

_BM25_CACHE: dict[str, tuple[bm25s.BM25, list[str]]] = {}


def _corpus_fingerprint(kb_contexts: dict[str, str]) -> str:
    """Stable fingerprint of (name, text) pairs — invalidates cache on edits."""
    h = hashlib.sha1()
    for name in sorted(kb_contexts):
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(kb_contexts[name].encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


def _bm25_cache_clear() -> None:
    _BM25_CACHE.clear()


def _build_bm25_index(
    corpus_tokens: list[list[str]], *, fingerprint: str,
) -> bm25s.BM25:
    retriever = bm25s.BM25(method="lucene")
    retriever.index(corpus_tokens)
    return retriever
```

3. Replace the inside of `route_kbs(...)` so that index construction goes through the cache. Pseudocode for the relevant block:

```python
fingerprint = _corpus_fingerprint(kb_contexts)
cached = _BM25_CACHE.get(fingerprint)
if cached is None:
    kb_names = list(kb_contexts.keys())
    corpus_tokens = [tokenize(kb_contexts[n]) for n in kb_names]
    retriever = _build_bm25_index(corpus_tokens, fingerprint=fingerprint)
    _BM25_CACHE[fingerprint] = (retriever, kb_names)
else:
    retriever, kb_names = cached

query_tokens = tokenize(query)
# bm25s.retrieve returns ndarray indices + scores. Convert to top-k names.
results, scores = retriever.retrieve([query_tokens], k=min(top_k, len(kb_names)))
chosen = [kb_names[int(i)] for i in results[0]]
return chosen
```

   Adapt the surrounding code (e.g. preserve existing return shape if the caller expects a list of (name, score) tuples — check the current signature and keep the same return type).

- [ ] **Step 5: Run tests to verify pass**

```
pytest tests/unit/test_kb_router_bm25s.py -v
pytest tests/unit/ -k "kb_router or routing" -v
```

Expected: all PASS. If any pre-existing kb_router tests assumed `rank_bm25` import or specific score values, update them to match the new tokeniser/scoring (semantics-preserving migration; exact scores will differ).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/perspicacite/rag/kb_router.py tests/unit/test_kb_router_bm25s.py
git commit -m "feat(rag): migrate kb_router to bm25s with corpus-fingerprint cache"
```

---

## Final verification

After all 7 tasks are committed:

- [ ] Run full test suite:

```
pytest tests/unit/ -x -q
```

Expected: all PASS, no regressions. If any pre-existing test broke, address it before declaring the batch done.

- [ ] Smoke-run the audit harness to confirm cite-graph still resolves AlphaFold and now resolves the arXiv RAG paper:

```
python tests/audit/run_2026_05_15_audit.py
```

Expected output: RAG cite-graph hits > 0 (was 0 before Task 3); AlphaFold cite-graph hits unchanged (~7–10 topical hits). Append findings to `tests/audit/results/2026-05-15-audit-findings.md` under a "Post-batch audit" section.
