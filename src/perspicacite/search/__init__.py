"""Literature search providers."""

from perspicacite.search.doi_resolver import resolve_doi, resolve_dois_batch
from perspicacite.search.domain_aggregator import DomainAwareAggregator, build_aggregator
from perspicacite.search.domain_classifier import DomainClassifier
from perspicacite.search.google_scholar import GoogleScholarSearch, SearchAggregator
from perspicacite.search.protocols import SearchProvider
from perspicacite.search.scilex_adapter import SciLExAdapter, SciLExSearchProvider
from perspicacite.search.semantic_scholar import lookup_paper, normalize_paper_id

__all__ = [
    "DomainAwareAggregator",
    "DomainClassifier",
    "GoogleScholarSearch",
    "SciLExAdapter",
    "SciLExSearchProvider",
    "SearchAggregator",
    "SearchProvider",
    "build_aggregator",
    "lookup_paper",
    "normalize_paper_id",
    "resolve_doi",
    "resolve_dois_batch",
]
