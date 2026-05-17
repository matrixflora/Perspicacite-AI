# Search Source Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add domain-aware search routing infrastructure and six new provider adapters (EuropePMC, PubChem, CORE, INSPIRE-HEP, ADS, OpenCitations COCI) without breaking existing behavior.

**Architecture:** A `DomainClassifier` tags each query with domain labels; a `DomainAwareAggregator` routes to providers whose `domains` match. Providers carry `domains`, `tier`, and `retry` metadata. The aggregator replaces the bare `SciLExAdapter` call in `mcp/server.py` and `pipeline/search_to_kb.py`. OpenCitations slots into `cite_graph.py` as a third citation-graph source.

**Tech Stack:** Python 3.12, httpx (already a dep), biopython (already a dep for PubMed), pytest-asyncio, unittest.mock.

---

## File Map

### Create
| File | Responsibility |
|------|---------------|
| `src/perspicacite/search/domain_classifier.py` | Regex-based query → domain tag classifier |
| `src/perspicacite/search/domain_aggregator.py` | Routing aggregator + circuit breaker + `build_aggregator()` factory |
| `src/perspicacite/search/europepmc_search.py` | Europe PMC REST search provider |
| `src/perspicacite/search/pubchem_search.py` | PubChem compound→PMID→Paper provider |
| `src/perspicacite/search/core_search.py` | CORE API v3 search provider |
| `src/perspicacite/search/inspire_search.py` | INSPIRE-HEP search provider |
| `src/perspicacite/search/ads_search.py` | NASA ADS search provider |
| `src/perspicacite/pipeline/download/opencitations.py` | OpenCitations COCI citation fetcher |
| `tests/unit/test_domain_classifier.py` | 20 parametrized classifier cases |
| `tests/unit/test_domain_aggregator.py` | Routing, dedup, circuit-breaker tests |
| `tests/unit/test_europepmc_search.py` | Mock HTTP → Paper mapping |
| `tests/unit/test_pubchem_search.py` | Two-hop CID resolution tests |
| `tests/unit/test_core_search.py` | CORE auth + year filter tests |
| `tests/unit/test_inspire_search.py` | INSPIRE query construction tests |
| `tests/unit/test_ads_search.py` | ADS key-absent skip + mapping tests |
| `tests/unit/test_opencitations.py` | COCI parsing + multi-source bonus tests |

### Modify
| File | Change |
|------|--------|
| `src/perspicacite/models/papers.py` | Add 6 `PaperSource` enum values |
| `src/perspicacite/search/protocols.py` | Add `domains`, `tier`, `retry` to protocol |
| `src/perspicacite/search/scilex_adapter.py` | Add class-level `domains`, `tier`, `retry` |
| `src/perspicacite/search/pubmed.py` | Add class-level `domains`, `tier`, `retry` |
| `src/perspicacite/search/__init__.py` | Export new classes |
| `src/perspicacite/config/schema.py` | Add `SearchConfig`; wire into `Config` |
| `config.example.yml` | Add `search:` stanza |
| `src/perspicacite/mcp/server.py` | Use `build_aggregator()` in `search_literature` |
| `src/perspicacite/pipeline/search_to_kb.py` | Accept `config` in `run_search()`, use aggregator |
| `src/perspicacite/pipeline/cite_graph.py` | Add COCI third arm + `multi_source_bonus` scoring |
| `src/perspicacite/config/schema.py` | Add `multi_source_bonus` to `CiteGraphConfig` |

---

## Task 1: Foundation — PaperSource enum + SearchConfig

**Files:**
- Modify: `src/perspicacite/models/papers.py:10-31`
- Modify: `src/perspicacite/config/schema.py:1030-1054`

- [ ] **Step 1: Add 6 new PaperSource enum values**

In `src/perspicacite/models/papers.py`, after `SEMANTIC_SCHOLAR = "semantic_scholar"` (line 30), add:

```python
    EUROPE_PMC = "europe_pmc"
    PUBCHEM = "pubchem"
    CORE = "core"
    INSPIRE_HEP = "inspire_hep"
    ADS = "ads"
    OPENCITATIONS = "opencitations"
```

- [ ] **Step 2: Write a quick sanity test**

```python
# tests/unit/test_paper_source_new_values.py
def test_new_paper_source_values():
    from perspicacite.models.papers import PaperSource
    assert PaperSource.EUROPE_PMC.value == "europe_pmc"
    assert PaperSource.PUBCHEM.value == "pubchem"
    assert PaperSource.CORE.value == "core"
    assert PaperSource.INSPIRE_HEP.value == "inspire_hep"
    assert PaperSource.ADS.value == "ads"
    assert PaperSource.OPENCITATIONS.value == "opencitations"
```

Run: `uv run pytest tests/unit/test_paper_source_new_values.py -v`
Expected: PASS

- [ ] **Step 3: Add SearchConfig to config/schema.py**

Before the `Config` class (around line 1030), add:

```python
class SearchConfig(BaseModel):
    """Search provider routing configuration."""

    provider_timeout_s: float = Field(
        default=20.0, ge=1.0,
        description=(
            "Timeout (seconds) for 'reliable' tier providers. "
            "external = 1.5×, flaky = 2.25× this value."
        ),
    )
    max_results_per_provider: int = Field(
        default=25, ge=1, le=200,
        description="Max results fetched per provider before merge.",
    )
    enabled_providers: list[str] = Field(
        default_factory=list,
        description=(
            "Allowlist of provider names. Empty list = all registered "
            "providers enabled. Options: scilex, pubmed, europepmc, "
            "pubchem, core, inspire, ads."
        ),
    )
    core_api_key: str = Field(
        default="",
        description="CORE API v3 key (optional; raises rate limit when set).",
    )
    ads_api_key: str = Field(
        default="",
        description="NASA ADS token (required for ADS provider; skipped if absent).",
    )
```

- [ ] **Step 4: Wire SearchConfig into Config**

In the `Config` class body, add after `copyright_filter`:

```python
    search: SearchConfig = Field(default_factory=SearchConfig)
```

- [ ] **Step 5: Verify config loads**

Run: `uv run python -c "from perspicacite.config.schema import Config; c = Config(); print(c.search)"`
Expected: prints `provider_timeout_s=20.0 ...` with no errors

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/models/papers.py src/perspicacite/config/schema.py tests/unit/test_paper_source_new_values.py
git commit -m "feat(search): add SearchConfig + 6 new PaperSource enum values"
```

---

## Task 2: Extend SearchProvider Protocol + Update Existing Providers

**Files:**
- Modify: `src/perspicacite/search/protocols.py`
- Modify: `src/perspicacite/search/scilex_adapter.py:26-30`
- Modify: `src/perspicacite/search/pubmed.py:131`

- [ ] **Step 1: Extend protocols.py**

Replace the entire `protocols.py` content:

```python
"""Search provider protocol definitions."""

from typing import Any, Protocol

from perspicacite.models.papers import Paper


class SearchProvider(Protocol):
    """Protocol for literature search providers."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def domains(self) -> list[str]:
        """Domain tags for routing. Use ['general'] to match all queries."""
        ...

    @property
    def tier(self) -> str:
        """Reliability tier: 'reliable' | 'external' | 'flaky'."""
        ...

    @property
    def retry(self) -> int:
        """Number of retry attempts after first failure (0 = fail fast)."""
        ...

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **kwargs: Any,
    ) -> list[Paper]: ...
```

- [ ] **Step 2: Add new attrs to SciLExAdapter**

In `src/perspicacite/search/scilex_adapter.py`, the class starts with:
```python
    name = "scilex"
    description = (
        "SciLEx multi-database academic literature search "
        "(Semantic Scholar, OpenAlex, PubMed, arXiv, HAL, DBLP)"
    )
```

Add three lines immediately after `description`:

```python
    domains: list[str] = ["general", "biomedical", "cs"]
    tier: str = "reliable"
    retry: int = 0
```

- [ ] **Step 3: Add new attrs to PubMedSearchAdapter**

In `src/perspicacite/search/pubmed.py`, the `PubMedSearchAdapter` class definition (line 131) opens the class. Add after the class docstring:

```python
    name = "pubmed"
    description = "Direct NCBI PubMed search via Biopython Entrez (esearch → efetch)"
    domains: list[str] = ["biomedical"]
    tier: str = "reliable"
    retry: int = 0
```

- [ ] **Step 4: Verify both providers structurally satisfy the protocol**

```python
# quick smoke — not a file, just run interactively
uv run python -c "
from perspicacite.search.scilex_adapter import SciLExAdapter
from perspicacite.search.pubmed import PubMedSearchAdapter
a = SciLExAdapter()
print(a.domains, a.tier, a.retry)
"
```

Expected: `['general', 'biomedical', 'cs'] reliable 0`

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/protocols.py src/perspicacite/search/scilex_adapter.py src/perspicacite/search/pubmed.py
git commit -m "feat(search): extend SearchProvider protocol with domains/tier/retry"
```

---

## Task 3: DomainClassifier

**Files:**
- Create: `src/perspicacite/search/domain_classifier.py`
- Create: `tests/unit/test_domain_classifier.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_domain_classifier.py
import pytest
from perspicacite.search.domain_classifier import DomainClassifier


@pytest.fixture
def clf():
    return DomainClassifier()


@pytest.mark.parametrize("query,expected_domains", [
    # Biomedical
    ("microbiome metabolomics gut bacteria", {"biomedical", "chemistry"}),
    ("CRISPR gene editing protein expression", {"biomedical"}),
    ("cancer tumor immunotherapy clinical trial", {"biomedical"}),
    ("PMID 12345678 pubmed search", {"biomedical"}),
    # Chemistry
    ("SMILES C1CCCCC1 molecule synthesis", {"chemistry"}),
    ("InChIKey UHOVQNZJYSORNB-UHFFFAOYSA-N", {"chemistry"}),
    ("mass spectrometry NMR spectroscopy metabolite", {"biomedical", "chemistry"}),
    ("compound CAS number molecular weight formula", {"chemistry"}),
    # CS
    ("transformer neural network deep learning benchmark", {"cs"}),
    ("graph neural network software framework algorithm", {"cs"}),
    ("large language model LLM dataset DBLP", {"cs"}),
    # Physics
    ("quantum particle Higgs boson LHC collider", {"physics"}),
    ("dark matter gravitational wave detector CERN", {"physics"}),
    ("hep-ph neutrino INSPIRE inspire-hep", {"physics"}),
    # Astronomy
    ("galaxy exoplanet telescope JWST redshift", {"astronomy"}),
    ("NASA ADS supernova photometric spectral", {"astronomy"}),
    ("black hole cosmology Hubble Chandra", {"astronomy"}),
    # Multi-domain
    ("computational drug discovery machine learning", {"biomedical", "chemistry", "cs"}),
    # General fallback
    ("literature review systematic review", set()),  # no specific domain → general fallback
    # Edge cases
    ("", set()),
    ("42", set()),
])
def test_classify_domains(clf, query, expected_domains):
    result = set(clf.classify(query))
    result.discard("general")  # general is implicit wildcard, not tested here
    assert result == expected_domains, f"query={query!r}: got {result}, want {expected_domains}"


def test_classify_returns_list(clf):
    result = clf.classify("neural network deep learning")
    assert isinstance(result, list)


def test_general_fallback_when_no_domain_matched(clf):
    result = clf.classify("literature review publication trends")
    assert "general" in result
```

Run: `uv run pytest tests/unit/test_domain_classifier.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement DomainClassifier**

Create `src/perspicacite/search/domain_classifier.py`:

```python
"""Lightweight domain classifier for query-to-provider routing."""

from __future__ import annotations

import re


_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("biomedical", re.compile(
        r"\b(gene|protein|mrna|rna|dna|genome|metabol|microbiom|microbiota|"
        r"pathogen|disease|clinical|patient|drug|pharma|enzyme|antibody|"
        r"pcr|sequencing|transcriptom|proteom|cell|tissue|tumor|cancer|"
        r"bacteria|virus|fungal|pmid|pubmed|medline|mesh|immunotherap|"
        r"crispr|epigenet|biomarker|lipidom|glycom|proteom)\b",
        re.IGNORECASE,
    )),
    ("chemistry", re.compile(
        r"\b(compound|molecule|smiles|inchikey|inchi|chemical|reaction|"
        r"synthesis|polymer|ligand|solvent|catalyst|reagent|metabolite|"
        r"spectroscopy|nmr|mass.?spec|chromatograph|pubchem|cas.?number|"
        r"mol(?:ecular)?.?weight|formula|pharmacophore|cheminformat|"
        r"stereochemist|conformer|tautomer)\b",
        re.IGNORECASE,
    )),
    ("cs", re.compile(
        r"\b(algorithm|neural.?network|deep.?learn|machine.?learn|"
        r"transformer|llm|language.?model|graph.?neural|convolutional|"
        r"benchmark|dataset|software|framework|compiler|distributed|"
        r"parallel|blockchain|dblp|reinforcement.?learn|attention.?mechanism|"
        r"recurrent|random.?forest|gradient.?boost)\b",
        re.IGNORECASE,
    )),
    ("physics", re.compile(
        r"\b(quantum|particle|quark|lepton|boson|higgs|dark.?matter|"
        r"dark.?energy|collider|lhc|cern|neutrino|hadron|gravitational.?wave|"
        r"detector|accelerator|plasma|inspire|hep.?(ph|th|ex|lat)|"
        r"supersymmet|string.?theory|qcd|qed|feynman)\b",
        re.IGNORECASE,
    )),
    ("astronomy", re.compile(
        r"\b(galaxy|galax|star|planet|exoplanet|telescope|nebula|pulsar|"
        r"black.?hole|quasar|supernova|cosmolog|redshift|spectral|"
        r"photometric|hubble|jwst|chandra|fermi|nasa|astrophys|ads.?nasa|"
        r"dark.?energy.?survey|milky.?way|solar.?system|asteroid)\b",
        re.IGNORECASE,
    )),
]


class DomainClassifier:
    """Maps a query string to a list of domain tags.

    Returns ['general'] when no specific domain matches — signals to the
    DomainAwareAggregator that general-tagged providers should run.
    Multi-label: a query can match several domains simultaneously.
    """

    def classify(self, query: str) -> list[str]:
        if not query or not query.strip():
            return ["general"]
        domains: list[str] = []
        for domain, pattern in _RULES:
            if pattern.search(query):
                domains.append(domain)
        return domains if domains else ["general"]
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_domain_classifier.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/domain_classifier.py tests/unit/test_domain_classifier.py
git commit -m "feat(search): DomainClassifier — regex-based query→domain routing"
```

---

## Task 4: DomainAwareAggregator + ProviderHealthTracker

**Files:**
- Create: `src/perspicacite/search/domain_aggregator.py`
- Create: `tests/unit/test_domain_aggregator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_domain_aggregator.py
from __future__ import annotations

import asyncio
import pytest
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.search.domain_aggregator import DomainAwareAggregator, ProviderHealthTracker


def _paper(doi: str, title: str = "Title") -> Paper:
    return Paper(id=doi, title=title, doi=doi, source=PaperSource.PUBMED)


class _Provider:
    def __init__(
        self,
        name: str,
        papers: list[Paper],
        domains: list[str] = None,
        tier: str = "reliable",
        retry: int = 0,
        fail: bool = False,
    ):
        self.name = name
        self.description = name
        self.domains = domains or ["general"]
        self.tier = tier
        self.retry = retry
        self._papers = papers
        self._fail = fail
        self.call_count = 0

    async def search(self, query, max_results=20, year_min=None, year_max=None, **kwargs):
        self.call_count += 1
        if self._fail:
            raise RuntimeError("provider failed")
        return self._papers


@pytest.mark.asyncio
async def test_basic_routing_general_provider():
    p = _Provider("gen", [_paper("10.1/a")])
    agg = DomainAwareAggregator([p], provider_timeout_s=5.0)
    results = await agg.search("any query")
    assert len(results) == 1
    assert results[0].doi == "10.1/a"


@pytest.mark.asyncio
async def test_domain_provider_included_when_query_matches():
    bio = _Provider("bio", [_paper("10.1/bio")], domains=["biomedical"])
    phys = _Provider("phys", [_paper("10.1/phys")], domains=["physics"])
    agg = DomainAwareAggregator([bio, phys], provider_timeout_s=5.0)
    results = await agg.search("gene expression cancer")
    dois = {r.doi for r in results}
    assert "10.1/bio" in dois
    assert "10.1/phys" not in dois


@pytest.mark.asyncio
async def test_domain_provider_excluded_when_query_doesnt_match():
    phys = _Provider("phys", [_paper("10.1/phys")], domains=["physics"])
    agg = DomainAwareAggregator([phys], provider_timeout_s=5.0)
    results = await agg.search("gene expression cancer microbiome")
    assert results == []


@pytest.mark.asyncio
async def test_dedup_by_doi():
    p1 = _Provider("a", [_paper("10.1/dup"), _paper("10.1/unique")])
    p2 = _Provider("b", [_paper("10.1/dup")])
    agg = DomainAwareAggregator([p1, p2], provider_timeout_s=5.0)
    results = await agg.search("any")
    dois = [r.doi for r in results]
    assert dois.count("10.1/dup") == 1
    assert "10.1/unique" in dois


@pytest.mark.asyncio
async def test_dedup_by_title_when_no_doi():
    p1 = _Provider("a", [Paper(id="x", title="Same Title", source=PaperSource.PUBMED)])
    p2 = _Provider("b", [Paper(id="y", title="Same Title", source=PaperSource.PUBMED)])
    agg = DomainAwareAggregator([p1, p2], provider_timeout_s=5.0)
    results = await agg.search("any")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_failed_provider_returns_others():
    good = _Provider("good", [_paper("10.1/good")])
    bad = _Provider("bad", [], fail=True)
    agg = DomainAwareAggregator([good, bad], provider_timeout_s=5.0)
    results = await agg.search("any")
    assert len(results) == 1
    assert results[0].doi == "10.1/good"


@pytest.mark.asyncio
async def test_max_results_respected():
    papers = [_paper(f"10.1/{i}") for i in range(30)]
    p = _Provider("big", papers)
    agg = DomainAwareAggregator([p], provider_timeout_s=5.0)
    results = await agg.search("any", max_results=10)
    assert len(results) == 10


def test_circuit_breaker_trips_after_3_failures():
    tracker = ProviderHealthTracker()
    for _ in range(3):
        tracker.record_failure("prov")
    assert not tracker.is_available("prov")


def test_circuit_breaker_resets_on_success():
    tracker = ProviderHealthTracker()
    tracker.record_failure("prov")
    tracker.record_failure("prov")
    tracker.record_success("prov")
    tracker.record_failure("prov")  # counter reset, only 1 failure now
    assert tracker.is_available("prov")


@pytest.mark.asyncio
async def test_circuit_broken_provider_skipped():
    bad = _Provider("bad", [], fail=True)
    agg = DomainAwareAggregator([bad], provider_timeout_s=5.0)
    # Trip the circuit manually
    for _ in range(3):
        agg._health.record_failure("bad")
    good = _Provider("good", [_paper("10.1/g")])
    agg._providers = [bad, good]
    results = await agg.search("any")
    assert bad.call_count == 0
    assert len(results) == 1


def test_available_false_when_no_providers():
    agg = DomainAwareAggregator([])
    assert not agg.available


def test_available_true_when_providers_registered():
    p = _Provider("p", [])
    agg = DomainAwareAggregator([p])
    assert agg.available
```

Run: `uv run pytest tests/unit/test_domain_aggregator.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement DomainAwareAggregator**

Create `src/perspicacite/search/domain_aggregator.py`:

```python
"""Domain-aware search aggregator with per-tier reliability policies."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper
from perspicacite.search.domain_classifier import DomainClassifier

logger = get_logger("perspicacite.search.domain_aggregator")

_OBVIOUS_PLACEHOLDERS = {
    "", "user@example.com", "you@example.com",
    "your.email@domain.com", "email@example.com", "test@test.com",
}


class ProviderHealthTracker:
    """In-memory circuit breaker: skip providers with repeated failures."""

    FAILURE_THRESHOLD = 3
    COOLDOWN_S = 300.0  # 5 minutes

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._tripped_at: dict[str, float] = {}

    def record_success(self, name: str) -> None:
        self._failures.pop(name, None)
        self._tripped_at.pop(name, None)

    def record_failure(self, name: str) -> None:
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= self.FAILURE_THRESHOLD and name not in self._tripped_at:
            self._tripped_at[name] = time.monotonic()
            logger.warning("provider_circuit_tripped", provider=name)

    def is_available(self, name: str) -> bool:
        if name not in self._tripped_at:
            return True
        if time.monotonic() - self._tripped_at[name] >= self.COOLDOWN_S:
            self._tripped_at.pop(name, None)
            self._failures.pop(name, None)
            logger.info("provider_circuit_reset", provider=name)
            return True
        return False


class DomainAwareAggregator:
    """Routes queries to domain-appropriate providers and merges results."""

    def __init__(
        self,
        providers: list[Any],
        *,
        provider_timeout_s: float = 20.0,
        max_results_per_provider: int = 25,
    ) -> None:
        self._providers = providers
        self._timeout_s = provider_timeout_s
        self._max_per = max_results_per_provider
        self._classifier = DomainClassifier()
        self._health = ProviderHealthTracker()

    @property
    def available(self) -> bool:
        return bool(self._providers)

    def _tier_timeout(self, tier: str) -> float:
        if tier == "external":
            return self._timeout_s * 1.5
        if tier == "flaky":
            return self._timeout_s * 2.25
        return self._timeout_s

    def _select_providers(self, domains: list[str]) -> list[Any]:
        domain_set = set(domains)
        selected = []
        for p in self._providers:
            p_domains = set(getattr(p, "domains", ["general"]))
            if "general" in p_domains or p_domains & domain_set:
                name = getattr(p, "name", repr(p))
                if self._health.is_available(name):
                    selected.append(p)
        return selected

    async def _call_provider(
        self,
        provider: Any,
        query: str,
        max_results: int,
        year_min: int | None,
        year_max: int | None,
        extra_kwargs: dict[str, Any],
    ) -> list[Paper]:
        name = getattr(provider, "name", repr(provider))
        tier = getattr(provider, "tier", "reliable")
        retry = getattr(provider, "retry", 0)
        timeout = self._tier_timeout(tier)
        backoffs = [2.0, 5.0]

        for attempt in range(retry + 1):
            try:
                papers = await asyncio.wait_for(
                    provider.search(
                        query=query,
                        max_results=max_results,
                        year_min=year_min,
                        year_max=year_max,
                        **extra_kwargs,
                    ),
                    timeout=timeout,
                )
                self._health.record_success(name)
                return papers
            except asyncio.TimeoutError:
                logger.warning("provider_timeout", provider=name, attempt=attempt)
            except Exception as exc:
                logger.warning("provider_error", provider=name, error=str(exc), attempt=attempt)
            self._health.record_failure(name)
            if attempt < retry:
                await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
        return []

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        apis: list[str] | None = None,
        **kwargs: Any,
    ) -> list[Paper]:
        """Search all domain-appropriate providers and merge results.

        ``apis`` is forwarded to the SciLEx provider only (backward compat
        with mcp/server.py and search_to_kb.py call sites).
        """
        domains = self._classifier.classify(query)
        providers = self._select_providers(domains)

        if not providers:
            logger.warning("no_providers_selected", query=query[:80], domains=domains)
            return []

        tasks = []
        for p in providers:
            extra: dict[str, Any] = {}
            if apis and getattr(p, "name", "") == "scilex":
                extra["apis"] = apis
            tasks.append(
                self._call_provider(
                    p, query=query,
                    max_results=self._max_per,
                    year_min=year_min, year_max=year_max,
                    extra_kwargs=extra,
                )
            )

        results_per_provider: list[list[Paper]] = await asyncio.gather(*tasks)

        seen_dois: set[str] = set()
        seen_title_hashes: set[str] = set()
        merged: list[Paper] = []
        for papers in results_per_provider:
            for paper in papers:
                if paper.doi:
                    doi_key = paper.doi.lower().strip()
                    if doi_key in seen_dois:
                        continue
                    seen_dois.add(doi_key)
                else:
                    title_hash = paper.title.lower().strip()[:80]
                    if title_hash in seen_title_hashes:
                        continue
                    seen_title_hashes.add(title_hash)
                merged.append(paper)

        return merged[:max_results]


def build_aggregator(config: Any) -> DomainAwareAggregator:
    """Construct a DomainAwareAggregator from a Config object.

    Reads config.search for provider list and keys.
    Falls back gracefully when optional providers are unavailable.
    """
    search_cfg = getattr(config, "search", None)
    enabled_raw: list[str] = getattr(search_cfg, "enabled_providers", []) or []
    enabled: set[str] = set(enabled_raw) if enabled_raw else {
        "scilex", "pubmed", "europepmc", "pubchem", "core", "inspire", "ads"
    }
    timeout = float(getattr(search_cfg, "provider_timeout_s", 20.0))
    max_per = int(getattr(search_cfg, "max_results_per_provider", 25))

    providers: list[Any] = []
    scilex_available = False

    if "scilex" in enabled:
        try:
            from perspicacite.search.scilex_adapter import SciLExAdapter
            adapter = SciLExAdapter.from_config(config)
            if adapter.available:
                providers.append(adapter)
                scilex_available = True
        except Exception as exc:
            logger.warning("build_aggregator_scilex_unavailable", error=str(exc))

    # Standalone PubMed (biopython Entrez) — useful when SciLEx is absent
    if "pubmed" in enabled and not scilex_available:
        try:
            from perspicacite.search.pubmed import PubMedSearchAdapter
            pdf_cfg = getattr(config, "pdf_download", None)
            email = getattr(pdf_cfg, "unpaywall_email", "") or ""
            if email and email.strip().lower() not in _OBVIOUS_PLACEHOLDERS:
                providers.append(PubMedSearchAdapter(email=email))
        except Exception as exc:
            logger.warning("build_aggregator_pubmed_unavailable", error=str(exc))

    if "europepmc" in enabled:
        try:
            from perspicacite.search.europepmc_search import EuropePMCSearchProvider
            providers.append(EuropePMCSearchProvider())
        except Exception as exc:
            logger.warning("build_aggregator_europepmc_unavailable", error=str(exc))

    if "core" in enabled:
        try:
            from perspicacite.search.core_search import CORESearchProvider
            core_key = getattr(search_cfg, "core_api_key", "") or ""
            providers.append(CORESearchProvider(api_key=core_key or None))
        except Exception as exc:
            logger.warning("build_aggregator_core_unavailable", error=str(exc))

    if "inspire" in enabled:
        try:
            from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
            providers.append(INSPIREHEPSearchProvider())
        except Exception as exc:
            logger.warning("build_aggregator_inspire_unavailable", error=str(exc))

    if "ads" in enabled:
        try:
            from perspicacite.search.ads_search import ADSSearchProvider
            ads_key = getattr(search_cfg, "ads_api_key", "") or ""
            if ads_key:
                providers.append(ADSSearchProvider(api_key=ads_key))
            else:
                logger.info("build_aggregator_ads_skipped_no_key")
        except Exception as exc:
            logger.warning("build_aggregator_ads_unavailable", error=str(exc))

    if "pubchem" in enabled:
        try:
            from perspicacite.search.pubchem_search import PubChemSearchProvider
            pdf_cfg = getattr(config, "pdf_download", None)
            email = getattr(pdf_cfg, "unpaywall_email", "") or ""
            providers.append(PubChemSearchProvider(ncbi_email=email or None))
        except Exception as exc:
            logger.warning("build_aggregator_pubchem_unavailable", error=str(exc))

    logger.info(
        "build_aggregator_ready",
        n_providers=len(providers),
        names=[getattr(p, "name", "?") for p in providers],
    )
    return DomainAwareAggregator(
        providers, provider_timeout_s=timeout, max_results_per_provider=max_per,
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_domain_aggregator.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/domain_aggregator.py tests/unit/test_domain_aggregator.py
git commit -m "feat(search): DomainAwareAggregator + ProviderHealthTracker + build_aggregator factory"
```

---

## Task 5: Wire Aggregator into MCP Server + search_to_kb + __init__ + config.example.yml

**Files:**
- Modify: `src/perspicacite/mcp/server.py:395-421`
- Modify: `src/perspicacite/pipeline/search_to_kb.py:333-366`
- Modify: `src/perspicacite/search/__init__.py`
- Modify: `config.example.yml`

- [ ] **Step 1: Update mcp/server.py search_literature tool**

In `src/perspicacite/mcp/server.py`, find the block starting at line 395:

```python
    from perspicacite.search.scilex_adapter import SciLExAdapter

    try:
        adapter = SciLExAdapter.from_config(state.config)
        if not adapter.available:
            # SciLEx is an optional extra; without it, this tool has no
            # backend. Tell the caller how to install it instead of
            # silently returning zero results.
            return _json_error(
                "SciLEx (multi-DB search aggregator) is not installed. "
                "Install with: `uv pip install -e \".[scilex]\"` from the "
                "Perspicacité repo. Or skip and use search_knowledge_base / "
                "generate_report on a pre-ingested KB instead.",
                scilex_available=False,
            )
        # When filtering by relevance, overfetch ~3x so the post-filter
        # has enough candidates to actually return ``max_results``
        # quality hits. Capped at SciLEx's per-DB ceiling.
        fetch_n = min(max_results * 3, 100) if min_relevance > 0 else max_results
        papers = await adapter.search(
            query=query,
            max_results=fetch_n,
            year_min=year_min,
            year_max=year_max,
            apis=databases or ["semantic_scholar", "openalex", "pubmed"],
            article_type=article_type,
        )
```

Replace with:

```python
    from perspicacite.search.domain_aggregator import build_aggregator

    try:
        aggregator = build_aggregator(state.config)
        if not aggregator.available:
            return _json_error(
                "No search providers are available. Install SciLEx with: "
                "`uv pip install -e \".[scilex]\"` from the Perspicacité repo, "
                "or configure at least one search provider in config.yml.",
                scilex_available=False,
            )
        fetch_n = min(max_results * 3, 100) if min_relevance > 0 else max_results
        papers = await aggregator.search(
            query=query,
            max_results=fetch_n,
            year_min=year_min,
            year_max=year_max,
            apis=databases or ["semantic_scholar", "openalex", "pubmed"],
            article_type=article_type,
        )
```

- [ ] **Step 2: Update run_search() in search_to_kb.py**

Find the `run_search` function signature (around line 333):

```python
async def run_search(
    *,
    query: str,
    max_results: int,
    databases: list[str] | None,
    year_min: int | None,
    year_max: int | None,
    article_type: str | None = None,
) -> list[Any]:
```

Replace with:

```python
async def run_search(
    *,
    query: str,
    max_results: int,
    databases: list[str] | None,
    year_min: int | None,
    year_max: int | None,
    article_type: str | None = None,
    config: Any | None = None,
) -> list[Any]:
```

Replace the function body:

```python
    """Run a multi-DB search via DomainAwareAggregator.

    Falls back to SciLEx-only when config is not provided.
    Returns an empty list when no providers are available.
    """
    if config is not None:
        from perspicacite.search.domain_aggregator import build_aggregator
        aggregator = build_aggregator(config)
        if not aggregator.available:
            logger.warning("run_search_no_providers_available")
            return []
        papers = await aggregator.search(
            query=query,
            max_results=max_results,
            year_min=year_min,
            year_max=year_max,
            apis=databases or ["semantic_scholar", "openalex", "pubmed"],
        )
    else:
        from perspicacite.search.scilex_adapter import SciLExAdapter
        adapter = SciLExAdapter()
        if not adapter.available:
            logger.warning(
                "search_to_kb_scilex_missing",
                advice="install with: uv pip install -e \".[scilex]\"",
            )
            return []
        papers = await adapter.search(
            query=query,
            max_results=max_results,
            year_min=year_min,
            year_max=year_max,
            apis=databases or ["semantic_scholar", "openalex", "pubmed"],
            article_type=article_type,
        )
    logger.info("search_to_kb_search", query=query, hits=len(papers))
    return list(papers)
```

- [ ] **Step 3: Update search/__init__.py**

Replace the entire file content:

```python
"""Literature search providers."""

from perspicacite.search.scilex_adapter import SciLExAdapter, SciLExSearchProvider
from perspicacite.search.google_scholar import GoogleScholarSearch, SearchAggregator
from perspicacite.search.doi_resolver import resolve_doi, resolve_dois_batch
from perspicacite.search.semantic_scholar import lookup_paper, normalize_paper_id
from perspicacite.search.protocols import SearchProvider
from perspicacite.search.domain_classifier import DomainClassifier
from perspicacite.search.domain_aggregator import DomainAwareAggregator, build_aggregator

__all__ = [
    "SciLExAdapter",
    "SciLExSearchProvider",
    "GoogleScholarSearch",
    "SearchAggregator",
    "resolve_doi",
    "resolve_dois_batch",
    "lookup_paper",
    "normalize_paper_id",
    "SearchProvider",
    "DomainClassifier",
    "DomainAwareAggregator",
    "build_aggregator",
]
```

- [ ] **Step 4: Add search stanza to config.example.yml**

Append to `config.example.yml` (after the last existing stanza):

```yaml
# =============================================================================
# Search Provider Configuration
# =============================================================================

search:
  # Timeout (seconds) for "reliable" tier providers.
  # external tier = 1.5×, flaky tier = 2.25× this value.
  provider_timeout_s: 20

  # Max results fetched per provider before merge + dedup.
  max_results_per_provider: 25

  # Allowlist of provider names. Remove or comment out to disable a provider.
  # Omit this key entirely to enable all registered providers.
  enabled_providers:
    - scilex       # SciLEx aggregator (Semantic Scholar, OpenAlex, PubMed, arXiv, HAL, DBLP)
    - europepmc    # Europe PMC biomedical search (free, no key)
    - pubchem      # PubChem compound → literature search (free, no key)
    - core         # CORE open-access aggregator (free, optional key below)
    - inspire      # INSPIRE-HEP physics bibliography (free, no key)
    - ads          # NASA ADS astronomy (requires ads_api_key below)

  # CORE API key (optional — raises rate limit from 10 to unlimited req/min)
  # Register at https://core.ac.uk/api-keys/register
  core_api_key: ""

  # NASA ADS token (required for ADS provider; register at https://ui.adsabs.harvard.edu)
  # The ADS provider is silently skipped when this is empty.
  ads_api_key: ""
```

- [ ] **Step 5: Smoke-test imports**

Run: `uv run python -c "from perspicacite.search import DomainAwareAggregator, build_aggregator; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Run ruff**

Run: `uv run ruff check src/perspicacite/search/ src/perspicacite/mcp/server.py src/perspicacite/pipeline/search_to_kb.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/search/__init__.py src/perspicacite/mcp/server.py src/perspicacite/pipeline/search_to_kb.py config.example.yml
git commit -m "feat(search): wire DomainAwareAggregator into mcp/server + search_to_kb"
```

---

## Task 6: EuropePMC Search Provider (Wave A)

**Files:**
- Create: `src/perspicacite/search/europepmc_search.py`
- Create: `tests/unit/test_europepmc_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_europepmc_search.py
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from perspicacite.models.papers import PaperSource


_SAMPLE_RESPONSE = {
    "resultList": {
        "result": [
            {
                "id": "PMC1234567",
                "title": "Gut microbiome and health",
                "authorString": "Smith J, Jones A, Brown K",
                "journalTitle": "Nature",
                "pubYear": "2023",
                "doi": "10.1038/nature12345",
                "pmid": "98765432",
                "abstractText": "The gut microbiome plays a key role in health.",
                "isOpenAccess": "Y",
            },
            {
                "id": "PMC9999999",
                "title": "No DOI paper",
                "authorString": "Doe J",
                "journalTitle": "Science",
                "pubYear": "2022",
                "abstractText": "Abstract text.",
                "isOpenAccess": "N",
            },
        ]
    }
}


def _mock_response(data: dict, status: int = 200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data
    mock.raise_for_status = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_search_returns_papers():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_response(_SAMPLE_RESPONSE))
        mock_client_cls.return_value = mock_client

        papers = await provider.search("gut microbiome", max_results=10)

    assert len(papers) == 2
    assert papers[0].doi == "10.1038/nature12345"
    assert papers[0].title == "Gut microbiome and health"
    assert papers[0].year == 2023
    assert papers[0].journal == "Nature"
    assert papers[0].source == PaperSource.EUROPE_PMC
    assert len(papers[0].authors) == 3
    assert papers[0].authors[0].name == "Smith J"


@pytest.mark.asyncio
async def test_search_with_year_filter():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_response({"resultList": {"result": []}}))
        mock_client_cls.return_value = mock_client

        await provider.search("query", year_min=2020, year_max=2023)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
        if isinstance(params, dict):
            assert "2020" in str(params.get("query", "")) or "2020" in str(params)


@pytest.mark.asyncio
async def test_search_empty_result():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()
    empty = {"resultList": {"result": []}}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_response(empty))
        mock_client_cls.return_value = mock_client

        papers = await provider.search("nonexistent topic xyz")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    p = EuropePMCSearchProvider()
    assert p.name == "europepmc"
    assert "biomedical" in p.domains
    assert p.tier == "reliable"
    assert p.retry == 0
```

Run: `uv run pytest tests/unit/test_europepmc_search.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement EuropePMCSearchProvider**

Create `src/perspicacite/search/europepmc_search.py`:

```python
"""Europe PMC REST API search provider."""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.europepmc")

_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


class EuropePMCSearchProvider:
    """Searches Europe PMC via their free REST API."""

    name = "europepmc"
    description = "Europe PMC biomedical literature search (free REST API)"
    domains: list[str] = ["biomedical"]
    tier: str = "reliable"
    retry: int = 0

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        q = query
        if year_min or year_max:
            y_min = year_min or 1800
            y_max = year_max or 2100
            q = f"({q}) AND (FIRST_PDATE:[{y_min}-01-01 TO {y_max}-12-31])"

        params = {
            "query": q,
            "resultType": "core",
            "pageSize": min(max_results, 100),
            "format": "json",
            "cursorMark": "*",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("europepmc_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for item in (data.get("resultList") or {}).get("result") or []:
            doi = item.get("doi") or None
            pmid = item.get("pmid") or None
            paper_id = doi or (f"pmid:{pmid}" if pmid else f"epmc:{item.get('id', 'unknown')}")

            authors: list[Author] = []
            for name in (item.get("authorString") or "").split(","):
                name = name.strip()
                if name:
                    authors.append(Author(name=name))

            year: int | None = None
            try:
                year = int(item["pubYear"])
            except (KeyError, ValueError, TypeError):
                pass

            papers.append(
                Paper(
                    id=paper_id,
                    title=item.get("title") or "Untitled",
                    authors=authors,
                    year=year,
                    doi=doi,
                    pmid=pmid,
                    abstract=item.get("abstractText"),
                    journal=item.get("journalTitle"),
                    source=PaperSource.EUROPE_PMC,
                    metadata={"epmc_id": item.get("id"), "is_oa": item.get("isOpenAccess") == "Y"},
                )
            )

        logger.info("europepmc_search", query=query[:80], results=len(papers))
        return papers
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_europepmc_search.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/europepmc_search.py tests/unit/test_europepmc_search.py
git commit -m "feat(search): EuropePMCSearchProvider — biomedical REST search (Wave A)"
```

---

## Task 7: PubChem Search Provider (Wave A)

**Files:**
- Create: `src/perspicacite/search/pubchem_search.py`
- Create: `tests/unit/test_pubchem_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_pubchem_search.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from perspicacite.models.papers import Paper, PaperSource


def _mock_resp(data: dict, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


_CID_RESP = {"IdentifierList": {"CID": [2244]}}
_PMID_RESP = {"InformationList": {"Information": [{"CID": 2244, "PubMedID": [11234567, 22345678]}]}}
_PUBMED_PAPER = Paper(
    id="10.1000/test",
    title="Aspirin study",
    doi="10.1000/test",
    source=PaperSource.PUBMED,
)


@pytest.mark.asyncio
async def test_name_query_resolves_via_cid(monkeypatch):
    from perspicacite.search import pubchem_search

    async def mock_get_cid(input_value, input_type, client):
        return 2244

    async def mock_get_pmids(cid, client):
        return [11234567, 22345678]

    async def mock_pmids_to_papers(pmids, email, max_results):
        return [_PUBMED_PAPER]

    monkeypatch.setattr(pubchem_search, "_get_cid", mock_get_cid)
    monkeypatch.setattr(pubchem_search, "_get_pmids_for_cid", mock_get_pmids)
    monkeypatch.setattr(pubchem_search, "_pmids_to_papers", mock_pmids_to_papers)

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    papers = await provider.search("aspirin")

    assert len(papers) == 1
    assert papers[0].title == "Aspirin study"


@pytest.mark.asyncio
async def test_inchikey_detected_as_inchikey():
    from perspicacite.search import pubchem_search

    detected_types: list[str] = []

    async def mock_get_cid(input_value, input_type, client):
        detected_types.append(input_type)
        return 2244

    async def mock_get_pmids(cid, client):
        return []

    async def mock_pmids_to_papers(pmids, email, max_results):
        return []

    monkeypatch.setattr = None  # set below in fixture
    from perspicacite.search.pubchem_search import PubChemSearchProvider
    import perspicacite.search.pubchem_search as mod
    mod._get_cid = mock_get_cid
    mod._get_pmids_for_cid = mock_get_pmids
    mod._pmids_to_papers = mock_pmids_to_papers

    provider = PubChemSearchProvider()
    await provider.search("UHOVQNZJYSORNB-UHFFFAOYSA-N")
    assert "inchikey" in detected_types


@pytest.mark.asyncio
async def test_no_cid_returns_empty():
    from perspicacite.search import pubchem_search
    import perspicacite.search.pubchem_search as mod

    async def mock_get_cid(input_value, input_type, client):
        return None

    mod._get_cid = mock_get_cid

    from perspicacite.search.pubchem_search import PubChemSearchProvider
    provider = PubChemSearchProvider()
    papers = await provider.search("nonexistentcompound999xyz")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.pubchem_search import PubChemSearchProvider
    p = PubChemSearchProvider()
    assert p.name == "pubchem"
    assert "chemistry" in p.domains
    assert p.tier == "external"
    assert p.retry == 1


def test_detect_input_type_inchikey():
    from perspicacite.search.pubchem_search import _detect_input_type
    assert _detect_input_type("UHOVQNZJYSORNB-UHFFFAOYSA-N") == "inchikey"


def test_detect_input_type_smiles():
    from perspicacite.search.pubchem_search import _detect_input_type
    assert _detect_input_type("C1CCCCC1") == "smiles"
    assert _detect_input_type("CC(=O)Oc1ccccc1C(=O)O") == "smiles"


def test_detect_input_type_name():
    from perspicacite.search.pubchem_search import _detect_input_type
    assert _detect_input_type("aspirin") == "name"
    assert _detect_input_type("glucose") == "name"
```

Run: `uv run pytest tests/unit/test_pubchem_search.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement PubChemSearchProvider**

Create `src/perspicacite/search/pubchem_search.py`:

```python
"""PubChem compound → PubMed literature search provider."""

from __future__ import annotations

import re
from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Paper, PaperSource

logger = get_logger("perspicacite.search.pubchem")

_PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_SMILES_CHARS = set("=#()[]@+-/\\%")


def _detect_input_type(query: str) -> str:
    """Classify query as 'inchikey', 'smiles', or 'name'."""
    if _INCHIKEY_RE.match(query.strip()):
        return "inchikey"
    if any(c in query for c in _SMILES_CHARS):
        return "smiles"
    return "name"


async def _get_cid(input_value: str, input_type: str, client: httpx.AsyncClient) -> int | None:
    url = f"{_PUBCHEM_BASE}/compound/{input_type}/{input_value}/cids/JSON"
    try:
        resp = await client.get(url, timeout=15.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        cids = resp.json().get("IdentifierList", {}).get("CID", [])
        return cids[0] if cids else None
    except Exception as exc:
        logger.warning("pubchem_cid_lookup_error", input_type=input_type, error=str(exc))
        return None


async def _get_pmids_for_cid(cid: int, client: httpx.AsyncClient) -> list[int]:
    url = f"{_PUBCHEM_BASE}/compound/cid/{cid}/xrefs/PubMedID/JSON"
    try:
        resp = await client.get(url, timeout=15.0)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        info_list = resp.json().get("InformationList", {}).get("Information", [])
        if not info_list:
            return []
        return [int(p) for p in info_list[0].get("PubMedID", [])]
    except Exception as exc:
        logger.warning("pubchem_pmid_lookup_error", cid=cid, error=str(exc))
        return []


async def _pmids_to_papers(
    pmids: list[int],
    email: str | None,
    max_results: int,
) -> list[Paper]:
    if not pmids:
        return []
    try:
        from perspicacite.search.pubmed import PubMedSearchAdapter

        _PLACEHOLDERS = {
            "", "user@example.com", "you@example.com",
            "your.email@domain.com", "email@example.com", "test@test.com",
        }
        eff_email = email if email and email.strip().lower() not in _PLACEHOLDERS else "pubchem@perspicacite.local"
        adapter = PubMedSearchAdapter(email=eff_email)
        pmid_query = " OR ".join(f"{p}[pmid]" for p in pmids[:max_results])
        return await adapter.search(pmid_query, max_results=max_results)
    except Exception as exc:
        logger.warning("pubchem_pmids_to_papers_error", error=str(exc))
        return []


class PubChemSearchProvider:
    """Finds papers by compound name / InChIKey / SMILES via PubChem literature API."""

    name = "pubchem"
    description = "PubChem compound → PubMed literature search (two-hop: CID → PMIDs → Papers)"
    domains: list[str] = ["chemistry"]
    tier: str = "external"
    retry: int = 1

    def __init__(self, ncbi_email: str | None = None) -> None:
        self._email = ncbi_email

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        input_type = _detect_input_type(query.strip())

        async with httpx.AsyncClient(timeout=20.0) as client:
            cid = await _get_cid(query.strip(), input_type, client)
            if cid is None:
                logger.info("pubchem_no_cid", query=query[:80])
                return []
            pmids = await _get_pmids_for_cid(cid, client)

        if not pmids:
            logger.info("pubchem_no_pmids", cid=cid, query=query[:80])
            return []

        papers = await _pmids_to_papers(pmids, self._email, max_results)
        logger.info("pubchem_search", query=query[:80], cid=cid, pmids=len(pmids), papers=len(papers))
        return papers
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_pubchem_search.py -v`
Expected: all PASS (some tests use module-level monkeypatching)

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/pubchem_search.py tests/unit/test_pubchem_search.py
git commit -m "feat(search): PubChemSearchProvider — compound→PMID→Paper (Wave A)"
```

---

## Task 8: CORE Search Provider (Wave B)

**Files:**
- Create: `src/perspicacite/search/core_search.py`
- Create: `tests/unit/test_core_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_core_search.py
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from perspicacite.models.papers import PaperSource


_SAMPLE_RESPONSE = {
    "results": [
        {
            "id": "123",
            "title": "Open access paper on machine learning",
            "authors": [{"name": "Smith, John"}, {"name": "Jones, Alice"}],
            "yearPublished": 2023,
            "doi": "10.1234/core.123",
            "abstract": "This paper discusses ML methods.",
            "downloadUrl": "https://core.ac.uk/download/pdf/123.pdf",
            "journals": [{"title": "Journal of ML"}],
        },
        {
            "id": "456",
            "title": "Another paper",
            "authors": [],
            "yearPublished": None,
            "doi": None,
            "abstract": None,
            "downloadUrl": None,
            "journals": [],
        },
    ],
    "totalHits": 2,
}


def _mock_resp(data: dict, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_search_returns_papers(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider
    import httpx

    provider = CORESearchProvider()

    async def mock_post(url, **kwargs):
        return _mock_resp(_SAMPLE_RESPONSE)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    papers = await provider.search("machine learning")
    assert len(papers) == 2
    assert papers[0].doi == "10.1234/core.123"
    assert papers[0].title == "Open access paper on machine learning"
    assert papers[0].year == 2023
    assert papers[0].journal == "Journal of ML"
    assert papers[0].source == PaperSource.CORE
    assert len(papers[0].authors) == 2
    assert papers[0].authors[0].name == "Smith, John"


@pytest.mark.asyncio
async def test_search_with_api_key_sets_auth_header(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider
    import httpx

    headers_sent: list[dict] = []

    async def mock_post(url, *, headers=None, **kwargs):
        headers_sent.append(headers or {})
        return _mock_resp({"results": [], "totalHits": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider(api_key="mykey123")
    await provider.search("test")
    assert any("Authorization" in h for h in headers_sent)
    assert any("mykey123" in str(h) for h in headers_sent)


@pytest.mark.asyncio
async def test_search_without_api_key_no_auth_header(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider
    import httpx

    headers_sent: list[dict] = []

    async def mock_post(url, *, headers=None, **kwargs):
        headers_sent.append(headers or {})
        return _mock_resp({"results": [], "totalHits": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider(api_key=None)
    await provider.search("test")
    assert not any("Authorization" in h for h in headers_sent)


@pytest.mark.asyncio
async def test_year_filter_in_query(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider
    import httpx

    payloads_sent: list[dict] = []

    async def mock_post(url, *, json=None, **kwargs):
        payloads_sent.append(json or {})
        return _mock_resp({"results": [], "totalHits": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider()
    await provider.search("test", year_min=2020, year_max=2023)
    assert payloads_sent
    payload_str = str(payloads_sent[0])
    assert "2020" in payload_str


def test_provider_metadata():
    from perspicacite.search.core_search import CORESearchProvider
    p = CORESearchProvider()
    assert p.name == "core"
    assert "general" in p.domains
    assert p.tier == "reliable"
    assert p.retry == 0
```

Run: `uv run pytest tests/unit/test_core_search.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement CORESearchProvider**

Create `src/perspicacite/search/core_search.py`:

```python
"""CORE API v3 open-access search provider."""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.core")

_CORE_API = "https://api.core.ac.uk/v3/search/works"


class CORESearchProvider:
    """Searches CORE — a cross-domain open-access aggregator (230M+ papers)."""

    name = "core"
    description = "CORE open-access aggregator search (free, optional API key)"
    domains: list[str] = ["general"]
    tier: str = "reliable"
    retry: int = 0

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or None

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        filters: dict[str, Any] = {}
        if year_min:
            filters.setdefault("yearPublished", {})["$gte"] = year_min
        if year_max:
            filters.setdefault("yearPublished", {})["$lte"] = year_max

        payload: dict[str, Any] = {
            "q": query,
            "limit": min(max_results, 100),
        }
        if filters:
            payload["filters"] = filters

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                resp = await client.post(_CORE_API, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("core_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for item in data.get("results") or []:
            doi = item.get("doi") or None
            paper_id = doi or f"core:{item.get('id', 'unknown')}"

            authors: list[Author] = []
            for a in item.get("authors") or []:
                name = (a.get("name") or "").strip()
                if name:
                    authors.append(Author(name=name))

            year: int | None = None
            try:
                year = int(item["yearPublished"])
            except (KeyError, ValueError, TypeError):
                pass

            journals = item.get("journals") or []
            journal = journals[0].get("title") if journals else None

            papers.append(
                Paper(
                    id=paper_id,
                    title=item.get("title") or "Untitled",
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=item.get("abstract"),
                    journal=journal,
                    pdf_url=item.get("downloadUrl"),
                    source=PaperSource.CORE,
                    metadata={"core_id": item.get("id")},
                )
            )

        logger.info("core_search", query=query[:80], results=len(papers))
        return papers
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_core_search.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/core_search.py tests/unit/test_core_search.py
git commit -m "feat(search): CORESearchProvider — open-access aggregator search (Wave B)"
```

---

## Task 9: INSPIRE-HEP Search Provider (Wave B)

**Files:**
- Create: `src/perspicacite/search/inspire_search.py`
- Create: `tests/unit/test_inspire_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_inspire_search.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from perspicacite.models.papers import PaperSource


_SAMPLE_RESPONSE = {
    "hits": {
        "total": 1,
        "hits": [
            {
                "metadata": {
                    "titles": [{"title": "Dark matter detection theory"}],
                    "authors": [
                        {"full_name": "Smith, John"},
                        {"full_name": "Jones, Alice"},
                    ],
                    "publication_info": [{"year": 2023, "journal_title": "Physical Review D"}],
                    "dois": [{"value": "10.1103/PhysRevD.107.123456"}],
                    "arxiv_eprints": [{"value": "2301.12345"}],
                    "abstracts": [{"value": "We study dark matter detection."}],
                    "texkeys": ["Smith:2023abc"],
                }
            }
        ],
    }
}


def _mock_resp(data: dict):
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_search_returns_papers(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
    import httpx

    async def mock_get(url, **kwargs):
        return _mock_resp(_SAMPLE_RESPONSE)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    papers = await provider.search("dark matter detection")

    assert len(papers) == 1
    assert papers[0].doi == "10.1103/PhysRevD.107.123456"
    assert papers[0].title == "Dark matter detection theory"
    assert papers[0].year == 2023
    assert papers[0].journal == "Physical Review D"
    assert papers[0].source == PaperSource.INSPIRE_HEP
    assert len(papers[0].authors) == 2
    assert papers[0].metadata.get("arxiv_id") == "2301.12345"
    assert papers[0].metadata.get("texkey") == "Smith:2023abc"


@pytest.mark.asyncio
async def test_year_filter_appended_to_query(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
    import httpx

    queries_sent: list[str] = []

    async def mock_get(url, *, params=None, **kwargs):
        queries_sent.append((params or {}).get("q", ""))
        return _mock_resp({"hits": {"total": 0, "hits": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    await provider.search("quantum gravity", year_min=2020, year_max=2023)
    assert queries_sent
    assert "2020" in queries_sent[0]
    assert "2023" in queries_sent[0]


@pytest.mark.asyncio
async def test_search_empty_result(monkeypatch):
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
    import httpx

    async def mock_get(url, **kwargs):
        return _mock_resp({"hits": {"total": 0, "hits": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = INSPIREHEPSearchProvider()
    papers = await provider.search("nonexistent topic xyz")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
    p = INSPIREHEPSearchProvider()
    assert p.name == "inspire"
    assert "physics" in p.domains
    assert p.tier == "reliable"
    assert p.retry == 0
```

Run: `uv run pytest tests/unit/test_inspire_search.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement INSPIREHEPSearchProvider**

Create `src/perspicacite/search/inspire_search.py`:

```python
"""INSPIRE-HEP literature search provider."""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.inspire")

_BASE_URL = "https://inspirehep.net/api/literature"


class INSPIREHEPSearchProvider:
    """Searches INSPIRE-HEP — the authoritative physics bibliography."""

    name = "inspire"
    description = "INSPIRE-HEP high-energy physics bibliography (free REST API)"
    domains: list[str] = ["physics"]
    tier: str = "reliable"
    retry: int = 0

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        q = query
        if year_min or year_max:
            y_min = year_min or 1900
            y_max = year_max or 2100
            q = f"{q} de {y_min}--{y_max}"

        params: dict[str, Any] = {
            "q": q,
            "size": min(max_results, 100),
            "sort": "mostrecent",
            "fields": "titles,authors,publication_info,dois,arxiv_eprints,abstracts,texkeys",
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("inspire_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for hit in (data.get("hits") or {}).get("hits") or []:
            meta = hit.get("metadata") or {}

            titles = meta.get("titles") or []
            title = titles[0].get("title") if titles else "Untitled"

            authors: list[Author] = []
            for a in meta.get("authors") or []:
                name = (a.get("full_name") or "").strip()
                if name:
                    authors.append(Author(name=name))

            pub_info = meta.get("publication_info") or []
            year: int | None = None
            journal: str | None = None
            if pub_info:
                year_raw = pub_info[0].get("year")
                try:
                    year = int(year_raw)
                except (TypeError, ValueError):
                    pass
                journal = pub_info[0].get("journal_title")

            dois = meta.get("dois") or []
            doi = dois[0].get("value") if dois else None

            arxiv_eprints = meta.get("arxiv_eprints") or []
            arxiv_id = arxiv_eprints[0].get("value") if arxiv_eprints else None

            abstracts = meta.get("abstracts") or []
            abstract = abstracts[0].get("value") if abstracts else None

            texkeys = meta.get("texkeys") or []
            texkey = texkeys[0] if texkeys else None

            paper_id = doi or (f"arxiv:{arxiv_id}" if arxiv_id else f"inspire:{hit.get('id', 'unknown')}")

            papers.append(
                Paper(
                    id=paper_id,
                    title=title or "Untitled",
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=abstract,
                    journal=journal,
                    source=PaperSource.INSPIRE_HEP,
                    metadata={"arxiv_id": arxiv_id, "texkey": texkey, "inspire_id": hit.get("id")},
                )
            )

        logger.info("inspire_search", query=query[:80], results=len(papers))
        return papers
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_inspire_search.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/inspire_search.py tests/unit/test_inspire_search.py
git commit -m "feat(search): INSPIREHEPSearchProvider — physics bibliography (Wave B)"
```

---

## Task 10: ADS Search Provider (Wave B)

**Files:**
- Create: `src/perspicacite/search/ads_search.py`
- Create: `tests/unit/test_ads_search.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_ads_search.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from perspicacite.models.papers import PaperSource


_SAMPLE_RESPONSE = {
    "response": {
        "numFound": 1,
        "docs": [
            {
                "title": ["Exoplanet atmospheric characterization with JWST"],
                "author": ["Smith, J.", "Jones, A."],
                "year": "2023",
                "doi": ["10.1086/123456"],
                "bibcode": "2023ApJ...123..456S",
                "abstract": "We characterize atmospheres of exoplanets.",
                "identifier": ["arxiv:2301.12345"],
            }
        ],
    }
}


def _mock_resp(data: dict, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_search_returns_papers(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider
    import httpx

    async def mock_get(url, **kwargs):
        return _mock_resp(_SAMPLE_RESPONSE)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="testtoken")
    papers = await provider.search("exoplanet atmosphere")

    assert len(papers) == 1
    assert papers[0].doi == "10.1086/123456"
    assert papers[0].title == "Exoplanet atmospheric characterization with JWST"
    assert papers[0].year == 2023
    assert papers[0].source == PaperSource.ADS
    assert len(papers[0].authors) == 2
    assert papers[0].metadata.get("bibcode") == "2023ApJ...123..456S"


@pytest.mark.asyncio
async def test_search_sends_auth_header(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider
    import httpx

    headers_sent: list[dict] = []

    async def mock_get(url, *, headers=None, **kwargs):
        headers_sent.append(headers or {})
        return _mock_resp({"response": {"numFound": 0, "docs": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="myadstoken")
    await provider.search("test")
    assert any("Authorization" in h for h in headers_sent)
    assert any("myadstoken" in str(h) for h in headers_sent)


@pytest.mark.asyncio
async def test_year_filter_in_query(monkeypatch):
    from perspicacite.search.ads_search import ADSSearchProvider
    import httpx

    queries_sent: list[str] = []

    async def mock_get(url, *, params=None, **kwargs):
        queries_sent.append((params or {}).get("q", ""))
        return _mock_resp({"response": {"numFound": 0, "docs": []}})

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    provider = ADSSearchProvider(api_key="tok")
    await provider.search("galaxy formation", year_min=2020, year_max=2023)
    assert queries_sent
    assert "2020" in queries_sent[0]
    assert "2023" in queries_sent[0]


def test_provider_metadata():
    from perspicacite.search.ads_search import ADSSearchProvider
    p = ADSSearchProvider(api_key="tok")
    assert p.name == "ads"
    assert "astronomy" in p.domains
    assert p.tier == "external"
    assert p.retry == 1
```

Run: `uv run pytest tests/unit/test_ads_search.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Implement ADSSearchProvider**

Create `src/perspicacite/search/ads_search.py`:

```python
"""NASA Astrophysics Data System (ADS) search provider."""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.ads")

_ADS_BASE = "https://api.adsabs.harvard.edu/v1/search/query"
_ADS_FIELDS = "title,author,year,doi,abstract,bibcode,identifier"


class ADSSearchProvider:
    """Searches NASA ADS — the authoritative astronomy bibliography."""

    name = "ads"
    description = "NASA ADS astronomy search (requires free ADS API token)"
    domains: list[str] = ["astronomy"]
    tier: str = "external"
    retry: int = 1

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        q = query
        if year_min or year_max:
            y_min = year_min or 1900
            y_max = year_max or 2100
            q = f"{q} pubdate:[{y_min} TO {y_max}]"

        params: dict[str, Any] = {
            "q": q,
            "fl": _ADS_FIELDS,
            "rows": min(max_results, 200),
            "sort": "citation_count desc",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(_ADS_BASE, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("ads_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for doc in (data.get("response") or {}).get("docs") or []:
            raw_title = doc.get("title") or []
            title = raw_title[0] if raw_title else "Untitled"

            authors: list[Author] = []
            for name in doc.get("author") or []:
                name = name.strip()
                if name:
                    authors.append(Author(name=name))

            year: int | None = None
            try:
                year = int(doc["year"])
            except (KeyError, ValueError, TypeError):
                pass

            raw_doi = doc.get("doi") or []
            doi = raw_doi[0] if raw_doi else None
            paper_id = doi or f"ads:{doc.get('bibcode', 'unknown')}"

            identifiers = doc.get("identifier") or []
            arxiv_id: str | None = None
            for ident in identifiers:
                if ident.startswith("arxiv:"):
                    arxiv_id = ident[6:]
                    break

            papers.append(
                Paper(
                    id=paper_id,
                    title=title,
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=doc.get("abstract"),
                    source=PaperSource.ADS,
                    metadata={
                        "bibcode": doc.get("bibcode"),
                        "arxiv_id": arxiv_id,
                    },
                )
            )

        logger.info("ads_search", query=query[:80], results=len(papers))
        return papers
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_ads_search.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/search/ads_search.py tests/unit/test_ads_search.py
git commit -m "feat(search): ADSSearchProvider — NASA ADS astronomy search (Wave B)"
```

---

## Task 11: OpenCitations COCI + Cite-Graph Multi-Source Bonus (Wave C)

**Files:**
- Create: `src/perspicacite/pipeline/download/opencitations.py`
- Modify: `src/perspicacite/pipeline/cite_graph.py:157-182` (score_cite_hit)
- Modify: `src/perspicacite/pipeline/cite_graph.py:310-390` (enrich_kb_from_cite_graph)
- Modify: `src/perspicacite/config/schema.py` (CiteGraphConfig)
- Create: `tests/unit/test_opencitations.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_opencitations.py
from __future__ import annotations

import pytest
from unittest.mock import MagicMock


_SAMPLE_COCI = [
    {
        "oci": "020...",
        "citing": "10.1234/citing1",
        "cited": "10.1234/seed",
        "creation": "2023-01",
        "timespan": "P1Y2M",
        "journal_sc": "no",
        "author_sc": "no",
    },
    {
        "oci": "021...",
        "citing": "10.1234/citing2",
        "cited": "10.1234/seed",
        "creation": "2021-06",
        "timespan": "P3Y0M",
        "journal_sc": "no",
        "author_sc": "no",
    },
]


def _mock_resp(data, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_fetch_returns_citing_dois(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations
    import httpx

    async def mock_get(url, **kwargs):
        return _mock_resp(_SAMPLE_COCI)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/seed")
    dois = [r["doi"] for r in results]
    assert "10.1234/citing1" in dois
    assert "10.1234/citing2" in dois


@pytest.mark.asyncio
async def test_fetch_extracts_year_from_timespan(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations
    import httpx

    async def mock_get(url, **kwargs):
        return _mock_resp(_SAMPLE_COCI)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/seed")
    # creation "2023-01" → year 2023
    r1 = next(r for r in results if r["doi"] == "10.1234/citing1")
    assert r1["publication_year"] == 2023


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_404(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations
    import httpx

    async def mock_get(url, **kwargs):
        return _mock_resp([], status=404)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/unknown")
    assert results == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_error(monkeypatch):
    from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations
    import httpx

    async def mock_get(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    results = await fetch_opencitations_citations("10.1234/any")
    assert results == []


def test_multi_source_bonus_applied():
    from perspicacite.pipeline.cite_graph import CiteHit, score_cite_hit
    from perspicacite.config.schema import CiteGraphConfig

    cfg = CiteGraphConfig()
    hit = CiteHit(doi="10.1/x", title="Test", year=2022, venue=None, citation_count=10, is_oa=True)
    score_without_bonus = score_cite_hit(hit, [], cfg, now_year=2024, source_count=1)

    hit2 = CiteHit(doi="10.1/x", title="Test", year=2022, venue=None, citation_count=10, is_oa=True)
    score_with_bonus = score_cite_hit(hit2, [], cfg, now_year=2024, source_count=2)

    assert score_with_bonus > score_without_bonus
```

Run: `uv run pytest tests/unit/test_opencitations.py -v`
Expected: FAIL (module not found)

- [ ] **Step 2: Create opencitations.py**

Create `src/perspicacite/pipeline/download/opencitations.py`:

```python
"""OpenCitations COCI citation fetcher."""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.opencitations")

_COCI_BASE = "https://opencitations.net/index/coci/api/v1/citations"


async def fetch_opencitations_citations(
    doi: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch papers citing ``doi`` from OpenCitations COCI.

    Returns a list of OpenAlex-like work dicts with keys:
    ``doi``, ``publication_year``, ``id`` (synthetic).
    Returns [] on any error (404, network, parse failure).
    """
    if not doi:
        return []

    client = http_client or httpx.AsyncClient(timeout=20.0)
    should_close = http_client is None

    try:
        url = f"{_COCI_BASE}/{doi}"
        resp = await client.get(url, headers={"Accept": "application/json"})

        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            logger.warning("coci_http_error", doi=doi, status=resp.status_code)
            return []

        records = resp.json()
        if not isinstance(records, list):
            return []

        results: list[dict[str, Any]] = []
        for rec in records:
            citing_doi = rec.get("citing") or ""
            if not citing_doi:
                continue

            year: int | None = None
            creation = rec.get("creation") or ""
            if creation and len(creation) >= 4:
                try:
                    year = int(creation[:4])
                except ValueError:
                    pass

            results.append({
                "doi": citing_doi,
                "publication_year": year,
                "id": f"https://doi.org/{citing_doi}",
                "title": "",
                "display_name": "",
                "cited_by_count": 0,
                "abstract_inverted_index": None,
                "authorships": [],
                "primary_location": {},
                "metadata": {"coci_oci": rec.get("oci"), "coci_timespan": rec.get("timespan")},
            })

        logger.info("coci_fetch", doi=doi, citing_count=len(results))
        return results

    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("coci_fetch_error", doi=doi, error=str(exc))
        return []

    finally:
        if should_close:
            await client.aclose()
```

- [ ] **Step 3: Add multi_source_bonus to CiteGraphConfig**

In `src/perspicacite/config/schema.py`, inside `CiteGraphConfig`, add after the existing weight fields:

```python
    multi_source_bonus: float = Field(
        default=0.15, ge=0.0, le=0.5,
        description=(
            "Score bonus for citing papers confirmed by ≥2 of the three "
            "citation-graph sources (OpenAlex, Semantic Scholar, COCI). "
            "Rewards cross-validated citations without penalising COCI's "
            "lower recall."
        ),
    )
```

- [ ] **Step 4: Update score_cite_hit to accept source_count**

In `src/perspicacite/pipeline/cite_graph.py`, change the `score_cite_hit` signature:

```python
def score_cite_hit(
    hit: CiteHit,
    tool_synonyms: list[str],
    config: CiteGraphConfig,
    *,
    now_year: int,
    source_count: int = 1,
) -> float:
    """Compute hit.score from the four signal components plus optional multi-source bonus."""
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
    if source_count >= 2:
        s = min(s + config.multi_source_bonus, 1.0)
    hit.score = round(s, 4)
    hit.score_breakdown = {
        "citations": round(cit, 4),
        "recency": round(rec, 4),
        "oa": round(oa, 4),
        "match": round(match, 4),
        "multi_source_bonus": config.multi_source_bonus if source_count >= 2 else 0.0,
    }
    return hit.score
```

- [ ] **Step 5: Update enrich_kb_from_cite_graph to add COCI third arm**

In `src/perspicacite/pipeline/cite_graph.py`, inside `enrich_kb_from_cite_graph`, find the section where `works, seed_title = await _resolve_and_fetch(...)` is called. After resolving the seed, fetch COCI citations concurrently with the existing OpenAlex+SS fetch.

Replace the block starting with `async with httpx.AsyncClient() as client:` through the end of the orchestrator:

```python
    async with httpx.AsyncClient() as client:
        # Fetch OpenAlex + Semantic Scholar (existing path)
        oa_works, seed_title = await _resolve_and_fetch(
            tool=tool,
            doi=seed_doi,
            openalex_id=openalex_id,
            headers=headers,
            client=client,
            max_results=cfg.max_papers * 2,
        )

        # Fetch COCI citations concurrently when we have a DOI
        coci_works: list[dict] = []
        if seed_doi:
            from perspicacite.pipeline.download.opencitations import fetch_opencitations_citations
            coci_works = await fetch_opencitations_citations(seed_doi, http_client=client)

    # Build DOI→source_count map for multi-source bonus
    doi_source_counts: dict[str, int] = {}
    for w in oa_works:
        raw_doi = (w.get("doi") or "").replace("https://doi.org/", "")
        if raw_doi:
            doi_source_counts[raw_doi] = doi_source_counts.get(raw_doi, 0) + 1
    for w in coci_works:
        raw_doi = (w.get("doi") or "").replace("https://doi.org/", "")
        if raw_doi:
            doi_source_counts[raw_doi] = doi_source_counts.get(raw_doi, 0) + 1

    all_works = oa_works + coci_works

    tool_synonyms = tool_synonyms_from_seed(tool=tool, seed_title=seed_title)

    raw_hits: list[CiteHit] = []
    for work in all_works:
        h = _hit_from_oa_work(work)
        if h is not None:
            raw_hits.append(h)

    filtered = apply_cite_graph_filters(
        raw_hits, config=cfg, existing_dois=existing_dois, now_year=now_year,
    )
    for h in filtered:
        sc = doi_source_counts.get(h.doi, 1)
        score_cite_hit(h, tool_synonyms, cfg, now_year=now_year, source_count=sc)

    ranked = sorted(filtered, key=lambda h: h.score, reverse=True)
    return ranked[: cfg.max_papers]
```

Note: you will need to find where `headers` is defined in the existing function body — it's passed into `_resolve_and_fetch`. If `headers` is not already a local variable, initialise it:

```python
    headers: dict = {}
    pdf_cfg = getattr(kb_config, "pdf_download", None) if hasattr(kb_config, "pdf_download") else None
    email = getattr(pdf_cfg, "unpaywall_email", None) if pdf_cfg else None
    if email:
        headers = {"User-Agent": f"Perspicacite/2 (mailto:{email})"}
```

- [ ] **Step 6: Run all new tests**

Run: `uv run pytest tests/unit/test_opencitations.py -v`
Expected: all PASS

Run: `uv run pytest tests/unit/ -v -x --ignore=tests/unit/__pycache__`
Expected: all PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/pipeline/download/opencitations.py \
        src/perspicacite/pipeline/cite_graph.py \
        src/perspicacite/config/schema.py \
        tests/unit/test_opencitations.py
git commit -m "feat(cite-graph): OpenCitations COCI third arm + multi_source_bonus scoring (Wave C)"
```

---

## Task 12: Final Integration — Ruff + Full Test Run + search/__init__ exports

**Files:**
- Modify: `src/perspicacite/search/__init__.py` (add Wave A/B/C providers to exports)

- [ ] **Step 1: Update __init__.py exports**

Replace the full content of `src/perspicacite/search/__init__.py`:

```python
"""Literature search providers."""

from perspicacite.search.scilex_adapter import SciLExAdapter, SciLExSearchProvider
from perspicacite.search.google_scholar import GoogleScholarSearch, SearchAggregator
from perspicacite.search.doi_resolver import resolve_doi, resolve_dois_batch
from perspicacite.search.semantic_scholar import lookup_paper, normalize_paper_id
from perspicacite.search.protocols import SearchProvider
from perspicacite.search.domain_classifier import DomainClassifier
from perspicacite.search.domain_aggregator import DomainAwareAggregator, build_aggregator
from perspicacite.search.europepmc_search import EuropePMCSearchProvider
from perspicacite.search.pubchem_search import PubChemSearchProvider
from perspicacite.search.core_search import CORESearchProvider
from perspicacite.search.inspire_search import INSPIREHEPSearchProvider
from perspicacite.search.ads_search import ADSSearchProvider

__all__ = [
    "SciLExAdapter",
    "SciLExSearchProvider",
    "GoogleScholarSearch",
    "SearchAggregator",
    "resolve_doi",
    "resolve_dois_batch",
    "lookup_paper",
    "normalize_paper_id",
    "SearchProvider",
    "DomainClassifier",
    "DomainAwareAggregator",
    "build_aggregator",
    "EuropePMCSearchProvider",
    "PubChemSearchProvider",
    "CORESearchProvider",
    "INSPIREHEPSearchProvider",
    "ADSSearchProvider",
]
```

- [ ] **Step 2: Run ruff across the entire changed surface**

Run: `uv run ruff check src/perspicacite/search/ src/perspicacite/pipeline/download/opencitations.py src/perspicacite/pipeline/cite_graph.py src/perspicacite/config/schema.py src/perspicacite/mcp/server.py src/perspicacite/pipeline/search_to_kb.py`
Expected: no errors. Fix any lint issues before proceeding.

- [ ] **Step 3: Run the full unit test suite**

Run: `uv run pytest tests/unit/ -v --tb=short 2>&1 | tail -30`
Expected: all PASS. Investigate and fix any regressions before committing.

- [ ] **Step 4: Verify config round-trip**

Run:
```bash
uv run python -c "
from perspicacite.config.loader import load_config
c = load_config()
print('search providers:', c.search.enabled_providers)
print('cite_graph bonus:', c.knowledge_base.cite_graph.multi_source_bonus)
print('ok')
"
```
Expected: prints values with no errors.

- [ ] **Step 5: Final commit**

```bash
git add src/perspicacite/search/__init__.py
git commit -m "feat(search): export all Wave A/B/C providers from search/__init__.py"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] DomainClassifier — Task 3
- [x] SearchProvider protocol extended — Task 2
- [x] DomainAwareAggregator + ProviderHealthTracker — Task 4
- [x] Tier policy (reliable/external/flaky + timeout scaling) — Task 4 (implemented in `_tier_timeout`)
- [x] Circuit breaker (3 failures → 5 min cooldown) — Task 4
- [x] Dedup by DOI + title-hash fallback — Task 4
- [x] SearchConfig + config.example.yml — Tasks 1 + 5
- [x] `build_aggregator(config)` factory — Task 4
- [x] mcp/server.py updated — Task 5
- [x] search_to_kb.py updated — Task 5
- [x] EuropePMCSearchProvider — Task 6
- [x] PubChemSearchProvider (two-hop CID→PMID→Paper) — Task 7
- [x] CORESearchProvider — Task 8
- [x] INSPIREHEPSearchProvider — Task 9
- [x] ADSSearchProvider — Task 10
- [x] OpenCitations COCI fetcher — Task 11
- [x] multi_source_bonus in CiteGraphConfig + score_cite_hit — Task 11
- [x] COCI as third concurrent arm in enrich_kb_from_cite_graph — Task 11
- [x] New PaperSource enum values — Task 1
- [x] Tests for every component — Tasks 2-11

**Type consistency check:**
- `score_cite_hit` now takes `source_count: int = 1` — all existing call sites pass no `source_count`, so default `1` preserves behavior. Only the new cite_graph path passes `source_count=sc`.
- `run_search` gains `config: Any | None = None` with backward-compat default — existing call sites without `config` still work.
- `DomainAwareAggregator.search()` signature matches `SearchProvider.search()` plus the extra `apis` backward-compat kwarg.
