"""Domain-aware search aggregator with per-tier reliability policies."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from perspicacite.logging import get_logger
from perspicacite.search.domain_classifier import DomainClassifier

if TYPE_CHECKING:
    from perspicacite.models.papers import Paper

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
            except TimeoutError:
                logger.warning("provider_timeout", provider=name, attempt=attempt)
            except Exception as exc:
                logger.warning("provider_error", provider=name, error=str(exc), attempt=attempt)
            if attempt < retry:
                await asyncio.sleep(backoffs[min(attempt, len(backoffs) - 1)])
        # Count one logical failure regardless of how many retry attempts were made.
        self._health.record_failure(name)
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
                    p,
                    query=query,
                    max_results=self._max_per,
                    year_min=year_min,
                    year_max=year_max,
                    extra_kwargs=extra,
                )
            )

        results_per_provider: list[list[Paper]] = await asyncio.gather(*tasks)

        # Annotate each paper with the name of its source provider.
        for p, papers in zip(providers, results_per_provider, strict=True):
            provider_name = getattr(p, "name", "unknown")
            for paper in papers:
                paper.metadata.setdefault("sources", [])
                if provider_name not in paper.metadata["sources"]:
                    paper.metadata["sources"].append(provider_name)

        seen_dois: dict[str, Paper] = {}
        seen_title_hashes: dict[str, Paper] = {}
        merged: list[Paper] = []
        for papers in results_per_provider:
            for paper in papers:
                new_sources: list[str] = paper.metadata.get("sources", [])
                if paper.doi:
                    doi_key = paper.doi.lower().strip()
                    if doi_key in seen_dois:
                        kept = seen_dois[doi_key]
                        for s in new_sources:
                            if s not in kept.metadata.get("sources", []):
                                kept.metadata.setdefault("sources", []).append(s)
                        continue
                    seen_dois[doi_key] = paper
                else:
                    title_hash = paper.title.lower().strip()[:80]
                    if title_hash in seen_title_hashes:
                        kept = seen_title_hashes[title_hash]
                        for s in new_sources:
                            if s not in kept.metadata.get("sources", []):
                                kept.metadata.setdefault("sources", []).append(s)
                        continue
                    seen_title_hashes[title_hash] = paper
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
