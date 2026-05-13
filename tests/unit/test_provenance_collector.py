import json

from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting, get_collector


def test_collector_accumulates_and_finalizes() -> None:
    c = ProvenanceCollector(
        conversation_id="conv-1",
        message_id="msg-1",
        rag_mode="basic",
        request_params={"top_k": 5, "kb_names": ["kb1"]},
    )
    c.add_retrieval(
        paper_id="p1", doi="10.1/a", title="A", score=0.9,
        kb_name="kb1", content_type="full_text", pipeline_step="pdf",
        rank=0, stage_label="basic.retrieve",
    )
    c.add_trace("plan", detail={"steps": 3})
    c.add_llm_call(
        stage_label="basic.answer", provider="deepseek", model="deepseek-chat",
        prompt_messages=[{"role": "user", "content": "hi"}],
        response_text="hello", prompt_tokens=10, completion_tokens=5,
        latency_ms=42.0,
    )
    out = c.finalize()
    assert out["conversation_id"] == "conv-1"
    assert out["message_id"] == "msg-1"
    assert out["rag_mode"] == "basic"
    assert out["request_params"]["top_k"] == 5
    assert len(out["retrieval_events"]) == 1
    assert out["retrieval_events"][0]["doi"] == "10.1/a"
    assert out["mode_trace"][0]["step"] == "plan"
    assert out["mode_trace"][0]["detail"]["steps"] == 3
    assert len(out["llm_calls"]) == 1
    assert out["llm_calls"][0]["provider"] == "deepseek"
    assert out["llm_calls"][0]["prompt_tokens"] == 10
    json.dumps(out)


def test_collector_finalize_is_idempotent() -> None:
    c = ProvenanceCollector(conversation_id=None, message_id="m", rag_mode="basic", request_params={})
    a = c.finalize()
    b = c.finalize()
    assert a == b


def test_contextvar_default_none() -> None:
    assert get_collector() is None


def test_contextvar_collecting_sets_and_resets() -> None:
    c = ProvenanceCollector(conversation_id=None, message_id="m", rag_mode="basic", request_params={})
    with collecting(c):
        assert get_collector() is c
    assert get_collector() is None
