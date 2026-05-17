"""Tests for DomainClassifier — regex-based query→domain routing."""

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
