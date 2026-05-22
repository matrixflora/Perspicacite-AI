import json
import pytest
from unittest.mock import AsyncMock
from perspicacite.pipeline.claims import extract_claims


@pytest.mark.unit
async def test_extract_claims_builds_typed_claims():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({"claims": [{
        "context": "in vitro", "subject": "compound A", "qualifier": "inhibits",
        "relation": "inhibits growth of", "object": "cell line B",
        "claim_type": "explicit", "evidence_type": "data",
        "source_type": "text", "quote": "A inhibited B",
        "source_doi": "10.1/x"}]}))
    passages = [{"chunk_text": "A inhibited B", "source": {"doi": "10.1/x", "title": "T"}}]
    claims = await extract_claims(llm_client=llm, passages=passages, context="onc")
    assert len(claims) == 1
    c = claims[0]
    assert c["qualifier"] == "inhibits"
    assert c["evidence"][0]["doi"] == "10.1/x"
    assert c["evidence"][0]["evidence_type"] == "data"


@pytest.mark.unit
async def test_extract_claims_drops_out_of_vocab_qualifier():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({"claims": [{
        "context": "x", "subject": "s", "qualifier": "frobnicates",
        "relation": "r", "object": "o", "evidence_type": "data"}]}))
    claims = await extract_claims(llm_client=llm, passages=[{"chunk_text": "t"}], context="c")
    assert claims == []
