"""Unit tests for Paper.discovery_sources / enrichment_sources fields."""
from perspicacite.models.papers import Paper


def test_discovery_sources_default_empty():
    p = Paper(id="x", title="t")
    assert p.discovery_sources == []
    assert p.enrichment_sources == []


def test_discovery_sources_mirrored_from_legacy_metadata():
    """Back-compat: metadata['sources'] populates discovery_sources."""
    p = Paper(
        id="x", title="t",
        metadata={"sources": ["openalex", "pubmed"]},
    )
    assert p.discovery_sources == ["openalex", "pubmed"]


def test_enrichment_sources_mirrored_from_legacy_metadata():
    p = Paper(
        id="x", title="t",
        metadata={"enrichment_sources": ["crossref"]},
    )
    assert p.enrichment_sources == ["crossref"]


def test_explicit_field_wins_over_legacy():
    p = Paper(
        id="x", title="t",
        discovery_sources=["new_value"],
        metadata={"sources": ["legacy_value"]},
    )
    assert p.discovery_sources == ["new_value"]


def test_fields_are_independent_lists():
    p1 = Paper(id="x", title="t")
    p2 = Paper(id="y", title="t")
    p1.discovery_sources.append("openalex")
    assert p2.discovery_sources == []
