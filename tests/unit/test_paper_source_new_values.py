"""Tests for new PaperSource enum values."""

def test_new_paper_source_values():
    """Verify all 6 new PaperSource enum values exist and have correct values."""
    from perspicacite.models.papers import PaperSource

    assert PaperSource.EUROPE_PMC.value == "europe_pmc"
    assert PaperSource.PUBCHEM.value == "pubchem"
    assert PaperSource.CORE.value == "core"
    assert PaperSource.INSPIRE_HEP.value == "inspire_hep"
    assert PaperSource.ADS.value == "ads"
    assert PaperSource.OPENCITATIONS.value == "opencitations"


def test_openrouter_web_paper_source():
    from perspicacite.models.papers import PaperSource
    assert PaperSource.OPENROUTER_WEB.value == "openrouter_web"
    # Round-trip
    assert PaperSource("openrouter_web") is PaperSource.OPENROUTER_WEB
