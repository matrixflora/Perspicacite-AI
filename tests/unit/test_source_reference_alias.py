"""Verify SourceReference.discovery_sources alias to legacy sources_all."""
from perspicacite.models.rag import SourceReference


def test_construct_via_new_name():
    s = SourceReference(title="t", discovery_sources=["a", "b"])
    assert s.discovery_sources == ["a", "b"]


def test_construct_via_legacy_alias():
    s = SourceReference(title="t", sources_all=["a", "b"])
    assert s.discovery_sources == ["a", "b"]


def test_serialises_with_both_names_available():
    s = SourceReference(title="t", discovery_sources=["a"])
    # Default dump uses field name
    d = s.model_dump()
    assert d["discovery_sources"] == ["a"]
    # by_alias produces legacy name (used in wire payloads)
    d2 = s.model_dump(by_alias=True)
    assert d2["sources_all"] == ["a"]


def test_none_by_default():
    s = SourceReference(title="t")
    assert s.discovery_sources is None


def test_roundtrip_via_alias_serialisation():
    """A model_dump(by_alias=True) payload can be parsed back as SourceReference."""
    s = SourceReference(title="t", discovery_sources=["pubmed", "openalex"])
    wire = s.model_dump(by_alias=True)
    # Wire payload uses "sources_all"; reconstructing via model_validate must work
    s2 = SourceReference.model_validate(wire)
    assert s2.discovery_sources == ["pubmed", "openalex"]
