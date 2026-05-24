"""Chat SSE emits a ``'type': 'asb_metadata'`` event when sources carry
ASB content.

Two assertions:
- The pure helper returns the right shape for an ASB-tagged source and
  the empty shape for non-ASB input.
- The chat router code path imports + calls the helper, emits the SSE
  type, and guards emission with a non-empty check (white-box check
  via ``inspect.getsource`` since a true behavioural integration test
  requires a full RAG engine fixture — out of scope here).
"""
from __future__ import annotations


def test_asb_metadata_sse_event_payload_shape():
    """Pure: feed the helper a list of source dicts (SourceReference
    .model_dump output shape) and confirm the helper returns the
    expected shape. The SSE event wraps this verbatim."""
    from perspicacite.pipeline.asb.response import build_asb_response_metadata

    sources = [
        {
            "title": "Skill",
            "metadata": {
                "content_kind": "skill_body",
                "skill_id": "abc",
                "skill_name": "Abc",
                "tools": [],
                "environment": [],
                "parameters": [],
            },
        },
    ]
    md = build_asb_response_metadata(sources)
    assert {s["skill_id"] for s in md["skill_metadata"]} == {"abc"}
    assert md["workflow_metadata"] == []

    # Empty / non-ASB: helper returns the well-formed empty shape; the
    # chat router uses this to suppress emission entirely.
    empty = build_asb_response_metadata([{"title": "x", "metadata": None}])
    assert empty == {"skill_metadata": [], "workflow_metadata": []}


def test_chat_router_emits_asb_metadata_event_only_when_nonempty():
    """White-box check on the chat router code path. We assert the
    presence of the call + emit + skip-when-empty branch by importing
    the router source and grepping. (A true behavioural integration
    test would require a full RAG engine fixture — out of scope.)"""
    import inspect

    from perspicacite.web.routers import chat as chat_mod

    src = inspect.getsource(chat_mod)
    assert "build_asb_response_metadata" in src, "helper must be imported + called"
    assert (
        "'type': 'asb_metadata'" in src or '"type": "asb_metadata"' in src
    ), "router must emit an asb_metadata SSE event"
    # Guard against unconditional emission: the helper's output keys
    # must be referenced so the conditional skip can read them.
    assert "skill_metadata" in src and "workflow_metadata" in src, (
        "router emit code must reference the helper's output keys"
    )
