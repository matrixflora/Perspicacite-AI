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
