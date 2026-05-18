# DBLP SPARQL Provider + `--ingest-mode` CLI Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a citation-ranked DBLP SPARQL search provider (enriched with SemOpenAlex abstracts), a `--ingest-mode` CLI flag on `create-kb` / `add-to-kb`, and fix two stale paragraphs in CLAUDE.md.

**Architecture:** `DBLPSPARQLSearchProvider` issues two sequential async SPARQL requests — phase 1 to DBLP's QLever endpoint for citation-ranked title-matching papers, phase 2 to SemOpenAlex GraphDB for batch abstract enrichment. It is registered in `build_aggregator` under key `"dblp_sparql"`. The `--ingest-mode` flag mutates `config.knowledge_base.ingest_mode` before the pipeline call.

**Tech Stack:** Python 3.12+, `httpx` (async HTTP, already a dependency), `click` (CLI, already a dependency), `pytest` + `unittest.mock` for tests.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/perspicacite/models/papers.py` | Modify | Add `DBLP_SPARQL = "dblp_sparql"` to `PaperSource` enum |
| `src/perspicacite/search/dblp_sparql_search.py` | Create | All DBLP+SemOpenAlex logic |
| `src/perspicacite/search/__init__.py` | Modify | Export `DBLPSPARQLSearchProvider` |
| `src/perspicacite/search/domain_aggregator.py` | Modify | Wire `dblp_sparql` into `build_aggregator` |
| `config.example.yml` | Modify | Add commented `# - dblp_sparql` line |
| `src/perspicacite/cli.py` | Modify | `--ingest-mode` option on `create-kb` and `add-to-kb` |
| `CLAUDE.md` | Modify | Correct stale recency_weight / multi-KB paragraphs |
| `tests/unit/test_dblp_sparql_search.py` | Create | All DBLP+SemOpenAlex unit tests |
| `tests/unit/test_dblp_sparql_aggregator.py` | Create | Aggregator wiring tests |
| `tests/unit/test_cli_ingest_mode.py` | Create | CLI flag tests |

---

## Task 1: Fix stale CLAUDE.md paragraphs

**Files:**
- Modify: `CLAUDE.md` (lines 78 and 80)

No TDD needed — this is a documentation correction, not code.

- [ ] **Step 1: Edit the recency_weight paragraph (line 78)**

Find this exact text in `CLAUDE.md`:

```
Currently wired into `basic` and `contradiction` modes only. `advanced` and `profound` (WRRF/two-pass paper-dict flow) and the `agentic` orchestrator (raw-query interface) do **not** yet honor it — wiring those is a known follow-up task.
```

Replace with:

```
Wired into all six RAG modes. See `retrieval/recency.py` → `apply_recency_weighting()`.
```

- [ ] **Step 2: Edit the multi-KB paragraph (line 80)**

Find this exact text in `CLAUDE.md`:

```
Currently wired into `basic` and `contradiction` modes; `advanced`, `profound`, `agentic`, and `literature_survey` are a known follow-up.
```

Replace with:

```
Wired into `basic`, `contradiction`, `advanced`, `profound`, and `agentic` modes. `literature_survey` accepts `kb_names` but fans retrieval across only the first KB for survey storage; full multi-KB retrieval in `literature_survey` is a tracked follow-up.
```

- [ ] **Step 3: Run tests to confirm nothing broke**

```bash
uv run pytest tests/unit/ -q --tb=no
```

Expected: all previously passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: correct stale recency_weight and multi-KB wiring notes in CLAUDE.md"
```

---

## Task 2: Add `PaperSource.DBLP_SPARQL` and DBLP query helpers

**Files:**
- Modify: `src/perspicacite/models/papers.py`
- Create: `src/perspicacite/search/dblp_sparql_search.py` (helpers only, no class yet)
- Create: `tests/unit/test_dblp_sparql_search.py`

- [ ] **Step 1: Write failing tests for query helpers**

Create `tests/unit/test_dblp_sparql_search.py`:

```python
"""Unit tests for DBLP SPARQL + SemOpenAlex search provider helpers."""
from __future__ import annotations


# ── _tokenise_query ───────────────────────────────────────────────────────────

def test_tokenise_removes_stop_words():
    from perspicacite.search.dblp_sparql_search import _tokenise_query
    result = _tokenise_query("the analysis of neural networks")
    assert "the" not in result
    assert "of" not in result
    assert "neural" in result
    assert "networks" in result


def test_tokenise_caps_at_eight():
    from perspicacite.search.dblp_sparql_search import _tokenise_query
    long_query = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    result = _tokenise_query(long_query)
    assert len(result) <= 8


def test_tokenise_fallback_on_all_stop_words():
    from perspicacite.search.dblp_sparql_search import _tokenise_query
    # When all tokens are stop words, fall back to first 3 raw tokens
    result = _tokenise_query("the a an")
    assert len(result) >= 1  # fallback ensures at least something is returned


def test_tokenise_short_tokens_dropped():
    from perspicacite.search.dblp_sparql_search import _tokenise_query
    result = _tokenise_query("AI ML deep learning")
    # "AI" and "ML" are 2 chars, should be dropped; "deep" and "learning" kept
    assert "deep" in result
    assert "learning" in result


# ── _build_dblp_sparql ────────────────────────────────────────────────────────

def test_build_dblp_sparql_contains_keywords():
    from perspicacite.search.dblp_sparql_search import _build_dblp_sparql
    sparql = _build_dblp_sparql(["neural", "network"], max_results=10)
    assert "CONTAINS(?lowerTitle, 'neural')" in sparql
    assert "CONTAINS(?lowerTitle, 'network')" in sparql


def test_build_dblp_sparql_year_filter_both():
    from perspicacite.search.dblp_sparql_search import _build_dblp_sparql
    sparql = _build_dblp_sparql(["graph"], max_results=5, year_min=2018, year_max=2023)
    assert "FILTER(?year >= 2018 && ?year <= 2023)" in sparql


def test_build_dblp_sparql_year_filter_min_only():
    from perspicacite.search.dblp_sparql_search import _build_dblp_sparql
    sparql = _build_dblp_sparql(["graph"], max_results=5, year_min=2020)
    assert "FILTER(?year >= 2020" in sparql


def test_build_dblp_sparql_no_year_filter_when_none():
    from perspicacite.search.dblp_sparql_search import _build_dblp_sparql
    sparql = _build_dblp_sparql(["graph"], max_results=5)
    assert "FILTER(?year" not in sparql


def test_build_dblp_sparql_limit():
    from perspicacite.search.dblp_sparql_search import _build_dblp_sparql
    sparql = _build_dblp_sparql(["test"], max_results=15)
    assert "LIMIT 15" in sparql


# ── _clean_literal + _parse_dblp_response ────────────────────────────────────

def test_clean_literal_quoted_string():
    from perspicacite.search.dblp_sparql_search import _clean_literal
    assert _clean_literal('"hello world"') == "hello world"


def test_clean_literal_typed_literal():
    from perspicacite.search.dblp_sparql_search import _clean_literal
    assert _clean_literal('"2021"^^xsd:integer') == "2021"


def test_clean_literal_iri():
    from perspicacite.search.dblp_sparql_search import _clean_literal
    assert _clean_literal("<https://example.org/foo>") == "https://example.org/foo"


def test_clean_literal_plain_string():
    from perspicacite.search.dblp_sparql_search import _clean_literal
    assert _clean_literal("plain") == "plain"


def test_parse_dblp_response_basic():
    from perspicacite.search.dblp_sparql_search import _parse_dblp_response
    data = {
        "res": [
            ['"Attention Is All You Need"', '"10.1234/abc"', '"2017"', '"5000"', '"2"'],
        ]
    }
    results = _parse_dblp_response(data)
    assert len(results) == 1
    assert results[0]["title"] == "Attention Is All You Need"
    assert results[0]["doi"] == "10.1234/abc"
    assert results[0]["year"] == 2017
    assert results[0]["cites"] == 5000


def test_parse_dblp_response_strips_doi_uri():
    from perspicacite.search.dblp_sparql_search import _parse_dblp_response
    data = {
        "res": [
            ['"A Paper"', '"https://doi.org/10.5678/xyz"', '"2020"', '"10"', '"1"'],
        ]
    }
    results = _parse_dblp_response(data)
    assert results[0]["doi"] == "10.5678/xyz"


def test_parse_dblp_response_skips_malformed_rows():
    from perspicacite.search.dblp_sparql_search import _parse_dblp_response
    data = {"res": [["only_two_cols", "x"], ['"Good"', '"10.1/a"', '"2020"', '"1"', '"1"']]}
    results = _parse_dblp_response(data)
    assert len(results) == 1
    assert results[0]["doi"] == "10.1/a"


def test_parse_dblp_response_empty():
    from perspicacite.search.dblp_sparql_search import _parse_dblp_response
    assert _parse_dblp_response({"res": []}) == []
    assert _parse_dblp_response({}) == []
```

- [ ] **Step 2: Run to confirm all FAIL**

```bash
uv run pytest tests/unit/test_dblp_sparql_search.py -q 2>&1 | head -20
```

Expected: `ImportError` or `ModuleNotFoundError` — file doesn't exist yet.

- [ ] **Step 3: Add `DBLP_SPARQL` to `PaperSource` enum**

In `src/perspicacite/models/papers.py`, after the `GOOGLE_SCHOLAR = "google_scholar"` line, add:

```python
    DBLP_SPARQL = "dblp_sparql"
```

- [ ] **Step 4: Create `dblp_sparql_search.py` with helpers**

Create `src/perspicacite/search/dblp_sparql_search.py`:

```python
"""DBLP SPARQL + SemOpenAlex search provider.

Phase 1: POST to DBLP QLever SPARQL endpoint — citation-ranked title search.
Phase 2: POST to SemOpenAlex GraphDB SPARQL endpoint — batch abstract enrichment.
"""
from __future__ import annotations

import re
from typing import Any, ClassVar

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper, PaperSource

logger = get_logger("perspicacite.search.dblp_sparql")

_DBLP_SPARQL_URL = "https://sparql.dblp.org/sparql"
_SEMOA_SPARQL_URL = "https://semopenalex.org/sparql"

# SemOpenAlex SPARQL predicates. If queries return empty results, run the
# schema discovery query in Task 3 Step 1 and update these constants.
_SEMOA_DOI_PRED = "schema:identifier"    # DOI as IRI <https://doi.org/10.xxx>
_SEMOA_ABS_PRED = "dcterms:abstract"     # plain-text abstract literal

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "in", "on", "and", "or", "for",
    "to", "with", "is", "are", "that", "this", "it", "its",
    "at", "by", "from", "as", "be", "was", "were", "has",
})

_MAX_KEYWORDS = 8


def _tokenise_query(query: str) -> list[str]:
    """Extract up to _MAX_KEYWORDS lowercase alpha tokens, excluding stop words."""
    tokens = re.split(r"[\s\W]+", query.lower())
    filtered = [t for t in tokens if t and t not in _STOP_WORDS and len(t) > 2]
    if filtered:
        return filtered[:_MAX_KEYWORDS]
    # Fallback: all-stop-word input — use first 3 raw non-empty tokens
    raw = [t for t in tokens if t]
    return raw[:3] if raw else [query.lower()[:20]]


def _clean_literal(val: str) -> str:
    """Strip SPARQL literal delimiters from a QLever result value.

    QLever returns string literals as '"value"' or '"value"^^xsd:type',
    and IRIs as '<iri>'. Plain strings (numeric counts) are returned as-is.
    """
    val = val.strip()
    if val.startswith('"'):
        # Find closing quote, ignore ^^type suffix
        end = val.find('"', 1)
        if end > 0:
            return val[1:end]
    if val.startswith("<") and val.endswith(">"):
        return val[1:-1]
    return val


def _build_dblp_sparql(
    keywords: list[str],
    max_results: int,
    year_min: int | None = None,
    year_max: int | None = None,
) -> str:
    """Build the DBLP QLever SPARQL query string."""
    score_expr = " +\n    ".join(
        f"IF(CONTAINS(?lowerTitle, '{kw}'), 1, 0)" for kw in keywords
    )
    year_filter = ""
    if year_min is not None or year_max is not None:
        lo = year_min if year_min is not None else 1800
        hi = year_max if year_max is not None else 2100
        year_filter = f"\n  FILTER(?year >= {lo} && ?year <= {hi})"

    return f"""PREFIX dblp: <https://dblp.org/rdf/schema#>
PREFIX cito: <http://purl.org/spar/cito/>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?label ?doi ?year (COUNT(?citation) AS ?cites) ?score WHERE {{
  ?publ rdf:type dblp:Publication ;
        dblp:title ?title ;
        dblp:yearOfPublication ?year ;
        dblp:doi ?doi ;
        dblp:omid ?omid .
  ?publ rdfs:label ?label .
  BIND(LCASE(STR(?title)) AS ?lowerTitle)
  BIND(
    {score_expr}
    AS ?score
  )
  FILTER(?score >= 1){year_filter}
  OPTIONAL {{
    ?citation rdf:type cito:Citation ;
              cito:hasCitedEntity ?omid .
  }}
}}
GROUP BY ?label ?doi ?year ?score
ORDER BY DESC(?score) DESC(?cites)
LIMIT {max_results}"""


_DOI_URI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)


def _parse_dblp_response(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse QLever JSON response (application/qlever-results+json) into records.

    Each record: {"title": str, "doi": str, "year": int | None, "cites": int}
    """
    results: list[dict[str, Any]] = []
    for row in data.get("res", []):
        if len(row) < 5:
            continue
        try:
            title = _clean_literal(str(row[0]))
            doi_raw = _clean_literal(str(row[1])).replace("\\_", "_").strip()
            for prefix in _DOI_URI_PREFIXES:
                if doi_raw.startswith(prefix):
                    doi_raw = doi_raw[len(prefix):]
                    break
            year_str = _clean_literal(str(row[2]))
            year = int(year_str) if year_str.isdigit() else None
            cites_str = _clean_literal(str(row[3]))
            cites = int(cites_str) if cites_str.isdigit() else 0
        except (ValueError, TypeError, IndexError):
            continue
        if title and doi_raw:
            results.append({"title": title, "doi": doi_raw, "year": year, "cites": cites})
    return results
```

- [ ] **Step 5: Run tests — expect most to pass, some may need tuning**

```bash
uv run pytest tests/unit/test_dblp_sparql_search.py -q
```

Expected: all 17 tests pass. If `test_tokenise_fallback_on_all_stop_words` or `test_clean_literal_typed_literal` fail, re-read the implementation and fix.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/papers.py \
        src/perspicacite/search/dblp_sparql_search.py \
        tests/unit/test_dblp_sparql_search.py
git commit -m "feat: add DBLP SPARQL query helpers and PaperSource.DBLP_SPARQL"
```

---

## Task 3: SemOpenAlex schema discovery + enrichment helpers

**Files:**
- Modify: `src/perspicacite/search/dblp_sparql_search.py` (add SemOpenAlex helpers)
- Modify: `tests/unit/test_dblp_sparql_search.py` (add SemOpenAlex tests)

- [ ] **Step 1: Discover the live SemOpenAlex schema**

Run this curl command to confirm which predicates SemOpenAlex uses for DOI and abstract on a known paper (AlphaFold 2, DOI `10.1038/s41586-021-03819-2`):

```bash
curl -s -X POST "https://semopenalex.org/sparql" \
  -H "Accept: application/sparql-results+json" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode 'query=
PREFIX schema: <https://schema.org/>
SELECT ?p ?o WHERE {
  ?work schema:identifier <https://doi.org/10.1038/s41586-021-03819-2> .
  ?work ?p ?o .
} LIMIT 20' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for b in d.get(\"results\",{}).get(\"bindings\",[]):
    print(b.get(\"p\",{}).get(\"value\",\"\")[:60], \"|\", str(b.get(\"o\",{}).get(\"value\",\"\"))[:80])
"
```

Look for a predicate whose object contains abstract text. Common values:
- `dcterms:abstract` / `http://purl.org/dc/terms/abstract` → abstract as plain string
- `schema:description` / `https://schema.org/description` → alternative abstract predicate

If `schema:identifier` returns a result, the DOI lookup works. If no results, run:

```bash
curl -s -X POST "https://semopenalex.org/sparql" \
  -H "Accept: application/sparql-results+json" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode 'query=
SELECT DISTINCT ?p WHERE {
  ?s a <https://semopenalex.org/ontology/Work> .
  ?s ?p ?o .
} LIMIT 30' | python3 -c "
import json,sys
d=json.load(sys.stdin)
for b in d.get(\"results\",{}).get(\"bindings\",[]):
    print(b[\"p\"][\"value\"])
"
```

Update `_SEMOA_DOI_PRED` and `_SEMOA_ABS_PRED` constants in `dblp_sparql_search.py` based on what you find.

- [ ] **Step 2: Add SemOpenAlex tests to the test file**

Append to `tests/unit/test_dblp_sparql_search.py`:

```python
# ── _build_semoa_sparql ───────────────────────────────────────────────────────

def test_build_semoa_sparql_contains_dois():
    from perspicacite.search.dblp_sparql_search import _build_semoa_sparql
    sparql = _build_semoa_sparql(["10.1234/a", "10.5678/b"])
    assert "https://doi.org/10.1234/a" in sparql
    assert "https://doi.org/10.5678/b" in sparql


def test_build_semoa_sparql_empty_dois():
    from perspicacite.search.dblp_sparql_search import _build_semoa_sparql
    # Empty list → valid but trivially empty VALUES clause
    sparql = _build_semoa_sparql([])
    assert "VALUES" in sparql


# ── _parse_semoa_response ─────────────────────────────────────────────────────

def test_parse_semoa_response_maps_doi_to_abstract():
    from perspicacite.search.dblp_sparql_search import _parse_semoa_response
    data = {
        "results": {
            "bindings": [
                {
                    "doiUri": {"type": "uri", "value": "https://doi.org/10.1234/abc"},
                    "abstract": {"type": "literal", "value": "This paper studies neural networks."},
                },
                {
                    "doiUri": {"type": "uri", "value": "https://doi.org/10.5678/xyz"},
                    "abstract": {"type": "literal", "value": "Graph convolutional methods."},
                },
            ]
        }
    }
    result = _parse_semoa_response(data)
    assert result["10.1234/abc"] == "This paper studies neural networks."
    assert result["10.5678/xyz"] == "Graph convolutional methods."


def test_parse_semoa_response_skips_missing_abstract():
    from perspicacite.search.dblp_sparql_search import _parse_semoa_response
    data = {
        "results": {
            "bindings": [
                {
                    "doiUri": {"type": "uri", "value": "https://doi.org/10.1234/abc"},
                    # no "abstract" key
                },
            ]
        }
    }
    result = _parse_semoa_response(data)
    assert result == {}


def test_parse_semoa_response_empty():
    from perspicacite.search.dblp_sparql_search import _parse_semoa_response
    assert _parse_semoa_response({}) == {}
    assert _parse_semoa_response({"results": {"bindings": []}}) == {}
```

- [ ] **Step 3: Run new tests — confirm FAIL**

```bash
uv run pytest tests/unit/test_dblp_sparql_search.py::test_build_semoa_sparql_contains_dois -q
```

Expected: `ImportError` — `_build_semoa_sparql` doesn't exist yet.

- [ ] **Step 4: Add SemOpenAlex helpers to `dblp_sparql_search.py`**

Append to `src/perspicacite/search/dblp_sparql_search.py` (after `_parse_dblp_response`):

```python
def _build_semoa_sparql(dois: list[str]) -> str:
    """Build a VALUES-based batch SPARQL to fetch abstracts from SemOpenAlex.

    Uses IRI form <https://doi.org/10.xxx> for DOI matching.
    Predicates are configured via _SEMOA_DOI_PRED / _SEMOA_ABS_PRED constants;
    update those constants if the live endpoint schema differs.
    """
    doi_iris = " ".join(f"<https://doi.org/{doi}>" for doi in dois)
    return f"""PREFIX schema:  <https://schema.org/>
PREFIX dcterms: <http://purl.org/dc/terms/>

SELECT ?doiUri ?abstract WHERE {{
  VALUES ?doiUri {{ {doi_iris} }}
  ?work {_SEMOA_DOI_PRED} ?doiUri .
  OPTIONAL {{ ?work {_SEMOA_ABS_PRED} ?abstract . }}
}}"""


def _parse_semoa_response(data: dict[str, Any]) -> dict[str, str]:
    """Parse standard SPARQL JSON response into {lowercase_doi: abstract} mapping."""
    doi_to_abstract: dict[str, str] = {}
    for binding in data.get("results", {}).get("bindings", []):
        doi_uri = binding.get("doiUri", {}).get("value", "")
        abstract = binding.get("abstract", {}).get("value", "")
        if not doi_uri or not abstract:
            continue
        doi = doi_uri
        for prefix in _DOI_URI_PREFIXES:
            if doi_uri.startswith(prefix):
                doi = doi_uri[len(prefix):]
                break
        doi_to_abstract[doi.lower()] = abstract
    return doi_to_abstract
```

- [ ] **Step 5: Run all tests in the file**

```bash
uv run pytest tests/unit/test_dblp_sparql_search.py -q
```

Expected: all 22 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/search/dblp_sparql_search.py \
        tests/unit/test_dblp_sparql_search.py
git commit -m "feat: add SemOpenAlex SPARQL enrichment helpers"
```

---

## Task 4: `DBLPSPARQLSearchProvider` class and full pipeline tests

**Files:**
- Modify: `src/perspicacite/search/dblp_sparql_search.py` (add async functions + class)
- Modify: `tests/unit/test_dblp_sparql_search.py` (add integration tests)

- [ ] **Step 1: Add integration tests for the full search() pipeline**

Append to `tests/unit/test_dblp_sparql_search.py`:

```python
# ── DBLPSPARQLSearchProvider.search() ────────────────────────────────────────

import pytest
from unittest.mock import AsyncMock, patch


_FAKE_DBLP_RESP = {
    "res": [
        ['"Graph Neural Networks Survey"', '"10.9999/gnn"', '"2020"', '"800"', '"2"'],
        ['"Deep Graph Learning"', '"10.9999/dgl"', '"2019"', '"300"', '"1"'],
    ]
}

_FAKE_SEMOA_RESP = {
    "results": {
        "bindings": [
            {
                "doiUri": {"type": "uri", "value": "https://doi.org/10.9999/gnn"},
                "abstract": {"type": "literal", "value": "A survey of GNNs."},
            }
        ]
    }
}


@pytest.mark.asyncio
async def test_search_returns_papers():
    from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider

    provider = DBLPSPARQLSearchProvider()

    with (
        patch(
            "perspicacite.search.dblp_sparql_search._query_dblp",
            new=AsyncMock(return_value=[
                {"title": "Graph Neural Networks Survey", "doi": "10.9999/gnn", "year": 2020, "cites": 800},
                {"title": "Deep Graph Learning", "doi": "10.9999/dgl", "year": 2019, "cites": 300},
            ]),
        ),
        patch(
            "perspicacite.search.dblp_sparql_search._enrich_semoa",
            new=AsyncMock(return_value={"10.9999/gnn": "A survey of GNNs."}),
        ),
    ):
        papers = await provider.search("graph neural networks", max_results=5)

    assert len(papers) == 2
    gnn = next(p for p in papers if p.doi == "10.9999/gnn")
    assert gnn.title == "Graph Neural Networks Survey"
    assert gnn.abstract == "A survey of GNNs."
    assert gnn.year == 2020
    assert gnn.metadata["citation_count"] == 800


@pytest.mark.asyncio
async def test_search_paper_without_semoa_abstract_has_none():
    from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider

    provider = DBLPSPARQLSearchProvider()

    with (
        patch(
            "perspicacite.search.dblp_sparql_search._query_dblp",
            new=AsyncMock(return_value=[
                {"title": "Deep Graph Learning", "doi": "10.9999/dgl", "year": 2019, "cites": 300},
            ]),
        ),
        patch(
            "perspicacite.search.dblp_sparql_search._enrich_semoa",
            new=AsyncMock(return_value={}),  # no abstracts
        ),
    ):
        papers = await provider.search("graph learning")

    assert papers[0].abstract is None


@pytest.mark.asyncio
async def test_search_returns_empty_on_dblp_failure():
    from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider

    provider = DBLPSPARQLSearchProvider()

    with patch(
        "perspicacite.search.dblp_sparql_search._query_dblp",
        new=AsyncMock(return_value=[]),
    ):
        papers = await provider.search("anything")

    assert papers == []


def test_provider_metadata():
    from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider
    p = DBLPSPARQLSearchProvider()
    assert p.name == "dblp_sparql"
    assert p.tier == "external"
    assert p.domains == ["general"]
```

- [ ] **Step 2: Run new tests — confirm FAIL**

```bash
uv run pytest tests/unit/test_dblp_sparql_search.py -k "search" -q
```

Expected: `ImportError` or `AttributeError` — class and async helpers don't exist yet.

- [ ] **Step 3: Install pytest-asyncio if not present**

```bash
uv run python -c "import pytest_asyncio; print('ok')" 2>/dev/null || uv add --dev pytest-asyncio
```

If installed, confirm `asyncio_mode = "auto"` is in `pyproject.toml` or add `@pytest.mark.asyncio` marker. Check:

```bash
grep -r "asyncio_mode\|asyncio" pyproject.toml | head -5
```

If `asyncio_mode = "auto"` is present, the `@pytest.mark.asyncio` on the tests is optional but harmless. If not, the tests need the decorator (already present above).

- [ ] **Step 4: Add async helpers and provider class to `dblp_sparql_search.py`**

Append to `src/perspicacite/search/dblp_sparql_search.py` (after `_parse_semoa_response`):

```python
async def _query_dblp(sparql: str) -> list[dict[str, Any]]:
    """POST SPARQL to DBLP QLever endpoint; return parsed records or [] on error."""
    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            resp = await client.post(
                _DBLP_SPARQL_URL,
                headers={
                    "Accept": "application/qlever-results+json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                data={"query": sparql},
            )
            resp.raise_for_status()
            return _parse_dblp_response(resp.json())
        except Exception as exc:
            logger.warning("dblp_sparql_query_error", error=str(exc))
            return []


async def _enrich_semoa(dois: list[str]) -> dict[str, str]:
    """POST batch VALUES SPARQL to SemOpenAlex; return {doi: abstract} or {} on error."""
    if not dois:
        return {}
    sparql = _build_semoa_sparql(dois)
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.post(
                _SEMOA_SPARQL_URL,
                headers={
                    "Accept": "application/sparql-results+json",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                data={"query": sparql},
            )
            resp.raise_for_status()
            return _parse_semoa_response(resp.json())
        except Exception as exc:
            logger.warning(
                "semopenalex_enrich_failed", error=str(exc), n_dois=len(dois)
            )
            return {}


class DBLPSPARQLSearchProvider:
    """Search DBLP via SPARQL (citation-ranked) with SemOpenAlex abstract enrichment.

    Phase 1: POST to sparql.dblp.org — returns papers whose titles match
             query keywords, ordered by keyword score × citation count.
    Phase 2: POST to semopenalex.org/sparql — batch-fetches abstracts for
             the DOIs from phase 1. Failures degrade gracefully (no abstract).
    """

    name: ClassVar[str] = "dblp_sparql"
    description: ClassVar[str] = (
        "DBLP citation-ranked title search + SemOpenAlex abstract enrichment (free SPARQL)"
    )
    domains: ClassVar[list[str]] = ["general"]
    tier: ClassVar[str] = "external"   # 30 s timeout via DomainAwareAggregator
    retry: ClassVar[int] = 1

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        keywords = _tokenise_query(query)
        sparql = _build_dblp_sparql(keywords, max_results, year_min, year_max)

        # Phase 1 — DBLP SPARQL
        records = await _query_dblp(sparql)
        if not records:
            return []

        # Phase 2 — SemOpenAlex abstract enrichment (best-effort)
        dois = [r["doi"] for r in records]
        abstracts = await _enrich_semoa(dois)

        papers: list[Paper] = []
        for rec in records:
            doi = rec["doi"]
            papers.append(
                Paper(
                    id=doi,
                    title=rec["title"],
                    doi=doi,
                    year=rec.get("year"),
                    abstract=abstracts.get(doi.lower()),
                    source=PaperSource.DBLP_SPARQL,
                    metadata={
                        "sources": ["dblp_sparql"],
                        "citation_count": rec.get("cites", 0),
                    },
                )
            )

        logger.info("dblp_sparql_search", query=query[:80], results=len(papers))
        return papers
```

- [ ] **Step 5: Run all tests in the file**

```bash
uv run pytest tests/unit/test_dblp_sparql_search.py -q
```

Expected: all tests pass (including the 4 new async ones).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/search/dblp_sparql_search.py \
        tests/unit/test_dblp_sparql_search.py
git commit -m "feat: add DBLPSPARQLSearchProvider with two-phase SPARQL search"
```

---

## Task 5: Aggregator wiring, `__init__` export, and `config.example.yml`

**Files:**
- Modify: `src/perspicacite/search/__init__.py`
- Modify: `src/perspicacite/search/domain_aggregator.py`
- Modify: `config.example.yml`
- Create: `tests/unit/test_dblp_sparql_aggregator.py`

- [ ] **Step 1: Write failing aggregator tests**

Create `tests/unit/test_dblp_sparql_aggregator.py`:

```python
"""Tests for DBLPSPARQLSearchProvider wiring in build_aggregator."""
from __future__ import annotations

from types import SimpleNamespace


def _make_config(enabled_providers=None):
    return SimpleNamespace(
        search=SimpleNamespace(
            enabled_providers=enabled_providers or [],
            provider_timeout_s=20.0,
            max_results_per_provider=25,
            core_api_key="",
            ads_api_key="",
        ),
        google_scholar=SimpleNamespace(enabled=False),
        pdf_download=SimpleNamespace(unpaywall_email=""),
    )


def test_dblp_sparql_in_aggregator_when_enabled():
    from perspicacite.search.domain_aggregator import build_aggregator

    cfg = _make_config(enabled_providers=["dblp_sparql"])
    agg = build_aggregator(cfg)
    names = [getattr(p, "name", "") for p in agg._providers]
    assert "dblp_sparql" in names


def test_dblp_sparql_not_in_aggregator_when_absent():
    from perspicacite.search.domain_aggregator import build_aggregator

    cfg = _make_config(enabled_providers=["europepmc"])
    agg = build_aggregator(cfg)
    names = [getattr(p, "name", "") for p in agg._providers]
    assert "dblp_sparql" not in names
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/unit/test_dblp_sparql_aggregator.py -q
```

Expected: `test_dblp_sparql_in_aggregator_when_enabled` FAIL — provider not wired yet.

- [ ] **Step 3: Add `dblp_sparql` block to `build_aggregator` in `domain_aggregator.py`**

In `src/perspicacite/search/domain_aggregator.py`, insert the following block **before** the `logger.info("build_aggregator_ready", ...)` call (after the `google_scholar` block, around line 307):

```python
    if "dblp_sparql" in enabled:
        try:
            from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider
            providers.append(DBLPSPARQLSearchProvider())
        except Exception as exc:
            logger.warning("build_aggregator_dblp_sparql_unavailable", error=str(exc))
```

- [ ] **Step 4: Add export to `src/perspicacite/search/__init__.py`**

Add two lines: the import and the `__all__` entry.

After the existing `from perspicacite.search.ads_search import ADSSearchProvider` line, add:

```python
from perspicacite.search.dblp_sparql_search import DBLPSPARQLSearchProvider
```

In the `__all__` list, add `"DBLPSPARQLSearchProvider"` (keep the list alphabetically sorted — it goes between `"DomainClassifier"` and `"EuropePMCSearchProvider"`).

- [ ] **Step 5: Add commented line to `config.example.yml`**

In `config.example.yml`, in the `search.enabled_providers` section, after the `# - google_scholar` line, add:

```yaml
    # - dblp_sparql  # DBLP citation-ranked titles + SemOpenAlex abstracts (free, no key)
```

- [ ] **Step 6: Run all tests**

```bash
uv run pytest tests/unit/test_dblp_sparql_aggregator.py tests/unit/test_dblp_sparql_search.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Run full unit suite to check for regressions**

```bash
uv run pytest tests/unit/ -q --tb=short 2>&1 | tail -10
```

Expected: all previously passing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/search/__init__.py \
        src/perspicacite/search/domain_aggregator.py \
        config.example.yml \
        tests/unit/test_dblp_sparql_aggregator.py
git commit -m "feat: wire DBLPSPARQLSearchProvider into build_aggregator and exports"
```

---

## Task 6: `--ingest-mode` CLI flag on `create-kb` and `add-to-kb`

**Files:**
- Modify: `src/perspicacite/cli.py`
- Create: `tests/unit/test_cli_ingest_mode.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/unit/test_cli_ingest_mode.py`:

```python
"""Tests for --ingest-mode CLI flag on create-kb and add-to-kb commands."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def _make_bib(tmp_path: Path) -> Path:
    bib = tmp_path / "refs.bib"
    bib.write_text("@article{a, title={Test Paper}, year={2024}}\n")
    return bib


def test_add_to_kb_ingest_mode_overrides_config(tmp_path):
    """--ingest-mode abstract_only sets config.knowledge_base.ingest_mode before pipeline call."""
    from click.testing import CliRunner
    from perspicacite.cli import cli

    bib = _make_bib(tmp_path)
    captured: dict = {}

    async def fake_add_bibtex(config, kb_name, bib_path, session_db, chroma_dir):
        captured["mode"] = config.knowledge_base.ingest_mode
        return {
            "new_papers": 0,
            "chunks_added": 0,
            "pdf_stats": {"attempted": 0, "success": 0, "failed": 0, "skipped_no_doi": 0},
        }

    with patch("perspicacite.cli._add_bibtex_to_existing_kb", new=fake_add_bibtex):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "-c", "config.example.yml",
                "add-to-kb", "mytest",
                "--from-bibtex", str(bib),
                "--ingest-mode", "abstract_only",
            ],
        )

    assert captured.get("mode") == "abstract_only", (
        f"Expected 'abstract_only', got {captured.get('mode')!r}. "
        f"CLI output:\n{result.output}"
    )


def test_add_to_kb_default_ingest_mode_unchanged(tmp_path):
    """Omitting --ingest-mode leaves config.knowledge_base.ingest_mode as config default."""
    from click.testing import CliRunner
    from perspicacite.cli import cli

    bib = _make_bib(tmp_path)
    captured: dict = {}

    async def fake_add_bibtex(config, kb_name, bib_path, session_db, chroma_dir):
        captured["mode"] = config.knowledge_base.ingest_mode
        return {
            "new_papers": 0,
            "chunks_added": 0,
            "pdf_stats": {"attempted": 0, "success": 0, "failed": 0, "skipped_no_doi": 0},
        }

    with patch("perspicacite.cli._add_bibtex_to_existing_kb", new=fake_add_bibtex):
        runner = CliRunner()
        runner.invoke(
            cli,
            ["-c", "config.example.yml", "add-to-kb", "mytest", "--from-bibtex", str(bib)],
        )

    # config.example.yml has ingest_mode: "auto" — flag not given, so stays "auto"
    assert captured.get("mode") == "auto"


def test_add_to_kb_ingest_mode_full_text(tmp_path):
    """--ingest-mode full_text is a valid choice."""
    from click.testing import CliRunner
    from perspicacite.cli import cli

    bib = _make_bib(tmp_path)
    captured: dict = {}

    async def fake_add_bibtex(config, kb_name, bib_path, session_db, chroma_dir):
        captured["mode"] = config.knowledge_base.ingest_mode
        return {
            "new_papers": 0,
            "chunks_added": 0,
            "pdf_stats": {"attempted": 0, "success": 0, "failed": 0, "skipped_no_doi": 0},
        }

    with patch("perspicacite.cli._add_bibtex_to_existing_kb", new=fake_add_bibtex):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["-c", "config.example.yml", "add-to-kb", "mytest",
             "--from-bibtex", str(bib), "--ingest-mode", "full_text"],
        )

    assert captured.get("mode") == "full_text"


def test_add_to_kb_invalid_ingest_mode_rejected(tmp_path):
    """--ingest-mode banana is rejected by Click."""
    from click.testing import CliRunner
    from perspicacite.cli import cli

    bib = _make_bib(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["-c", "config.example.yml", "add-to-kb", "mytest",
         "--from-bibtex", str(bib), "--ingest-mode", "banana"],
    )
    assert result.exit_code != 0
    assert "banana" in result.output or "Invalid value" in result.output


def test_create_kb_ingest_mode_overrides_config(tmp_path):
    """--ingest-mode abstract_only also works on create-kb --from-bibtex."""
    from click.testing import CliRunner
    from perspicacite.cli import cli

    bib = _make_bib(tmp_path)
    captured: dict = {}

    async def fake_create_bibtex(config, kb_name, bib_path, description, session_db, chroma_dir):
        captured["mode"] = config.knowledge_base.ingest_mode
        return {
            "name": kb_name,
            "collection_name": f"kb_{kb_name}",
            "papers": 0,
            "chunks_added": 0,
            "pdf_stats": {"attempted": 0, "success": 0, "failed": 0, "skipped_no_doi": 0},
        }

    with patch("perspicacite.cli._create_kb_from_bibtex", new=fake_create_bibtex):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["-c", "config.example.yml", "create-kb", "newkb",
             "--from-bibtex", str(bib), "--ingest-mode", "abstract_only"],
        )

    assert captured.get("mode") == "abstract_only", (
        f"Expected 'abstract_only', got {captured.get('mode')!r}. Output:\n{result.output}"
    )
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
uv run pytest tests/unit/test_cli_ingest_mode.py -q
```

Expected: `test_add_to_kb_ingest_mode_overrides_config` fails because `--ingest-mode` is not a recognised option yet (Click returns exit_code=2, "no such option").

- [ ] **Step 3: Add `--ingest-mode` to the `add-to-kb` command**

In `src/perspicacite/cli.py`, find the `@cli.command("add-to-kb")` decorator block. It currently ends with:

```python
@click.option(
    "--chroma-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Chroma persist directory (default: from config database.chroma_path)",
)
@click.pass_context
def add_to_kb(
    ctx: click.Context,
    name: str,
    from_bibtex: Path,
    session_db: Path | None,
    chroma_dir: Path | None,
) -> None:
```

Add the new option between `--chroma-dir` and `@click.pass_context`:

```python
@click.option(
    "--ingest-mode",
    "ingest_mode",
    type=click.Choice(["auto", "full_text", "abstract_only"]),
    default=None,
    help=(
        "Override knowledge_base.ingest_mode for this run. "
        "'abstract_only' skips PDF download (~80% faster for large corpora)."
    ),
)
```

Update the function signature to include `ingest_mode: str | None`:

```python
def add_to_kb(
    ctx: click.Context,
    name: str,
    from_bibtex: Path,
    session_db: Path | None,
    chroma_dir: Path | None,
    ingest_mode: str | None,
) -> None:
```

Add the override immediately after `config = ctx.obj["config"]`:

```python
    config = ctx.obj["config"]
    if ingest_mode is not None:
        config.knowledge_base.ingest_mode = ingest_mode
```

- [ ] **Step 4: Add `--ingest-mode` to the `create-kb` command**

In `src/perspicacite/cli.py`, find the `@cli.command()` block for `create_kb`. It has these options: `--description`, `--from-bibtex`, `--session-db`, `--chroma-dir`. Add after `--chroma-dir`:

```python
@click.option(
    "--ingest-mode",
    "ingest_mode",
    type=click.Choice(["auto", "full_text", "abstract_only"]),
    default=None,
    help=(
        "Override knowledge_base.ingest_mode for this run. "
        "'abstract_only' skips PDF download (~80% faster for large corpora)."
    ),
)
```

Update the function signature:

```python
def create_kb(
    ctx: click.Context,
    name: str,
    description: str | None,
    from_bibtex: Path | None,
    session_db: Path | None,
    chroma_dir: Path | None,
    ingest_mode: str | None,
) -> None:
```

Add the override immediately after `config = ctx.obj["config"]`:

```python
    config = ctx.obj["config"]
    if ingest_mode is not None:
        config.knowledge_base.ingest_mode = ingest_mode
```

- [ ] **Step 5: Run CLI tests**

```bash
uv run pytest tests/unit/test_cli_ingest_mode.py -q
```

Expected: all 5 tests pass.

- [ ] **Step 6: Run full unit suite**

```bash
uv run pytest tests/unit/ -q --tb=short 2>&1 | tail -10
```

Expected: all previously passing tests still pass, plus 5 new ones.

- [ ] **Step 7: Ruff check**

```bash
uv run ruff check src/perspicacite/search/dblp_sparql_search.py \
                  src/perspicacite/cli.py \
                  --select I001,E501,RUF 2>&1 | grep -v "^Found\|^All"
```

Expected: no output (no lint errors). Fix any issues reported.

- [ ] **Step 8: Final commit**

```bash
git add src/perspicacite/cli.py tests/unit/test_cli_ingest_mode.py
git commit -m "feat: add --ingest-mode CLI flag to create-kb and add-to-kb"
```

---

## Completion check

```bash
uv run pytest tests/unit/ -q --tb=short 2>&1 | tail -5
```

Expected output: all tests pass, new count ≥ previous + 29 (17 helper tests + 4 async tests + 2 aggregator tests + 5 CLI tests + 1 provider-metadata test).

```bash
uv run ruff check src/ --select I001,E501,RUF 2>&1 | grep -v "^Found\|^All\|schema.py:56\|schema.py:61\|models/papers.py"
```

Expected: no output.
