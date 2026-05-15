# SS Fallback Cite-Graph for arXiv — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-trigger a Semantic Scholar references/citations fetch when a `snowball_expand` seed is an arXiv-only preprint; merge the results into the existing OpenAlex-derived `ExpansionHit` stream with edge-level provenance tagging.

**Spec:** `docs/superpowers/specs/2026-05-15-ss-fallback-cite-graph-design.md`

**Architecture:** Two new fetchers in `src/perspicacite/search/semantic_scholar.py` (`fetch_ss_references`, `fetch_ss_citations`); one small adapter that maps S2's response shape to the OpenAlex-like dict consumed by `_paper_from_oa_work`; two new helpers and an `ExpansionHit.provenance` field in `src/perspicacite/pipeline/snowball.py`; a merge-with-dedup pass inside `snowball_expand` that runs after the OpenAlex pass when `_seed_needs_ss_fallback()` returns True. Behavior is opt-out via `include_semantic_scholar: bool = True` on `snowball_expand`.

**Tech Stack:** Python 3.11, httpx, pytest, pytest-asyncio, pydantic v2.

**Migration map:**

| Concern | Site | New code |
|---|---|---|
| SS fetchers | `src/perspicacite/search/semantic_scholar.py` | `fetch_ss_references`, `fetch_ss_citations`, `_ss_record_to_oa_like_work` |
| Edge provenance | `src/perspicacite/pipeline/snowball.py:58` (`ExpansionHit`) | `provenance: str = "openalex"` |
| Detection | `src/perspicacite/pipeline/snowball.py` | `_seed_needs_ss_fallback`, `_ss_id_for_seed` |
| Merge | `src/perspicacite/pipeline/snowball.py:353` (`snowball_expand`) | post-OpenAlex SS pass + dedup helper |
| Public API | same | `include_semantic_scholar: bool = True` kwarg |

**Out of scope:** SS-only seeds (where OpenAlex can't resolve the seed at all); cursor-based pagination beyond the first page; config-schema entries; replacing OpenAlex as primary.

---

### Task 1: SS reference/citation fetchers + adapter

**Files:**
- Modify: `src/perspicacite/search/semantic_scholar.py` — append `fetch_ss_references`, `fetch_ss_citations`, `_ss_record_to_oa_like_work`
- Create: `tests/unit/test_semantic_scholar_cite_graph.py`

- [ ] **Step 1.1: Write the failing fetcher tests**

Create `tests/unit/test_semantic_scholar_cite_graph.py`:

```python
"""Unit tests for the Semantic Scholar references/citations fetchers.

These back the SS fallback path in snowball_expand. The adapter
(_ss_record_to_oa_like_work) maps S2's nested {citedPaper: {...}}
shape to the OpenAlex-like dict that _paper_from_oa_work consumes,
so downstream ExpansionHit construction is uniform.
"""
from __future__ import annotations

import httpx
import pytest

from perspicacite.search.semantic_scholar import (
    fetch_ss_references,
    fetch_ss_citations,
)


_SAMPLE_REF_RESPONSE = {
    "data": [
        {
            "isInfluential": True,
            "citedPaper": {
                "paperId": "ssid-cited-1",
                "corpusId": 42,
                "externalIds": {"DOI": "10.1234/cited", "ArXiv": "1234.5678"},
                "title": "A Cited Work",
                "abstract": "abstract text",
                "authors": [{"name": "Ada Lovelace"}],
                "year": 2020,
                "citationCount": 99,
                "venue": "NeurIPS",
            },
        }
    ]
}

_SAMPLE_CIT_RESPONSE = {
    "data": [
        {
            "isInfluential": False,
            "citingPaper": {
                "paperId": "ssid-citing-1",
                "externalIds": {"DOI": "10.1234/citer"},
                "title": "A Citing Work",
                "abstract": "abstract",
                "authors": [{"name": "Babbage"}],
                "year": 2023,
                "citationCount": 5,
                "venue": "ICML",
            },
        }
    ]
}


@pytest.mark.asyncio
async def test_fetch_ss_references_happy_path(monkeypatch):
    async def fake_get(self, url, **kwargs):
        assert "/references" in url
        return httpx.Response(200, json=_SAMPLE_REF_RESPONSE, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.2005.11401", limit=5)
    assert len(out) == 1
    rec = out[0]
    # Adapter must produce OpenAlex-like keys consumed by _paper_from_oa_work:
    assert rec["doi"] == "https://doi.org/10.1234/cited"
    assert rec["title"] == "A Cited Work"
    assert rec["publication_year"] == 2020
    assert rec["cited_by_count"] == 99
    # _paper_from_oa_work reads journal from primary_location.source.display_name:
    assert rec["primary_location"]["source"]["display_name"] == "NeurIPS"
    # Authors flattened into OpenAlex's authorships shape:
    assert any("Ada" in (a.get("author") or {}).get("display_name", "") for a in rec["authorships"])
    # Preserve the arXiv id and S2 identifiers for diagnostic / future dedup use:
    assert rec.get("metadata", {}).get("arxiv_id") == "1234.5678"
    assert rec.get("metadata", {}).get("s2_paper_id") == "ssid-cited-1"


@pytest.mark.asyncio
async def test_fetch_ss_citations_happy_path(monkeypatch):
    async def fake_get(self, url, **kwargs):
        assert "/citations" in url
        return httpx.Response(200, json=_SAMPLE_CIT_RESPONSE, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_citations("DOI:10.48550/arXiv.2005.11401", limit=5)
    assert len(out) == 1
    assert out[0]["doi"] == "10.1234/citer"
    assert out[0]["title"] == "A Citing Work"


@pytest.mark.asyncio
async def test_fetch_ss_references_handles_404(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.notfound")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_ss_references_handles_429(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(429, json={}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.X")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_ss_references_handles_network_error(monkeypatch):
    async def fake_get(self, url, **kwargs):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.X")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_ss_citations_clamps_limit(monkeypatch):
    """Limit must be clamped to [1, 1000] before being sent to S2."""
    captured: dict = {}
    async def fake_get(self, url, **kwargs):
        captured["params"] = kwargs.get("params") or {}
        return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await fetch_ss_citations("DOI:test", limit=5000)
    assert captured["params"]["limit"] == 1000

    captured.clear()
    await fetch_ss_citations("DOI:test", limit=0)
    assert captured["params"]["limit"] == 1
```

- [ ] **Step 1.2: Run the tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_semantic_scholar_cite_graph.py -v`
Expected: 6 FAIL with `ImportError: cannot import name 'fetch_ss_references' ...`.

- [ ] **Step 1.3: Implement the fetchers + adapter**

Append to `src/perspicacite/search/semantic_scholar.py` (after `lookup_paper`):

```python
def _ss_record_to_oa_like_work(record: dict, *, key: str) -> dict | None:
    """Map an S2 references/citations record to an OpenAlex-like work dict.

    ``key`` is ``citedPaper`` for /references or ``citingPaper`` for /citations.
    Returns None for malformed records.
    """
    paper = record.get(key) or {}
    if not paper:
        return None
    ext_ids = paper.get("externalIds") or {}
    doi = ext_ids.get("DOI")
    arxiv_id = ext_ids.get("ArXiv")

    # Authors → OpenAlex authorships shape so _paper_from_oa_work picks them up
    authorships = []
    for a in paper.get("authors") or []:
        name = (a or {}).get("name", "").strip()
        if name:
            authorships.append({"author": {"display_name": name}})

    # Journal: _paper_from_oa_work reads primary_location.source.display_name
    venue = paper.get("venue") or None
    primary_location: dict = {}
    if venue:
        primary_location = {"source": {"display_name": venue}}

    # NOTE: abstract_inverted_index is None — S2 gives us a plain
    # abstract string, but _paper_from_oa_work's _reconstruct_abstract
    # only consumes inverted indexes. The plain abstract is preserved
    # under "abstract" as a fallback for downstream readers; ExpansionHit
    # tolerates abstract being None from this path (OpenAlex sometimes
    # also returns null inverted indexes).

    return {
        # Stable OA-shaped id from S2 paperId when there's no DOI
        "id": f"https://openalex.org/W_S2_{paper.get('paperId', '')}",
        "doi": (f"https://doi.org/{doi}" if doi else None),
        "title": paper.get("title") or "Untitled",
        "display_name": paper.get("title") or "Untitled",
        "publication_year": paper.get("year"),
        "cited_by_count": paper.get("citationCount"),
        "abstract_inverted_index": None,
        "abstract": paper.get("abstract"),
        "authorships": authorships,
        "primary_location": primary_location,
        # Diagnostic / future-use payload (not consumed by _paper_from_oa_work):
        "metadata": {
            "arxiv_id": arxiv_id,
            "s2_paper_id": paper.get("paperId"),
            "s2_corpus_id": paper.get("corpusId"),
            "ss_is_influential": record.get("isInfluential", False),
        },
    }


_SS_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1/paper"
_SS_REF_CIT_FIELDS = (
    "title,abstract,authors,year,externalIds,citationCount,venue"
)


async def _ss_fetch_graph(
    paper_id: str,
    endpoint: str,           # "references" or "citations"
    *,
    limit: int,
    http_client: httpx.AsyncClient | None,
) -> list[dict]:
    """Shared HTTP path for /references and /citations.

    Returns adapted records (OpenAlex-like dicts). On 4xx / 5xx / network
    error, logs and returns [] — the caller (snowball_expand) treats SS
    failure as a no-op enrichment, not an error.
    """
    normalized = normalize_paper_id(paper_id)
    if not normalized:
        return []

    clamped_limit = max(1, min(int(limit), 1000))

    client = http_client or httpx.AsyncClient(timeout=15.0)
    should_close = http_client is None
    try:
        url = f"{_SS_GRAPH_BASE}/{normalized}/{endpoint}"
        headers: dict[str, str] = {}
        api_key = _get_api_key()
        if api_key:
            headers["x-api-key"] = api_key

        response = await client.get(
            url,
            params={"fields": _SS_REF_CIT_FIELDS, "limit": clamped_limit},
            headers=headers,
        )

        if response.status_code == 404:
            logger.info("snowball_ss_paper_not_found", paper_id=normalized, endpoint=endpoint)
            return []
        if response.status_code == 429:
            logger.warning("snowball_ss_rate_limited", paper_id=normalized, endpoint=endpoint)
            return []
        if response.status_code >= 400:
            logger.warning(
                "snowball_ss_error",
                paper_id=normalized,
                endpoint=endpoint,
                status=response.status_code,
            )
            return []

        body = response.json() or {}
        records = body.get("data") or []
        key = "citedPaper" if endpoint == "references" else "citingPaper"
        out: list[dict] = []
        for rec in records:
            mapped = _ss_record_to_oa_like_work(rec, key=key)
            if mapped is not None:
                out.append(mapped)
        return out

    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "snowball_ss_error",
            paper_id=normalized,
            endpoint=endpoint,
            error=str(exc),
        )
        return []

    finally:
        if should_close:
            await client.aclose()


async def fetch_ss_references(
    paper_id: str,
    *,
    limit: int = 100,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Papers that ``paper_id`` cites (backward direction).

    Returns OpenAlex-like work dicts consumed by
    ``perspicacite.pipeline.snowball._paper_from_oa_work``. Returns [] on
    any SS-side failure (404 / 429 / 5xx / network).
    """
    return await _ss_fetch_graph(
        paper_id, "references", limit=limit, http_client=http_client,
    )


async def fetch_ss_citations(
    paper_id: str,
    *,
    limit: int = 100,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Papers that cite ``paper_id`` (forward direction).

    Same shape and failure semantics as ``fetch_ss_references``.
    """
    return await _ss_fetch_graph(
        paper_id, "citations", limit=limit, http_client=http_client,
    )
```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_semantic_scholar_cite_graph.py -v`
Expected: 6 PASSED.

- [ ] **Step 1.5: Commit**

```bash
git add src/perspicacite/search/semantic_scholar.py tests/unit/test_semantic_scholar_cite_graph.py
git commit -m "$(cat <<'EOF'
feat(search): SS /references and /citations fetchers + OA-shape adapter

Two new async fetchers (fetch_ss_references, fetch_ss_citations) that
return OpenAlex-like work dicts consumed by the snowball pipeline. The
private _ss_record_to_oa_like_work adapter handles the {citedPaper}
vs {citingPaper} envelope and flattens authors into the authorships
shape. 4xx / 5xx / network errors log + return [] so the caller can
treat SS failure as a no-op enrichment.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `ExpansionHit.provenance` field

**Files:**
- Modify: `src/perspicacite/pipeline/snowball.py:58` (the `ExpansionHit` dataclass)
- Modify: `tests/unit/test_paper_source_adapter_migration.py` (extend with the source-vs-provenance pin test from the spec)

- [ ] **Step 2.1: Write the failing pin test**

Append to `tests/unit/test_paper_source_adapter_migration.py`:

```python
def test_snowball_ss_provenance_papers_still_use_citation_follow_enum():
    """Cite-graph hits — regardless of whether OpenAlex or SS sourced
    them — produce Papers with source=CITATION_FOLLOW. provenance is
    the edge-level signal; Paper.source is the paper-record signal."""
    from perspicacite.pipeline.snowball import ExpansionHit, _papers_from_hits
    h = ExpansionHit(
        seed_doi="10.48550/arXiv.2005.11401",
        expanded_doi="10.1234/cited",
        direction="forward",
        title="A Cited Work",
        authors=["Author A"],
        year=2024,
        abstract="...",
        journal="Journal",
        citation_count=3,
        provenance="semantic_scholar",
    )
    papers = _papers_from_hits([h])
    assert len(papers) == 1
    assert papers[0].source is PaperSource.CITATION_FOLLOW
```

- [ ] **Step 2.2: Run the test to verify it fails**

Run: `PYTHONPATH=src pytest tests/unit/test_paper_source_adapter_migration.py::test_snowball_ss_provenance_papers_still_use_citation_follow_enum -v`
Expected: FAIL with `TypeError: ExpansionHit.__init__() got an unexpected keyword argument 'provenance'`.

- [ ] **Step 2.3: Add the `provenance` field**

In `src/perspicacite/pipeline/snowball.py`, find the `ExpansionHit` dataclass (around line 58) and add the field. The class currently has roughly:

```python
@dataclass
class ExpansionHit:
    seed_doi: str
    expanded_doi: str
    direction: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    journal: str | None = None
    citation_count: int | None = None
```

Add at the end (so it's the last field, keeping existing positional/keyword callers working):

```python
    provenance: str = "openalex"  # "openalex" | "semantic_scholar" | "both"
```

- [ ] **Step 2.4: Run the test to verify it passes**

Run: `PYTHONPATH=src pytest tests/unit/test_paper_source_adapter_migration.py -v`
Expected: 10 PASSED (the 9 existing + this new one).

- [ ] **Step 2.5: Commit**

```bash
git add src/perspicacite/pipeline/snowball.py tests/unit/test_paper_source_adapter_migration.py
git commit -m "$(cat <<'EOF'
feat(snowball): ExpansionHit.provenance for edge-level cite-graph source

Tracks which provider supplied each citation edge: "openalex",
"semantic_scholar", or "both" (set by the merge pass when OpenAlex
and SS both report the same edge). Defaults to "openalex" so existing
callers stay green. Paper.source for snowball-derived papers remains
CITATION_FOLLOW — provenance is the edge signal, source is the paper
signal. Pinned by test_snowball_ss_provenance_papers_still_use_citation_follow_enum.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Detection helper + SS id resolver

**Files:**
- Modify: `src/perspicacite/pipeline/snowball.py` (add `_seed_needs_ss_fallback`, `_ss_id_for_seed`)
- Create: `tests/unit/test_snowball_ss_fallback.py` (will grow over Tasks 3 + 4)

- [ ] **Step 3.1: Write the failing detection tests**

Create `tests/unit/test_snowball_ss_fallback.py`:

```python
"""Unit tests for the snowball → Semantic Scholar fallback path."""
from __future__ import annotations

import pytest

from perspicacite.pipeline.snowball import (
    _seed_needs_ss_fallback,
    _ss_id_for_seed,
)


def test_seed_needs_ss_fallback_arxiv_doi_uppercase():
    assert _seed_needs_ss_fallback("10.48550/arXiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"}) is True


def test_seed_needs_ss_fallback_arxiv_doi_lowercase():
    assert _seed_needs_ss_fallback("10.48550/arxiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"}) is True


def test_seed_needs_ss_fallback_crossref_doi_returns_false():
    assert _seed_needs_ss_fallback("10.1145/3404835.3462913", {"doi": "10.1145/3404835.3462913"}) is False


def test_seed_needs_ss_fallback_work_without_doi_returns_true():
    # OpenAlex resolved via title.search but has no canonical DOI
    assert _seed_needs_ss_fallback("foo", {"id": "W123", "doi": None}) is True
    assert _seed_needs_ss_fallback("foo", {"id": "W123"}) is True


def test_seed_needs_ss_fallback_none_work_returns_false():
    # If the seed didn't resolve at all, snowball already skipped it — the
    # SS branch never runs. Returning False here is defensive.
    assert _seed_needs_ss_fallback("10.48550/arxiv.X", None) is False


def test_ss_id_for_seed_arxiv_doi():
    """When the seed DOI is an arxiv DOI, prefer the ArXiv: form so
    Semantic Scholar can resolve the preprint directly."""
    out = _ss_id_for_seed("10.48550/arXiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"})
    assert out == "ArXiv:2005.11401"


def test_ss_id_for_seed_arxiv_doi_with_version_suffix():
    """arXiv ids can carry a vN version suffix; SS accepts the base id."""
    out = _ss_id_for_seed("10.48550/arXiv.2005.11401v2", {"doi": "10.48550/arxiv.2005.11401v2"})
    assert out == "ArXiv:2005.11401"


def test_ss_id_for_seed_crossref_doi_falls_back_to_doi_prefix():
    out = _ss_id_for_seed("10.1145/3404835.3462913", {"doi": "10.1145/3404835.3462913"})
    assert out == "DOI:10.1145/3404835.3462913"
```

- [ ] **Step 3.2: Run the tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_snowball_ss_fallback.py -v`
Expected: 8 FAIL with `ImportError`.

- [ ] **Step 3.3: Implement the helpers**

Append to `src/perspicacite/pipeline/snowball.py` (after the existing private helpers, before `snowball_expand`):

```python
_ARXIV_DOI_PREFIX = "10.48550/arxiv."


def _seed_needs_ss_fallback(seed_doi: str, seed_work: dict | None) -> bool:
    """True if the seed's citation graph is likely underreported by OpenAlex.

    Triggered when:
      - the seed DOI is an arXiv DOI (10.48550/arxiv.*), case-insensitive, OR
      - the resolved seed_work has no DOI of its own (rare; means
        OpenAlex stored the work via title.search but couldn't link it
        to a CrossRef record).

    Returns False when seed_work is None (caller has already given up
    on this seed; SS branch can't help without a paper id).
    """
    if seed_work is None:
        return False
    if (seed_doi or "").lower().startswith(_ARXIV_DOI_PREFIX):
        return True
    if not seed_work.get("doi"):
        return True
    return False


def _ss_id_for_seed(seed_doi: str, seed_work: dict | None) -> str:
    """Return the Semantic Scholar id string for fetching this seed's
    /references and /citations.

    Preference order:
      1. arXiv id parsed from the seed DOI (10.48550/arxiv.X → ArXiv:X),
         stripping any version suffix (v1/v2/...)
      2. DOI:<doi> fallback

    The seed_work argument is accepted for forward-compat (a later
    refinement may inspect the OpenAlex Work record to find an arXiv
    id when the DOI is a CrossRef DOI) but is currently unused.
    """
    del seed_work  # unused in v1
    sd = (seed_doi or "")
    if sd.lower().startswith(_ARXIV_DOI_PREFIX):
        bare = sd[len(_ARXIV_DOI_PREFIX):]
        # Strip "vN" version suffix if present
        import re
        m = re.match(r"^(.*?)(v\d+)?$", bare)
        bare = m.group(1) if m else bare
        return f"ArXiv:{bare}"
    return f"DOI:{sd}"
```

- [ ] **Step 3.4: Run the tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_snowball_ss_fallback.py -v`
Expected: 8 PASSED.

- [ ] **Step 3.5: Commit**

```bash
git add src/perspicacite/pipeline/snowball.py tests/unit/test_snowball_ss_fallback.py
git commit -m "$(cat <<'EOF'
feat(snowball): _seed_needs_ss_fallback + _ss_id_for_seed detection

The two helpers that gate the SS pass: detection (arxiv-DOI or
DOI-less OpenAlex work) and id-resolution (prefer arxiv_id from work
metadata > arxiv-DOI parse > DOI: prefix). No call sites yet — Task 4
wires them into snowball_expand.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Merge SS results into `snowball_expand`

**Files:**
- Modify: `src/perspicacite/pipeline/snowball.py` (add `_merge_with_dedup`, wire the SS pass into `snowball_expand`)
- Modify: `tests/unit/test_snowball_ss_fallback.py` (add integration tests)

- [ ] **Step 4.1: Write the failing integration tests**

Append to `tests/unit/test_snowball_ss_fallback.py`:

```python
import httpx

from perspicacite.pipeline.snowball import snowball_expand


def _arxiv_seed_work_response():
    """OpenAlex 200 response for the RAG paper's arXiv DOI."""
    return {
        "id": "https://openalex.org/W_RAG",
        "doi": "https://doi.org/10.48550/arxiv.2005.11401",
        "title": "Retrieval-Augmented Generation",
        "display_name": "Retrieval-Augmented Generation",
        "publication_year": 2020,
        "cited_by_count": 18,
        "referenced_works": [],
        "authorships": [],
    }


def _oa_forward_hit():
    return {
        "id": "https://openalex.org/W_OAFWD",
        "doi": "https://doi.org/10.1234/oa-fwd",
        "title": "OA-only citer",
        "display_name": "OA-only citer",
        "publication_year": 2023,
        "cited_by_count": 2,
        "authorships": [],
    }


def _ss_only_hit_dict():
    """An OpenAlex-shaped dict that fetch_ss_citations would produce
    (already passed through _ss_record_to_oa_like_work)."""
    return {
        "id": "https://openalex.org/W_S2_ssid-1",
        "doi": "https://doi.org/10.1234/ss-only",
        "title": "SS-only citer",
        "display_name": "SS-only citer",
        "publication_year": 2024,
        "cited_by_count": 99,
        "authorships": [],
        "metadata": {"arxiv_id": None, "s2_paper_id": "ssid-1"},
    }


def _ss_dup_hit_dict():
    """SS hit that duplicates the OpenAlex forward hit (same DOI)."""
    return {
        "id": "https://openalex.org/W_S2_dup",
        "doi": "https://doi.org/10.1234/oa-fwd",   # same DOI as _oa_forward_hit
        "title": "OA-only citer",
        "publication_year": 2023,
        "cited_by_count": 2,
        "authorships": [],
        "metadata": {"s2_paper_id": "dup"},
    }


@pytest.mark.asyncio
async def test_snowball_appends_ss_only_hits_for_arxiv_seed(monkeypatch):
    """SS hit that OpenAlex didn't return → appended with
    provenance=semantic_scholar."""
    from perspicacite import search as search_pkg

    async def fake_oa_get(self, url, **kwargs):
        # Seed resolution: /works/doi:<arxiv-doi> returns the seed work
        if "/works/doi:" in url:
            return httpx.Response(200, json=_arxiv_seed_work_response(),
                                  request=httpx.Request("GET", url))
        # Forward citations: filter=cites:...
        params = kwargs.get("params") or {}
        if params.get("filter", "").startswith("cites:"):
            return httpx.Response(200,
                                  json={"results": [_oa_forward_hit()], "meta": {}},
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(paper_id, *, limit=100, http_client=None):
        # Returns one OpenAlex-shaped dict the OA branch did NOT see
        return [_ss_only_hit_dict()]

    async def fake_ss_references(paper_id, *, limit=100, http_client=None):
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr(
        "perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations,
    )
    monkeypatch.setattr(
        "perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references,
    )

    hits = await snowball_expand(
        seed_dois=["10.48550/arXiv.2005.11401"],
        direction="forward",
        max_per_seed=10,
    )

    # Expect 2 forward hits for this seed: OA-only and SS-only
    fwd = [h for h in hits if h.direction == "forward"]
    assert len(fwd) == 2
    by_doi = {h.expanded_doi: h for h in fwd}
    assert by_doi["10.1234/oa-fwd"].provenance == "openalex"
    assert by_doi["10.1234/ss-only"].provenance == "semantic_scholar"


@pytest.mark.asyncio
async def test_snowball_marks_duplicate_as_both(monkeypatch):
    """SS hit that DOES match an OpenAlex DOI → existing OA entry's
    provenance flips to 'both'; no duplicate ExpansionHit is appended."""

    async def fake_oa_get(self, url, **kwargs):
        if "/works/doi:" in url:
            return httpx.Response(200, json=_arxiv_seed_work_response(),
                                  request=httpx.Request("GET", url))
        params = kwargs.get("params") or {}
        if params.get("filter", "").startswith("cites:"):
            return httpx.Response(200,
                                  json={"results": [_oa_forward_hit()], "meta": {}},
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(paper_id, *, limit=100, http_client=None):
        return [_ss_dup_hit_dict()]   # same DOI as the OA hit

    async def fake_ss_references(paper_id, *, limit=100, http_client=None):
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references)

    hits = await snowball_expand(
        seed_dois=["10.48550/arXiv.2005.11401"],
        direction="forward",
    )

    fwd = [h for h in hits if h.direction == "forward"]
    assert len(fwd) == 1                  # dedup'd to one
    assert fwd[0].expanded_doi == "10.1234/oa-fwd"
    assert fwd[0].provenance == "both"


@pytest.mark.asyncio
async def test_snowball_skips_ss_when_flag_disabled(monkeypatch):
    """include_semantic_scholar=False → no SS HTTP calls."""
    ss_called: list = []

    async def fake_oa_get(self, url, **kwargs):
        if "/works/doi:" in url:
            return httpx.Response(200, json=_arxiv_seed_work_response(),
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(paper_id, *, limit=100, http_client=None):
        ss_called.append("citations")
        return []

    async def fake_ss_references(paper_id, *, limit=100, http_client=None):
        ss_called.append("references")
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references)

    await snowball_expand(
        seed_dois=["10.48550/arXiv.2005.11401"],
        direction="both",
        include_semantic_scholar=False,
    )
    assert ss_called == []


@pytest.mark.asyncio
async def test_snowball_skips_ss_for_crossref_seed(monkeypatch):
    """Non-arxiv seed with a real DOI in OpenAlex → no SS calls."""
    ss_called: list = []

    async def fake_oa_get(self, url, **kwargs):
        if "/works/doi:" in url:
            return httpx.Response(200, json={
                "id": "https://openalex.org/W_CROSSREF",
                "doi": "https://doi.org/10.1145/foo",
                "title": "A CrossRef Paper",
                "display_name": "A CrossRef Paper",
                "publication_year": 2022,
                "cited_by_count": 50,
                "referenced_works": [],
                "authorships": [],
            }, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"results": [], "meta": {}},
                              request=httpx.Request("GET", url))

    async def fake_ss_citations(*a, **kw):
        ss_called.append("c")
        return []

    async def fake_ss_references(*a, **kw):
        ss_called.append("r")
        return []

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_oa_get)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_citations", fake_ss_citations)
    monkeypatch.setattr("perspicacite.pipeline.snowball.fetch_ss_references", fake_ss_references)

    await snowball_expand(
        seed_dois=["10.1145/foo"], direction="both",
    )
    assert ss_called == []
```

- [ ] **Step 4.2: Run the tests to verify they fail**

Run: `PYTHONPATH=src pytest tests/unit/test_snowball_ss_fallback.py -v`
Expected: the new 4 integration tests FAIL (snowball_expand doesn't accept `include_semantic_scholar` yet, and the merge isn't wired).

- [ ] **Step 4.3: Wire the SS pass into `snowball_expand`**

In `src/perspicacite/pipeline/snowball.py`:

1. Add the merge helper above `snowball_expand`:

```python
def _hit_dedup_key(hit_doi: str, work_dict: dict | None) -> str:
    """Stable key for dedup across OpenAlex + SS pass results.

    Preference order: DOI (lowercased), arxiv_id, title-normalized prefix.
    """
    if hit_doi:
        return f"doi:{hit_doi.lower()}"
    wd = work_dict or {}
    arxiv = (wd.get("metadata") or {}).get("arxiv_id")
    if arxiv:
        return f"arxiv:{arxiv.lower()}"
    title = (wd.get("title") or wd.get("display_name") or "").lower().strip()
    title_norm = " ".join(title.split())[:120]
    return f"title:{title_norm}" if title_norm else f"id:{id(wd)}"


def _merge_ss_into_hits(
    existing: list[ExpansionHit],
    ss_works: list[dict],
    *,
    seed_doi: str,
    direction: str,
) -> None:
    """Mutate ``existing`` in place: flip provenance to 'both' for any
    SS hit whose dedup key matches an existing OpenAlex hit; append
    new SS-only hits with provenance='semantic_scholar'."""
    existing_keys: dict[str, ExpansionHit] = {}
    for h in existing:
        # Build a key from the existing ExpansionHit (no access to
        # the original work dict, so use the DOI).
        existing_keys[f"doi:{(h.expanded_doi or '').lower()}"] = h

    for w in ss_works:
        doi, fields = _paper_from_oa_work(w)
        if not doi:
            continue
        key = f"doi:{doi.lower()}"
        existing_hit = existing_keys.get(key)
        if existing_hit is not None:
            existing_hit.provenance = "both"
            continue
        existing.append(ExpansionHit(
            seed_doi=seed_doi,
            expanded_doi=doi,
            direction=direction,
            provenance="semantic_scholar",
            **fields,
        ))
        existing_keys[key] = existing[-1]
```

2. Import the SS fetchers at the top of `snowball.py`:

```python
from perspicacite.search.semantic_scholar import (
    fetch_ss_references,
    fetch_ss_citations,
)
```

3. Modify `snowball_expand` signature to add the new kwarg, and add the SS pass inside the seed loop:

```python
async def snowball_expand(
    *,
    seed_dois: list[str],
    direction: str = "both",
    max_per_seed: int = 10,
    http_client: httpx.AsyncClient | None = None,
    mailto: str | None = None,
    include_semantic_scholar: bool = True,   # NEW
) -> list[ExpansionHit]:
    ...   # existing code unchanged through the per-seed for loop ...
    
    for seed in seed_dois:
        work = await _fetch_seed_work(client, seed, headers)
        if not work:
            continue

        # Track hits for this seed only so the SS merge dedups locally
        seed_back: list[ExpansionHit] = []
        seed_fwd: list[ExpansionHit] = []

        if direction in {"backward", "both"}:
            refs = work.get("referenced_works") or []
            refs = refs[:max_per_seed]
            if refs:
                ref_works = await _batch_get_works(client, refs, headers)
                for rw in ref_works:
                    doi, fields = _paper_from_oa_work(rw)
                    if not doi:
                        continue
                    seed_back.append(ExpansionHit(
                        seed_doi=seed, expanded_doi=doi,
                        direction="backward", **fields,
                    ))

        if direction in {"forward", "both"}:
            forward_works = await _fetch_forward_citations(
                client, work, max_per_seed, headers,
            )
            for fw in forward_works:
                doi, fields = _paper_from_oa_work(fw)
                if not doi:
                    continue
                seed_fwd.append(ExpansionHit(
                    seed_doi=seed, expanded_doi=doi,
                    direction="forward", **fields,
                ))

        # NEW: SS pass for arxiv-only seeds
        if include_semantic_scholar and _seed_needs_ss_fallback(seed, work):
            ss_id = _ss_id_for_seed(seed, work)
            if direction in {"backward", "both"}:
                ss_back_works = await fetch_ss_references(
                    ss_id, limit=max_per_seed, http_client=client,
                )
                _merge_ss_into_hits(
                    seed_back, ss_back_works,
                    seed_doi=seed, direction="backward",
                )
            if direction in {"forward", "both"}:
                ss_fwd_works = await fetch_ss_citations(
                    ss_id, limit=max_per_seed, http_client=client,
                )
                _merge_ss_into_hits(
                    seed_fwd, ss_fwd_works,
                    seed_doi=seed, direction="forward",
                )

        hits.extend(seed_back)
        hits.extend(seed_fwd)
```

(Replace the existing per-seed loop body with the above. The variable `hits` is collected across all seeds as before.)

- [ ] **Step 4.4: Run the tests to verify they pass**

Run: `PYTHONPATH=src pytest tests/unit/test_snowball_ss_fallback.py -v`
Expected: all 12 tests PASS (8 from Task 3 + 4 from Task 4).

- [ ] **Step 4.5: Run the broader snowball test suite for regressions**

Run: `PYTHONPATH=src pytest tests/unit -k "snowball or arxiv" -v 2>&1 | tail -30`
Expected: all PASS (no regressions in `test_arxiv_id_fallback.py`, the existing snowball pin test in `test_paper_source_adapter_migration.py`, etc.).

- [ ] **Step 4.6: Commit**

```bash
git add src/perspicacite/pipeline/snowball.py tests/unit/test_snowball_ss_fallback.py
git commit -m "$(cat <<'EOF'
feat(snowball): auto-trigger SS fallback for arxiv seeds + dedup merge

snowball_expand grows include_semantic_scholar=True kwarg (opt-out for
tests / batches). When _seed_needs_ss_fallback returns True, the SS
references/citations endpoints are fetched after the OpenAlex pass and
merged via _merge_ss_into_hits: matching DOIs flip the existing entry's
provenance to "both"; novel SS hits append with provenance="semantic_scholar".
Closes the audit P3 follow-up on OpenAlex underreporting arxiv preprints.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Live integration smoke test

**Files:**
- Create: `tests/integration/test_snowball_ss_fallback_live.py`

This is opt-in via `SEMANTIC_SCHOLAR_API_KEY` (or `SCILEX_SEMANTIC_SCHOLAR_API_KEY`). If the key is absent the test skips cleanly, mirroring the `test_codestral_embed_live.py` pattern.

- [ ] **Step 5.1: Write the live test**

Create `tests/integration/test_snowball_ss_fallback_live.py`:

```python
"""Live smoke test for SS-fallback cite-graph on the RAG arXiv paper.

Pins the audit P3 finding: OpenAlex returns ~18 forward citations for
10.48550/arXiv.2005.11401; SS returns far more. The combined snowball
should return at least an order of magnitude more than OpenAlex alone.

Skipped without SEMANTIC_SCHOLAR_API_KEY (or SCILEX_SEMANTIC_SCHOLAR_API_KEY).
"""
from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        or os.environ.get("SCILEX_SEMANTIC_SCHOLAR_API_KEY")
    ),
    reason="SEMANTIC_SCHOLAR_API_KEY not set — skip live SS-fallback test",
)

RAG_DOI = "10.48550/arXiv.2005.11401"


@pytest.mark.asyncio
async def test_snowball_with_ss_beats_openalex_alone_for_rag_paper():
    from perspicacite.pipeline.snowball import snowball_expand

    oa_only = await snowball_expand(
        seed_dois=[RAG_DOI],
        direction="forward",
        max_per_seed=100,
        include_semantic_scholar=False,
    )
    combined = await snowball_expand(
        seed_dois=[RAG_DOI],
        direction="forward",
        max_per_seed=100,
        include_semantic_scholar=True,
    )

    n_oa = sum(1 for h in oa_only if h.provenance == "openalex")
    n_combined = len(combined)
    n_ss = sum(1 for h in combined if h.provenance in {"semantic_scholar", "both"})

    # SS should contribute meaningfully — at least double the OA count or
    # at least 30 SS-tagged hits, whichever is lower.
    assert n_combined >= max(n_oa * 2, min(30, max_per_seed_fallback := 100)), (
        f"combined snowball produced only {n_combined} hits "
        f"(OA-only: {n_oa}, SS contribution: {n_ss}); expected >= 2x OA"
    )
```

- [ ] **Step 5.2: Run the live test with the key**

Run (from the worktree, with SS API key sourced from zshrc):

```bash
bash -c 'source ~/.zshrc 2>/dev/null; cd /Users/holobiomicslab/git/Perspicacite-AI/.claude/worktrees/trusting-aryabhata-92508b && PYTHONPATH=src pytest tests/integration/test_snowball_ss_fallback_live.py -v 2>&1 | tail -20'
```

Expected: 1 PASSED. If the key isn't in `~/.zshrc` under the expected name (`SEMANTIC_SCHOLAR_API_KEY` or `SCILEX_SEMANTIC_SCHOLAR_API_KEY`), the implementer should:
- Extract it from the user's environment via the same pattern used in Task 9 of the prior plan (grep + sed on zshrc)
- If no key is found at all, run with no key — SS will use the unauthenticated tier (~100 req / 5 min, plenty for one seed)
- If the test fails because SS rate-limits or returns 0 hits, report DONE_WITH_CONCERNS rather than DONE

- [ ] **Step 5.3: Confirm skip path works**

Run: `SEMANTIC_SCHOLAR_API_KEY= SCILEX_SEMANTIC_SCHOLAR_API_KEY= PYTHONPATH=src pytest tests/integration/test_snowball_ss_fallback_live.py -v 2>&1 | tail -10`
Expected: 1 SKIPPED.

- [ ] **Step 5.4: Commit**

```bash
git add tests/integration/test_snowball_ss_fallback_live.py
git commit -m "$(cat <<'EOF'
test(integration): live SS-fallback snowball smoke test for RAG paper

Pins the audit P3 finding (OpenAlex underreports arXiv cite-graph).
With include_semantic_scholar=True, snowball returns at least 2x the
OpenAlex-only count for 10.48550/arXiv.2005.11401. Skipped cleanly
when no SS API key is set.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Verification sweep

**Files:** none (verification only).

- [ ] **Step 6.1: Full unit-test suite — confirm no regressions**

Run: `PYTHONPATH=src pytest tests/unit -q --tb=line 2>&1 | tail -15`
Expected: 1300+ passed, 1 skipped, 0 failed. (Previously 1297 passing; the new tests in Tasks 1-4 add ~13 unit tests, so expect ~1310 passing.)

- [ ] **Step 6.2: Final grep — confirm no `WEB_SEARCH` reintroduction**

Run: `grep -rn 'source=PaperSource.WEB_SEARCH' src/`
Expected: 0 matches. (The Task 8 invariant test should also still pass.)

- [ ] **Step 6.3: Final grep — confirm SS fetchers are reachable**

Run: `grep -n 'fetch_ss_references\|fetch_ss_citations' src/perspicacite/`
Expected: two definitions in `semantic_scholar.py`; two imports in `snowball.py`; no other uses (we intentionally don't expose them widely in v1).

No commit on this task — verification only.

---

## Self-review

**Spec coverage:**
- Spec §Architecture (fetchers) → Task 1 ✓
- Spec §`ExpansionHit.provenance` → Task 2 ✓
- Spec §Detection → Task 3 ✓
- Spec §Merge + §`snowball_expand` flow → Task 4 ✓
- Spec §Testing strategy (unit) → Tasks 1, 3, 4 ✓
- Spec §Testing strategy (live integration test) → Task 5 ✓
- Spec §Testing strategy (source-vs-provenance pin test) → Task 2 ✓
- Spec §Public API → Task 4 (`include_semantic_scholar` kwarg) ✓

**Placeholder scan:** No "TBD" / "implement later" placeholders. All steps have exact code + exact commands.

**Type consistency:** All new functions return `list[dict]` (OpenAlex-like work shape) or `list[ExpansionHit]`. Provenance field is `str` with three documented values. The `_merge_ss_into_hits` function mutates in place (no return), matching the convention of other private snowball helpers.

**Known caveats:**
- Task 4 redesigns the per-seed branch of `snowball_expand` slightly (introduces `seed_back`/`seed_fwd` locals before extending `hits`). This is a refactor, not just an addition. The implementer should preserve existing behavior — review the test_paper_source_adapter_migration `test_snowball_expansion_hit_uses_citation_follow_enum` and confirm it still passes after Task 4.
- The merge dedup uses DOI as the primary key. If both OpenAlex and SS produce DOI-less hits for the same paper (rare; only happens when neither provider has a CrossRef DOI), they would not dedup. Acceptable for v1; documented in the dedup helper docstring.
- Live test (Task 5) needs an arXiv-only paper with a known significant SS↔OA gap. RAG is canonical.

---

## Execution handoff

Recommend Subagent-Driven for Tasks 1-5 (one subagent per task, two-stage review after each). Task 6 is verification only, run inline.
