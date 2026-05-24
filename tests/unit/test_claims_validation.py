import pytest
from perspicacite.pipeline.claims import claims_to_graph, validate_claims


@pytest.mark.unit
def test_valid_claim_conforms():
    pytest.importorskip("indicium")
    claims = [{"context": "c", "subject": "s", "qualifier": "causes",
               "relation": "r", "object": "o"}]
    conforms, _ = validate_claims(claims)
    assert conforms is True


@pytest.mark.unit
def test_claim_missing_slots_is_rejected():
    pytest.importorskip("indicium")
    claims = [{"context": "c"}]  # missing 4 of 5 required slots
    conforms, _ = validate_claims(claims)
    assert conforms is False
