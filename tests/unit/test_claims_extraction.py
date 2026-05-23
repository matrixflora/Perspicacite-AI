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
    assert "id" in c, "every coerced claim must carry an id for Indicium adapter compatibility"
    assert c["id"].startswith("perspicacite:")
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


# ---------------------------------------------------------------------------
# Domain adapter integration
# ---------------------------------------------------------------------------

class _MockAdapter:
    """Minimal adapter that satisfies the DomainAdapter structural protocol."""
    domain_id = "test"
    qualifiers = frozenset({"test_qualifier"})
    ontology_prefixes = {"TST": "http://test.example.org/"}

    def extraction_context(self) -> str:
        return "Domain: test. Extra qualifier: test_qualifier."

    def enrich_claim(self, claim: dict) -> dict:
        claim = dict(claim)
        claim["enriched_by_adapter"] = True
        return claim


@pytest.mark.unit
async def test_extract_claims_with_adapter_appends_domain_context():
    """domain_adapter.extraction_context() must appear in the LLM prompt."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value='{"claims": []}')
    adapter = _MockAdapter()

    await extract_claims(
        llm_client=llm,
        passages=[{"chunk_text": "Some text.", "source": {"doi": "10.1/x"}}],
        context="test",
        domain_adapter=adapter,
    )

    prompt_sent = llm.complete.call_args[1]["messages"][0]["content"]
    assert "Domain: test." in prompt_sent, (
        f"Expected domain context in prompt, got:\n{prompt_sent}"
    )


@pytest.mark.unit
async def test_extract_claims_with_adapter_enriches_claims():
    """Each coerced claim must be passed through domain_adapter.enrich_claim()."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({"claims": [{
        "context": "in vitro", "subject": "compound A",
        "qualifier": "inhibits", "relation": "inhibits growth of",
        "object": "cell line B", "claim_type": "explicit",
        "evidence_type": "data", "source_type": "text",
        "quote": "A inhibited B", "source_doi": "10.1/x",
    }]}))
    adapter = _MockAdapter()

    claims = await extract_claims(
        llm_client=llm,
        passages=[{"chunk_text": "A inhibited B", "source": {"doi": "10.1/x"}}],
        context="test",
        domain_adapter=adapter,
    )

    assert len(claims) == 1
    assert claims[0].get("enriched_by_adapter") is True


@pytest.mark.unit
async def test_extract_claims_with_adapter_accepts_domain_qualifier():
    """Qualifiers from domain_adapter.qualifiers must not be dropped by _coerce_claim."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({"claims": [{
        "context": "in vitro", "subject": "compound A",
        "qualifier": "test_qualifier",   # domain-specific; unknown to base _QUALIFIERS
        "relation": "something", "object": "B",
    }]}))
    adapter = _MockAdapter()

    claims = await extract_claims(
        llm_client=llm,
        passages=[{"chunk_text": "text"}],
        context="test",
        domain_adapter=adapter,
    )

    assert len(claims) == 1
    assert claims[0]["qualifier"] == "test_qualifier"


@pytest.mark.unit
async def test_extract_claims_without_adapter_unchanged():
    """Passing no adapter must preserve existing behaviour exactly."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({"claims": [{
        "context": "in vitro", "subject": "compound A", "qualifier": "inhibits",
        "relation": "inhibits", "object": "B",
        "evidence_type": "data", "source_doi": "10.1/x",
    }]}))

    claims = await extract_claims(
        llm_client=llm,
        passages=[{"chunk_text": "text", "source": {"doi": "10.1/x"}}],
        context="test",
        # no domain_adapter
    )

    assert len(claims) == 1
    assert "enriched_by_adapter" not in claims[0]


@pytest.mark.unit
async def test_extract_claims_domain_qualifier_dropped_without_adapter():
    """Without an adapter, domain-only qualifiers are still dropped by _coerce_claim."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({"claims": [{
        "context": "in vitro", "subject": "A", "qualifier": "test_qualifier",
        "relation": "r", "object": "B",
    }]}))

    claims = await extract_claims(
        llm_client=llm,
        passages=[{"chunk_text": "text"}],
        context="test",
        # no adapter — test_qualifier is out-of-vocab
    )

    assert claims == []
