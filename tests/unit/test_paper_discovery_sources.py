"""Unit tests for Paper.discovery_sources / enrichment_sources fields."""
from perspicacite.models.papers import Paper


def test_discovery_sources_default_empty():
    p = Paper(id="x", title="t")
    assert p.discovery_sources == []
    assert p.enrichment_sources == []


def test_legacy_metadata_sources_not_mirrored():
    """Back-compat shim removed: metadata['sources'] does NOT populate
    discovery_sources. Callers must use the typed field directly."""
    p = Paper(
        id="x", title="t",
        metadata={"sources": ["openalex", "pubmed"]},
    )
    assert p.discovery_sources == []


def test_legacy_metadata_enrichment_sources_not_mirrored():
    """Back-compat shim removed: metadata['enrichment_sources'] does NOT
    populate enrichment_sources. Callers must use the typed field directly."""
    p = Paper(
        id="x", title="t",
        metadata={"enrichment_sources": ["crossref"]},
    )
    assert p.enrichment_sources == []


def test_explicit_typed_field_used_directly():
    """Typed fields are populated when passed as kwargs, independent of
    metadata."""
    p = Paper(
        id="x", title="t",
        discovery_sources=["new_value"],
        metadata={"sources": ["ignored_legacy_value"]},
    )
    assert p.discovery_sources == ["new_value"]


def test_fields_are_independent_lists():
    p1 = Paper(id="x", title="t")
    p2 = Paper(id="y", title="t")
    p1.discovery_sources.append("openalex")
    assert p2.discovery_sources == []
