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
    good = _Provider("good", [_paper("10.1/g")])
    agg = DomainAwareAggregator([bad, good], provider_timeout_s=5.0)
    # Trip the circuit manually
    for _ in range(3):
        agg._health.record_failure("bad")
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
