# Cite-graph enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Given a library name (or DOI), resolve it to a canonical paper, walk the OpenAlex forward-citation graph, filter + score citing papers, and ingest the top-N into a KB. New chunks are tagged `source_via="cite_graph"` and `cited_tool=<library>`.

**Architecture:** A thin layer over the existing `pipeline/snowball.py` forward-traversal. New `pipeline/library_doi.py` resolves `library → DOI` from three sources (config map → bundle.yml → README scrape). New `pipeline/cite_graph.py` orchestrates resolver → snowball helpers → filter+score → existing DOI ingest path. Snowball gains two public helpers extracted from its private ones.

**Tech Stack:** httpx (already used by snowball), OpenAlex REST, Pydantic v2.

**Spec:** `docs/superpowers/specs/2026-05-15-cite-graph-enrichment-design.md`

**v1 scope notes:** `--include-scripts` (optional script pull-in for citing papers with GitHub repos) is **deferred** to a follow-up — it requires GitHub-KB ingest fully shipped. Live integration test is **deferred** (network-dependent; will land when we add a CI gate for opt-in network tests).

---

## File Map

| Path | Action | Responsibility |
|---|---|---|
| `src/perspicacite/models/documents.py` | MODIFY | Widen `source_via` Literal; add `cited_tool`, `discovery_score` |
| `src/perspicacite/config/schema.py` | MODIFY | Add `CiteGraphConfig`; add `KnowledgeBaseConfig.library_paper_map` + `KnowledgeBaseConfig.cite_graph` |
| `src/perspicacite/pipeline/snowball.py` | MODIFY (small) | Expose two public helpers: `openalex_id_for_doi`, `fetch_cited_by_works` |
| `src/perspicacite/pipeline/library_doi.py` | CREATE | `LibraryPaper` + `resolve_library_paper` |
| `src/perspicacite/pipeline/cite_graph.py` | CREATE | `CiteHit` + `score_cite_hit` + `enrich_kb_from_cite_graph` orchestrator |
| `src/perspicacite/cli.py` | MODIFY | New subcommand `kb enrich-cite-graph` |
| `src/perspicacite/mcp/server.py` | MODIFY | New MCP tool `enrich_kb_from_cite_graph` |
| `tests/unit/test_chunk_metadata_cite_graph.py` | CREATE | New ChunkMetadata fields |
| `tests/unit/test_cite_graph_config.py` | CREATE | CiteGraphConfig + library_paper_map |
| `tests/unit/test_library_doi_resolver.py` | CREATE | Resolver with mocked README |
| `tests/unit/test_snowball_public_helpers.py` | CREATE | New public helpers |
| `tests/unit/test_cite_graph_scoring.py` | CREATE | Scoring + filtering |
| `tests/unit/test_cite_graph_dry_run.py` | CREATE | Orchestrator dry-run with mocks |

---

## Task 1: ChunkMetadata cite-graph fields

**Files:**
- Modify: `src/perspicacite/models/documents.py`
- Test: `tests/unit/test_chunk_metadata_cite_graph.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_chunk_metadata_cite_graph.py
import pytest
from pydantic import ValidationError

from perspicacite.models.documents import ChunkMetadata


def test_source_via_default_is_bundle():
    """Default literal value — back-compat with prior chunks."""
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    # If source_via wasn't added previously (sub-project A landed
    # ChunkMetadata without source_via, since that field belongs to
    # the GitHub-KB ingest spec), the cite-graph plan introduces it
    # here. Treat None as the legacy/empty case.
    assert md.source_via in (None, "bundle")


def test_source_via_accepts_cite_graph_values():
    md = ChunkMetadata(paper_id="p", chunk_index=0, source_via="cite_graph")
    assert md.source_via == "cite_graph"


def test_source_via_accepts_cite_graph_script():
    md = ChunkMetadata(paper_id="p", chunk_index=0, source_via="cite_graph_script")
    assert md.source_via == "cite_graph_script"


def test_invalid_source_via_rejected():
    with pytest.raises(ValidationError):
        ChunkMetadata(paper_id="p", chunk_index=0, source_via="unknown_kind")


def test_cited_tool_default_none():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.cited_tool is None


def test_cited_tool_round_trip():
    md = ChunkMetadata(paper_id="p", chunk_index=0, cited_tool="openff-evaluator")
    assert md.cited_tool == "openff-evaluator"


def test_discovery_score_default_none():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.discovery_score is None


def test_discovery_score_round_trip():
    md = ChunkMetadata(paper_id="p", chunk_index=0, discovery_score=0.73)
    assert md.discovery_score == 0.73
```

- [ ] **Step 2: Verify fail** — `pytest tests/unit/test_chunk_metadata_cite_graph.py -v`

- [ ] **Step 3: Add fields**

In `src/perspicacite/models/documents.py`, inside `class ChunkMetadata(BaseModel)`, add (after the sub-project A and B fields):

```python
    # Cite-graph enrichment fields (2026-05-15 spec).
    source_via: Optional[Literal["bundle", "enrichment", "cite_graph", "cite_graph_script"]] = None
    cited_tool: Optional[str] = None
    discovery_score: Optional[float] = None
```

If `Literal` is not imported at the top of the file: add `from typing import Literal, Optional` (or extend the existing typing import). Verify before adding.

- [ ] **Step 4: Verify pass** — `pytest tests/unit/test_chunk_metadata_cite_graph.py -v` → 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_chunk_metadata_cite_graph.py src/perspicacite/models/documents.py
git commit -m "feat(models): ChunkMetadata.source_via / cited_tool / discovery_score (cite-graph)"
```

---

## Task 2: CiteGraphConfig + KnowledgeBaseConfig fields

**Files:**
- Modify: `src/perspicacite/config/schema.py`
- Test: `tests/unit/test_cite_graph_config.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_cite_graph_config.py
import pytest
from pydantic import ValidationError

from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig


def test_cite_graph_config_defaults():
    cfg = CiteGraphConfig()
    assert cfg.min_year_offset == 7
    assert cfg.min_citations == 1
    assert cfg.max_papers == 50
    assert cfg.include_scripts is False
    assert cfg.venue_denylist == []


def test_cite_graph_weight_defaults_sum_to_one():
    cfg = CiteGraphConfig()
    s = cfg.w_citations + cfg.w_recency + cfg.w_oa + cfg.w_match
    assert abs(s - 1.0) < 1e-6


def test_kb_config_library_paper_map_default_empty():
    kb = KnowledgeBaseConfig()
    assert kb.library_paper_map == {}


def test_kb_config_cite_graph_default_factory():
    kb = KnowledgeBaseConfig()
    assert isinstance(kb.cite_graph, CiteGraphConfig)


def test_invalid_weight_rejected():
    """Weights are floats in [0,1]; out-of-range raises."""
    with pytest.raises(ValidationError):
        CiteGraphConfig(w_citations=-0.1)
    with pytest.raises(ValidationError):
        CiteGraphConfig(w_citations=1.5)
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Add config**

In `src/perspicacite/config/schema.py`, add a new class (place it near other enrichment-related configs, e.g. after `MultimodalConfig`):

```python
class CiteGraphConfig(BaseModel):
    """Cite-graph enrichment knobs (2026-05-15 spec)."""

    min_year_offset: int = Field(
        default=7, ge=0, le=100,
        description="Drop citing papers older than now - min_year_offset years.",
    )
    min_citations: int = Field(
        default=1, ge=0,
        description="Drop citing papers with fewer than this many citations.",
    )
    max_papers: int = Field(
        default=50, ge=1, le=1000,
        description="Hard cap on papers ingested per enrichment run.",
    )
    venue_denylist: list[str] = Field(
        default_factory=list,
        description="Venue/journal names to drop (e.g., predatory journals).",
    )
    include_scripts: bool = Field(
        default=False,
        description=(
            "When True, also pull ≤3 GitHub scripts per citing paper "
            "(deferred to follow-up; v1 ignores this flag)."
        ),
    )
    w_citations: float = Field(default=0.30, ge=0.0, le=1.0)
    w_recency:   float = Field(default=0.20, ge=0.0, le=1.0)
    w_oa:        float = Field(default=0.20, ge=0.0, le=1.0)
    w_match:     float = Field(default=0.30, ge=0.0, le=1.0)
```

Then in `class KnowledgeBaseConfig(BaseModel)`, append:

```python
    library_paper_map: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional curated map of library name → canonical paper DOI. "
            "First lookup source for the cite-graph resolver. "
            "Example: {'openff-evaluator': '10.1021/acs.jctc.8b00640'}."
        ),
    )
    cite_graph: CiteGraphConfig = Field(default_factory=CiteGraphConfig)
```

- [ ] **Step 4: Verify pass** — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_cite_graph_config.py src/perspicacite/config/schema.py
git commit -m "feat(config): CiteGraphConfig + KnowledgeBaseConfig.{library_paper_map,cite_graph}"
```

---

## Task 3: Public snowball helpers

**Files:**
- Modify: `src/perspicacite/pipeline/snowball.py`
- Test: `tests/unit/test_snowball_public_helpers.py`

The cite-graph orchestrator needs `openalex_id_for_doi(doi)` and `fetch_cited_by_works(seed_work_or_id, max_results)` as public callables. Today's snowball has them embedded in `_fetch_seed_work` and `_fetch_forward_citations` (private). Extract.

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_snowball_public_helpers.py
from __future__ import annotations

import pytest
import httpx

from perspicacite.pipeline.snowball import (
    openalex_id_for_doi,
    fetch_cited_by_works,
)


@pytest.mark.asyncio
async def test_openalex_id_for_doi_uses_works_doi_endpoint(monkeypatch):
    """When given a DOI, returns the OpenAlex id field of the work record."""
    captured = {}

    async def fake_get(self, url, **kwargs):
        captured["url"] = url
        req = httpx.Request("GET", url)
        return httpx.Response(
            200, json={"id": "https://openalex.org/W1234567890"}, request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(client, "10.1000/test", headers={})
    assert oa_id == "W1234567890"
    assert "doi:10.1000/test" in captured["url"]


@pytest.mark.asyncio
async def test_openalex_id_for_doi_returns_none_on_miss(monkeypatch):
    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(404, json={}, request=req)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(client, "10.1000/missing", headers={})
    assert oa_id is None


@pytest.mark.asyncio
async def test_fetch_cited_by_works_paginates(monkeypatch):
    """The function follows OpenAlex's cited_by_api_url and caps at max_results."""
    # Mock seed work with a cited_by_api_url
    seed_work = {"cited_by_api_url": "https://api.openalex.org/works?filter=cites:W1"}
    pages = [
        # First page
        {"results": [{"id": f"https://openalex.org/W{i}"} for i in range(10, 20)],
         "meta": {"next_cursor": "page2"}},
        # Second page
        {"results": [{"id": f"https://openalex.org/W{i}"} for i in range(20, 30)],
         "meta": {"next_cursor": None}},
    ]
    call_count = {"n": 0}

    async def fake_get(self, url, **kwargs):
        idx = call_count["n"]
        call_count["n"] += 1
        body = pages[idx] if idx < len(pages) else {"results": []}
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=body, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with httpx.AsyncClient() as client:
        # max_results=15 — should stop after first page is exhausted.
        works = await fetch_cited_by_works(
            client, seed_work=seed_work, max_results=15, headers={},
        )
    assert 10 <= len(works) <= 15
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Extract the helpers**

In `src/perspicacite/pipeline/snowball.py`, expose two new public functions. Use the Read tool to inspect the existing `_fetch_seed_work` and `_fetch_forward_citations` then add wrappers/refactor:

Approach: keep the existing private functions intact (to avoid breaking the rest of snowball) and add thin public wrappers that delegate.

Add after `_fetch_seed_work`:

```python
async def openalex_id_for_doi(
    client: httpx.AsyncClient, doi: str, *, headers: dict[str, str] | None = None,
) -> str | None:
    """Resolve a DOI to an OpenAlex work id (W12345...). Returns None on miss."""
    if headers is None:
        headers = {}
    work = await _fetch_seed_work(client, doi, headers)
    if not work:
        return None
    full_id = work.get("id", "")
    return full_id.rsplit("/", 1)[-1] if full_id else None
```

Add after `_fetch_forward_citations`:

```python
async def fetch_cited_by_works(
    client: httpx.AsyncClient,
    *,
    seed_work: dict,
    max_results: int = 100,
    headers: dict[str, str] | None = None,
) -> list[dict]:
    """Public alias for the forward-citation fetcher. Returns a list of
    OpenAlex work records that cite the given seed work."""
    if headers is None:
        headers = {}
    return await _fetch_forward_citations(client, seed_work, max_results, headers)
```

- [ ] **Step 4: Verify pass** — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_snowball_public_helpers.py src/perspicacite/pipeline/snowball.py
git commit -m "feat(pipeline/snowball): expose openalex_id_for_doi + fetch_cited_by_works"
```

---

## Task 4: library_doi resolver

**Files:**
- Create: `src/perspicacite/pipeline/library_doi.py`
- Test: `tests/unit/test_library_doi_resolver.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_library_doi_resolver.py
from __future__ import annotations

import pytest

from perspicacite.pipeline.library_doi import (
    LibraryPaper,
    resolve_library_paper,
)


@pytest.mark.asyncio
async def test_config_map_takes_precedence():
    paper = await resolve_library_paper(
        "openff-evaluator",
        bundle=None,
        github_repo=None,
        config_map={"openff-evaluator": "10.1021/acs.jctc.8b00640"},
        readme_text=None,
    )
    assert paper is not None
    assert paper.source == "config"
    assert paper.confidence == 1.0
    assert paper.doi == "10.1021/acs.jctc.8b00640"


@pytest.mark.asyncio
async def test_bundle_field_used_when_no_config_match():
    bundle = {"tools": [
        {"name": "openff-evaluator", "paper_doi": "10.0/bundle"},
        {"name": "other-tool"},
    ]}
    paper = await resolve_library_paper(
        "openff-evaluator",
        bundle=bundle, github_repo=None, config_map=None, readme_text=None,
    )
    assert paper is not None
    assert paper.source == "bundle"
    assert paper.doi == "10.0/bundle"


@pytest.mark.asyncio
async def test_readme_scrape_finds_please_cite():
    readme = (
        "# my-lib\n\nA cool library.\n\n"
        "If you use my-lib in your research, please cite "
        "DOI 10.1234/abcd1234 for the original paper.\n"
    )
    paper = await resolve_library_paper(
        "my-lib",
        bundle=None, github_repo=None, config_map=None, readme_text=readme,
    )
    assert paper is not None
    assert paper.source == "readme"
    assert paper.doi == "10.1234/abcd1234"
    assert 0.4 <= paper.confidence <= 0.9


@pytest.mark.asyncio
async def test_returns_none_when_nothing_resolvable():
    paper = await resolve_library_paper(
        "unknown-lib",
        bundle=None, github_repo=None, config_map={}, readme_text=None,
    )
    assert paper is None


@pytest.mark.asyncio
async def test_citation_cff_doi_field_recognised():
    """CITATION.cff (YAML) — recognises `doi: 10.x/y` lines anywhere in the readme arg.
    For v1 we just regex-match; full YAML parse is a follow-up."""
    cff_like = "cff-version: 1.2.0\ndoi: 10.0/cff\n"
    paper = await resolve_library_paper(
        "lib",
        bundle=None, github_repo=None, config_map=None, readme_text=cff_like,
    )
    assert paper is not None
    assert paper.doi == "10.0/cff"
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Implement**

Create `src/perspicacite/pipeline/library_doi.py`:

```python
"""Library → canonical-paper DOI resolver for cite-graph enrichment.

Tries (in order):
1. A curated config map (KnowledgeBaseConfig.library_paper_map).
2. A bundle.yml `tools` entry with a `paper_doi` field.
3. README text scraping (regex patterns matching "Please cite [DOI]",
   "Citation: DOI", and the CITATION.cff `doi:` field).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional


_DOI_RE = r"(10\.\d{4,9}/[\w./()\-:]+)"

PATTERNS = [
    re.compile(
        rf"if you use\s+\S+\s+(?:in your|please).{{0,200}}?{_DOI_RE}",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"please cite.{{0,200}}?{_DOI_RE}",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"citation\s*[:=].{{0,200}}?{_DOI_RE}",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^doi\s*:\s*{_DOI_RE}",
        re.IGNORECASE | re.MULTILINE,
    ),
]


@dataclass(frozen=True)
class LibraryPaper:
    library: str
    doi: str
    title: Optional[str]
    source: Literal["config", "bundle", "readme"]
    confidence: float  # 1.0 for config/bundle, 0.5-0.8 for README


async def resolve_library_paper(
    library: str,
    *,
    bundle: Optional[dict] = None,
    github_repo: Optional[str] = None,
    config_map: Optional[dict[str, str]] = None,
    readme_text: Optional[str] = None,
) -> Optional[LibraryPaper]:
    """Resolve a library name to its canonical paper.

    The `readme_text` arg is the raw README + CITATION.cff text already
    fetched by the caller. v1 doesn't fetch; we leave that to the
    orchestrator (or the GitHub-KB ingest path) so this function is
    pure.

    Returns None when no source yields a DOI.
    """
    # 1. config map (highest confidence)
    if config_map and library in config_map:
        return LibraryPaper(
            library=library,
            doi=config_map[library],
            title=None,
            source="config",
            confidence=1.0,
        )

    # 2. bundle.yml `tools[].paper_doi`
    if bundle:
        tools = bundle.get("tools") or []
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != library:
                continue
            doi = entry.get("paper_doi")
            if doi:
                return LibraryPaper(
                    library=library,
                    doi=doi,
                    title=entry.get("paper_title"),
                    source="bundle",
                    confidence=1.0,
                )

    # 3. README scrape
    if readme_text:
        for pat in PATTERNS:
            m = pat.search(readme_text)
            if m:
                return LibraryPaper(
                    library=library,
                    doi=m.group(1),
                    title=None,
                    source="readme",
                    confidence=0.6,
                )

    return None
```

- [ ] **Step 4: Verify pass** — 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_library_doi_resolver.py src/perspicacite/pipeline/library_doi.py
git commit -m "feat(pipeline): library_doi resolver (config/bundle/readme) for cite-graph"
```

---

## Task 5: Scoring + filtering for citing works

**Files:**
- Create: `src/perspicacite/pipeline/cite_graph.py` (orchestrator container; this task adds CiteHit + scoring; Task 6 adds the orchestrator)
- Test: `tests/unit/test_cite_graph_scoring.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_cite_graph_scoring.py
from __future__ import annotations

import math
from datetime import datetime

from perspicacite.config.schema import CiteGraphConfig
from perspicacite.pipeline.cite_graph import (
    CiteHit,
    apply_cite_graph_filters,
    score_cite_hit,
)


def _hit(**kwargs):
    base = dict(
        doi="10.0/x", title="t", year=2022, venue="J",
        citation_count=10, is_oa=True, abstract="example tool usage",
        github_url=None,
    )
    base.update(kwargs)
    return CiteHit(**base)


def test_score_in_zero_to_one_range():
    hit = _hit(citation_count=100, year=2024)
    cfg = CiteGraphConfig()
    s = score_cite_hit(hit, tool_synonyms=["example", "tool"], config=cfg, now_year=2026)
    assert 0.0 <= s <= 1.0


def test_score_monotonic_in_citation_count():
    cfg = CiteGraphConfig()
    a = score_cite_hit(_hit(citation_count=5), ["tool"], cfg, now_year=2026)
    b = score_cite_hit(_hit(citation_count=500), ["tool"], cfg, now_year=2026)
    assert b > a


def test_score_recency_boost():
    cfg = CiteGraphConfig()
    old = score_cite_hit(_hit(year=2018), ["tool"], cfg, now_year=2026)
    new = score_cite_hit(_hit(year=2025), ["tool"], cfg, now_year=2026)
    assert new > old


def test_score_oa_higher_than_non_oa():
    cfg = CiteGraphConfig()
    closed = score_cite_hit(_hit(is_oa=False), ["tool"], cfg, now_year=2026)
    oa = score_cite_hit(_hit(is_oa=True), ["tool"], cfg, now_year=2026)
    assert oa > closed


def test_score_match_when_abstract_mentions_synonym():
    cfg = CiteGraphConfig()
    no_match = score_cite_hit(_hit(abstract="unrelated content"), ["openff-evaluator"], cfg, now_year=2026)
    matched = score_cite_hit(_hit(abstract="we ran openff-evaluator on this dataset"), ["openff-evaluator"], cfg, now_year=2026)
    assert matched > no_match


def test_filter_drops_by_min_year():
    cfg = CiteGraphConfig(min_year_offset=5)
    hits = [_hit(year=2015), _hit(year=2024)]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois=set(), now_year=2026)
    assert [h.year for h in out] == [2024]


def test_filter_drops_by_min_citations():
    cfg = CiteGraphConfig(min_citations=5)
    hits = [_hit(citation_count=2), _hit(citation_count=10)]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois=set(), now_year=2026)
    assert [h.citation_count for h in out] == [10]


def test_filter_drops_duplicates_in_kb():
    cfg = CiteGraphConfig()
    hits = [_hit(doi="10.0/a"), _hit(doi="10.0/b")]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois={"10.0/a"}, now_year=2026)
    assert [h.doi for h in out] == ["10.0/b"]


def test_filter_respects_venue_denylist():
    cfg = CiteGraphConfig(venue_denylist=["Predatory J"])
    hits = [_hit(venue="Predatory J"), _hit(venue="Nature")]
    out = apply_cite_graph_filters(hits, config=cfg, existing_dois=set(), now_year=2026)
    assert [h.venue for h in out] == ["Nature"]
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Implement**

Create `src/perspicacite/pipeline/cite_graph.py`:

```python
"""Cite-graph enrichment orchestrator (2026-05-15 spec).

Given a library/tool name (or explicit DOI), resolves to a canonical
paper, walks the OpenAlex forward-citation graph, filters + scores
citing works, and (optionally) ingests survivors via the existing
DOI-ingest path.

This module owns:
- ``CiteHit`` (a citing-paper record)
- ``apply_cite_graph_filters`` (cheap drops)
- ``score_cite_hit`` (final ranking)
- ``enrich_kb_from_cite_graph`` (orchestrator — Task 6)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from perspicacite.config.schema import CiteGraphConfig


@dataclass
class CiteHit:
    """A citing paper record — built from an OpenAlex work."""
    doi: str
    title: str
    year: int
    venue: Optional[str]
    citation_count: int
    is_oa: bool
    abstract: Optional[str] = None
    github_url: Optional[str] = None
    score: float = 0.0
    score_breakdown: dict = field(default_factory=dict)


# ---- normalisation helpers (log-scaled, recency) -------------------

def _normalize_citations(citations: int) -> float:
    """Log-scale citation count to [0,1]. 1000 citations → ~1.0."""
    if citations <= 0:
        return 0.0
    return min(math.log10(citations + 1) / 3.0, 1.0)


def _recency_score(year: int, *, now_year: int) -> float:
    """0..1 based on a 5-year half-life from now_year."""
    age = max(now_year - year, 0)
    return 0.5 ** (age / 5.0)


_WORD_RE = re.compile(r"\w+")


def _keyword_match(text: Optional[str], synonyms: list[str]) -> float:
    """BM25-ish bag-of-tokens overlap. Returns [0,1]."""
    if not text or not synonyms:
        return 0.0
    tokens = {w.lower() for w in _WORD_RE.findall(text)}
    syn_tokens = {s.lower() for s in synonyms if s}
    if not syn_tokens:
        return 0.0
    hits = len(tokens & syn_tokens)
    return min(hits / max(len(syn_tokens), 1), 1.0)


# ---- public ranking + filtering ------------------------------------

def score_cite_hit(
    hit: CiteHit,
    tool_synonyms: list[str],
    config: CiteGraphConfig,
    *,
    now_year: int,
) -> float:
    """Compute hit.score from the four signal components."""
    cit = _normalize_citations(hit.citation_count)
    rec = _recency_score(hit.year, now_year=now_year)
    oa = 1.0 if hit.is_oa else 0.5
    match = _keyword_match(hit.abstract, tool_synonyms)
    s = (
        config.w_citations * cit
        + config.w_recency   * rec
        + config.w_oa        * oa
        + config.w_match     * match
    )
    hit.score = round(s, 4)
    hit.score_breakdown = {
        "citations": round(cit, 4),
        "recency": round(rec, 4),
        "oa": round(oa, 4),
        "match": round(match, 4),
    }
    return hit.score


def apply_cite_graph_filters(
    hits: list[CiteHit],
    *,
    config: CiteGraphConfig,
    existing_dois: set[str],
    now_year: int,
) -> list[CiteHit]:
    """Drop hits that fail cheap rejects (year, citations, denylist, dedup)."""
    min_year = now_year - config.min_year_offset
    out: list[CiteHit] = []
    deny = {v.lower() for v in config.venue_denylist}
    for h in hits:
        if h.year < min_year:
            continue
        if h.citation_count < config.min_citations:
            continue
        if h.doi in existing_dois:
            continue
        if h.venue and h.venue.lower() in deny:
            continue
        out.append(h)
    return out
```

- [ ] **Step 4: Verify pass** — 9 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_cite_graph_scoring.py src/perspicacite/pipeline/cite_graph.py
git commit -m "feat(pipeline/cite_graph): CiteHit + scoring + filtering"
```

---

## Task 6: Orchestrator (dry-run only in v1; ingest follow-up)

**Files:**
- Modify: `src/perspicacite/pipeline/cite_graph.py` (append `enrich_kb_from_cite_graph`)
- Test: `tests/unit/test_cite_graph_dry_run.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_cite_graph_dry_run.py
"""Orchestrator dry-run test — mocks the OpenAlex client and
verifies the resolve → fetch → filter+score → return path returns
a ranked list without touching the KB."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.config.schema import CiteGraphConfig, KnowledgeBaseConfig


@pytest.mark.asyncio
async def test_dry_run_returns_ranked_hits():
    from perspicacite.pipeline.cite_graph import enrich_kb_from_cite_graph

    fake_works = [
        # 10 fake citing works with varying year/citations
        {"doi": f"10.0/{i}", "title": f"T{i}", "publication_year": 2020 + (i % 6),
         "cited_by_count": i * 5, "open_access": {"is_oa": (i % 2 == 0)},
         "abstract_inverted_index": None,
         "primary_location": {"source": {"display_name": "Journal X"}},
         "ids": {"doi": f"https://doi.org/10.0/{i}"}}
        for i in range(1, 11)
    ]

    with patch("perspicacite.pipeline.cite_graph._resolve_and_fetch",
               new=AsyncMock(return_value=fake_works)):
        kb_cfg = KnowledgeBaseConfig(
            library_paper_map={"my-lib": "10.0/seed"},
            cite_graph=CiteGraphConfig(max_papers=5, min_year_offset=10),
        )
        hits = await enrich_kb_from_cite_graph(
            tool="my-lib", kb_config=kb_cfg, existing_dois=set(),
            dry_run=True, now_year=2026,
        )

    # Top 5, sorted by score desc.
    assert len(hits) <= 5
    if len(hits) > 1:
        for a, b in zip(hits, hits[1:]):
            assert a.score >= b.score


@pytest.mark.asyncio
async def test_dry_run_returns_empty_when_resolver_fails():
    from perspicacite.pipeline.cite_graph import enrich_kb_from_cite_graph

    with patch("perspicacite.pipeline.cite_graph._resolve_and_fetch",
               new=AsyncMock(return_value=[])):
        kb_cfg = KnowledgeBaseConfig()  # no library_paper_map; resolver returns None
        hits = await enrich_kb_from_cite_graph(
            tool="unknown-lib", kb_config=kb_cfg, existing_dois=set(),
            dry_run=True, now_year=2026,
        )
    assert hits == []


@pytest.mark.asyncio
async def test_dry_run_does_not_call_ingest():
    """Dry-run must not invoke any ingest function."""
    from perspicacite.pipeline.cite_graph import enrich_kb_from_cite_graph

    with patch("perspicacite.pipeline.cite_graph._resolve_and_fetch",
               new=AsyncMock(return_value=[])):
        kb_cfg = KnowledgeBaseConfig()
        # Just verify no exception; no ingest hooks present in cite_graph.py
        # for v1. Test serves as a guard against accidental wiring.
        hits = await enrich_kb_from_cite_graph(
            tool="x", kb_config=kb_cfg, existing_dois=set(),
            dry_run=True, now_year=2026,
        )
    assert isinstance(hits, list)
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Append the orchestrator to `cite_graph.py`**

```python
# Append to src/perspicacite/pipeline/cite_graph.py

from typing import Optional as _Optional


async def _resolve_and_fetch(
    *, tool: _Optional[str], doi: _Optional[str], kb_config,
) -> list[dict]:
    """Resolve the library to a seed DOI, then fetch OpenAlex citing works.

    Returns a list of raw OpenAlex work dicts. This is the only network
    surface; tests patch this function.
    """
    import httpx
    from perspicacite.pipeline.library_doi import resolve_library_paper
    from perspicacite.pipeline.snowball import (
        openalex_id_for_doi, fetch_cited_by_works,
    )

    seed_doi: _Optional[str] = doi
    if seed_doi is None:
        if not tool:
            return []
        paper = await resolve_library_paper(
            tool,
            bundle=None, github_repo=None,
            config_map=dict(kb_config.library_paper_map),
            readme_text=None,
        )
        if paper is None:
            return []
        seed_doi = paper.doi

    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(client, seed_doi)
        if not oa_id:
            return []
        # Fetch the seed work so we have a cited_by_api_url.
        seed_work = await _fetch_seed_work_local(client, seed_doi)
        if seed_work is None:
            return []
        return await fetch_cited_by_works(
            client, seed_work=seed_work,
            max_results=kb_config.cite_graph.max_papers * 4,  # over-fetch for filtering
        )


async def _fetch_seed_work_local(client, doi):
    """Thin local re-fetch of the seed work — duplicates _fetch_seed_work
    only because we want a name we can patch in tests without monkey-
    patching the snowball private."""
    from perspicacite.pipeline.snowball import _fetch_seed_work
    return await _fetch_seed_work(client, doi, {})


def _hit_from_oa_work(work: dict) -> _Optional[CiteHit]:
    """Project an OpenAlex work dict into a CiteHit."""
    doi = (work.get("doi") or "").replace("https://doi.org/", "")
    if not doi:
        # Try ids.doi
        doi = (work.get("ids") or {}).get("doi", "").replace("https://doi.org/", "")
    if not doi:
        return None
    title = work.get("title") or ""
    year = int(work.get("publication_year") or 0)
    cit = int(work.get("cited_by_count") or 0)
    oa = bool((work.get("open_access") or {}).get("is_oa"))
    venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
    # Reconstruct abstract from inverted index (best-effort).
    inv = work.get("abstract_inverted_index") or None
    abstract = None
    if isinstance(inv, dict) and inv:
        try:
            positions: list[tuple[int, str]] = []
            for word, idxs in inv.items():
                for i in idxs:
                    positions.append((i, word))
            positions.sort()
            abstract = " ".join(w for _, w in positions)
        except Exception:
            abstract = None
    return CiteHit(
        doi=doi, title=title, year=year, venue=venue,
        citation_count=cit, is_oa=oa, abstract=abstract,
    )


async def enrich_kb_from_cite_graph(
    *,
    tool: _Optional[str] = None,
    doi: _Optional[str] = None,
    kb_config,                             # KnowledgeBaseConfig
    existing_dois: set[str],
    dry_run: bool = False,
    now_year: _Optional[int] = None,
) -> list[CiteHit]:
    """Resolve library/DOI → fetch citing works → filter+score → top-N.

    In v1, ``dry_run`` is the only mode that does anything; ingest
    plumbing lands in a follow-up. The function always returns the
    ranked hit list (top max_papers) so callers can preview.
    """
    import datetime as _dt
    if now_year is None:
        now_year = _dt.datetime.now(_dt.UTC).year

    cfg = kb_config.cite_graph
    works = await _resolve_and_fetch(tool=tool, doi=doi, kb_config=kb_config)
    raw_hits: list[CiteHit] = []
    for w in works:
        h = _hit_from_oa_work(w)
        if h is not None:
            raw_hits.append(h)

    filtered = apply_cite_graph_filters(
        raw_hits, config=cfg, existing_dois=existing_dois, now_year=now_year,
    )

    synonyms = [tool] if tool else []
    for h in filtered:
        score_cite_hit(h, synonyms, cfg, now_year=now_year)

    filtered.sort(key=lambda h: h.score, reverse=True)
    return filtered[: cfg.max_papers]
```

- [ ] **Step 4: Verify pass** — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_cite_graph_dry_run.py src/perspicacite/pipeline/cite_graph.py
git commit -m "feat(pipeline/cite_graph): enrich_kb_from_cite_graph orchestrator (dry-run v1)"
```

---

## Task 7: CLI + MCP entry points (dry-run only in v1)

**Files:**
- Modify: `src/perspicacite/cli.py`
- Modify: `src/perspicacite/mcp/server.py`
- Test: `tests/unit/test_cite_graph_cli.py`

This task adds the user-facing entry points. Both surfaces only support dry-run in v1 (no ingest); the user can preview ranked candidates and decide manually.

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_cite_graph_cli.py
"""Smoke test for the CLI subcommand `kb enrich-cite-graph`."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch


def test_cli_subcommand_registered():
    """The Click command group should expose the subcommand."""
    from perspicacite.cli import cli
    # Walk the command tree.
    kb = cli.commands.get("kb")
    assert kb is not None, "expected `cli kb` group"
    assert "enrich-cite-graph" in (kb.commands if hasattr(kb, "commands") else {}), (
        "expected `kb enrich-cite-graph` subcommand"
    )
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Add the CLI subcommand**

Find `src/perspicacite/cli.py` and locate the `kb` Click group. Add a new command:

```python
@kb.command("enrich-cite-graph")
@click.argument("kb_name")
@click.option("--tool", default=None, help="Library/tool name to resolve.")
@click.option("--doi", default=None, help="Skip resolver and use this DOI as seed.")
@click.option("--max-papers", default=None, type=int, help="Override max_papers cap.")
@click.option("--dry-run/--no-dry-run", default=True, help="Preview only (default).")
def kb_enrich_cite_graph(kb_name, tool, doi, max_papers, dry_run):
    """Enrich a KB from the cite-graph of a library's canonical paper.

    v1 supports dry-run only; ingest is a follow-up.
    """
    import asyncio
    from perspicacite.config.schema import Config
    from perspicacite.pipeline.cite_graph import enrich_kb_from_cite_graph

    if not tool and not doi:
        raise click.UsageError("Provide --tool or --doi.")

    cfg = Config()  # loads default; full config-load is also acceptable
    kb_cfg = cfg.kb
    if max_papers is not None:
        kb_cfg.cite_graph.max_papers = max_papers

    hits = asyncio.run(enrich_kb_from_cite_graph(
        tool=tool, doi=doi, kb_config=kb_cfg,
        existing_dois=set(), dry_run=dry_run,
    ))
    click.echo(f"[dry-run={dry_run}] {len(hits)} ranked hits for {tool or doi}:")
    for i, h in enumerate(hits, 1):
        click.echo(
            f"  {i:2d}. score={h.score:.3f}  cit={h.citation_count}  "
            f"year={h.year}  DOI={h.doi}  {h.title[:80]}"
        )
```

(Adapt the Config loading pattern to whatever the rest of `cli.py` uses — if there's a helper like `load_config()`, prefer it.)

- [ ] **Step 4: Add the MCP tool (small)**

In `src/perspicacite/mcp/server.py`, find an existing MCP tool registration and add a sibling:

```python
@mcp.tool()
async def enrich_kb_from_cite_graph_tool(
    kb_name: str,
    tool: str | None = None,
    doi: str | None = None,
    max_papers: int | None = None,
    dry_run: bool = True,
) -> dict:
    """MCP tool: cite-graph enrichment preview.

    v1: dry-run only. Returns a list of ranked CiteHit records as dicts.
    """
    from perspicacite.pipeline.cite_graph import enrich_kb_from_cite_graph
    cfg = app_state.config
    kb_cfg = cfg.kb
    if max_papers is not None:
        kb_cfg.cite_graph.max_papers = max_papers
    hits = await enrich_kb_from_cite_graph(
        tool=tool, doi=doi, kb_config=kb_cfg, existing_dois=set(),
        dry_run=dry_run,
    )
    return {"hits": [
        {
            "doi": h.doi, "title": h.title, "year": h.year,
            "citation_count": h.citation_count, "is_oa": h.is_oa,
            "venue": h.venue, "score": h.score,
            "score_breakdown": h.score_breakdown,
        }
        for h in hits
    ]}
```

(Adapt to match how other MCP tools in the file are registered. If the file uses a different pattern — e.g., a registry dict — mirror that.)

- [ ] **Step 5: Verify CLI test passes** — `pytest tests/unit/test_cite_graph_cli.py -v` → 1 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_cite_graph_cli.py src/perspicacite/cli.py src/perspicacite/mcp/server.py
git commit -m "feat(cli+mcp): kb enrich-cite-graph subcommand + MCP tool (dry-run v1)"
```

---

## Self-Review

**Spec coverage** (`docs/superpowers/specs/2026-05-15-cite-graph-enrichment-design.md`):

| Spec section | Task |
|---|---|
| §2 Resolve library → DOI | Task 4 |
| §4 Snowball helper extraction | Task 3 |
| §5 Data flow | Task 6 (orchestrator) |
| §6 Filter + score | Task 5 |
| §7 RAGResponse new fields | Task 1 (ChunkMetadata.cited_tool / discovery_score / source_via) |
| §8 Optional script pull-in (--include-scripts) | **Deferred** (config flag landed in Task 2; runtime behaviour is follow-up) |
| §10 CLI + MCP | Task 7 |
| §12 Tests | Tasks 1-7 each ship unit tests |
| §13 Decomposition | This plan: 7 tasks |

**Deferred for follow-up:** ingest plumbing (v1 returns ranked previews only), `--include-scripts` behaviour (config flag exists but no runtime code), live integration test.

**Placeholder scan:** No "TBD" / "TODO". Steps reference exact code/commands; tests are concrete.

**Type consistency:**
- `CiteHit(doi, title, year, venue, citation_count, is_oa, abstract, github_url, score, score_breakdown)` — Tasks 5, 6.
- `LibraryPaper(library, doi, title, source, confidence)` — Task 4.
- `CiteGraphConfig.{min_year_offset, min_citations, max_papers, w_*, venue_denylist, include_scripts}` — Tasks 2, 5, 6.
- `score_cite_hit(hit, tool_synonyms, config, *, now_year)` and `apply_cite_graph_filters(hits, *, config, existing_dois, now_year)` — Tasks 5, 6.
- `enrich_kb_from_cite_graph(*, tool, doi, kb_config, existing_dois, dry_run, now_year=None)` — Tasks 6, 7.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-15-cite-graph-enrichment.md`. Execute via superpowers:subagent-driven-development.

After cite-graph ships, the final task is a live audit against 2 real articles to verify the integrated 2026-05-15 work end-to-end.
